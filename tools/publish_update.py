"""Publish a new Jarvis version for friends: bump VERSION, build jarvis_share.zip (without personal data)
and upload it as the latest GitHub release. Friends' copies then offer "Обновить" in Settings.

    python tools/publish_update.py "что нового"            publish
    python tools/publish_update.py --create                create the public release repository once

Needs the GitHub CLI logged in (gh auth login)."""
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from jarvis.config import DEFAULTS  # noqa: E402

REPO = DEFAULTS["update"]["github"]
ZIP = ROOT.parent / "jarvis_share.zip"


def gh(*args, check=True):
    return subprocess.run(["gh", *args], check=check, capture_output=True, text=True, encoding="utf-8", errors="replace")


def main():
    if "--create" in sys.argv:
        r = gh("repo", "create", REPO, "--public", "--add-readme",
               "--description", "J.A.R.V.I.S. — релизы для обновления по одной кнопке", check=False)
        print(r.stdout or r.stderr)
        return
    if gh("repo", "view", REPO, check=False).returncode != 0:
        sys.exit(f"Репозиторий {REPO} не найден. Создайте его: python tools/publish_update.py --create")
    notes = " ".join(a for a in sys.argv[1:] if not a.startswith("--")) or "Исправления и улучшения."
    ver = dt.datetime.now().strftime("%Y.%m.%d.%H%M")
    (ROOT / "VERSION").write_text(ver, encoding="utf-8")
    subprocess.run([sys.executable, str(ROOT / "pack_for_friend.py")], check=True)
    # version.json is what copies check (a plain download, no GitHub API rate limit)
    meta = ROOT.parent / "version.json"
    meta.write_text(json.dumps({"version": ver, "notes": notes, "size": ZIP.stat().st_size}, ensure_ascii=False),
                    encoding="utf-8")
    r = gh("release", "create", "v" + ver, str(ZIP), str(meta), "--repo", REPO, "--title", f"J.A.R.V.I.S. {ver}",
           "--notes", notes, "--latest", check=False)
    meta.unlink(missing_ok=True)
    if r.returncode != 0:
        sys.exit("Не удалось опубликовать: " + (r.stderr or r.stdout))
    print(f"Опубликована версия {ver}: {r.stdout.strip()}")


if __name__ == "__main__":
    main()
