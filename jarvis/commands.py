"""User command blocks (trigger phrases -> chain of actions) and data sources (integrations)."""
import datetime as dt
import json
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

import requests

from .config import JsonStore

ACTION_TYPES = {
    # any phrase Jarvis understands, as if said aloud: chains, search, mouse, mail, the AI — everything
    "voice":     {"title": "Фраза Джарвису (что угодно, как голосом)", "fields": ["value"]},
    "message":   {"title": "Сообщение в мессенджер", "fields": ["mode", "value", "body"]},
    "plugin":    {"title": "Запустить плагин", "fields": ["value", "body"]},
    "click_text": {"title": "Клик по надписи или кнопке на экране", "fields": ["value"]},
    "open":      {"title": "Открыть (сайт / файл / папку / программу)", "fields": ["value"]},
    "app":       {"title": "Запустить приложение по имени", "fields": ["value"]},
    "youtube":   {"title": "Включить видео с YouTube (первое по запросу)", "fields": ["value"]},
    "music":     {"title": "Включить музыку (первый трек, дальше похожие)", "fields": ["value"]},
    "close":     {"title": "Закрыть приложение", "fields": ["value"]},
    "say":       {"title": "Сказать фразу", "fields": ["value"]},
    "wait":      {"title": "Пауза (секунды)", "fields": ["value"]},
    "search":    {"title": "Загуглить", "fields": ["value"]},
    "hotkey":    {"title": "Горячая клавиша (ctrl+shift+esc)", "fields": ["value"]},
    "type":      {"title": "Напечатать текст", "fields": ["value"]},
    "window":    {"title": "Окно: свернуть/развернуть/закрыть/фокус/скрыть", "fields": ["mode", "value"]},
    "volume":    {"title": "Громкость (0-100, +10, -10, mute)", "fields": ["value"]},
    "media":     {"title": "Медиа (play / next / prev / stop)", "fields": ["value"]},
    "shell":     {"title": "Команда консоли (результат в {result})", "fields": ["value"]},
    "http":      {"title": "HTTP запрос (результат в {result})", "fields": ["method", "value", "body"]},
    "source":    {"title": "Взять данные из интеграции (в {result})", "fields": ["value"]},
    "ai":        {"title": "Спросить ИИ (ответ в {result})", "fields": ["value", "speak"]},
    "bridge":    {"title": "Задача агенту (claude / codex / antigravity / gemini)", "fields": ["mode", "value"]},
    "notify":    {"title": "Уведомление Windows", "fields": ["value"]},
    "timer":     {"title": "Таймер (например 25 минут)", "fields": ["value"]},
    "click":     {"title": "Клик мышью (x,y)", "fields": ["value"]},
    "clipboard": {"title": "Скопировать в буфер", "fields": ["value"]},
    "screenshot": {"title": "Скриншот", "fields": []},
    "run":       {"title": "Выполнить другую команду", "fields": ["value"]},
    "lock":      {"title": "Заблокировать ПК", "fields": []},
    # logic: flat blocks "Если … / Иначе / Конец", like a simple script; steps between them are indented in the editor
    "if":        {"title": "Если… (дальше шаги до «Конец»)", "fields": ["mode", "value", "body"]},
    "else":      {"title": "Иначе", "fields": []},
    "end":       {"title": "Конец условия или повтора", "fields": []},
    "repeat":    {"title": "Повторить N раз (до «Конец»), номер круга в {i}", "fields": ["value"]},
    "ask":       {"title": "Спросить и запомнить ответ", "fields": ["value", "body"]},
    "set":       {"title": "Запомнить в переменную", "fields": ["value", "body"]},
    "stop":      {"title": "Остановить команду", "fields": ["value"]},
}
BLOCK_OPEN = ("if", "repeat")
YES = re.compile(r"(?i)^\W*(?:да|ага|угу|конечно|давай|хочу|верно|ок|окей|yes|разумеется|естественно|можно|го)\b")

