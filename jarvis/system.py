"""PC control: app index, launching/closing apps, windows, volume, media keys, power, clipboard, macros, system info."""
import ctypes
import datetime as dt
import json
import os
import re
import subprocess
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote_plus

import psutil

from .config import DATA

CREATE_NO_WINDOW = 0x08000000

# spoken Russian name -> canonical app name (matched against the Start menu index)
ALIASES = {
    "хром": "chrome", "гугл хром": "chrome", "браузер": "browser", "яндекс браузер": "yandex", "яндекс": "yandex",
    "файрфокс": "firefox", "мозилла": "firefox", "опера": "opera", "эдж": "edge", "edge": "edge",
    "телеграм": "telegram", "телеграмм": "telegram", "телега": "telegram", "дискорд": "discord", "стим": "steam",
    "спотифай": "spotify", "вс код": "visual studio code", "вскод": "visual studio code", "vs code": "visual studio code",
    "визуал студио код": "visual studio code", "визуал студио": "visual studio 2022", "курсор": "cursor",
    "антигравити": "antigravity", "виндсерф": "windsurf", "пайчарм": "pycharm", "идея": "intellij idea",
    "блокнот": "notepad", "нотпад": "notepad++", "калькулятор": "calc", "проводник": "explorer",
    "диспетчер задач": "taskmgr", "командная строка": "cmd", "терминал": "wt", "пауэршелл": "powershell",
    "панель управления": "control", "параметры": "ms-settings:", "настройки windows": "ms-settings:",
    "пэйнт": "mspaint", "паинт": "mspaint", "ножницы": "snippingtool", "word": "word", "ворд": "word",
    "эксель": "excel", "повер поинт": "powerpoint", "павер поинт": "powerpoint", "обс": "obs studio",
    "блендер": "blender", "юнити": "unity hub", "годо": "godot", "годот": "godot", "крита": "krita",
    "фл студио": "fl studio", "аудасити": "audacity", "яндекс музыка": "yandex music", "вотсап": "whatsapp",
    "ватсап": "whatsapp", "зум": "zoom", "скайп": "skype", "эпик": "epic games", "эпик геймс": "epic games",
    "ардуино": "arduino ide", "андроид студио": "android studio", "олама": "ollama", "оллама": "ollama",
    "клод": "claude", "кодекс": "codex", "микшер": "sndvol", "громкость": "sndvol", "реестр": "regedit",
    "майнкрафт": "minecraft", "лунар": "lunar client", "нотепад плюс плюс": "notepad++",
}

SITES = {
    "ютуб": "https://youtube.com", "youtube": "https://youtube.com", "вк": "https://vk.com", "вконтакте": "https://vk.com",
    "гитхаб": "https://github.com", "github": "https://github.com", "почта": "https://mail.google.com",
    "гмейл": "https://mail.google.com", "джимейл": "https://mail.google.com", "переводчик": "https://translate.google.com",
    "карты": "https://yandex.ru/maps", "яндекс карты": "https://yandex.ru/maps", "гугл карты": "https://maps.google.com",
    "чат гпт": "https://chatgpt.com", "чатгпт": "https://chatgpt.com", "клод ай": "https://claude.ai",
    "википедия": "https://ru.wikipedia.org", "твич": "https://twitch.tv", "реддит": "https://reddit.com",
    "кинопоиск": "https://kinopoisk.ru", "гугл диск": "https://drive.google.com", "диск": "https://disk.yandex.ru",
    "авито": "https://avito.ru", "озон": "https://ozon.ru", "вайлдберриз": "https://wildberries.ru",
    "хабр": "https://habr.com", "стековерфлоу": "https://stackoverflow.com", "4пда": "https://4pda.to",
    "календарь": "https://calendar.google.com", "новости": "https://news.google.com/?hl=ru",
    "погоду": "https://yandex.ru/pogoda", "фигму": "https://figma.com", "фигма": "https://figma.com",
}

FOLDERS = {
    "загрузки": "~/Downloads", "документы": "~/Documents", "рабочий стол": "~/Desktop", "изображения": "~/Pictures",
    "картинки": "~/Pictures", "музыка": "~/Music", "видео": "~/Videos", "домашнюю папку": "~", "домашняя папка": "~",
    "папку джарвиса": str(DATA.parent), "скриншоты": str(DATA / "screenshots"), "автозагрузку": "shell:startup",
    "корзину": "shell:RecycleBinFolder", "этот компьютер": "shell:MyComputerFolder", "мой компьютер": "shell:MyComputerFolder",
    # Windows settings pages
    "настройки звука": "ms-settings:sound", "звук": "ms-settings:sound", "настройки блютуз": "ms-settings:bluetooth",
    "блютуз": "ms-settings:bluetooth", "bluetooth": "ms-settings:bluetooth", "настройки вайфай": "ms-settings:network-wifi",
    "вайфай": "ms-settings:network-wifi", "wi-fi": "ms-settings:network-wifi", "настройки сети": "ms-settings:network",
    "настройки экрана": "ms-settings:display", "настройки дисплея": "ms-settings:display",
    "обновления": "ms-settings:windowsupdate", "центр обновления": "ms-settings:windowsupdate",
    "приложения": "ms-settings:appsfeatures", "установленные программы": "ms-settings:appsfeatures",
    "настройки мыши": "ms-settings:mousetouchpad", "персонализацию": "ms-settings:personalization",
    "обои": "ms-settings:personalization-background", "настройки питания": "ms-settings:powersleep",
    "хранилище": "ms-settings:storagesense", "память компьютера": "ms-settings:storagesense",
    "уведомления": "ms-settings:notifications", "настройки микрофона": "ms-settings:privacy-microphone",
    "игровой режим": "ms-settings:gaming-gamemode", "язык": "ms-settings:regionlanguage",
    "раскладку": "ms-settings:regionlanguage", "настройки": "ms-settings:", "параметры windows": "ms-settings:",
}

BUILTIN = {
    "notepad": "notepad.exe", "calc": "calc.exe", "explorer": "explorer.exe", "taskmgr": "taskmgr.exe", "cmd": "cmd.exe",
    "powershell": "powershell.exe", "control": "control.exe", "ms-settings:": "ms-settings:", "mspaint": "mspaint.exe",
    "snippingtool": "snippingtool.exe", "wt": "wt.exe", "sndvol": "sndvol.exe", "regedit": "regedit.exe",
}

TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
                     "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def translit(s):
    return "".join(TRANSLIT.get(c, c) for c in s.lower())


def resolve_alias(query, extra=None):
    """Spoken name -> canonical app name. Tolerates Russian word endings: "диспетчер задачи" -> taskmgr."""
    from rapidfuzz import fuzz, process
    q = query.lower().strip().replace("ё", "е")
    al = dict(ALIASES)
    al.update({k.lower(): v for k, v in (extra or {}).items()})
    if q in al:
        return al[q]
    r = process.extractOne(q, list(al), scorer=fuzz.ratio, score_cutoff=86)
    return al[r[0]] if r else q


