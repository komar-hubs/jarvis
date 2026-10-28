"""Builds jarvis_share.zip next to this folder without personal data (config with token and keys, chat, tasks)
and without the local virtual environments. The friend unpacks it and runs install.bat."""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT.parent / "jarvis_share.zip"
SKIP_DIRS = {".venv", ".xtts-venv", "data", "__pycache__", ".git"}

with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    n = 0
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT)
        if any(part in SKIP_DIRS for part in rel.parts) or p.is_dir() or p.suffix in (".zip", ".log") \
                or p.name in ("mic_report.txt", "CLAUDE.md", "screamer.py", ".gitignore"):  # notes about this PC, not for others
            continue
        z.write(p, Path("jarvis") / rel)
        n += 1
print(f"Готово: {OUT} ({n} файлов, {OUT.stat().st_size // 1024} КБ)")
