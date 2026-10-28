"""Mouse and "what is on the screen", rebuilt.

The old way (pyautogui + OCR of the primary monitor) missed icons and anything without text, could not pick
"the second link", clicked Jarvis's own subtitles, and pyautogui's fail-safe threw an exception whenever the
cursor sat in a screen corner. Now:

- the cursor moves and clicks through SendInput, smoothly, on every monitor, in physical pixels
  (each call switches its thread to per-monitor DPI awareness, so scaled monitors line up);
- targets come from UI Automation first: real buttons, links, fields, tabs, menu items with their names and
  rectangles, in the active window and on the taskbar; then from Windows OCR over all monitors;
- "вторая ссылка", "последняя кнопка", "кнопка Отправить", "поле поиска" pick by role and reading order;
- when nothing matches (icons, "крестик", "аватарка"), a vision model looks at a screenshot with numbered
  marks and says which one (any OpenAI-compatible or Anthropic model that accepts images);
- a point is clicked only if it really belongs to the window in front (nothing hidden behind another window).
"""
import contextlib
import ctypes
import io
import json
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from ctypes import wintypes

user32 = ctypes.WinDLL("user32")  # private handle: these argtypes must not leak into other libraries
user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]

OWN_TITLES = ("J.A.R.V.I.S.", "JARVIS HUD", "JARVIS MENU")


@contextlib.contextmanager
def physical():
    """Per-monitor DPI awareness for this thread: cursor, window rects, OCR and UIA all in physical pixels."""
    old = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    try:
        yield
    finally:
        if old:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(old))


# ===================================================================== SendInput
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _IU(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _IU)]


MOVE, LDOWN, LUP, RDOWN, RUP, MDOWN, MUP, WHEEL, HWHEEL, ABS, VDESK = (
    0x1, 0x2, 0x4, 0x8, 0x10, 0x20, 0x40, 0x800, 0x1000, 0x8000, 0x4000)
BTN = {"left": (LDOWN, LUP), "right": (RDOWN, RUP), "middle": (MDOWN, MUP)}


def _send(flags, dx=0, dy=0, data=0):
    inp = INPUT(type=0, u=_IU(mi=MOUSEINPUT(dx, dy, ctypes.c_ulong(data & 0xFFFFFFFF).value, flags, 0, 0)))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def virtual_screen():
    with physical():
        return (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77),
                user32.GetSystemMetrics(78), user32.GetSystemMetrics(79))


def position():
    with physical():
        p = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(p))
        return p.x, p.y


def _abs_move(x, y):
    vx, vy, vw, vh = virtual_screen()
    nx = int(round((x - vx) * 65535 / max(1, vw - 1)))
    ny = int(round((y - vy) * 65535 / max(1, vh - 1)))
    _send(MOVE | ABS | VDESK, nx, ny)


def move(x, y, duration=None):
    """Smooth move (ease in-out). duration None: by distance, 0.08..0.32 s."""
    with physical():
        vx, vy, vw, vh = virtual_screen()
        x = max(vx, min(vx + vw - 1, int(x)))
        y = max(vy, min(vy + vh - 1, int(y)))
        x0, y0 = position()
        dist = math.hypot(x - x0, y - y0)
        if duration is None:
            duration = 0 if dist < 4 else min(0.32, 0.08 + dist / 6000)
        steps = max(1, int(duration / 0.012))
        for i in range(1, steps + 1):
            k = i / steps
            e = k * k * (3 - 2 * k)
            _abs_move(x0 + (x - x0) * e, y0 + (y - y0) * e)
            if steps > 1:
                time.sleep(duration / steps)
        user32.SetCursorPos(x, y)  # land exactly


def move_rel(dx, dy):
    x, y = position()
    move(x + dx, y + dy)


def click(x=None, y=None, button="left", double=False):
    if x is not None and y is not None:
        move(x, y)
        time.sleep(0.04)
    down, up = BTN.get(button, BTN["left"])
    for i in range(2 if double else 1):
        _send(down)
        time.sleep(0.03)
        _send(up)
        if double and i == 0:
            time.sleep(0.07)


def press(button="left"):
    _send(BTN.get(button, BTN["left"])[0])


def release(button="left"):
    _send(BTN.get(button, BTN["left"])[1])


def drag(x1, y1, x2, y2, duration=0.45, button="left"):
    move(x1, y1)
    time.sleep(0.05)
    press(button)
    time.sleep(0.08)
    with physical():
        steps = max(8, int(duration / 0.015))
        for i in range(1, steps + 1):
            k = i / steps
            e = k * k * (3 - 2 * k)
            _abs_move(x1 + (x2 - x1) * e, y1 + (y2 - y1) * e)
            time.sleep(duration / steps)
    time.sleep(0.08)
    release(button)


def scroll(direction="down", notches=5, x=None, y=None):
    """Wheel scroll in small steps (smooth in browsers). left/right scroll sideways."""
    if x is not None and y is not None:
        move(x, y)
    horiz = direction in ("left", "right")
    sign = -1 if direction in ("down", "left") else 1
    if horiz:
        sign = -sign
    for _ in range(max(1, int(notches))):
        _send(HWHEEL if horiz else WHEEL, data=sign * 120)
        time.sleep(0.025)


