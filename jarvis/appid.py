"""Jarvis's own identity on the taskbar.

Jarvis runs in the shared Python312\\pythonw.exe (the venv's pythonw starts it), and Windows groups taskbar buttons by
program: any shortcut to that pythonw.exe lends its name, so Jarvis showed up as "КАЛЬКУЛЯТОР_ПЕРЕНОСОВ" with the
Python icon. An explicit AppUserModelID gives Jarvis a group of its own; a Start menu shortcut carrying the same ID
gives that group its name and icon (pinning it starts Jarvis.bat); WM_SETICON puts the icon on the window itself."""
import ctypes
import math
from pathlib import Path

AUMID = "KomarHubs.Jarvis"
ROOT = Path(__file__).resolve().parent.parent
ICO = ROOT / "data" / "jarvis.ico"


def set_process_id():
    """Before the first window is created."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(AUMID)
    except Exception:
        pass


def draw(s=256):
    """The arc reactor of the tray icon, drawn at any size."""
    from PIL import Image, ImageDraw
    k = s / 64
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2 * k, 2 * k, s - 2 * k, s - 2 * k), fill=(3, 12, 22, 255), outline=(80, 220, 255, 255), width=max(1, round(4 * k)))
    d.ellipse((14 * k, 14 * k, s - 14 * k, s - 14 * k), outline=(80, 220, 255, 200), width=max(1, round(3 * k)))
    d.ellipse((24 * k, 24 * k, s - 24 * k, s - 24 * k), fill=(190, 245, 255, 255))
    for i in range(3):
        a = math.radians(90 + i * 120)
        cx, cy = s / 2 + math.cos(a) * 20 * k, s / 2 + math.sin(a) * 20 * k
        d.ellipse((cx - 3 * k, cy - 3 * k, cx + 3 * k, cy + 3 * k), fill=(80, 220, 255, 255))
    return img


def icon_path():
    if not ICO.exists():
        try:
            ICO.parent.mkdir(parents=True, exist_ok=True)
            draw(256).save(ICO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        except Exception:
            return None
    return str(ICO)


def ensure_shortcut(log=None):
    """Start menu → J.A.R.V.I.S..lnk with Jarvis's AppUserModelID: the taskbar takes the group's name and icon from it.
    Rewritten only when missing or pointing elsewhere (the folder moved)."""
    try:
        import os
        import pythoncom
        from win32com.propsys import propsys, pscon
        from win32com.shell import shell
        ico = icon_path()
        target = str(ROOT / "Jarvis.bat")
        lnk = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "J.A.R.V.I.S..lnk"
        pythoncom.CoInitialize()
        link = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink)
        if lnk.exists():
            try:
                link.QueryInterface(pythoncom.IID_IPersistFile).Load(str(lnk))
                same = link.GetPath(0)[0].lower() == target.lower()
                aumid = link.QueryInterface(propsys.IID_IPropertyStore).GetValue(pscon.PKEY_AppUserModel_ID).GetValue()
                if same and aumid == AUMID:
                    return True
            except Exception:
                pass
            link = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink)
        link.SetPath(target)
        link.SetWorkingDirectory(str(ROOT))
        link.SetDescription("J.A.R.V.I.S. — голосовой помощник")
        link.SetShowCmd(7)                       # the .bat's console starts minimised
        if ico:
            link.SetIconLocation(ico, 0)
        store = link.QueryInterface(propsys.IID_IPropertyStore)
        store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(AUMID, pythoncom.VT_LPWSTR))
        store.Commit()
        link.QueryInterface(pythoncom.IID_IPersistFile).Save(str(lnk), 0)
        return True
    except Exception as e:
        if log:
            log(f"Ярлык в меню Пуск: {e}", "warn")
        return False


def set_window_icon(hwnd):
    """The window's own icon (taskbar button, Alt+Tab) instead of Python's."""
    ico = icon_path()
    if not hwnd or not ico:
        return
    try:
        u = ctypes.windll.user32
        u.LoadImageW.restype = ctypes.c_void_p
        u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        for kind, metric in ((1, 11), (0, 49)):      # ICON_BIG / SM_CXICON, ICON_SMALL / SM_CXSMICON (DPI-scaled)
            size = u.GetSystemMetrics(metric) or (32 if kind else 16)
            h = u.LoadImageW(None, ico, 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
            if h:
                u.SendMessageW(hwnd, 0x80, kind, h)  # WM_SETICON
    except Exception:
        pass
