"""Screen memory: "Джарвис, где я видел ту ссылку на видеокарту?".

Once a minute, while the user is at the PC, the text of the active window is read by Windows' own OCR (on this
PC: no internet, no AI requests) and kept in a small local database with full-text search, with a small picture
of the window. Nothing is taken when the window has not changed, when a game or film is full screen (no FPS
drops), or from password managers, banks and private browser windows. Everything older than a week is deleted.
All of it lives in data/ (never in git, never sent anywhere); "не запоминай экран" pauses it, "забудь экран"
wipes it."""
import datetime as dt
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from . import system as S
from .config import DATA

DB = DATA / "screen_memory.db"
PICS = DATA / "screen_memory"
SKIP_RX = re.compile(r"(?i)keepass|bitwarden|1password|lastpass|enpass|пароль|password|сбербанк|сбер\s*онлайн|"
                     r"т-банк|тинькофф|альфа-банк|втб|газпромбанк|банк онлайн|inprivate|инкогнито|incognito|"
                     r"private browsing|частный просмотр|j\.a\.r\.v\.i\.s\.|jarvis quick|jarvis menu|jarvis hud")
MONTHS_G = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


class ScreenMemory:
    def __init__(self, core):
        self.core = core
        self.cfg = core.cfg
        self._last = {}            # window -> small picture fingerprint of the last snapshot
        self._lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="screen-memory").start()

    def _db(self):
        con = sqlite3.connect(DB, timeout=10)
        con.execute("create table if not exists snaps(id integer primary key, ts real, exe text, title text, text text, pic text)")
        con.execute("create virtual table if not exists snaps_fts using fts5(title, text, content='snaps', content_rowid='id', "
                    "tokenize='unicode61 remove_diacritics 2')")
        return con

    # ---------------------------------------------------------------- taking snapshots
    def _loop(self):
        time.sleep(60)
        last_clean = 0
        while True:
            try:
                if self.cfg.get("screenmem.enabled", not S.lite(self.cfg)):
                    self.snap()
                if time.time() - last_clean > 6 * 3600:
                    last_clean = time.time()
                    self.cleanup()
            except Exception as e:
                self.core.log(f"Память экрана: {str(e)[:150]}", "warn")
            time.sleep(max(20, int(self.cfg.get("screenmem.every_s", 60))))

    def snap(self):
        from . import system as S
        if S.idle_seconds() > 120 or S.foreground_fullscreen() or self.core.sleeping:
            return False
        import win32gui
        import win32process
        import psutil
        h = win32gui.GetForegroundWindow()
        title = S.active_title()
        try:
            _, pid = win32process.GetWindowThreadProcessId(h)
            exe = psutil.Process(pid).name().replace(".exe", "")
            if pid == os.getpid():
                return False                         # Jarvis's own windows
        except Exception:
            exe = ""
        if not title or SKIP_RX.search(f"{title} {exe}"):
            return False
        try:
            l, t, r, b = win32gui.GetWindowRect(h)
        except Exception:
            return False
        if r - l < 200 or b - t < 120:
            return False
        img = self._grab(l, t, r, b)
        if img is None:
            return False
        key = f"{exe}|{title}"
        fp = self._fingerprint(img)
        old = self._last.get(key)
        if old is not None and sum(abs(a - c) for a, c in zip(fp, old)) / len(fp) < 6:
            return False                             # the same window, practically unchanged
        self._last[key] = fp
        text = self._ocr(img)
        if len(text) < 12:
            return False
        day = dt.datetime.now()
        folder = PICS / day.strftime("%Y-%m-%d")
        folder.mkdir(parents=True, exist_ok=True)
        pic = folder / day.strftime("%H%M%S.jpg")
        thumb = img.copy()
        thumb.thumbnail((960, 960))
        thumb.save(pic, "JPEG", quality=60)
        with self._lock:
            con = self._db()
            cur = con.execute("insert into snaps(ts, exe, title, text, pic) values (?,?,?,?,?)",
                              (time.time(), exe, title[:300], text[:20000], str(pic)))
            con.execute("insert into snaps_fts(rowid, title, text) values (?,?,?)", (cur.lastrowid, title[:300], text[:20000]))
            con.commit()
            con.close()
        return True

    @staticmethod
    def _grab(l, t, r, b):
        import mss
        from PIL import Image
        try:
            with mss.MSS() as s:
                shot = s.grab({"left": l, "top": t, "width": r - l, "height": b - t})
            return Image.frombytes("RGB", shot.size, shot.rgb)
        except Exception:
            return None

    @staticmethod
    def _fingerprint(img):
        return list(img.convert("L").resize((24, 16)).getdata())

    @staticmethod
    def _ocr(img):
        """Windows OCR (ru + en), upscaled x2 for small text: the same engine the mouse commands use."""
        import asyncio
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter
        scale = 2 if img.width <= 1600 else 1
        if scale != 1:
            img = img.resize((img.width * scale, img.height * scale))
        data = img.convert("RGBA").tobytes("raw", "BGRA")

        async def run():
            eng = OcrEngine.try_create_from_language(Language("ru")) or OcrEngine.try_create_from_user_profile_languages()
            w = DataWriter()
            w.write_bytes(data)
            sb = SoftwareBitmap.create_copy_from_buffer(w.detach_buffer(), BitmapPixelFormat.BGRA8, img.width, img.height)
            return await eng.recognize_async(sb)
        res = asyncio.run(run())
        return "\n".join(ln.text for ln in res.lines)

    def cleanup(self):
        """Older than a week (Settings: screenmem.days) goes away, pictures included."""
        days = int(self.cfg.get("screenmem.days", 7))
        cut = time.time() - days * 86400
        with self._lock:
            con = self._db()
            con.execute("insert into snaps_fts(snaps_fts, rowid, title, text) select 'delete', id, title, text from snaps where ts < ?", (cut,))
            con.execute("delete from snaps where ts < ?", (cut,))
            con.commit()
            con.close()
        if PICS.exists():
            limit = (dt.date.today() - dt.timedelta(days=days)).isoformat()
            for d in PICS.iterdir():
                if d.is_dir() and d.name < limit:
                    for f in d.iterdir():
                        try:
                            f.unlink()
                        except Exception:
                            pass
                    try:
                        d.rmdir()
                    except Exception:
                        pass

    def wipe(self):
        with self._lock:
            try:
                DB.unlink()
            except Exception:
                pass
        if PICS.exists():
            for d in PICS.iterdir():
                for f in (d.iterdir() if d.is_dir() else []):
                    try:
                        f.unlink()
                    except Exception:
                        pass
                try:
                    d.rmdir()
                except Exception:
                    pass
        self._last.clear()

    # ---------------------------------------------------------------- searching
    @staticmethod
    def _terms(q):
        """Spoken words -> FTS prefixes: "видеокарту" -> "видеок*" (Russian endings change), plus digits/Latin
        variants ("пятьдесят семьдесят" -> "5070")."""
        from .mouse import _variants
        words = []
        for v in _variants(q):
            for w in re.findall(r"\w+", v):
                if len(w) < 3 and not w.isdigit():
                    continue
                stem = w[:max(4, len(w) - 2)] if len(w) > 5 and not w.isdigit() else w
                tok = '"' + stem.replace('"', "") + '"*'
                if tok not in words:
                    words.append(tok)
        return words

    def search(self, q, limit=8):
        q = re.sub(r"(?i)^(?:ту|тот|то|ту самую|тот самый|эту|этот)\s+", "", q.strip())
        terms = self._terms(q)
        if not terms or not DB.exists():
            return []
        with self._lock:
            con = self._db()
            rows = []
            for joiner in (" AND ", " OR "):
                try:
                    rows = con.execute("select s.id, s.ts, s.exe, s.title, s.pic, snippet(snaps_fts, 1, '[', ']', '…', 12) "
                                       "from snaps_fts join snaps s on s.id = snaps_fts.rowid where snaps_fts match ? "
                                       "order by bm25(snaps_fts), s.ts desc limit ?", (joiner.join(terms), limit)).fetchall()
                except sqlite3.OperationalError:
                    rows = []
                if rows:
                    break
            con.close()
        return [{"id": r[0], "ts": r[1], "exe": r[2], "title": r[3], "pic": r[4], "snippet": r[5]} for r in rows]

    def at(self, hour, minute, day=None):
        """What was on the screen at that time (today, or yesterday when that time has not come yet)."""
        now = dt.datetime.now()
        when = (day or now).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if when > now:
            when -= dt.timedelta(days=1)
        if not DB.exists():
            return None
        with self._lock:
            con = self._db()
            r = con.execute("select id, ts, exe, title, pic from snaps order by abs(ts - ?) limit 1", (when.timestamp(),)).fetchone()
            con.close()
        if not r or abs(r[1] - when.timestamp()) > 20 * 60:
            return None
        return {"id": r[0], "ts": r[1], "exe": r[2], "title": r[3], "pic": r[4]}

    @staticmethod
    def when_said(ts):
        d = dt.datetime.fromtimestamp(ts)
        today = dt.date.today()
        day = "сегодня" if d.date() == today else "вчера" if d.date() == today - dt.timedelta(days=1) \
            else f"{d.day} {MONTHS_G[d.month - 1]}"
        return f"{day} в {d:%H:%M}"

    # ---------------------------------------------------------------- RPC (Settings)
    def rpc_screenmem_state(self):
        n, size = 0, 0
        if DB.exists():
            with self._lock:
                con = self._db()
                n = con.execute("select count(*) from snaps").fetchone()[0]
                con.close()
            size = DB.stat().st_size + sum(f.stat().st_size for f in PICS.rglob("*.jpg")) if PICS.exists() else DB.stat().st_size
        return {"enabled": self.cfg.get("screenmem.enabled", not S.lite(self.cfg)), "days": self.cfg.get("screenmem.days", 7),
                "count": n, "mb": round(size / 2**20, 1)}

    def rpc_screenmem_set(self, enabled=None, days=None):
        if enabled is not None:
            self.cfg.set("screenmem.enabled", bool(enabled))
        if days is not None:
            self.cfg.set("screenmem.days", max(1, min(30, int(days))))
        return self.rpc_screenmem_state()

    def rpc_screenmem_search(self, q):
        return [dict(r, when=self.when_said(r["ts"])) for r in self.search(q, 20)]

    def rpc_screenmem_open(self, id):
        with self._lock:
            con = self._db()
            r = con.execute("select pic from snaps where id = ?", (int(id),)).fetchone()
            con.close()
        if r and r[0] and Path(r[0]).exists():
            os.startfile(r[0])
            return True
        return False

    def rpc_screenmem_wipe(self):
        self.wipe()
        return self.rpc_screenmem_state()


