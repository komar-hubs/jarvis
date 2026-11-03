"""VirusTotal check of a release, through the API (no browser, no captcha), and the README / channel post updated
with the result. Called by publish_update.py after a release; by hand:

    python tools/virustotal.py              check ..\\jarvis_share.zip and the latest release link, update the docs

The key lives in data/config.json -> virustotal.api_key (data/ is never committed). The free public API allows
4 requests a minute: the polling below waits 20 s between requests. When an engine flags the archive, the docs are
left as they were and the engine and its verdict are printed (Kaspersky once flagged install.ps1)."""
import datetime as dt
import hashlib
import json
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
ZIP = ROOT.parent / "jarvis_share.zip"
API = "https://www.virustotal.com/api/v3"
POST = Path.home() / "Desktop" / "jarvis-telegram-channel" / "ПОСТЫ.md"
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def api_key():
    try:
        return (json.loads((ROOT / "data" / "config.json").read_text("utf-8")).get("virustotal") or {}).get("api_key", "")
    except Exception:
        return ""


def _wait(key, analysis_id, what, limit=600):
    """Polls an analysis until it is completed: its stats."""
    t0 = time.time()
    while time.time() - t0 < limit:
        time.sleep(20)
        r = requests.get(f"{API}/analyses/{analysis_id}", headers={"x-apikey": key}, timeout=30)
        if r.status_code == 404:          # a re-analysis is not readable for the first moments
            continue
        r.raise_for_status()
        a = r.json()["data"]["attributes"]
        if a.get("status") == "completed":
            return a["stats"]
        print(f"  VirusTotal проверяет {what}… {int(time.time() - t0)} с")
    raise TimeoutError(f"VirusTotal не закончил проверку {what} за {limit // 60} мин")


def file_report(key, sha, settle=True):
    """The file's latest results. "completed" comes while slower engines are still working (51 of ~65 answered), so
    with settle the report is re-read every 30 s until the number of engines stops growing (at most 4 minutes)."""
    best, t0 = None, time.time()
    while True:
        r = requests.get(f"{API}/files/{sha}", headers={"x-apikey": key}, timeout=30)
        r.raise_for_status()
        a = r.json()["data"]["attributes"]
        stats = a["last_analysis_stats"]
        flagged = [(eng, res.get("result") or res.get("category")) for eng, res in (a.get("last_analysis_results") or {}).items()
                   if res.get("category") in ("malicious", "suspicious")]
        total = sum(stats.get(k, 0) for k in ("malicious", "suspicious", "undetected", "harmless"))
        kasp = ((a.get("last_analysis_results") or {}).get("Kaspersky") or {}).get("category", "")
        cur = {"sha256": sha, "stats": stats, "total": total, "flagged": flagged, "kaspersky": kasp}
        if not settle or total >= 62 or (best and total <= best["total"]) or time.time() - t0 > 240:
            return cur if not best or total >= best["total"] else best
        best = cur
        print(f"  ответили {total} антивирусов, жду остальных…")
        time.sleep(30)


def scan_file(key, path):
    """{"sha256", "stats", "total", "flagged": [(engine, verdict)]}."""
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    with path.open("rb") as f:
        r = requests.post(f"{API}/files", headers={"x-apikey": key}, files={"file": (path.name, f)}, timeout=300)
    r.raise_for_status()
    _wait(key, r.json()["data"]["id"], "архив")
    rep = file_report(key, sha)
    # a busy VirusTotal leaves engines on "timeout" (Kaspersky, Dr.Web, Avast once): one re-analysis gets most of them
    # Kaspersky matters most here (the users are in Russia): it is waited for up to 5 more minutes
    if rep["stats"].get("timeout", 0) >= 5 or rep.get("kaspersky") in ("", "timeout"):
        print(f"  {rep['stats'].get('timeout', 0)} антивирусов не успели ответить (Касперский: {rep.get('kaspersky') or '—'}), "
              f"прошу повторную проверку…")
        r = requests.post(f"{API}/files/{sha}/analyse", headers={"x-apikey": key}, timeout=30)
        r.raise_for_status()
        try:
            _wait(key, r.json()["data"]["id"], "архив повторно")
        except TimeoutError:
            # a busy VirusTotal: the first answers stand (the Kaspersky note is added only when it really answered);
            # "python tools/virustotal.py --recheck" reads the finished report later
            print("  повторная проверка не закончилась, беру первые ответы")
            return rep
        t0 = time.time()
        while True:
            again = file_report(key, sha, settle=False)
            if again.get("kaspersky") not in ("", "timeout") or time.time() - t0 > 300:
                break
            print("  жду ответа Касперского…")
            time.sleep(20)
        rep = again if again["total"] >= rep["total"] or again.get("kaspersky") not in ("", "timeout") else rep
    return rep


def scan_url(key, url):
    """{"id" (sha256 of the URL, as the site links it), "stats", "total"}."""
    r = requests.post(f"{API}/urls", headers={"x-apikey": key}, data={"url": url}, timeout=60)
    r.raise_for_status()
    stats = _wait(key, r.json()["data"]["id"], "ссылку")
    total = sum(stats.get(k, 0) for k in ("malicious", "suspicious", "undetected", "harmless"))
    return {"id": hashlib.sha256(url.encode()).hexdigest(), "stats": stats, "total": total}