# ===================================================================== app index
class AppIndex:
    def __init__(self, log=print):
        self.log = log
        self.apps = []          # {name, path, target, kind}
        self.ready = False
        self.cache = DATA / "cache" / "apps.json"
        if self.cache.exists():
            try:
                self.apps = json.loads(self.cache.read_text("utf-8"))
                self.ready = True
            except Exception:
                pass
        threading.Thread(target=self.rebuild, daemon=True).start()

    def rebuild(self):
        apps = []
        dirs = [Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs",
                Path(os.environ.get("PROGRAMDATA", "")) / r"Microsoft\Windows\Start Menu\Programs",
                Path.home() / "Desktop", Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop"]
        shell = None
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            shell = win32com.client.Dispatch("WScript.Shell")
        except Exception:
            pass
        for d in dirs:
            if not d.exists():
                continue
            for p in list(d.rglob("*.lnk")) + list(d.rglob("*.url")):
                name = p.stem
                low = name.lower()
                if any(w in low for w in ("uninstall", "удалить", "удаление", "readme", "help", "справка", "license")):
                    continue
                target = ""
                if shell and p.suffix == ".lnk":
                    try:
                        target = shell.CreateShortcut(str(p)).TargetPath
                    except Exception:
                        pass
                apps.append({"name": name, "path": str(p), "target": target, "kind": "lnk"})
        # UWP / Store apps
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
                                 creationflags=CREATE_NO_WINDOW).stdout
            known = {a["name"].lower() for a in apps}
            for a in json.loads(out or "[]"):
                if a["Name"].lower() not in known and "!" in a.get("AppID", ""):
                    apps.append({"name": a["Name"], "path": "shell:AppsFolder\\" + a["AppID"], "target": "", "kind": "uwp"})
        except Exception as e:
            self.log(f"Get-StartApps: {e}")
        # dedupe by name
        seen, uniq = set(), []
        for a in apps:
            k = a["name"].lower()
            if k not in seen:
                seen.add(k)
                uniq.append(a)
        self.apps = uniq
        self.ready = True
        try:
            self.cache.write_text(json.dumps(uniq, ensure_ascii=False), "utf-8")
        except Exception:
            pass
        self.log(f"Найдено приложений: {len(uniq)}")

    def find(self, query, extra_aliases=None):
        from rapidfuzz import fuzz, process
        q = resolve_alias(query, extra_aliases)
        if not self.apps:
            return None
        names = [a["name"].lower() for a in self.apps]
        best, best_score = None, 0
        for cand in dict.fromkeys([q, translit(q)]):
            for i, n in enumerate(names):
                if n == cand:
                    return self.apps[i]
            # strict matching: "rutracker" must not open "Run", "диспетчер задачи" must not open a scanner tool
            for name, _, i in process.extract(cand, names, scorer=fuzz.WRatio, limit=8, score_cutoff=70):
                words = re.findall(r"\w+", name)
                whole_word = cand in words or (len(cand) >= 4 and any(w.startswith(cand) for w in words))
                full = fuzz.ratio(cand, name)
                tok = fuzz.token_set_ratio(cand, name)
                short, long_ = sorted((len(cand), len(name)))
                score = max(full, tok if short / long_ >= 0.5 else 0, 92 if whole_word else 0)
                if score > best_score:
                    best, best_score = self.apps[i], score
        return best if best_score >= 82 else None

    def find_loose(self, query, extra_aliases=None):
        """For user protocols: the name was typed on purpose in the command editor, so a looser match is safe
        ("яндекс" -> "Yandex Browser", "visual studio" -> "Visual Studio Code")."""
        from rapidfuzz import fuzz, process
        hit = self.find(query, extra_aliases)
        if hit or not self.apps:
            return hit
        q = resolve_alias(query, extra_aliases)
        names = [a["name"].lower() for a in self.apps]
        best = None
        for cand in dict.fromkeys([q, translit(q)]):
            r = process.extractOne(cand, names, scorer=fuzz.WRatio, score_cutoff=75)
            if r and (best is None or r[1] > best[1]):
                best = r
        return self.apps[best[2]] if best else None


# ===================================================================== windows
def _user32():
    return ctypes.windll.user32


def list_windows():
    import win32gui
    import win32process
    res = []

    def cb(h, _):
        if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h).strip():
            ex = win32gui.GetWindowLong(h, -20)
            if ex & 0x80:  # tool window
                return True
            try:
                _, pid = win32process.GetWindowThreadProcessId(h)
                pname = psutil.Process(pid).name()
            except Exception:
                pid, pname = 0, ""
            title = win32gui.GetWindowText(h)
            if title in ("Program Manager", "Windows Input Experience") or pname.lower() in ("textinputhost.exe",):
                return True
            res.append({"hwnd": h, "title": title, "pid": pid, "proc": pname})
        return True

    win32gui.EnumWindows(cb, None)
    return res


def find_windows(query):
    from rapidfuzz import fuzz
    q = resolve_alias(query)
    q2 = translit(q)
    if len(q) < 3:
        return []
    out = []
    for w in list_windows():
        hay = (w["title"] + " " + w["proc"].replace(".exe", "")).lower()
        # whole-word hits only: "вкладки" must not match a random window title
        words = re.findall(r"\w+", hay)
        hit = any(x in words or (len(x) >= 4 and any(wd.startswith(x) for wd in words)) for x in (q, q2))
        s = max(fuzz.partial_ratio(q, hay), fuzz.partial_ratio(q2, hay))
        if hit or s >= 95:
            out.append((s, w))
    out.sort(key=lambda x: -x[0])
    return [w for _, w in out]


def focus_window(hwnd):
    import win32con
    import win32gui
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        # Alt press lets SetForegroundWindow work from a background process
        _user32().keybd_event(0x12, 0, 0, 0)
        win32gui.SetForegroundWindow(hwnd)
        _user32().keybd_event(0x12, 0, 2, 0)
        return True
    except Exception:
        return False


def window_action(query, action):
    """action: minimize | maximize | restore | close | focus | hide | show"""
    import win32con
    import win32gui
    if query in ("", "это", "окно", "текущее", "активное", "текущее окно", "это окно"):
        wins = [{"hwnd": win32gui.GetForegroundWindow(), "title": win32gui.GetWindowText(win32gui.GetForegroundWindow())}]
    else:
        wins = find_windows(query)
    if not wins:
        return 0
    n = 0
    for w in wins:
        h = w["hwnd"]
        if action == "minimize":
            win32gui.ShowWindow(h, win32con.SW_MINIMIZE)
        elif action == "maximize":
            win32gui.ShowWindow(h, win32con.SW_MAXIMIZE)
            focus_window(h)
        elif action == "restore":
            win32gui.ShowWindow(h, win32con.SW_RESTORE)
        elif action == "close":
            win32gui.PostMessage(h, win32con.WM_CLOSE, 0, 0)
        elif action == "focus":
            focus_window(h)
            return 1
        elif action == "hide":
            win32gui.ShowWindow(h, win32con.SW_HIDE)
            _hidden.append(h)
        elif action == "show":
            win32gui.ShowWindow(h, win32con.SW_SHOW)
        n += 1
    return n


