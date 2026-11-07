"""Screen translator: the translation is drawn right over the original text, in any program or game.

"Джарвис, переведи экран" (or Ctrl+Alt+T) — the active window is read with Windows OCR, lines are joined into
paragraphs, each paragraph is translated and painted over its place: a dark plate the size of the original with the
translation fitted inside. A click, another window or Esc takes it away; hold Ctrl+Shift to peek at the original.

- Live mode ("переводи экран", "переводи субтитры", Ctrl+Alt+L): the window (or a picked area — "переводи эту
  область") is looked at about once a second; only when the picture changes it is read again, and only new lines are
  translated (a cache), so game dialogues and subtitles get translated as they appear. "…вслух" also reads the new
  lines aloud.
- The overlay window is excluded from screen capture (WDA_EXCLUDEFROMCAPTURE), so the live mode never reads its own
  translation back. Click-through, never takes focus. Works over borderless/windowed games; an exclusive full screen
  game draws over every window, there it has to be switched to borderless.
- Translation: Google Translate's free web endpoint (no key), cached; without network or when it fails, one request
  to the user's AI. Only the recognised text is sent, never the picture, and only when asked."""
import ctypes
import queue
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests

user32 = ctypes.WinDLL("user32")

LANGS = {"русский": "ru", "английский": "en", "немецкий": "de", "французский": "fr", "испанский": "es",
         "итальянский": "it", "украинский": "uk", "польский": "pl", "турецкий": "tr", "японский": "ja",
         "китайский": "zh-CN", "корейский": "ko", "португальский": "pt", "казахский": "kk", "белорусский": "be"}
LANG_RX = r"(русск\w*|английск\w*|немецк\w*|французск\w*|испанск\w*|итальянск\w*|украинск\w*|польск\w*|турецк\w*|японск\w*|китайск\w*|корейск\w*|португальск\w*|казахск\w*|белорусск\w*)"
WDA_EXCLUDEFROMCAPTURE = 0x11
VK = {"lbutton": 0x01, "rbutton": 0x02, "esc": 0x1B, "ctrl": 0x11, "shift": 0x10}


def lang_code(word):
    w = (word or "").lower()
    return next((c for k, c in LANGS.items() if w.startswith(k[:5])), None)


def _down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


# ====================================================================== OCR
_engines = {}


def _engine(lang):
    from winrt.windows.globalization import Language
    from winrt.windows.media.ocr import OcrEngine
    if lang not in _engines:
        eng = None
        for tag in ({"en": ("en-US", "en-GB", "en"), "ru": ("ru", "ru-RU")}.get(lang, (lang,))):
            try:
                if OcrEngine.is_language_supported(Language(tag)):
                    eng = OcrEngine.try_create_from_language(Language(tag))
                    break
            except Exception:
                pass
        _engines[lang] = eng or OcrEngine.try_create_from_user_profile_languages()
    return _engines[lang]


def grab(rect):
    """Physical-pixel screenshot of rect (l, t, r, b) as a PIL image."""
    import mss
    from PIL import Image

    from .mouse import physical
    l, t, r, b = rect
    with physical():
        with mss.MSS() as s:
            shot = s.grab({"left": l, "top": t, "width": max(1, r - l), "height": max(1, b - t)})
    return Image.frombytes("RGB", shot.size, shot.rgb)


