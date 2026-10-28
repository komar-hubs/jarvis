"""Automations: "когда случилось X (и выполнены условия) — сделать Y", без голоса.

Triggers are checked in one background loop with cheap local checks (no network, no AI):
  app_start / app_stop {app}   a program started / closed
  time {at "HH:MM"}            at a time of day (days of week go into the conditions)
  interval {minutes}           every N minutes
  startup                      when Jarvis starts
  idle {minutes}               no mouse / keyboard for N minutes
  back {minutes}               the user is back after being away at least N minutes
  power {state plug|unplug}    the charger was plugged / unplugged
  battery {below}              the battery dropped below N %
  audio {device}               sound switched to a device (headphones)
  wifi {ssid}                  connected to a Wi-Fi network ("" = any)
  window {title}               a window with this text in the title became active
  cpu {percent, minutes}       CPU above N % for M minutes
  file {folder, pattern}       a new file appeared in a folder (*.torrent)
  usb                          a flash drive / external disk was plugged in

Conditions: time window, days of week, a program running / not running, power source. Actions are the steps of
the Commands tab (ChainRunner) with variables from the trigger: {app} {file} {path} {drive} {ssid} {title}
{battery} {device} {away}. Stored in data/automations.json."""
import collections
import datetime as dt
import fnmatch
import os
import re
import threading
import time
import uuid
from pathlib import Path

import psutil

from .config import JsonStore

PARTIAL = (".crdownload", ".part", ".partial", ".download", ".opdownload", ".tmp")

TRIGGERS = {
    "app_start": {"title": "Запустилась программа", "fields": [["app", "Программа: часть имени процесса (steam, cs2, chrome)", "text"]], "vars": "{app}"},
    "app_stop":  {"title": "Закрылась программа", "fields": [["app", "Программа: часть имени процесса", "text"]], "vars": "{app}"},
    "time":      {"title": "В заданное время", "fields": [["at", "Время", "time"]], "vars": ""},
    "interval":  {"title": "Каждые N минут", "fields": [["minutes", "Минут", "number"]], "vars": ""},
    "startup":   {"title": "При запуске Джарвиса", "fields": [], "vars": ""},
    "idle":      {"title": "Я отошёл от ПК", "fields": [["minutes", "Нет движений мыши и клавиатуры, минут", "number"]], "vars": ""},
    "back":      {"title": "Я вернулся за ПК", "fields": [["minutes", "После отсутствия не меньше, минут", "number"]], "vars": "{away} — сколько минут вас не было"},
    "power":     {"title": "Зарядка подключена / отключена", "fields": [["state", "Событие", "select:unplug=Отключили зарядку|plug=Подключили зарядку"]], "vars": "{battery}"},
    "battery":   {"title": "Заряд батареи ниже", "fields": [["below", "Процентов", "number"]], "vars": "{battery}"},
    "audio":     {"title": "Звук переключился на устройство", "fields": [["device", "Часть названия устройства (наушники, AirPods, HDMI)", "text"]], "vars": "{device}"},
    "wifi":      {"title": "Подключился к Wi-Fi", "fields": [["ssid", "Сеть (пусто — любая)", "text"]], "vars": "{ssid}"},
    "window":    {"title": "Открыто окно с текстом в заголовке", "fields": [["title", "Текст в заголовке (YouTube, Figma)", "text"]], "vars": "{title}"},
    "cpu":       {"title": "Процессор долго загружен", "fields": [["percent", "Больше, %", "number"], ["minutes", "Дольше, минут", "number"]], "vars": ""},
    "file":      {"title": "В папке появился файл", "fields": [["folder", "Папка", "folder"], ["pattern", "Маска (*.torrent, *.pdf, * — любой)", "text"]], "vars": "{file} — имя, {path} — полный путь"},
    "usb":       {"title": "Подключили флешку или диск", "fields": [], "vars": "{drive} — буква диска"},
}
DEFAULTS = {"interval": {"minutes": 60}, "idle": {"minutes": 10}, "back": {"minutes": 30}, "power": {"state": "unplug"},
            "battery": {"below": 20}, "cpu": {"percent": 90, "minutes": 3}, "time": {"at": "09:00"},
            "file": {"folder": "~\\Downloads", "pattern": "*"}, "wifi": {"ssid": ""}}