_hidden = []


def show_hidden():
    import win32con
    import win32gui
    n = 0
    while _hidden:
        h = _hidden.pop()
        try:
            win32gui.ShowWindow(h, win32con.SW_SHOW)
            n += 1
        except Exception:
            pass
    return n


# ===================================================================== launching / closing
def open_target(target):
    """Open anything: URL, file, folder, exe path, shell: URI, UWP id."""
    t = os.path.expandvars(os.path.expanduser(target.strip().strip('"')))
    if re.match(r"^[a-z]+://", t) or re.match(r"^www\.", t):
        webbrowser.open(t if "://" in t else "https://" + t)
        return True
    if re.match(r"^[\w-]+(\.[\w-]+)+(/\S*)?$", t) and not os.path.exists(t) and "." in t and not t.endswith(".exe"):
        webbrowser.open("https://" + t)
        return True
    if t.startswith("shell:") or t.startswith("ms-settings:"):
        subprocess.Popen(["explorer.exe", t])
        return True
    os.startfile(t)
    return True


def launch_app(app, index, aliases=None, loose=False):
    """Returns (ok, display_name). loose: fuzzier name matching (names typed in the command editor)."""
    q = resolve_alias(app, aliases)
    if re.match(r"^[a-z][\w+.-]*://", q, re.I):  # steam://rungameid/..., https://..., ms-settings:...
        open_target(q)
        return True, app
    if q in ("browser",):
        webbrowser.open("https://www.google.com")
        return True, "браузер"
    if q in BUILTIN:
        open_target(BUILTIN[q])
        return True, app
    if os.path.exists(os.path.expandvars(os.path.expanduser(q))):
        open_target(q)
        return True, Path(q).stem
    found = index.find_loose(q, aliases) if loose else index.find(q, aliases)
    if found:
        open_target(found["path"])
        return True, found["name"]
    # last resort: maybe it is an exe on PATH
    import shutil
    exe = shutil.which(q) or shutil.which(translit(q))
    if exe:
        subprocess.Popen([exe], creationflags=0)
        return True, Path(exe).stem
    return False, app


def close_app(app, index, aliases=None, force=False):
    q = resolve_alias(app, aliases)
    exe_names = set()
    found = index.find(q, aliases) if index else None
    if found and found.get("target", "").lower().endswith(".exe"):
        exe_names.add(Path(found["target"]).name.lower())
    if q in BUILTIN and BUILTIN[q].endswith(".exe"):
        exe_names.add(BUILTIN[q])
    for cand in (q, translit(q)):
        exe_names.add(cand.replace(" ", "") + ".exe")
    killed = 0
    for p in psutil.process_iter(["name", "pid"]):
        n = (p.info["name"] or "").lower()
        if n in exe_names and p.pid != os.getpid():
            try:
                p.kill() if force else p.terminate()
                killed += 1
            except Exception:
                pass
    if killed:
        return killed
    # no process match -> close windows whose title matches
    return window_action(q, "close")


# ===================================================================== keys, media, volume
VK = {"play": 0xB3, "next": 0xB0, "prev": 0xB1, "stop": 0xB2, "mute": 0xAD, "vol_down": 0xAE, "vol_up": 0xAF}


def press_vk(code, times=1):
    for _ in range(times):
        _user32().keybd_event(code, 0, 0, 0)
        _user32().keybd_event(code, 0, 2, 0)
        time.sleep(0.02)


def media(action):
    press_vk(VK[action])


# ===================================================================== media control (Windows SMTC)
# The system media transport controls are what Windows shows above the volume flyout: YouTube/Twitch in
# Chrome, Edge and Yandex, Spotify, Яндекс Музыка, VLC, Windows Media Player... They give explicit play/pause
# (media keys only toggle), the position and duration, seeking and playback speed when the app allows it.
MEDIA_APPS = {"ютуб": "chrome|msedge|yandex|browser|firefox|opera", "youtube": "chrome|msedge|yandex|browser|firefox|opera",
              "браузер": "chrome|msedge|yandex|browser|firefox|opera", "хром": "chrome", "твич": "chrome|msedge|yandex|browser",
              "спотифай": "spotify", "spotify": "spotify", "яндекс музык": "yandex.music|yandexmusic|music.yandex",
              "вк": "vk", "плеер": "vlc|wmplayer|zune|media", "vlc": "vlc"}


def _media_pick(mgr, app=None):
    sessions = list(mgr.get_sessions())
    if app:
        a = app.lower()
        pat = next((v for k, v in MEDIA_APPS.items() if a.startswith(k) or k in a), re.escape(a))
        hit = [s for s in sessions if re.search(pat, (s.source_app_user_model_id or "").lower())]
        if hit:
            return hit[0]
    cur = mgr.get_current_session()
    if cur:
        return cur
    playing = [s for s in sessions if int(s.get_playback_info().playback_status) == 4]
    return (playing or sessions or [None])[0]


def _media_state(s, props=None):
    info = s.get_playback_info()
    tl = s.get_timeline_properties()
    c = info.controls
    pos, end = tl.position.total_seconds(), tl.end_time.total_seconds()
    playing = int(info.playback_status) == 4
    if playing and end > 0:  # the position is a snapshot from last_updated_time: move it to "now"
        try:
            pos += max(0.0, (dt.datetime.now(dt.timezone.utc) - tl.last_updated_time).total_seconds()) * (info.playback_rate or 1.0)
        except Exception:
            pass
    st = {"app": (s.source_app_user_model_id or "").replace(".exe", ""), "playing": playing,
          "position": round(max(0.0, min(pos, end)) if end > 0 else 0.0, 1), "duration": round(max(end, 0.0), 1),
          "can_seek": bool(c.is_playback_position_enabled) and end > 0, "can_next": bool(c.is_next_enabled),
          "can_prev": bool(c.is_previous_enabled), "rate": info.playback_rate or 1.0}
    if props is not None:
        st.update(title=props.title or "", artist=props.artist or "")
    return st


def media_sessions():
    """Every media session Windows knows about, current one first (data for the AI)."""
    import asyncio
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager

    async def go():
        mgr = await Manager.request_async()
        cur = mgr.get_current_session()
        out = []
        for s in mgr.get_sessions():
            try:
                st = _media_state(s, await s.try_get_media_properties_async())
                st["current"] = bool(cur and s.source_app_user_model_id == cur.source_app_user_model_id)
                out.append(st)
            except Exception:
                pass
        return sorted(out, key=lambda x: (not x["current"], not x["playing"]))
    return asyncio.run(go())


