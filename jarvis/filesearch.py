"""Search inside documents by meaning: "найди файл про проект с роботом", "в каком файле я писал про бюджет".

- The text of documents in the chosen folders (Рабочий стол, Документы, Загрузки by default) is indexed on this PC:
  txt/md/csv/код, docx/pptx/xlsx/odt (read straight from the zip), pdf (pypdf), rtf. Pieces of ~800 letters go into
  SQLite FTS5 as normal forms of the words (pymorphy3): "роботов", "роботу" and "робот" are one word, and a prefix
  finds "робототехника" too.
- «Умный поиск» (optional, ~135 MB, downloaded on request): the multilingual e5-small model (ONNX, int8) turns every
  piece into a meaning vector, so "про деньги на лето" finds "бюджет отпуска" without a common word. Both rankings
  are merged (reciprocal rank fusion).
- Indexing runs in the background slowly (pauses in games), checks changes every 30 minutes. The index is
  data/files.db; nothing leaves the PC."""
import ctypes
import os
import re
import sqlite3
import threading
import time
import zipfile
from pathlib import Path

import numpy as np

from .config import DATA

TEXT_EXT = {".txt", ".md", ".csv", ".py", ".js", ".ts", ".html", ".htm", ".xml", ".json", ".ini", ".cfg", ".tex",
            ".rtf", ".docx", ".pptx", ".xlsx", ".odt", ".odp", ".ods", ".pdf", ".srt"}
KINDS = {"презентаци": {".pptx", ".odp", ".pdf"}, "таблиц": {".xlsx", ".ods", ".csv"},
         "документ": {".docx", ".odt", ".pdf", ".txt", ".rtf", ".md"}, "ворд": {".docx", ".odt", ".rtf"},
         "pdf": {".pdf"}, "пдф": {".pdf"}, "текст": {".txt", ".md", ".docx", ".rtf"}, "код": {".py", ".js", ".ts", ".html"},
         "доклад": {".docx", ".pptx", ".pdf", ".odt"}, "реферат": {".docx", ".pdf", ".odt"}, "конспект": {".docx", ".txt", ".md", ".pdf"}}
SKIP_DIRS = {"node_modules", "__pycache__", ".venv", "venv", "site-packages", ".git", "$recycle.bin", "appdata",
             "program files", "program files (x86)", "windows", ".idea", ".vs", "build", "cache", "temp"}
STOP = {"про", "о", "об", "обо", "где", "файл", "документ", "в", "во", "который", "которой", "котором", "мой", "моя",
        "мое", "мои", "что", "быть", "есть", "на", "с", "со", "и", "для", "это", "этот", "там", "тот", "я", "писать",
        "записывать", "сохранять", "найти", "поискать", "по", "из", "у", "к", "a", "the", "of", "about"}
MODEL_URL = "https://huggingface.co/Xenova/multilingual-e5-small/resolve/main/"
MODEL_DIR = DATA / "models" / "e5-small"
CHUNK, STEP, MAX_CHUNKS = 800, 700, 80


def known_folders():
    buf = ctypes.create_unicode_buffer(260)
    out = []
    for csidl in (0x10, 0x05):                            # Desktop, Documents (follows OneDrive / moved folders)
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0:
            out.append(buf.value)
    out.append(str(Path.home() / "Downloads"))
    return [p for p in dict.fromkeys(out) if Path(p).exists()]


# ====================================================================== text out of files
def _xml_text(z, names):
    out = []
    for n in names:
        try:
            x = z.read(n).decode("utf-8", "ignore")
        except KeyError:
            continue
        x = re.sub(r"</(?:w:p|a:p|text:p|text:h|row)>", "\n", x)
        x = re.sub(r"<[^>]+>", " ", x)
        out.append(x)
    import html
    return html.unescape(" ".join(out))