def update_docs(version, f, u):
    """README: the badge, the line under the screenshots, the «Безопасность» paragraph; the channel post line."""
    d = dt.date(*map(int, version.split(".")[:3]))
    when = f"{d.day} {MONTHS[d.month - 1]}"
    n, h = f["total"], f["sha256"]
    p = ROOT / "README.md"
    s = p.read_text("utf-8")
    s = re.sub(r"\[!\[VirusTotal: 0 из \d+\]\(https://img\.shields\.io/badge/VirusTotal-0%2F\d+%20",
               f"[![VirusTotal: 0 из {n}](https://img.shields.io/badge/VirusTotal-0%2F{n}%20", s)
    s = re.sub(r"virustotal\.com/gui/file/[0-9a-f]{64}", f"virustotal.com/gui/file/{h}", s)
    of = "антивируса" if n % 10 == 1 and n % 100 != 11 else "антивирусов"
    s = re.sub(r"\[VirusTotal, 0 из \d+ антивирус\w+\]", f"[VirusTotal, 0 из {n} {of}]", s)
    by = "антивирусом" if n % 10 == 1 and n % 100 != 11 else "антивирусами"
    s = re.sub(r"\*\*Проверка \d+ антивирус\w+\.\*\* Архив релиза v[\d.]+(?: от \d+ [а-я]+)?",
               f"**Проверка {n} {by}.** Архив релиза v{version} от {when}", s)
    s = re.sub(r"(`jarvis_share\.zip`, SHA-256\s+`)[0-9a-f]{64}`", rf"\g<1>{h}`", s)
    s = re.sub(r"\[0 из \d+ — «No security vendors", f"[0 из {n} — «No security vendors", s)
    if u:
        s = re.sub(r"Ссылка на скачивание из релизов: \[0 из \d+\]\(https://www\.virustotal\.com/gui/url/[0-9a-f]{64}\)",
                   f"Ссылка на скачивание из релизов: [0 из {u['total']}](https://www.virustotal.com/gui/url/{u['id']})", s)
    p.write_text(s, "utf-8")
    if POST.exists():
        t = POST.read_text("utf-8")
        # "(Касперский тоже чистый)" only when Kaspersky really answered for this archive
        kasp = " (Касперский тоже чистый)" if f.get("kaspersky") == "undetected" else ""
        t = re.sub(r"◆ \*\*VirusTotal: 0 из \d+\*\* антивирус\w+ для архива версии v[\d.]+(?: \(Касперский тоже чистый\))?",
                   f"◆ **VirusTotal: 0 из {n}** {of} для архива версии v{version}{kasp}", t)
        POST.write_text(t, "utf-8")


def check_release(version=None):
    """The whole check; True when the docs were updated (nothing flagged)."""
    key = api_key()
    if not key:
        print("VirusTotal: ключа нет (data/config.json -> virustotal.api_key), проверка пропущена.")
        return False
    version = version or (ROOT / "VERSION").read_text("utf-8").strip()
    print("VirusTotal: отправляю архив…")
    f = scan_file(key, ZIP)
    print(f"VirusTotal, архив: {f['stats'].get('malicious', 0)} из {f['total']} (Касперский: {f.get('kaspersky') or 'нет ответа'}) — "
          f"https://www.virustotal.com/gui/file/{f['sha256']}")
    u = None
    try:
        u = scan_url(key, f"https://github.com/komar-hubs/jarvis/releases/download/v{version}/jarvis_share.zip")
        print(f"VirusTotal, ссылка: {u['stats'].get('malicious', 0)} из {u['total']}")
    except Exception as e:
        print(f"VirusTotal, ссылка не проверена: {e}")
    if f["flagged"] or (u and (u["stats"].get("malicious") or u["stats"].get("suspicious"))):
        print("ВНИМАНИЕ: есть срабатывания, README и пост не обновлены:")
        for eng, verdict in f["flagged"]:
            print(f"  {eng}: {verdict}")
        return False
    update_docs(version, f, u)
    print("README.md и пост для канала обновлены: закоммитьте README вместе с VERSION.")
    return True


def recheck(version=None, wait=600):
    """The release archive is already on VirusTotal: wait until Kaspersky has answered (up to `wait` seconds), scan the
    link, update the docs. No new upload."""
    key = api_key()
    version = version or (ROOT / "VERSION").read_text("utf-8").strip()
    sha = hashlib.sha256(ZIP.read_bytes()).hexdigest()
    t0 = time.time()
    while True:
        f = file_report(key, sha, settle=False)
        if f.get("kaspersky") not in ("", "timeout") or time.time() - t0 > wait:
            break
        print(f"  Касперский ещё не ответил ({int(time.time() - t0)} с), всего ответили {f['total']}")
        time.sleep(30)
    print(f"VirusTotal, архив: {f['stats'].get('malicious', 0)} из {f['total']} (Касперский: {f.get('kaspersky') or 'нет ответа'})")
    u = None
    try:
        u = scan_url(key, f"https://github.com/komar-hubs/jarvis/releases/download/v{version}/jarvis_share.zip")
        print(f"VirusTotal, ссылка: {u['stats'].get('malicious', 0)} из {u['total']}")
    except Exception as e:
        print(f"VirusTotal, ссылка не проверена: {e}")
    if f["flagged"]:
        print("ВНИМАНИЕ: есть срабатывания, README не обновлён:", f["flagged"])
        return False
    update_docs(version, f, u)
    print("README.md и пост для канала обновлены.")
    return True


if __name__ == "__main__":
    sys.exit(0 if (recheck() if "--recheck" in sys.argv else check_release()) else 1)