SOURCE_TYPES = {
    "http_json": "HTTP JSON (путь к полю, например current.temperature_2m)",
    "http_text": "HTTP текст / HTML (regex по желанию)",
    "command":   "Вывод консольной команды",
    "file":      "Содержимое файла",
    "window":    "Заголовок активного окна",
    "clipboard": "Буфер обмена",
    "process":   "Запущен ли процесс (имя .exe)",
    "rss":       "RSS лента (заголовки)",
}


def _norm(s):
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[^\w\s{}]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def json_path(data, path):
    cur = data
    for part in [p for p in re.split(r"\.|\[(\d+)\]", path or "") if p]:
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def render(template, vars_):
    def rep(m):
        k = m.group(1)
        v = vars_.get(k)
        if v is None:
            return m.group(0)
        if isinstance(v, (dict, list)):
            return json.dumps(v, ensure_ascii=False)
        return str(v)
    return re.sub(r"\{(\w+)\}", rep, template or "")


DEFAULT_COMMANDS = [
    {"name": "Рабочий режим", "triggers": ["рабочий режим", "протокол работа", "начинаем работать"],
     "reply": "Запускаю рабочий режим, сэр. Удачного дня.",
     "actions": [{"type": "app", "value": "visual studio code"}, {"type": "open", "value": "https://github.com"},
                 {"type": "app", "value": "telegram"}, {"type": "volume", "value": "30"}]},
    {"name": "Режим вечеринки", "triggers": ["протокол вечеринка", "режим вечеринки", "включи вечеринку"],
     "reply": "Протокол «Вечеринка» активирован. Постарайтесь ничего не разрушить, сэр.",
     "actions": [{"type": "volume", "value": "70"}, {"type": "open", "value": "https://music.yandex.ru"}]},
    {"name": "Игровой режим", "triggers": ["игровой режим", "протокол игры", "пора играть"],
     "reply": "Игровой режим. Пожалуйста, не забывайте моргать.",
     "actions": [{"type": "app", "value": "steam"}, {"type": "app", "value": "discord"},
                 {"type": "volume", "value": "50"}]},
    {"name": "Включи музыку", "triggers": ["включи музыку {query}", "поставь песню {query}", "включи песню {query}"],
     "reply": "Включаю {query}.",
     "actions": [{"type": "music", "value": "{query}"}]},
    {"name": "Найди на ютубе", "triggers": ["найди на ютубе {query}", "ютуб {query}"],
     "reply": "Открываю YouTube.",
     "actions": [{"type": "open", "value": "https://www.youtube.com/results?search_query={query}"}]},
    {"name": "Протокол чистый стол", "triggers": ["протокол чистый стол", "убери всё", "покажи рабочий стол"],
     "reply": "Убираю окна.", "actions": [{"type": "hotkey", "value": "windows+d"}]},
    {"name": "Фокус 25 минут", "triggers": ["режим фокуса", "помидор", "протокол фокус"],
     "reply": "Двадцать пять минут концентрации. Я вас не потревожу, сэр.",
     "actions": [{"type": "timer", "value": "25 минут"}, {"type": "close", "value": "telegram"},
                 {"type": "close", "value": "discord"}]},
]

DEFAULT_SOURCES = [
    {"name": "Биткоин", "type": "http_json", "url": "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd",
     "path": "bitcoin.usd", "template": "Биткоин стоит {value} долларов.", "triggers": ["сколько стоит биткоин", "курс биткоина"]},
    {"name": "Мой IP", "type": "http_text", "url": "https://api.ipify.org", "template": "Ваш внешний IP: {value}.",
     "triggers": ["какой у меня ip", "мой ip адрес"]},
    {"name": "Активное окно", "type": "window", "template": "Сейчас активно окно: {value}.", "triggers": ["какое окно активно"]},
    {"name": "Новости Хабра", "type": "rss", "url": "https://habr.com/ru/rss/articles/?fl=ru", "limit": 3,
     "template": "Свежее на Хабре: {value}", "triggers": ["что нового на хабре", "новости хабра"]},
]