def _browser_keys(keys, times=1):
    """YouTube-style keyboard fallback in the browser window."""
    if active_exe() not in BROWSERS and not focus_browser():
        return False
    for _ in range(max(1, times)):
        hotkey(keys)
        time.sleep(0.05)
    return True


def media_control(action, value=None, app=None):
    """action: play | pause | toggle | stop | next | prev | seek (value = ±seconds) | seek_to (value = seconds)
    | restart | rate (value = 0.25..3) | info. Returns a dict with ok and the session state."""
    import asyncio
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager

    async def go():
        mgr = await Manager.request_async()
        s = _media_pick(mgr, app)
        if s is None:
            return {"ok": False, "error": "ничего не играет"}
        st = _media_state(s, await s.try_get_media_properties_async())
        ok = True
        if action == "play":
            ok = await s.try_play_async()
        elif action == "pause":
            ok = await s.try_pause_async()
        elif action == "toggle":
            ok = await s.try_toggle_play_pause_async()
        elif action == "stop":
            ok = await s.try_stop_async() or await s.try_pause_async()
        elif action == "next":
            ok = await s.try_skip_next_async()
        elif action == "prev":
            ok = await s.try_skip_previous_async()
        elif action in ("seek", "seek_to", "restart"):
            target = 0.0 if action == "restart" else float(value or 0) + (st["position"] if action == "seek" else 0.0)
            if st["duration"] > 0:
                target = max(0.0, min(target, st["duration"] - 1))
            ok = st["can_seek"] and await s.try_change_playback_position_async(int(target * 10_000_000))
            st["target"] = round(target, 1)
        elif action == "rate":
            ok = await s.try_change_playback_rate_async(float(value or 1.0))
        elif action == "shuffle":
            ok = await s.try_change_shuffle_active_async(bool(True if value is None else value))
        elif action == "repeat":
            mode = {"none": 0, "track": 1, "list": 2}.get(str(value or "track"), 1)
            try:
                from winrt.windows.media import MediaPlaybackAutoRepeatMode as RepeatMode
                mode = RepeatMode(mode)
            except Exception:
                pass
            ok = await s.try_change_auto_repeat_mode_async(mode)
        return {"ok": bool(ok), **st}

    res = asyncio.run(go())
    browser = any(b in res.get("app", "").lower() for b in ("chrome", "msedge", "yandex", "browser", "firefox", "opera"))
    if res.get("ok") or not browser or action == "info":
        return res
    # the page did not accept the command through Windows (live streams, sites without Media Session): keys
    if action == "seek":
        v = float(value or 0)
        res["ok"] = _browser_keys("l" if v > 0 else "j", round(abs(v) / 10) or 1)  # YouTube: 10 s per press
    elif action == "restart":
        res["ok"] = _browser_keys("0")  # YouTube: "0" jumps to the start
    elif action == "rate":
        steps = round((float(value or 1.0) - (res.get("rate") or 1.0)) / 0.25)
        res["ok"] = steps == 0 or _browser_keys("shift+." if steps > 0 else "shift+,", abs(steps))
    elif action in ("play", "pause", "toggle"):
        res["ok"] = _browser_keys("k")
    elif action == "next":
        res["ok"] = _browser_keys("shift+n")
    return res


def now_playing():
    """Current media session from Windows (Spotify, Яндекс Музыка, YouTube in a browser, players)."""
    import asyncio
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager

    async def get():
        mgr = await Manager.request_async()
        s = mgr.get_current_session()
        if not s:
            return None
        p = await s.try_get_media_properties_async()
        status = int(s.get_playback_info().playback_status)
        return {"app": (s.source_app_user_model_id or "").replace(".exe", ""), "title": p.title or "",
                "artist": p.artist or "", "playing": status == 4}
    return asyncio.run(get())


KEY_WORDS = {
    "энтер": "enter", "ентер": "enter", "ввод": "enter", "интер": "enter", "пробел": "space", "спейс": "space",
    "эскейп": "esc", "искейп": "esc", "escape": "esc", "эскейт": "esc", "таб": "tab", "удалить": "delete",
    "делит": "delete", "делете": "delete", "бэкспейс": "backspace", "назад": "backspace", "вверх": "up",
    "вниз": "down", "влево": "left", "вправо": "right", "контрл": "ctrl", "контрол": "ctrl", "контр": "ctrl",
    "ктрл": "ctrl", "control": "ctrl", "альт": "alt", "шифт": "shift", "шифта": "shift", "вин": "windows",
    "винда": "windows", "винду": "windows", "виндовс": "windows", "win": "windows", "пуск": "windows",
    "капс": "caps lock", "хоум": "home", "энд": "end", "пейдж ап": "page up", "принтскрин": "print screen",
    "плюс": "+", "минус": "-", "точка": ".", "запятая": ",",
}
# letters as people say them: Russian sound ("дэ") or English name in Cyrillic ("ди")
LETTER_WORDS = {
    "а": "a", "эй": "a", "бэ": "b", "би": "b", "б": "b", "си": "c", "цэ": "c", "ц": "c", "дэ": "d", "де": "d", "ди": "d",
    "д": "d", "е": "e", "и": "e", "эф": "f", "ф": "f", "гэ": "g", "джи": "g", "г": "g", "эйч": "h", "аш": "h", "ай": "i",
    "джей": "j", "кей": "k", "ка": "k", "к": "k", "эл": "l", "л": "l", "эм": "m", "м": "m", "эн": "n", "н": "n", "о": "o",
    "оу": "o", "пи": "p", "пэ": "p", "п": "p", "кью": "q", "ар": "r", "эр": "r", "р": "r", "эс": "s", "с": "s", "ти": "t",
    "тэ": "t", "т": "t", "ю": "u", "у": "u", "ви": "v", "вэ": "v", "в": "v", "дабл ю": "w", "икс": "x", "х": "x",
    "уай": "y", "игрек": "y", "зет": "z", "зэ": "z", "з": "z",
}


def parse_keys(spoken):
    """'контрол шифт эскейп' -> 'ctrl+shift+esc', 'вин д' / 'виндэ' -> 'windows+d', 'альт ф4' -> 'alt+f4'."""
    s = spoken.lower().replace("ё", "е").strip(" .!")
    s = re.sub(r"\b(?:и|плюс)\b", " ", s)
    s = re.sub(r"\bдабл ю\b", "дабл_ю", s)
    out = []
    for tok in re.split(r"[\s+]+", s):
        if not tok:
            continue
        tok = tok.replace("_", " ")
        m = re.match(r"^(?:эф|f|ф)\s*(\d{1,2})$", tok)
        if m:
            out.append("f" + m.group(1))
        elif tok in KEY_WORDS:
            out.append(KEY_WORDS[tok])
        elif tok in LETTER_WORDS:
            out.append(LETTER_WORDS[tok])
        elif re.match(r"^вин(?:д[эеы]?|ди)$", tok):  # "виндэ" = win + D
            out += ["windows", "d"]
        elif re.fullmatch(r"[a-z0-9]|f\d{1,2}|[a-z]+", tok):
            out.append(tok)
        elif re.fullmatch(r"\d", tok):
            out.append(tok)
        else:
            raise ValueError(f"unknown key {tok}")
    if not out:
        raise ValueError("empty")
    return "+".join(out)