EXAMPLES = [
    {"name": "Отключил зарядку — экономия", "trigger": {"type": "power", "state": "unplug"},
     "actions": [{"type": "voice", "value": "режим энергосбережения"}], "reply": ""},
    {"name": "Подключил зарядку — обычный режим", "trigger": {"type": "power", "state": "plug"},
     "actions": [{"type": "voice", "value": "сбалансированный режим"}], "reply": ""},
    {"name": "Наушники — громкость 30", "trigger": {"type": "audio", "device": "наушники"},
     "actions": [{"type": "volume", "value": "30"}], "reply": ""},
    {"name": "Флешка — открыть", "trigger": {"type": "usb"}, "actions": [{"type": "open", "value": "{drive}"}],
     "reply": "Подключён диск {drive}."},
    {"name": "Торрент скачался — открыть", "trigger": {"type": "file", "folder": "~\\Downloads", "pattern": "*.torrent"},
     "actions": [{"type": "open", "value": "{path}"}], "reply": ""},
    {"name": "Утро в будни", "trigger": {"type": "time", "at": "09:00"}, "cond": {"days": [0, 1, 2, 3, 4]},
     "actions": [{"type": "voice", "value": "какая погода"}, {"type": "voice", "value": "какие задачи на сегодня"}],
     "reply": "Доброе утро, {name}."},
    {"name": "Ночью тише", "trigger": {"type": "time", "at": "23:00"}, "actions": [{"type": "volume", "value": "20"}], "reply": ""},
    {"name": "Вернулся — что на сегодня", "trigger": {"type": "back", "minutes": 60},
     "actions": [{"type": "voice", "value": "какие задачи на сегодня"}], "reply": "С возвращением. Вас не было {away} минут."},
    {"name": "Батарея садится", "trigger": {"type": "battery", "below": 20}, "actions": [],
     "reply": "Сэр, заряд {battery} процентов. Пора на зарядку."},
    {"name": "Комп тормозит — найти виновника", "trigger": {"type": "cpu", "percent": 90, "minutes": 3},
     "actions": [{"type": "voice", "value": "почему тормозит компьютер"}], "reply": "", "cooldown": 30},
    {"name": "Игра запустилась — игровой режим", "trigger": {"type": "app_start", "app": "cs2"},
     "actions": [{"type": "voice", "value": "игровой режим"}], "reply": ""},
]


def _folder(p):
    return Path(os.path.expandvars(os.path.expanduser(p or "~\\Downloads")))