def extract(path):
    ext = path.suffix.lower()
    if ext in (".docx", ".pptx", ".xlsx", ".odt", ".odp", ".ods"):
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if ext == ".docx":
                parts = [n for n in names if re.match(r"word/(document|header\d*|footer\d*|footnotes)\.xml$", n)]
            elif ext == ".pptx":
                parts = sorted((n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                               key=lambda n: int(re.search(r"(\d+)\.xml", n).group(1)))
            elif ext == ".xlsx":
                parts = [n for n in names if n == "xl/sharedStrings.xml"]
            else:
                parts = ["content.xml"]
            return _xml_text(z, parts)
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            return ""
        r = PdfReader(str(path))
        return "\n".join((p.extract_text() or "") for p in r.pages[:120])
    raw = path.read_bytes()[:2_000_000]
    for enc in ("utf-8", "cp1251"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", "ignore")
    if ext == ".rtf":
        text = re.sub(r"\\'([0-9a-f]{2})", lambda m: bytes([int(m.group(1), 16)]).decode("cp1251", "ignore"), text)
        text = re.sub(r"\\[a-z]+-?\d* ?|[{}]", " ", text)
    elif ext in (".html", ".htm", ".xml"):
        text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>|<[^>]+>", " ", text)
    return text


# ====================================================================== words
_morph = None
_lem_cache = {}


def lemmas(text):
    global _morph
    if _morph is None:
        import pymorphy3
        _morph = pymorphy3.MorphAnalyzer()
    out = []
    for w in re.findall(r"[а-яёa-z0-9]+", text.lower().replace("ё", "е")):
        if len(w) < 2 and not w.isdigit():
            continue
        if re.match(r"[а-я]", w):
            lm = _lem_cache.get(w)
            if lm is None:
                lm = _morph.parse(w)[0].normal_form.replace("ё", "е")
                if len(_lem_cache) < 200_000:
                    _lem_cache[w] = lm
            out.append(lm)
        else:
            out.append(w)
    return out


# ====================================================================== meaning vectors
class Embedder:
    def __init__(self):
        self.sess = self.tok = None

    @staticmethod
    def ready():
        return (MODEL_DIR / "model_quantized.onnx").exists() and (MODEL_DIR / "tokenizer.json").exists()

    def load(self):
        if self.sess is None:
            import onnxruntime as ort
            from tokenizers import Tokenizer
            so = ort.SessionOptions()
            so.intra_op_num_threads = 2
            self.sess = ort.InferenceSession(str(MODEL_DIR / "model_quantized.onnx"), so, providers=["CPUExecutionProvider"])
            self.tok = Tokenizer.from_file(str(MODEL_DIR / "tokenizer.json"))
            self.tok.enable_truncation(max_length=256)
            self.tok.enable_padding()
            self.inputs = {i.name for i in self.sess.get_inputs()}

    def embed(self, texts, prefix="passage: "):
        self.load()
        enc = self.tok.encode_batch([prefix + t for t in texts])
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        h = self.sess.run(None, feed)[0]
        v = (h * mask[..., None]).sum(1) / np.maximum(mask.sum(1, keepdims=True), 1)
        return (v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)).astype(np.float32)


# ====================================================================== the index
class FileSearch:
    def __init__(self, core):
        self.core = core
        self.cfg = core.cfg
        self.db = sqlite3.connect(str(DATA / "files.db"), check_same_thread=False)
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, path TEXT UNIQUE, name TEXT, ext TEXT,
                                                  mtime REAL, size INTEGER);
                CREATE TABLE IF NOT EXISTS chunks (id INTEGER PRIMARY KEY, file_id INTEGER, n INTEGER, text TEXT);
                CREATE INDEX IF NOT EXISTS chunks_file ON chunks(file_id);
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(lem, tokenize='unicode61');
                CREATE TABLE IF NOT EXISTS vec (chunk_id INTEGER PRIMARY KEY, v BLOB);
            """)
        self.emb = Embedder()
        self._mat = None                # (chunk ids, matrix) of meaning vectors, loaded on the first smart search
        self.progress = {"state": "idle", "done": 0, "total": 0, "file": ""}
        self.downloading = None
        self.last = []                  # the last results ("открой второй")
        self._wake = threading.Event()

    def enabled(self):
        from . import system as S
        v = self.cfg.get("filesearch.enabled")
        return (not S.lite(self.cfg)) if v is None else bool(v)

    def folders(self):
        return self.cfg.get("filesearch.folders") or known_folders()

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="filesearch").start()

    # ---------------------------------------------------------------- indexing
    def _walk(self):
        for root in self.folders():
            for dirpath, dirs, files in os.walk(root):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in SKIP_DIRS]
                for f in files:
                    p = Path(dirpath) / f
                    if p.suffix.lower() in TEXT_EXT and not f.startswith("~$"):
                        yield p

    def _in_game(self):
        try:
            return bool(self.core.gamefx.game()[0])
        except Exception:
            return False

    def _loop(self):
        time.sleep(90)
        while True:
            if self.enabled():
                try:
                    self.scan()
                    if self.cfg.get("filesearch.smart") and self.emb.ready():
                        self.embed_missing()
                except Exception as e:
                    self.core.log(f"Поиск по файлам: {e}", "warn")
                self.progress["state"] = "idle"
            self._wake.wait(1800)
            self._wake.clear()

    def scan(self):
        with self.lock:
            known = {p: (i, m, s) for i, p, m, s in self.db.execute("SELECT id, path, mtime, size FROM files")}
        seen, todo = set(), []
        for p in self._walk():
            try:
                st = p.stat()
            except OSError:
                continue
            sp = str(p)
            seen.add(sp)
            if st.st_size > 30 * 1024 * 1024:
                continue
            k = known.get(sp)
            if not k or abs(k[1] - st.st_mtime) > 1 or k[2] != st.st_size:
                todo.append((p, st))
        gone = [known[p][0] for p in known if p not in seen]
        with self.lock:
            for fid in gone:
                self._drop(fid)
            self.db.commit()
        self.progress.update(state="index", done=0, total=len(todo))
        for i, (p, st) in enumerate(todo):
            while self._in_game():
                self.progress["state"] = "pause"
                time.sleep(20)
            self.progress.update(state="index", done=i, file=p.name)
            try:
                text = extract(p)
            except Exception:
                text = ""
            self._store(p, st, text)
            time.sleep(0.03)
        if todo or gone:
            self._mat = None
            self.core.log(f"Поиск по файлам: проиндексировано {len(todo)}, убрано {len(gone)}")

    def _drop(self, fid):
        ids = [r[0] for r in self.db.execute("SELECT id FROM chunks WHERE file_id=?", (fid,))]
        self.db.executemany("DELETE FROM chunks_fts WHERE rowid=?", [(i,) for i in ids])
        self.db.executemany("DELETE FROM vec WHERE chunk_id=?", [(i,) for i in ids])
        self.db.execute("DELETE FROM chunks WHERE file_id=?", (fid,))
        self.db.execute("DELETE FROM files WHERE id=?", (fid,))

    def _store(self, p, st, text):
        text = re.sub(r"[ \t\r\f\v]+", " ", text or "")
        text = re.sub(r"\n\s*\n+", "\n", text).strip()[:CHUNK * MAX_CHUNKS]
        name_lem = " ".join(lemmas(p.stem.replace("_", " ").replace("-", " ")))
        pieces = [text[i:i + CHUNK] for i in range(0, max(1, len(text)), STEP)][:MAX_CHUNKS] if text else [""]
        with self.lock:
            old = self.db.execute("SELECT id FROM files WHERE path=?", (str(p),)).fetchone()
            if old:
                self._drop(old[0])
            cur = self.db.execute("INSERT INTO files(path, name, ext, mtime, size) VALUES (?,?,?,?,?)",
                                  (str(p), p.name, p.suffix.lower(), st.st_mtime, st.st_size))
            fid = cur.lastrowid
            for n, piece in enumerate(pieces):
                c = self.db.execute("INSERT INTO chunks(file_id, n, text) VALUES (?,?,?)", (fid, n, piece))
                # the file name goes into every piece: "реферат по истории" finds the file by its name too
                self.db.execute("INSERT INTO chunks_fts(rowid, lem) VALUES (?,?)",
                                (c.lastrowid, name_lem + " " + " ".join(lemmas(piece))))
            self.db.commit()

    def embed_missing(self):
        with self.lock:
            rows = self.db.execute("SELECT c.id, c.text, f.name FROM chunks c JOIN files f ON f.id=c.file_id "
                                   "LEFT JOIN vec v ON v.chunk_id=c.id WHERE v.chunk_id IS NULL").fetchall()
        if not rows:
            return
        self.progress.update(state="smart", done=0, total=len(rows))
        for i in range(0, len(rows), 16):
            while self._in_game():
                self.progress["state"] = "pause"
                time.sleep(20)
            batch = rows[i:i + 16]
            vs = self.emb.embed([f"{name}. {text}"[:1200] for _, text, name in batch])
            with self.lock:
                self.db.executemany("INSERT OR REPLACE INTO vec(chunk_id, v) VALUES (?,?)",
                                    [(cid, v.astype(np.float16).tobytes()) for (cid, _, _), v in zip(batch, vs)])
                self.db.commit()
            self.progress.update(state="smart", done=i + len(batch))
            time.sleep(0.05)
        self._mat = None

    # ---------------------------------------------------------------- search
    def _matrix(self):
        if self._mat is None:
            with self.lock:
                rows = self.db.execute("SELECT chunk_id, v FROM vec").fetchall()
            if not rows:
                return None
            ids = np.array([r[0] for r in rows])
            m = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float16).reshape(len(rows), -1).astype(np.float32)
            self._mat = (ids, m)
        return self._mat

    def search(self, query, kind=None, limit=8):
        terms = [w for w in lemmas(query) if w not in STOP]
        exts = None
        if kind:
            k = next((v for st, v in KINDS.items() if kind.lower().startswith(st)), None)
            exts = k
        ranks = {}                                # file id -> fused score
        best_chunk = {}
        if terms:
            q = " OR ".join(f'"{t}"*' for t in terms)
            with self.lock:
                rows = self.db.execute(
                    "SELECT c.id, c.file_id, bm25(chunks_fts) AS s FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.rowid "
                    "WHERE chunks_fts MATCH ? ORDER BY s LIMIT 300", (q,)).fetchall()
            # files where more of the words meet rank higher than one file with one word many times
            per = {}
            for cid, fid, s in rows:
                per.setdefault(fid, []).append((s, cid))
            order = sorted(per, key=lambda f: min(x[0] for x in per[f]))
            for r, fid in enumerate(order):
                ranks[fid] = ranks.get(fid, 0) + 1 / (60 + r)
                best_chunk.setdefault(fid, min(per[fid])[1])
        mat = self._matrix() if self.cfg.get("filesearch.smart") and self.emb.ready() else None
        if mat is not None:
            ids, m = mat
            qv = self.emb.embed([query], prefix="query: ")[0]
            sims = m @ qv
            top = np.argsort(-sims)[:150]
            with self.lock:
                fmap = dict(self.db.execute(f"SELECT id, file_id FROM chunks WHERE id IN ({','.join(str(int(ids[i])) for i in top)})"))
            seen = []
            for i in top:
                fid = fmap.get(int(ids[i]))
                if fid is None or fid in seen or sims[i] < 0.78:
                    continue
                seen.append(fid)
                ranks[fid] = ranks.get(fid, 0) + 1 / (60 + len(seen) - 1)
                best_chunk.setdefault(fid, int(ids[i]))
        out = []
        with self.lock:
            for fid in sorted(ranks, key=lambda f: -ranks[f]):
                f = self.db.execute("SELECT path, name, ext, mtime FROM files WHERE id=?", (fid,)).fetchone()
                if not f or (exts and f[2] not in exts) or not Path(f[0]).exists():
                    continue
                text = (self.db.execute("SELECT text FROM chunks WHERE id=?", (best_chunk.get(fid),)).fetchone() or [""])[0]
                out.append({"path": f[0], "name": f[1], "ext": f[2], "mtime": f[3], "snippet": self._snippet(text, terms),
                            "folder": str(Path(f[0]).parent)})
                if len(out) >= limit:
                    break
        self.last = out
        return out

    @staticmethod
    def _snippet(text, terms):
        low = text.lower().replace("ё", "е")
        pos = min([p for p in (low.find(t[:max(4, len(t) - 2)]) for t in terms) if p >= 0] or [0])
        a = max(0, pos - 90)
        s = text[a:a + 240].replace("\n", " ").strip()
        return ("…" if a else "") + s + ("…" if a + 240 < len(text) else "")

    # ---------------------------------------------------------------- the smart model
    def download_model(self):
        import requests
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        for name in ("tokenizer.json", "onnx/model_quantized.onnx"):
            dst = MODEL_DIR / Path(name).name
            if dst.exists():
                continue
            tmp = dst.with_suffix(".part")
            with requests.get(MODEL_URL + name, stream=True, timeout=30) as r:
                r.raise_for_status()
                total, got = int(r.headers.get("content-length") or 0), 0
                with open(tmp, "wb") as f:
                    for block in r.iter_content(1 << 20):
                        f.write(block)
                        got += len(block)
                        self.downloading = {"file": dst.name, "got": got, "total": total}
            os.replace(tmp, dst)
        self.downloading = None

    def rpc_filesearch_smart(self, on=True):
        self.cfg.set("filesearch.smart", bool(on))
        if on and not self.emb.ready():
            def go():
                try:
                    self.download_model()
                    self.core.say("Умный поиск по файлам готов. Разбираю документы по смыслу в фоне.")
                    self._wake.set()
                except Exception as e:
                    self.downloading = None
                    self.cfg.set("filesearch.smart", False)
                    self.core.log(f"Поиск по файлам: модель не скачалась: {e}", "warn")
                    self.core.say("Не получилось скачать модель для умного поиска. Проверьте интернет.")
            threading.Thread(target=go, daemon=True).start()
        elif on:
            self._wake.set()
        return self.rpc_filesearch_state()

    # ---------------------------------------------------------------- RPC
    def rpc_filesearch_state(self):
        with self.lock:
            nf = self.db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            nv = self.db.execute("SELECT COUNT(*) FROM vec").fetchone()[0]
            nc = self.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return {"enabled": self.enabled(), "folders": self.folders(), "files": nf, "chunks": nc, "vectors": nv,
                "smart": bool(self.cfg.get("filesearch.smart")), "model": self.emb.ready(),
                "progress": self.progress, "downloading": self.downloading}

    def rpc_filesearch_query(self, q, kind=None):
        return self.search(q, kind, limit=12)

    def rpc_filesearch_set(self, folders=None, enabled=None):
        if folders is not None:
            self.cfg.set("filesearch.folders", [f for f in folders if f and Path(f).exists()] or None)
        if enabled is not None:
            self.cfg.set("filesearch.enabled", bool(enabled))
        self._wake.set()
        return self.rpc_filesearch_state()

    def rpc_filesearch_reindex(self):
        self._wake.set()
        return True

    def rpc_filesearch_open(self, path, folder=False):
        from . import system as S
        if folder:
            S.reveal(Path(path))
        else:
            os.startfile(path)
        return True


# ====================================================================== voice rules
def register(R, core):
    FS = lambda: core.filesearch
    KIND = r"(файл\w*|документ\w*|презентаци\w*|таблиц\w*|текст\w*|конспект\w*|доклад\w*|реферат\w*|pdf|пдф|ворд\w*)"

    def answer(q, kind=None):
        q = q.strip(" «»\"?.")
        res = FS().search(q, kind)
        core.emit("files_found", query=q, files=[r["path"] for r in res], rich=res)
        if not res:
            if FS().progress.get("state") == "index":
                return f"Ещё разбираю документы ({FS().progress['done']} из {FS().progress['total']}). Пока не нашёл «{q}»."
            core.find_files(q)                     # nothing inside the files: maybe it is in a name
            return True
        r = res[0]
        when = time.strftime("%d.%m", time.localtime(r["mtime"]))
        more = f" Ещё {len(res) - 1} — скажите «открой второй»." if len(res) > 1 else " Скажите «открой его»."
        return f"Похоже, это «{Path(r['name']).stem}» в папке {Path(r['folder']).name}, от {when}.{more}"

    @R(r"^(?:найди|поищи|отыщи|где|покажи)\s+(?:мне\s+)?(?:мой\s+|мою\s+|мои\s+|тот\s+|ту\s+|тот\s+самый\s+)?" + KIND +
       r"\s+(?:про|о|об|по|где|в\s+котор\w+|с|со|на\s+тему)\s+(.+)$")
    def _kind(m, t):
        return answer(m.group(2), m.group(1))

    @R(r"^в\s+каком\s+(?:файле|документе)\s+(?:я\s+)?(?:писал\w*\s+|было\s+|есть\s+)?(?:про\s+|о\s+|об\s+)?(.+)$|"
       r"^(?:найди|поищи)\s+в\s+(?:файлах|документах)\s+(.+)$|^где\s+(?:я\s+)?(?:писал|записывал|сохранял)\w*\s+(?:про\s+|о\s+|об\s+)?(.+)$")
    def _where(m, t):
        return answer(next(g for g in m.groups() if g))

    ORD = {"первый": 0, "второй": 1, "третий": 2, "четвертый": 3, "пятый": 4, "его": 0, "этот": 0, "найденный": 0}

    @R(r"^открой\s+(первый|второй|третий|четвертый|пятый|его|этот)(?:\s+(?:файл|документ|найденный))?$|^открой\s+(найденный)\s+файл$|"
       r"^покажи\s+(первый|второй|третий|его|этот)\s+в\s+папке$")
    def _open(m, t):
        g = next(x for x in m.groups() if x)
        res = FS().last
        i = ORD.get(g, 0)
        if not res or i >= len(res):
            return False
        FS().rpc_filesearch_open(res[i]["path"], folder="папке" in t)
        return f"Открываю «{Path(res[i]['name']).stem}»."

    @R(r"^(?:включи|запусти)\s+умный\s+поиск(?:\s+по\s+файлам)?$")
    def _smart(m, t):
        if FS().emb.ready():
            FS().rpc_filesearch_smart(True)
            return "Умный поиск включён."
        FS().rpc_filesearch_smart(True)
        return "Скачиваю модель для умного поиска, около 135 мегабайт. Скажу, когда будет готово."
