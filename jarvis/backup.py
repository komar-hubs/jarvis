"""Backups of everything the user made: settings (with keys and mail passwords), commands, integrations,
automations, plugins with their saved data, notes, tasks, reminders, habits, contacts, chat.

One .zip with a manifest. Not included: models, voice cache, browser cache, screenshots, logs — they come back
by themselves. By default a copy is made once a week into Документы\\Jarvis backups and the last 5 are kept.
Restoring makes a safety copy of the current state first, then restarts Jarvis."""
import datetime as dt
import json
import os
import socket
import threading
import time
import zipfile
from pathlib import Path

from .config import DATA

SKIP_DIRS = {"cache", "models", "webview", "screenshots", "backup", "backups", "update", ".deleted", "__pycache__"}
SKIP_EXT = (".log", ".tmp", ".part", ".lock")
VOICES_MAX = 30 * 2**20      # a reference voice for XTTS is worth keeping, a pile of renders is not
PREFIX = "jarvis-backup-"


def default_folder():
    docs = Path.home() / "Documents"
    return (docs if docs.is_dir() else DATA) / "Jarvis backups"


class Backup:
    def __init__(self, core):
        self.core = core
        self.cfg = core.cfg

    def start(self):
        threading.Thread(target=self._auto, daemon=True, name="backup").start()

    def folder(self):
        f = Path(self.cfg.get("backup.folder") or default_folder())
        f.mkdir(parents=True, exist_ok=True)
        return f

    # ---------------------------------------------------------------- making
    def _files(self):
        voices = sum(p.stat().st_size for p in (DATA / "voices").rglob("*") if p.is_file()) if (DATA / "voices").exists() else 0
        for p in DATA.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(DATA)
            if rel.parts[0] in SKIP_DIRS or any(part in SKIP_DIRS for part in rel.parts[:-1]):
                continue
            if p.name.lower().endswith(SKIP_EXT) or ".old." in p.name:
                continue
            if rel.parts[0] == "voices" and voices > VOICES_MAX:
                continue
            yield p, rel.as_posix()

    def make(self, note="", folder=None):
        from .updater import current_version
        dst_dir = Path(folder) if folder else self.folder()
        dst_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        dst = dst_dir / f"{PREFIX}{stamp}{('-' + note) if note else ''}.zip"
        files = list(self._files())
        manifest = {"app": "jarvis", "version": current_version(), "created": dt.datetime.now().isoformat(timespec="seconds"),
                    "pc": socket.gethostname(), "note": note, "files": [r for _, r in files]}
        tmp = dst.with_suffix(".zip.part")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
            for p, rel in files:
                try:
                    z.write(p, "data/" + rel)
                except OSError:
                    pass       # a file being written right now: the next copy will have it
        os.replace(tmp, dst)
        self.cfg.set("backup.last", time.time())
        return dst

    def list(self):
        out = []
        for p in sorted(self.folder().glob(PREFIX + "*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
            out.append({"path": str(p), "name": p.name, "size": p.stat().st_size,
                        "at": dt.datetime.fromtimestamp(p.stat().st_mtime).strftime("%d.%m.%Y %H:%M"),
                        "auto": "-auto" in p.name, "safety": "-before-restore" in p.name})
        return out

    def prune(self):
        keep = int(self.cfg.get("backup.keep", 5) or 5)
        autos = [b for b in self.list() if b["auto"]]
        for b in autos[keep:]:
            try:
                Path(b["path"]).unlink()
            except OSError:
                pass

    def _auto(self):
        time.sleep(120)
        while True:
            try:
                if self.cfg.get("backup.auto", True) and time.time() - float(self.cfg.get("backup.last", 0) or 0) > 7 * 86400:
                    p = self.make("auto")
                    self.prune()
                    self.core.log(f"Резервная копия: {p.name}")
            except Exception as e:
                self.core.log(f"Резервная копия: {e}", "warn")
            time.sleep(3600)

    # ---------------------------------------------------------------- restoring
    @staticmethod
    def inspect(path):
        with zipfile.ZipFile(path) as z:
            try:
                m = json.loads(z.read("manifest.json").decode("utf-8"))
            except KeyError:
                raise ValueError("это не резервная копия Джарвиса: нет manifest.json")
            if m.get("app") != "jarvis":
                raise ValueError("это не резервная копия Джарвиса")
            names = [n for n in z.namelist() if n.startswith("data/")]
        what = {"plugins": sum(1 for n in names if n.startswith("data/plugins/") and n.endswith(".py")),
                "notes": "data/notebook.json" in names, "commands": "data/commands.json" in names,
                "automations": "data/automations.json" in names, "files": len(names)}
        return m | {"what": what}

    def restore(self, path):
        info = self.inspect(path)
        safety = self.make("before-restore")
        root = DATA.resolve()
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if not n.startswith("data/") or n.endswith("/"):
                    continue
                dst = (DATA / n[5:]).resolve()
                if root not in dst.parents:      # "../" in a crafted archive
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                with z.open(n) as src, open(dst, "wb") as out:
                    out.write(src.read())
        return info, safety

    # ---------------------------------------------------------------- UI
    def rpc_backups(self):
        return {"items": self.list(), "folder": str(self.folder()), "auto": self.cfg.get("backup.auto", True),
                "keep": self.cfg.get("backup.keep", 5), "last": self.cfg.get("backup.last", 0)}

    def rpc_backup_make(self, folder=None):
        p = self.make(folder=folder)
        return {"path": str(p), "name": p.name, "size": p.stat().st_size}

    def rpc_backup_inspect(self, path):
        return self.inspect(path)

    def rpc_backup_restore(self, path):
        info, safety = self.restore(path)
        self.core.log(f"Восстановлено из {Path(path).name}; прежнее состояние в {safety.name}")
        threading.Timer(1.5, self.core._restart).start()
        return {"safety": str(safety), "version": info.get("version")}

    def rpc_backup_set(self, auto=None, keep=None, folder=None):
        if auto is not None:
            self.cfg.set("backup.auto", bool(auto))
        if keep is not None:
            self.cfg.set("backup.keep", max(1, int(keep)))
        if folder:
            self.cfg.set("backup.folder", folder)
        return self.rpc_backups()

    def rpc_backup_open_folder(self):
        os.startfile(self.folder())
        return True


# ====================================================================== voice rules
def register(R, core):
    @R(r"^(?:сделай|создай|сохрани)\s+(?:резервную\s+копию|бэкап|бекап|копию\s+настроек)(?:\s+джарвиса|\s+настроек)?$|^забэкапь\s+(?:себя|настройки)$")
    def _make(m, t):
        def go():
            try:
                p = core.backup.make()
                core.say(f"Резервная копия готова: {round(p.stat().st_size / 2**20, 1)} мегабайта, в папке Jarvis backups.")
            except Exception as e:
                core.log(f"Резервная копия: {e}", "error")
                core.say("Не получилось сделать копию. Подробности в журнале.")
        threading.Thread(target=go, daemon=True).start()
        return "Делаю резервную копию."

    @R(r"^(?:открой|покажи)\s+(?:папку\s+)?(?:резервны\w+\s+копи\w+|бэкап\w*|бекап\w*)$")
    def _open(m, t):
        core.backup.rpc_backup_open_folder()
        return True
