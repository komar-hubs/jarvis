"""The suit's power-up: a cinematic full-screen boot when the PC starts (ui/boot.html, window "JARVIS BOOT").

The desktop dissolves into the HUD, the reactor assembles, a system check runs over this PC's real state (speech,
voice, CPU, memory, GPU temperature, network, phone, AI), the day's panels slide in (weather, tasks, load), the
name decodes, and Jarvis greets the user with the day ahead — then it all collapses into the reactor and the
desktop comes back. No AI requests: the voice is local, the weather comes from open-meteo like everywhere else.

Plays when the PC has just started or on the first Jarvis start of the day (not on every restart of Jarvis), or
on "протокол загрузки". A click or Esc skips it."""
import datetime as dt
import random
import re
import threading
import time

from . import system as S

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MONTHS_G = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def _plural(n, one, few, many):
    n = abs(int(n))
    return one if n % 10 == 1 and n % 100 != 11 else few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def cpu_name():
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        name = winreg.QueryValueEx(k, "ProcessorNameString")[0]
        name = " ".join(name.replace("(R)", "").replace("(TM)", "").replace("CPU", "").split())
        return re.sub(r"\s+(?:\d+|Six|Eight|Quad|Dual)-Core Processor.*$|\s+Processor$|\s+w/.*$|\s+with Radeon.*$", "", name)
    except Exception:
        return "процессор"


