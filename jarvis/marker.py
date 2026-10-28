"""The marker: Jarvis circles, underlines, boxes or points an arrow at something on the screen.

"Джарвис, покажи где кнопка Отправить", "обведи поле поиска", "подчеркни Настройки"; the AI uses it too
(tool "highlight") when it explains where to click. The target is found the same way clicks find it
(UI Automation, OCR, then the vision model: mouse.locate), so it works in any program.

The drawing is a layered Win32 window with per-pixel alpha (UpdateLayeredWindow) covering only the marks'
area: click-through (WS_EX_TRANSPARENT), never takes focus, always on top. The stroke draws itself like a felt
pen in ~0.4 s with a slight hand wobble, stays a few seconds and fades. One thread owns the window."""
import ctypes
import math
import queue
import random
import re
import threading
import time
from ctypes import wintypes

import numpy as np

COLORS = {"cyan": (79, 216, 255), "yellow": (255, 214, 64), "red": (255, 75, 92), "green": (93, 255, 176),
          "purple": (155, 134, 255), "white": (235, 245, 255)}
THEME_COLOR = {"jarvis": "cyan", "mark3": "red", "nano": "purple", "emerald": "green", "stealth": "white"}
STYLES = ("circle", "box", "underline", "arrow", "fill")

user32 = ctypes.WinDLL("user32")
gdi32 = ctypes.WinDLL("gdi32")


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte), ("SourceConstantAlpha", ctypes.c_byte),
                ("AlphaFormat", ctypes.c_byte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


gdi32.CreateDIBSection.restype = ctypes.c_void_p
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
                                   wintypes.HANDLE, wintypes.DWORD]
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
user32.GetDC.restype = ctypes.c_void_p
user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, ctypes.POINTER(wintypes.POINT),
                                       ctypes.POINTER(wintypes.SIZE), ctypes.c_void_p, ctypes.POINTER(wintypes.POINT),
                                       wintypes.DWORD, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]


# ====================================================================== shapes: point lists in window coordinates
def _wobble(pts, amp, seed):
    """A hand never draws a perfect line: low-frequency jitter along the path."""
    rnd = random.Random(seed)
    ph = [rnd.uniform(0, 6.28) for _ in range(3)]
    out = []
    for i, (x, y) in enumerate(pts):
        t = i / max(1, len(pts) - 1)
        d = amp * (math.sin(t * 9 + ph[0]) * .5 + math.sin(t * 17 + ph[1]) * .3 + math.sin(t * 31 + ph[2]) * .2)
        out.append((x + d, y + d * .7))
    return out


def _shape(style, l, t, r, b, seed):
    cx, cy, w, h = (l + r) / 2, (t + b) / 2, r - l, b - t
    if style == "circle":
        rx, ry = w / 2 + 16, h / 2 + 12
        rnd = random.Random(seed)
        a0, turn = rnd.uniform(-2.6, -2.0), 2 * math.pi * 1.12          # a bit more than a full turn, like a pen
        pts = []
        for i in range(140):
            a = a0 + turn * i / 139
            k = 1 + .06 * (i / 139)                                        # the second pass drifts outward
            pts.append((cx + math.cos(a) * rx * k, cy + math.sin(a) * ry * k))
        return [_wobble(pts, 3.5, seed)]
    if style == "box":
        p = 10
        x0, y0, x1, y1 = l - p, t - p, r + p, b + p
        seg = lambda a, b_, n: [(a[0] + (b_[0] - a[0]) * i / n, a[1] + (b_[1] - a[1]) * i / n) for i in range(n)]
        pts = seg((x0, y0), (x1, y0), 30) + seg((x1, y0), (x1, y1), 14) + seg((x1, y1), (x0, y1), 30) \
            + seg((x0, y1), (x0 - 2, y0 - 8), 15)
        return [_wobble(pts, 2.5, seed)]
    if style == "underline":
        y = b + 7
        pts = [(l - 6 + (w + 12) * i / 60, y + math.sin(i / 60 * math.pi * 2.5) * 2.2) for i in range(61)]
        return [_wobble(pts, 1.5, seed), _wobble([(x, yy + 7) for x, yy in pts[8:56]], 1.5, seed + 1)]
    if style == "arrow":
        # from the upper left towards the target's edge, a slight curve, then the head
        tx, ty = l - 6, cy
        sx, sy = tx - 150, ty - 110
        if sx < 20:                                                        # no room on the left: come from the right
            tx, sx = r + 6, r + 156
        pts = []
        for i in range(60):
            u = i / 59
            mx, my = (sx + tx) / 2 + 30, (sy + ty) / 2 - 25
            x = (1 - u) ** 2 * sx + 2 * (1 - u) * u * mx + u * u * tx
            y = (1 - u) ** 2 * sy + 2 * (1 - u) * u * my + u * u * ty
            pts.append((x, y))
        (px, py), (qx, qy) = pts[-6], pts[-1]
        ang = math.atan2(qy - py, qx - px)
        head = [(qx - math.cos(ang - .5) * 24, qy - math.sin(ang - .5) * 24), (qx, qy),
                (qx - math.cos(ang + .5) * 24, qy - math.sin(ang + .5) * 24)]
        return [_wobble(pts, 2, seed), head]
    if style == "fill":                                                    # a highlighter swipe over the text
        return [[(l - 4 + (w + 8) * i / 40, cy + math.sin(i / 3.0) * .8) for i in range(41)]]
    return []