DEFAULT_COMMANDS_V2 = [
    {"name": "Ухожу", "triggers": ["я ухожу", "я ушел", "я пошел", "ухожу из дома", "я убежал"],
     "reply": "Блокирую компьютер. До встречи, сэр.",
     "actions": [{"type": "media", "value": "stop"}, {"type": "wait", "value": "2.5"}, {"type": "lock"}]},
    {"name": "Спокойной ночи", "triggers": ["спокойной ночи", "я спать", "иду спать", "пора спать"],
     "reply": "Спокойной ночи, сэр. Я присмотрю за всем.",
     "actions": [{"type": "media", "value": "stop"}, {"type": "volume", "value": "20"}]},
    {"name": "Режим кино", "triggers": ["режим кино", "киновечер", "включи режим кино", "хочу посмотреть фильм"],
     "reply": "Приятного просмотра, сэр. Попкорн, увы, на вас.",
     "actions": [{"type": "open", "value": "https://www.kinopoisk.ru"}, {"type": "volume", "value": "60"}]},
]

DEFAULT_SOURCES_V2 = [
    {"name": "Новости", "type": "rss", "url": "https://lenta.ru/rss/top7", "limit": 3,
     "template": "Главное сейчас: {value}", "triggers": ["что нового", "новости", "какие новости", "что в мире"]},
]


