"""Macros by demonstration: "Джарвис, запиши макрос" — the user clicks and types, "стоп запись", gives it a name, and
from then on saying the name repeats it at the same pace. Nothing goes to the AI: clicks, drags, the wheel and keys
are recorded on this PC (a low-level mouse hook and the keyboard hook) and played back the same way.

Saved as data/macros/<name>.json and as a command with that name (Команды tab), so it can also be one step of a
chain or an automation. "стоп" stops a playing macro."""
import ctypes
import json
import re
import threading
import time
from ctypes import wintypes

from .config import DATA

DIR = DATA / "macros"
user32 = ctypes.WinDLL("user32")
kernel32 = ctypes.WinDLL("kernel32")
WH_MOUSE_LL, WM_QUIT = 14, 0x0012
DOWN = {0x201: "left", 0x204: "right", 0x207: "middle"}
UP = {0x202: "left", 0x205: "right", 0x208: "middle"}
WM_MOUSEWHEEL = 0x20A


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.GetMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
kernel32.GetModuleHandleW.restype = ctypes.c_void_p


class Macros:
    def __init__(self, core):
        self.core = core
        self.recording = None        # {"t0", "mouse": [...], "keys": [...], "thread_id"}
        self.playing = False

    # ---------------------------------------------------------------- recording
    def start(self):
        if self.recording:
            return False
        import keyboard
        rec = {"t0": time.time(), "mouse": [], "keys": [], "thread_id": None}
        self.recording = rec

        def on_key(e):
            rec["keys"].append({"t": e.time - rec["t0"], "type": e.event_type, "name": e.name, "scan": e.scan_code})
        rec["khook"] = keyboard.hook(on_key)
        threading.Thread(target=self._mouse_hook, args=(rec,), daemon=True, name="macro-mouse").start()
        return True

    def _mouse_hook(self, rec):
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))     # physical pixels, like playback
        rec["thread_id"] = kernel32.GetCurrentThreadId()

        def proc(n, wparam, lparam):
            if n == 0:
                s = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if not s.flags & 1:                                   # LLMHF_INJECTED: skip Jarvis's own clicks
                    t = time.time() - rec["t0"]
                    if wparam in DOWN:
                        rec["mouse"].append({"t": t, "type": "down", "button": DOWN[wparam], "x": s.pt.x, "y": s.pt.y})
                    elif wparam in UP:
                        rec["mouse"].append({"t": t, "type": "up", "button": UP[wparam], "x": s.pt.x, "y": s.pt.y})
                    elif wparam == WM_MOUSEWHEEL:
                        delta = ctypes.c_short(s.mouseData >> 16).value
                        rec["mouse"].append({"t": t, "type": "wheel", "dir": "up" if delta > 0 else "down", "x": s.pt.x, "y": s.pt.y})
            return user32.CallNextHookEx(None, n, wparam, lparam)
        cb = HOOKPROC(proc)
        rec["cb"] = cb                                               # keep a reference: the hook must not be collected
        hook = user32.SetWindowsHookExW(WH_MOUSE_LL, cb, kernel32.GetModuleHandleW(None), 0)
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        user32.UnhookWindowsHookEx(hook)

    def stop(self):
        """Stop recording; returns the steps (clicks, drags, wheel, typed text and hotkeys) or None."""
        rec, self.recording = self.recording, None
        if not rec:
            return None
        import keyboard
        try:
            keyboard.unhook(rec["khook"])
        except Exception:
            pass
        if rec.get("thread_id"):
            user32.PostThreadMessageW(rec["thread_id"], WM_QUIT, 0, 0)
        return self._steps(rec)

    @staticmethod
    def _steps(rec):
        steps = []
        # mouse: a press and release in one place is a click (two quick ones a double click); apart — a drag
        pend = {}
        for e in rec["mouse"]:
            if e["type"] == "down":
                pend[e["button"]] = e
            elif e["type"] == "up" and e["button"] in pend:
                d = pend.pop(e["button"])
                if abs(e["x"] - d["x"]) + abs(e["y"] - d["y"]) > 10:
                    steps.append({"t": d["t"], "do": "drag", "button": d["button"], "x": d["x"], "y": d["y"], "x2": e["x"], "y2": e["y"]})
                else:
                    last = steps[-1] if steps else None
                    if last and last["do"] == "click" and last["button"] == d["button"] and d["t"] - last["t"] < 0.4 \
                            and abs(last["x"] - d["x"]) + abs(last["y"] - d["y"]) < 6 and not last.get("double"):
                        last["double"] = True
                    else:
                        steps.append({"t": d["t"], "do": "click", "button": d["button"], "x": d["x"], "y": d["y"]})
            elif e["type"] == "wheel":
                last = steps[-1] if steps else None
                if last and last["do"] == "wheel" and last["dir"] == e["dir"] and e["t"] - last["t_end"] < 0.5:
                    last["n"] += 1
                    last["t_end"] = e["t"]
                else:
                    steps.append({"t": e["t"], "t_end": e["t"], "do": "wheel", "dir": e["dir"], "n": 1, "x": e["x"], "y": e["y"]})
        # keys: as they were, pressed and released (typing, Ctrl+C, Enter); Jarvis's own hotkeys are left out
        mods = {"ctrl", "left ctrl", "right ctrl", "alt", "left alt", "right alt", "alt gr", "shift", "left shift",
                "right shift", "left windows", "right windows"}
        held, skip_up, keys = set(), set(), []
        for k in rec["keys"]:
            name = (k["name"] or "").lower()
            if k["type"] == "down":
                if name in mods:
                    held.add(name)
                elif {h for h in held if "ctrl" in h} and {h for h in held if "alt" in h} and name in ("j", "q", "space", "о", "й"):
                    # Ctrl+Alt+J / Q / Space are Jarvis's own hotkeys, not part of the macro: the whole chord goes,
                    # its modifiers too (left in, they would be held down during playback)
                    skip_up |= {name} | held
                    for mod in held:                         # only the presses still held now, not an earlier Ctrl+C
                        i = max((j for j, x in enumerate(keys) if x["type"] == "down" and (x["name"] or "").lower() == mod), default=None)
                        if i is not None:
                            keys.pop(i)
                    continue
            else:
                if name in skip_up:
                    skip_up.discard(name)
                    held.discard(name)
                    continue
                held.discard(name)
            keys.append({"t": k["t"], "do": "key", "type": k["type"], "name": k["name"], "scan": k["scan"]})
        steps += keys
        steps.sort(key=lambda s: s["t"])
        return steps

    # ---------------------------------------------------------------- saving and playing
    @staticmethod
    def _path(name):
        return DIR / (re.sub(r"[^\w\- ]", "", name).strip().lower() + ".json")

    def save(self, name, steps):
        DIR.mkdir(parents=True, exist_ok=True)
        self._path(name).write_text(json.dumps({"name": name, "steps": steps}, ensure_ascii=False), encoding="utf-8")
        # a command with the same name: "Джарвис, <name>" plays it, and it can be a step of a chain or an automation
        cmds = self.core.commands
        if not cmds.get(name):
            cmds.save_command({"name": name, "triggers": [name.lower(), f"макрос {name.lower()}"], "reply": "",
                               "actions": [{"type": "voice", "value": f"выполни макрос {name}"}], "enabled": True})

    def names(self):
        return sorted(json.loads(p.read_text("utf-8")).get("name", p.stem) for p in DIR.glob("*.json")) if DIR.exists() else []

    def play(self, name, speed=1.0):
        p = self._path(name)
        if not p.exists():
            from rapidfuzz import fuzz
            best = max(self.names(), key=lambda n: fuzz.ratio(n.lower(), name.lower()), default=None)
            if not best or fuzz.ratio(best.lower(), name.lower()) < 70:
                raise LookupError(f"нет макроса «{name}»")
            p = self._path(best)
        steps = json.loads(p.read_text("utf-8"))["steps"]
        if self.playing:
            raise RuntimeError("уже выполняю макрос")
        import keyboard
        from . import mouse as MS
        gen = self.core.cancel_gen
        self.playing = True
        try:
            prev = steps[0]["t"] if steps else 0
            for s in steps:
                if gen != self.core.cancel_gen:
                    return False                                     # "стоп"
                time.sleep(min(3.0, max(0.0, (s["t"] - prev) / max(0.25, speed))))
                prev = s["t"]
                if s["do"] == "click":
                    MS.click(s["x"], s["y"], button=s["button"], double=bool(s.get("double")))
                elif s["do"] == "drag":
                    MS.drag(s["x"], s["y"], s["x2"], s["y2"], button=s["button"])
                elif s["do"] == "wheel":
                    MS.scroll(s["dir"], s["n"], s["x"], s["y"])
                elif s["do"] == "key":
                    key = s["scan"] or s["name"]
                    (keyboard.press if s["type"] == "down" else keyboard.release)(key)
            return True
        finally:
            self.playing = False
            try:
                import keyboard as kb
                for m in ("ctrl", "alt", "shift"):                    # never leave a modifier stuck down
                    kb.release(m)
            except Exception:
                pass

    def delete(self, name):
        p = self._path(name)
        if p.exists():
            p.unlink()
            return True
        return False

    def rpc_macros(self):
        return self.names()

    def rpc_macro_play(self, name):
        threading.Thread(target=lambda: self.play(name), daemon=True, name="macro-play").start()
        return True

    def rpc_macro_delete(self, name):
        ok = self.delete(name)
        c = self.core.commands.get(name)
        if ok and c:
            self.core.commands.delete_command(c["id"])
        return ok