def corner(where):
    """'левый верхний', 'правый нижний', 'центр'... of the monitor under the cursor."""
    import win32api
    with physical():
        mon = win32api.MonitorFromPoint(position(), 2)
        l, t, r, b = win32api.GetMonitorInfo(mon)["Monitor"]
    pad = 8
    xs = {"лев": l + pad, "прав": r - pad}
    ys = {"верх": t + pad, "ниж": b - pad}
    x = next((v for k, v in xs.items() if k in where), (l + r) // 2)
    y = next((v for k, v in ys.items() if k in where), (t + b) // 2)
    move(x, y)
    return x, y


# ===================================================================== windows
def _root(h):
    return user32.GetAncestor(h, 2) if h else 0  # GA_ROOT


def foreground():
    """The window the user works in (never Jarvis's own)."""
    import win32gui
    h = win32gui.GetForegroundWindow()
    if win32gui.GetWindowText(h) not in OWN_TITLES:
        return h
    # Jarvis is in front: take the next real window in z-order
    hh = win32gui.GetWindow(h, 2)  # GW_HWNDNEXT
    while hh:
        if win32gui.IsWindowVisible(hh) and not win32gui.IsIconic(hh) and win32gui.GetWindowText(hh).strip() \
                and win32gui.GetWindowText(hh) not in OWN_TITLES:
            return hh
        hh = win32gui.GetWindow(hh, 2)
    return h


def own_rects():
    import win32gui
    out = []
    with physical():
        for title in OWN_TITLES:
            h = win32gui.FindWindow(None, title)
            if h and win32gui.IsWindowVisible(h) and not win32gui.IsIconic(h):
                out.append(win32gui.GetWindowRect(h))
    return out


def visible_at(x, y, hwnd=None):
    """True if the point is not covered by another window (or by Jarvis)."""
    with physical():
        h = _root(user32.WindowFromPoint(wintypes.POINT(int(x), int(y))))
    if any(l <= x <= r and t <= y <= b for l, t, r, b in own_rects()):
        return False
    return hwnd is None or h == _root(hwnd) or not h


# ===================================================================== UI Automation
ROLE = {50000: "button", 50002: "checkbox", 50003: "combobox", 50004: "edit", 50005: "link", 50006: "image",
        50007: "listitem", 50011: "menuitem", 50013: "radio", 50015: "slider", 50016: "spinner", 50019: "tab",
        50020: "text", 50024: "treeitem", 50029: "dataitem", 50031: "splitbutton", 50035: "headeritem",
        50018: "tab", 50010: "menubar", 50009: "menu", 50033: "pane", 50026: "group", 50030: "document"}
CLICKABLE = {50000, 50002, 50003, 50004, 50005, 50006, 50007, 50011, 50013, 50015, 50019, 50020, 50024, 50029, 50031, 50035}
ROLE_RU = {"button": "кнопка", "checkbox": "флажок", "combobox": "список", "edit": "поле", "link": "ссылка",
           "image": "картинка", "listitem": "элемент", "menuitem": "пункт меню", "radio": "переключатель",
           "slider": "ползунок", "tab": "вкладка", "text": "надпись", "treeitem": "элемент дерева",
           "dataitem": "строка", "splitbutton": "кнопка", "headeritem": "заголовок", "ocr": "текст"}


class _UIA:
    def __init__(self):
        self.pool = None
        self.uia = None
        self.mod = None
        self._lock = threading.Lock()

    def _init_thread(self):
        # MTA: UIA clients must not block on STA message pumps. comtypes initializes COM in the thread that
        # imports it first (STA by default), and a later CoInitializeEx(MTA) then fails — so if this thread is
        # the first importer, it imports comtypes asking for MTA and the flag is removed right after
        import sys
        if "comtypes" not in sys.modules:
            had, old = hasattr(sys, "coinit_flags"), getattr(sys, "coinit_flags", None)
            sys.coinit_flags = 0
            try:
                import comtypes  # noqa: F401
            finally:
                if had:
                    sys.coinit_flags = old
                else:
                    del sys.coinit_flags
        else:
            ctypes.windll.ole32.CoInitializeEx(None, 0)
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))

    def _ensure(self):
        with self._lock:
            if self.pool is None:
                self.pool = ThreadPoolExecutor(1, thread_name_prefix="uia", initializer=self._init_thread)
                self.uia = None

    def run(self, fn, timeout=4.0):
        self._ensure()
        fut = self.pool.submit(fn)
        try:
            return fut.result(timeout=timeout)
        except FutTimeout:
            # a hung application blocks UIA calls: drop the stuck worker and start a fresh one next time
            with self._lock:
                self.pool = None
            raise TimeoutError("UI Automation не ответил")

    def get(self):
        if self.uia is None:
            import comtypes.client
            self.mod = comtypes.client.GetModule("UIAutomationCore.dll")
            self.uia = comtypes.client.CreateObject(self.mod.CUIAutomation, interface=self.mod.IUIAutomation)
            cr = self.uia.CreateCacheRequest()
            for pid in (30005, 30003, 30001, 30010, 30011, 30013, 30022):
                cr.AddProperty(pid)
            self.cache = cr
            self.cond = self.uia.CreatePropertyCondition(30022, False)  # IsOffscreen == False
        return self.uia


