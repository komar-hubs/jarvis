"""J.A.R.V.I.S. launcher: core + local server + desktop window (pywebview) + tray icon + global hotkeys."""
import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

# before any window exists: window calls that cannot deadlock against the WebView GUI thread (jarvis/winsafe.py)
from jarvis import winsafe  # noqa: E402
winsafe.install()

class RotatingLog:
    """data/jarvis.log that never grows past ~5 MB: it reached 60 MB of asyncio noise and was never trimmed."""
    LIMIT = 5_000_000

    def __init__(self, path):
        self.path = path
        self.n = 0
        self._open()

    def _open(self):
        try:
            if self.path.exists() and self.path.stat().st_size > self.LIMIT:
                self.path.replace(self.path.with_name("jarvis.old.log"))
        except OSError:
            pass
        self.f = open(self.path, "a", encoding="utf-8", buffering=1)

    def write(self, s):
        self.n += 1
        if self.n % 500 == 0:
            try:
                if self.f.tell() > self.LIMIT:
                    self.f.close()
                    self._open()
            except Exception:
                pass
        try:
            return self.f.write(s)
        except Exception:
            return 0

    def flush(self):
        try:
            self.f.flush()
        except Exception:
            pass

    def isatty(self):
        return False


# pythonw has no console: send prints to a log file instead of crashing on write
if sys.stdout is None or sys.executable.lower().endswith("pythonw.exe"):
    log = RotatingLog(ROOT / "data" / "jarvis.log")
    sys.stdout = sys.stderr = log


def single_instance():
    """A named mutex per Jarvis folder. A second copy used to start when the first one was slow to answer
    the ping: it failed to bind the port but kept listening to the microphone, so 4 Jarvises answered at once."""
    import ctypes
    import hashlib
    name = "Local\\JARVIS_" + hashlib.md5(str(ROOT).lower().encode()).hexdigest()[:16]
    h = ctypes.windll.kernel32.CreateMutexW(None, False, name)
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return None
    return h


def kill_orphan_webviews():
    """WebView2 processes of a previous Jarvis that was killed (crash, "taskkill python") keep our profile folder
    locked, and the new window fails with 0x800700AA "Требуемый ресурс занят". Only processes started with our
    own data/webview folder are touched (already_running() has confirmed no live Jarvis owns them)."""
    try:
        import psutil
        mine = str(ROOT / "data" / "webview").lower()
        for p in psutil.process_iter(["name", "cmdline"]):
            if (p.info["name"] or "").lower() == "msedgewebview2.exe" and mine in " ".join(p.info["cmdline"] or []).lower():
                p.kill()
    except Exception:
        pass


def already_running():
    cfg_path = ROOT / "data" / "config.json"
    if not cfg_path.exists():
        return False
    try:
        cfg = json.loads(cfg_path.read_text("utf-8"))
        port, token = cfg.get("port", 8765), cfg.get("token", "")
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1) as r:
            if json.loads(r.read()).get("app") != "jarvis":
                return False
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/show", data=b"{}", method="POST",
                                     headers={"X-Jarvis-Token": token, "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=2)
        return True
    except Exception:
        return False


def tray_image():
    from PIL import Image, ImageDraw
    s = 64
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, s - 2, s - 2), outline=(80, 220, 255, 255), width=4)
    d.ellipse((14, 14, s - 14, s - 14), outline=(80, 220, 255, 200), width=3)
    d.ellipse((24, 24, s - 24, s - 24), fill=(190, 245, 255, 255))
    for i in range(3):
        import math
        a = math.radians(90 + i * 120)
        cx, cy = s / 2 + math.cos(a) * 20, s / 2 + math.sin(a) * 20
        d.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), fill=(80, 220, 255, 255))
    return img


class WindowApi:
    """Methods callable from JS as window.pywebview.api.*"""

    def __init__(self):
        self._window = None
        self._maxed = False

    def minimize(self):
        self._window.minimize()

    def hide(self):
        self._window.hide()

    def toggle_max(self):
        if self._maxed:
            self._window.restore()
        else:
            self._window.maximize()
        self._maxed = not self._maxed

    def pick_file(self, kind="any"):
        import webview
        types = {"wav": ("Audio (*.wav)",), "py": ("Плагин Джарвиса (*.py)",),
                 "zip": ("Резервная копия Джарвиса (*.zip)",)}.get(kind, ("All files (*.*)",))
        r = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=types)
        return r[0] if r else None

    def pick_folder(self):
        import webview
        r = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return r[0] if r else None

    def quit(self):
        threading.Thread(target=shutdown, daemon=True).start()


