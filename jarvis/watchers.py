"""Watching things for the user: "скажи, когда выйдет новое видео у Robzi", "скажи, когда Куплинов начнёт стрим",
"скажи, когда скачается файл", "скажи, когда закроется блендер", "скажи, когда на сайте … появится …".

Each watch is polled in the background (YouTube feed every 15 min, Twitch every 3 min, pages every 10 min, local
things every few seconds) and reports through the initiative with "Открыть?" — never by grabbing the mouse.
Stored in data/watchers.json, so they survive restarts."""
import hashlib
import html
import re
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote_plus

import psutil
import requests

from .config import JsonStore

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/130 Safari/537.36",
     "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.6", "Cookie": "CONSENT=YES+1; SOCS=CAI"}
EVERY = {"youtube": 900, "twitch": 180, "page": 600, "process": 5, "download": 5, "media": 5}
MEDIA_ACTS = {"shutdown": "выключу компьютер", "sleep": "переведу компьютер в сон", "pause": "поставлю на паузу и выключу звук"}
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
PARTIAL = (".crdownload", ".part", ".partial", ".download", ".opdownload", ".tmp")


def youtube_channel(name):
    """'Robzi' / 'робзи' / '@robzi' / a channel URL -> (channel_id, title) or (None, None)."""
    from rapidfuzz import fuzz
    from .system import translit
    name = name.strip().strip("«»\"")
    m = re.search(r"(UC[\w-]{22})", name)
    if m:
        return m.group(1), name
    handle = re.search(r"@([\w.-]+)", name)
    cands = [handle.group(1)] if handle else []
    cands += [name.replace(" ", ""), translit(name).replace(" ", "")]
    for h in dict.fromkeys(c for c in cands if c and re.fullmatch(r"[\w.-]+", c) and c.isascii()):
        try:
            r = requests.get(f"https://www.youtube.com/@{h}", headers=H, timeout=15)
            if r.ok:
                m = re.search(r'"(?:channelId|externalId)":"(UC[\w-]{22})"', r.text)
                t = re.search(r'<meta property="og:title" content="([^"]+)"', r.text)
                if m:
                    return m.group(1), html.unescape(t.group(1)) if t else h
        except Exception:
            pass
    r = requests.get("https://www.youtube.com/results", params={"search_query": name, "sp": "EgIQAg=="}, headers=H, timeout=15)
    found = re.findall(r'"channelRenderer":\{"channelId":"(UC[\w-]{22})","title":\{"simpleText":"([^"]+)"', r.text)
    if not found:
        return None, None
    tl = translit(name.lower())
    best = max(found[:6], key=lambda x: max(fuzz.ratio(name.lower(), x[1].lower()), fuzz.ratio(tl, translit(x[1].lower()))))
    return best[0], html.unescape(best[1])


def youtube_latest(channel_id):
    r = requests.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}", headers=H, timeout=15)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for e in root.findall("a:entry", NS)[:10]:
        out.append({"id": e.findtext("yt:videoId", namespaces=NS), "title": e.findtext("a:title", namespaces=NS) or "",
                    "published": e.findtext("a:published", namespaces=NS) or ""})
    return out


def twitch_live(login):
    r = requests.get(f"https://www.twitch.tv/{login}", headers=H, timeout=15)
    return '"isLiveBroadcast":true' in r.text


def page_text(url):
    r = requests.get(url, headers=H, timeout=20)
    r.raise_for_status()
    t = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", r.text)
    t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"\s+", " ", t).strip()