class Marker:
    def __init__(self, core):
        self.core = core
        self.q = queue.Queue()
        self._thread = None

    # ---------------------------------------------------------------- public
    def show(self, marks, color=None, seconds=6.0):
        """marks: [{"rect": (l, t, r, b) in physical pixels, "style": "circle", "label": "…"}]."""
        marks = [m for m in marks if m.get("rect")]
        if not marks:
            return False
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._loop, daemon=True, name="marker")
            self._thread.start()
        if not color or color == "auto":
            color = THEME_COLOR.get(self.core.cfg.get("theme", "jarvis"), "cyan")
        self.q.put(("show", marks, COLORS.get(color, COLORS["cyan"]), max(1.5, min(float(seconds), 60))))
        return True

    def clear(self):
        self.q.put(("clear",))

    def point(self, what, style="circle", label="", color=None, seconds=6.0):
        """Find "what" on the screen and mark it. Returns the hit or None."""
        from . import mouse
        hit = None
        try:   # tight targets first: a whole panel that happens to share the name is the wrong thing to circle
            hit = mouse.find(what, small=True)
        except Exception as e:
            self.core.log(f"Маркер: поиск на экране: {e}", "warn")
        if not hit and self.core.cfg.get("mouse.vision", True):
            hit = mouse.vision_find(self.core, what)
            if hit and hit.get("role") == "vision":
                hit = self._snap(hit) or hit
        if not hit:
            return None
        style = style if style in STYLES else "circle"
        rect = hit.get("rect")
        if hit.get("role") == "vision" or not rect:
            # only an approximate point (the vision model guessed it): an arrow at it is honest, a ring around
            # an invented box drew "beside" the thing
            x, y = hit["x"], hit["y"]
            rect, style = (x - 18, y - 12, x + 18, y + 12), "arrow"
        self.core.log(f"Маркер: «{what}» → «{(hit.get('name') or '')[:50]}» ({hit.get('src')}) {tuple(int(v) for v in rect)}")
        self.show([{"rect": tuple(int(v) for v in rect), "style": style, "label": label}], color, seconds)
        return hit

    @staticmethod
    def _snap(hit):
        """The vision model gives a point; the real element or word under it gives the true rectangle."""
        from . import mouse
        x, y = hit["x"], hit["y"]
        try:
            with mouse.physical():
                cands = [e for e in mouse.uia_elements(mouse.foreground(), taskbar=True) if e.get("rect")]
            cands += [c for c in mouse._ocr_candidates() if c.get("word") or len(c["name"]) <= 40]
        except Exception:
            return None
        sw, sh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
        inside = [c for c in cands if c["rect"][0] - 4 <= x <= c["rect"][2] + 4 and c["rect"][1] - 4 <= y <= c["rect"][3] + 4
                  and (c["rect"][2] - c["rect"][0]) * (c["rect"][3] - c["rect"][1]) < sw * sh * 0.08]
        if inside:   # the smallest thing under the point: the button, not the toolbar around it
            best = min(inside, key=lambda c: (c["rect"][2] - c["rect"][0]) * (c["rect"][3] - c["rect"][1]))
        else:
            near = [(math.hypot(c["x"] - x, c["y"] - y), c) for c in cands if c.get("x") is not None]
            near = [nc for nc in near if nc[0] < 45]
            if not near:
                return None
            best = min(near, key=lambda nc: nc[0])[1]
        return dict(best, src=f"vision+{best.get('src', 'uia')}")

    # ---------------------------------------------------------------- window thread
    def _loop(self):
        import win32con
        import win32gui
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))   # physical pixels, like mouse.locate
        wc = win32gui.WNDCLASS()
        wc.lpszClassName = "JarvisMarker"
        wc.lpfnWndProc = lambda h, m, w, l: win32gui.DefWindowProc(h, m, w, l)
        wc.hInstance = win32gui.GetModuleHandle(None)
        try:
            win32gui.RegisterClass(wc)
        except Exception:
            pass                                                     # already registered by an earlier thread
        ex = (win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_TOPMOST
              | win32con.WS_EX_TOOLWINDOW | 0x08000000)              # + NOACTIVATE
        hwnd = win32gui.CreateWindowEx(ex, "JarvisMarker", "JARVIS MARK", win32con.WS_POPUP, 0, 0, 1, 1, 0, 0,
                                       wc.hInstance, None)
        job = None                                                   # the marks on screen and their timeline
        while True:
            try:
                cmd = self.q.get(timeout=0.016 if job else 0.25)
            except queue.Empty:
                cmd = None
            win32gui.PumpWaitingMessages()
            if cmd:
                if cmd[0] == "clear":
                    if job:
                        job["t_end"] = min(job["t_end"], time.time())
                elif cmd[0] == "show":
                    job = self._prepare(cmd[1], cmd[2], cmd[3])
            if not job:
                continue
            now = time.time() - job["t0"]
            draw, fade = min(1.0, now / 0.42), 1.0
            if time.time() > job["t_end"]:
                fade = max(0.0, 1 - (time.time() - job["t_end"]) / 0.45)
            if draw < 1 or not job.get("full"):
                self._render(job, draw)
                job["full"] = draw >= 1
            self._blit(hwnd, job, int(255 * fade))
            if fade <= 0:
                win32gui.ShowWindow(hwnd, 0)
                job = None

    def _prepare(self, marks, color, seconds):
        vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)          # virtual screen
        vw, vh = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
        seed = random.randint(0, 10 ** 6)
        shapes = []
        for i, m in enumerate(marks):
            l, t, r, b = m["rect"]
            shapes.append({"style": m.get("style") or "circle", "label": m.get("label") or "",
                           "rect": (l, t, r, b), "paths": _shape(m.get("style") or "circle", l, t, r, b, seed + i)})
        xs = [p[0] for s in shapes for path in s["paths"] for p in path] + [c for s in shapes for c in s["rect"][::2]]
        ys = [p[1] for s in shapes for path in s["paths"] for p in path] + [c for s in shapes for c in s["rect"][1::2]]
        pad = 60
        x0, y0 = max(vx, int(min(xs)) - pad), max(vy, int(min(ys)) - pad - 34)   # room for a label above
        x1, y1 = min(vx + vw, int(max(xs)) + pad + 220), min(vy + vh, int(max(ys)) + pad + 34)
        return {"shapes": shapes, "color": color, "origin": (x0, y0), "size": (max(1, x1 - x0), max(1, y1 - y0)),
                "t0": time.time(), "t_end": time.time() + seconds, "img": None}

    def _render(self, job, frac):
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
        W, H = job["size"]
        ox, oy = job["origin"]
        c = job["color"]
        line = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(line)
        for s in job["shapes"]:
            fill = s["style"] == "fill"
            total = sum(len(p) for p in s["paths"])
            budget = int(total * frac)
            for path in s["paths"]:
                n = min(len(path), budget)
                budget -= n
                if n < 2:
                    continue
                pts = [(x - ox, y - oy) for x, y in path[:n]]
                if fill:   # a translucent highlighter swipe as tall as the text
                    hgt = max(14, s["rect"][3] - s["rect"][1] + 6)
                    d.line(pts, fill=(255, 214, 64, 105), width=int(hgt), joint="curve")
                else:
                    d.line(pts, fill=c + (235,), width=5, joint="curve")
                    r = 2.5
                    for x, y in (pts[0], pts[-1]):              # round pen caps
                        d.ellipse((x - r, y - r, x + r, y + r), fill=c + (235,))
        # a soft glow under the ink, like the HUD lines
        glow = line.filter(ImageFilter.GaussianBlur(7))
        a = np.array(glow)
        a[..., 3] = (a[..., 3].astype(np.float32) * .8).astype(np.uint8)
        img = Image.alpha_composite(Image.fromarray(a), line)
        if frac >= 1:
            d2 = ImageDraw.Draw(img)
            try:
                font = ImageFont.truetype("segoeuib.ttf", 18)
            except Exception:
                font = ImageFont.load_default()
            for s in job["shapes"]:
                if not s["label"]:
                    continue
                l, t, r, b = s["rect"]
                x, y = l - ox, t - oy - 34
                if y < 2:
                    y = b - oy + 18
                d2.text((x, y), s["label"], font=font, fill=(255, 255, 255, 255), stroke_width=3,
                        stroke_fill=(2, 8, 14, 230))
        job["img"] = img

    def _blit(self, hwnd, job, alpha):
        import win32con
        import win32gui
        img = job["img"]
        if img is None:
            return
        W, H = img.size
        a = np.asarray(img, dtype=np.uint16)
        al = a[..., 3:4]
        bgra = np.empty((H, W, 4), np.uint8)                        # premultiplied BGRA for UpdateLayeredWindow
        bgra[..., 0] = (a[..., 2:3] * al // 255)[..., 0]
        bgra[..., 1] = (a[..., 1:2] * al // 255)[..., 0]
        bgra[..., 2] = (a[..., 0:1] * al // 255)[..., 0]
        bgra[..., 3] = al[..., 0]
        screen = user32.GetDC(None)
        mem = gdi32.CreateCompatibleDC(screen)
        bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), W, -H, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        bmp = gdi32.CreateDIBSection(mem, ctypes.byref(bi), 0, ctypes.byref(bits), None, 0)
        old = gdi32.SelectObject(mem, bmp)
        try:
            ctypes.memmove(bits, bgra.ctypes.data, W * H * 4)
            ox, oy = job["origin"]
            blend = BLENDFUNCTION(0, 0, max(0, min(255, alpha)), 1)
            user32.UpdateLayeredWindow(hwnd, screen, ctypes.byref(wintypes.POINT(ox, oy)), ctypes.byref(wintypes.SIZE(W, H)),
                                       mem, ctypes.byref(wintypes.POINT(0, 0)), 0, ctypes.byref(blend), 2)
            if not win32gui.IsWindowVisible(hwnd):
                win32gui.ShowWindow(hwnd, 4)                         # SW_SHOWNOACTIVATE
                win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                      win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
        finally:
            gdi32.SelectObject(mem, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(None, screen)

    # ---------------------------------------------------------------- AI tool and RPC
    def tool_highlight(self, a):
        """{"text": "что на экране", "style": "circle|box|underline|arrow|fill", "label": "…", "color": "…"}"""
        what = str(a.get("text") or a.get("what") or "").strip()
        if a.get("clear"):
            self.clear()
            return "Выделение убрано."
        if not what:
            return "Не сказано, что выделить."
        hit = self.point(what, a.get("style") or "circle", str(a.get("label") or "")[:60], a.get("color"),
                         a.get("seconds") or 8)
        return f"Выделил на экране: {hit.get('name') or what}." if hit else f"«{what}» на экране не найдено."

    def rpc_marker_point(self, text, style="circle", label=""):
        """Mark something on the screen by its name (debugging / the API): what was found and where."""
        hit = self.point(text, style, label)
        return {"name": hit.get("name"), "src": hit.get("src"), "rect": hit.get("rect")} if hit else None

    def rpc_marker_test(self, style="circle"):
        """Settings / debugging: mark the middle of the primary screen."""
        w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        return self.show([{"rect": (w // 2 - 120, h // 2 - 30, w // 2 + 120, h // 2 + 30), "style": style,
                           "label": "Вот так выглядит маркер"}], seconds=4)


# ====================================================================== voice rules
def register(R, core):
    STYLE_WORD = {"обведи": "circle", "подчеркни": "underline", "покажи стрелкой": "arrow", "укажи на": "arrow",
                  "выдели маркером": "fill", "выдели на экране": "box", "отметь на экране": "box",
                  "покажи где": "circle", "где": "circle", "покажи на экране": "circle"}

    @R(r"^(?:убери|сотри|спрячь)\s+(?:выделение|маркер|обводку|стрелку|пометки?)$")
    def _clear(m, t):
        core.marker.clear()
        return True

    @R(r"^(обведи|подчеркни|покажи\s+стрелкой|укажи\s+на|выдели\s+маркером|выдели\s+на\s+экране|отметь\s+на\s+экране|"
       r"покажи\s+на\s+экране|покажи,?\s+где|где\s+(?:тут|здесь|на\s+экране))\s+(?:находится\s+|тут\s+)?(.+?)\??$")
    def _mark(m, t):
        verb = re.sub(r"\s+", " ", m.group(1).replace(",", "")).strip()
        verb = "где" if verb.startswith("где") else verb
        what = m.group(2).strip()
        if len(what) < 2:
            return False
        threading.Thread(target=lambda: _do(STYLE_WORD.get(verb, "circle"), what), daemon=True).start()
        return True

    def _do(style, what):
        hit = core.marker.point(what, style)
        core.say(random.choice(["Вот здесь, сэр.", "Вот, сэр.", "Здесь."]) if hit
                 else f"Не вижу «{what}» на экране, сэр.")