# Keys are sent as Windows virtual-key codes. The `keyboard` library resolved names through scan-code tables
# and picked extended codes first: "d" became E0 20 = Volume Mute ("нажми Win+D" muted the sound) and
# "f" became E0 21 = Calculator ("полный экран" opened the calculator). Virtual keys do not depend on the layout.
VK_NAMES = {
    "ctrl": 0x11, "control": 0x11, "left ctrl": 0xA2, "right ctrl": 0xA3, "shift": 0x10, "left shift": 0xA0,
    "right shift": 0xA1, "alt": 0x12, "left alt": 0xA4, "right alt": 0xA5, "alt gr": 0xA5, "windows": 0x5B,
    "win": 0x5B, "left windows": 0x5B, "right windows": 0x5C, "cmd": 0x5B, "super": 0x5B,
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "space": 0x20, "spacebar": 0x20,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "ins": 0x2D, "home": 0x24, "end": 0x23,
    "page up": 0x21, "pageup": 0x21, "pgup": 0x21, "page down": 0x22, "pagedown": 0x22, "pgdn": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "caps lock": 0x14, "capslock": 0x14,
    "print screen": 0x2C, "printscreen": 0x2C, "prtsc": 0x2C, "pause": 0x13, "apps": 0x5D, "menu": 0x5D,
    "num lock": 0x90, "scroll lock": 0x91,
    "volume mute": 0xAD, "volume down": 0xAE, "volume up": 0xAF, "next track": 0xB0, "previous track": 0xB1,
    "stop media": 0xB2, "play/pause media": 0xB3, "play/pause": 0xB3,
    "+": 0xBB, "=": 0xBB, "plus": 0xBB, "-": 0xBD, "minus": 0xBD, ",": 0xBC, ".": 0xBE, "/": 0xBF, ";": 0xBA,
    "'": 0xDE, "[": 0xDB, "]": 0xDD, "\\": 0xDC, "`": 0xC0, "~": 0xC0,
}
_EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D, 0x2E, 0x5B, 0x5C, 0x5D, 0xA3, 0xA5, 0x90}


def _vk(name):
    n = name.strip().lower()
    if n in VK_NAMES:
        return VK_NAMES[n]
    if len(n) == 1 and ("a" <= n <= "z" or "0" <= n <= "9"):
        return ord(n.upper())  # VK_A..VK_Z, VK_0..VK_9 equal the ASCII codes on every layout
    m = re.fullmatch(r"f(\d{1,2})", n)
    if m and 1 <= int(m.group(1)) <= 24:
        return 0x6F + int(m.group(1))
    raise ValueError(f"unknown key {name}")


def _split_combo(combo):
    """'ctrl+shift+esc' -> ['ctrl', 'shift', 'esc']; 'ctrl++' -> ['ctrl', '+']."""
    parts, cur = [], ""
    for ch in combo.strip():
        if ch == "+" and cur.strip():
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def send_vks(vks):
    """Press the virtual keys in order and release them in reverse (a chord)."""
    u = _user32()
    for vk in vks:
        u.keybd_event(vk, u.MapVirtualKeyW(vk, 0), 1 if vk in _EXTENDED else 0, 0)
        time.sleep(0.012)
    for vk in reversed(vks):
        u.keybd_event(vk, u.MapVirtualKeyW(vk, 0), (1 if vk in _EXTENDED else 0) | 2, 0)
        time.sleep(0.008)


def hotkey(combo):
    """'win+d', 'ctrl+shift+w', 'f', 'энтер', 'контрол шифт эскейп': one chord, or several separated by ', '."""
    for chord in [c for c in re.split(r"\s*,\s*", combo.strip()) if c]:
        try:
            vks = [_vk(k) for k in _split_combo(chord)]
        except ValueError:
            vks = [_vk(k) for k in _split_combo(parse_keys(chord))]  # spoken Russian key names
        send_vks(vks)


def type_text(text):
    """Types any text as Unicode characters (SendInput KEYEVENTF_UNICODE): layout-independent, and it never
    goes through key names (the old way typed an English "d" as the Mute key)."""
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    u = _user32()
    for ch in text.replace("\r\n", "\n"):
        if ch == "\n":
            send_vks([0x0D])
            continue
        code = ord(ch)
        units = [code] if code < 0x10000 else [0xD800 + ((code - 0x10000) >> 10), 0xDC00 + ((code - 0x10000) & 0x3FF)]
        for unit in units:
            down = INPUT(type=1, u=_U(ki=KEYBDINPUT(0, unit, 0x0004, 0, 0)))          # KEYEVENTF_UNICODE
            up = INPUT(type=1, u=_U(ki=KEYBDINPUT(0, unit, 0x0004 | 0x0002, 0, 0)))  # + KEYUP
            arr = (INPUT * 2)(down, up)
            u.SendInput(2, arr, ctypes.sizeof(INPUT))
        time.sleep(0.004)


def _endpoint():
    import comtypes
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    comtypes.CoInitialize()
    dev = AudioUtilities.GetSpeakers()
    if hasattr(dev, "EndpointVolume"):
        return dev.EndpointVolume
    from comtypes import CLSCTX_ALL
    # QueryInterface keeps the reference count right (ctypes.cast led to a double Release, see PeakMeter)
    return dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(IAudioEndpointVolume)


def _mic_endpoint(name=""):
    """(Windows name, IAudioEndpointVolume) of the capture device matching a PortAudio name (MME cuts names to
    31 characters); the default microphone when nothing matches."""
    import comtypes
    from pycaw.constants import DEVICE_STATE, EDataFlow
    from pycaw.pycaw import AudioUtilities
    comtypes.CoInitialize()
    devs = [d for d in AudioUtilities.GetAllDevices(data_flow=EDataFlow.eCapture.value,
                                                    device_state=DEVICE_STATE.ACTIVE.value) if d.FriendlyName]
    name = (name or "").strip()
    pick = next((d for d in devs if name and (d.FriendlyName == name or d.FriendlyName.startswith(name))), None)
    if pick is None:
        try:
            did = AudioUtilities.GetMicrophone().GetId()
        except Exception:
            did = None
        pick = next((d for d in devs if d.id == did), devs[0] if devs else None)
    return (pick.FriendlyName, pick.EndpointVolume) if pick else (None, None)