api = WindowApi()
tray = None
core = None
_mutex = None


def shutdown():
    try:
        from jarvis import system
        system.duck(False)  # never leave other apps quiet
    except Exception:
        pass
    try:
        if core:
            core.tts.engines["xtts"].reload()
    except Exception:
        pass
    try:
        if tray:
            tray.stop()
    except Exception:
        pass
    os._exit(0)


def main():
    global core, tray
    hidden = "--hidden" in sys.argv
    global _mutex
    _mutex = single_instance()
    if _mutex is None:
        # another copy owns this folder: bring its window up (it may still be loading) and leave
        for _ in range(20):
            if already_running():
                break
            time.sleep(0.5)
        print("Jarvis is already running.")
        return
    if already_running():
        print("Jarvis is already running, focused the existing window.")
        return

    import webview
    from jarvis.core import Core
    from jarvis.server import Server

    core = Core()
    server = Server(core)
    server.start()
    core.emit_hook = server.broadcast
    url = f"http://127.0.0.1:{server.port}/"
    for _ in range(50):
        try:
            urllib.request.urlopen(url + "api/ping", timeout=0.5)
            break
        except Exception:
            time.sleep(0.1)
    if server.failed:
        core.log(f"Порт {server.port} занят другой программой: интерфейс не откроется. Смените «port» в "
                 f"data/config.json или закройте программу, которая его держит.", "error")

    window = webview.create_window(
        "J.A.R.V.I.S.", url, js_api=api, width=1360, height=860, min_size=(980, 640),
        frameless=True, easy_drag=False, background_color="#03080f", hidden=hidden or core.cfg.get("start_hidden"))
    api._window = window

    def on_closing():
        window.hide()
        return False  # keep running in tray

    window.events.closing += on_closing

    def show():
        window.show()
        window.restore()
        try:
            window.on_top = True
            window.on_top = False
        except Exception:
            pass

    # the tray menu in Jarvis's own style (ui/traymenu.html) instead of the grey Windows one; the native menu stays
    # as a fallback if this window cannot be shown
    MW, MH = 290, 350
    tray_menu = None
    try:
        tray_menu = webview.create_window(
            "JARVIS MENU", f"{url}ui/traymenu.html?t={core.cfg.get('token')}", width=MW, height=MH, frameless=True,
            easy_drag=False, on_top=True, hidden=True, resizable=False, background_color="#040a12")
    except Exception as e:
        core.log(f"Меню трея: {e}", "warn")

    def menu_hide():
        if tray_menu:
            tray_menu.hide()

    def menu_show():
        import ctypes
        from ctypes import wintypes
        pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        wa = wintypes.RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(wa), 0)  # work area (without the taskbar)
        x = max(wa.left + 6, min(pt.x - MW + 14, wa.right - MW - 6))
        y = pt.y - MH - 8 if pt.y - MH - 8 >= wa.top else pt.y + 8   # above the cursor; below when the taskbar is on top
        y = max(wa.top + 6, min(y, wa.bottom - MH - 6))
        tray_menu.move(x, y)
        tray_menu.show()
        import win32gui
        from jarvis import system as S
        h = win32gui.FindWindow(None, "JARVIS MENU")
        if h:
            S.focus_window(h)  # focus (with the Alt trick): a click anywhere else then closes it (blur)
            tray_menu.evaluate_js("opened();")

    core.ui_hooks = {"show": show, "hide": window.hide, "quit": shutdown, "menu_hide": menu_hide}

    # tray
    try:
        import pystray
        from pystray._util import win32 as _pw32

        class TrayIcon(pystray.Icon):
            def _on_notify(self, wparam, lparam):
                if lparam == _pw32.WM_RBUTTONUP and tray_menu is not None:
                    try:
                        menu_show()
                        return
                    except Exception as e:
                        core.log(f"Меню трея: {e}", "warn")
                return super()._on_notify(wparam, lparam)

        menu = pystray.Menu(
            pystray.MenuItem("Открыть J.A.R.V.I.S.", lambda i, it: show(), default=True),
            pystray.MenuItem("Слушать команду", lambda i, it: core.rpc_listen()),
            pystray.MenuItem("Режим сна", lambda i, it: core.set_sleep(not core.sleeping),
                             checked=lambda it: core.sleeping),
            pystray.MenuItem("Замолчать", lambda i, it: core.stop_all()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", lambda i, it: shutdown()))
        tray = TrayIcon("jarvis", tray_image(), "J.A.R.V.I.S.", menu)
        tray.run_detached()
    except Exception as e:
        core.log(f"Трей недоступен: {e}", "warn")

    # global hotkeys
    try:
        import keyboard
        keyboard.add_hotkey(core.cfg.get("stt.ptt_hotkey", "ctrl+alt+j"), lambda: core.rpc_listen())
        keyboard.add_hotkey(core.cfg.get("stt.show_hotkey", "ctrl+alt+space"),
                            lambda: show() if not window_visible() else window.hide())
    except Exception as e:
        core.log(f"Горячие клавиши: {e}", "warn")

    def window_visible():
        try:
            import win32gui
            h = win32gui.FindWindow(None, "J.A.R.V.I.S.")
            return bool(h and win32gui.IsWindowVisible(h) and not win32gui.IsIconic(h))
        except Exception:
            return False

    # floating HUD: appears above all windows while Jarvis listens/thinks/speaks and the main window is not in front
    overlay = None
    if core.cfg.get("overlay", True):
        try:
            import ctypes
            from ctypes import wintypes
            wa = wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(wa), 0)  # SPI_GETWORKAREA
            ow, oh = 430, 104
            overlay = webview.create_window(
                "JARVIS HUD", f"{url}ui/overlay.html?t={core.cfg.get('token')}", width=ow, height=oh,
                x=wa.right - ow - 18, y=wa.bottom - oh - 18, frameless=True, easy_drag=True, on_top=True,
                focus=False, hidden=True, resizable=False, background_color="#03080f")
        except Exception as e:
            core.log(f"HUD-индикатор недоступен: {e}", "warn")

    ui_ready = threading.Event()
    window.events.loaded += lambda *a: ui_ready.set()

    def overlay_loop():
        import win32con
        import win32gui
        ui_ready.wait(30)  # never restyle windows while WebView2 is still creating them
        time.sleep(1.0)
        shown, last_active, hwnd = False, 0.0, 0

        def hud():
            """The HUD must never take focus (dictation types into the active window), so it is shown with
            WinAPI: WS_EX_NOACTIVATE + SW_SHOWNOACTIVATE instead of pywebview's show()."""
            nonlocal hwnd
            if not hwnd or not win32gui.IsWindow(hwnd):
                hwnd = win32gui.FindWindow(None, "JARVIS HUD")
                if hwnd:
                    ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE,
                                           ex | 0x08000000 | win32con.WS_EX_TOOLWINDOW)  # NOACTIVATE, no taskbar
            return hwnd

        def show_hud():
            h = hud()
            if h:
                win32gui.ShowWindow(h, 4)  # SW_SHOWNOACTIVATE
                win32gui.SetWindowPos(h, win32con.HWND_TOPMOST, 0, 0, 0, 0, win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
                                      | win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW)

        def hide_hud():
            h = hud()
            if h:
                win32gui.ShowWindow(h, 0)
        while True:
            time.sleep(0.15)
            busy = core.state in ("listening", "thinking", "speaking") or getattr(core, "dictating", False) \
                or core.alarm_active or bool(core.notice) \
                or core.conversation or time.time() < core.follow_until  # "говорите без имени" must be visible
            if busy:
                last_active = time.time()
            try:
                main_front = win32gui.GetWindowText(win32gui.GetForegroundWindow()) == "J.A.R.V.I.S."
            except Exception:
                main_front = False
            want = time.time() - last_active < 3.5 and not main_front and core.cfg.get("overlay", True)
            try:
                if want and not shown:
                    show_hud()
                    shown = True
                elif not want and shown:
                    hide_hud()
                    shown = False
            except Exception:
                pass

    if overlay:
        threading.Thread(target=overlay_loop, daemon=True, name="overlay").start()

    core.start()
    kill_orphan_webviews()
    webview.start(private_mode=False, storage_path=str(ROOT / "data" / "webview"))
    shutdown()


if __name__ == "__main__":
    main()