UIA = _UIA()


def _uia_elements(hwnd, limit=900):
    def work():
        u = UIA.get()
        out = []
        for attempt in range(2):
            try:
                arr = u.ElementFromHandle(hwnd).FindAllBuildCache(4, UIA.cond, UIA.cache)
                break
            except Exception:
                if attempt:
                    return out
                time.sleep(0.2)
        n = min(arr.Length, limit)
        for i in range(n):
            try:
                e = arr.GetElement(i)
                ct = e.CachedControlType
                if ct not in CLICKABLE:
                    continue
                r = e.CachedBoundingRectangle
                if r.right - r.left < 3 or r.bottom - r.top < 3:
                    continue
                name = (e.CachedName or "").strip()
                if not name:
                    name = (e.CachedHelpText or "").strip()
                out.append({"name": name, "role": ROLE.get(ct, "other"), "rect": (r.left, r.top, r.right, r.bottom),
                            "enabled": bool(e.CachedIsEnabled), "aid": e.CachedAutomationId or "", "src": "uia",
                            "hwnd": hwnd})
            except Exception:
                continue
        return out
    try:
        return UIA.run(work)
    except Exception:
        return []


def uia_elements(hwnd=None, taskbar=False):
    import win32gui
    from . import system as S
    hwnd = hwnd or foreground()
    els = _uia_elements(hwnd) if hwnd else []
    if hwnd:
        try:
            exe = S.active_exe()
        except Exception:
            exe = ""
        # Chromium switches its web accessibility tree on after the first UIA request: ask again
        if exe in S.BROWSERS and sum(1 for e in els if e["role"] == "link") < 3:
            time.sleep(0.5)
            els = _uia_elements(hwnd) or els
    if taskbar:
        tb = win32gui.FindWindow("Shell_TrayWnd", None)
        if tb:
            els += [dict(e, taskbar=True) for e in _uia_elements(tb, 300)]
    own = own_rects()
    vx, vy, vw, vh = virtual_screen()
    keep = []
    for e in els:
        l, t, r, b = e["rect"]
        cx, cy = (l + r) // 2, (t + b) // 2
        if not (vx <= cx < vx + vw and vy <= cy < vy + vh):
            continue
        if any(ol <= cx <= orr and ot <= cy <= ob for ol, ot, orr, ob in own):
            continue
        e["x"], e["y"] = cx, cy
        keep.append(e)
    return keep


# ===================================================================== OCR
_ocr_cache = {"t": 0.0, "v": None}