class Boot:
    def __init__(self, core):
        self.core = core
        self.cfg = core.cfg
        self.pending = False       # play it once the boot window and the voice are ready
        self.page_ready = False
        self.running = False
        self._data = None

    # ---------------------------------------------------------------- when
    def should_play(self):
        mode = self.cfg.get("boot.mode", "startup")      # startup | always | off
        if mode == "off" or not self.cfg.get("setup_done"):
            return False
        if mode == "always":
            return True
        import psutil
        today = dt.date.today().isoformat()
        fresh_pc = time.time() - psutil.boot_time() < 15 * 60
        first_today = self.cfg.get("boot.last_day") != today
        return fresh_pc or first_today

    def schedule(self):
        """From core.start(): wait for the boot page and the voice (≤ 15 s), then play."""
        self.pending = True

        def wait():
            t0 = time.time()
            while time.time() - t0 < 15:
                silero = self.core.tts.engines.get("silero")
                voice_ok = silero is None or getattr(silero, "ready", True) or self.cfg.get("voice.engine") != "silero"
                if self.page_ready and "boot_show" in self.core.ui_hooks and voice_ok:
                    break
                time.sleep(0.3)
            self.play()
        threading.Thread(target=wait, daemon=True, name="boot-wait").start()

    def play(self):
        if self.running:
            return False
        if "boot_show" not in self.core.ui_hooks:
            self.pending = False
            self.core.say(self.greeting())                  # no window (overlay off): the greeting at least
            return False
        self.pending = False
        self.running = True
        self.cfg.set("boot.last_day", dt.date.today().isoformat())
        self._data = self.collect()
        self.core.player.sound("suit")
        self.core.ui("boot_show")
        threading.Timer(32, self._failsafe).start()       # never leave a full-screen window up by mistake
        return True

    def _failsafe(self):
        if self.running:
            self.rpc_boot_done()

    # ---------------------------------------------------------------- what is shown and said
    def collect(self):
        c, data = self.core, {}
        st = {}
        try:
            st = S.system_stats()
        except Exception:
            pass
        persona = c.persona.public()
        lst = c.listener
        checks = [("ЯДРО", f"J.A.R.V.I.S. {self._version()}", "ok")]
        stt = (lst.model_name or "").replace("gigaam-v3-ctc", "GigaAM v3")
        checks.append(("РАСПОЗНАВАНИЕ РЕЧИ", f"{stt or 'загружается'} · {(lst.device or 'cpu').upper()}", "ok" if lst.model else "warn"))
        eng = self.cfg.get("voice.engine", "silero")
        voice = {"silero": f"Silero · {self.cfg.get('voice.silero_speaker', 'aidar')}", "edge": self.cfg.get("voice.edge_voice", "Edge"),
                 "elevenlabs": "ElevenLabs", "xtts": "XTTS", "sapi": "Windows"}.get(eng, eng)
        checks.append(("СИНТЕЗ РЕЧИ", voice, "ok"))
        checks.append(("МИКРОФОН", (lst.mic_name or "по умолчанию").split(" (")[0][:34], "warn" if lst.mic_error else "ok"))
        import psutil
        checks.append(("ПРОЦЕССОР", f"{cpu_name()[:30]} · {psutil.cpu_count(logical=False) or '?'} ядер", "ok"))
        if st:
            checks.append(("ПАМЯТЬ", f"{st.get('ram_total')} ГБ · занято {round(st.get('ram', 0))}%", "warn" if st.get("ram", 0) > 90 else "ok"))
            g = st.get("gpu")
            if g:
                checks.append(("ВИДЕОКАРТА", f"{g['name'].replace('NVIDIA GeForce ', '')} · {g['temp']}°C", "warn" if g["temp"] >= 80 else "ok"))
            checks.append(("ДИСК", f"свободно {st.get('disk_free')} ГБ", "warn" if (st.get("disk_free") or 99) < 15 else "ok"))
        ssid = S.wifi_ssid()
        checks.append(("СЕТЬ", f"Wi-Fi · {ssid}" if ssid else "проводная", "ok"))
        ph = c.phone
        dev = ph.device() if ph.devices else None
        known = next((d["name"] for d in ph.devices), None)
        if dev or known:
            checks.append(("ТЕЛЕФОН", (dev or {}).get("name") or known, "ok" if dev else "warn"))
        prov = self.cfg.provider()
        checks.append(("ИСКУССТВЕННЫЙ ИНТЕЛЛЕКТ", prov.get("title", prov.get("id", "?"))[:30], "ok"))
        try:
            n = sum(1 for i in c.plugins.items.values() if not i["error"] and c.plugins.enabled(i))
            checks.append(("ПЛАГИНЫ", f"загружено: {n}", "ok"))
        except Exception:
            pass
        data["checks"] = [{"label": a, "value": b, "status": s} for a, b, s in checks]
        data["stats"] = {k: st.get(k) for k in ("cpu", "ram", "disk")} | {"gpu": (st.get("gpu") or {}).get("temp")}
        data["weather"] = self._weather()
        now = dt.datetime.now()
        tasks = c.planner.tasks_for_day() or [t for t in c.planner.sorted_tasks(True) if not t.get("due")][:3]
        data["tasks"] = [{"text": t["text"], "at": (t.get("due") or "")[11:16]} for t in tasks[:4]]
        rem = []
        for r in sorted((r for r in c.planner.reminders.items if r.get("active")), key=lambda r: r.get("at", "")):
            try:
                at = dt.datetime.fromisoformat(r["at"])
            except Exception:
                continue
            if now <= at and at.date() == now.date():
                rem.append({"text": r["text"], "at": at.strftime("%H:%M")})
        data["reminders"] = rem[:3]
        data["persona"] = {"id": persona["id"], "logo": persona.get("logo"), "sub": persona.get("sub")}
        data["theme"] = self.cfg.get("theme", "jarvis")
        data["date"] = f"{WEEKDAYS[now.weekday()]}, {now.day} {MONTHS_G[now.month - 1]}"
        warn = [x["label"].lower() for x in data["checks"] if x["status"] != "ok"]
        data["status"] = "ВСЕ СИСТЕМЫ В НОРМЕ" if not warn else "СИСТЕМЫ В НОРМЕ · ЕСТЬ ЗАМЕЧАНИЯ"
        data["greeting"] = self.greeting(data, warn)
        return data

    @staticmethod
    def _version():
        try:
            from .updater import current_version
            return current_version()
        except Exception:
            return ""

    def _weather(self):
        loc = self.cfg.get("location") or {}
        if not loc.get("lat"):
            return None
        out = {}

        def go():
            try:
                from . import info
                out["w"] = info.weather(loc["lat"], loc["lon"])
            except Exception:
                pass
        t = threading.Thread(target=go, daemon=True)
        t.start()
        t.join(3)                                         # the boot does not wait for a slow network
        w = out.get("w")
        return {"temp": w["temp"], "desc": w["desc"], "max": w.get("max"), "min": w.get("min"), "city": loc.get("city", "")} if w else None

    def greeting(self, data=None, warn=None):
        data = data or {}
        h = dt.datetime.now().hour
        part = "Доброй ночи" if h < 5 else "Доброе утро" if h < 12 else "Добрый день" if h < 18 else "Добрый вечер"
        addr = self.cfg.get("user.address") or "сэр"
        name = self.cfg.get("user.name")
        who = name if name and random.random() < 0.35 else addr
        parts = [f"{part}, {who}."]
        if warn:
            parts.append(f"Все системы в норме, кроме одной: {warn[0]}." if len(warn) == 1 else "Системы в норме, есть пара замечаний.")
        else:
            parts.append(random.choice(["Все системы в норме.", "Все системы работают в штатном режиме.", "Системы онлайн, всё в норме."]))
        if data.get("date"):
            parts.append(f"Сегодня {data['date']}.")
        w = data.get("weather")
        if w:
            t = w["temp"]
            parts.append(f"За окном {'плюс ' if t > 0 else 'минус ' if t < 0 else ''}{abs(t)}, {w['desc']}.")
        tasks = data.get("tasks") or []
        if tasks:
            first = tasks[0]
            n = len(tasks)
            parts.append(f"На сегодня {n} {_plural(n, 'задача', 'задачи', 'задач')}" +
                         (f", первая в {first['at']} — {first['text']}." if first.get("at") else f": {first['text']}" + ("." if n == 1 else " и другие.")))
        rem = data.get("reminders") or []
        if rem:
            parts.append(f"В {rem[0]['at']} напомню: {rem[0]['text']}.")
        parts.append(random.choice(["Чем займёмся?", "Готов к работе.", "Жду указаний."]))
        return " ".join(parts)

    # ---------------------------------------------------------------- the page talks to these
    def rpc_boot_data(self):
        return self._data or self.collect()

    def rpc_boot_ready(self):
        self.page_ready = True
        return self.pending

    def rpc_boot_speak(self):
        text = (self._data or {}).get("greeting") or self.greeting()
        self.core.say(text)
        return True

    def rpc_boot_done(self):
        self.running = False
        self.core.ui("boot_hide")
        return True

    def rpc_boot_play(self):
        return self.play()

    def rpc_boot_state(self):
        return {"page_ready": self.page_ready, "running": self.running, "pending": self.pending}


# ====================================================================== voice rules
def register(R, core):
    @R(r"^(?:протокол|режим|покажи|запусти|включи)\s+(?:загрузк\w*|запуск\w*)(?:\s+костюма)?$|^загрузка\s+костюма$|"
       r"^(?:перезагрузи|проверь)\s+(?:все\s+)?системы$")
    def _boot(m, t):
        if not core.boot.play():
            return "Загрузка уже идёт, сэр." if core.boot.running else True
        return True
