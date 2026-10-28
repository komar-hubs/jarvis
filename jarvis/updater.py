"""One-button updates. The author publishes jarvis_share.zip as a GitHub release (tools/publish_update.py);
every copy checks the latest release, downloads it and replaces its own code in place. Personal data, voice
models and the virtual environments are never touched, and the previous code is kept as a backup zip."""
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

import requests

from .config import ROOT

KEEP = {".venv", ".xtts-venv", "data", "__pycache__", ".git"}  # an update never writes into these
UA = {"User-Agent": "JARVIS-updater"}
CREATE_NO_WINDOW = 0x08000000
NEW_GROUP = 0x00000200  # CREATE_NEW_PROCESS_GROUP (DETACHED_PROCESS made powershell silently do nothing)


def current_version():
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip() or "dev"
    except OSError:
        return "dev"


def version_human(v):
    """'2026.10.12.1932' -> 'от 12 октября, 19:32' (speakable; the dotted number came out as gibberish)."""
    m = re.match(r"^(\d{4})\.(\d{1,2})\.(\d{1,2})\.(\d{2})(\d{2})$", v or "")
    if not m:
        return v or "dev"
    months = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
              "ноября", "декабря"]
    return f"от {int(m.group(3))} {months[int(m.group(2)) - 1]}, {m.group(4)}:{m.group(5)}"


def _vkey(v):
    return [int(x) for x in re.findall(r"\d+", v or "")]


class Updater:
    def __init__(self, cfg, log=print, emit=lambda *a, **k: None):
        self.cfg = cfg
        self.log = log
        self.emit = emit
        self.info = None
        self.busy = False

    def _state(self, status, **kw):
        self.emit("update", status=status, **kw)

    def check(self):
        """Latest release info: {current, latest, available, notes, url, size}."""
        repo = self.cfg.get("update.github") or ""
        if not repo:
            raise RuntimeError("источник обновлений не настроен")
        # release asset downloads are plain file links: unlike api.github.com (60 requests an hour per IP,
        # often used up behind a shared VPN exit) they have no rate limit
        # ?t= skips GitHub's cached "latest" redirect, which kept pointing at the previous release for minutes
        r = requests.get(f"https://github.com/{repo}/releases/latest/download/version.json?t={int(time.time())}",
                         headers=UA, timeout=15)
        if r.status_code == 404:
            raise RuntimeError("обновлений пока не публиковали")
        r.raise_for_status()
        j = r.json()
        cur, latest = current_version(), str(j.get("version") or "")
        if not latest:
            raise RuntimeError("в последнем релизе нет номера версии")
        self.info = {"current": cur, "latest": latest, "notes": str(j.get("notes") or "").strip()[:3000],
                     "url": f"https://github.com/{repo}/releases/download/v{latest}/jarvis_share.zip",
                     "size": int(j.get("size") or 0),
                     # a copy without VERSION is the author's working tree: never overwrite it by itself
                     "available": cur != "dev" and _vkey(latest) > _vkey(cur)}
        return self.info

    def apply_async(self, on_done):
        if self.busy:
            return False
        self.busy = True

        def go():
            try:
                info = self.info if self.info and self.info.get("available") else self.check()
                if not info.get("available"):
                    self._state("latest", **info)
                    on_done(False, "уже последняя версия")
                    return
                self._apply(info)
                on_done(True, info["latest"])
            except Exception as e:
                self.log(f"Обновление: {e}", "error")
                self._state("error", error=str(e))
                on_done(False, str(e))
            finally:
                self.busy = False
        threading.Thread(target=go, daemon=True, name="update").start()
        return True

    def _apply(self, info):
        work = ROOT / "data" / "update"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        zpath = work / "update.zip"
        with requests.get(info["url"], headers=UA, stream=True, timeout=(15, 60)) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or info.get("size") or 0)
            got, last = 0, -1
            with open(zpath, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
                    got += len(chunk)
                    pct = int(got * 100 / total) if total else 0
                    if pct != last:
                        last = pct
                        self._state("downloading", progress=pct)
        self._state("installing", progress=100)
        with zipfile.ZipFile(zpath) as z:
            names = z.namelist()
            prefix = "jarvis/" if names and all(n.startswith("jarvis/") for n in names) else ""
            if prefix + "run.py" not in names or prefix + "jarvis/core.py" not in names:
                raise RuntimeError("архив обновления не похож на Джарвиса")
            z.extractall(work / "new")  # zipfile strips absolute paths and ".." parts
        src = work / "new" / prefix.rstrip("/") if prefix else work / "new"

        self._backup()
        req_old = self._read(ROOT / "requirements.txt")
        n = 0
        for p in src.rglob("*"):
            rel = p.relative_to(src)
            if p.is_dir() or rel.parts[0] in KEEP:
                continue
            dst = ROOT / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)  # .py files are not locked while Python runs them
            n += 1
        self.log(f"Обновление {info['current']} → {info['latest']}: заменено файлов {n}")
        if self._read(ROOT / "requirements.txt") != req_old:
            self._state("deps")
            self._install_requirements()
        shutil.rmtree(work, ignore_errors=True)
        self._state("done", latest=info["latest"])

    @staticmethod
    def _read(p):
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _backup(self):
        """Current code (without data and environments) -> data/backup/code_<version>.zip, last 3 kept."""
        bdir = ROOT / "data" / "backup"
        bdir.mkdir(parents=True, exist_ok=True)
        out = bdir / f"code_{current_version()}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for p in ROOT.rglob("*"):
                rel = p.relative_to(ROOT)
                if p.is_file() and rel.parts[0] not in KEEP and p.suffix != ".zip":
                    z.write(p, rel)
        for old in sorted(bdir.glob("code_*.zip"), key=lambda p: p.stat().st_mtime)[:-3]:
            old.unlink(missing_ok=True)

    def _install_requirements(self):
        py = ROOT / ".venv" / "Scripts" / "python.exe"
        if not py.exists():
            py = Path(sys.executable)
        r = subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-r",
                            str(ROOT / "requirements.txt")], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", creationflags=CREATE_NO_WINDOW, timeout=1800)
        if r.returncode != 0:
            self.log("pip после обновления: " + (r.stderr or r.stdout)[-600:], "warn")

    @staticmethod
    def restart():
        """Start Jarvis.bat again as soon as this process has exited (the new copy must not see the old one)."""
        q = lambda s: str(s).replace("'", "''")
        ps = (f"Wait-Process -Id {os.getpid()} -Timeout 40 -ErrorAction SilentlyContinue; "
              f"Start-Process -FilePath '{q(ROOT / 'Jarvis.bat')}' -WorkingDirectory '{q(ROOT)}' -WindowStyle Hidden")
        argv = ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps]
        try:  # leave a job object if Jarvis runs inside one, so the helper outlives this process
            subprocess.Popen(argv, creationflags=CREATE_NO_WINDOW | NEW_GROUP | 0x01000000, close_fds=True)
        except OSError:
            subprocess.Popen(argv, creationflags=CREATE_NO_WINDOW | NEW_GROUP, close_fds=True)
