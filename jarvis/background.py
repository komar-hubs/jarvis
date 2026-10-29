"""Quiet background work: routine jobs that never touch the mouse or the keyboard, so the user keeps working.

- downloads ("скачай файл по ссылке из буфера") straight to Загрузки over HTTP;
- clearing junk when the disk runs low: old files in %TEMP%, Jarvis's own old screenshots, closed browsers' caches;
- freeing memory when RAM is nearly full: trims the working sets of background programs (they page back in on
  demand, nothing is closed);
- sorting mail (opt-in): newsletters older than two days move from Входящие to a "Newsletters" folder.

Jobs run one at a time in a low-priority thread, show in the interface and speak only when they are done and it
was the user who asked. Automatic ones only leave a quiet note in the event list."""
import collections
import ctypes
import datetime as dt
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

import psutil

from .config import DATA

CREATE_NO_WINDOW = 0x08000000


SORT_DIRS = {
    "Документы": "pdf doc docx odt rtf txt md xls xlsx ods csv ppt pptx odp epub fb2 djvu",
    "Картинки": "jpg jpeg png gif webp bmp heic heif svg tif tiff ico psd",
    "Видео": "mp4 mkv avi mov webm wmv flv m4v",
    "Музыка": "mp3 flac wav ogg m4a aac opus wma",
    "Архивы": "zip rar 7z tar gz bz2 xz tgz iso",
    "Установщики": "exe msi msix appx appinstaller",
    "Торренты": "torrent",
    "Код": "py js ts json html css xml yml yaml ipynb sql bat ps1 sh",
}
EXT_DIR = {e: d for d, exts in SORT_DIRS.items() for e in exts.split()}
SORT_LOG = DATA / "downloads_sort.json"


def human(n):
    for unit, k in (("ГБ", 2**30), ("МБ", 2**20), ("КБ", 2**10)):
        if n >= k:
            return f"{n / k:.1f} {unit}".replace(".0 ", " ")
    return f"{n} байт"