# ====================================================================== voice rules
def register(R, core):
    M = lambda: core.screenmem

    def tell(hits, what):
        if not hits:
            return f"Не помню «{what}» на экране, {core.persona.address()}." if what else "Ничего не нашёл."
        h = hits[0]
        app = h["exe"] or "окне"
        title = re.sub(r"\s+[-—–]\s+(?:Google Chrome|Mozilla Firefox|Microsoft Edge|Яндекс Браузер|Opera)$", "", h["title"])
        try:
            os.startfile(h["pic"])                  # the picture of that moment: the user sees it right away
        except Exception:
            pass
        more = f" Похожих моментов ещё {len(hits) - 1}." if len(hits) > 1 else ""
        return f"{ScreenMemory.when_said(h['ts']).capitalize()}, в {app}: «{title[:80]}». Открыл снимок.{more}"

    @R(r"^(?:где|когда)\s+я\s+(?:видел|видела|смотрел|смотрела|читал|читала)\s+(.+)$|"
       r"^(?:вспомни|найди)\s+(?:на\s+экране|в\s+памяти\s+экрана|в\s+истории\s+экрана)\s+(.+)$|"
       r"^(?:вспомни|напомни),?\s+где\s+(?:я\s+)?(?:видел|видела|был[аои]?)\s+(.+)$")
    def _where(m, t):
        what = next(g for g in m.groups() if g).strip()
        if not core.cfg.get("screenmem.enabled", not S.lite(core.cfg)) and not M().search(what):
            return "Память экрана выключена, сэр. Скажите «запоминай экран», чтобы включить."
        return tell(M().search(what), what)

    @R(r"^что\s+(?:у\s+меня\s+)?(?:было\s+)?на\s+экране\s+(?:в|около)\s+(\d{1,2})(?:[:.\s]+(\d{2}))?(?:\s+час\w*)?$")
    def _at(m, t):
        hit = M().at(int(m.group(1)), int(m.group(2) or 0))
        if not hit:
            return "В это время я ничего не запомнил: вас не было за ПК или память экрана была выключена."
        return tell([hit], "")

    @R(r"^(?:не\s+запоминай|перестань\s+запоминать|выключи\s+память)\s+(?:экран\w*|что\s+на\s+экране)$|^выключи\s+память\s+экрана$")
    def _off(m, t):
        core.cfg.set("screenmem.enabled", False)
        return "Больше не запоминаю экран."

    @R(r"^(?:запоминай|включи\s+память)\s+(?:экран\w*|что\s+на\s+экране)$|^включи\s+память\s+экрана$")
    def _on(m, t):
        core.cfg.set("screenmem.enabled", True)
        return "Запоминаю, что на экране: раз в минуту, только у вас на компьютере."

    @R(r"^(?:забудь|сотри|очисти)\s+(?:что\s+было\s+на\s+экране|память\s+экрана|историю\s+экрана|экран)$")
    def _wipe(m, t):
        M().wipe()
        return "Стёр всё, что запомнил с экрана."