def ocr(max_age=1.2):
    """Windows OCR (ru + en) over all monitors, upscaled x2 on ordinary screens: small text reads much better.
    [{text, x, y, w, h, words: [...]}] in screen pixels."""
    if _ocr_cache["v"] is not None and time.time() - _ocr_cache["t"] < max_age:
        return _ocr_cache["v"]
    import asyncio
    import mss
    from PIL import Image
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    with physical():
        with mss.MSS() as s:
            mon = s.monitors[0]
            shot = s.grab(mon)
    img = Image.frombytes("RGB", shot.size, shot.rgb)
    scale = 2 if img.width <= 2600 else 1
    try:
        limit = OcrEngine.max_image_dimension
    except Exception:
        limit = 10000
    while scale > 1 and max(img.width, img.height) * scale > limit:
        scale -= 0.5
    if scale != 1:
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.BICUBIC)
    data = img.convert("RGBA").tobytes("raw", "BGRA")

    async def run():
        eng = OcrEngine.try_create_from_language(Language("ru")) or OcrEngine.try_create_from_user_profile_languages()
        w = DataWriter()
        w.write_bytes(data)
        sb = SoftwareBitmap.create_copy_from_buffer(w.detach_buffer(), BitmapPixelFormat.BGRA8, img.width, img.height)
        return await eng.recognize_async(sb)

    res = asyncio.run(run())
    out = []
    for ln in res.lines:
        words = []
        for wd in ln.words:
            r = wd.bounding_rect
            words.append({"text": wd.text, "x": int(r.x / scale) + mon["left"], "y": int(r.y / scale) + mon["top"],
                          "w": max(1, int(r.width / scale)), "h": max(1, int(r.height / scale))})
        if words:
            x0 = min(w["x"] for w in words); y0 = min(w["y"] for w in words)
            x1 = max(w["x"] + w["w"] for w in words); y1 = max(w["y"] + w["h"] for w in words)
            out.append({"text": ln.text, "x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0, "words": words})
    _ocr_cache.update(t=time.time(), v=out)
    return out


def screen_lines(limit=160):
    """OCR lines outside Jarvis's windows with click points (what the AI reads)."""
    own = own_rects()
    out = []
    for ln in ocr():
        cx, cy = ln["x"] + ln["w"] // 2, ln["y"] + ln["h"] // 2
        if any(l <= cx <= r and t <= cy <= b for l, t, r, b in own):
            continue
        out.append({"text": ln["text"], "x": cx, "y": cy})
    return out[:limit]


def _ocr_candidates():
    own = own_rects()
    cands = []
    for ln in ocr():
        lcx, lcy = ln["x"] + ln["w"] // 2, ln["y"] + ln["h"] // 2
        if any(l <= lcx <= r and t <= lcy <= b for l, t, r, b in own):
            continue
        box = lambda b: {"rect": (b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]),
                         "x": b["x"] + b["w"] // 2, "y": b["y"] + b["h"] // 2, "role": "ocr", "src": "ocr"}
        cands.append({"name": ln["text"], **box(ln)})
        ws = ln["words"]
        for w in ws:
            cands.append({"name": w["text"], **box(w), "word": True})
        for i in range(len(ws)):
            for j in range(i + 2, min(len(ws), i + 6) + 1):
                run = ws[i:j]
                b = {"x": run[0]["x"], "y": min(r["y"] for r in run), "w": run[-1]["x"] + run[-1]["w"] - run[0]["x"],
                     "h": max(r["h"] for r in run)}
                cands.append({"name": " ".join(r["text"] for r in run), **box(b)})
    return cands


# ===================================================================== matching
ORDINALS = {"перв": 1, "втор": 2, "трет": 3, "четверт": 4, "пят": 5, "шест": 6, "седьм": 7, "восьм": 8, "девят": 9,
            "десят": 10, "последн": -1, "предпоследн": -2}
ROLE_WORDS = [  # spoken word -> roles it means
    (r"кнопк\w*", {"button", "splitbutton"}), (r"ссылк\w*|линк\w*", {"link"}), (r"вкладк\w*|таб\w*", {"tab"}),
    (r"пункт\w*(?:\s+меню)?|меню", {"menuitem"}), (r"пол[еяю]\w*|строк\w*\s+(?:поиска|ввода|адреса)|поиск\w*|адресн\w+\s+строк\w*", {"edit", "combobox"}),
    (r"галочк\w*|флажок|флажк\w*|чекбокс\w*", {"checkbox"}), (r"переключател\w*|радио\w*", {"radio"}),
    (r"иконк\w*|значок|значк\w*|картинк\w*|изображени\w*", {"image", "button"}), (r"папк\w*|файл\w*", {"listitem", "treeitem", "dataitem"}),
    (r"видео|ролик\w*|результат\w*|стать\w*", {"link"}), (r"элемент\w*|строчк\w*", {"listitem", "dataitem", "treeitem"}),
]


def parse_target(spoken):
    """'вторую ссылку' -> ('', {'link'}, 2); 'кнопку Отправить' -> ('отправить', {'button',...}, None)."""
    s = spoken.lower().replace("ё", "е").strip(" .,!?«»\"")
    nth = None
    m = re.match(r"^(?:на\s+|по\s+)?(перв|втор|трет|четверт|пят|шест|седьм|восьм|девят|десят|последн|предпоследн)\w*\s+", s)
    if m:
        nth = ORDINALS[m.group(1)]
        s = s[m.end():]
    m = re.match(r"^(?:номер\s+)?(\d{1,2})(?:-?(?:ю|ую|й|ая|ой))?\s+", s)
    if nth is None and m and re.search(r"ссылк|кнопк|вкладк|видео|результат|пункт|элемент|файл|папк", s):
        nth = int(m.group(1))
        s = s[m.end():]
    roles = set()
    for rx, rs in ROLE_WORDS:
        mm = re.match(rf"^(?:{rx})(?:\s+|$)", s)
        if mm:
            roles = rs
            s = s[mm.end():]
            break
    s = re.sub(r"^(?:с\s+надписью|с\s+текстом|под\s+названием|называется|которая|который)\s+", "", s).strip()
    return s, roles, nth


def _norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or "").lower().replace("ё", "е"))).strip()


_lemma_cache = {}


def _lemmas(s):
    from .nlu import _parse
    out = []
    for w in _norm(s).split():
        if w not in _lemma_cache:
            ps = _parse(w)
            _lemma_cache[w] = ps[0].normal_form if ps else w
        out.append(_lemma_cache[w])
    return out


def score(q, name):
    from rapidfuzz import fuzz
    n = _norm(name)
    if not n or not q:
        return 0
    if n == q:
        return 100
    if len(n) > 4 * len(q) + 40:  # a whole paragraph that merely contains the word
        return 0
    if n.startswith(q + " ") or n.startswith(q):
        return 94 - min(15, (len(n) - len(q)) * 0.4)
    if re.search(rf"\b{re.escape(q)}\b", n):
        return 90 - min(20, (len(n) - len(q)) * 0.5)
    lq, ln_ = _lemmas(q), _lemmas(n)
    if lq and lq == ln_[:len(lq)]:
        return 93 - min(10, (len(ln_) - len(lq)) * 2)
    if lq and all(w in ln_ for w in lq):
        return 86 - min(15, (len(ln_) - len(lq)) * 2)
    if q in n:
        return 80 - min(20, (len(n) - len(q)) * 0.5)
    if abs(len(n) - len(q)) <= max(2, len(q) * 0.3):
        r = fuzz.ratio(q, n)
        # OCR slips ("Отпрaвить", "Настроики") stay close; "подписки" vs "поиск" (77) is a different word
        return r * 0.92 if r >= 82 else 0
    return 0