class Background:
    def __init__(self, core):
        self.core = core
        self.cfg = core.cfg
        self.q = collections.deque()
        self.jobs = collections.deque(maxlen=40)
        self._cv = threading.Condition()
        self._last = collections.defaultdict(float)
        self._ram_high = 0
        self.updates = []

    def start(self):
        threading.Thread(target=self._worker, daemon=True, name="background").start()
        threading.Thread(target=self._auto, daemon=True, name="background-auto").start()

    # ---------------------------------------------------------------- queue
    def submit(self, kind, title, fn, user=True):
        job = {"id": uuid.uuid4().hex[:8], "kind": kind, "title": title, "state": "queued", "progress": 0,
               "result": "", "user": user, "at": dt.datetime.now().strftime("%H:%M")}
        job["_fn"] = fn
        self.jobs.appendleft(job)
        with self._cv:
            self.q.append(job)
            self._cv.notify()
        self._emit(job)
        return job

    def _emit(self, job):
        self.core.emit("bg_job", job={k: v for k, v in job.items() if not k.startswith("_")})

    def _worker(self):
        try:  # THREAD_PRIORITY_BELOW_NORMAL: the user's programs come first
            ctypes.windll.kernel32.SetThreadPriority(ctypes.windll.kernel32.GetCurrentThread(), -1)
        except Exception:
            pass
        while True:
            with self._cv:
                while not self.q:
                    self._cv.wait()
                job = self.q.popleft()
            job["state"] = "running"
            self._emit(job)
            try:
                job["result"] = job["_fn"](job) or "готово"
                job["state"] = "done"
            except Exception as e:
                job["state"] = "error"
                job["result"] = str(e)[:200]
                self.core.log(f"Фон: {job['title']}: {e}", "warn")
            job["progress"] = 100
            self._emit(job)
            if job["user"]:
                ok = job["state"] == "done"
                self.core.initiative.propose(f"bg:{job['id']}", (job["result"] if ok else f"Не получилось: {job['result']}"),
                                             priority=2, title="Фоновая задача", cooldown_min=0, kind="background")
            elif job["state"] == "done" and job["result"]:
                self.core.initiative.propose(f"bg:{job['id']}", job["result"], priority=1, title="Сделал в фоне",
                                             cooldown_min=0, kind="background")

    # ---------------------------------------------------------------- automatic triggers
    def _auto(self):
        time.sleep(60)
        while True:
            time.sleep(30)
            if not self.cfg.get("background.enabled", True):
                continue
            try:
                vm = psutil.virtual_memory()
                self._ram_high = self._ram_high + 1 if vm.percent >= 90 else 0
                if self._ram_high >= 4 and time.time() - self._last["ram"] > 15 * 60:
                    self._last["ram"] = time.time()
                    self.submit("ram", "Освобождаю память", self.trim_memory, user=False)
                free = psutil.disk_usage(os.path.splitdrive(str(Path.home()))[0] + "\\").free
                if free < 8 * 2**30 and time.time() - self._last["disk"] > 6 * 3600:
                    self._last["disk"] = time.time()
                    self.submit("clean", "Чищу временные файлы", self.clean_junk, user=False)
                if self.cfg.get("background.sort_downloads", False) and time.time() - self._last["sort"] > 6 * 3600:
                    self._last["sort"] = time.time()
                    self.submit("sort", "Раскладываю Загрузки", self.sort_downloads, user=False)
                if self.cfg.get("background.mail_sort", False) and time.time() - self._last["mail"] > 30 * 60 \
                        and self.core.mail.accounts():
                    self._last["mail"] = time.time()
                    self.submit("mail", "Разбираю почту", self.sort_mail, user=False)
            except Exception as e:
                self.core.log(f"Фон (авто): {e}", "warn")

    # ---------------------------------------------------------------- jobs
    def download(self, url, name=None):
        def run(job):
            import requests
            folder = Path.home() / "Downloads"
            fn = name or unquote(Path(urlparse(url).path).name) or "download"
            with requests.get(url, stream=True, timeout=(15, 60), headers={"User-Agent": "Mozilla/5.0 JARVIS"}) as r:
                r.raise_for_status()
                cd = r.headers.get("content-disposition", "")
                m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", cd)
                if m and not name:
                    fn = unquote(m.group(1))
                fn = re.sub(r'[<>:"/\\|?*]', "_", fn)[:150] or "download"
                dst = folder / fn
                i = 2
                while dst.exists():
                    dst = folder / f"{Path(fn).stem} ({i}){Path(fn).suffix}"
                    i += 1
                total = int(r.headers.get("content-length") or 0)
                part = dst.with_name(dst.name + ".part")
                got, last = 0, 0
                with open(part, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        f.write(chunk)
                        got += len(chunk)
                        if total and time.time() - last > 0.7:
                            last = time.time()
                            job["progress"] = int(got * 100 / total)
                            self._emit(job)
                os.replace(part, dst)
            job["path"] = str(dst)
            return f"Скачал {dst.name} ({human(got)}) в Загрузки."
        return self.submit("download", f"Скачиваю {url[:60]}", run)

    def clean_junk(self, job=None):
        """Old temp files, Jarvis's old screenshots and caches of browsers that are closed. Never user files."""
        freed, n = 0, 0
        cut = time.time() - 2 * 86400
        targets = [Path(os.environ.get("TEMP", "")), DATA / "screenshots", DATA / "update"]
        running = {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])}
        la = Path(os.environ.get("LOCALAPPDATA", ""))
        browsers = {"chrome.exe": la / "Google/Chrome/User Data", "msedge.exe": la / "Microsoft/Edge/User Data",
                    "browser.exe": la / "Yandex/YandexBrowser/User Data"}
        for exe, root in browsers.items():
            if exe not in running and root.exists():
                for prof in root.glob("*"):
                    for sub in ("Cache/Cache_Data", "Code Cache/js", "GPUCache"):
                        if (prof / sub).exists():
                            targets.append(prof / sub)
        for root in targets:
            if not root or not root.exists():
                continue
            shots = root == DATA / "screenshots"
            for p in list(root.rglob("*")):
                try:
                    if not p.is_file():
                        continue
                    st = p.stat()
                    if shots and (time.time() - st.st_mtime < 14 * 86400 or p.name == "look.png"):
                        continue
                    if "Cache" not in str(root) and st.st_mtime > cut:
                        continue
                    p.unlink()
                    freed += st.st_size
                    n += 1
                except Exception:
                    continue  # in use or protected: skip
        return f"Почистил временные файлы: {n}, освободил {human(freed)}." if n else "Мусора почти нет."

    def trim_memory(self, job=None):
        """EmptyWorkingSet for background programs of this user: their pages go to the file and come back when
        needed. Nothing is closed; the foreground app and Jarvis are left alone."""
        import win32gui
        import win32process
        before = psutil.virtual_memory().available
        try:
            _, fg_pid = win32process.GetWindowThreadProcessId(win32gui.GetForegroundWindow())
        except Exception:
            fg_pid = 0
        me = os.getpid()
        k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
        n = 0
        for p in psutil.process_iter(["pid", "name", "memory_info"]):
            pid = p.info["pid"]
            if pid in (0, 4, me, fg_pid) or (p.info["name"] or "").lower() in ("audiodg.exe", "dwm.exe", "csrss.exe"):
                continue
            try:   # a game behind an alt-tab would stutter for seconds when the user comes back to it
                if self.core.extras.is_game(p.info["name"] or ""):
                    continue
            except Exception:
                pass
            try:
                if p.info["memory_info"].rss < 80 * 2**20:
                    continue
            except Exception:
                continue
            h = k32.OpenProcess(0x0400 | 0x0100, False, pid)  # QUERY_INFORMATION | SET_QUOTA
            if h:
                if psapi.EmptyWorkingSet(h):
                    n += 1
                k32.CloseHandle(h)
        time.sleep(1.5)
        gained = psutil.virtual_memory().available - before
        return f"Освободил около {human(max(0, gained))} оперативной памяти ({n} программ в фоне)." if gained > 50 * 2**20 \
            else "Оперативная память и так в порядке."

    def sort_mail(self, job=None):
        from .mail import Mailbox
        moved = 0
        for a in self.core.mail.accounts():
            mb = Mailbox(a)
            hs = mb.headers(limit=200)
            old = dt.datetime.now() - dt.timedelta(days=2)
            uids = [h["uid"] for h in hs if h["bulk"] and h["date"] and dt.datetime.fromisoformat(h["date"]) < old]
            if not uids:
                continue
            M = mb.imap()
            try:
                M.create("Newsletters")
            except Exception:
                pass
            try:
                M.select("INBOX")
                for i in range(0, len(uids), 50):
                    chunk = ",".join(uids[i:i + 50])
                    typ, _ = M.uid("copy", chunk, "Newsletters")
                    if typ == "OK":
                        M.uid("store", chunk, "+FLAGS", "(\\Deleted)")
                        moved += len(uids[i:i + 50])
                M.expunge()
            finally:
                try:
                    M.logout()
                except Exception:
                    pass
            self.core.mail.cache.pop(a["id"], None)
        self.core.emit("mail")
        return f"Разобрал почту: {moved} рассылок перенёс в папку Newsletters." if moved else ""

    # ---------------------------------------------------------------- Downloads
    def sort_downloads(self, job=None):
        """Top-level files of Загрузки into subfolders by type. Files younger than 10 minutes (still downloading or
        just opened) and unknown types stay. Every move is logged, so "верни загрузки" puts them back."""
        import json
        folder = Path.home() / "Downloads"
        cut = time.time() - 600
        moves = []
        for p in list(folder.iterdir()):
            try:
                if not p.is_file() or p.suffix.lower() in (".part", ".crdownload", ".tmp", ".partial", ".download") \
                        or p.name.lower() == "desktop.ini" or p.stat().st_mtime > cut:
                    continue
                sub = EXT_DIR.get(p.suffix.lower().lstrip("."))
                if not sub:
                    continue
                dst_dir = folder / sub
                dst_dir.mkdir(exist_ok=True)
                dst = dst_dir / p.name
                i = 2
                while dst.exists():
                    dst = dst_dir / f"{p.stem} ({i}){p.suffix}"
                    i += 1
                os.replace(p, dst)
                moves.append([str(p), str(dst)])
            except OSError:
                continue  # open in another program: next time
        if not moves:
            return "В Загрузках уже порядок." if job and job.get("user") else ""
        try:
            SORT_LOG.write_text(json.dumps({"at": time.time(), "moves": moves}, ensure_ascii=False), "utf-8")
        except Exception:
            pass
        by = collections.Counter(Path(d).parent.name for _, d in moves)
        return f"Разложил Загрузки: {len(moves)} файлов — " + ", ".join(f"{k.lower()} {v}" for k, v in by.most_common()) + "."

    def unsort_downloads(self, job=None):
        import json
        if not SORT_LOG.exists():
            return "Нечего возвращать: Загрузки я ещё не раскладывал."
        moves = json.loads(SORT_LOG.read_text("utf-8")).get("moves", [])
        back = 0
        for src, dst in reversed(moves):
            try:
                if Path(dst).exists() and not Path(src).exists():
                    os.replace(dst, src)
                    back += 1
            except OSError:
                continue
        SORT_LOG.unlink(missing_ok=True)
        for d in SORT_DIRS:  # remove the folders this created, if now empty
            try:
                (Path.home() / "Downloads" / d).rmdir()
            except OSError:
                pass
        return f"Вернул {back} файлов обратно в Загрузки."

    def old_installers(self, days=30):
        folder = Path.home() / "Downloads"
        cut = time.time() - days * 86400
        out = []
        for p in list(folder.glob("*")) + list((folder / "Установщики").glob("*")):
            try:
                if p.is_file() and p.suffix.lower() in (".exe", ".msi", ".msix") and p.stat().st_mtime < cut:
                    out.append(p)
            except OSError:
                pass
        return out

    # ---------------------------------------------------------------- program updates (winget)
    @staticmethod
    def _winget(args, timeout=120):
        import subprocess
        r = subprocess.run(["winget", *args, "--accept-source-agreements", "--disable-interactivity"],
                           capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
        out = r.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
        out = re.sub(r"[▀-▟█▒░]+", "", out)                  # progress bars
        return r.returncode, "\n".join(l.split("\r")[-1] for l in out.split("\n"))  # spinners redraw with \r

    def upgradable(self):
        """[(name, id, version, available)] from `winget upgrade`; the table is cut by its header's column starts."""
        code, out = self._winget(["upgrade", "--include-unknown"], timeout=180)
        lines = out.splitlines()
        hi = next((i for i, l in enumerate(lines) if re.match(r"^-{10,}", l.strip())), None)
        if hi is None or hi == 0:
            return []
        head = lines[hi - 1]
        cols = [m.start() for m in re.finditer(r"\S+", head)]
        if len(cols) < 4:
            return []
        rows = []
        for l in lines[hi + 1:]:
            if len(l) < cols[3]:          # the "N upgrades available" footer and blank lines
                continue
            name, pid = l[:cols[1]].strip(), l[cols[1]:cols[2]].strip()
            ver, avail = l[cols[2]:cols[3]].strip(), l[cols[3]:cols[4] if len(cols) > 4 else None].strip()
            if pid and avail and re.match(r"^[\w.+-]+$", pid):
                rows.append((name, pid, ver, avail))
        return rows

    def check_updates(self, job=None):
        rows = self.upgradable()
        self.updates = rows
        if not rows:
            return "Все программы обновлены."
        names = ", ".join(r[0] for r in rows[:6]) + (f" и ещё {len(rows) - 6}" if len(rows) > 6 else "")
        return f"Можно обновить {len(rows)}: {names}. Скажите «обнови все программы»."

    def upgrade_all(self, job=None, ids=None):
        rows = [r for r in (getattr(self, "updates", None) or self.upgradable()) if not ids or r[1] in ids]
        if not rows:
            return "Обновлять нечего: всё свежее."
        ok, fail = [], []
        for i, (name, pid, _, avail) in enumerate(rows):
            if job:
                job["title"] = f"Обновляю {name} ({i + 1}/{len(rows)})"
                job["progress"] = int(i * 100 / len(rows)) or 3
                self._emit(job)
            try:
                code, out = self._winget(["upgrade", "--id", pid, "--exact", "--silent", "--accept-package-agreements"], timeout=1800)
                (ok if code == 0 else fail).append(name)
            except Exception:
                fail.append(name)
        self.updates = []
        return (f"Обновил {len(ok)}: {', '.join(ok[:6])}." if ok else "") + (f" Не получилось: {', '.join(fail[:5])}." if fail else "")

    # ---------------------------------------------------------------- local AI (Ollama) in one click
    OLLAMA = "http://localhost:11434"

    def _ollama_up(self):
        import requests
        try:
            return requests.get(self.OLLAMA + "/api/tags", timeout=2).ok
        except Exception:
            return False

    def ollama_state(self, start=True, wait=25):
        """"up" | "not_installed" | "failed". Installed but not running (after a reboot, in a virtual machine, a closed tray
        icon) is the usual reason for "Max retries exceeded … 11434": the server is started here, quietly, and awaited."""
        import subprocess
        if self._ollama_up():
            return "up"
        exe = self._find_ollama()
        if not exe:
            return "not_installed"
        if start:
            if time.time() - getattr(self, "_ollama_started", 0) > 60:
                self._ollama_started = time.time()
                try:
                    subprocess.Popen([str(exe), "serve"], creationflags=CREATE_NO_WINDOW)
                except Exception:
                    return "failed"
            for _ in range(int(wait * 2)):
                time.sleep(0.5)
                if self._ollama_up():
                    return "up"
        return "failed"

    def ollama_models(self):
        """Names of the models Ollama has, or None if it does not answer."""
        import requests
        try:
            return [m["name"] for m in requests.get(self.OLLAMA + "/api/tags", timeout=3).json().get("models", [])]
        except Exception:
            return None

    def ollama_problem(self, model):
        """A plain-words reason why the local AI cannot answer now (and what to press), or "" when it can."""
        st = self.ollama_state()
        if st == "not_installed":
            return ("Ollama не установлена на этом ПК. Нажмите ниже «Бесплатный локальный ИИ в один клик»: Джарвис поставит её сам "
                    "и подберёт модель под ваш ПК.")
        if st == "failed":
            from . import system as S
            vm = S.virtual_machine()
            return ("Ollama установлена, но не запускается. Перезагрузите ПК; если не поможет, антивирус или брандмауэр может блокировать "
                    "localhost:11434" + (f". Это виртуальная машина ({vm}): нужны инструкции процессора AVX" if vm else "") + ".")
        names = self.ollama_models()
        model = (model or "").strip()
        if names is not None and model and model not in names and f"{model}:latest" not in names:
            rec = self.ollama_model_for_pc()
            have = f" Сейчас скачано: {', '.join(names[:3])}." if names else ""
            return (f"Ollama работает, но модели «{model}» на этом ПК нет.{have} Нажмите «Бесплатный локальный ИИ в один клик»: "
                    f"Джарвис скачает {rec}, она подходит вашему ПК.")
        return ""

    def ollama_setup(self, job, model):
        """Free AI on this PC, no key: install Ollama if needed (winget, else the official installer), start it, pull the
        model, switch Jarvis to it. Every failure says what exactly went wrong and what to do."""
        import json
        import subprocess
        import requests
        gb = self.OLLAMA_GB.get(model.split(":")[-1], 4.0)
        installed = bool(self._ollama_up() or self._find_ollama())
        need = gb + (0 if installed else 1.8)            # the model, and the program itself (about 1.5 GB)
        free = shutil.disk_usage(Path.home()).free / 2**30
        if free < need + 0.5:
            raise RuntimeError(f"мало места на диске: свободно {free:.1f} ГБ, нужно около {need + 0.5:.1f} ГБ. Освободите место и нажмите ещё раз")
        if not self._ollama_up():
            if not self._find_ollama():
                self._ollama_install(job)
            for i in range(30):                       # the installer starts it; if not, start the server ourselves
                if self._ollama_up():
                    break
                if i == 5:
                    subprocess.Popen([str(self._find_ollama() or "ollama"), "serve"], creationflags=CREATE_NO_WINDOW)
                time.sleep(2)
            if not self._ollama_up():
                from . import system as S
                vm = S.virtual_machine()
                raise RuntimeError("Ollama установлена, но не запустилась: перезагрузите ПК и нажмите ещё раз "
                                   "(если не поможет, антивирус или брандмауэр блокирует localhost:11434)"
                                   + (f". Это виртуальная машина ({vm}): у её процессора может не быть нужных инструкций (AVX), "
                                      "включите их в настройках машины или возьмите бесплатный ключ Groq / Gemini (Настройки → ИИ)" if vm else ""))
        job["title"] = f"Скачиваю модель {model}"
        job["progress"] = 0
        self._emit(job)
        try:
            with requests.post(self.OLLAMA + "/api/pull", json={"model": model, "stream": True}, stream=True, timeout=(15, 600)) as r:
                r.raise_for_status()
                last = 0
                for line in r.iter_lines():
                    if not line:
                        continue
                    st = json.loads(line)
                    if st.get("error"):
                        raise RuntimeError(st["error"])
                    if st.get("total") and time.time() - last > 1:
                        last = time.time()
                        job["progress"] = int(st.get("completed", 0) * 100 / st["total"])
                        self._emit(job)
        except requests.exceptions.RequestException as e:
            raise RuntimeError(f"не удалось скачать модель {model}: нет связи с registry.ollama.ai (интернет, VPN или прокси). "
                               f"Нажмите ещё раз, скачивание продолжится с того же места. {str(e)[:120]}")
        self.cfg.update({"ai": {"provider": "ollama", "providers": {"ollama": {"model": model}}}})
        self.core.emit("config")
        small = " Это самая маленькая модель: для слабого ПК умнее подойдёт бесплатный ключ Groq или Gemini (Настройки → ИИ)." if model.endswith(("0.8b", "2b")) else ""
        from . import system as S
        vm = S.virtual_machine()
        slow = f" Это виртуальная машина ({vm}) без видеокарты: ответы будут медленными." if vm else ""
        return f"Готово: локальный ИИ {model} работает без интернета и ключей." + small + slow

    # download size on disk, GB (ollama.com/library/qwen3.5)
    OLLAMA_GB = {"0.8b": 1.3, "2b": 3.1, "4b": 4.0, "9b": 7.6}

    @staticmethod
    def _find_ollama():
        for p in (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
                  Path(os.environ.get("ProgramFiles", "")) / "Ollama" / "ollama.exe"):
            if p.exists():
                return p
        w = shutil.which("ollama")
        return Path(w) if w else None

    def _ollama_install(self, job):
        """winget first; when it is missing (a Windows without App Installer) or fails, the official installer from
        ollama.com is downloaded and run quietly. The reason of the winget failure goes into the final error."""
        import subprocess
        job["title"] = "Устанавливаю Ollama"
        job["progress"] = 0
        self._emit(job)
        why = ""
        try:
            code, out = self._winget(["install", "--id", "Ollama.Ollama", "--exact", "--silent", "--accept-package-agreements"], timeout=1800)
            if code == 0 or self._find_ollama():
                return
            why = ([l.strip() for l in out.splitlines() if l.strip()] or [""])[-1][:140]
        except FileNotFoundError:
            why = "winget не найден (нет «Установщика приложений»)"
        except Exception as e:
            why = str(e)[:140]
        job["title"] = "Скачиваю установщик Ollama с ollama.com"
        self._emit(job)
        setup = Path(os.environ.get("TEMP") or ".") / "OllamaSetup.exe"
        try:
            self._fetch("https://ollama.com/download/OllamaSetup.exe", setup, job)
        except Exception as e:
            raise RuntimeError(f"не удалось установить Ollama. winget: {why or 'не сработал'}. Скачать установщик с ollama.com тоже не вышло "
                               f"({str(e)[:100]}). Поставьте Ollama вручную с ollama.com/download и нажмите ещё раз")
        job["title"] = "Устанавливаю Ollama (1–3 минуты)"
        job["progress"] = 100
        self._emit(job)
        try:
            subprocess.run([str(setup), "/SILENT", "/NORESTART"], timeout=900, creationflags=CREATE_NO_WINDOW)
        finally:
            setup.unlink(missing_ok=True)
        if not self._find_ollama():
            raise RuntimeError(f"установщик Ollama отработал, но программа не появилась (winget: {why or 'не сработал'}). "
                               "Возможно, его остановил антивирус: поставьте Ollama вручную с ollama.com/download")

    def _fetch(self, url, dest, job):
        import requests
        with requests.get(url, stream=True, timeout=(15, 60), allow_redirects=True) as r:
            r.raise_for_status()
            total, got, last = int(r.headers.get("content-length") or 0), 0, 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
                    got += len(chunk)
                    if total and time.time() - last > 1:
                        last = time.time()
                        job["progress"] = int(got * 100 / total)
                        self._emit(job)
        if got < 50 * 2**20:
            raise RuntimeError(f"скачалось только {got // 1024} КБ: похоже, это не установщик")

    def rpc_ollama_setup(self, model=""):
        model = (model or "").strip() or self.ollama_model_for_pc()
        self.submit("ollama", "Готовлю локальный ИИ", lambda job: self.ollama_setup(job, model))
        return model

    @staticmethod
    def ollama_model_for_pc():
        """A model the PC can run comfortably: 9B with 9+ GB of video memory, 4B with 3.5+ GB; without a video card it
        depends on RAM (4B: 14+ GB, 2B: 7.5+ GB, else the 0.8B — on 6 GB, with Windows and Jarvis in it, nothing bigger fits)."""
        import subprocess
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"], capture_output=True,
                                 text=True, timeout=8, creationflags=CREATE_NO_WINDOW).stdout
            vram = max(int(x) for x in out.split() if x.strip().isdigit())
        except Exception:
            vram = 0
        if vram >= 9000:
            return "qwen3.5:9b"
        if vram >= 3500:
            return "qwen3.5:4b"
        ram = psutil.virtual_memory().total / 2**30
        return "qwen3.5:4b" if ram >= 14 else "qwen3.5:2b" if ram >= 7.5 else "qwen3.5:0.8b"

    def rpc_updates_check(self):
        rows = self.upgradable()
        self.updates = rows
        return [{"name": r[0], "id": r[1], "version": r[2], "available": r[3]} for r in rows]

    def rpc_updates_install(self, ids=None):
        self.submit("upgrade", "Обновляю программы", lambda job: self.upgrade_all(job, ids))
        return True

    def rpc_downloads_sort(self, undo=False):
        self.submit("sort", "Возвращаю Загрузки" if undo else "Раскладываю Загрузки", self.unsort_downloads if undo else self.sort_downloads)
        return True

    # ---------------------------------------------------------------- UI / AI
    def rpc_bg_jobs(self):
        return [{k: v for k, v in j.items() if not k.startswith("_")} for j in self.jobs]

    def tool_download(self, a):
        url = a.get("url") or ""
        if not re.match(r"^https?://", url):
            raise ValueError("нужна ссылка http(s)")
        self.download(url, a.get("name") or None)
        return "скачиваю в фоне"

    def tool_cleanup(self, a):
        what = (a.get("what") or "temp").lower()
        if what.startswith("ram") or what.startswith("mem"):
            self.submit("ram", "Освобождаю память", self.trim_memory)
        elif what.startswith("mail"):
            self.submit("mail", "Разбираю почту", self.sort_mail)
        else:
            self.submit("clean", "Чищу временные файлы", self.clean_junk)
        return "делаю в фоне"


