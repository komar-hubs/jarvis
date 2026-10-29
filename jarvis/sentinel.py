"""Initiative and the PC sentinel.

Initiative decides whether Jarvis may speak first. Every module (mail, sentinel, reminders of other kinds)
calls initiative.propose(...) with a priority:
  1 — FYI (only a card in the interface), 2 — worth mentioning, 3 — important, 4 — critical.
The user's level (Настройки → Инициатива) sets the bar: 0 silent, 1 only critical, 2 important (default),
3 everything. Even an allowed event waits while the user is in a fullscreen game or video, dictating, talking to
Jarvis, or away from the PC; "не беспокой" and focus mode hold everything but critical. When an event comes with
a question ("Закрыть её?"), Jarvis asks it and the next "да" without the name does it.

The sentinel looks for things that are odd, not just busy: a new program in autostart, an unsigned program
running from Temp or Downloads, the GPU working hard while nobody is at the PC (miners), a program that
stopped responding, memory that keeps growing, the hosts file changed, Defender switched off, the internet gone,
a big upload while idle, a new drive plugged in, the disk filling up fast."""
import collections
import ctypes
import datetime as dt
import hashlib
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path

import psutil

from . import system as S
from .config import DATA, JsonStore

CREATE_NO_WINDOW = 0x08000000
LEVEL_BAR = {0: 99, 1: 4, 2: 3, 3: 2}
HOSTS = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "drivers" / "etc" / "hosts"
SUSPECT_DIRS = ("\\appdata\\local\\temp\\", "\\windows\\temp\\", "\\downloads\\", "\\users\\public\\", "\\$recycle.bin\\",
                "\\appdata\\roaming\\microsoft\\windows\\start menu\\")
HEAVY_OK = {"blender.exe", "obs64.exe", "obs32.exe", "davinci resolve.exe", "resolve.exe", "premiere pro.exe",
            "afterfx.exe", "unrealeditor.exe", "unity.exe", "ollama.exe", "ollama_llama_server.exe", "python.exe",
            "pythonw.exe", "lm studio.exe", "steam.exe", "steamwebhelper.exe", "nvcontainer.exe", "dwm.exe",
            "msedgewebview2.exe", "chrome.exe", "msedge.exe", "firefox.exe", "browser.exe", "opera.exe", "handbrake.exe",
            "ffmpeg.exe", "comfyui.exe", "stable-diffusion.exe"}


def _user_state():
    """SHQueryUserNotificationState: 2 busy (fullscreen app), 3 D3D fullscreen game, 4 presentation, 5 normal."""
    st = ctypes.c_int(5)
    try:
        ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(st))
    except Exception:
        pass
    return st.value