def mic_level(name=""):
    fn, ev = _mic_endpoint(name)
    if ev is None:
        return None
    return {"name": fn, "level": round(float(ev.GetMasterVolumeLevelScalar()), 2), "muted": bool(ev.GetMute())}


def set_mic_level(level=1.0, name=""):
    """Unmute the microphone and set its Windows input level (0..1). Returns the device name."""
    fn, ev = _mic_endpoint(name)
    if ev is None:
        return None
    ev.SetMute(0, None)
    ev.SetMasterVolumeLevelScalar(max(0.0, min(1.0, float(level))), None)
    return fn


def get_volume():
    try:
        return round(_endpoint().GetMasterVolumeLevelScalar() * 100)
    except Exception:
        return None


def set_volume(pct):
    pct = max(0, min(100, int(pct)))
    try:
        ep = _endpoint()
        ep.SetMute(0, None)
        ep.SetMasterVolumeLevelScalar(pct / 100, None)
        return pct
    except Exception:
        # fallback: keys (2% per press)
        press_vk(VK["vol_down"], 50)
        press_vk(VK["vol_up"], pct // 2)
        return pct


def change_volume(delta):
    cur = get_volume()
    if cur is None:
        press_vk(VK["vol_up" if delta > 0 else "vol_down"], abs(delta) // 2)
        return None
    return set_volume(cur + delta)


def mute(on=True):
    try:
        _endpoint().SetMute(1 if on else 0, None)
    except Exception:
        press_vk(VK["mute"])


def app_volume(query, level=None, delta=None):
    """Per-app volume through the Windows mixer: 'сделай дискорд тише', 'громкость браузера 30'."""
    import comtypes
    from pycaw.pycaw import AudioUtilities
    comtypes.CoInitialize()
    q = resolve_alias(query)
    names = {q, translit(q), q.replace(" ", "")}
    if q in ("browser", "браузер", "браузера"):
        names |= {"chrome", "msedge", "firefox", "opera", "browser", "yandex"}
    hit = []
    for s in AudioUtilities.GetAllSessions():
        try:
            pname = (s.Process.name() if s.Process else "").lower().replace(".exe", "")
        except Exception:
            continue
        if pname and any(n and (n in pname or pname in n) for n in names):
            vol = s.SimpleAudioVolume
            cur = vol.GetMasterVolume()
            new = level / 100 if level is not None else max(0.0, min(1.0, cur + (delta or 0) / 100))
            vol.SetMasterVolume(new, None)
            hit.append((pname, round(new * 100)))
    return hit


# ===================================================================== screen OCR + mouse
def screen_ocr(monitor=1):
    """Windows built-in OCR (Russian + English) over a screen: [{text, x, y, w, h, words: [...]}] in screen pixels."""
    import asyncio
    import mss
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    with mss.MSS() as s:
        mon = s.monitors[monitor if monitor < len(s.monitors) else 1]
        shot = s.grab(mon)

    async def run():
        eng = OcrEngine.try_create_from_language(Language("ru")) or OcrEngine.try_create_from_user_profile_languages()
        w = DataWriter()
        w.write_bytes(bytes(shot.bgra))
        sb = SoftwareBitmap.create_copy_from_buffer(w.detach_buffer(), BitmapPixelFormat.BGRA8, shot.width, shot.height)
        res = await eng.recognize_async(sb)
        out = []
        for ln in res.lines:
            words = []
            for wd in ln.words:
                r = wd.bounding_rect
                words.append({"text": wd.text, "x": int(r.x) + mon["left"], "y": int(r.y) + mon["top"],
                              "w": int(r.width), "h": int(r.height)})
            if words:
                x0 = min(w_["x"] for w_ in words); y0 = min(w_["y"] for w_ in words)
                x1 = max(w_["x"] + w_["w"] for w_ in words); y1 = max(w_["y"] + w_["h"] for w_ in words)
                out.append({"text": ln.text, "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0, "words": words})
        return out
    return asyncio.run(run())


def own_window_rects():
    """Screen rects of Jarvis's own visible windows: their text (the subtitle repeats the user's phrase)
    must never be the click target."""
    import win32gui
    out = []
    for title in ("J.A.R.V.I.S.", "JARVIS HUD"):
        h = win32gui.FindWindow(None, title)
        if h and win32gui.IsWindowVisible(h) and not win32gui.IsIconic(h):
            out.append(win32gui.GetWindowRect(h))
    return out


def screen_text_lines(limit=120):
    """OCR lines (all monitors) outside Jarvis's windows, with click points: for the AI to see the screen."""
    from . import mouse
    return mouse.screen_lines(limit)


def find_on_screen(query):
    """Best on-screen target for a spoken label: UI Automation controls first, then OCR (jarvis/mouse.py)."""
    from . import mouse
    hit = mouse.find(query)
    return dict(hit, text=hit.get("name", "")) if hit else None


def click_text(query, button="left", double=False):
    hit = find_on_screen(query)
    if hit:
        mouse_click(hit["x"], hit["y"], button=button, double=double)
    return hit


def is_key_phrase(spoken):
    """True if the spoken words name keyboard keys ('энтер', 'контрол ц'), not an on-screen label."""
    try:
        [_vk(k) for k in _split_combo(parse_keys(spoken))]
        return True
    except Exception:
        return False


# The mouse goes through SendInput (jarvis/mouse.py). pyautogui threw FailSafeException whenever the cursor
# rested in a screen corner, so every click failed until the user moved the mouse away.
def mouse_move(x, y, duration=None):
    from . import mouse
    mouse.move(int(x), int(y), duration)


def mouse_move_rel(dx, dy, duration=None):
    from . import mouse
    mouse.move_rel(int(dx), int(dy))


def mouse_drag(x1, y1, x2, y2, duration=0.45):
    from . import mouse
    mouse.drag(int(x1), int(y1), int(x2), int(y2), duration)


def mouse_position():
    from . import mouse
    x, y = mouse.position()
    vx, vy, vw, vh = mouse.virtual_screen()
    return {"x": x, "y": y, "screen_w": vw, "screen_h": vh, "screen_left": vx, "screen_top": vy}


def idle_seconds():
    """Seconds since the last keyboard/mouse input (is the user at the PC?)."""
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(li)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li))
    return (ctypes.windll.kernel32.GetTickCount() - li.dwTime) / 1000.0


def audio_peak():
    """Current output level 0..1 of the default speakers (what is playing right now, Jarvis included). Local: a
    Windows meter read, no network."""
    m = PeakMeter()
    try:
        return m.peak()
    finally:
        m.close()


class PeakMeter:
    """The speakers' level for a fast loop (the quick panel's reactor, ~15 reads a second). audio_peak() builds new
    COM objects on every call, and the garbage collector then released them from other threads: an access
    violation that took the whole process down. Here one meter is opened and released in the thread that uses it."""

    def __init__(self):
        import comtypes
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation
        comtypes.CoInitialize()
        self._enum = AudioUtilities.GetDeviceEnumerator()
        self._dev = self._enum.GetDefaultAudioEndpoint(0, 1)   # eRender, eMultimedia
        # QueryInterface, not ctypes.cast: cast makes a second pointer without AddRef, and both Release it later
        self._m = self._dev.Activate(IAudioMeterInformation._iid_, CLSCTX_ALL, None).QueryInterface(IAudioMeterInformation)

    def peak(self):
        return float(self._m.GetPeakValue())

    def close(self):
        """Call from the same thread: the references drop here, so Release runs here."""
        import gc
        self._m = self._dev = self._enum = None
        gc.collect()


def audio_output_name():
    """Name of the default output device: "Динамики (Realtek(R) Audio)", "Наушники (AirPods)"."""
    import comtypes
    from pycaw.pycaw import AudioUtilities
    comtypes.CoInitialize()
    try:
        return AudioUtilities.GetSpeakers().FriendlyName or ""
    except Exception:
        return ""


def wifi_ssid():
    """The Wi-Fi network the PC is connected to, "" when none."""
    try:
        out = subprocess.run(["netsh", "wlan", "show", "interfaces"], capture_output=True, timeout=8,
                             creationflags=CREATE_NO_WINDOW).stdout.decode("cp866", "replace")
    except Exception:
        return ""
    m = re.search(r"^\s*SSID\s*:\s*(.+)$", out, re.M)
    return m.group(1).strip() if m else ""


def power_state():
    """(on_ac, battery_percent) — (True, None) on a desktop without a battery."""
    bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if not bat:
        return True, None
    return bool(bat.power_plugged), int(bat.percent)


def removable_drives():
    out = set()
    for p in psutil.disk_partitions(all=False):
        if "removable" in (p.opts or "").lower():
            out.add(p.mountpoint)
    return out


def active_title():
    """Title of the active window without sending it a message: GetWindowText sends WM_GETTEXT to windows of this
    process and, called from a background thread while Jarvis's own window is in front, can wait on the GUI thread
    (the window hung and Windows closed it)."""
    try:
        u32 = ctypes.windll.user32
        buf = ctypes.create_unicode_buffer(512)
        u32.InternalGetWindowText(u32.GetForegroundWindow(), buf, 512)
        return buf.value
    except Exception:
        return ""


def foreground_pid():
    """Process id of the active window (no message is sent to the window)."""
    try:
        u32 = ctypes.windll.user32
        pid = ctypes.c_ulong()
        u32.GetWindowThreadProcessId(u32.GetForegroundWindow(), ctypes.byref(pid))
        return pid.value
    except Exception:
        return 0


def foreground_fullscreen():
    """The active window covers its whole monitor (a film, a game, a full-screen video), the desktop excluded."""
    try:
        import win32api
        import win32gui
        h = win32gui.GetForegroundWindow()
        if not h or win32gui.GetClassName(h) in ("Progman", "WorkerW", "Shell_TrayWnd"):
            return False
        l, t, r, b = win32gui.GetWindowRect(h)
        ml, mt, mr, mb = win32api.GetMonitorInfo(win32api.MonitorFromWindow(h, 2))["Monitor"]
        return l <= ml and t <= mt and r >= mr and b >= mb
    except Exception:
        return False


def recycle(paths):
    """Send files to the Recycle Bin (they can be restored), no dialogs."""
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
    paths = [str(p) for p in paths]
    if not paths:
        return True
    op = SHFILEOPSTRUCTW(wFunc=3, pFrom="\0".join(paths) + "\0\0",  # FO_DELETE
                         fFlags=0x40 | 0x10 | 0x4 | 0x400)  # ALLOWUNDO | NOCONFIRMATION | SILENT | NOERRORUI
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0


def scroll(direction="down", amount=6):
    from . import mouse
    mouse.scroll(direction, amount)


_ducked = {}  # exe name -> original session volume
DUCK_FILE = DATA / "ducked.json"


def _save_ducked():
    try:
        if _ducked:
            DUCK_FILE.write_text(json.dumps(_ducked), encoding="utf-8")
        else:
            DUCK_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def duck(on, level=0.5):
    """Lower every other app's audio (music, videos) while the assistant speaks; duck(False) restores it.
    Levels are keyed by exe name and saved to data/ducked.json: Windows remembers per-app volume, so if
    Jarvis was closed or killed while ducking, Chrome would otherwise stay quiet forever."""
    import comtypes
    from pycaw.pycaw import AudioUtilities
    comtypes.CoInitialize()
    me = os.getpid()
    for s in AudioUtilities.GetAllSessions():
        try:
            if not s.Process or s.Process.pid == me:
                continue
            exe = s.Process.name().lower()
            vol = s.SimpleAudioVolume
            if on:
                cur = vol.GetMasterVolume()
                if exe not in _ducked and cur > level:
                    _ducked[exe] = cur
                if exe in _ducked and cur > level:
                    vol.SetMasterVolume(level, None)
            elif exe in _ducked:
                vol.SetMasterVolume(_ducked[exe], None)  # every session of that app, even a restarted one
        except Exception:
            pass
    if not on:
        _ducked.clear()
    _save_ducked()


def restore_ducked():
    """At start-up: give back volumes a previous run lowered and could not restore (crash, taskkill)."""
    try:
        saved = json.loads(DUCK_FILE.read_text(encoding="utf-8"))
    except Exception:
        return 0
    _ducked.update({k: float(v) for k, v in saved.items()})
    n = len(_ducked)
    duck(False)
    return n


BROWSERS = ("chrome.exe", "msedge.exe", "firefox.exe", "browser.exe", "opera.exe", "brave.exe", "vivaldi.exe",
            "yandex.exe", "arc.exe")


def active_exe():
    import psutil
    import win32gui
    import win32process
    try:
        _, pid = win32process.GetWindowThreadProcessId(win32gui.GetForegroundWindow())
        return psutil.Process(pid).name().lower()
    except Exception:
        return ""


def focus_browser():
    """Bring the most recent browser window to the front; True if the browser is (now) active."""
    if active_exe() in BROWSERS:
        return True
    import psutil
    import win32gui
    import win32process
    found = []

    def cb(h, _):
        if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h).strip():
            try:
                _, pid = win32process.GetWindowThreadProcessId(h)
                if psutil.Process(pid).name().lower() in BROWSERS:
                    found.append(h)
            except Exception:
                pass
        return True
    win32gui.EnumWindows(cb, None)
    if not found:
        return False
    focus_window(found[0])  # EnumWindows walks the z-order: the first one is the most recent
    time.sleep(0.25)
    return active_exe() in BROWSERS


def set_brightness(pct):
    import screen_brightness_control as sbc
    sbc.set_brightness(max(0, min(100, int(pct))))
    return pct


# ===================================================================== power & misc
def lock():
    ctypes.windll.user32.LockWorkStation()


def sleep_pc():
    subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])