def _reading_order(items):
    return sorted(items, key=lambda e: (round(e["rect"][1] / 14), e["rect"][0]))


def _variants(q):
    """What the screen may show for a spoken label: as said, with digits ("пять и пять" -> "5.5"), in Latin
    letters ("опус" -> "opus") and both ("Opus 5.5"). Speech has no digits and no Latin."""
    from .planner import words_to_digits
    from .system import translit
    d = words_to_digits(q)
    d = re.sub(r"(\d+)\s+(?:и|точка|запятая)\s+(\d+)", r"\1.\2", d)
    out = []
    for v in (q, d, translit(q), translit(d)):
        v = _norm(v)
        if v and v not in out:
            out.append(v)
    return out


def find(spoken, near_role=None, taskbar=None, allow_ocr=True, small=False):
    """Best on-screen target for a spoken description. Returns a dict with x, y, name, role, src or None.
    small: prefer tight targets (the marker draws around the rectangle: a whole panel named alike is wrong)."""
    q, roles, nth = parse_target(spoken)
    roles = roles or (near_role or set())
    qn = _norm(q)
    qvs = _variants(q) if qn else []

    def best_score(name):
        return max((score(v, name) for v in qvs), default=0)

    def size_penalty(e):
        if not small or not e.get("rect"):
            return 0
        l, t, r, b = e["rect"]
        return 22 if (b - t) > 160 or (r - l) > 900 else 8 if (b - t) > 80 else 0
    want_taskbar = taskbar if taskbar is not None else bool(re.search(r"панел\w*\s+задач|трей|пуск", spoken.lower()))
    with physical():
        fg = foreground()
        els = [e for e in uia_elements(fg, taskbar=want_taskbar or not qn) if e.get("enabled", True)]
        if want_taskbar:
            qn = re.sub(r"\s*(?:на|в)\s+(?:панели\s+задач|трее)\s*", " ", qn).strip()

        # no label: "вторая ссылка", "последняя кнопка"
        if not qn:
            pool = [e for e in els if (not roles or e["role"] in roles) and e["name"] and not e.get("taskbar")]
            if roles == {"link"} and re.search(r"видео|ролик|результат|стать", spoken.lower()):
                # results and videos: real titles in the page body — not the logo, the menu, @channels or #tags
                import win32gui
                try:
                    l, t, r, b = win32gui.GetWindowRect(fg)
                except Exception:
                    t, b = 0, 1080
                body_top = t + (b - t) * 0.14
                content = [e for e in pool if len(e["name"]) >= 20 and not e["name"].startswith(("@", "#"))
                           and e["rect"][1] > body_top and not re.search(r"(?i)главная|youtube$|^подпис|^shorts", e["name"])]
                pool = content or pool
            elif roles == {"link"}:
                long_ = [e for e in pool if len(e["name"]) >= 14]
                pool = long_ or pool
            pool = _reading_order(pool)
            if not pool:
                return None
            i = (nth or 1)
            pick = pool[i - 1] if 0 < i <= len(pool) else pool[i] if -len(pool) <= i < 0 else None
            return pick

        scored = []
        for e in els:
            s = max(best_score(e["name"]), best_score(e.get("aid", "")) - 10)
            if s <= 0:
                continue
            s -= size_penalty(e)
            if roles:
                s += 6 if e["role"] in roles else -18
            if e["role"] == "text":
                s -= 4  # a label: the control next to it is usually better, but a text link is fine
            if e.get("taskbar") and not want_taskbar:
                s -= 8
            scored.append((s, e))
        best = max((s for s, _ in scored), default=0)
        if allow_ocr and best < 88:
            for c in _ocr_candidates():
                s = best_score(c["name"]) - (2 if c.get("word") else 0)
                if roles and not roles & {"link", "button", "listitem", "menuitem", "tab"}:
                    s -= 10
                if s > 0:
                    scored.append((s, c))
        if not scored:
            return None
        scored.sort(key=lambda x: -x[0])
        top = scored[0][0]
        if top < 70:
            return None
        if nth:
            same = _reading_order([e for s, e in scored if s >= top - 6])
            # one control is often found twice (UIA + OCR): keep distinct places
            uniq = []
            for e in same:
                if all(abs(e["x"] - u["x"]) > 12 or abs(e["y"] - u["y"]) > 10 for u in uniq):
                    uniq.append(e)
            if nth > 0 and nth <= len(uniq):
                return uniq[nth - 1]
            if nth < 0 and -nth <= len(uniq):
                return uniq[nth]
        hit = scored[0][1]
        if hit["src"] == "uia" and not visible_at(hit["x"], hit["y"], hit.get("hwnd")):
            # covered by another window: prefer the next visible candidate
            for s, e in scored[1:]:
                if s >= top - 10 and visible_at(e["x"], e["y"], e.get("hwnd")):
                    return e
        return hit