# ====================================================================== voice rules
def register(R, core):
    B = lambda: core.background
    from . import system as S

    @R(r"^(?:скачай|загрузи|скачать)\s+(?:мне\s+)?(?:файл\s+)?(?:по\s+)?(?:ссылк\w+\s+)?(?:из\s+буфера(?:\s+обмена)?|из\s+буфера|это|по\s+ссылке)$"
       r"|^(?:скачай|загрузи)\s+(?:файл\s+)?(https?://\S+)$")
    def _dl(m, t):
        url = m.group(1) or (S.clipboard_get() or "").strip()
        if not re.match(r"^https?://\S+$", url):
            return "В буфере нет ссылки, сэр. Скопируйте ссылку на файл и повторите."
        B().download(url)
        return "Скачиваю в фоне, сэр. Скажу, когда будет готово."

    @R(r"^(?:почисти|очисти|удали|убери)\s+(?:временные\s+файлы|кэш\w*|кеш\w*|мусор|временн\w+|темп\w*)(?:\s+на\s+компьютере|\s+в\s+системе)?$|^(?:почисти|очисти)\s+(?:компьютер|комп|систему|диск)$")
    def _clean(m, t):
        B().submit("clean", "Чищу временные файлы", B().clean_junk)
        return "Чищу в фоне. Ничего вашего не трону."

    @R(r"^(?:освободи|почисти|очисти|разгрузи)\s+(?:оперативн\w+\s+память|оперативку|память|озу|ram)$")
    def _ram(m, t):
        B().submit("ram", "Освобождаю память", B().trim_memory)
        return "Освобождаю память в фоне."

    @R(r"^(?:разбери|отсортируй|рассортируй|почисти|наведи\s+порядок\s+в)\s+(?:мою\s+)?почт\w*$")
    def _mail(m, t):
        if not core.mail.accounts():
            return "Почта не подключена, сэр."
        B().submit("mail", "Разбираю почту", B().sort_mail)
        return "Разбираю почту в фоне: старые рассылки уберу в папку Newsletters."

    @R(r"^(?:всегда\s+)?(?:сортируй|разбирай)\s+почту(?:\s+сам|\s+автоматически)?$")
    def _mail_auto(m, t):
        core.cfg.set("background.mail_sort", True)
        return "Хорошо: раз в полчаса буду убирать старые рассылки в папку Newsletters."

    DL = r"(?:загрузки|загрузок|папку\s+загрузки|скачанное|скачанные\s+файлы|папку\s+загрузок)"

    @R(rf"^(?:разбери|разложи|отсортируй|рассортируй|наведи\s+порядок\s+в|почисти)\s+{DL}$")
    def _sort(m, t):
        B().submit("sort", "Раскладываю Загрузки", B().sort_downloads)
        return "Раскладываю Загрузки по папкам: документы, картинки, видео, архивы, установщики."

    @R(rf"^(?:всегда|сам|автоматически)\s+(?:разбирай|раскладывай|сортируй)\s+{DL}$|^(?:разбирай|раскладывай|сортируй)\s+{DL}\s+(?:сам|автоматически|всегда)$")
    def _sort_auto(m, t):
        core.cfg.set("background.sort_downloads", True)
        return "Хорошо: раз в несколько часов буду раскладывать Загрузки. Свежие файлы не трогаю."

    @R(rf"^(?:верни|разложи)\s+{DL}\s+(?:обратно|как\s+было)$|^отмени\s+сортировку(?:\s+{DL})?$")
    def _unsort(m, t):
        B().submit("sort", "Возвращаю Загрузки", B().unsort_downloads)
        return "Возвращаю файлы как было."

    @R(r"^(?:удали|убери|почисти)\s+(?:старые\s+)?установщики$")
    def _installers(m, t):
        old = B().old_installers()
        if not old:
            return "Старых установщиков нет."
        size = sum(p.stat().st_size for p in old)

        def go():
            S.recycle(old)
            core.say(f"Убрал в корзину {len(old)} установщиков, {human(size)}.")
        core.ask_confirm(go, f"Нашёл {len(old)} установщиков старше месяца, {human(size)}. Убрать их в корзину?")
        return True

    @R(r"^(?:какие\s+программы\s+(?:можно\s+)?обновить|проверь\s+обновления\s+(?:программ|приложений)|есть\s+обновления\s+(?:программ|приложений))$")
    def _upd_check(m, t):
        B().submit("upgrade", "Проверяю обновления программ", B().check_updates)
        return "Проверяю через winget, это займёт до минуты."

    @R(r"^(?:обнови|обнови\s+все|обнови\s+мне)\s+(?:все\s+)?(?:программы|приложения|софт)$|^обнови\s+всё$")
    def _upd_all(m, t):
        def go():
            B().submit("upgrade", "Обновляю программы", B().upgrade_all)
            core.say("Обновляю в фоне. Если Windows попросит разрешение администратора, подтвердите.")
        core.ask_confirm(go, "Обновить все программы через winget? Некоторые закроются на время установки.")
        return True

    @R(r"^(?:что\s+ты\s+делаешь\s+в\s+фоне|фоновые\s+задачи|что\s+в\s+фоне|что\s+ты\s+сейчас\s+делаешь)$")
    def _list(m, t):
        js = [j for j in B().jobs if j["state"] in ("queued", "running")]
        if not js:
            done = [j for j in B().jobs if j["state"] == "done"][:2]
            return "В фоне ничего не идёт." + (" Последнее: " + "; ".join(j["result"] for j in done if j["result"]) + "." if done else "")
        return "В фоне: " + "; ".join(f"{j['title']}" + (f", {j['progress']}%" if j["progress"] else "") for j in js) + "."