class Commands:
    def __init__(self):
        self.store = JsonStore("commands", None)
        if not self.store.path.exists():
            self.store.items = [self._fill(c) for c in DEFAULT_COMMANDS]
            self.store.save()
        self.sources = JsonStore("sources", None)
        if not self.sources.path.exists():
            self.sources.items = [self._fill(s) for s in DEFAULT_SOURCES]
            self.sources.save()
        self._add_new_defaults()
        self._play_youtube_v3()
        self._play_music_v4()

    def _play_music_v4(self):
        """The default "Включи музыку" opened Яндекс Музыка's search: it now plays the first track (action "music").
        Changed once, only while its action is the untouched default."""
        marker = self.store.path.parent / ".defaults_v4"
        if marker.exists():
            return
        for c in self.store.items:
            acts = c.get("actions") or []
            # by the action, not the name: it may be renamed ("Найди музыку")
            if (len(acts) == 1 and acts[0].get("type") == "open"
                    and acts[0].get("value") == "https://music.yandex.ru/search?text={query}"
                    and any(x.startswith(("включи", "поставь")) for x in c.get("triggers", []))):
                c["actions"] = [{"type": "music", "value": "{query}"}]
                if c.get("reply") == "Ищу {query}.":
                    c["reply"] = "Включаю {query}."
                self.store.save()
        marker.write_text("1", "utf-8")

    def _play_youtube_v3(self):
        """"Включи на ютубе X" now plays the first video (intents): the old default trigger opened the results list and,
        as a user command, would win over it. Removed once, only from the untouched default."""
        marker = self.store.path.parent / ".defaults_v3"
        if marker.exists():
            return
        for c in self.store.items:
            acts = c.get("actions") or []
            if (c.get("name") == "Найди на ютубе" and "включи на ютубе {query}" in c.get("triggers", [])
                    and len(acts) == 1 and "youtube.com/results" in str(acts[0].get("value", ""))):
                c["triggers"] = [x for x in c["triggers"] if x != "включи на ютубе {query}"]
                self.store.save()
        marker.write_text("1", "utf-8")

    def _add_new_defaults(self):
        """Defaults added in later versions go to existing installs once (a deleted one is not re-added)."""
        marker = self.store.path.parent / ".defaults_v2"
        if marker.exists():
            return
        have = {c["name"].lower() for c in self.store.items}
        for c in DEFAULT_COMMANDS_V2:
            if c["name"].lower() not in have:
                self.store.items.append(self._fill(c))
        self.store.save()
        have_src = {s["name"].lower() for s in self.sources.items}
        for s in DEFAULT_SOURCES_V2:
            if s["name"].lower() not in have_src:
                self.sources.items.append(self._fill(s))
        self.sources.save()
        marker.write_text("1", "utf-8")

    @staticmethod
    def _fill(c):
        c = dict(c)
        c.setdefault("id", uuid.uuid4().hex[:8])
        c.setdefault("enabled", True)
        c.setdefault("triggers", [])
        return c

    # ----------------------------------------------------------- CRUD
    def save_command(self, cmd):
        cmd = self._fill(cmd)
        items = self.store.items
        for i, c in enumerate(items):
            if c["id"] == cmd["id"]:
                items[i] = cmd
                break
        else:
            items.append(cmd)
        self.store.save()
        return cmd

    def delete_command(self, cid):
        self.store.items = [c for c in self.store.items if c["id"] != cid]
        self.store.save()

    def save_source(self, src):
        src = self._fill(src)
        for i, s in enumerate(self.sources.items):
            if s["id"] == src["id"]:
                self.sources.items[i] = src
                break
        else:
            self.sources.items.append(src)
        self.sources.save()
        return src

    def delete_source(self, sid):
        self.sources.items = [s for s in self.sources.items if s["id"] != sid]
        self.sources.save()

    def get(self, name_or_id, kind="command"):
        items = self.store.items if kind == "command" else self.sources.items
        n = _norm(name_or_id)
        for c in items:
            if c["id"] == name_or_id or _norm(c["name"]) == n:
                return c
        from rapidfuzz import fuzz, process
        r = process.extractOne(n, [_norm(c["name"]) for c in items], scorer=fuzz.WRatio, score_cutoff=80)
        return items[r[2]] if r else None

    # ----------------------------------------------------------- matching
    def match(self, text, kind="command"):
        """Returns (item, vars) or (None, None). Supports {var} placeholders in triggers."""
        from rapidfuzz import fuzz
        t0 = _norm(text)
        # a trigger "рутрекер" must also fire on "открой рутрекер" / "запусти протокол рутрекер"
        t1 = re.sub(r"^(?:открой|запусти|включи|покажи|выполни|активируй|зайди на|перейди на)\s+(?:протокол\s+|сайт\s+)?",
                    "", t0)
        variants = list(dict.fromkeys([t0, t1]))
        items = self.store.items if kind == "command" else self.sources.items
        best, best_score, best_vars = None, 0, None
        builtin = {"time", "date", "result", "name", "value"}
        for c in items:
            if not c.get("enabled", True):
                continue
            # a command whose steps need {query} must not fire on its bare name: "включи музыку" opened
            # ".../search?text={query}" literally
            needs = set(re.findall(r"\{(\w+)\}", json.dumps(c.get("actions", []), ensure_ascii=False) + (c.get("reply") or "")))
            needs -= builtin
            for trig in c.get("triggers", []) + ([c["name"]] if kind == "command" and not needs else []):
                if needs and "{" not in trig:
                    continue
                tr = _norm(trig)
                if not tr:
                    continue
                for t in variants:
                    if "{" in tr:
                        pat = "^" + re.sub(r"\\\{(\w+)\\\}", r"(?P<\1>.+)", re.escape(tr)) + "$"
                        m = re.match(pat, t)
                        if m:
                            return c, {k: v.strip() for k, v in m.groupdict().items()}
                        continue
                    if t == tr:
                        return c, {}
                    score = fuzz.ratio(t, tr)
                    if score > best_score:
                        best, best_score, best_vars = c, score, {}
        if best and best_score >= 88:
            return best, best_vars
        return None, None

    # ----------------------------------------------------------- sources
    def fetch_source(self, src, sysmod=None):
        typ = src.get("type")
        val = None
        if typ in ("http_json", "http_text", "rss"):
            headers = {"User-Agent": "Mozilla/5.0 JARVIS"}
            for line in (src.get("headers") or "").splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip()] = v.strip()
            r = requests.get(src["url"], headers=headers, timeout=12)
            r.raise_for_status()
            if typ == "http_json":
                val = json_path(r.json(), src.get("path", ""))
            elif typ == "rss":
                import xml.etree.ElementTree as ET
                root = ET.fromstring(r.content)
                titles = [i.findtext("title") for i in root.iter("item")] or \
                         [e.findtext("{http://www.w3.org/2005/Atom}title") for e in root.iter("{http://www.w3.org/2005/Atom}entry")]
                val = ". ".join(t.strip() for t in titles[: int(src.get("limit", 3))] if t)
            else:
                val = r.text
                if src.get("regex"):
                    m = re.search(src["regex"], val, re.S)
                    val = (m.group(1) if m and m.groups() else m.group(0)) if m else None
                val = re.sub(r"<[^>]+>", " ", val or "")
                val = re.sub(r"\s+", " ", val).strip()[:2000]
        elif typ == "command":
            r = subprocess.run(src["command"], shell=True, capture_output=True, text=True, timeout=30,
                               encoding="cp866", errors="replace", creationflags=0x08000000)
            val = (r.stdout or r.stderr).strip()[:3000]
        elif typ == "file":
            val = Path(src["path_file"]).read_text("utf-8", "replace")[:5000]
        elif typ == "window":
            import win32gui
            val = win32gui.GetWindowText(win32gui.GetForegroundWindow())
        elif typ == "clipboard":
            import pyperclip
            val = pyperclip.paste()
        elif typ == "process":
            import psutil
            name = src.get("process", "").lower()
            val = "запущен" if any((p.info["name"] or "").lower() == name for p in psutil.process_iter(["name"])) else "не запущен"
        if isinstance(val, float):
            val = round(val, 2)
        return val

    def source_phrase(self, src, val):
        tpl = src.get("template") or "{value}"
        return render(tpl, {"value": val, "name": src["name"]})