class Automations:
    def __init__(self, core):
        self.core = core
        self.store = JsonStore("automations", default=[])
        if not isinstance(self.store.items, list):
            self.store.items = []
        self.rt = collections.defaultdict(dict)     # automation id -> runtime state (edges, timers)
        self.snap = {}                              # shared observations from the previous tick
        self.history = collections.deque(maxlen=60)
        self.started = time.time()
        self._lock = threading.RLock()

    @property
    def items(self):
        return self.store.items

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="automations").start()

    def save(self):
        self.store.save()
        self.core.emit("automations")

    # ---------------------------------------------------------------- loop
    def _loop(self):
        time.sleep(10)
        tick = 0
        while True:
            time.sleep(2)
            tick += 1
            autos = [a for a in self.items if a.get("enabled", True)]
            if not autos:
                continue
            try:
                self._observe({a["trigger"]["type"] for a in autos}, tick)
            except Exception as e:
                self.core.log(f"Автоматизации: {e}", "warn")
                continue
            for a in autos:
                try:
                    for vars_ in self._check(a, tick):
                        self._fire(a, vars_)
                except Exception as e:
                    self.core.log(f"Автоматизация «{a.get('name')}»: {e}", "warn")

    def _observe(self, kinds, tick):
        """One look at the PC per tick for every kind of trigger in use; edges = what changed since last time."""
        from . import system as S
        s = self.snap
        if kinds & {"app_start", "app_stop"}:
            now = {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])} - {""}
            prev = s.get("procs")
            s["started"], s["stopped"] = (now - prev, prev - now) if prev is not None else (set(), set())
            s["procs"] = now
        if kinds & {"idle", "back"}:
            s["idle"] = S.idle_seconds()
        # slower checks run every few ticks; in between "previous" = "current", so one change fires once
        s["prev_power"], s["prev_audio"], s["prev_ssid"], s["new_drives"] = s.get("power"), s.get("audio"), s.get("ssid"), set()
        if kinds & {"power", "battery"} and tick % 3 == 0:
            s["power"] = S.power_state()
        if "audio" in kinds and tick % 2 == 0:
            s["audio"] = S.audio_output_name()
        if "wifi" in kinds and tick % 10 == 0:
            s["ssid"] = S.wifi_ssid()
        if "window" in kinds:
            s["prev_title"], s["title"] = s.get("title"), S.active_title()
        if "usb" in kinds and tick % 2 == 0:
            now = S.removable_drives()
            prev = s.get("drives")
            s["new_drives"] = (now - prev) if prev is not None else set()
            s["drives"] = now
        if "cpu" in kinds:
            s["cpu"] = psutil.cpu_percent(interval=None)

    def _check(self, a, tick):
        """Yields a dict of variables for every time the automation's trigger happened since the last tick."""
        t, s, rt = a["trigger"], self.snap, self.rt[a["id"]]
        typ = t.get("type")
        num = lambda k, d: float(t.get(k) or d)
        if typ in ("app_start", "app_stop"):
            q = (t.get("app") or "").lower().replace(" ", "").removesuffix(".exe")
            for name in s.get("started" if typ == "app_start" else "stopped", ()):
                if q and q in name.replace(" ", ""):
                    yield {"app": name}
                    break
        elif typ == "time":
            now = dt.datetime.now()
            if now.strftime("%H:%M") == (t.get("at") or "")[:5] and rt.get("day") != now.date().isoformat():
                rt["day"] = now.date().isoformat()
                yield {}
        elif typ == "interval":
            last = rt.setdefault("last", time.time())
            if time.time() - last >= max(1.0, num("minutes", 60)) * 60:
                rt["last"] = time.time()
                yield {}
        elif typ == "startup":
            if not rt.get("done") and time.time() - self.started > 20:
                rt["done"] = True
                yield {}
        elif typ == "idle":
            idle = s.get("idle", 0)
            if idle >= num("minutes", 10) * 60 and not rt.get("on"):
                rt["on"] = True
                yield {}
            elif idle < 5:
                rt["on"] = False
        elif typ == "back":
            idle = s.get("idle", 0)
            if idle >= num("minutes", 30) * 60:
                rt["away"] = max(rt.get("away", 0), idle)
            elif idle < 3 and rt.get("away"):
                away = round(rt.pop("away") / 60)
                yield {"away": away}
        elif typ == "power":
            cur, prev = s.get("power"), s.get("prev_power")
            if cur and prev and cur[0] != prev[0] and cur[0] == (t.get("state") == "plug"):
                yield {"battery": cur[1] if cur[1] is not None else ""}
        elif typ == "battery":
            cur, prev = s.get("power"), s.get("prev_power")
            below = num("below", 20)
            if cur and prev and cur[1] is not None and prev[1] is not None and not cur[0] and cur[1] < below <= prev[1]:
                yield {"battery": cur[1]}
        elif typ == "audio":
            cur, prev = s.get("audio") or "", s.get("prev_audio")
            q = (t.get("device") or "").lower()
            if prev is not None and cur != prev and q and q in cur.lower():
                yield {"device": cur}
        elif typ == "wifi":
            cur, prev = s.get("ssid"), s.get("prev_ssid")
            q = (t.get("ssid") or "").lower()
            if prev is not None and cur and cur != prev and (not q or q in cur.lower()):
                yield {"ssid": cur}
        elif typ == "window":
            cur, prev = s.get("title") or "", s.get("prev_title")
            q = (t.get("title") or "").lower()
            if q and prev is not None and q in cur.lower() and q not in (prev or "").lower():
                yield {"title": cur}
        elif typ == "cpu":
            if s.get("cpu", 0) >= num("percent", 90):
                rt.setdefault("since", time.time())
                if not rt.get("fired") and time.time() - rt["since"] >= num("minutes", 3) * 60:
                    rt["fired"] = True
                    yield {}
            else:
                rt.pop("since", None)
                rt["fired"] = False
        elif typ == "file":
            if tick % 5:
                return
            folder = _folder(t.get("folder"))
            if not folder.is_dir():
                return
            pat = (t.get("pattern") or "*").lower()
            names = {p.name for p in folder.iterdir() if p.is_file()}
            seen = rt.get("seen")
            rt["seen"] = names
            if seen is None:
                return
            for n in sorted(names - seen)[:3]:
                p = folder / n
                if n.lower().endswith(PARTIAL) or not fnmatch.fnmatch(n.lower(), pat):
                    continue
                yield {"file": n, "path": str(p)}
        elif typ == "usb":
            for d in sorted(s.get("new_drives", ())):
                yield {"drive": d}

    # ---------------------------------------------------------------- conditions and running
    def _running(self, q):
        q = q.lower().replace(" ", "").removesuffix(".exe")
        procs = self.snap.get("procs") or {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])}
        return any(q in p.replace(" ", "") for p in procs)

    def cond_ok(self, a):
        from . import system as S
        c = a.get("cond") or {}
        now = dt.datetime.now()
        if c.get("days") and now.weekday() not in c["days"]:
            return False
        f, to = c.get("from"), c.get("to")
        if f and to:
            cur = now.strftime("%H:%M")
            if not (f <= cur < to if f <= to else cur >= f or cur < to):
                return False
        if c.get("running") and not self._running(c["running"]):
            return False
        if c.get("not_running") and self._running(c["not_running"]):
            return False
        if c.get("power") in ("ac", "battery") and S.power_state()[0] != (c["power"] == "ac"):
            return False
        return True

    def _fire(self, a, vars_, manual=False):
        if not manual:
            if time.time() - a.get("last_run", 0) < max(0.0, float(a.get("cooldown", 1) or 0)) * 60:
                return
            if not self.cond_ok(a):
                return
        a["last_run"] = time.time()
        a["runs"] = a.get("runs", 0) + 1
        self.history.appendleft({"id": a["id"], "name": a["name"], "at": dt.datetime.now().strftime("%d.%m %H:%M"),
                                 "vars": {k: str(v)[:80] for k, v in vars_.items()}, "manual": manual})
        cmd = {"name": a["name"], "reply": a.get("reply") or "", "actions": a.get("actions") or []}
        run = lambda: self.core.run_command(cmd, vars_)
        if a.get("ask") and not manual:
            self.core.initiative.propose(f"auto:{a['id']}:{time.time()}", f"{a['name']}. Выполнить?", priority=3,
                                         title="Автоматизация", on_yes=run, cooldown_min=0, kind="automation")
        else:
            if a.get("announce"):
                self.core.notify("Автоматизация", a["name"])
            run()
        if a.get("once") and not manual:
            a["enabled"] = False
        self.core.log(f"Автоматизация «{a['name']}» сработала")
        self.save()

    def find(self, spoken):
        from rapidfuzz import fuzz
        s = (spoken or "").lower().strip()
        best = max(self.items, key=lambda a: fuzz.partial_ratio(s, a["name"].lower()), default=None)
        return best if best and fuzz.partial_ratio(s, best["name"].lower()) >= 70 else None

    # ---------------------------------------------------------------- UI
    def rpc_automations(self):
        return {"items": self.items, "triggers": TRIGGERS, "defaults": DEFAULTS, "examples": EXAMPLES,
                "history": list(self.history)}

    def rpc_automation_save(self, auto):
        a = dict(auto)
        if (a.get("trigger") or {}).get("type") not in TRIGGERS:
            raise ValueError("выберите событие")
        a["name"] = (a.get("name") or "").strip() or TRIGGERS[a["trigger"]["type"]]["title"]
        with self._lock:
            old = next((x for x in self.items if x["id"] == a.get("id")), None) if a.get("id") else None
            if old:
                keep = {k: old[k] for k in ("last_run", "runs") if k in old}
                old.clear()
                old.update(a | keep)
                self.rt.pop(a["id"], None)      # the trigger may have changed: start edges fresh
                a = old
            else:
                a["id"] = uuid.uuid4().hex[:8]
                a.setdefault("enabled", True)
                self.items.append(a)
            self.save()
        return a

    def rpc_automation_delete(self, id):
        with self._lock:
            self.store.items = [a for a in self.items if a["id"] != id]
            self.save()
        return True

    def rpc_automation_toggle(self, id, on):
        a = next((x for x in self.items if x["id"] == id), None)
        if a:
            a["enabled"] = bool(on)
            self.rt.pop(id, None)
            self.save()
        return bool(on)

    def rpc_processes(self):
        """Running programs (process names without .exe) as suggestions for the trigger and conditions."""
        skip = {"svchost", "conhost", "runtimebroker", "system", "registry", "idle", "csrss", "smss", "wininit", "services",
                "lsass", "fontdrvhost", "dwm", "sihost", "taskhostw", "ctfmon", "dllhost", "searchhost", "wmiprvse"}
        names = {(p.info["name"] or "").lower().removesuffix(".exe") for p in psutil.process_iter(["name"])}
        return sorted(n for n in names if n and n not in skip)[:300]

    def rpc_automation_run(self, id):
        """Run the actions now, as a test (conditions and cooldown are skipped)."""
        a = next((x for x in self.items if x["id"] == id), None)
        if not a:
            raise ValueError("не найдена")
        sample = {"app": a["trigger"].get("app", ""), "file": "пример.txt", "path": str(_folder("~\\Downloads") / "пример.txt"),
                  "drive": "E:\\", "ssid": a["trigger"].get("ssid") or "Wi-Fi", "title": a["trigger"].get("title", ""),
                  "battery": 50, "device": a["trigger"].get("device", ""), "away": 30}
        self._fire(a, sample, manual=True)
        return True


# ====================================================================== voice rules
def register(R, core):
    A = lambda: core.automations

    @R(r"^(?:какие|мои|покажи)\s+(?:у\s+меня\s+)?автоматизаци\w*$|^список\s+автоматизаций$")
    def _list(m, t):
        items = A().items
        core.show_tab("commands")
        core.emit("show_automations")
        if not items:
            return "Автоматизаций пока нет. Их можно создать во вкладке «Команды» → «Автоматизации»."
        on = [a["name"] for a in items if a.get("enabled", True)]
        return f"Автоматизаций {len(items)}, включено {len(on)}" + (": " + ", ".join(on[:6]) + "." if on else ".")

    @R(r"^(выключи|отключи|включи|запусти)\s+автоматизаци\w+\s+(.+)$")
    def _toggle(m, t):
        a = A().find(m.group(2))
        if not a:
            return "Такой автоматизации нет."
        if m.group(1) == "запусти":
            A().rpc_automation_run(a["id"])
            return True
        on = m.group(1) == "включи"
        A().rpc_automation_toggle(a["id"], on)
        return f"Автоматизация «{a['name']}» {'включена' if on else 'выключена'}."