class Watchers:
    def __init__(self, core):
        self.core = core
        self.store = JsonStore("watchers", default=[])
        if not isinstance(self.store.items, list):
            self.store.items = []
        self._t = {}
        self._rt = {}                 # watch id -> runtime state not worth writing to disk every 5 s
        self._sleep_timer = None
        self._lock = threading.RLock()

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="watchers").start()

    @property
    def items(self):
        return self.store.items

    def save(self):
        self.store.save()
        self.core.emit("watchers")

    # ---------------------------------------------------------------- adding
    def add(self, kind, **kw):
        w = {"id": uuid.uuid4().hex[:8], "kind": kind, "created": time.time(), "fired": 0, **kw}
        with self._lock:
            self.items.append(w)
            self.save()
        self.core.journal.record(f"слежу: {self.title(w)}", lambda: self.remove(w["id"]), "watch")
        return w

    def remove(self, wid):
        with self._lock:
            self.store.items = [w for w in self.items if w["id"] != wid]
            self.save()
        return True

    def find(self, spoken):
        from rapidfuzz import fuzz
        from .system import translit
        s = (spoken or "").lower()
        best = None
        for w in self.items:
            t = self.title(w).lower()
            sc = max(fuzz.partial_ratio(s, t), fuzz.partial_ratio(translit(s), translit(t)))
            if best is None or sc > best[0]:
                best = (sc, w)
        return best[1] if best and best[0] >= 70 else None

    @staticmethod
    def title(w):
        k = w["kind"]
        if k == "youtube":
            return f"новые видео {w.get('name')}"
        if k == "twitch":
            return f"стримы {w.get('login')}"
        if k == "page":
            return f"«{w['text']}» на {w['url']}" if w.get("text") else f"изменения {w['url']}"
        if k == "process":
            return f"когда закроется {w.get('name')}"
        if k == "download":
            return "окончание загрузки" + (f" «{w['name']}»" if w.get("name") else "")
        if k == "media":
            return {"shutdown": "выключить ПК", "sleep": "усыпить ПК", "pause": "выключить звук"}.get(w.get("act"), "") + ", когда досмотрите"
        return k

    def watch_youtube(self, name):
        cid, title = youtube_channel(name)
        if not cid:
            return None, f"Не нашёл канал «{name}» на YouTube."
        if any(w["kind"] == "youtube" and w.get("channel") == cid for w in self.items):
            return None, f"Я уже слежу за каналом {title}."
        vids = youtube_latest(cid)
        w = self.add("youtube", channel=cid, name=title, seen=[v["id"] for v in vids])
        last = f" Последнее видео — «{vids[0]['title']}»." if vids else ""
        return w, f"Слежу за каналом {title}. Скажу, как только выйдет новое видео.{last}"

    def watch_twitch(self, login):
        from .system import translit
        login = re.sub(r"[^\w]", "", translit(login.lower()) if not login.isascii() else login.lower())
        live = twitch_live(login)
        w = self.add("twitch", login=login, live=live)
        return w, (f"Слежу за {login} на Twitch." + (" Кстати, он уже в эфире." if live else " Скажу, когда начнётся стрим."))

    def watch_download(self, name=None):
        folder = Path.home() / "Downloads"
        partial = [p.name for p in folder.glob("*") if p.suffix.lower() in PARTIAL]
        if not partial and not name:
            return None, "Сейчас ничего не скачивается в папку «Загрузки»."
        w = self.add("download", name=name or "", folder=str(folder), partial=partial, once=True)
        return w, "Хорошо, скажу, когда загрузка закончится."

    def watch_process(self, name):
        from .system import resolve_alias
        q = resolve_alias(name).lower().replace(" ", "")
        procs = [p.info["name"] for p in psutil.process_iter(["name"]) if p.info["name"] and q in p.info["name"].lower().replace(" ", "")]
        if not procs:
            return None, f"Программа «{name}» сейчас не запущена."
        w = self.add("process", name=name, exe=procs[0], once=True)
        return w, f"Скажу, когда {name} закроется."

    def watch_page(self, url, text=None):
        if not re.match(r"^https?://", url):
            url = "https://" + url
        try:
            body = page_text(url)
        except Exception as e:
            return None, f"Не смог открыть {url}: {e}"
        if text and text.lower() in body.lower():
            return None, f"«{text}» там уже есть."
        w = self.add("page", url=url, text=text or "", hash=hashlib.md5(body.encode()).hexdigest(), once=bool(text))
        return w, (f"Слежу за {url}: скажу, когда появится «{text}»." if text else f"Скажу, когда страница {url} изменится.")

    def watch_media(self, act="shutdown", quiet=120):
        """"Выключи компьютер, когда досмотрю": waits until the sound has been silent AND nobody touched the mouse or
        keyboard for `quiet` seconds, warns, then acts a minute later. Only local checks every 5 s: the Windows
        output level meter and the last-input time — no network, no AI."""
        self.store.items = [w for w in self.items if w["kind"] != "media"]   # one at a time
        w = self.add("media", act=act, quiet=int(quiet), deadline=time.time() + 6 * 3600)
        return w, f"Хорошо. Как только видео закончится и станет тихо пару минут, {MEDIA_ACTS[act]}."

    def cancel_media(self):
        n = 0
        for w in [w for w in self.items if w["kind"] == "media"]:
            self.remove(w["id"])
            n += 1
        if self._sleep_timer:
            self._sleep_timer.cancel()
            self._sleep_timer = None
            n += 1
        return n

    # ---------------------------------------------------------------- polling
    def _loop(self):
        time.sleep(15)
        while True:
            time.sleep(3)
            for w in list(self.items):
                every = EVERY.get(w["kind"], 600)
                if time.time() - self._t.get(w["id"], 0) < every:
                    continue
                self._t[w["id"]] = time.time()
                try:
                    getattr(self, "_check_" + w["kind"])(w)
                except Exception as e:
                    if w["kind"] in ("youtube", "twitch", "page"):
                        w["error"] = str(e)[:200]  # network hiccups: try again next round
                    else:
                        self.core.log(f"Отслеживание {self.title(w)}: {e}", "warn")

    def _fire(self, w, text, url=None, priority=3):
        w["fired"] = time.time()
        w.pop("error", None)
        if url:
            from . import system as S
            self.core.initiative.propose(f"watch:{w['id']}:{url}", text + " Открыть?", priority=priority, title="Отслеживание",
                                         on_yes=lambda: S.open_target(url), cooldown_min=0, kind="watch")
        else:
            self.core.initiative.propose(f"watch:{w['id']}:{time.time()}", text, priority=priority, title="Отслеживание",
                                         cooldown_min=0, kind="watch")
        if w.get("once"):
            self.remove(w["id"])
        else:
            self.save()

    def _check_youtube(self, w):
        vids = youtube_latest(w["channel"])
        seen = set(w.get("seen", []))
        new = [v for v in vids if v["id"] not in seen]
        w["seen"] = [v["id"] for v in vids][:30] + [s for s in w.get("seen", []) if s not in {v["id"] for v in vids}][:20]
        if not new:
            self.store.save()
            return
        v = new[0]
        more = f" И ещё {len(new) - 1}." if len(new) > 1 else ""
        self._fire(w, f"Новое видео у {w['name']}: «{v['title']}».{more}", f"https://www.youtube.com/watch?v={v['id']}")

    def _check_twitch(self, w):
        live = twitch_live(w["login"])
        was = w.get("live")
        w["live"] = live
        if live and not was:
            self._fire(w, f"{w['login']} начал стрим на Twitch.", f"https://www.twitch.tv/{w['login']}")
        else:
            self.store.save()

    def _check_page(self, w):
        body = page_text(w["url"])
        if w.get("text"):
            if w["text"].lower() in body.lower():
                self._fire(w, f"На {w['url']} появилось «{w['text']}».", w["url"])
            return
        h = hashlib.md5(body.encode()).hexdigest()
        if h != w.get("hash"):
            w["hash"] = h
            self._fire(w, f"Страница {w['url']} изменилась.", w["url"], priority=2)

    def _check_media(self, w):
        from . import system as S
        rt = self._rt.setdefault(w["id"], {"since": time.time(), "heard": False})
        if time.time() > w.get("deadline", 0):
            self.remove(w["id"])
            self.core.log("«Выключить, когда досмотрю»: прошло 6 часов, отменил", "warn")
            return
        if self.core.tts_busy():
            rt["quiet"] = None          # Jarvis talking is not the movie
            return
        if S.audio_peak() > 0.003:
            rt["heard"], rt["quiet"] = True, None
            return
        rt["quiet"] = rt.get("quiet") or time.time()
        quiet = w.get("quiet", 120)
        # nothing was playing when asked (a pause, a menu): wait up to 15 minutes for the sound before trusting silence
        if not rt["heard"] and time.time() - rt["since"] < 15 * 60:
            return
        if time.time() - rt["quiet"] >= quiet and S.idle_seconds() >= quiet:
            self.remove(w["id"])
            self._rt.pop(w["id"], None)
            act = w.get("act", "shutdown")
            if act == "pause":
                S.media_control("pause")
                S.mute(True)
                return
            what = "Выключаю компьютер" if act == "shutdown" else "Перевожу компьютер в сон"
            self.core.say(f"Похоже, вы досмотрели. {what} через минуту. Скажите «Джарвис, отмени выключение», чтобы остановить.")
            self.core.notify("Через минуту", what.lower() + ". «Отмени выключение» — остановить.")
            if act == "shutdown":
                S.shutdown(delay=60)
            else:
                self._sleep_timer = threading.Timer(60, S.sleep_pc)
                self._sleep_timer.daemon = True
                self._sleep_timer.start()

    def _check_process(self, w):
        exe = w["exe"].lower()
        if not any((p.info["name"] or "").lower() == exe for p in psutil.process_iter(["name"])):
            self._fire(w, f"{w['name'].capitalize()} закрылся.")

    def _check_download(self, w):
        folder = Path(w["folder"])
        partial = [p for p in folder.glob("*") if p.suffix.lower() in PARTIAL]
        if w.get("name"):
            hit = [p for p in folder.glob("*") if w["name"].lower() in p.name.lower() and p.suffix.lower() not in PARTIAL]
            if hit and not any(w["name"].lower() in p.name.lower() for p in partial):
                self._fire(w, f"Скачалось: {hit[0].name}.", str(hit[0]))
            return
        if not partial:
            newest = max((p for p in folder.glob("*") if p.is_file()), key=lambda p: p.stat().st_mtime, default=None)
            self._fire(w, "Загрузка закончилась" + (f": {newest.name}." if newest else "."), str(newest) if newest else None)

    # ---------------------------------------------------------------- UI / AI
    def rpc_watches(self):
        return [{"id": w["id"], "kind": w["kind"], "title": self.title(w), "error": w.get("error"),
                 "fired": w.get("fired", 0)} for w in self.items]

    def rpc_watch_delete(self, id):
        if self._sleep_timer and any(w["id"] == id and w["kind"] == "media" for w in self.items):
            self._sleep_timer.cancel()
        return self.remove(id)

    def rpc_watch_media(self, act="shutdown"):
        return self.watch_media(act if act in MEDIA_ACTS else "shutdown")[1]

    def rpc_watch_media_cancel(self):
        from . import system as S
        n = self.cancel_media()
        S.cancel_shutdown()
        return n

    def tool_watch(self, a):
        kind = (a.get("kind") or "").lower()
        target = a.get("target") or a.get("name") or a.get("url") or ""
        if kind == "youtube":
            return self.watch_youtube(target)[1]
        if kind == "twitch":
            return self.watch_twitch(target)[1]
        if kind == "page":
            return self.watch_page(target, a.get("text"))[1]
        if kind == "process":
            return self.watch_process(target)[1]
        if kind == "download":
            return self.watch_download(target or None)[1]
        if kind == "media":
            return self.watch_media(target if target in MEDIA_ACTS else "shutdown")[1]
        raise ValueError("kind: youtube | twitch | page | process | download | media (target: shutdown|sleep|pause)")


