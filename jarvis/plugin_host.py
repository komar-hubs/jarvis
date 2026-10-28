"""Runs one plugin call in its own process: python -I plugin_host.py < {"path", "text", "args", "store", "perms"}.

A plugin that hangs or crashes takes only this process down, never Jarvis. What the plugin wants done on the PC
(speak, open, type…) comes back as a list of effects and Jarvis performs them.

Permissions ("net", "open", "keys", "run", "system") are enforced here: api methods and imports of the matching
modules raise PermissionError when not granted. It is a safety catch against accidents and obvious actions,
not a sandbox: Python code that tries hard can get around it."""
import builtins
import importlib.util
import json
import os
import sys
import traceback

TITLES = {"net": "интернет", "open": "открывать сайты и файлы", "keys": "клавиатура и буфер обмена",
          "run": "команды Джарвису", "system": "файлы и система"}
MODULES = {"requests": "net", "urllib": "net", "http": "net", "socket": "net", "httpx": "net", "aiohttp": "net",
           "ftplib": "net", "smtplib": "net", "websocket": "net", "websockets": "net", "ssl": "net",
           "webbrowser": "open",
           "pyautogui": "keys", "keyboard": "keys", "pynput": "keys", "pyperclip": "keys", "win32clipboard": "keys",
           "subprocess": "system", "ctypes": "system", "winreg": "system", "shutil": "system", "multiprocessing": "system",
           "win32api": "system", "win32con": "system", "win32com": "system", "win32process": "system", "_winapi": "system",
           "psutil": "system", "signal": "system"}
OS_CALLS = ("system", "popen", "remove", "unlink", "rmdir", "removedirs", "rename", "renames", "replace", "kill",
            "startfile", "execv", "execl", "spawnl", "spawnv", "chmod", "truncate")
PERMS = set()


def need(p):
    if p not in PERMS:
        raise PermissionError(f"плагину не разрешено: {TITLES.get(p, p)}. Разрешите во вкладке «Плагины».")


class Api:
    """What a plugin gets as `api`."""

    def __init__(self, store):
        self.effects = []
        self.store = store          # dict saved between calls (data/plugins/<file>.json)

    # ---- the internet
    def get(self, url, **kw):
        need("net")
        import requests
        r = requests.get(url, timeout=kw.pop("timeout", 15), headers=kw.pop("headers", {"User-Agent": "Mozilla/5.0 JARVIS"}), **kw)
        r.raise_for_status()
        return r.text

    def get_json(self, url, **kw):
        need("net")
        import requests
        r = requests.get(url, timeout=kw.pop("timeout", 15), headers=kw.pop("headers", {"User-Agent": "Mozilla/5.0 JARVIS"}), **kw)
        r.raise_for_status()
        return r.json()

    def post_json(self, url, data, **kw):
        need("net")
        import requests
        r = requests.post(url, json=data, timeout=kw.pop("timeout", 15), **kw)
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:
            return r.text

    # ---- things Jarvis does after the call
    def say(self, text):
        self.effects.append(["say", str(text)])

    def open(self, target):
        need("open")
        self.effects.append(["open", str(target)])

    def notify(self, title, text=""):
        self.effects.append(["notify", str(title), str(text)])

    def clipboard(self, text):
        need("keys")
        self.effects.append(["clipboard", str(text)])

    def type(self, text):
        need("keys")
        self.effects.append(["type", str(text)])

    def hotkey(self, keys):
        need("keys")
        self.effects.append(["hotkey", str(keys)])

    def run(self, phrase):
        """Any command Jarvis understands: api.run("открой телеграм и напиши Ване привет")."""
        need("run")
        self.effects.append(["run", str(phrase)])


def guard():
    """Imports made by the plugin's own code are checked; Jarvis's helpers above import freely."""
    real = builtins.__import__

    def imp(name, globals=None, locals=None, fromlist=(), level=0):
        if globals and globals.get("__name__") == "jarvis_plugin" and level == 0:
            p = MODULES.get(name.split(".")[0])
            if p:
                need(p)
        return real(name, globals, locals, fromlist, level)
    builtins.__import__ = imp
    if "system" not in PERMS:
        def blocked(*a, **k):
            need("system")
        for fn in OS_CALLS:
            if hasattr(os, fn):
                setattr(os, fn, blocked)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    req = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    PERMS.update(req.get("perms") or [])
    out_stream = sys.stdout
    api = Api(req.get("store") or {})
    out = {"ok": True, "result": None}
    try:
        spec = importlib.util.spec_from_file_location("jarvis_plugin", req["path"])
        mod = importlib.util.module_from_spec(spec)
        guard()
        spec.loader.exec_module(mod)
        res = mod.run(req.get("text", ""), req.get("args") or {}, api)
        out["result"] = res if isinstance(res, (str, int, float, type(None))) else json.dumps(res, ensure_ascii=False)
    except Exception:
        out = {"ok": False, "error": traceback.format_exc()[-1500:]}
    out["effects"] = api.effects
    out["store"] = api.store
    out_stream.write(json.dumps(out, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