# ====================================================================== initiative
class Initiative:
    def __init__(self, core):
        self.core = core
        self.cfg = core.cfg
        self.events = collections.deque(maxlen=80)
        self.said = {}
        self.queue = []
        self._seq = 0
        self._lock = threading.RLock()

    def level(self):
        try:
            return int(self.cfg.get("initiative.level", 2))
        except Exception:
            return 2

    def propose(self, key, text, priority=2, title="Джарвис", on_yes=None, cooldown_min=30, toast=True, kind="info"):
        """Returns True if it was (or will be) said aloud."""
        now = time.time()
        with self._lock:
            if key and now - self.said.get(key, 0) < cooldown_min * 60:
                return False
            if key:
                self.said[key] = now
            self._seq += 1
            ev = {"id": self._seq, "key": key, "text": text, "priority": int(priority), "title": title,
                  "at": dt.datetime.now().strftime("%H:%M"), "ts": now, "ask": bool(on_yes), "kind": kind,
                  "state": "new"}
            self.events.appendleft(ev)
            ev["_yes"] = on_yes
        self.core.emit("initiative", event=self._public(ev))
        self.core.log(f"{title}: {text}", "warn" if priority >= 3 else "info")
        if priority < LEVEL_BAR.get(self.level(), 3):
            if toast:
                self.core.notify(title, text)
            ev["state"] = "shown"
            return False
        if self._can_speak(priority):
            self._speak(ev)
        else:
            with self._lock:
                self.queue.append(ev)
                self.queue = self.queue[-6:]
            if toast:
                self.core.notify(title, text)
        return True

    @staticmethod
    def _public(ev):
        return {k: v for k, v in ev.items() if not k.startswith("_")}

    def _can_speak(self, priority):
        c = self.core
        if c.tts_busy() or c.ai_busy or c.alarm_active or getattr(c, "dictating", False) or c.state != "idle":
            return False
        if c.pending:
            return False
        if priority >= 4:
            return True
        if c.sleeping or time.time() < c.dnd_until or c.extras.focus_active():
            return False
        if _user_state() in (2, 3, 4):
            return False  # fullscreen game, video or presentation
        if S.idle_seconds() > 600:
            return False  # nobody at the PC: it waits until they come back
        return True

    def _speak(self, ev):
        ev["state"] = "said"
        self.core.player.sound("notify")
        addr = (self.cfg.get("user.address") or "сэр").capitalize()
        t = ev["text"]
        # "SMS от…", "KDE Connect…", "YouTube": an abbreviation or a name keeps its capital ("Сэр, sMS" was shown)
        head = t if len(t) > 1 and (t[1:2].isupper() or t.split(" ")[0] in ("YouTube", "Telegram", "Discord", "WhatsApp"))             else t[:1].lower() + t[1:]
        text = t if ev["priority"] < 3 or t.lower().startswith(addr.lower()) else f"{addr}, {head}"
        if ev.get("_yes"):
            fn = ev["_yes"]

            def yes():
                ev["state"] = "done"
                self.core.emit("initiative", event=self._public(ev))
                fn()
            self.core.ask_confirm(yes, text)
        else:
            self.core.say(text)
            self.core.follow_up()
        self.core.emit("initiative", event=self._public(ev))

    def tick(self):
        with self._lock:
            # a phone notification said 8 minutes late (after a game) is stale: it stays in the list, not in the voice
            self.queue = [e for e in self.queue if e["state"] == "new" and time.time() - e["ts"] <
                          (180 if e.get("kind") == "phone" and not str(e.get("key") or "").startswith("missed:") else 1800)]
            if not self.queue:
                return
            ev = max(self.queue, key=lambda e: (e["priority"], e["ts"]))
            if not self._can_speak(ev["priority"]):
                return
            self.queue.remove(ev)
        self._speak(ev)

    def summary(self, hours=24):
        cut = time.time() - hours * 3600
        evs = [e for e in self.events if e["ts"] >= cut and e["priority"] >= 2]
        return evs

    # ---- UI
    def rpc_events(self):
        return [self._public(e) for e in self.events]

    def rpc_event_answer(self, id, yes=True):
        ev = next((e for e in self.events if e["id"] == id), None)
        if not ev:
            return False
        ev["state"] = "done" if yes else "dismissed"
        if yes and ev.get("_yes"):
            threading.Thread(target=ev["_yes"], daemon=True).start()
        self.core.emit("initiative", event=self._public(ev))
        return True


# ====================================================================== sentinel
# unsigned helpers that come with many games and apps unpacked into Downloads: not worth a warning
BENIGN_HELPERS = {"crashpad_handler.exe", "unitycrashhandler64.exe", "unitycrashhandler32.exe", "crashreporter.exe",
                  "crashhandler.exe", "crashsender1403.exe", "bugsplatcrashhandler.exe", "werfault.exe"}