# ===================================================================== vision
_last_marks = {"t": 0.0, "items": []}


def snapshot_elements(limit=90):
    """Numbered interactive elements of the active window (+ taskbar) for the AI: [{id, role, name, x, y}]."""
    with physical():
        els = [e for e in uia_elements(foreground(), taskbar=True) if e["role"] != "text" or len(e["name"]) < 60]
    els = _reading_order([e for e in els if e["name"]])
    els = [e for e in els if not e.get("taskbar")][:limit - 20] + [e for e in els if e.get("taskbar")][:20]
    items = [{"id": i + 1, "role": ROLE_RU.get(e["role"], e["role"]), "name": e["name"][:80], "x": e["x"], "y": e["y"]}
             for i, e in enumerate(els)]
    _last_marks.update(t=time.time(), items=items)
    return items


def element_by_id(i):
    if time.time() - _last_marks["t"] > 600:
        return None
    return next((e for e in _last_marks["items"] if e["id"] == int(i)), None)


def _screenshot_png(max_w=1400, marks=None):
    import mss
    from PIL import Image, ImageDraw, ImageFont
    with physical():
        with mss.MSS() as s:
            mon = s.monitors[0]
            shot = s.grab(mon)
    img = Image.frombytes("RGB", shot.size, shot.rgb)
    k = min(1.0, max_w / img.width)
    if k < 1:
        img = img.resize((int(img.width * k), int(img.height * k)), Image.LANCZOS)
    if marks:
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arialbd.ttf", 13)
        except Exception:
            font = ImageFont.load_default()
        for m in marks:
            l, t, r, b = [(v - o) * k for v, o in zip(m["rect"], (mon["left"], mon["top"], mon["left"], mon["top"]))]
            d.rectangle((l, t, r, b), outline=(255, 0, 180), width=2)
            label = str(m["mark"])
            tw = d.textlength(label, font=font)
            d.rectangle((l, t, l + tw + 4, t + 15), fill=(255, 0, 180))
            d.text((l + 2, t), label, fill="white", font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue(), k, mon


def vision_find(core, description):
    """Ask the vision model where the described thing is. Returns a hit dict or None."""
    from .llm import LLMError, vision_ask, vision_supported
    prov = core.cfg.provider()
    if not vision_supported(prov):
        return None
    with physical():
        els = [e for e in uia_elements(foreground(), taskbar=True) if e["role"] != "text"]
    els = _reading_order(els)[:140]
    marks = [dict(e, mark=i + 1) for i, e in enumerate(els)]
    png, k, mon = _screenshot_png(marks=marks)
    prompt = (f"Это скриншот экрана пользователя ({int(mon['width'] * k)}x{int(mon['height'] * k)} пикселей). "
              f"Розовыми рамками с номерами отмечены элементы интерфейса. Пользователь хочет кликнуть: «{description}».\n"
              "Ответь ТОЛЬКО JSON без пояснений: {\"mark\": N}, если нужный элемент отмечен номером; иначе "
              "{\"x\": X, \"y\": Y} — точка клика в пикселях этой картинки; если такого на экране нет — {\"none\": true}.")
    try:
        ans = vision_ask(prov, prompt, png, max_tokens=60)
    except LLMError as e:
        core.log(f"Зрение: {e}", "warn")
        return None
    except Exception as e:
        core.log(f"Зрение: {e}", "warn")
        return None
    m = re.search(r"\{.*?\}", ans or "", re.S)
    if not m:
        return None
    try:
        j = json.loads(m.group(0))
    except Exception:
        return None
    if j.get("none"):
        return None
    if j.get("mark"):
        e = next((x for x in marks if x["mark"] == int(j["mark"])), None)
        return dict(e, src="vision") if e else None
    if "x" in j and "y" in j:
        x = int(float(j["x"]) / k) + mon["left"]
        y = int(float(j["y"]) / k) + mon["top"]
        return {"x": x, "y": y, "name": description, "role": "vision", "src": "vision", "rect": (x - 4, y - 4, x + 4, y + 4)}
    return None


def describe_screen(core, question):
    """Answer a question about the screen with a real picture (OpenAI-compatible / Anthropic vision models)."""
    from .llm import vision_ask, vision_supported
    prov = core.cfg.provider()
    if not vision_supported(prov):
        return None
    png, _, _ = _screenshot_png(max_w=1600)
    sys_p = (f"Ты — Джарвис, голосовой ассистент. Отвечай по-русски, кратко (1-3 предложения), без markdown: "
             f"ответ будет озвучен. Обращайся «{core.cfg.get('user.address') or 'сэр'}».")
    try:
        return (vision_ask(prov, question, png, max_tokens=500, system=sys_p) or "").strip() or None
    except Exception as e:
        core.log(f"Зрение: {e}", "warn")
        return None


# ===================================================================== high level
def locate(core, spoken, allow_vision=True):
    """find() and, failing that, the vision model."""
    hit = None
    try:
        hit = find(spoken)
    except Exception as e:
        core.log(f"Поиск на экране: {e}", "warn")
    if not hit and allow_vision and core.cfg.get("mouse.vision", True):
        hit = vision_find(core, spoken)
    return hit


def click_target(core, spoken, button="left", double=False, allow_vision=True):
    hit = locate(core, spoken, allow_vision)
    if not hit:
        return None
    click(hit["x"], hit["y"], button=button, double=double)
    core.journal.record(f"кликнул по «{(hit.get('name') or spoken)[:40]}»", None, "click")
    return hit


def type_into(core, field, text, enter=False, replace=True):
    """'введи котики в поле поиска': click the field, (select all), type, (Enter)."""
    from . import system as S
    hit = None
    if field:
        q, roles, nth = parse_target(field)
        hit = find(field, near_role={"edit", "combobox"})
        if not hit and core.cfg.get("mouse.vision", True):
            hit = vision_find(core, f"поле ввода «{field}»")
        if not hit:
            return None
        click(hit["x"], hit["y"])
        time.sleep(0.15)
    if replace:
        S.hotkey("ctrl+a")
        time.sleep(0.05)
    S.type_text(text)
    if enter:
        time.sleep(0.05)
        S.hotkey("enter")
    return hit or {"name": "активное поле"}


def scroll_to(core, spoken, direction="down", max_steps=14):
    """Scroll until the thing is on the screen, then point at it."""
    for i in range(max_steps):
        _ocr_cache["v"] = None
        hit = None
        try:
            hit = find(spoken)
        except Exception:
            pass
        if hit and visible_at(hit["x"], hit["y"]):
            move(hit["x"], hit["y"])
            return hit
        with physical():
            fg = foreground()
        import win32gui
        try:
            l, t, r, b = win32gui.GetWindowRect(fg)
            scroll(direction, 5, (l + r) // 2, (t + b) // 2)
        except Exception:
            scroll(direction, 5)
        time.sleep(0.35)
    return None


def drag_between(core, src, dst):
    a = locate(core, src)
    b = locate(core, dst)
    if not a or not b:
        return None
    drag(a["x"], a["y"], b["x"], b["y"])
    return a, b


# ===================================================================== voice rules
def register(R, core):
    def addr():
        return core.cfg.get("user.address") or "сэр"

    def bg(fn, fail):
        """Screen work takes a moment: do it off the voice thread and speak only on failure."""
        def go():
            try:
                ok = fn()
            except Exception as e:
                core.log(f"Мышь: {e}", "warn")
                ok = None
            if not ok and fail:
                core.say(fail)
        threading.Thread(target=go, daemon=True).start()
        return True

    CLICK = r"(?:нажми|кликни|клацни|клик|щелкни|ткни|тыкни|жми)"
    MODE = r"(?:(дважды|два\s+раза|двойным(?:\s+кликом)?|правой(?:\s+кнопкой)?(?:\s+мыши)?|средней(?:\s+кнопкой)?)\s+)?"

    @R(rf"^{CLICK}\s+{MODE}(?:на|по)\s+(.+)$")
    def _click_on(m, t):
        mode, label = m.group(1) or "", m.group(2).strip()
        from . import system as S
        if not mode and S.is_key_phrase(label):  # "нажми на энтер" is a key
            S.hotkey(label)
            return True
        button = "right" if mode.startswith("прав") else "middle" if mode.startswith("средн") else "left"
        double = bool(re.match(r"дважды|два|двойн", mode))
        return bg(lambda: click_target(core, label, button, double), f"Не вижу на экране «{label}», {addr()}.")

    @R(rf"^(?:открой|выбери|включи|запусти)\s+(перв\w+|втор\w+|трет\w+|четверт\w+|пят\w+|последн\w+|предпоследн\w+)\s+"
       rf"(ссылк\w*|видео|ролик\w*|результат\w*|вкладк\w*|пункт\w*|файл\w*|папк\w*|стать\w*)(?:\s+(?:на\s+странице|в\s+списке|в\s+выдаче))?$")
    def _open_nth(m, t):
        return bg(lambda: click_target(core, f"{m.group(1)} {m.group(2)}", allow_vision=False),
                  f"Не нашёл такой {m.group(2)}, {addr()}.")

    @R(r"^(?:наведи(?:сь)?|наведи\s+(?:мышь|мышку|курсор))\s+(?:на\s+)(.+)$")
    def _hover(m, t):
        def go():
            hit = locate(core, m.group(1))
            if hit:
                move(hit["x"], hit["y"])
            return hit
        return bg(go, f"Не вижу на экране «{m.group(1)}», {addr()}.")

    @R(r"^(?:введи|впиши|напиши|напечатай|вбей|набери)\s+(.+?)\s+(?:в|во)\s+(поле\s*.*|строк\w+\s*.*|поиск\w*.*|адресн\w+\s+строк\w*)$")
    def _type_into(m, t):
        text, field = m.group(1), m.group(2)
        field = re.sub(r"^(?:поле|строк\w+)\s*", "", field).strip() or field
        enter = bool(re.search(r"поиск|адрес", field))
        return bg(lambda: type_into(core, field, text, enter=enter), f"Не нашёл поле «{field}», {addr()}.")

    @R(r"^(?:в|во)\s+(?:поле|строк\w+)\s+(.+?)\s+(?:введи|впиши|напиши|напечатай|вбей|набери)\s+(.+)$")
    def _type_into2(m, t):
        field, text = m.group(1), m.group(2)
        return bg(lambda: type_into(core, field, text, enter=bool(re.search(r"поиск|адрес", field))),
                  f"Не нашёл поле «{field}», {addr()}.")

    @R(r"^(?:прокрути|листай|пролистай|скролль|мотай)\s+(?:(вниз|вверх|ниже|выше)\s+)?до\s+(.+)$")
    def _scroll_to(m, t):
        d = "up" if (m.group(1) or "") in ("вверх", "выше") else "down"
        target = m.group(2).strip()
        if re.match(r"^(?:конца|низа|самого\s+низа)$", target):
            from . import system as S
            S.hotkey("ctrl+end")
            return True
        if re.match(r"^(?:начала|верха|самого\s+верха)$", target):
            from . import system as S
            S.hotkey("ctrl+home")
            return True
        return bg(lambda: scroll_to(core, target, d), f"Пролистал, но «{target}» так и не появилось, {addr()}.")

    @R(r"^(?:листай|прокрути|пролистай|скролль)\s+(влево|вправо)(?:\s+(сильно|много|чуть|немного))?$")
    def _hscroll(m, t):
        scroll("left" if m.group(1) == "влево" else "right", 12 if m.group(2) in ("сильно", "много") else 4)
        return True

    @R(r"^(?:перетащи|перетяни|перенеси\s+мышкой)\s+(.+?)\s+(?:на|в|во|к)\s+(.+)$")
    def _drag(m, t):
        return bg(lambda: drag_between(core, m.group(1), m.group(2)), f"Не смог найти, что и куда тащить, {addr()}.")

    @R(r"^(?:мышь|мышку|курсор)\s+(?:в|на)\s+((?:левый|правый)\s+(?:верхний|нижний)|верхний\s+(?:левый|правый)|нижний\s+(?:левый|правый))\s+угол$")
    def _corner(m, t):
        corner(m.group(1))
        return True

    @R(r"^(?:мышь|мышку|курсор)\s+(?:в|по)\s+центр\w*(?:\s+экрана)?$|^(?:центр|отцентруй)\w*\s+(?:мышь|мышку|курсор)$")
    def _center(m, t):
        corner("центр")
        return True

    @R(r"^(?:двигай|сдвинь|подвинь|передвинь|веди|отведи|подними|опусти)?\s*(?:мышь|мышку|курсор)\s+"
       r"(?:(?:чуть|чуть-чуть|немного|сильно|далеко)\s+)?(вправо|направо|правее|влево|налево|левее|вверх|наверх|выше|вниз|ниже)"
       r"(?:\s+(?:на\s+)?(\d+)|\s+(чуть|чуть-чуть|немного|сильно|далеко))?$")
    def _nudge(m, t):
        amount = {"чуть": 40, "чуть-чуть": 25, "немного": 100, "сильно": 450, "далеко": 600}
        n = int(m.group(2)) if m.group(2) else next((v for k, v in amount.items() if re.search(rf"\b{k}\b", t)), 200)
        d = m.group(1)
        dx = n if d in ("вправо", "направо", "правее") else -n if d in ("влево", "налево", "левее") else 0
        dy = -n if d in ("вверх", "наверх", "выше") else n if d in ("вниз", "ниже") else 0
        move_rel(dx, dy)
        return True

    @R(r"^(?:кликни|нажми|щелкни|клик)\s+(?:здесь|тут|сюда|мышкой)$")
    def _click_here(m, t):
        click()
        return True

    @R(r"^(?:зажми|удерживай|держи)\s+(?:левую\s+|правую\s+)?(?:кнопку\s+)?мыш\w*$")
    def _hold(m, t):
        press("right" if "прав" in t else "left")
        return "Держу. Скажите «отпусти мышь»."

    @R(r"^(?:отпусти)\s+(?:левую\s+|правую\s+)?(?:кнопку\s+)?(?:мыш\w*)?$")
    def _release(m, t):
        release("right" if "прав" in t else "left")
        return True

    @R(r"^(?:что\s+(?:тут|здесь|на\s+экране)\s+можно\s+нажать|какие\s+(?:кнопки|ссылки)\s+(?:есть\s+)?на\s+экране)$")
    def _what_clickable(m, t):
        def go():
            items = snapshot_elements(60)
            names = [f"{i['role']} «{i['name']}»" for i in items if i["role"] in ("кнопка", "ссылка", "вкладка", "пункт меню")][:6]
            core.say((f"Вижу {len(items)} элементов. Например: " + "; ".join(names) + ".") if names else "Ничего кликабельного не вижу.")
            return True
        return bg(go, None)
