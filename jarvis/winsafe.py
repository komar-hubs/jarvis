"""Deadlock-free window calls.

pywin32 keeps the GIL while it waits inside SetWindowLong / ShowWindow / SetWindowPos / GetWindowText. When the
target is one of Jarvis's own windows, Windows delivers a message to the WebView GUI thread and waits for it — but
that thread was itself waiting for the GIL. Both waited forever and Jarvis froze at start-up (the HUD thread
restyling its window while WebView2 was still running its ready callback): no voice, no interface, no API.

ctypes releases the GIL for the duration of a foreign call, so these replacements can never block the GUI thread.
install() swaps them into win32gui once, before any window exists."""
import ctypes
from ctypes import wintypes

# a private user32 handle: argtypes set on the shared ctypes.windll.user32 leaked into pywebview, whose
# SetWindowPos(hwnd, None, …) then failed with "argument 2: NoneType" (the tray menu could not be moved)
_u = ctypes.WinDLL("user32")
_u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
_u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
# insert-after may be HWND_TOPMOST (-1) / HWND_NOTOPMOST (-2): a signed integer, not a pointer
_u.SetWindowPos.argtypes = [wintypes.HWND, ctypes.c_ssize_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
_u.SetForegroundWindow.argtypes = [wintypes.HWND]
_u.BringWindowToTop.argtypes = [wintypes.HWND]
_u.SendMessageTimeoutW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM, ctypes.c_uint,
                                   ctypes.c_uint, ctypes.POINTER(ctypes.c_size_t)]
_installed = False


def get_window_text(hwnd):
    # SendMessageTimeout(WM_GETTEXT): a hung window costs at most 300 ms instead of freezing the caller
    n = ctypes.c_size_t(0)
    if not _u.SendMessageTimeoutW(hwnd, 0x000E, 0, 0, 0x0002, 300, ctypes.byref(n)):  # WM_GETTEXTLENGTH, ABORTIFHUNG
        return ""
    size = int(n.value) + 2
    buf = ctypes.create_unicode_buffer(size)
    res = ctypes.c_size_t(0)
    if not _u.SendMessageTimeoutW(hwnd, 0x000D, size, ctypes.cast(buf, ctypes.c_void_p).value or 0, 0x0002, 300,
                                  ctypes.byref(res)):  # WM_GETTEXT
        return ""
    return buf.value


def set_window_long(hwnd, index, value):
    return _u.SetWindowLongPtrW(hwnd, index, value)


def show_window(hwnd, cmd):
    return bool(_u.ShowWindow(hwnd, cmd))


def set_window_pos(hwnd, after, x, y, cx, cy, flags):
    return bool(_u.SetWindowPos(hwnd, after or 0, x, y, cx, cy, flags))


def set_foreground_window(hwnd):
    if not _u.SetForegroundWindow(hwnd):
        raise OSError("SetForegroundWindow failed")


def bring_window_to_top(hwnd):
    _u.BringWindowToTop(hwnd)


def install():
    global _installed
    if _installed:
        return
    import win32gui
    win32gui.GetWindowText = get_window_text
    win32gui.SetWindowLong = set_window_long
    win32gui.ShowWindow = show_window
    win32gui.SetWindowPos = set_window_pos
    win32gui.SetForegroundWindow = set_foreground_window
    win32gui.BringWindowToTop = bring_window_to_top
    _installed = True