class Sentinel:
    def __init__(self, core):
        self.core = core
        self.cfg = core.cfg
        self.store = JsonStore("sentinel", default={})
        if not isinstance(self.store.items, dict):
            self.store.items = {}
        self._t = collections.defaultdict(float)
        self._seen_pids = set()
        self._suspect_seen = set()
        self._sig_cache = {}
        self._hung = {}
        self._rss = collections.defaultdict(collections.deque)   # pid -> deque[(t, rss)]
        self._gpu_busy_since = 0.0
        self._up_hist = collections.deque(maxlen=12)
        self._net_ok = True
        self._net_fail = 0
        self._disk_hist = collections.deque(maxlen=40)
        self._drives = None
        self._defender_broken = False
        self._started = time.time()

    def enabled(self):
        return bool(self.cfg.get("sentinel.enabled", True))

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="sentinel").start()

    def _loop(self):
        time.sleep(20)  # let the start-up settle
        checks = [(self._processes, 15), (self._hung_windows, 10), (self._autostart, 60), (self._hosts, 120),
                  (self._gpu, 20), (self._network, 30), (self._upload, 10), (self._drives_check, 10),
                  (self._memory_growth, 60), (self._disk_fill, 60), (self._defender, 900)]
        while True:
            time.sleep(5)
            if not self.enabled():
                continue
            now = time.time()
            for fn, every in checks:
                if now - self._t[fn.__name__] < every:
                    continue
                self._t[fn.__name__] = now
                try:
                    fn()
                except Exception as e:
                    self.core.log(f"Сторож ({fn.__name__}): {e}", "warn")

    def _say(self, key, text, priority=3, on_yes=None, cooldown_min=60, title="Сторож ПК", kind="sentinel"):
        return self.core.initiative.propose(key, text, priority=priority, title=title, on_yes=on_yes,
                                            cooldown_min=cooldown_min, kind=kind)

    def _save(self):
        self.store.save()

    # ---------------------------------------------------------------- autostart
    @staticmethod
    def autostart_entries():
        import winreg
        out = {}
        for hive, hname in ((winreg.HKEY_CURRENT_USER, "HKCU"), (winreg.HKEY_LOCAL_MACHINE, "HKLM")):
            for sub in (r"Software\Microsoft\Windows\CurrentVersion\Run", r"Software\Microsoft\Windows\CurrentVersion\RunOnce",
                        r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"):
                try:
                    k = winreg.OpenKey(hive, sub)
                except OSError:
                    continue
                i = 0
                while True:
                    try:
                        name, val, _ = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    out[f"{hname}\\{sub}\\{name}"] = {"name": name, "cmd": str(val), "where": hname, "key": sub}
                    i += 1
                winreg.CloseKey(k)
        for folder, where in ((Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup", "folder"),
                              (Path(os.environ.get("PROGRAMDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup", "common")):
            if folder.exists():
                for p in folder.iterdir():
                    if p.name.lower() != "desktop.ini":
                        out[f"{where}\\{p.name}"] = {"name": p.stem, "cmd": str(p), "where": where, "path": str(p)}
        return out

    def _autostart(self):
        cur = self.autostart_entries()
        base = self.store.items.get("autostart")
        self.store.items["autostart"] = sorted(cur)
        self._save()
        if base is None:
            return
        new = [cur[k] for k in cur if k not in set(base)]
        for e in new:
            if "jarvis" in e["name"].lower():
                continue
            exe = re.sub(r'^"([^"]+)".*$', r"\1", e["cmd"]).split(" /")[0].split(" --")[0]
            if self._trusted_autostart(e, exe):
                continue
            self._say("auto:" + e["name"], f"В автозагрузку добавилась программа «{e['name']}» ({Path(exe).name}). "
                                            f"Если вы её не ставили — убрать её из автозагрузки?", priority=3,
                      on_yes=lambda e=e: self.core.say(self.remove_autostart(e)), cooldown_min=24 * 60)

    # updaters that add and remove themselves on every update: "msedge_cleanup_{…}" is Edge cleaning up its old version
    UPDATER_RX = re.compile(r"(?i)^(?:msedge_cleanup_|MicrosoftEdgeAutoLaunch_|GoogleUpdate|GoogleChromeAutoLaunch_|"
                            r"Delete Cached (?:Standalone )?Update Binary|OneDrive|Opera Browser Assistant|Discord$|Steam$)")

    def _trusted_autostart(self, e, exe):
        """Not worth a question: known self-updaters, and signed programs installed into Program Files / Windows."""
        if self.UPDATER_RX.match(e["name"]):
            return True
        low = exe.lower()
        installed = any(low.startswith(os.environ.get(v, "?").lower()) for v in ("ProgramFiles", "ProgramFiles(x86)", "SystemRoot"))
        return installed and os.path.exists(exe) and self._signed(exe) is True

    def remove_autostart(self, e):
        """Disable an autostart entry; the value is kept in data/sentinel.json to bring it back."""
        import winreg
        backup = self.store.items.setdefault("autostart_removed", [])
        try:
            if e["where"] in ("HKCU", "HKLM"):
                hive = winreg.HKEY_CURRENT_USER if e["where"] == "HKCU" else winreg.HKEY_LOCAL_MACHINE
                k = winreg.OpenKey(hive, e["key"], 0, winreg.KEY_SET_VALUE)
                winreg.DeleteValue(k, e["name"])
                winreg.CloseKey(k)
            else:
                q = DATA / "quarantine"
                q.mkdir(exist_ok=True)
                os.replace(e["path"], q / Path(e["path"]).name)
                e = dict(e, moved_to=str(q / Path(e["path"]).name))
        except PermissionError:
            return "Нужны права администратора, сэр: это общая автозагрузка. Уберите её в Диспетчере задач → Автозагрузка."
        except Exception as ex:
            return f"Не получилось: {ex}"
        backup.append(dict(e, at=dt.datetime.now().isoformat(timespec="seconds")))
        self._save()
        return f"Убрал «{e['name']}» из автозагрузки. Если понадобится — скажите «верни из автозагрузки {e['name']}»."

    def restore_autostart(self, name):
        import winreg
        from rapidfuzz import fuzz
        rem = self.store.items.get("autostart_removed", [])
        e = max(rem, key=lambda x: fuzz.partial_ratio(name.lower(), x["name"].lower()), default=None)
        if not e or fuzz.partial_ratio(name.lower(), e["name"].lower()) < 70:
            return "Такого я не убирал, сэр."
        try:
            if e["where"] in ("HKCU", "HKLM"):
                hive = winreg.HKEY_CURRENT_USER if e["where"] == "HKCU" else winreg.HKEY_LOCAL_MACHINE
                k = winreg.OpenKey(hive, e["key"], 0, winreg.KEY_SET_VALUE)
                winreg.SetValueEx(k, e["name"], 0, winreg.REG_SZ, e["cmd"])
                winreg.CloseKey(k)
            elif e.get("moved_to"):
                os.replace(e["moved_to"], e["path"])
        except Exception as ex:
            return f"Не получилось вернуть: {ex}"
        rem.remove(e)
        self._save()
        return f"Вернул «{e['name']}» в автозагрузку."

    # ---------------------------------------------------------------- processes
    def _signed(self, path):
        if path in self._sig_cache:
            return self._sig_cache[path]
        ok = None
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                f"(Get-AuthenticodeSignature -LiteralPath '{path.replace(chr(39), chr(39) * 2)}').Status"],
                               capture_output=True, text=True, timeout=12, creationflags=CREATE_NO_WINDOW)
            ok = r.stdout.strip().lower() == "valid"
        except Exception:
            ok = None
        self._sig_cache[path] = ok
        return ok

    def _processes(self):
        first = not self._seen_pids
        now_pids = set()
        for p in psutil.process_iter(["pid", "name", "exe", "create_time"]):
            pid = p.info["pid"]
            now_pids.add(pid)
            if pid in self._seen_pids or first:
                continue
            exe = (p.info["exe"] or "")
            low = exe.lower()
            if not low or not any(d in low for d in SUSPECT_DIRS) or not low.endswith(".exe"):
                continue
            if exe in self._suspect_seen:
                continue
            # installers people run on purpose from Downloads are fine if signed; unsigned ones are worth a word
            self._suspect_seen.add(exe)
            threading.Thread(target=self._check_suspect, args=(pid, exe), daemon=True).start()
        self._seen_pids = now_pids

    def _check_suspect(self, pid, exe):
        time.sleep(20)  # short-lived helpers come and go
        if not psutil.pid_exists(pid):
            return
        if self._signed(exe) is not False:
            return
        name = Path(exe).name
        if name.lower() in BENIGN_HELPERS:   # crash reporters shipped inside games and portable apps
            return
        where = "Temp" if "\\temp\\" in exe.lower() else "Загрузки" if "\\downloads\\" in exe.lower() else Path(exe).parent.name

        def kill():
            try:
                psutil.Process(pid).terminate()
                self.core.say(f"Закрыл {name}.")
            except Exception:
                self.core.say("Он уже закрылся.")
        self._say("proc:" + exe, f"Из папки {where} работает неподписанная программа {name}. Если вы её не запускали — "
                                 f"закрыть её?", priority=3, on_yes=kill, cooldown_min=12 * 60)

    def _hung_windows(self):
        import win32process
        now = time.time()
        alive = set()
        for w in S.list_windows():
            h = w["hwnd"]
            try:
                if not ctypes.windll.user32.IsHungAppWindow(h):
                    continue
            except Exception:
                continue
            alive.add(h)
            since = self._hung.setdefault(h, now)
            if now - since < 25:
                continue
            try:
                _, pid = win32process.GetWindowThreadProcessId(h)
                name = self.core.extras.app_info(psutil.Process(pid).name().lower())[0]
            except Exception:
                pid, name = None, w["title"][:30]

            def kill(pid=pid, name=name):
                try:
                    psutil.Process(pid).kill()
                    self.core.say(f"Закрыл {name}.")
                except Exception:
                    self.core.say("Не получилось, она, кажется, ожила.")
            self._say(f"hung:{h}", f"{name} не отвечает уже полминуты. Закрыть её?", priority=3,
                      on_yes=kill if pid else None, cooldown_min=30)
        for h in list(self._hung):
            if h not in alive:
                self._hung.pop(h, None)

    def _memory_growth(self):
        now = time.time()
        top = []
        for p in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                top.append((p.info["memory_info"].rss, p.info["pid"], (p.info["name"] or "").lower()))
            except Exception:
                pass
        top.sort(reverse=True)
        live = set()
        for rss, pid, name in top[:15]:
            live.add(pid)
            d = self._rss[pid]
            d.append((now, rss))
            while d and now - d[0][0] > 900:
                d.popleft()
            if len(d) >= 6 and rss > 2.5 * 2**30 and rss - d[0][1] > 1.5 * 2**30 and \
                    all(b[1] >= a[1] - 50 * 2**20 for a, b in zip(d, list(d)[1:])):
                gb = round(rss / 2**30, 1)
                nm = self.core.extras.app_info(name)[0]
                self._say(f"leak:{pid}", f"{nm} занимает уже {gb} гигабайта памяти и всё растёт — похоже на утечку. "
                                         "Перезапустить его будет разумно.", priority=2, cooldown_min=60)
        for pid in list(self._rss):
            if pid not in live:
                self._rss.pop(pid, None)

    # ---------------------------------------------------------------- GPU, network, disks
    def _gpu(self):
        g = S.gpu_info()
        if not g:
            return
        busy = g["util"] >= 70 and S.idle_seconds() > 240 and _user_state() not in (2, 3, 4)
        try:
            fg = S.active_exe()
        except Exception:
            fg = ""
        if not busy or self.core.extras.app_info(fg)[1] in ("Игры", "Медиа"):
            self._gpu_busy_since = 0.0
            return
        self._gpu_busy_since = self._gpu_busy_since or time.time()
        if time.time() - self._gpu_busy_since < 150:
            return
        who = ""
        try:
            r = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader"],
                               capture_output=True, text=True, timeout=5, creationflags=CREATE_NO_WINDOW)
            names = [Path(x.split(",")[1].strip()).name for x in r.stdout.strip().splitlines() if "," in x]
            names = [n for n in names if n.lower() not in HEAVY_OK]
            if not names:
                return  # a known heavy app (rendering, a local AI model) is doing it
            who = names[0]
        except Exception:
            pass
        self._say("gpu_idle", f"Пока вас не было, видеокарта загружена на {g['util']} процентов"
                              + (f" — это {who}" if who else "") + ". Если вы ничего не запускали, это подозрительно: "
                              "так выглядят майнеры.", priority=3, cooldown_min=120)

    def _network(self):
        ok = False
        for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53), ("ya.ru", 443)):
            try:
                with socket.create_connection((host, port), timeout=2.5):
                    ok = True
                    break
            except OSError:
                continue
        if ok:
            if not self._net_ok and self._net_fail >= 2:
                self._say("net_back", "Интернет снова есть.", priority=1, cooldown_min=1, title="Сеть")
            self._net_ok, self._net_fail = True, 0
            return
        self._net_fail += 1
        if self._net_ok and self._net_fail >= 2:
            self._net_ok = False
            self._say("net_down", "Пропал интернет. Проверьте роутер или кабель.", priority=2, cooldown_min=15, title="Сеть")

    def _upload(self):
        io_ = psutil.net_io_counters()
        self._up_hist.append((time.time(), io_.bytes_sent))
        if len(self._up_hist) < 7:
            return
        (t0, b0), (t1, b1) = self._up_hist[0], self._up_hist[-1]
        rate = (b1 - b0) / max(1.0, t1 - t0)
        if rate > 3 * 2**20 and S.idle_seconds() > 300 and _user_state() not in (2, 3, 4):
            mb = int((b1 - b0) / 2**20)
            self._say("upload", f"Компьютер без вас отправил в сеть {mb} мегабайт за минуту. Если вы ничего не "
                                "выгружаете — это стоит проверить.", priority=2, cooldown_min=60, title="Сеть")

    def _drives_check(self):
        cur = {}
        for d in psutil.disk_partitions(all=False):
            if "cdrom" in d.opts:
                continue
            cur[d.device] = d
        if self._drives is not None:
            for dev in set(cur) - set(self._drives):
                try:
                    total = round(psutil.disk_usage(cur[dev].mountpoint).total / 2**30)
                except Exception:
                    total = "?"
                self._say("drive:" + dev, f"Подключён диск {dev.rstrip(chr(92))}, {total} гигабайт.", priority=1,
                          cooldown_min=1, title="Устройства")
        self._drives = cur

    def _disk_fill(self):
        try:
            free = psutil.disk_usage(os.path.splitdrive(str(Path.home()))[0] + "\\").free
        except Exception:
            return
        now = time.time()
        self._disk_hist.append((now, free))
        old = next(((t, f) for t, f in self._disk_hist if now - t <= 900), None)
        if old and old[1] - free > 10 * 2**30:
            gb = round((old[1] - free) / 2**30)
            self._say("diskfill", f"За последние пятнадцать минут на системном диске исчезло {gb} гигабайт. "
                                  f"Осталось {round(free / 2**30)}.", priority=2, cooldown_min=60)

    def _hosts(self):
        try:
            data = HOSTS.read_bytes()
        except Exception:
            return
        h = hashlib.sha256(data).hexdigest()
        old = self.store.items.get("hosts")
        self.store.items["hosts"] = h
        lines = [x.strip() for x in data.decode("utf-8", "replace").splitlines() if x.strip() and not x.strip().startswith("#")]
        old_lines = set(self.store.items.get("hosts_lines", []))
        self.store.items["hosts_lines"] = lines[:300]
        self._save()
        if old and old != h:
            added = [x for x in lines if x not in old_lines][:3]
            self._say("hosts", "Изменился системный файл hosts" + (f": добавлено «{added[0]}»" if added else "") +
                      ". Так иногда делают вирусы, чтобы подменять сайты. Если это не вы — стоит проверить.", priority=3,
                      cooldown_min=180)

    def _defender(self):
        if self._defender_broken:
            return
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                "$s=Get-MpComputerStatus; \"$($s.RealTimeProtectionEnabled) $($s.AntivirusEnabled)\""],
                               capture_output=True, text=True, timeout=25, creationflags=CREATE_NO_WINDOW)
        except Exception:
            self._defender_broken = True
            return
        out = r.stdout.strip().lower()
        if r.returncode != 0 or not re.match(r"^(true|false) (true|false)$", out):
            self._defender_broken = True  # another antivirus manages protection
            return
        rt, av = out.split()
        if av == "true" and rt == "false":
            self._say("defender", "Защита в реальном времени Защитника Windows выключена. Если это не вы её "
                                  "отключали — включите в «Безопасность Windows».", priority=3, cooldown_min=24 * 60)

    # ---------------------------------------------------------------- UI
    def rpc_sentinel(self):
        return {"enabled": self.enabled(), "events": self.core.initiative.rpc_events(),
                "autostart": [{"id": k, **v} for k, v in self.autostart_entries().items()],
                "removed": self.store.items.get("autostart_removed", [])}

    def rpc_autostart_remove(self, id):
        e = self.autostart_entries().get(id)
        if not e:
            raise ValueError("нет такой записи")
        return self.remove_autostart(e)

    def rpc_autostart_restore(self, name):
        return self.restore_autostart(name)


