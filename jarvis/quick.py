"""Quick panel: a HUD of buttons over any program or game, on a global hotkey (Ctrl+Alt+Q by default).

Pinned protocols (commands with "quick": true, or the first ones when nothing is pinned), plugins, media with the
current track, volume, timers, focus, screenshot, lock, Jarvis's modes, the phone and the persona;
a line to type any command. The window is ui/quick.html; this module gives it data and runs the buttons."""
import base64
import datetime as dt
import io
import re
import threading

ACTIONS = {
    "play": "Пауза / играть", "next": "Следующий трек", "prev": "Предыдущий трек", "mute": "Без звука",
    "screenshot": "Скриншот", "lock": "Заблокировать ПК", "sleep": "Режим сна Джарвиса", "talk": "Режим разговора",
    "focus25": "Фокус 25 минут", "listen": "Слушать команду", "phone_ring": "Найти телефон",
    "phone_clip": "Буфер на телефон", "persona": "Следующая личность",
    "window": "Открыть Джарвиса",
}
PERSONA_ORDER = ["jarvis", "friday", "ultron"]


class Quick:
    def __init__(self, core):
        self.core = core
        self._visible = False

    @property
    def visible(self):
        return self._visible

    @visible.setter
    def visible(self, on):
        """While the panel is on screen the reactor breathes with the sound: the speakers' level goes to the page ~15
        times a second (the microphone level already flows as "mic_level"). Nothing runs while it is hidden."""
        was, self._visible = self._visible, bool(on)
        if on and not was:
            threading.Thread(target=self._levels, daemon=True, name="quick-levels").start()

    def _levels(self):
        import time
        from . import system as S
        try:
            meter = S.PeakMeter()   # one meter per opening, created and released in this thread only
        except Exception:
            return
        try:
            while self._visible:
                try:
                    self.core.emit("out_level", v=round(meter.peak(), 3))
                except Exception:
                    break           # the output device went away: the reactor just stops reacting to music
                time.sleep(0.066)
        finally:
            meter.close()

    def show(self):
        self.core.ui("quick_show")

    def hide(self):
        self.core.ui("quick_hide")

    @staticmethod
    def snapshot(rect):
        """The monitor as it looks right before the panel opens: the HUD blurs and darkens it into its background,
        so the screen seems to dissolve into the interface. A JPEG data URL, or "" (an exclusive-fullscreen game
        gives a black frame: the page then keeps its own dark background)."""
        try:
            from PIL import ImageGrab
            im = ImageGrab.grab(bbox=rect, all_screens=True).convert("RGB")
            im.thumbnail((1600, 1600))
            if max(im.getextrema()[0][1], im.getextrema()[1][1], im.getextrema()[2][1]) < 12:
                return ""
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=72)
            return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        except Exception:
            return ""

    # ---------------------------------------------------------------- data for the panel
    def rpc_quick(self):
        from . import system as S
        c = self.core
        cmds = [x for x in c.commands.store.items if x.get("enabled", True)]
        pinned = ([x for x in cmds if x.get("quick")] or cmds[:8])[:10]
        plugins = [{"file": i["file"], "name": i["name"]} for i in c.plugins.items.values()
                   if not i["error"] and c.plugins.enabled(i)][:max(0, 10 - len(pinned))]   # 10 slots around the core
        media = None
        try:
            ms = S.media_sessions()
            m = next((x for x in ms if x.get("current")), ms[0] if ms else None)
            if m:
                media = {"title": m.get("title", ""), "artist": m.get("artist", ""), "playing": m.get("playing"),
                         "app": m.get("app", ""), "position": m.get("position", 0), "duration": m.get("duration", 0)}
        except Exception:
            pass
        try:
            vol = S.get_volume()
        except Exception:
            vol = None
        phone = c.phone.device() if c.phone.devices else None
        try:
            stats = S.system_stats()
        except Exception:
            stats = {}
        now = dt.datetime.now()
        rem = []
        for r in sorted((r for r in c.planner.reminders.items if r.get("active")), key=lambda r: r.get("at", ""))[:3]:
            try:
                at = dt.datetime.fromisoformat(r["at"])
            except Exception:
                continue
            day = "" if at.date() == now.date() else "завтра " if at.date() == (now + dt.timedelta(days=1)).date() \
                else at.strftime("%d.%m ")
            rem.append({"text": r["text"], "at": day + at.strftime("%H:%M"), "alarm": bool(r.get("alarm"))})
        tasks = [{"id": t["id"], "text": t["text"], "due": (t.get("due") or "")[11:16]} for t in c.planner.sorted_tasks(True)[:5]]
        try:
            clip = (S.clipboard_get() or "").strip()
        except Exception:
            clip = ""
        h = now.hour
        greet = "Доброй ночи" if h < 5 else "Доброе утро" if h < 12 else "Добрый день" if h < 18 else "Добрый вечер"
        tray = c.rpc_tray_state()
        return {"commands": [{"id": x["id"], "name": x["name"]} for x in pinned], "plugins": plugins,
                "pinned": any(x.get("quick") for x in cmds), "media": media, "volume": vol, "stats": stats,
                "reminders": rem, "tasks": tasks, "clip": clip[:600], "clip_len": len(clip), "greet": c.persona.speech(f"{greet}, сэр."), "label": tray["label"],
                "theme": tray["theme"], "night": tray["night"],
                "timers": [{"id": t["id"], "label": t.get("label") or "Таймер", "left": c.planner.timer_left(t),
                            "total": t.get("duration") or 0, "paused": t.get("paused_left") is not None}
                           for t in c.planner.timers.items],
                "dnd": tray["dnd"],
                "persona": c.persona.id, "sleeping": c.sleeping, "conversation": c.conversation,
                "focus": bool(c.extras.focus_active()), "phone": (phone or {}).get("name"),
                "hotkey": c.cfg.get("quick.hotkey", "ctrl+alt+q"),
                "state": c.state}

    # ---------------------------------------------------------------- buttons
    def rpc_quick_run(self, kind, id="", value=None):
        c = self.core
        from . import system as S
        if kind == "command":
            cmd = c.commands.get(id)
            if not cmd:
                raise ValueError("команда не найдена")
            c.run_command(cmd, {})
        elif kind == "plugin":
            item = c.plugins.items.get(id)
            if not item:
                raise ValueError("плагин не найден")
            trig = next((t for t in item["triggers"] if not t.startswith("^") and "{" not in t), "")
            c.plugins.run_async(item, trig)
        elif kind == "text":
            c.execute(str(value or ""))
        elif kind == "volume":
            S.set_volume(int(value))
            return True
        elif kind == "timer":
            c.planner.add_timer(int(value) * 60, "")
        elif kind == "action":
            self._action(id)
        elif kind == "window":   # from the search line: bring that window up (after the panel has hidden)
            h = int(id)
            threading.Timer(0.25, lambda: self._focus(h)).start()
        elif kind == "app":
            ok, name = S.launch_app(str(id), c.apps)
            if not ok:
                raise ValueError(f"не нашёл «{id}»")
        elif kind == "timer_pause":
            t = next((t for t in c.planner.timers.items if t["id"] == id), None)
            if t:
                c.planner.pause_timer(id, t.get("paused_left") is None)
        elif kind == "timer_cancel":
            c.planner.cancel_timer(id)
        elif kind == "task_done":
            c.planner.update_task(id, done=True)
        elif kind == "seek":
            S.media_control("seek_to", float(value))
        else:
            raise ValueError("неизвестная кнопка")
        return True

    @staticmethod
    def _focus(h):
        import win32con
        import win32gui
        from . import system as S
        if win32gui.IsIconic(h):
            win32gui.ShowWindow(h, win32con.SW_RESTORE)
        S.focus_window(h)

    # ---------------------------------------------------------------- the search line: windows, apps, protocols
    @staticmethod
    def _windows():
        """Visible top-level windows with their titles read by InternalGetWindowText: GetWindowText would send
        WM_GETTEXT to Jarvis's own windows from this server thread (that once hung the app)."""
        import ctypes
        import os
        import psutil
        import win32gui
        import win32process
        u32, out, me = ctypes.windll.user32, [], os.getpid()
        buf = ctypes.create_unicode_buffer(300)

        def cb(h, _):
            if not win32gui.IsWindowVisible(h) or win32gui.GetWindowLong(h, -20) & 0x80:   # hidden / tool window
                return True
            u32.InternalGetWindowText(h, buf, 300)
            title = buf.value.strip()
            if not title or title in ("Program Manager", "Windows Input Experience"):
                return True
            try:
                _, pid = win32process.GetWindowThreadProcessId(h)
                if pid == me:
                    return True
                proc = psutil.Process(pid).name().replace(".exe", "")
            except Exception:
                proc = ""
            out.append({"hwnd": h, "title": title, "proc": proc})
            return True
        win32gui.EnumWindows(cb, None)
        return out

    def rpc_quick_search(self, q):
        from rapidfuzz import fuzz
        q = (q or "").strip().lower()
        if len(q) < 2:
            return []
        res = []

        def score(*names):
            return max((fuzz.partial_ratio(q, n.lower()) + (25 if n.lower().startswith(q) else 0)
                        + (10 if q in n.lower() else 0)) for n in names if n)
        for w in self._windows():
            s = score(w["title"], w["proc"])
            if s >= 80:
                res.append({"kind": "window", "id": w["hwnd"], "name": w["title"][:80], "sub": "окно · " + w["proc"], "s": s + 5})
        seen = set()
        for a in self.core.apps.apps:
            n = a.get("name") or ""
            if n.lower() in seen:
                continue
            s = score(n)
            if s >= 85:
                seen.add(n.lower())
                res.append({"kind": "app", "id": n, "name": n, "sub": "запустить", "s": s})
        for cmd in self.core.commands.store.items:
            if not cmd.get("enabled", True):
                continue
            s = score(cmd["name"], *cmd.get("triggers", [])[:5])
            if s >= 85:
                res.append({"kind": "command", "id": cmd["id"], "name": cmd["name"], "sub": "протокол", "s": s})
        res.sort(key=lambda r: -r["s"])
        return res[:7]

    # ---------------------------------------------------------------- what loads the PC right now
    def rpc_quick_top(self):
        """The busiest processes since the previous call (CPU over ~1.5 s) and the biggest by memory."""
        import os
        import psutil
        cache = getattr(self, "_procs", {})
        cur, me = {}, os.getpid()
        rows = []
        ncpu = psutil.cpu_count() or 1
        for p in psutil.process_iter(["name", "memory_info"]):
            if p.pid in (0, 4):
                continue
            proc = cache.get(p.pid) or p
            try:
                cpu = proc.cpu_percent(None) / ncpu
                rows.append((cpu, p.info["memory_info"].rss if p.info["memory_info"] else 0,
                             (p.info["name"] or "").replace(".exe", ""), p.pid == me))
                cur[p.pid] = proc
            except Exception:
                continue
        self._procs = cur
        by_name = {}
        for cpu, rss, name, mine in rows:   # chrome = all its processes together
            if mine:
                name = "Jarvis"
            a = by_name.setdefault(name, [0.0, 0])
            a[0] += cpu
            a[1] += rss
        cpu_top = sorted(by_name.items(), key=lambda kv: -kv[1][0])[:2]
        ram_top = max(by_name.items(), key=lambda kv: kv[1][1], default=None)
        return {"cpu": [{"name": n, "v": round(v[0])} for n, v in cpu_top if v[0] >= 1],
                "ram": {"name": ram_top[0], "gb": round(ram_top[1][1] / 2**30, 1)} if ram_top else None}

    def _action(self, a):
        c = self.core
        from . import system as S
        if a in ("play", "next", "prev"):
            S.media_control({"play": "toggle", "next": "next", "prev": "prev"}[a])
        elif a == "mute":
            S.media("mute")
        elif a == "screenshot":
            threading.Timer(0.4, lambda: S.screenshot(open_after=True)).start()   # after the panel has hidden
        elif a == "lock":
            S.lock()
        elif a == "sleep":
            c.set_sleep(not c.sleeping)
        elif a == "talk":
            c.set_conversation(not c.conversation)
        elif a == "focus25":
            c.extras.rpc_focus_start(25)
        elif a == "listen":
            c.rpc_listen()
        elif a == "phone_ring":
            threading.Thread(target=lambda: c.say(c.phone.ring()), daemon=True).start()
        elif a == "phone_clip":
            threading.Thread(target=lambda: c.say(c.phone.share_text(S.clipboard_get() or "")), daemon=True).start()
        elif a == "persona":
            cur = c.persona.id
            c.persona.set(PERSONA_ORDER[(PERSONA_ORDER.index(cur) + 1) % len(PERSONA_ORDER)] if cur in PERSONA_ORDER else "jarvis")
        elif a == "window":
            c.ui("show")
        elif a == "dnd":
            c.rpc_tray_action("dnd")
        elif a == "night":
            c.set_night(not c.night_on())
        elif a == "memory":
            c.background.submit("ram", "Освобождаю память", c.background.trim_memory)
        elif a == "taskmgr":
            import os
            os.startfile("taskmgr.exe")
        else:
            raise ValueError("неизвестное действие")

    @staticmethod
    def _google(text, src, target):
        """Free Google Translate. POST: the text goes in the body, not in the address. The Chrome extension's
        endpoint first: the old "gtx" one answers 429 to shared VPN addresses."""
        import requests
        try:
            r = requests.post("https://clients5.google.com/translate_a/t",
                              params={"client": "dict-chrome-ex", "sl": src or "auto", "tl": target},
                              data={"q": text}, timeout=6)
            r.raise_for_status()
            j = r.json()
            x = j[0] if j else ""
            return (x[0] if isinstance(x, list) else x).strip()   # with sl=auto: [["текст", "en"]]
        except Exception:
            r = requests.post("https://translate.googleapis.com/translate_a/single",
                              params={"client": "gtx", "sl": src or "auto", "tl": target, "dt": "t"},
                              data={"q": text}, timeout=6)
            r.raise_for_status()
            return "".join(x[0] for x in r.json()[0] if x and x[0]).strip()

    def rpc_quick_clip(self, action):
        """The clipboard panel: translate (shown in the HUD), read aloud, search the web."""
        from . import system as S
        text = (S.clipboard_get() or "").strip()
        if not text:
            raise ValueError("буфер пуст")
        if action == "translate":
            ru = len(re.findall(r"[а-яё]", text.lower())) > len(re.findall(r"[a-z]", text.lower()))
            return {"text": self._google(text[:4000], "auto", "en" if ru else "ru"), "to": "en" if ru else "ru"}
        if action == "say":
            threading.Thread(target=lambda: self.core.say(text[:1500]), daemon=True).start()
        elif action == "search":
            import urllib.parse
            import webbrowser
            webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote(text[:300]))
        return True

    def rpc_quick_show(self):
        self.show()
        return True

    def rpc_quick_hide(self, gen=None):
        """From the page: gen is the opening it belongs to, so a late close from an old opening does nothing."""
        self.core.ui("quick_hide", gen, False)
        return True

    def rpc_quick_pin(self, id, on):
        cmd = self.core.commands.get(id)
        if cmd:
            cmd["quick"] = bool(on)
            self.core.commands.save_command(cmd)
        return bool(on)


# ====================================================================== voice rules
def register(R, core):
    @R(r"^(?:открой|покажи|вызови)\s+(?:быструю\s+панель|панель\s+(?:быстрых\s+)?действий|быстрые\s+действия)$")
    def _show(m, t):
        core.quick.show()
        return True
