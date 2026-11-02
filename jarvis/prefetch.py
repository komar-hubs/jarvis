"""The part of the installation that runs in Python, for install.ps1: `python -m jarvis.prefetch`.

Kept out of the PowerShell script on purpose. Kaspersky's heuristic (HEUR:Trojan.PowerShell.Alien.gen) flagged
install.ps1 first for inline code that measured memory and processor threads (a sandbox check), and then — with its
2026-11-02 bases — for the downloader look of the script as a whole: removing the "downloaded from the internet" mark
from every file and creating a shortcut through WScript.Shell, next to downloads. Here it is ordinary program code:
- the "downloaded from the internet" mark comes off the unpacked files (Jarvis.bat would ask "Run?" every time);
- the speech models are downloaded once (a weak laptop gets the light int8 GigaAM: 225 MB instead of 880);
- the desktop and Start menu shortcuts (with Jarvis's own taskbar identity) are made."""
import os
from pathlib import Path

from . import system as S

ROOT = Path(__file__).resolve().parent.parent


def unblock(root=ROOT):
    """Removes the Zone.Identifier stream ("downloaded from the internet") from the unpacked files."""
    n = 0
    for p in root.rglob("*"):
        if p.is_file() and ".venv" not in p.parts and "data" not in p.parts:
            try:
                os.remove(str(p) + ":Zone.Identifier")
                n += 1
            except OSError:
                pass
    return n


def desktop():
    try:
        from win32com.shell import shell, shellcon
        return Path(shell.SHGetKnownFolderPath(shellcon.FOLDERID_Desktop))
    except Exception:
        return Path.home() / "Desktop"


def shortcuts():
    from . import appid
    ok = appid.ensure_shortcut(print, desktop() / "J.A.R.V.I.S..lnk")
    appid.ensure_shortcut(print)
    return ok


def models():
    try:
        import onnx_asr
        q = "int8" if S.weak_pc() else None
        onnx_asr.load_model("gigaam-v3-ctc", quantization=q, providers=["CPUExecutionProvider"])
        print("GigaAM ok", "(light)" if q else "")
    except Exception as e:
        print("GigaAM FAIL:", e)
    try:
        from .config import Config
        from .tts import SileroEngine
        print("Silero ok" if SileroEngine(Config()).start() else "Silero FAIL")
    except Exception as e:
        print("Silero FAIL:", e)


def main():
    n = unblock()
    if n:
        print(f"Снята метка «из интернета» с {n} файлов")
    models()
    if shortcuts():
        print("Ярлык J.A.R.V.I.S. создан на рабочем столе.")


if __name__ == "__main__":
    main()