# ====================================================================== voice rules
def register(R, core):
    def addr():
        return core.cfg.get("user.address") or "сэр"

    @R(r"^(?:что\s+(?:случилось|произошло|стряслось|нового\s+с\s+компьютером)|что\s+ты\s+заметил\w*|"
       r"(?:есть|были)\s+(?:какие-нибудь\s+|какие-то\s+)?(?:проблемы|события|предупреждения|тревоги)|"
       r"(?:все\s+ли\s+|всё\s+ли\s+)?в\s+порядке\s+с\s+(?:компьютером|пк|системой)|отчет\s+(?:сторожа|по\s+системе)|"
       r"(?:какие|последние)\s+(?:события|предупреждения))$")
    def _events(m, t):
        evs = core.initiative.summary(24)
        if not evs:
            return random_ok()
        core.show_tab("hub")
        parts = [f"в {e['at']} — {e['text'].rstrip('.?')}" for e in evs[:4]]
        return f"За сутки {len(evs)} {plural_events(len(evs))}. " + "; ".join(parts) + "."

    @R(r"^(?:что\s+(?:у\s+меня\s+)?в\s+автозагрузке|покажи\s+автозагрузку|список\s+автозагрузки)$")
    def _auto_list(m, t):
        es = list(core.sentinel.autostart_entries().values())
        core.show_tab("hub")
        if not es:
            return "Автозагрузка пуста."
        return f"В автозагрузке {len(es)}: " + ", ".join(e["name"] for e in es[:8]) + (" и другие." if len(es) > 8 else ".")

    @R(r"^(?:убери|удали|выключи|отключи)\s+(.+?)\s+из\s+автозагрузки$|^(?:убери|удали)\s+из\s+автозагрузки\s+(.+)$")
    def _auto_remove(m, t):
        from rapidfuzz import fuzz
        name = (m.group(1) or m.group(2)).strip()
        es = list(core.sentinel.autostart_entries().values())
        best = max(es, key=lambda e: fuzz.partial_ratio(name.lower(), e["name"].lower()), default=None)
        if not best or fuzz.partial_ratio(name.lower(), best["name"].lower()) < 70:
            return f"«{name}» в автозагрузке не нашёл."
        if "jarvis" in best["name"].lower():
            return "Себя я убирать не буду, сэр. Это делается в Настройках → Автозапуск."
        core.ask_confirm(lambda: core.say(core.sentinel.remove_autostart(best)), f"Убрать «{best['name']}» из автозагрузки?")
        return True

    @R(r"^(?:верни|восстанови)\s+(?:в\s+автозагрузку\s+|из\s+автозагрузки\s+)(.+)$")
    def _auto_restore(m, t):
        return core.sentinel.restore_autostart(m.group(1))

    @R(r"^(?:выключи|отключи)\s+сторож\w*(?:\s+пк)?$")
    def _off(m, t):
        core.cfg.set("sentinel.enabled", False)
        return "Сторож выключен. Странности в системе больше не отслеживаю."

    @R(r"^(?:включи|запусти)\s+сторож\w*(?:\s+пк)?$|^(?:следи|присматривай)\s+за\s+(?:компьютером|системой|пк)$")
    def _on(m, t):
        core.cfg.set("sentinel.enabled", True)
        return "Сторож включён. Скажу, если замечу что-то странное."

    @R(r"^(?:говори\s+(?:первым\s+)?только\s+(?:о|про)\s+(?:важном|важное|критичном)|только\s+важное)$")
    def _lvl2(m, t):
        core.cfg.set("initiative.level", 2)
        return "Принято: заговорю первым только о важном."

    @R(r"^(?:сообщай|говори|рассказывай)\s+(?:мне\s+)?(?:обо\s+всем|всё|все|чаще)$")
    def _lvl3(m, t):
        core.cfg.set("initiative.level", 3)
        return "Буду сообщать обо всём, что замечу."

    @R(r"^(?:не\s+говори\s+первым|говори\s+(?:первым\s+)?только\s+(?:о|про)\s+критич\w+|говори\s+реже)$")
    def _lvl1(m, t):
        core.cfg.set("initiative.level", 1)
        return "Хорошо. Первым заговорю только если что-то критичное."


def plural_events(n):
    from .planner import plural
    return plural(n, "событие", "события", "событий")


def random_ok():
    import random
    return random.choice(["За сутки ничего странного, сэр. Все системы в норме.",
                          "Всё спокойно: ни сбоев, ни подозрительной активности."])