def shutdown(restart=False, delay=20):
    subprocess.Popen(["shutdown", "/r" if restart else "/s", "/t", str(delay)], creationflags=CREATE_NO_WINDOW)


def cancel_shutdown():
    subprocess.Popen(["shutdown", "/a"], creationflags=CREATE_NO_WINDOW)


def empty_recycle_bin():
    # flags: no confirmation, no progress, no sound
    return ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4) == 0


def screenshot(open_after=False):
    import mss
    import mss.tools
    path = DATA / "screenshots" / f"screen_{dt.datetime.now():%Y-%m-%d_%H-%M-%S}.png"
    with mss.mss() as s:
        shot = s.grab(s.monitors[0])
        mss.tools.to_png(shot.rgb, shot.size, output=str(path))
    if open_after:
        os.startfile(path)
    return path


SKIP_DIRS = {"node_modules", ".git", "__pycache__", "$recycle.bin", "appdata", ".venv", ".xtts-venv", "venv",
             "site-packages", "windows", "program files", "program files (x86)", "programdata", "steamapps"}


def _search_index(query, limit):
    """Windows Search index (the one behind Explorer search): instant, covers the user's folders."""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    conn = win32com.client.Dispatch("ADODB.Connection")
    conn.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
    out = []
    try:
        words = [w.replace("'", "''") for w in re.findall(r"[\w.-]+", query.lower())]
        if not words:
            return []
        # "отчёт" must also find "отчет"
        cond = " AND ".join(f"(System.FileName LIKE '%{w}%' OR System.FileName LIKE '%{w.replace('ё', 'е')}%')"
                            for w in words)
        rs = conn.Execute(f"SELECT TOP {limit * 6} System.ItemUrl FROM SYSTEMINDEX WHERE {cond} "
                          f"ORDER BY System.DateModified DESC")[0]
        junk = ("\\appdata\\", "\\node_modules\\", "\\.git\\", "\\site-packages\\", "\\intermediate\\",
                "\\__pycache__\\", "\\.venv\\", "\\$recycle.bin\\")
        while not rs.EOF:
            url = rs.Fields.Item("System.ItemUrl").Value or ""  # "file:C:/Users/..." = real path, not the localized one
            p = url[5:].replace("/", "\\") if url.lower().startswith("file:") else ""
            if p and not any(j in p.lower() for j in junk):
                out.append(Path(p))
            rs.MoveNext()
    finally:
        conn.Close()
    return out[:limit]


