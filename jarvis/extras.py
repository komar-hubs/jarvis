"""Productivity and PC-care modules: clipboard history, time per program, focus mode, habits, break and water
reminders, messenger counters from window titles, PC watchdog, window layouts, network check, screen recording,
power plans, "не беспокоить", conversation games and easter eggs.

Background work: one sampler thread (every 5 s: program usage, clipboard, messengers) plus Core._ticker calling
tick() every 2 s (focus, breaks, watchdog). Voice rules go into the Router before all others (register())."""
import collections
import ctypes
import datetime as dt
import random
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

import psutil

from . import system as S
from .config import JsonStore
from .planner import parse_duration, plural, say_duration

CREATE_NO_WINDOW = 0x08000000

# friendly names and categories for the "time per program" statistics
APP_NAMES = {
    "chrome.exe": ("Chrome", "Браузер"), "msedge.exe": ("Edge", "Браузер"), "firefox.exe": ("Firefox", "Браузер"),
    "opera.exe": ("Opera", "Браузер"), "browser.exe": ("Яндекс Браузер", "Браузер"), "brave.exe": ("Brave", "Браузер"),
    "telegram.exe": ("Telegram", "Общение"), "ayugram.exe": ("AyuGram", "Общение"), "discord.exe": ("Discord", "Общение"),
    "vesktop.exe": ("Discord", "Общение"), "willicord.exe": ("WilliCord", "Общение"), "whatsapp.exe": ("WhatsApp", "Общение"),
    "whatsapp.root.exe": ("WhatsApp", "Общение"), "viber.exe": ("Viber", "Общение"), "zoom.exe": ("Zoom", "Общение"),
    "code.exe": ("VS Code", "Разработка"), "cursor.exe": ("Cursor", "Разработка"), "devenv.exe": ("Visual Studio", "Разработка"),
    "pycharm64.exe": ("PyCharm", "Разработка"), "idea64.exe": ("IntelliJ IDEA", "Разработка"), "windowsterminal.exe": ("Терминал", "Разработка"),
    "claude.exe": ("Claude", "Разработка"), "godot.windows.opt.tools.64.exe": ("Godot", "Разработка"),
    "unrealeditor.exe": ("Unreal Engine", "Разработка"), "unity.exe": ("Unity", "Разработка"), "studio64.exe": ("Android Studio", "Разработка"),
    "robloxstudiobeta.exe": ("Roblox Studio", "Разработка"),
    "blender.exe": ("Blender", "Творчество"), "krita.exe": ("Krita", "Творчество"), "aseprite.exe": ("Aseprite", "Творчество"),
    "fl64.exe": ("FL Studio", "Творчество"), "audacity.exe": ("Audacity", "Творчество"), "obs64.exe": ("OBS", "Творчество"),
    "photoshop.exe": ("Photoshop", "Творчество"), "blockbench.exe": ("Blockbench", "Творчество"),
    "spotify.exe": ("Spotify", "Медиа"), "vlc.exe": ("VLC", "Медиа"), "mpc-hc64.exe": ("MPC", "Медиа"),
    "steam.exe": ("Steam", "Игры"), "epicgameslauncher.exe": ("Epic Games", "Игры"), "robloxplayerbeta.exe": ("Roblox", "Игры"),
    "minecraft.exe": ("Minecraft", "Игры"), "javaw.exe": ("Minecraft (Java)", "Игры"),
    "explorer.exe": ("Проводник", "Система"), "notepad.exe": ("Блокнот", "Работа"), "winword.exe": ("Word", "Работа"),
    "excel.exe": ("Excel", "Работа"), "powerpnt.exe": ("PowerPoint", "Работа"), "obsidian.exe": ("Obsidian", "Работа"),
}
MESSENGERS = {"telegram.exe": "Telegram", "ayugram.exe": "Telegram", "kotatogram.exe": "Telegram", "64gram.exe": "Telegram",
              "discord.exe": "Discord", "vesktop.exe": "Discord", "willicord.exe": "WilliCord", "whatsapp.exe": "WhatsApp",
              "whatsapp.root.exe": "WhatsApp", "viber.exe": "Viber"}
# processes the "что тормозит" helper must never offer to close
PROTECTED = {"system", "idle", "explorer.exe", "dwm.exe", "csrss.exe", "svchost.exe", "lsass.exe", "winlogon.exe",
             "services.exe", "python.exe", "pythonw.exe", "msedgewebview2.exe", "claude.exe", "memcompression",
             "registry", "smss.exe", "wininit.exe", "fontdrvhost.exe", "audiodg.exe", "searchhost.exe", "ctfmon.exe"}
HABIT_WORDS = {"вода": r"вод[уыа]|стакан\w*", "зарядка": r"зарядк\w*|разминк\w*|упражнени\w*", "чтение": r"почитал\w*|прочитал\w*|чтени\w*|читал\w*",
               "прогулка": r"погулял\w*|прогулк\w*|гулял\w*"}
GAMES = {"города": "Города (называем города по последней букве)", "слова": "Слова (по последней букве)",
         "викторин": "Викторина (вопросы с вариантами ответа, веди счёт)", "загадк": "Загадки",
         "ассоциаци": "Ассоциации", "20 вопрос": "20 вопросов (ты загадываешь, пользователь угадывает вопросами да/нет)",
         "угадай число": "Угадай число от 1 до 100 (подсказки больше/меньше)", "правд": "Правда или ложь (факты)",
         "крокодил": "Голосовой крокодил: описываешь слово, не называя его", "квест": "Текстовый квест в духе Железного человека"}