def ocr_image(img, origin, lang="en"):
    """[{text, x, y, w, h, wh (word height)}] in screen pixels. Small text is upscaled: OCR reads it much better."""
    import asyncio

    from PIL import Image
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter
    eng = _engine(lang)
    scale = 2.0 if max(img.size) <= 2200 else 1.0
    try:
        limit = OcrEngine.max_image_dimension
    except Exception:
        limit = 10000
    while scale > 1 and max(img.size) * scale > limit:
        scale -= 0.5
    if scale != 1:
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.BICUBIC)
    data = img.convert("RGBA").tobytes("raw", "BGRA")

    async def run():
        w = DataWriter()
        w.write_bytes(data)
        sb = SoftwareBitmap.create_copy_from_buffer(w.detach_buffer(), BitmapPixelFormat.BGRA8, img.width, img.height)
        return await eng.recognize_async(sb)

    res = asyncio.run(run())
    ox, oy = origin
    out = []
    for ln in res.lines:
        rs = [wd.bounding_rect for wd in ln.words]
        if not rs:
            continue
        x0 = min(r.x for r in rs) / scale; y0 = min(r.y for r in rs) / scale
        x1 = max(r.x + r.width for r in rs) / scale; y1 = max(r.y + r.height for r in rs) / scale
        hs = sorted(r.height / scale for r in rs)
        out.append({"text": ln.text, "x": int(x0) + ox, "y": int(y0) + oy, "w": int(x1 - x0), "h": int(y1 - y0),
                    "wh": hs[len(hs) // 2]})
    return out


def paragraphs(lines):
    """Lines -> paragraphs: the next line belongs to the block when it is right below it, of the same text size and
    starts near the same left edge or overlaps it. A sentence split over three lines is translated as one."""
    lines = sorted(lines, key=lambda l: (l["y"], l["x"]))
    blocks = []
    for l in lines:
        best = None
        for b in blocks:
            last = b["lines"][-1]
            gap = l["y"] - (last["y"] + last["h"])
            same = 0.7 <= l["wh"] / max(1, last["wh"]) <= 1.4
            overlap = min(l["x"] + l["w"], b["x"] + b["w"]) - max(l["x"], b["x"])
            aligned = abs(l["x"] - last["x"]) < 2.5 * l["wh"] or overlap > 0.3 * min(l["w"], b["w"])
            wrapped = len(last["text"]) >= 24 and not last["text"].rstrip().endswith((":", ";"))
            if -0.3 * l["wh"] <= gap <= 0.9 * l["wh"] and same and aligned and wrapped:
                best = b
                break
        if best:
            best["lines"].append(l)
            x1 = max(best["x"] + best["w"], l["x"] + l["w"])
            best["x"] = min(best["x"], l["x"])
            best["w"] = x1 - best["x"]
            best["h"] = l["y"] + l["h"] - best["y"]
        else:
            blocks.append({"x": l["x"], "y": l["y"], "w": l["w"], "h": l["h"], "lines": [l]})
    for b in blocks:
        text = ""
        for l in b["lines"]:
            t = l["text"].strip()
            text = text[:-1] + t if text.endswith("-") else (text + " " + t).strip()
        b["text"] = text
        b["wh"] = sorted(l["wh"] for l in b["lines"])[len(b["lines"]) // 2]
    return blocks


def worth(text, tl):
    """Is it text to translate: letters, not a lone number or "OK", and not already in the target script."""
    letters = re.findall(r"[^\W\d_]", text)
    if len(letters) < 2 or len(text.strip()) < 2:
        return False
    cyr = sum(1 for c in letters if "а" <= c.lower() <= "я" or c in "ёЁ")
    if tl in ("ru", "uk", "be", "kk"):
        return cyr / len(letters) < 0.5
    if tl in ("en", "de", "fr", "es", "it", "pl", "tr", "pt"):
        return cyr / len(letters) >= 0.5 or tl != "en"
    return True


# ====================================================================== translation
class Translator:
    def __init__(self, core):
        self.core = core
        self.cache = {}
        self.pool = ThreadPoolExecutor(6, thread_name_prefix="translate")
        self.last_engine = "google"

    def google(self, text, tl):
        r = requests.get("https://translate.googleapis.com/translate_a/single",
                         params={"client": "gtx", "sl": "auto", "tl": tl, "dt": "t", "q": text}, timeout=8,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        j = r.json()
        out = "".join(seg[0] for seg in (j[0] or []) if seg and seg[0])
        src = j[2] if len(j) > 2 and isinstance(j[2], str) else ""
        return out.strip(), src

    def ai(self, texts, tl):
        name = next((k for k, c in LANGS.items() if c == tl), tl)
        prompt = (f"Переведи на {name} язык каждую строку. Ответь только переводами, по одному на строку, с тем же "
                  f"номером, без пояснений.\n" + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts)))
        ans = self.core.ask_ai_once(prompt)
        got = {}
        for line in ans.splitlines():
            m = re.match(r"\s*(\d+)[.)]\s*(.+)", line)
            if m:
                got[int(m.group(1)) - 1] = m.group(2).strip()
        return [got.get(i, t) for i, t in enumerate(texts)]

    def many(self, texts, tl):
        """{text: translation}; the same text is never sent twice in a session."""
        need = [t for t in dict.fromkeys(texts) if (t, tl) not in self.cache]
        if need:
            engine = self.core.cfg.get("screentrans.engine", "google")
            failed = []
            if engine == "google":
                futs = {t: self.pool.submit(self.google, t, tl) for t in need}
                for t, f in futs.items():
                    try:
                        tr, src = f.result()
                        self.cache[(t, tl)] = t if src == tl.split("-")[0] else (tr or t)
                    except Exception:
                        failed.append(t)
            else:
                failed = need
            if failed:
                try:
                    for t, tr in zip(failed, self.ai(failed, tl)):
                        self.cache[(t, tl)] = tr
                    self.last_engine = "ai"
                except Exception as e:
                    self.core.log(f"Перевод: {e}", "warn")
            if len(self.cache) > 4000:
                self.cache = dict(list(self.cache.items())[-3000:])
        return {t: self.cache.get((t, tl), t) for t in texts}


# ====================================================================== the overlay
class Overlay:
    """One click-through layered window over the translated area, owned by its own thread."""

    def __init__(self, core):
        self.core = core
        self.q = queue.Queue()
        self.thread = None
        self.visible = False
        self.excluded = None          # SetWindowDisplayAffinity worked: OCR does not see the overlay

    def show(self, rect, blocks, once=True, shot=None):
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._loop, daemon=True, name="translate-overlay")
            self.thread.start()
        self.q.put(("show", rect, blocks, once, shot))

    @staticmethod
    def colours(shot, box):
        """(background, text) colours of a text box on the screenshot: the background is the median of a thin ring
        around the box, the text is what differs from it most inside."""
        W, H = shot.size
        x0, y0, x1, y1 = [int(v) for v in box]
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
        if x1 - x0 < 3 or y1 - y0 < 3:
            return None
        a = np.asarray(shot.crop((x0, y0, x1, y1)).convert("RGB"), dtype=np.int16)
        ring = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
        bg = np.median(ring, axis=0)
        dist = np.abs(a.reshape(-1, 3) - bg).sum(axis=1)
        far = a.reshape(-1, 3)[dist >= np.percentile(dist, 92)]
        fg = far.mean(axis=0) if len(far) else 255 - bg
        lum = lambda c: 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
        if abs(lum(fg) - lum(bg)) < 90:                 # too close to read: plain white or black
            fg = np.array([20, 20, 20]) if lum(bg) > 140 else np.array([245, 248, 252])
        return tuple(int(v) for v in bg), tuple(int(v) for v in fg)

    def hide(self):
        self.q.put(("hide",))

    def _render(self, rect, blocks, shot=None):
        from PIL import Image, ImageDraw, ImageFont

        from .marker import COLORS, THEME_COLOR
        l, t, r, b = rect
        W, H = max(1, r - l), max(1, b - t)
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        accent = COLORS.get(THEME_COLOR.get(self.core.cfg.get("theme", "jarvis"), "cyan"), COLORS["cyan"])
        opacity = int(255 * float(self.core.cfg.get("screentrans.opacity", 0.96)))
        style = self.core.cfg.get("screentrans.style", "lens")
        fonts = {}

        def font(px):
            if px not in fonts:
                try:
                    fonts[px] = ImageFont.truetype("segoeui.ttf", px)
                except Exception:
                    fonts[px] = ImageFont.load_default()
            return fonts[px]

        def wrap(text, f, width):
            out, cur = [], ""
            for word in text.split():
                test = (cur + " " + word).strip()
                if f.getlength(test) <= width or not cur:
                    cur = test
                else:
                    out.append(cur)
                    cur = word
            if cur:
                out.append(cur)
            return out

        for bl in blocks:
            tr = bl.get("tr") or ""
            if not tr:
                continue
            pad = max(3, int(bl["wh"] * 0.25))
            x0, y0 = bl["x"] - l - pad, bl["y"] - t - pad
            bw, bh = bl["w"] + 2 * pad, bl["h"] + 2 * pad
            px = int(max(11, min(44, bl["wh"] * 1.05)))
            lines, lh = [], px
            # a one-line label ("Quit to Desktop" -> "Выйти на рабочий стол") grows sideways before its font shrinks
            maxw = bw if len(bl["lines"]) > 1 else max(bw, min(W - max(0, x0), int(bw * 1.8)))
            for size in range(px, max(10, int(px * 0.62)) - 1, -1):
                f = font(size)
                lines = wrap(tr, f, max(20, maxw - 2 * pad - 3))
                lh = int(size * 1.22)
                if len(lines) * lh <= bh - pad or size == max(10, int(px * 0.62)):
                    px = size
                    break
            f = font(px)
            need_h = len(lines) * lh + pad
            need_w = max([f.getlength(x) for x in lines] + [0]) + 2 * pad + 3
            bh, bw = max(bh, need_h), max(bw, int(need_w))
            x0 = min(max(0, x0), max(0, W - bw))
            y0 = min(max(0, y0), max(0, H - bh))
            cols = self.colours(shot, (bl["x"] - l - pad, bl["y"] - t - pad, bl["x"] - l + bl["w"] + pad,
                                       bl["y"] - t + bl["h"] + pad)) if shot is not None and style == "lens" else None
            if cols:
                bg, fg = cols
                d.rounded_rectangle((x0, y0, x0 + bw, y0 + bh), radius=max(2, pad // 2), fill=bg + (255,))
                ink = fg + (255,)
            else:
                d.rounded_rectangle((x0, y0, x0 + bw, y0 + bh), radius=max(3, pad), fill=(10, 17, 26, opacity))
                d.rectangle((x0, y0 + 2, x0 + 2, y0 + bh - 2), fill=accent + (230,))
                ink = (238, 246, 255, 255)
            ty = y0 + (bh - len(lines) * lh) // 2 - max(1, px // 10)
            for line in lines:
                d.text((x0 + pad + (0 if cols else 3), ty), line, font=f, fill=ink)
                ty += lh
        return img

    def _loop(self):
        import win32con
        import win32gui

        from .marker import blit
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        wc = win32gui.WNDCLASS()
        wc.lpszClassName = "JarvisTranslate"
        wc.lpfnWndProc = lambda h, m, w, l: win32gui.DefWindowProc(h, m, w, l)
        wc.hInstance = win32gui.GetModuleHandle(None)
        try:
            win32gui.RegisterClass(wc)
        except Exception:
            pass
        ex = (win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_TOPMOST
              | win32con.WS_EX_TOOLWINDOW | 0x08000000)
        hwnd = win32gui.CreateWindowEx(ex, "JarvisTranslate", "JARVIS TRANSLATE", win32con.WS_POPUP, 0, 0, 1, 1, 0, 0,
                                       wc.hInstance, None)
        self.excluded = bool(user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE))
        job = None                 # {"img", "origin", "once", "fg", "t0", "clicks"}
        peek = False
        while True:
            try:
                cmd = self.q.get(timeout=0.03)
            except queue.Empty:
                cmd = None
            win32gui.PumpWaitingMessages()
            if cmd and cmd[0] == "hide":
                job = None
            elif cmd and cmd[0] == "show":
                _, rect, blocks, once, shot = cmd
                try:
                    img = self._render(rect, blocks, shot)
                except Exception as e:
                    self.core.log(f"Перевод: рисование: {e}", "warn")
                    img = None
                if img is not None:
                    job = {"img": img, "origin": rect[:2], "once": once, "fg": win32gui.GetForegroundWindow(),
                           "t0": time.time(), "btn": _down(VK["lbutton"]) or _down(VK["rbutton"])}
                    blit(hwnd, img, rect[:2], 255)
                    self.visible = True
            if job and job["once"]:
                btn = _down(VK["lbutton"]) or _down(VK["rbutton"])
                timeout = float(self.core.cfg.get("screentrans.seconds", 60))
                if (btn and not job["btn"]) or _down(VK["esc"]) or time.time() - job["t0"] > timeout or \
                        (win32gui.GetForegroundWindow() != job["fg"] and time.time() - job["t0"] > 0.6):
                    job = None
                else:
                    job["btn"] = btn
            if job:
                p = _down(VK["ctrl"]) and _down(VK["shift"])          # hold Ctrl+Shift: the original
                if p != peek:
                    peek = p
                    blit(hwnd, job["img"], job["origin"], 0 if p else 255)
            elif self.visible:
                win32gui.ShowWindow(hwnd, 0)
                self.visible = False
                peek = False


# ====================================================================== the area picker
def pick_area(timeout=30):
    """A dim full-screen layer: drag a rectangle with the mouse (Esc or a right click cancels). (l, t, r, b) or None."""
    import win32api
    import win32con
    import win32gui
    from PIL import Image, ImageDraw

    from .marker import blit
    res = {"rect": None, "done": threading.Event()}

    def run():
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
        vw, vh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
        st = {"a": None, "b": None}

        def draw():
            img = Image.new("RGBA", (vw, vh), (4, 10, 18, 110))
            if st["a"] and st["b"]:
                (x0, y0), (x1, y1) = st["a"], st["b"]
                box = (min(x0, x1) - vx, min(y0, y1) - vy, max(x0, x1) - vx, max(y0, y1) - vy)
                d = ImageDraw.Draw(img)
                d.rectangle(box, fill=(0, 0, 0, 1))
                d.rectangle(box, outline=(79, 216, 255, 255), width=2)
            else:
                d = ImageDraw.Draw(img)
                d.text((vw // 2 - 190 - 0, 40), "Выделите область мышью · Esc — отмена", fill=(220, 240, 255, 255))
            blit(hwnd, img, (vx, vy), 255)

        def proc(h, msg, w, lp):
            if msg == win32con.WM_LBUTTONDOWN:
                st["a"] = win32api.GetCursorPos()
                st["b"] = st["a"]
                win32gui.SetCapture(h)
            elif msg == win32con.WM_MOUSEMOVE and st["a"]:
                st["b"] = win32api.GetCursorPos()
                draw()
            elif msg == win32con.WM_LBUTTONUP and st["a"]:
                b = win32api.GetCursorPos()
                win32gui.ReleaseCapture()
                (x0, y0) = st["a"]
                r = (min(x0, b[0]), min(y0, b[1]), max(x0, b[0]), max(y0, b[1]))
                if r[2] - r[0] > 12 and r[3] - r[1] > 8:
                    res["rect"] = r
                win32gui.DestroyWindow(h)
            elif msg in (win32con.WM_RBUTTONUP,) or (msg == win32con.WM_KEYDOWN and w == win32con.VK_ESCAPE):
                win32gui.DestroyWindow(h)
            elif msg == win32con.WM_DESTROY:
                res["done"].set()
                win32gui.PostQuitMessage(0)
            elif msg == win32con.WM_SETCURSOR:
                win32gui.SetCursor(win32gui.LoadCursor(0, win32con.IDC_CROSS))
                return True
            return win32gui.DefWindowProc(h, msg, w, lp)

        wc = win32gui.WNDCLASS()
        wc.lpszClassName = "JarvisAreaPick"
        wc.lpfnWndProc = proc
        wc.hInstance = win32gui.GetModuleHandle(None)
        try:
            win32gui.RegisterClass(wc)
        except Exception:
            pass
        hwnd = win32gui.CreateWindowEx(win32con.WS_EX_LAYERED | win32con.WS_EX_TOPMOST | win32con.WS_EX_TOOLWINDOW,
                                       "JarvisAreaPick", "JARVIS AREA", win32con.WS_POPUP, vx, vy, vw, vh, 0, 0,
                                       wc.hInstance, None)
        draw()
        win32gui.ShowWindow(hwnd, 5)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        t0 = time.time()
        while not res["done"].is_set():
            win32gui.PumpWaitingMessages()
            if time.time() - t0 > timeout:
                win32gui.DestroyWindow(hwnd)
            time.sleep(0.01)

    th = threading.Thread(target=run, daemon=True, name="area-pick")
    th.start()
    res["done"].wait(timeout + 5)
    return res["rect"]


# ====================================================================== the system
class ScreenTranslator:
    def __init__(self, core):
        self.core = core
        self.cfg = core.cfg
        self.tr = Translator(core)
        self.overlay = Overlay(core)
        self.live = None              # {"hwnd", "rect", "speak", "tl", "stop": Event}
        self.last = []                # the last blocks with translations
        self._busy = threading.Lock()

    # ---------------------------------------------------------------- where
    def target(self, mode="window"):
        """(rect, hwnd): the active window (not Jarvis's own), or the monitor under the cursor."""
        import win32api
        import win32gui

        from . import mouse
        with mouse.physical():
            h = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(h) if h else ""
            own = title in mouse.OWN_TITLES or title.startswith("JARVIS")
            if mode == "window" and h and not own and not win32gui.IsIconic(h):
                l, t, r, b = win32gui.GetWindowRect(h)
                mon = win32api.GetMonitorInfo(win32api.MonitorFromWindow(h, 2))["Monitor"]
                rect = (max(l, mon[0]), max(t, mon[1]), min(r, mon[2]), min(b, mon[3]))   # maximized windows overhang
                if rect[2] - rect[0] > 50 and rect[3] - rect[1] > 30:
                    return rect, h
            mon = win32api.GetMonitorInfo(win32api.MonitorFromPoint(win32api.GetCursorPos(), 2))["Monitor"]
            return tuple(mon), None

    def lang(self):
        return self.cfg.get("screentrans.lang", "ru") or "ru"

    # ---------------------------------------------------------------- one picture
    def read(self, rect, tl):
        img = grab(rect)
        lines = ocr_image(img, rect[:2], "ru" if tl != "ru" else "en")
        blocks = [b for b in paragraphs(lines) if worth(b["text"], tl)]
        return img, blocks

    def translate_blocks(self, blocks, tl):
        got = self.tr.many([b["text"] for b in blocks], tl)
        for b in blocks:
            b["tr"] = got.get(b["text"], "")
        return [b for b in blocks if b["tr"] and b["tr"].strip().lower() != b["text"].strip().lower()]

    def once(self, mode="window", speak=False, tl=None, rect=None):
        """Translate what is on the screen now. Returns the phrase to say."""
        tl = tl or self.lang()
        if self.live:
            self.stop_live()
        if rect is None:
            if mode == "area":
                rect = pick_area()
                if not rect:
                    return "Отменено."
                hwnd = None
            else:
                rect, hwnd = self.target(mode)
        t0 = time.time()
        with self._busy:
            shot, blocks = self.read(rect, tl)
            if not blocks:
                return "Не вижу текста, который нужно переводить."
            blocks = self.translate_blocks(blocks, tl)
        if not blocks:
            return "Здесь уже всё на нужном языке."
        self.last = blocks
        self.overlay.show(rect, blocks, once=True, shot=shot)
        self.core.log(f"Перевод экрана: {len(blocks)} блоков за {time.time() - t0:.1f} с"
                      + (" (через ИИ)" if self.tr.last_engine == "ai" else ""))
        if speak:
            return self.core.summarize_for_voice(" ".join(b["tr"] for b in blocks), 400)
        return True

    def hide(self):
        self.overlay.hide()

    # ---------------------------------------------------------------- live
    def start_live(self, mode="window", speak=False, tl=None):
        tl = tl or self.lang()
        self.stop_live()
        if mode == "area":
            rect = pick_area()
            if not rect:
                return "Отменено."
            hwnd = None
        else:
            rect, hwnd = self.target(mode)
        self.live = {"hwnd": hwnd, "rect": rect, "speak": speak, "tl": tl, "stop": threading.Event(), "area": mode == "area"}
        threading.Thread(target=self._live_loop, args=(self.live,), daemon=True, name="translate-live").start()
        self.core.emit("screentrans", live=True)
        return ("Перевожу на лету" + (" и читаю вслух" if speak else "") + ". Скажите «хватит переводить», "
                f"чтобы закончить, или нажмите {self.cfg.get('screentrans.live_hotkey', 'ctrl+alt+l').replace('+', ' + ')}.")

    def stop_live(self):
        lv, self.live = self.live, None
        if lv:
            lv["stop"].set()
            self.overlay.hide()
            self.core.emit("screentrans", live=False)
            return True
        return False

    def _live_loop(self, lv):
        import win32gui

        from . import mouse
        prev_sig, prev_texts, said = None, set(), set()
        every = float(self.cfg.get("screentrans.every", 1.0))
        while not lv["stop"].is_set():
            try:
                if lv["hwnd"]:
                    if not win32gui.IsWindow(lv["hwnd"]):
                        break
                    if win32gui.IsIconic(lv["hwnd"]):
                        self.overlay.hide()
                        prev_sig = None
                        lv["stop"].wait(1)
                        continue
                    with mouse.physical():
                        lv["rect"] = win32gui.GetWindowRect(lv["hwnd"]) if not lv["area"] else lv["rect"]
                    if win32gui.GetForegroundWindow() != lv["hwnd"]:
                        self.overlay.hide()          # another window in front: nothing over it
                        prev_sig = None
                        lv["stop"].wait(0.5)
                        continue
                img = grab(lv["rect"])
                small = np.asarray(img.convert("L").resize((96, 54)), dtype=np.float32)
                if prev_sig is not None and np.abs(small - prev_sig).mean() < 1.5:
                    lv["stop"].wait(every)
                    continue
                prev_sig = small
                lines = ocr_image(img, lv["rect"][:2], "ru" if lv["tl"] != "ru" else "en")
                blocks = [b for b in paragraphs(lines) if worth(b["text"], lv["tl"])]
                texts = {b["text"] for b in blocks}
                if texts == prev_texts:
                    if blocks:
                        lv["stop"].wait(every)
                        continue
                prev_texts = texts
                blocks = self.translate_blocks(blocks, lv["tl"]) if blocks else []
                if lv["stop"].is_set():
                    break
                if blocks:
                    self.last = blocks
                    self.overlay.show(lv["rect"], blocks, once=False, shot=img)
                    if lv["speak"]:
                        new = [b["tr"] for b in blocks if b["text"] not in said]
                        said |= {b["text"] for b in blocks}
                        if new and not self.core.listener.is_speaking():
                            self.core.say(" ".join(new)[:400])
                else:
                    self.overlay.hide()
            except Exception as e:
                self.core.log(f"Живой перевод: {e}", "warn")
                lv["stop"].wait(2)
            lv["stop"].wait(every)
        if self.live is lv:
            self.live = None
            self.overlay.hide()
            self.core.emit("screentrans", live=False)

    # ---------------------------------------------------------------- hotkeys
    def hotkey_once(self):
        if self.overlay.visible and not self.live:
            self.hide()
            return
        threading.Thread(target=lambda: self._say_result(self.once()), daemon=True).start()

    def hotkey_live(self):
        if self.live:
            self.stop_live()
            self.core.say("Перевод выключен.")
            return
        threading.Thread(target=lambda: self._say_result(self.start_live()), daemon=True).start()

    def _say_result(self, r):
        if isinstance(r, str):
            self.core.say(r)

    # ---------------------------------------------------------------- RPC
    def rpc_screentrans_get(self):
        g = self.cfg.get
        return {"lang": self.lang(), "engine": g("screentrans.engine", "google"), "opacity": g("screentrans.opacity", 0.96),
                "style": g("screentrans.style", "lens"),
                "every": g("screentrans.every", 1.0), "seconds": g("screentrans.seconds", 60),
                "hotkey": g("screentrans.hotkey", "ctrl+alt+t"), "live_hotkey": g("screentrans.live_hotkey", "ctrl+alt+l"),
                "live": bool(self.live), "langs": LANGS, "cached": len(self.tr.cache), "excluded": self.overlay.excluded}

    def rpc_screentrans_set(self, **kw):
        for k in ("lang", "engine", "opacity", "every", "seconds", "hotkey", "live_hotkey", "style"):
            if k in kw and kw[k] is not None:
                self.cfg.set(f"screentrans.{k}", kw[k])
        return self.rpc_screentrans_get()

    def rpc_screentrans_run(self, mode="window", live=False, delay=3):
        def go():
            time.sleep(float(delay))           # time to switch to the window to translate
            r = self.start_live(mode) if live else self.once(mode)
            self._say_result(r)
        threading.Thread(target=go, daemon=True).start()
        return True

    def rpc_screentrans_stop(self):
        self.stop_live()
        self.hide()
        return True


# ====================================================================== voice rules
def register(R, core):
    ST = lambda: core.screentrans
    WHAT = r"(?:этот\s+|это\s+)?(?:экран|окно|страниц\w*|игр\w*|текст\s+на\s+экране|что\s+на\s+экране|с\s+экрана|меню|диалог\w*|субтитр\w*)"
    TO = r"(?:\s+на\s+" + LANG_RX + r"(?:\s+язык)?)?"

    def run(fn, *a, **kw):
        def go():
            try:
                r = fn(*a, **kw)
            except Exception as e:
                core.log(f"Перевод экрана: {e}", "warn")
                r = "Не получилось перевести: " + str(e)[:120]
            if isinstance(r, str):
                core.say(r)
        threading.Thread(target=go, daemon=True).start()
        return True

    @R(r"^переведи\s+" + WHAT + TO + r"(?:\s+(и\s+прочитай|вслух|и\s+озвучь))?$|^переведи\s+(и\s+прочитай|вслух)\s+" + WHAT + TO + "$")
    def _once(m, t):
        g = m.groups()
        tl = lang_code(g[0] or g[3]) if (g[0] or g[3]) else None
        return run(ST().once, "window", speak=bool(g[1] or g[2]), tl=tl)

    @R(r"^переведи\s+(?:эту\s+|вот\s+эту\s+)?(?:область|часть\s+экрана|кусок(?:\s+экрана)?|выделенную\s+область)" + TO + r"$")
    def _area(m, t):
        return run(ST().once, "area", tl=lang_code(m.group(1)) if m.group(1) else None)

    @R(r"^(?:включи|запусти|начни)\s+(?:живой\s+|постоянный\s+|авто\w*\s+)?перевод(?:\s+экрана|\s+игры|\s+субтитров)?(\s+вслух)?$|"
       r"^переводи\s+(?:мне\s+)?(?:" + WHAT + r"|все|всё|на\s+лету|постоянно)" + TO + r"(\s+вслух|\s+и\s+читай)?$")
    def _live(m, t):
        g = m.groups()
        speak = bool(g[0] or g[2])
        return run(ST().start_live, "window", speak=speak, tl=lang_code(g[1]) if g[1] else None)

    @R(r"^переводи\s+(?:эту\s+|вот\s+эту\s+)?(?:область|часть\s+экрана|кусок)(\s+вслух)?$")
    def _live_area(m, t):
        return run(ST().start_live, "area", speak=bool(m.group(1)))

    @R(r"^(?:выключи|останови|отключи|убери|закрой|стоп|хватит)\s+(?:живой\s+)?перевод\w*(?:\s+экрана)?$|^хватит\s+переводить$|"
       r"^(?:не\s+переводи|перестань\s+переводить)$")
    def _stop(m, t):
        was = ST().stop_live()
        ST().hide()
        return "Перевод выключен." if was else "Убрал перевод."

    @R(r"^(?:скопируй|копируй)\s+перевод$")
    def _copy(m, t):
        from . import system as S
        if not ST().last:
            return "Пока нечего копировать: скажите «переведи экран»."
        S.clipboard_set("\n\n".join(b["tr"] for b in ST().last))
        return "Скопировал перевод."

    @R(r"^(?:прочитай|озвучь)\s+перевод$")
    def _read(m, t):
        if not ST().last:
            return "Пока нечего читать: скажите «переведи экран»."
        return core.summarize_for_voice(" ".join(b["tr"] for b in ST().last), 500)