def find_files(query, limit=12, timeout=5.0):
    """Windows Search index first; fallback: a time-limited walk of the user's folders."""
    try:
        hits = _search_index(query, limit)
        if hits:
            return hits
    except Exception:
        pass
    return _walk_files(query, limit, timeout)


def _walk_files(query, limit=12, timeout=5.0):
    """File search by name in the user's folders (Desktop, Documents, Downloads, media) and other drives' roots."""
    q = query.lower().strip()
    qt = translit(q)
    roots = [Path.home() / d for d in ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music")]
    roots += [Path(f"{d}:\\") for d in "DEFGH" if os.path.exists(f"{d}:\\")]
    t0 = time.time()
    hits = []
    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".")]
            if dirpath.count(os.sep) - str(root).count(os.sep) > 6:
                dirs[:] = []
            for name in files + dirs:
                low = name.lower()
                if q in low or qt in low:
                    score = 100 - len(low) / 10
                elif q.replace("ё", "е") in low.replace("ё", "е"):
                    score = 99 - len(low) / 10
                else:
                    continue  # fuzzy matching on file names produced garbage ("ch.png" for "отчёт")
                hits.append((score, Path(dirpath) / name))
            if time.time() - t0 > timeout:
                break
        if time.time() - t0 > timeout:
            break
    hits.sort(key=lambda x: (-x[0], len(str(x[1]))))
    return [p for _, p in hits[:limit]]


def reveal(path):
    """Open Explorer with the file selected."""
    subprocess.Popen(["explorer.exe", "/select,", str(path)])


def clipboard_get():
    import pyperclip
    return pyperclip.paste()


def clipboard_set(text):
    import pyperclip
    pyperclip.copy(text)


def search_web(query, url="https://www.google.com/search?q={q}"):
    webbrowser.open(url.replace("{q}", quote_plus(query)))


def run_shell(cmd, wait=True, timeout=60):
    if not wait:
        subprocess.Popen(cmd, shell=True, creationflags=CREATE_NO_WINDOW)
        return ""
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout,
                       encoding="cp866", errors="replace", creationflags=CREATE_NO_WINDOW)
    return (r.stdout + r.stderr).strip()


def mouse_click(x=None, y=None, button="left", double=False):
    from . import mouse
    mouse.click(x, y, button=button, double=double)


_gpu_cache = {"t": 0, "v": None}


def gpu_info():
    if time.time() - _gpu_cache["t"] < 3:
        return _gpu_cache["v"]
    v = None
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3,
                             creationflags=CREATE_NO_WINDOW).stdout.strip().splitlines()[0]
        name, util, mu, mt, temp = [x.strip() for x in out.split(",")]
        v = {"name": name, "util": int(util), "mem_used": int(mu), "mem_total": int(mt), "temp": int(temp)}
    except Exception:
        pass
    _gpu_cache.update(t=time.time(), v=v)
    return v


def system_stats():
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(os.path.splitdrive(str(Path.home()))[0] + "\\")
    bat = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    net = psutil.net_io_counters()
    return {
        "cpu": psutil.cpu_percent(interval=None),
        "ram": vm.percent, "ram_used": round(vm.used / 2**30, 1), "ram_total": round(vm.total / 2**30, 1),
        "disk": disk.percent, "disk_free": round(disk.free / 2**30),
        "battery": (round(bat.percent) if bat else None), "plugged": (bat.power_plugged if bat else None),
        "gpu": gpu_info(), "uptime_h": round((time.time() - psutil.boot_time()) / 3600, 1),
        "net_sent": net.bytes_sent, "net_recv": net.bytes_recv, "procs": len(psutil.pids()),
        "volume": get_volume(),
    }


def top_processes(n=5):
    procs = []
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            procs.append((p.info["memory_info"].rss, p.info["name"]))
        except Exception:
            pass
    procs.sort(reverse=True)
    return [{"name": nm, "mb": round(m / 2**20)} for m, nm in procs[:n]]


def set_autostart(enabled, command):
    import winreg
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                         winreg.KEY_SET_VALUE)
    if enabled:
        winreg.SetValueEx(key, "JarvisAssistant", 0, winreg.REG_SZ, command)
    else:
        try:
            winreg.DeleteValue(key, "JarvisAssistant")
        except FileNotFoundError:
            pass
    winreg.CloseKey(key)