# ====================================================================== voice rules
def register(R, core):
    W = lambda: core.watchers

    def bg(fn):
        def go():
            try:
                _, msg = fn()
            except Exception as e:
                core.log(f"Отслеживание: {e}", "warn")
                msg = "Не получилось: сеть не ответила."
            core.say(msg)
        threading.Thread(target=go, daemon=True).start()
        return True

    # "скажи, когда…" (the question rule may have cut "скажи" already: "когда выйдет новое видео у …")
    TELL = r"^(?:(?:скажи|сообщи|предупреди|оповести|дай\s+знать|напомни|уведоми|маякни|скажешь)\s+(?:мне\s+)?)?(?:как\s+только|когда)\s+"
    VID = r"(?:видео|ролик\w*|видос\w*|выпуск\w*|серия|серию)"

    @R(TELL + rf"(?:выйдет|появится|будет|выложат|выпустят|загрузят)\s+(?:нов\w+\s+)?{VID}\s+(?:у|на\s+канале|от|канала)\s+(.+)$"
       + rf"|{TELL}(.+?)\s+(?:выложит|выпустит|опубликует|снимет|загрузит|запилит)\s+(?:нов\w+\s+)?{VID}$"
       + rf"|^(?:следи|следить|подпишись)\s+(?:за\s+)?(?:каналом|ютубером|блогером|новыми\s+видео|видео)\s+(?:у\s+|от\s+)?(.+)$")
    def _yt(m, t):
        name = next(g for g in m.groups() if g)
        name = re.sub(r"\s+(?:на\s+)?(?:ютубе|youtube)$", "", name).strip()
        return bg(lambda: W().watch_youtube(name))

    @R(TELL + r"(.+?)\s+(?:начнет|запустит|включит|начинает)\s+(?:стрим|трансляцию|эфир)\w*$"
       r"|" + TELL + r"(?:начнется|начнет|будет)\s+(?:стрим|трансляция|эфир)\s+(?:у\s+)?(.+)$"
       r"|^(?:следи|следить)\s+за\s+(?:стримами|стримером|трансляциями)\s+(.+)$")
    def _tw(m, t):
        name = next(g for g in m.groups() if g)
        name = re.sub(r"\s+(?:на\s+)?(?:твиче|twitch)$", "", name).strip()
        return bg(lambda: W().watch_twitch(name))

    @R(TELL + r"(?:закончится|завершится|кончится)\s+(?:загрузка|скачивание|закачка)(?:\s+(.+))?$"
       r"|" + TELL + r"(?:скачается|загрузится|докачается)(?:\s+(?:файл\s+)?(.+))?$")
    def _dl(m, t):
        name = (m.group(1) or m.group(2) or "").strip() or None
        return bg(lambda: W().watch_download(name))

    @R(TELL + r"(?:закроется|завершится|закончит\s+работу|выключится)\s+(?:программа\s+|процесс\s+|игра\s+)?(.+)$")
    def _proc(m, t):
        return bg(lambda: W().watch_process(m.group(1).strip()))

    @R(TELL + r"(?:на\s+(?:сайте|странице)\s+)?(\S+\.\S+)\s+появится\s+(.+)$|" + TELL + r"(?:изменится|обновится)\s+(?:сайт|страница)\s+(\S+\.\S+)$")
    def _page(m, t):
        url, text = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), None)
        return bg(lambda: W().watch_page(url, text))

    # "выключи комп, когда досмотрю / после фильма / когда закончится видео"
    END = (r"(?:(?:когда|как\s+только|после\s+того\s+как)\s+(?:я\s+)?(?:досмотрю|досмотрим|закончится\s+(?:видео|фильм|серия|музыка|ролик)|"
           r"(?:видео|фильм|серия|ролик)\s+(?:закончится|кончится)|усну|засну)|после\s+(?:видео|фильма|серии|ролика))")
    PC = r"(?:компьютер|комп|пк|ноутбук|ноут)?"

    @R(rf"^(?:выключи|выруби|отключи)\s+{PC}\s*{END}$|^(усыпи|переведи\s+в\s+сон|сон)\s+{PC}\s*{END}$"
       rf"|^(выключи\s+звук|поставь\s+на\s+паузу|останови\s+(?:видео|музыку))\s+{END}$")
    def _media(m, t):
        act = "sleep" if m.group(1) else "pause" if m.group(2) else "shutdown"
        return W().watch_media(act)[1]

    @R(r"^(?:отмени|отмена|не\s+надо)\s+(?:выключени\w*|сон|усыплени\w*)(?:\s+(?:компьютера|пк|после\s+видео))?$|^не\s+выключай(?:\s+\w+)?$|^отмена\s+выключения$")
    def _media_cancel(m, t):
        from . import system as S
        n = W().cancel_media()
        S.cancel_shutdown()
        return "Выключение отменено." + (" Больше не жду конца видео." if n else "")

    @R(r"^(?:что|за\s+чем)\s+ты\s+(?:отслеживаешь|следишь)|^(?:мои\s+)?отслеживани[яе]$|^за\s+чем\s+следишь$")
    def _list(m, t):
        ws = W().items
        core.show_tab("hub")
        if not ws:
            return "Сейчас я ни за чем не слежу. Скажите, например: «скажи, когда выйдет новое видео у Робзи»."
        return "Слежу за: " + "; ".join(W().title(w) for w in ws[:6]) + "."

    @R(r"^(?:перестань|прекрати|хватит|не\s+надо|больше\s+не)\s+(?:следить|отслеживать)\s+(?:за\s+)?(.+)$|^(?:удали|отмени|убери)\s+отслеживани\w+\s+(.+)$")
    def _stop(m, t):
        w = W().find(m.group(1) or m.group(2))
        if not w:
            return "За таким я не слежу."
        W().remove(w["id"])
        return f"Больше не слежу: {W().title(w)}."