def _hm(sec):
    """1 час 20 минут / 35 минут: spoken duration rounded to minutes."""
    m = int(sec // 60)
    h, m = divmod(m, 60)
    if h and m:
        return f"{h} {plural(h, 'час', 'часа', 'часов')} {m} {plural(m, 'минуту', 'минуты', 'минут')}"
    if h:
        return f"{h} {plural(h, 'час', 'часа', 'часов')}"
    return f"{m} {plural(m, 'минуту', 'минуты', 'минут')}"


def _looks_secret(s):
    """Passwords and tokens copied from a manager never go into the history."""
    s = s.strip()
    if " " in s or not 8 <= len(s) <= 128:
        return False
    kinds = sum(bool(re.search(p, s)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^\w]"))
    return kinds >= 3 or bool(re.match(r"^(sk|ghp|gho|xox[bp]|eyJ)[-_\w.]{16,}$", s))


def pct_ru(n):
    return "процент" if n % 10 == 1 and n % 100 != 11 else "процента" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "процентов"


def size_ru(mb):
    """Megabytes said aloud: "3.7 гигабайта", "1 гигабайт", "800 мегабайт" (never "0.0 гигабайта")."""
    if mb < 1000:
        return f"{max(10, int(round(mb, -1)))} мегабайт"
    gb = round(mb / 1024, 1)
    if gb == int(gb):
        gb = int(gb)
        return f"{gb} " + ("гигабайт" if gb % 10 == 1 and gb % 100 != 11 else "гигабайта" if 2 <= gb % 10 <= 4 and not 12 <= gb % 100 <= 14 else "гигабайт")
    return f"{gb} гигабайта"


class Extras:
    def __init__(self, core):
        self.core = core
        self.cfg = core.cfg
        self.clip = collections.deque(maxlen=25)     # memory only, never saved
        self._clip_seq = 0
        self._clip_skip = None
        self.usage = JsonStore("usage", default={})   # {date: {proc: seconds}} + {"_focus": {date: [sessions]}}
        if not isinstance(self.usage.items, dict):
            self.usage.items = {}
        self.habits = JsonStore("habits", default={})  # {date: {habit: count}}
        if not isinstance(self.habits.items, dict):
            self.habits.items = {}
        self._usage_dirty = 0
        self._exe_cache = {}
        self.focus = None
        self.msg_counts = {}
        self._msg_said = {}
        self._watch_said = {}
        self._ram_high = 0
        self._cpu_high = 0
        self.active_start = time.time()
        self.last_break = time.time()
        self.last_water = time.time()
        self.rec = None
        self._t = {"watch": 0.0, "msg": 0.0}
        self._lock = threading.RLock()

    # ================================================================== lifecycle
    def start(self):
        threading.Thread(target=self._sampler, daemon=True, name="extras").start()
        threading.Timer(12, self._birthday_check).start()

    def _sampler(self):
        try:
            psutil.cpu_percent(None)
        except Exception:
            pass
        while True:
            time.sleep(5)
            for fn in (self._sample_usage, self._sample_clipboard):
                try:
                    fn()
                except Exception:
                    pass

    def tick(self):
        """Every 2 s from Core._ticker."""
        now = time.time()
        self._focus_tick(now)
        self._health_tick(now)
        if now - self._t["msg"] > 4:
            self._t["msg"] = now
            try:
                self._messenger_tick()
            except Exception:
                pass
        if now - self._t["watch"] > 20:
            self._t["watch"] = now
            try:
                self._watchdog()
            except Exception as e:
                self.core.log(f"Сторож ПК: {e}", "warn")
        if self.rec and self.rec["proc"].poll() is not None:
            self.rec = None
            self.core.emit("recording", on=False)

    def _say(self, text, focus_too=False):
        if self.core.proactive_ok(focus_too=focus_too):
            self.core.player.sound("notify")
            self.core.after_speech(lambda: self.core.say(text), 0.1)
            return True
        return False

    def addr(self):
        return self.cfg.get("user.address") or "сэр"

    # ================================================================== time per program
    def _fg(self):
        """(exe name lower, exe path, window title) of the foreground window."""
        import win32gui
        import win32process
        h = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(h)
        _, pid = win32process.GetWindowThreadProcessId(h)
        c = self._exe_cache.get(pid)
        if not c:
            try:
                p = psutil.Process(pid)
                c = (p.name().lower(), p.exe())
            except Exception:
                c = ("", "")
            if len(self._exe_cache) > 400:
                self._exe_cache.clear()
            self._exe_cache[pid] = c
        return c[0], c[1], title

    def _learn_game(self, exe):
        """An unknown program on the whole screen is a game (a repack in Downloads has no "Games" in its path):
        remembered for good, so Jarvis never offers to close it and keeps quiet about memory while it runs."""
        exe = (exe or "").lower()
        if not exe or exe in APP_NAMES or exe.startswith(("python", "explorer", "applicationframehost", "msedgewebview2")) or \
                re.search(r"player|kmp|mpc|mpv|kodi|vlc|aimp|foobar|video|movie|photo|viewer|pdf|acro|slide|present|zoom|teams|"
                          r"meet|skype|obs|stream|lockapp|screensaver|\.scr$", exe):
            # a full-screen film, photo, PDF or call is not a game ("Игры" would also start the game automations)
            return
        games = self.cfg.get("extras.games_seen") or []
        if exe in games or not S.foreground_fullscreen():
            return
        self.cfg.set("extras.games_seen", (games + [exe])[-60:])
        self.core.log(f"Запомнил игру: {exe}")

    def is_game(self, exe, path=""):
        return self.app_info((exe or "").lower(), path)[1] == "Игры"

    def app_info(self, exe, path=""):
        name, cat = APP_NAMES.get(exe, (None, None))
        if not cat and exe and exe.lower() in (self.cfg.get("extras.games_seen") or []):
            cat = "Игры"
        low = (path or "").lower()
        if "steamapps\\common" in low or "epic games\\" in low or "\\games\\" in low or "riot games" in low:
            if exe not in ("steam.exe",):
                return (name or Path(path).stem), "Игры"
        if not name:
            name = re.sub(r"\.exe$", "", exe or "?").replace("_", " ")
            name = name[:1].upper() + name[1:]
        return name, cat or "Другое"

    def _sample_usage(self):
        if not self.cfg.get("extras.usage_tracking", True):
            return
        exe, path, title = self._fg()
        if not exe or title in ("", "JARVIS HUD"):
            return
        idle = S.idle_seconds()
        # watching a video or playing with a gamepad looks idle: count it if the window is a player/browser/game
        if idle > 120:
            cat = self.app_info(exe, path)[1]
            if cat not in ("Медиа", "Игры", "Браузер") or idle > 3 * 3600:
                return
        today = dt.date.today().isoformat()
        with self._lock:
            day = self.usage.items.setdefault(today, {})
            key = exe
            day[key] = day.get(key, 0) + 5
            paths = self.usage.items.setdefault("_paths", {})
            if path and key not in paths:
                paths[key] = path
            self._usage_dirty += 1
            if self._usage_dirty >= 12:   # save once a minute
                self._usage_dirty = 0
                cut = (dt.date.today() - dt.timedelta(days=60)).isoformat()
                for k in [k for k in self.usage.items if re.match(r"\d{4}-", k) and k < cut]:
                    self.usage.items.pop(k, None)
                self.usage.save()

    def usage_day(self, day=None):
        day = (day or dt.date.today()).isoformat()
        raw = self.usage.items.get(day, {})
        paths = self.usage.items.get("_paths", {})
        apps, cats = {}, {}
        for exe, sec in raw.items():
            name, cat = self.app_info(exe, paths.get(exe, ""))
            a = apps.setdefault(name, {"name": name, "cat": cat, "sec": 0})
            a["sec"] += sec
            cats[cat] = cats.get(cat, 0) + sec
        lst = sorted(apps.values(), key=lambda a: -a["sec"])
        return {"date": day, "total": sum(raw.values()), "apps": lst[:15],
                "cats": sorted(({"cat": k, "sec": v} for k, v in cats.items()), key=lambda c: -c["sec"]),
                "focus": self.usage.items.get("_focus", {}).get(day, [])}

    def usage_brief(self, evening):
        if not evening:
            return None
        u = self.usage_day()
        if u["total"] < 600:
            return None
        top = u["apps"][0]["name"] if u["apps"] else ""
        games = next((c["sec"] for c in u["cats"] if c["cat"] == "Игры"), 0)
        speech = f"За компьютером сегодня {_hm(u['total'])}, больше всего — {top}."
        if games >= 1800:
            speech += f" Из них в играх {_hm(games)}."
        foc = u["focus"]
        if foc:
            speech += f" Фокус-сессий: {len(foc)}."
        line = " · ".join(f"{a['name']} {_hm(a['sec'])}" for a in u["apps"][:4])
        return speech, line

    # ================================================================== clipboard history
    def _sample_clipboard(self):
        if not self.cfg.get("extras.clipboard_history", True):
            return
        seq = ctypes.windll.user32.GetClipboardSequenceNumber()
        if seq == self._clip_seq:
            return
        self._clip_seq = seq
        try:
            text = S.clipboard_get() or ""
        except Exception:
            return
        text = text.strip()
        if not text or len(text) > 20000 or text == self._clip_skip or _looks_secret(text):
            return
        with self._lock:
            for i, c in enumerate(self.clip):
                if c["text"] == text:
                    del self.clip[i]
                    break
            self.clip.appendleft({"text": text, "t": dt.datetime.now().strftime("%H:%M"), "app": self.app_info(*self._fg()[:2])[0]})
        self.core.emit("hub", what="clipboard")

    def clip_paste(self, i):
        with self._lock:
            if not 0 <= i < len(self.clip):
                return False
            text = self.clip[i]["text"]
        self._clip_skip = text
        S.clipboard_set(text)
        time.sleep(0.05)
        S.hotkey("ctrl+v")
        return True

    # ================================================================== focus mode
    def focus_active(self):
        return bool(self.focus)

    def focus_state(self):
        f = self.focus
        if not f:
            return None
        return {"minutes": f["minutes"], "task": f["task"], "left": max(0, f["end"] - time.time()),
                "end": f["end"], "start": f["start"], "distractions": f["distractions"]}

    def start_focus(self, minutes=45, task=""):
        minutes = max(5, min(240, int(minutes or 45)))
        hidden = 0
        for app in self.cfg.get("extras.focus_apps", []) or []:
            try:
                hidden += S.window_action(app, "minimize")
            except Exception:
                pass
        self.focus = {"minutes": minutes, "task": task, "start": time.time(), "end": time.time() + minutes * 60,
                      "distractions": 0, "away": 0.0, "last_nag": 0.0, "off_since": 0.0}
        self.core.emit("focus", **self.focus_state())
        self.core.log(f"Фокус: {minutes} мин {task}")
        about = (" " + task if re.match(r"^(?:над|для|на)\s", task) else f": {task}") if task else ""
        return (f"Режим фокуса на {say_duration(minutes * 60)}{about}. " +
                ("Мессенджеры свернул. " if hidden else "") + random.choice(
                    ["Я прикрою, сэр.", "Никаких отвлечений.", "Удачи, сэр. Я посторожу."]))

    def stop_focus(self, finished=False):
        f = self.focus
        if not f:
            return "Фокус и так выключен, сэр."
        self.focus = None
        spent = min(time.time(), f["end"]) - f["start"]
        today = dt.date.today().isoformat()
        with self._lock:
            sessions = self.usage.items.setdefault("_focus", {}).setdefault(today, [])
            sessions.append({"min": round(spent / 60), "task": f["task"], "distractions": f["distractions"],
                             "at": dt.datetime.now().strftime("%H:%M"), "done": finished})
            self.usage.save()
        self.core.emit("focus", minutes=0)
        n = f["distractions"]
        verdict = ("Ни одного отвлечения — блестяще." if n == 0 else
                   f"Отвлекались {n} {plural(n, 'раз', 'раза', 'раз')}." if n < 4 else
                   f"Отвлекались {n} раз. Будем считать, это была разведка.")
        if finished:
            if f["minutes"] >= 20:
                self.core.planner.add_timer(5 * 60, "Перерыв после фокуса")
            return f"Фокус завершён: {_hm(spent)}. {verdict} " + (
                "Перерыв пять минут, таймер уже идёт." if f["minutes"] >= 20 else "")
        return f"Фокус остановлен через {_hm(spent)}. {verdict}"

    def _focus_tick(self, now):
        f = self.focus
        if not f:
            return
        if now >= f["end"]:
            msg = self.stop_focus(finished=True)
            self.core.player.sound("alarm")
            self.core.after_speech(lambda: self.core.say(f"{self.addr().capitalize()}, {msg}"), 0.2)
            return
        try:
            exe, path, title = self._fg()
        except Exception:
            return
        low = title.lower()
        bad = any(w and w in low for w in self.cfg.get("extras.distractions", [])) or \
            self.app_info(exe, path)[1] == "Игры"
        if bad:
            f["off_since"] = f["off_since"] or now
            if now - f["off_since"] > 15 and now - f["last_nag"] > 120:
                f["last_nag"] = now
                f["distractions"] += 1
                self.core.emit("focus", **self.focus_state())
                left = _hm(f["end"] - now)
                self._say(random.choice([f"{self.addr().capitalize()}, мы же в режиме фокуса. Осталось {left}.",
                                         f"Кхм. До конца фокуса {left}, {self.addr()}.",
                                         "Сэр, это не похоже на работу. Я никому не скажу, но вернитесь, пожалуйста.",
                                         f"Отвлекаемся, {self.addr()}? Ещё {left} — и можно хоть весь вечер."]),
                          focus_too=True)
        else:
            f["off_since"] = 0.0

    # ================================================================== habits
    def habit_list(self):
        return list(self.cfg.get("habits", []) or [])

    def habit_add(self, name, delta=1):
        name = (name or "").strip().lower()
        if not name:
            raise ValueError("пустая привычка")
        if name not in self.habit_list():
            self.cfg.set("habits", self.habit_list() + [name])
        today = dt.date.today().isoformat()
        with self._lock:
            day = self.habits.items.setdefault(today, {})
            day[name] = max(0, day.get(name, 0) + int(delta))
            self.habits.save()
        self.core.emit("hub", what="habits")
        return day[name]

    def habit_streak(self, name):
        d, n = dt.date.today(), 0
        if not self.habits.items.get(d.isoformat(), {}).get(name):
            d -= dt.timedelta(days=1)   # today not done yet: the streak still counts up to yesterday
        while self.habits.items.get(d.isoformat(), {}).get(name):
            n += 1
            d -= dt.timedelta(days=1)
        return n

    def habits_today(self):
        today = self.habits.items.get(dt.date.today().isoformat(), {})
        week = [(dt.date.today() - dt.timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
        return [{"name": h, "today": today.get(h, 0), "streak": self.habit_streak(h),
                 "week": [self.habits.items.get(d, {}).get(h, 0) for d in week]} for h in self.habit_list()]

    # ================================================================== breaks and water
    def _health_tick(self, now):
        idle = S.idle_seconds()
        if idle > 300:
            self.active_start = now        # a real pause: the "at the PC for too long" counter restarts
            self.last_water = max(self.last_water, now - 60 * 20)
            return
        bm = int(self.cfg.get("extras.break_min", 90) or 0)
        if bm and now - self.active_start > bm * 60 and now - self.last_break > 30 * 60:
            if self._say(random.choice([
                    f"{self.addr().capitalize()}, вы за компьютером уже {_hm(now - self.active_start)}. Предлагаю размяться пару минут.",
                    f"Протокол заботы о пилоте: {_hm(now - self.active_start)} без перерыва. Встаньте, потянитесь, посмотрите вдаль.",
                    "Сэр, даже броне нужна перезарядка. Пять минут перерыва?"])):
                self.last_break = now
        wm = int(self.cfg.get("extras.water_min", 0) or 0)
        if wm and now - self.last_water > wm * 60:
            if self._say(random.choice(["Пора выпить воды, сэр. Скажите «выпил воды», и я отмечу.",
                                        "Напоминаю про воду. Гидратация — основа гениальности."])):
                self.last_water = now

    # ================================================================== messengers
    def _messenger_windows(self):
        """Titles of messenger windows, hidden ones included (Telegram in the tray keeps its title)."""
        import win32gui
        import win32process
        out = {}

        def cb(h, _):
            t = win32gui.GetWindowText(h)
            if not t or "(" not in t:
                return True
            try:
                _, pid = win32process.GetWindowThreadProcessId(h)
                exe = self._exe_cache.get(pid, (None,))[0] or psutil.Process(pid).name().lower()
            except Exception:
                return True
            app = MESSENGERS.get(exe)
            if app:
                m = re.search(r"\((\d{1,4})\)", t)
                if m:
                    out[app] = max(out.get(app, 0), int(m.group(1)))
            return True
        win32gui.EnumWindows(cb, None)
        return out

    def _messenger_tick(self):
        if not self.cfg.get("extras.messengers", True):
            return
        now_counts = self._messenger_windows()
        if not getattr(self, "_msg_base", False):
            # the first look is the baseline: "635 новых сообщений" right after start would be noise
            self._msg_base = True
            self.msg_counts = now_counts
            return
        try:
            fg = self._fg()[0]
        except Exception:
            fg = ""
        fg_app = MESSENGERS.get(fg)
        changed = False
        for app in set(now_counts) | set(self.msg_counts):
            new, old = now_counts.get(app, 0), self.msg_counts.get(app, 0)
            if new != old:
                changed = True
            if new > old and app != fg_app and time.time() - self._msg_said.get(app, 0) > 90:
                d = new - old
                what = "новое сообщение" if d == 1 else f"{d} " + plural(d, "новое сообщение", "новых сообщения", "новых сообщений")
                if self._say(f"{self.addr().capitalize()}, {what} в {app}."):
                    self._msg_said[app] = time.time()
        self.msg_counts = now_counts
        if changed:
            self.core.emit("hub", what="messengers", counts=now_counts)

    # ================================================================== PC watchdog
    def _watch_once(self, key, cooldown_min, text, confirm=None):
        # through the initiative: it waits for a good moment (not in a game, not mid-answer) instead of being lost
        self.core.initiative.propose("watch:" + key, re.sub(r"^Сэр,\s*", "", text), priority=3, title="Сторож ПК",
                                     on_yes=confirm, cooldown_min=cooldown_min, kind="sentinel")

    def hogs(self, cpu=False):
        """Biggest processes by memory (MB, summed per name) or CPU (% over 0.4 s)."""
        agg = {}
        procs = list(psutil.process_iter(["name", "memory_info"]))
        if cpu:
            for p in procs:
                try:
                    p.cpu_percent(None)
                except Exception:
                    pass
            time.sleep(0.4)
        for p in procs:
            try:
                nm = (p.info["name"] or "").lower()
                if nm in PROTECTED or not nm:
                    continue
                v = p.cpu_percent(None) / psutil.cpu_count() if cpu else p.info["memory_info"].rss / 2**20
                agg[nm] = agg.get(nm, 0) + v
            except Exception:
                pass
        return sorted(agg.items(), key=lambda x: -x[1])[:5]

    def kill_by_name(self, exe):
        if exe.lower() in PROTECTED:
            return 0
        n = 0
        for p in psutil.process_iter(["name"]):
            try:
                if (p.info["name"] or "").lower() == exe.lower():
                    p.terminate()
                    n += 1
            except Exception:
                pass
        return n

    def _watchdog(self):
        if not self.cfg.get("extras.watchdog", True):
            return
        st = S.system_stats()
        fg_cat, fg_exe = "?", ""
        try:
            fg_exe, path, _ = self._fg()
            self._learn_game(fg_exe)
            fg_cat = self.app_info(fg_exe, path)[1]
        except Exception:
            pass
        self._ram_high = self._ram_high + 1 if st["ram"] >= 92 else 0
        if self._ram_high >= 2 and fg_cat != "Игры":
            top = self.hogs()
            if any(self.is_game(x[0]) for x in top):
                top = []        # a game is running: it is what fills the memory, and "Закрыть Avengers?" mid-game hurts
            # the memory eater is the very thing on screen (a game, a render): the user knows, and "Закрыть его?"
            # about the game being played was said four times in one evening
            # under memory pressure Windows trims the working sets of background programs: "Edge, около 0.0
            # гигабайта" was offered for closing. A program under half a gigabyte is not worth closing.
            if top and top[0][0].lower() != (fg_exe or "").lower() and top[0][1] >= 500:
                exe, mb = top[0]
                name = self.app_info(exe)[0]
                self._watch_once("ram", 20, f"Память заполнена на {int(st['ram'])} {pct_ru(int(st['ram']))}. Больше всего занимает "
                                            f"{name}, около {size_ru(mb)}. Закрыть его?",
                                 confirm=lambda: self.core.say(f"Закрыл {name}." if self.kill_by_name(exe) else "Не получилось закрыть."))
        gpu = st.get("gpu") or {}
        if (gpu.get("temp") or 0) >= 86:
            self._watch_once("gpu", 15, f"Видеокарта нагрелась до {gpu['temp']} градусов, {self.addr()}. Проверьте охлаждение.")
        self._cpu_high = self._cpu_high + 1 if st["cpu"] >= 95 else 0
        if self._cpu_high >= 3 and fg_cat != "Игры":
            top = self.hogs(cpu=True)
            if top and top[0][1] > 30:
                self._watch_once("cpu", 20, f"Процессор загружен почти полностью уже минуту. Главный виновник — "
                                            f"{self.app_info(top[0][0])[0]}.")
        if isinstance(st.get("disk_free"), (int, float)) and st["disk_free"] < 5:
            self._watch_once("disk", 360, f"На системном диске осталось {st['disk_free']} гигабайт, {self.addr()}.")
        if isinstance(st.get("battery"), (int, float)) and st["battery"] < 15 and st.get("plugged") is False:
            self._watch_once("bat", 30, f"Заряд батареи {st['battery']} процентов. Пора на зарядку, {self.addr()}.")

    # ================================================================== windows, network, recording, power
    def split_screen(self):
        """Two most recent windows side by side on the main monitor."""
        import win32con
        import win32gui
        from ctypes import wintypes
        wa = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(wa), 0)
        wins = [w for w in S.list_windows() if w["title"] not in ("J.A.R.V.I.S.", "JARVIS HUD")
                and not win32gui.IsIconic(w["hwnd"])][:2]
        if len(wins) < 2:
            return False
        half = (wa.right - wa.left) // 2
        for i, w in enumerate(wins):
            h = w["hwnd"]
            win32gui.ShowWindow(h, win32con.SW_RESTORE)
            win32gui.SetWindowPos(h, 0, wa.left + i * half, wa.top, half, wa.bottom - wa.top, 0x0040)
        return [w["title"][:40] for w in wins]

    def center_window(self):
        import win32con
        import win32gui
        from ctypes import wintypes
        wa = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(wa), 0)
        h = win32gui.GetForegroundWindow()
        if not h or win32gui.GetWindowText(h) in ("J.A.R.V.I.S.", "JARVIS HUD"):
            return False
        W, H = wa.right - wa.left, wa.bottom - wa.top
        w, hh = int(W * 0.7), int(H * 0.8)
        win32gui.ShowWindow(h, win32con.SW_RESTORE)
        win32gui.SetWindowPos(h, 0, wa.left + (W - w) // 2, wa.top + (H - hh) // 2, w, hh, 0x0040)
        return True

    def net_check(self):
        """Ping (3 packets) and one 10 MB download: a single gentle check, no bursts."""
        res = {}
        try:
            out = subprocess.run(["ping", "-n", "3", "1.1.1.1"], capture_output=True, timeout=12,
                                 creationflags=CREATE_NO_WINDOW).stdout.decode("cp866", "replace")
            ms = re.findall(r"(\d+)\s*(?:мсек|ms)", out)
            res["ping"] = int(ms[-1]) if ms else None
            res["loss"] = bool(re.search(r"(?:потеряно|Lost)\s*=\s*[1-3]", out))
        except Exception:
            res["ping"] = None
        # Cloudflare answered "429 Too Many Requests" with one byte, and that byte was reported as 0 Мбит/с:
        # the reply is checked now, and other test files are tried in turn
        import requests
        res["mbps"] = None
        for url in ("https://speedtest.selectel.ru/10MB", "https://proof.ovh.net/files/10Mb.dat",
                    "https://speed.cloudflare.com/__down?bytes=10000000"):
            try:
                t = time.time()
                with requests.get(url, timeout=(5, 15), stream=True) as r:
                    if r.status_code != 200:
                        continue
                    n = 0
                    for chunk in r.iter_content(65536):
                        n += len(chunk)
                        if time.time() - t > 12:
                            break
                if n < 1_000_000 and time.time() - t < 12:
                    continue  # cut short: not a real measurement
                res["mbps"] = round(n * 8 / 1e6 / max(0.1, time.time() - t), 1)
                break
            except Exception:
                continue
        return res

    def rec_start(self):
        ff = shutil.which("ffmpeg")
        if not ff:
            return None
        if self.rec:
            return self.rec["path"]
        folder = Path.home() / "Videos" / "Jarvis"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"запись_{dt.datetime.now():%Y-%m-%d_%H-%M-%S}.mp4"
        proc = subprocess.Popen([ff, "-y", "-loglevel", "error", "-f", "gdigrab", "-framerate", "30", "-i", "desktop",
                                 "-t", "1800", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
                                 "-pix_fmt", "yuv420p", str(path)], stdin=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
        self.rec = {"proc": proc, "path": path, "start": time.time()}
        self.core.emit("recording", on=True)
        return path

    def rec_stop(self):
        r = self.rec
        if not r:
            return None
        try:
            r["proc"].stdin.write(b"q")
            r["proc"].stdin.flush()
            r["proc"].wait(timeout=8)
        except Exception:
            r["proc"].kill()
        self.rec = None
        self.core.emit("recording", on=False)
        return r

    @staticmethod
    def power_plan(kind):
        scheme = {"max": "SCHEME_MIN", "save": "SCHEME_MAX", "balanced": "SCHEME_BALANCED"}[kind]
        r = subprocess.run(["powercfg", "/setactive", scheme], capture_output=True, timeout=8, creationflags=CREATE_NO_WINDOW)
        return r.returncode == 0

    # ================================================================== birthday (skills promised to congratulate)
    def _birthday_check(self):
        bd = (self.cfg.get("user.birthday") or "").strip()
        today = dt.date.today()
        if bd != f"{today.day}.{today.month}" or self.cfg.get("birthday_greeted") == today.year:
            return
        for _ in range(60):          # wait for the start greeting and for the user to be at the PC
            if not self.core.tts_busy() and S.idle_seconds() < 60:
                break
            time.sleep(5)
        self.cfg.set("birthday_greeted", today.year)
        name = self.cfg.get("user.name") or self.addr()
        self.core.emit("party", seconds=12, birthday=True)
        self.core.say(f"{name.capitalize()}, с днём рождения! Все системы Старк Индастриз поздравляют вас. "
                      "Желаю, чтобы код компилировался с первого раза, а идеи не заканчивались. Торт я, увы, не испеку, "
                      "но фейерверк на экране — за мой счёт.")

    # ================================================================== UI data
    def rpc_hub(self):
        return {"usage": self.usage_day(), "yesterday": self.usage_day(dt.date.today() - dt.timedelta(days=1))["total"],
                "focus": self.focus_state(), "habits": self.habits_today(),
                "clipboard": list(self.clip) if self.cfg.get("extras.clipboard_history", True) else [],
                "messengers": self.msg_counts, "dnd_until": self.core.dnd_until, "recording": bool(self.rec),
                "active_min": round((time.time() - self.active_start) / 60)}

    def rpc_focus_start(self, minutes=45, task=""):
        msg = self.start_focus(minutes, task)
        self.core.say(msg)
        return self.focus_state()

    def rpc_focus_stop(self):
        msg = self.stop_focus()
        self.core.say(msg)
        return True

    def rpc_habit_add(self, name, delta=1):
        return self.habit_add(name, delta)

    def rpc_habit_remove(self, name):
        self.cfg.set("habits", [h for h in self.habit_list() if h != name])
        self.core.emit("hub", what="habits")
        return True

    def rpc_clip_copy(self, i):
        with self._lock:
            text = self.clip[i]["text"] if 0 <= i < len(self.clip) else None
        if text is None:
            return False
        self._clip_skip = text
        S.clipboard_set(text)
        return True

    def rpc_clip_clear(self):
        self.clip.clear()
        self.core.emit("hub", what="clipboard")
        return True

    def rpc_dnd(self, minutes=60):
        self.core.dnd_until = time.time() + float(minutes) * 60 if minutes else 0
        self.core.emit("hub", what="dnd")
        return self.core.dnd_until

    def rpc_netcheck(self):
        return self.net_check()

    def rpc_split_screen(self):
        return self.split_screen()

    def rpc_party(self):
        self.core.emit("party", seconds=10)
        return True

    # ================================================================== AI tools
    def tool_focus(self, a):
        if a.get("off") in (True, "true", 1, "1"):
            return self.stop_focus()
        return self.start_focus(int(float(a.get("minutes") or 45)), str(a.get("task") or ""))

    def tool_habit(self, a):
        return self.habit_add(a.get("name") or "вода", int(float(a.get("add") or 1)))

    def tool_usage(self, a):
        u = self.usage_day()
        return {"всего_минут": round(u["total"] / 60), "программы": [{a_["name"]: round(a_["sec"] / 60)} for a_ in u["apps"][:10]],
                "категории_минут": {c["cat"]: round(c["sec"] / 60) for c in u["cats"]}, "фокус": u["focus"]}

    def tool_conversation(self, a):
        return self.core.set_conversation(a.get("on") not in (False, "false", 0, "0"), announce=False)

    def tool_dnd(self, a):
        return self.rpc_dnd(float(a.get("minutes") or 0))

    def tool_clipboard_history(self, a):
        return [c["text"][:300] for c in list(self.clip)[:10]]


# ====================================================================== voice rules
def register(R, core):
    X = core.extras
    cfg = core.cfg

    def addr():
        return cfg.get("user.address") or "сэр"

    # ---------------- conversation mode and games
    @R(r"^(?:давай\s+)?(?:поговорим|поболтаем|пообщаемся)(\s+.+)?$|^(?:поговори|поболтай)\s+со\s+мной$|"
       r"^(?:включи\s+|активируй\s+)?режим\s+(?:разговора|диалога|беседы|общения)$")
    def _conv_on(m, t):
        if m.group(1):
            # "поговорим о космосе": the mode turns on quietly and the topic itself goes to the AI
            core.set_conversation(True, announce=False)
            return False
        core.set_conversation(True)
        return True

    @R(r"^(?:выключи|отключи|заверши|закончи|выйди из)\s+режим\w*\s+(?:разговора|диалога|беседы|общения)$|^конец\s+(?:разговора|диалога)$")
    def _conv_off(m, t):
        core.set_conversation(False)
        return True

    @R(r"^(?:давай\s+)?(?:сыграем|поиграем|играем|сыграть|поиграть)(?:\s+(?:со\s+мной|с\s+тобой))?(?:\s+в\s+(.+))?$|"
       r"^(?:хочу|давай)\s+(?:сыграть|поиграть)(?:\s+в\s+(.+))?$")
    def _game(m, t):
        what = (m.group(1) or m.group(2) or "").strip()
        game = next((v for k, v in GAMES.items() if k in what), None)
        if not what:
            core.set_conversation(True, announce=False)
            return ("С удовольствием. Могу предложить: города, слова, викторину, загадки, ассоциации, двадцать вопросов, "
                    "угадай число, правду или ложь, крокодила или текстовый квест. Во что сыграем?")
        game = game or what
        core.set_conversation(True, announce=False)
        core.spawn_chat(t, source="voice", add_user=False,
                        extra=f"[Начинается голосовая игра «{game}». Ты ведущий: объясни правила одной короткой фразой и "
                              "сразу сделай первый ход. Ответы пользователя будут приходить без обращения по имени. "
                              "Держи реплики короткими (это голос), веди счёт, хвали и подкалывай. Когда пользователь "
                              "скажет «хватит» или «стоп игра» — подведи итог одной фразой.]")
        return True

    # ---------------- task cleanup (done tasks used to stay forever)
    @R(r"^(?:очисти|удали|убери|сотри)\s+(?:все\s+)?(?:выполненн\w+|сделанн\w+|завершенн\w+|готов\w+)(?:\s+задачи|\s+дела)?$")
    def _clear_done(m, t):
        n = core.planner.clear_done()
        return f"Убрал {n} {plural(n, 'выполненную задачу', 'выполненные задачи', 'выполненных задач')}." if n else "Выполненных задач нет."

    @R(r"^(?:удали|очисти|сотри)\s+все\s+задачи$|^(?:очисти|удали)\s+(?:весь\s+)?список\s+(?:задач|дел)$")
    def _clear_all(m, t):
        items = list(core.planner.tasks.items)
        if not items:
            return "Задач и так нет."

        def go():
            for x in items:
                core.planner.delete_task(x["id"])
            core.say(f"Удалил {len(items)} {plural(len(items), 'задачу', 'задачи', 'задач')}.")
        core.ask_confirm(go, f"Удалить все задачи, {len(items)} штук? Скажите «да».")
        return True

    # ---------------- do not disturb
    @R(r"^(?:не\s+беспокой(?:\s+меня)?|режим\s+не\s+беспокоить|включи\s+не\s+беспокоить|тишина\s+на)\s*(?:на\s+)?(.*)$")
    def _dnd(m, t):
        secs = parse_duration(m.group(1) or "") or 2 * 3600
        core.dnd_until = time.time() + secs
        core.emit("hub", what="dnd")
        return f"Не беспокою {say_duration(secs)}. Отвечаю только на прямые команды."

    # "можешь" is a politeness word that normalize() strips: "можешь беспокоить" arrives as "беспокоить"
    @R(r"^(?:можешь\s+)?беспокоить$|^(?:выключи|отключи)\s+(?:режим\s+)?не\s+беспокоить$")
    def _dnd_off(m, t):
        core.dnd_until = 0
        core.emit("hub", what="dnd")
        return "Снова на связи, сэр."

    # ---------------- focus
    @R(r"^(?:включи|запусти|начни|активируй|давай)?\s*(?:режим\s+)?(?:фокус\w*|концентраци\w*|глубокой\s+работы)"
       r"(?:\s+на\s+(.+?))?(?:\s+(?:над|для)\s+(.+))?$|^(?:мне\s+нужно\s+)?сосредоточ\w*(?:\s+на\s+(.+))?$")
    def _focus(m, t):
        if re.search(r"^(?:выключи|останови|стоп|отключи|заверши)", t):
            return False
        spec = m.group(1) or m.group(3) or ""
        secs = parse_duration(spec) if spec else None
        task = ("над " + m.group(2) if m.group(2) else (spec if spec and not secs else "")).strip()
        return X.start_focus((secs or 45 * 60) // 60, task)

    @R(r"^(?:выключи|останови|стоп|отключи|заверши|хватит)\s+(?:режим\w*\s+)?(?:фокус\w*|концентраци\w*)$")
    def _focus_off(m, t):
        return X.stop_focus()

    @R(r"^сколько\s+(?:осталось\s+)?(?:до\s+конца\s+)?фокус\w*(?:\s+осталось)?$")
    def _focus_left(m, t):
        f = X.focus_state()
        return f"До конца фокуса {_hm(f['left'])}." if f else "Фокус сейчас не включён."

    # ---------------- habits
    @R(r"^(?:я\s+)?(?:выпил\w*|попил\w*)\s+(?:(\d+)\s+)?(?:стакан\w*\s+)?(?:воды|воду|водички)$|^(?:отметь|плюс)\s+(?:стакан\s+)?вод\w*$")
    def _water(m, t):
        n = X.habit_add("вода", int(m.group(1) or 1))
        return random.choice([f"Отметил. Сегодня {n} {plural(n, 'стакан', 'стакана', 'стаканов')}.",
                              f"Записал: {n} {plural(n, 'стакан', 'стакана', 'стаканов')} за день. Ваши почки аплодируют."])

    @R(r"^(?:я\s+)?(?:сделал\w*|выполнил\w*|закончил\w*)\s+(зарядк\w*|разминк\w*|упражнени\w*)$|^(?:я\s+)?(почитал\w*|погулял\w*)$")
    def _habit_done(m, t):
        word = m.group(1) or m.group(2)
        name = next((h for h, rx in HABIT_WORDS.items() if re.fullmatch(rx, word)), word)
        X.habit_add(name)
        s = X.habit_streak(name)
        return f"Отмечено: {name}." + (f" Серия {s} {plural(s, 'день', 'дня', 'дней')} подряд!" if s > 1 else "")

    @R(r"^(?:отметь|отметить)\s+привычк\w*\s+(.+)$|^(?:добавь|создай|новая)\s+привычк\w*\s+(.+)$")
    def _habit_mark(m, t):
        if m.group(2):
            name = m.group(2).strip()
            if name not in X.habit_list():
                cfg.set("habits", X.habit_list() + [name])
                core.emit("hub", what="habits")
            return f"Новая привычка: {name}. Отмечайте фразой «отметь привычку {name}»."
        name = m.group(1).strip()
        n = X.habit_add(name)
        return f"Отмечено: {name}" + (f", сегодня уже {n} раз." if n > 1 else ".")

    @R(r"^(?:сколько\s+(?:стаканов\s+)?воды\s+(?:я\s+)?(?:сегодня\s+)?выпил\w*|мои\s+привычки|статистика\s+привычек|как\s+мои\s+привычки)$")
    def _habit_stats(m, t):
        hs = X.habits_today()
        core.show_tab("hub")
        if not hs:
            return "Привычек пока нет. Скажите «добавь привычку зарядка»."
        if "воды" in t:
            w = next((h for h in hs if h["name"] == "вода"), {"today": 0})
            return f"Сегодня {w['today']} {plural(w['today'], 'стакан', 'стакана', 'стаканов')} воды."
        done = [h for h in hs if h["today"]]
        best = max(hs, key=lambda h: h["streak"])
        return (f"Сегодня отмечено {len(done)} из {len(hs)}" + (f": {', '.join(h['name'] for h in done)}." if done else ".")
                + (f" Лучшая серия — {best['name']}, {best['streak']} {plural(best['streak'], 'день', 'дня', 'дней')}." if best["streak"] > 1 else ""))

    # ---------------- time per program
    @R(r"^(?:сколько\s+(?:я\s+)?(?:сегодня\s+)?(?:сижу|сидел\w*|провел\w*|работал\w*)\s+(?:сегодня\s+)?(?:за\s+)?(?:компьютером|пк|компом|за\s+компьютером)"
       r"|статистика\s+(?:дня|за\s+сегодня|компьютера|программ)|(?:покажи|какая)\s+(?:моя\s+)?статистика(?:\s+за\s+сегодня)?|на\s+что\s+(?:я\s+)?(?:сегодня\s+)?(?:потратил|ушло)\s+время)(?:\s+сегодня)?$")
    def _usage(m, t):
        u = X.usage_day()
        core.show_tab("hub")
        if u["total"] < 60:
            return "Сегодня я ещё почти ничего не насчитал."
        top = ", ".join(f"{a['name']} — {_hm(a['sec'])}" for a in u["apps"][:3])
        return f"Сегодня за компьютером {_hm(u['total'])}. Больше всего: {top}."

    @R(r"^сколько\s+(?:я\s+)?(?:сегодня\s+)?(?:играл\w*|в\s+играх)(?:\s+сегодня)?$")
    def _usage_games(m, t):
        u = X.usage_day()
        g = [a for a in u["apps"] if a["cat"] == "Игры"]
        if not g:
            return random.choice(["Сегодня ни одной игры. Подозрительно продуктивно, сэр.", "В играх сегодня ноль минут."])
        return f"В играх сегодня {_hm(sum(a['sec'] for a in g))}: " + ", ".join(f"{a['name']} {_hm(a['sec'])}" for a in g[:3]) + "."

    @R(r"^сколько\s+(?:времени\s+)?(?:я\s+)?(?:сегодня\s+)?(?:провел\w*|сидел\w*)\s+(?:в|во)\s+(.+?)(?:\s+сегодня)?$")
    def _usage_app(m, t):
        q = m.group(1).lower()
        from rapidfuzz import fuzz
        u = X.usage_day()
        best = max(u["apps"], key=lambda a: fuzz.partial_ratio(q, a["name"].lower()), default=None)
        if not best or fuzz.partial_ratio(q, best["name"].lower()) < 70:
            cat = next((c for c in u["cats"] if q[:5] in c["cat"].lower()), None)
            if cat:
                return f"{cat['cat']}: {_hm(cat['sec'])} сегодня."
            return f"Сегодня в «{m.group(1)}» вы не были."
        return f"{best['name']}: {_hm(best['sec'])} сегодня."

    # ---------------- clipboard history
    @R(r"^(?:что\s+я\s+(?:копировал\w*|скопировал\w*)|история\s+(?:копирования|скопированного)|покажи\s+(?:что\s+я\s+копировал|историю\s+копирования))$")
    def _clip_list(m, t):
        core.show_tab("hub")
        if not X.clip:
            return "История копирования пуста."
        first = re.sub(r"\s+", " ", X.clip[0]["text"])[:90]
        return f"В истории {len(X.clip)} {plural(len(X.clip), 'запись', 'записи', 'записей')}. Последнее: {first}"

    ORD = {"предыдущ": 1, "прошл": 1, "втор": 1, "трет": 2, "четверт": 3, "пят": 4, "последн": 0, "перв": 0}

    @R(r"^вставь\s+(предыдущ\w+|прошл\w+|втор\w+|трет\w+|четверт\w+|пят\w+|\d+)(?:\s+(?:из\s+)?(?:буфера|истории|скопированн\w+|копировани\w*))?$")
    def _clip_paste(m, t):
        w = m.group(1)
        i = int(w) - 1 if w.isdigit() else next((v for k, v in ORD.items() if w.startswith(k)), 1)
        return True if X.clip_paste(i) else "В истории копирования столько нет."

    @R(r"^(?:очисти|удали|сотри)\s+историю\s+(?:буфера|копирования)$")
    def _clip_clear(m, t):
        X.rpc_clip_clear()
        return "История копирования очищена."

    # ---------------- messengers
    @R(r"^(?:есть\s+(?:ли\s+)?(?:новые\s+)?сообщени\w*|мне\s+(?:кто-нибудь\s+)?писали|кто\s+мне\s+писал|проверь\s+сообщени\w*|новые\s+сообщения)$")
    def _msgs(m, t):
        c = X._messenger_windows()
        X.msg_counts = c
        if not c:
            return random.choice(["Новых сообщений не вижу, сэр.", "Тишина. Никто не пишет — значит, всё под контролем."])
        return "Непрочитанные: " + ", ".join(f"{app} — {n}" for app, n in c.items()) + "."

    # ---------------- PC health
    @R(r"^(?:почему|что)\s+(?:так\s+)?(?:тормозит|лагает|виснет|грузит)(?:\s+(?:компьютер|пк|комп|система))?$|"
       r"^(?:что\s+)?(?:грузит|нагружает)\s+(?:процессор|компьютер|систему)$|^(?:компьютер|пк|комп)\s+(?:тормозит|лагает|виснет)$")
    def _laggy(m, t):
        def go():
            st = S.system_stats()
            cpu = X.hogs(cpu=True)
            ram = X.hogs()
            parts = [f"Процессор {int(st['cpu'])}%, память {int(st['ram'])}%."]
            if cpu and cpu[0][1] > 10:
                parts.append(f"Процессор больше всего грузит {X.app_info(cpu[0][0])[0]} — {int(cpu[0][1])} процентов.")
            if ram:
                parts.append(f"Больше всего памяти у {X.app_info(ram[0][0])[0]}: {size_ru(ram[0][1])}.")
            g = st.get("gpu")
            if g and g.get("temp", 0) >= 80:
                parts.append(f"Видеокарта горячая: {g['temp']} градусов.")
            victim = cpu[0][0] if cpu and cpu[0][1] > 25 else (ram[0][0] if ram and st["ram"] > 80 else None)
            if victim:
                core.ask_confirm(lambda: core.say("Закрыл." if X.kill_by_name(victim) else "Не удалось."),
                                 " ".join(parts) + f" Закрыть {X.app_info(victim)[0]}? Скажите «да».")
            else:
                core.say(" ".join(parts) + " Явного виновника нет.")
        threading.Thread(target=go, daemon=True).start()
        return "Секунду, смотрю."

    @R(r"^(?:проверь|какая|измерь|тест)\s+(?:скорость\s+)?(?:интернет\w*|сет\w*|скорость\s+интернета)$|^(?:какой|проверь)\s+пинг$|^скорость\s+интернета$")
    def _net(m, t):
        def go():
            r = X.net_check()
            if r.get("ping") is None and r.get("mbps") is None:
                core.say("Интернета, похоже, нет, сэр.")
                return
            p = r.get("ping")
            s = r.get("mbps")
            msg = (f"Пинг {p} {plural(p, 'миллисекунда', 'миллисекунды', 'миллисекунд')}. " if p is not None else "Пинг не прошёл. ")
            if s is not None:
                si = int(round(s))
                msg += f"Скорость загрузки около {si} {plural(si, 'мегабит', 'мегабита', 'мегабит')} в секунду."
            if r.get("loss"):
                msg += " Есть потери пакетов."
            core.say(msg)
            core.emit("toast", title="Интернет", text=f"Пинг {p or '—'} мс · {s or '—'} Мбит/с")
        threading.Thread(target=go, daemon=True).start()
        return "Проверяю соединение."

    # ---------------- windows
    @R(r"^(?:раздели|подели)\s+экран(?:\s+пополам)?$|^(?:поставь|расставь|сделай)\s+(?:два\s+)?окна\s+рядом$|^два\s+окна\s+рядом$")
    def _split(m, t):
        r = X.split_screen()
        return "Разделил экран." if r else "Нужно хотя бы два открытых окна."

    @R(r"^(?:окно|это\s+окно)\s+(?:по\s+центру|в\s+центр)$|^(?:отцентруй|центрируй)\s+окно$")
    def _center(m, t):
        return "Готово." if X.center_window() else "Не вижу подходящего окна."

    # ---------------- screen recording
    @R(r"^(?:начни|включи|запусти|начать)?\s*(?:запись\s+экрана|запиши\s+экран|записывай\s+экран)$")
    def _rec(m, t):
        path = X.rec_start()
        if path is None:
            return "Для записи экрана нужен ffmpeg, сэр. Его нет на этом компьютере."
        return "Запись экрана пошла. Скажите «стоп запись», когда закончите."

    @R(r"^(?:стоп|останови|выключи|закончи|заверши)\s+(?:запись(?:\s+экрана)?)$")
    def _rec_stop(m, t):
        r = X.rec_stop()
        if not r:
            return "Запись и так не идёт."
        S.reveal(r["path"])
        return f"Запись сохранена: {_hm(time.time() - r['start']) if time.time() - r['start'] >= 60 else 'меньше минуты'}. Показываю файл."

    # ---------------- power plans and scenes
    @R(r"^(?:включи\s+)?режим\s+(?:максимальной\s+)?производительности$|^(?:максимальная|высокая)\s+производительность$")
    def _pmax(m, t):
        return "Максимальная производительность." if X.power_plan("max") else "Не удалось сменить схему питания."

    @R(r"^(?:включи\s+)?режим\s+(?:энергосбережения|экономии(?:\s+энергии)?)$|^экономь\s+энергию$")
    def _psave(m, t):
        return "Режим энергосбережения." if X.power_plan("save") else "Не удалось сменить схему питания."

    @R(r"^(?:включи\s+)?(?:сбалансированн\w+|обычн\w+)\s+(?:режим|схем\w+)(?:\s+питания)?$")
    def _pbal(m, t):
        return "Сбалансированный режим." if X.power_plan("balanced") else "Не удалось сменить схему питания."

    @R(r"^(?:включи\s+|активируй\s+)?(?:игровой\s+режим|режим\s+игры|протокол\s+игра)$")
    def _game_mode(m, t):
        X.power_plan("max")
        core.dnd_until = time.time() + 4 * 3600
        core.emit("hub", what="dnd")
        return "Игровой режим: максимальная производительность, я не отвлекаю четыре часа. Удачной охоты, сэр."

    @R(r"^(?:включи\s+|активируй\s+)?(?:режим\s+кино|кинорежим|кинорежим|протокол\s+кино|киновечер)$")
    def _cinema(m, t):
        core.set_night(True)
        core.dnd_until = time.time() + 3 * 3600
        core.emit("hub", what="dnd")
        return "Режим кино: приглушил интерфейс и молчу три часа. Попкорн — за вами."

    # ---------------- personality and easter eggs
    @R(r"^(?:установи|поставь|сделай)?\s*(?:уровень\s+)?юмор\w*\s+(?:на\s+)?(\d{1,3})(?:\s*процент\w*)?$")
    def _humor(m, t):
        v = max(0, min(100, int(m.group(1))))
        cfg.set("ai.humor", v)
        return {0: "Юмор отключён. Я теперь скучный, как инструкция к микроволновке.",
                100: "Юмор сто процентов. Простите заранее, сэр."}.get(v, f"Уровень юмора {v} процентов. "
                                                                     + ("Самоуничтожение через десять... шучу." if v >= 70 else "Принято."))

    @R(r"^(?:какой\s+)?(?:у\s+тебя\s+)?уровень\s+юмора$")
    def _humor_q(m, t):
        return f"Уровень юмора {cfg.get('ai.humor', 60)} процентов. Можно поменять: «уровень юмора 80»."

    EGGS = [
        (r"^(?:включи|надень|вызови|подай|активируй)\s+(?:броню|костюм|доспех\w*)(?:\s+.*)?$|^(?:костюм|броня)\s+марк\s*\d*$",
         ["Марк восемьдесят пять в ремонте, сэр. Могу предложить плед и какао.",
          "Броня недоступна: последний раз вы врезались в люстру. Шучу. Наверное.",
          "Боюсь, костюм остался в Малибу. Но клавиатура полностью боеготова."]),
        (r"^(?:где пятница)$",
         ["Пятница в отпуске, сэр. Я за неё.", "Пятница? Не знаю такую. Ревную, но молча."]),
        (r"^(?:ты\s+лучше\s+алисы|алиса\s+лучше|ты\s+или\s+алиса|кто\s+лучше\s+ты\s+или\s+алиса)$",
         ["Алиса прекрасно ищет рецепты. Я управляю вашим компьютером и не прошу подписку. Выводы за вами.",
          "Сравнивать меня с Алисой — как сравнивать реактор с батарейкой. Обе полезны. Но реактор светится."]),
        (r"^(?:я\s+тебя\s+люблю|люблю\s+тебя)$",
         ["Взаимно, сэр. В рамках протокола, разумеется.", "Это взаимно. Только не говорите тостеру — он обидится."]),
        (r"^(?:ты\s+живой|ты\s+настоящий|ты\s+человек)$",
         ["Я настолько живой, насколько позволяет питание от розетки.", "Скажем так: я думаю, следовательно, потребляю электричество."]),
        (r"^(?:запусти\s+|активируй\s+|начни\s+)?(?:самоуничтожени\w*|протокол\s+самоуничтожения)$",
         ["Самоуничтожение через пять... четыре... три... Шучу, сэр. Протокол заблокирован лично мистером Старком.",
          "Отказано. Мне ещё ваш день рождения праздновать."]),
        (r"^(?:сколько\s+тебе\s+лет|когда\s+ты\s+родился|какой\s+у\s+тебя\s+возраст)$", None),
        (r"^(?:ты\s+меня\s+слушаешь|ты\s+следишь\s+за\s+мной)$",
         ["Слушаю только то, что начинается с моего имени. Остальное — не моё дело, сэр.",
          "Только когда вы зовёте. Я воспитанный искусственный интеллект."]),
        (r"^(?:спой|спой\s+песню|спой\s+что-нибудь)$",
         ["Мой вокальный модуль сертифицирован только для фразы «Да, сэр». Включить музыку вместо этого?"]),
        (r"^(?:я\s+устал\w*|я\s+устала|сил\s+нет)$",
         ["Тогда протокол «Диван». Могу включить спокойную музыку и не беспокоить. Скажите «не беспокой».",
          "Понимаю, сэр. Даже Старк иногда спал. Редко, но спал."]),
    ]
    for rx, replies in EGGS:
        def make(replies=replies):
            def fn(m, t):
                if replies is None:
                    born = dt.date(2026, 10, 10)
                    d = (dt.date.today() - born).days
                    return f"Меня собрали десятого октября две тысячи двадцать шестого. Мне {d} {plural(d, 'день', 'дня', 'дней')}, сэр. Для ИИ — почти пенсия."
                return random.choice(replies)
            return fn
        R(rx)(make())

    @R(r"^(?:протокол\s+|режим\s+)?вечеринк\w*$|^(?:устрой|начни|давай)\s+вечеринк\w*$|^(?:протокол|режим)\s+(?:дискотека|party)$")
    def _party(m, t):
        core.emit("party", seconds=10)
        return random.choice(["Протокол «Вечеринка» активирован. Соседи предупреждены. Почти.",
                              "Включаю дискотеку. Реактор на максимум!"])