# ====================================================================== voice rules
def register(R, core):
    M = lambda: core.macros

    @R(r"^(?:запиши|начни\s+запись|записывай)\s+(?:новый\s+)?макрос$|^(?:смотри\s+и\s+запоминай|запоминай\s+(?:мои\s+)?действия)$")
    def _rec(m, t):
        if not M().start():
            return "Уже записываю, сэр. Скажите «стоп запись», когда закончите."
        return "Записываю, сэр. Делайте всё, что нужно, потом скажите «стоп запись»."

    @R(r"^(?:стоп|останови|закончи|заверши)\s+запис\w*$|^запись\s+(?:окончена|закончена|всё|стоп)$|^(?:всё|готово),?\s+(?:записал|запиши)$")
    def _stop(m, t):
        if not M().recording:
            return False
        steps = M().stop()
        if not steps:
            return "Ничего не записалось, сэр: ни кликов, ни клавиш."
        clicks = sum(1 for s in steps if s["do"] in ("click", "drag"))
        keys = sum(1 for s in steps if s["do"] == "key" and s["type"] == "down")

        def take(name):
            name = re.sub(r"^(?:назови\s+(?:его\s+)?|пусть\s+будет\s+|макрос\s+)", "", name.strip(), flags=re.I).strip(" .!«»\"")
            if not name:
                core.say("Без названия не сохраню, сэр.")
                return
            M().save(name, steps)
            core.say(f"Сохранил макрос «{name}». Скажите «{name}», и я повторю.")
        core.say(f"Записал: кликов {clicks}, клавиш {keys}. Как назвать макрос?")
        core.capture = (take, time.time() + 25)
        core.listen_next(25)
        return True

    @R(r"^(?:выполни|запусти|повтори|сделай)\s+макрос\s+(.+?)(?:\s+(быстро|быстрее|медленно|медленнее))?$")
    def _play(m, t):
        name = m.group(1).strip()
        speed = {"быстро": 2.0, "быстрее": 1.6, "медленно": 0.6, "медленнее": 0.75}.get(m.group(2) or "", 1.0)

        def go():
            try:
                done = M().play(name, speed)
                if done is False:
                    core.say("Остановил макрос.")
            except Exception as e:
                core.say(f"Не получилось: {e}.")
        threading.Thread(target=go, daemon=True, name="macro-play").start()
        return True

    @R(r"^(?:какие|мои)\s+макросы$|^список\s+макросов$")
    def _list(m, t):
        n = M().names()
        return ("Макросы: " + ", ".join(n) + ".") if n else "Макросов пока нет. Скажите «запиши макрос»."

    @R(r"^(?:удали|забудь)\s+макрос\s+(.+)$")
    def _del(m, t):
        name = m.group(1).strip()
        if M().delete(name):
            c = core.commands.get(name)
            if c:
                core.commands.delete_command(c["id"])
            return f"Удалил макрос «{name}»."
        return f"Нет макроса «{name}»."