class ChainRunner:
    """Executes a command's actions. ctx = Core (gives access to tts, system, planner, llm...)."""

    def __init__(self, core):
        self.core = core

    def run(self, cmd, vars_=None, depth=0):
        core = self.core
        if depth > 5:
            return
        v = {"time": dt.datetime.now().strftime("%H:%M"), "date": dt.date.today().strftime("%d.%m.%Y"),
             "result": "", "name": core.cfg.get("user.name", "")}
        v.update(vars_ or {})
        if cmd.get("reply"):
            core.say(render(cmd["reply"], v))
        self._exec(cmd.get("actions", []), v, depth, cmd["name"])
        core.emit("command_ran", name=cmd["name"])

    # ---------------------------------------------------------------- logic: if / else / repeat / ask / set / stop
    @staticmethod
    def _block(actions, i):
        """For the block opened at i: (index of its "Конец" or len, index of its "Иначе" or None)."""
        depth, els = 0, None
        for j in range(i + 1, len(actions)):
            t = actions[j].get("type")
            if t in BLOCK_OPEN:
                depth += 1
            elif t == "end":
                if depth == 0:
                    return j, els
                depth -= 1
            elif t == "else" and depth == 0 and els is None and actions[i].get("type") == "if":
                els = j
        return len(actions), els

    def _exec(self, actions, v, depth, name):
        """Runs a list of steps; returns "stop" when a «Остановить» step was reached."""
        i = 0
        while i < len(actions):
            a = actions[i]
            typ = a.get("type")
            try:
                if typ == "if":
                    end, els = self._block(actions, i)
                    ok = self.cond(a, v)
                    part = actions[i + 1:(els if els is not None else end)] if ok else (actions[els + 1:end] if els is not None else [])
                    if self._exec(part, v, depth, name) == "stop":
                        return "stop"
                    i = end + 1
                    continue
                if typ == "repeat":
                    end, _ = self._block(actions, i)
                    times = max(1, min(50, int(float(render(a.get("value", ""), v) or 1))))
                    for k in range(times):
                        v["i"] = k + 1
                        if self._exec(actions[i + 1:end], v, depth, name) == "stop":
                            return "stop"
                    i = end + 1
                    continue
                if typ in ("else", "end"):
                    i += 1
                    continue
                if typ == "stop":
                    if a.get("value"):
                        self.core.say(render(a["value"], v))
                    return "stop"
                if typ == "set":
                    var = re.sub(r"\W", "", a.get("value", "")) or "x"
                    v[var] = render(a.get("body", ""), v)
                elif typ == "ask":
                    var = re.sub(r"\W", "", a.get("body", "")) or "answer"
                    v[var] = self.ask(render(a.get("value", ""), v))
                    if v[var] is None:
                        v[var] = ""
                        self.core.say("Не услышал ответа. Останавливаю команду.")
                        return "stop"
                else:
                    self._do(a, v, depth)
            except Exception as e:
                self.core.log(f"Команда «{name}», шаг {typ}: {e}", "error")
            i += 1

    def ask(self, question, timeout=25):
        """Say the question and take the next phrase as the answer (no wake word needed)."""
        core = self.core
        got = {}
        done = threading.Event()
        fn = lambda raw: (got.setdefault("a", raw), done.set())
        core.capture = (fn, time.time() + timeout + 15)
        core.say(question)
        core.after_speech(lambda: core.listen_next(timeout))
        if not done.wait(timeout + 15):
            if isinstance(core.capture, tuple) and core.capture[0] is fn:
                core.capture = None
            return None
        return got["a"].strip()

    def cond(self, a, v):
        from . import system as s
        kind = a.get("mode") or "contains"
        val = render(a.get("value", ""), v).strip()
        other = render(a.get("body", ""), v).strip()
        now = dt.datetime.now()
        if kind == "time":
            m = re.findall(r"(\d{1,2})[:.](\d{2})", val)
            if len(m) < 2:
                return False
            f, t = (f"{int(h):02d}:{mm}" for h, mm in m[:2])
            cur = now.strftime("%H:%M")
            return f <= cur < t if f <= t else (cur >= f or cur < t)
        if kind == "days":
            low = val.lower()
            if "будн" in low:
                return now.weekday() < 5
            if "выходн" in low:
                return now.weekday() >= 5
            names = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
            return any(names[now.weekday()] == d.strip()[:2] for d in re.split(r"[,\s]+", low) if d.strip())
        if kind in ("running", "not_running"):
            import psutil
            q = val.lower().replace(" ", "").removesuffix(".exe")
            on = any(q and q in (p.info["name"] or "").lower().replace(" ", "") for p in psutil.process_iter(["name"]))
            return on if kind == "running" else not on
        if kind == "contains":
            return other.lower() in val.lower()
        if kind == "equals":
            return val.lower() == other.lower()
        if kind == "empty":
            return not val
        if kind == "not_empty":
            return bool(val)
        if kind == "answer_yes":
            return bool(YES.match(val))
        if kind == "battery_below":
            ac, pct = s.power_state()
            return pct is not None and pct < float(re.sub(r"[^\d.]", "", val) or 20)
        if kind == "online":
            try:
                return requests.head("https://www.google.com", timeout=4).status_code < 500
            except Exception:
                return False
        return False

    def _do(self, a, v, depth):
        from . import system as s
        core = self.core
        typ = a.get("type")
        val = render(a.get("value", ""), v)
        if typ == "open":
            from urllib.parse import quote_plus
            if "://" in a.get("value", "") and "{" in a.get("value", ""):
                val = render(a["value"], {k: quote_plus(str(x)) for k, x in v.items()})
            s.open_target(val)
        elif typ == "youtube":
            title = s.play_youtube(val.strip())
            v["result"] = title or ""
        elif typ == "music":
            title = s.play_music(val.strip(), core.cfg.get("music_service", "youtube"))
            v["result"] = title or ""
        elif typ == "app":
            ok, name = s.launch_app(val.strip().strip("\"'"), core.apps, core.cfg.get("aliases"), loose=True)
            if not ok:
                core.log(f"Не нашёл приложение «{val}»", "warn")
        elif typ == "close":
            s.close_app(val, core.apps, core.cfg.get("aliases"))
        elif typ == "say":
            core.say(val)
        elif typ == "wait":
            time.sleep(min(600, float(val or 1)))
        elif typ == "search":
            s.search_web(val, core.cfg.get("search_url"))
        elif typ == "hotkey":
            s.hotkey(val)
        elif typ == "type":
            s.type_text(val)
        elif typ == "window":
            s.window_action(val, a.get("mode", "minimize"))
        elif typ == "volume":
            if val == "mute":
                s.mute(True)
            elif val.startswith(("+", "-")):
                s.change_volume(int(val))
            else:
                s.set_volume(int(val))
        elif typ == "media":
            s.media(val or "play")
        elif typ == "shell":
            v["result"] = s.run_shell(val)
        elif typ == "http":
            method = (a.get("method") or "GET").upper()
            body = render(a.get("body", ""), v)
            r = requests.request(method, val, data=body.encode() if body else None, timeout=15,
                                 headers={"Content-Type": "application/json"} if body.strip().startswith("{") else None)
            v["result"] = r.text[:3000]
        elif typ == "source":
            src = core.commands.get(val, "source")
            if src:
                v["result"] = core.commands.fetch_source(src)
        elif typ == "ai":
            prompt = val
            answer = core.ask_ai_once(prompt)
            v["result"] = answer
            if a.get("speak", True) not in (False, "false", "0", ""):
                core.say(answer)
        elif typ == "bridge":
            agent = a.get("mode") or "claude"
            v["result"] = core.bridges.ask(agent, val)
            core.say(core.summarize_for_voice(v["result"]))
        elif typ == "notify":
            core.notify("J.A.R.V.I.S.", val)
        elif typ == "timer":
            from .planner import parse_duration
            secs = parse_duration(val) or int(float(val or 60))
            core.planner.add_timer(secs, a.get("label", ""))
        elif typ == "click":
            xy = [int(x) for x in re.findall(r"-?\d+", val)][:2]
            s.mouse_click(*xy) if len(xy) == 2 else s.mouse_click()
        elif typ == "clipboard":
            s.clipboard_set(val)
        elif typ == "screenshot":
            s.screenshot()
        elif typ == "run":
            other = core.commands.get(val)
            if other:
                self.run(other, v, depth + 1)
        elif typ == "lock":
            s.lock()
        elif typ == "voice":
            # waits until the phrase (with all its steps) is done, so the next step comes after it
            core.run_phrase_sync(val)
        elif typ == "message":
            from . import messenger
            who = val.strip()
            text = render(a.get("body", ""), v).strip()
            if who and text:
                messenger.send(core, a.get("mode") or "telegram", messenger.contact_name(core, who), text)
        elif typ == "plugin":
            item = core.plugins.get(val)
            if not item:
                core.log(f"Плагин «{val}» не найден", "warn")
                return
            res, err = core.plugins.call(item, render(a.get("body", ""), v) or (item["triggers"][0] if item["triggers"] else ""))
            v["result"] = res or ""
            if err:
                core.log(f"Плагин «{item['name']}»: {err}", "error")
            elif res:
                core.say(str(res))
        elif typ == "click_text":
            from . import mouse
            if not mouse.click_target(core, val):
                core.log(f"Не нашёл на экране «{val}»", "warn")
