"""Custom plugins: your own functions for Jarvis, one .py file each in data/plugins.

    NAME = "Курс биткоина"
    DESCRIPTION = "Говорит цену биткоина"
    TRIGGERS = ["курс биткоина", "сколько стоит биткоин", "биткоин в {валюта}"]   # phrases; {x} catches a word
    # or a regular expression starting with ^:  TRIGGERS = [r"^брось кубик d(\\d+)$"]

    def run(text, args, api):
        data = api.get_json("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd")
        return f"Биткоин стоит {data['bitcoin']['usd']} долларов."   # what Jarvis says

`api`: get / get_json / post_json (internet), say, open, notify, clipboard, type, hotkey, run("любая команда
Джарвису"), and api.store — a dict kept between calls. Files are picked up on the fly (no restart). Each call runs
in its own process with a time limit, so a broken plugin cannot freeze or crash Jarvis. Plugins are your code:
Jarvis reads only NAME/TRIGGERS/DESCRIPTION without running the file until a trigger is said."""
import ast
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import DATA, ROOT

DIR = DATA / "plugins"
HOST = ROOT / "jarvis" / "plugin_host.py"
CREATE_NO_WINDOW = 0x08000000

TEMPLATE = '''"""{desc}"""
NAME = "{name}"
DESCRIPTION = "{desc}"
# Фразы, на которые срабатывает плагин (после «Джарвис»). {{слово}} ловит любое слово или хвост фразы,
# а строка с ^ в начале — регулярное выражение.
TRIGGERS = [{triggers}]


def run(text, args, api):
    """text — что сказали, args — значения {{слов}} из триггера, api — возможности Джарвиса:
    api.get(url) / api.get_json(url) / api.post_json(url, data) — интернет
    api.say(текст), api.open(сайт или файл), api.notify(заголовок, текст), api.clipboard(текст),
    api.type(текст), api.hotkey("ctrl+s"), api.run("любая команда Джарвису")
    api.store — словарь, который сохраняется между вызовами
    Что вернёте строкой — Джарвис скажет вслух."""
    api.store["calls"] = api.store.get("calls", 0) + 1
    return f"Плагин «{name}» работает. Вызван уже {{api.store['calls']}} раз."
'''

EXAMPLE = '''"""Пример плагина: игральные кости для настольных игр."""
import random

NAME = "Кости для настолок"
DESCRIPTION = "«брось d20», «брось 3d6» — бросает кости и говорит сумму"
TRIGGERS = [r"^(?:брось|кинь)\\s+(?:кубик\\s+)?(\\d*)\\s*(?:д|d|дэ|ди)\\s*(\\d+)$"]


def run(text, args, api):
    import re
    m = re.match(TRIGGERS[0], text)
    n = int(m.group(1) or 1) if m else 1
    sides = int(m.group(2)) if m else 20
    n = max(1, min(n, 20))
    rolls = [random.randint(1, sides) for _ in range(n)]
    if n == 1:
        return f"Выпало {rolls[0]}." + (" Критический успех!" if rolls[0] == sides else "")
    return f"Выпало {', '.join(map(str, rolls))}. Сумма {sum(rolls)}."
'''


AI_GUIDE = '''Ты помогаешь написать плагин для голосового ассистента «Джарвис» (Windows, Python 3.12).
Плагин — один .py файл:

NAME = "Короткое название"
DESCRIPTION = "Что делает, одной фразой"
TRIGGERS = ["фраза", "фраза с {слово}"]  # фразы в нижнем регистре без знаков препинания, как их скажут после «Джарвис»;
# {имя} ловит кусок фразы и приходит в args["имя"]; строка, начинающаяся с ^, — регулярное выражение
# (тогда группы берёшь сам через re.match(TRIGGERS[i], text))

def run(text, args, api):
    ...
    return "фраза, которую Джарвис скажет вслух (коротко, по-русски)"

Что есть в api:
- api.get(url) -> str, api.get_json(url) -> dict/list, api.post_json(url, data) — интернет (requests внутри)
- api.say(текст), api.notify(заголовок, текст), api.open(сайт/файл/папка), api.clipboard(текст),
  api.type(текст) — напечатать в активное окно, api.hotkey("ctrl+s"),
  api.run("любая команда Джарвису, например: открой телеграм и напиши Ване привет")
- api.store — dict, который сохраняется между вызовами (счётчики, настройки, кэш)
Правила: только стандартная библиотека и requests; без input(), без бесконечных циклов (время на вызов ~30 с);
ошибки сети ловить и возвращать понятную фразу; бесплатные API без ключей, если пользователь не дал ключ;
не удалять файлы и не трогать систему без явной просьбы пользователя.'''


PERMS = {
    "net": "интернет",
    "open": "открывать сайты, файлы и программы",
    "keys": "печатать текст, нажимать клавиши, буфер обмена",
    "run": "давать Джарвису любые команды",
    "system": "файлы и система: удалять, запускать программы, реестр",
}
_MOD_PERM = {"requests": "net", "urllib": "net", "http": "net", "socket": "net", "httpx": "net", "aiohttp": "net",
             "ftplib": "net", "smtplib": "net", "websocket": "net", "websockets": "net", "ssl": "net",
             "webbrowser": "open",
             "pyautogui": "keys", "keyboard": "keys", "pynput": "keys", "pyperclip": "keys", "win32clipboard": "keys",
             "subprocess": "system", "ctypes": "system", "winreg": "system", "shutil": "system", "multiprocessing": "system",
             "win32api": "system", "win32con": "system", "win32com": "system", "win32process": "system", "_winapi": "system",
             "psutil": "system", "signal": "system"}
_API_PERM = {"get": "net", "get_json": "net", "post_json": "net", "open": "open", "type": "keys", "hotkey": "keys",
             "clipboard": "keys", "run": "run"}
_OS_SYSTEM = {"system", "popen", "remove", "unlink", "rmdir", "removedirs", "rename", "renames", "replace", "kill",
              "startfile", "execv", "execl", "spawnl", "spawnv", "chmod", "truncate"}


def needs(src):
    """What a plugin's code can do, from reading it (not running it): {"net", "open", "keys", "run", "system"}."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                p = _MOD_PERM.get(a.name.split(".")[0])
                if p:
                    out.add(p)
        elif isinstance(n, ast.ImportFrom) and n.module:
            p = _MOD_PERM.get(n.module.split(".")[0])
            if p:
                out.add(p)
        elif isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name):
            if n.value.id == "api" and n.attr in _API_PERM:
                out.add(_API_PERM[n.attr])
            elif n.value.id == "os" and n.attr in _OS_SYSTEM:
                out.add("system")
        elif isinstance(n, ast.Attribute) and n.attr in ("unlink", "rmdir", "rmtree", "write_text", "write_bytes"):
            out.add("system")
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("open", "exec", "eval", "__import__"):
            if n.func.id != "open":
                out.add("system")
            else:                       # open(..., "w") writes files
                mode = n.args[1] if len(n.args) > 1 else next((k.value for k in n.keywords if k.arg == "mode"), None)
                if isinstance(mode, ast.Constant) and isinstance(mode.value, str) and set(mode.value) & set("wax+"):
                    out.add("system")
    return out


def _meta(path):
    """NAME / DESCRIPTION / TRIGGERS read with ast: the file is not executed here."""
    src = path.read_text("utf-8", errors="replace")
    tree = ast.parse(src)
    meta = {"name": path.stem, "description": "", "triggers": []}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            key = node.targets[0].id
            if key in ("NAME", "DESCRIPTION", "TRIGGERS"):
                try:
                    meta[key.lower()] = ast.literal_eval(node.value)
                except Exception:
                    pass
    if not any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body):
        raise ValueError("нет функции run(text, args, api)")
    meta["triggers"] = [str(t) for t in meta["triggers"] if str(t).strip()]
    return meta


META_KEYS = ("NAME", "DESCRIPTION", "TRIGGERS")

BODY_TEMPLATES = {
    "blank": '''def run(text, args, api):
    """text — фраза целиком, args — значения {слов} из триггера.
    Что вернёте строкой, Джарвис скажет вслух."""
    return "Готово."
''',
    "web": '''def run(text, args, api):
    # курс доллара и евро по ЦБ; замените адрес и разбор ответа на свой сервис
    try:
        data = api.get_json("https://www.cbr-xml-daily.ru/daily_json.js")
    except Exception:
        return "Сервис сейчас не отвечает."
    usd = data["Valute"]["USD"]["Value"]
    eur = data["Valute"]["EUR"]["Value"]
    return f"Доллар {usd:.2f}, евро {eur:.2f} рубля."
''',
    "open": '''SITES = [
    "https://mail.google.com",
    "https://calendar.google.com",
]


def run(text, args, api):
    for url in SITES:
        api.open(url)
    return "Открываю."
''',
    "counter": '''import datetime


def run(text, args, api):
    # счётчик за день, хранится между запусками в api.store
    today = datetime.date.today().isoformat()
    if api.store.get("day") != today:
        api.store["day"], api.store["count"] = today, 0
    api.store["count"] += 1
    return f"Записал. Сегодня уже {api.store['count']}."
''',
    "chain": '''def run(text, args, api):
    # несколько команд Джарвису подряд, как если бы вы сказали их голосом
    api.run("громкость 30")
    api.run("открой телеграм")
    api.notify("Рабочий режим", "Громкость 30, Telegram открыт")
    return "Рабочий режим включён."
''',
}


def _lit(s):
    """A Python string literal; regex triggers stay readable as r"..."."""
    s = str(s)
    if "\\" in s and '"' not in s and not s.endswith("\\") and "\n" not in s:
        return f'r"{s}"'
    return json.dumps(s, ensure_ascii=False)


def _header(name, description, triggers):
    trig = [_lit(t) for t in triggers if str(t).strip()]
    one = "TRIGGERS = [" + ", ".join(trig) + "]"
    lines = [f"NAME = {_lit(name)}", f"DESCRIPTION = {_lit(description)}"]
    lines += [one] if len(one) <= 110 else ["TRIGGERS = ["] + [f"    {t}," for t in trig] + ["]"]
    return lines


def _split(src):
    """Plugin file -> form fields (NAME / DESCRIPTION / TRIGGERS) and the code without them."""
    meta = {"name": "", "description": "", "triggers": []}
    lines = src.replace("\r\n", "\n").split("\n")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return meta | {"body": src, "parsed": False}
    drop = set()
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in META_KEYS):
            try:
                meta[node.targets[0].id.lower()] = ast.literal_eval(node.value)
            except Exception:
                return meta | {"body": src, "parsed": False}   # computed value: leave the file as it is
            first = node.lineno - 1
            while first > 0 and lines[first - 1].lstrip().startswith("#"):
                first -= 1                       # the comment above belongs to the field
            drop.update(range(first, node.end_lineno))
    doc = tree.body[0] if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(getattr(tree.body[0], "value", None), ast.Constant) and isinstance(tree.body[0].value.value, str) else None
    if doc is not None and doc.value.value.strip() in (str(meta["description"]).strip(), ""):
        drop.update(range(doc.lineno - 1, doc.end_lineno))   # the template repeats DESCRIPTION as a docstring
    body = "\n".join(l for i, l in enumerate(lines) if i not in drop)
    body = re.sub(r"\n{3,}", "\n\n\n", body).strip("\n") + "\n"
    meta["triggers"] = [str(t) for t in meta["triggers"]] if isinstance(meta["triggers"], (list, tuple)) else []
    return meta | {"body": body, "parsed": True}


def _compose(name, description, triggers, body):
    """Form fields + code -> plugin file. Returns (source, header_at, header_len) so editor line numbers can be
    mapped to file line numbers in tracebacks."""
    body = (body or "").replace("\r\n", "\n")
    try:
        tree = ast.parse(body)
        nodes = tree.body
    except SyntaxError:
        tree, nodes = None, []
    # NAME / DESCRIPTION / TRIGGERS typed into the code by hand are dropped: the form owns them
    if tree is not None:
        rm = set()
        for n in nodes:
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) and n.targets[0].id in META_KEYS:
                rm.update(range(n.lineno - 1, n.end_lineno))
        if rm:
            bl = body.split("\n")
            body = "\n".join(l for i, l in enumerate(bl) if i not in rm)
            tree = ast.parse(body)
            nodes = tree.body
    el = body.split("\n")                    # editor lines; ast line numbers index into this
    at = 0                                   # after a module docstring and the imports at the top
    for n in nodes:
        is_doc = n is nodes[0] and isinstance(n, ast.Expr) and isinstance(getattr(n, "value", None), ast.Constant) and isinstance(n.value.value, str)
        if is_doc or isinstance(n, (ast.Import, ast.ImportFrom)):
            at = n.end_lineno
        else:
            break
    first = next((i for i, l in enumerate(el) if l.strip()), len(el))
    start = max(at, first)
    while start < len(el) and not el[start].strip():
        start += 1
    end = len(el)
    while end > start and not el[end - 1].strip():
        end -= 1
    top = list(range(first, at)) if at > first else []
    hdr = ([""] if top else []) + _header(name, description, triggers) + ["", ""]
    src = "\n".join([el[i] for i in top] + hdr + el[start:end]) + "\n"
    # file line -> editor line (0 = a line of the header the form owns)
    lmap = [i + 1 for i in top] + [0] * len(hdr) + [i + 1 for i in range(start, end)]
    return src, lmap


def _compile_trigger(tr):
    from .commands import _norm
    if tr.startswith("^"):
        return re.compile(tr)
    pat = "^" + re.sub(r"\\\{(\w+)\\\}", r"(?P<\1>.+?)", re.escape(_norm(tr))) + "$"
    return re.compile(pat)


class Plugins:
    def __init__(self, core):
        self.core = core
        self.items = {}       # file name -> {name, description, triggers, rx, path, mtime, error}
        self.last_effects = []
        DIR.mkdir(parents=True, exist_ok=True)
        if not any(DIR.glob("*.py")) and not (DIR / ".example_added").exists():
            (DIR / "кости_для_настолок.py").write_text(EXAMPLE, "utf-8")
            (DIR / ".example_added").write_text("1", "utf-8")
        self.scan()

    def start(self):
        def loop():
            while True:
                time.sleep(4)
                try:
                    self.scan()
                except Exception as e:
                    self.core.log(f"Плагины: {e}", "warn")
        threading.Thread(target=loop, daemon=True, name="plugins").start()

    # ---------------------------------------------------------------- discovery
    def scan(self):
        seen, changed = set(), False
        for p in sorted(DIR.glob("*.py")):
            seen.add(p.name)
            mt = p.stat().st_mtime
            old = self.items.get(p.name)
            if old and old["mtime"] == mt:
                continue
            changed = True
            item = {"file": p.name, "path": str(p), "mtime": mt, "error": None, "name": p.stem, "description": "",
                    "triggers": [], "rx": [], "needs": []}
            try:
                item.update(_meta(p))
                item["rx"] = [_compile_trigger(t) for t in item["triggers"]]
                item["needs"] = sorted(needs(p.read_text("utf-8", errors="replace")))
            except Exception as e:
                item["error"] = str(e)
            self.items[p.name] = item
            if old:
                self.core.log(f"Плагин «{item['name']}» обновлён" + (f" — ошибка: {item['error']}" if item["error"] else ""))
        for gone in set(self.items) - seen:
            self.items.pop(gone)
            changed = True
        if not self.core.cfg.get("plugins.perms_migrated"):
            # plugins from before permissions keep working: they get what their code already uses
            self.core.cfg.set("plugins.perms", {f: i["needs"] for f, i in self.items.items()})
            self.core.cfg.set("plugins.perms_migrated", True)
        if changed:
            self.core.emit("plugins")

    # ---------------------------------------------------------------- permissions
    def granted(self, file):
        return set((self.core.cfg.get("plugins.perms") or {}).get(file, []))

    def grant(self, file, perms):
        allp = dict(self.core.cfg.get("plugins.perms") or {})
        allp[file] = sorted(set(perms) & set(PERMS))
        self.core.cfg.set("plugins.perms", allp)

    def missing(self, item):
        return sorted(set(item.get("needs") or []) - self.granted(item["file"]))

    def enabled(self, item):
        return item["file"] not in (self.core.cfg.get("plugins.disabled") or [])

    def match(self, t):
        for item in self.items.values():
            if item["error"] or not self.enabled(item):
                continue
            for rx in item["rx"]:
                m = rx.match(t)
                if m:
                    return item, {k: v for k, v in m.groupdict().items() if v is not None}
        return None, None

    def get(self, name):
        from rapidfuzz import fuzz
        n = (name or "").lower().strip()
        if not n:
            return None
        score = lambda i: max(fuzz.ratio(n, i["name"].lower()), fuzz.ratio(n, i["file"].lower()),
                              fuzz.partial_ratio(n, i["name"].lower()) - 5)  # "кости" finds "Кости для настолок"
        best = max(self.items.values(), key=score, default=None)
        return best if best and score(best) >= 75 else None

    # ---------------------------------------------------------------- running
    def call(self, item, text, args=None, timeout=None):
        """Run in a separate process. Returns (spoken result or None, error or None)."""
        store_p = DIR / (Path(item["file"]).stem + ".json")
        try:
            store = json.loads(store_p.read_text("utf-8")) if store_p.exists() else {}
        except Exception:
            store = {}
        py = Path(sys.executable)
        if py.name.lower() == "pythonw.exe" and py.with_name("python.exe").exists():
            py = py.with_name("python.exe")
        payload = json.dumps({"path": item["path"], "text": text, "args": args or {}, "store": store,
                              "perms": sorted(self.granted(item["file"]))}, ensure_ascii=False)
        try:
            r = subprocess.run([str(py), "-I", str(HOST)], input=payload.encode("utf-8"), capture_output=True,
                               timeout=timeout or float(self.core.cfg.get("plugins.timeout", 30)),
                               cwd=str(DIR) if len(str(DIR)) < 200 else None,  # very long folders break CreateProcess
                               creationflags=CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            return None, "плагин работал слишком долго и был остановлен"
        except OSError as e:
            return None, f"не удалось запустить: {e}"
        try:
            out = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
        except Exception:
            return None, (r.stderr.decode("utf-8", "replace") or "плагин ничего не вернул")[-800:]
        try:
            store_p.write_text(json.dumps(out.get("store") or {}, ensure_ascii=False, indent=1), "utf-8")
        except Exception:
            pass
        self.last_effects = out.get("effects") or []
        self._effects(self.last_effects)
        if not out.get("ok"):
            return None, out.get("error") or "ошибка"
        return out.get("result"), None

    def _effects(self, effects):
        from . import system as S
        core = self.core
        for e in effects[:30]:
            kind, args = e[0], e[1:]
            try:
                if kind == "say":
                    core.say(args[0])
                elif kind == "open":
                    S.open_target(args[0])
                elif kind == "notify":
                    core.notify(args[0], args[1] if len(args) > 1 else "")
                elif kind == "clipboard":
                    S.clipboard_set(args[0])
                elif kind == "type":
                    S.type_text(args[0])
                elif kind == "hotkey":
                    S.hotkey(args[0])
                elif kind == "run":
                    core.run_phrase(args[0])
            except Exception as ex:
                core.log(f"Плагин ({kind}): {ex}", "warn")

    def run_async(self, item, text, args=None):
        miss = self.missing(item)
        if miss:
            # first run of a plugin that wants more than it was allowed: ask, run on "да"
            def yes():
                self.grant(item["file"], self.granted(item["file"]) | set(miss))
                self.run_async(item, text, args)
            self.core.ask_confirm(yes, f"Плагин «{item['name']}» хочет: {', '.join(PERMS[p] for p in miss)}. Разрешить?")
            return

        def go():
            self.core.set_state("thinking")
            res, err = self.call(item, text, args)
            if err:
                self.core.log(f"Плагин «{item['name']}»: {err}", "error")
                self.core.say(f"Плагин «{item['name']}» сломался. Подробности в журнале и во вкладке «Плагины».")
                self.core.emit("plugin_error", file=item["file"], error=err)
            elif res not in (None, ""):
                self.core.say(str(res))
            if not self.core.tts_busy():
                self.core.set_state("idle")
        threading.Thread(target=go, daemon=True).start()

    # ---------------------------------------------------------------- UI
    def rpc_plugins(self):
        return {"dir": str(DIR), "perm_titles": PERMS,
                "items": [{k: v for k, v in i.items() if k not in ("rx",)} | {"enabled": self.enabled(i),
                          "granted": sorted(self.granted(i["file"])), "missing": self.missing(i)} for i in self.items.values()]}

    def rpc_plugin_grant(self, file, perms):
        self.grant(Path(file).name, perms)
        self.core.emit("plugins")
        return sorted(self.granted(Path(file).name))

    def rpc_plugin_needs(self, source):
        """For the editor: what the code being typed can do."""
        return sorted(needs(source))

    def rpc_plugin_export(self, file, folder):
        import shutil
        src = DIR / Path(file).name
        dst = Path(folder) / src.name
        shutil.copy2(src, dst)
        return str(dst)

    def rpc_plugin_inspect(self, path):
        """A .py from outside, before installing: what it is and what it can do. Nothing is run."""
        p = Path(path)
        if p.suffix.lower() != ".py":
            raise ValueError("плагин — это файл .py")
        src = p.read_text("utf-8", errors="replace")
        s = _split(src)
        imports = set()
        try:
            tree = ast.parse(src)
            has_run = any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body)
            err = None if has_run else "в файле нет функции run(text, args, api) — это не плагин Джарвиса"
            for n in ast.walk(tree):
                if isinstance(n, ast.Import):
                    imports |= {a.name.split(".")[0] for a in n.names}
                elif isinstance(n, ast.ImportFrom) and n.module:
                    imports.add(n.module.split(".")[0])
        except SyntaxError as e:
            err = f"синтаксическая ошибка в строке {e.lineno}"
        imports = sorted(imports)
        return {"name": s["name"] or p.stem, "description": s["description"], "triggers": s["triggers"],
                "needs": sorted(needs(src)), "lines": src.count("\n") + 1, "imports": imports, "error": err,
                "exists": (DIR / p.name).exists(), "source": src[:20000]}

    def rpc_plugin_import(self, path, perms=None):
        import shutil
        info = self.rpc_plugin_inspect(path)
        if info["error"]:
            raise ValueError(info["error"])
        name = Path(path).name if not (DIR / Path(path).name).exists() else self._free_name(Path(path).stem)
        shutil.copy2(path, DIR / name)
        self.grant(name, perms or [])
        self.scan()
        return name

    def rpc_plugin_source(self, file):
        return (DIR / Path(file).name).read_text("utf-8", errors="replace")

    def rpc_plugin_open(self, file):
        """The editor form: fields and the code without them (parsed=False: the file is shown whole)."""
        name = Path(file).name
        out = _split((DIR / name).read_text("utf-8", errors="replace"))
        item = self.items.get(name, {})
        return out | {"file": name, "error": item.get("error"), "enabled": bool(item) and self.enabled(item)}

    def rpc_plugin_templates(self):
        return BODY_TEMPLATES

    def _free_name(self, name):
        base = re.sub(r"[^\w-]+", "_", (name or "plugin").lower()).strip("_") or "plugin"
        p, i = DIR / f"{base}.py", 2
        while p.exists():
            p, i = DIR / f"{base}_{i}.py", i + 1
        return p.name

    def rpc_plugin_store(self, file="", name="", description="", triggers=None, body="", raw=False, perms=None):
        """Save from the form. file="" creates a new plugin named after `name`. A syntax error is reported with the
        editor's line number and nothing is written."""
        name = (name or "").strip() or "Без названия"
        triggers = [str(t).strip() for t in (triggers or []) if str(t).strip()]
        if raw:
            src, lmap = body.replace("\r\n", "\n"), None
        else:
            src, lmap = _compose(name, description.strip(), triggers, body)
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            ln = lmap[e.lineno - 1] if lmap and e.lineno and 0 < e.lineno <= len(lmap) else e.lineno
            raise ValueError(f"синтаксическая ошибка в строке {ln}: {e.msg}")
        if not any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body):
            raise ValueError("в коде нет функции run(text, args, api)")
        if not raw and not triggers:
            raise ValueError("добавьте хотя бы одну фразу-триггер")
        fname = Path(file).name if file else self._free_name(name)
        if not fname.endswith(".py"):
            fname += ".py"
        (DIR / fname).write_text(src, "utf-8")
        if perms is not None:                    # the editor's checkboxes: the user saw the code and chose
            self.grant(fname, perms)
        self.scan()
        return {"file": fname, "error": self.items.get(fname, {}).get("error"), "lmap": lmap}

    def rpc_plugin_save(self, file, source):
        name = Path(file).name
        if not name.endswith(".py"):
            name += ".py"
        ast.parse(source)  # a syntax error is reported before the file is written
        (DIR / name).write_text(source, "utf-8")
        self.scan()
        return self.items.get(name, {}).get("error")

    def rpc_plugin_new(self, name="Новый плагин", trigger="мой плагин"):
        base = re.sub(r"[^\w-]+", "_", name.lower()).strip("_") or "plugin"
        p = DIR / f"{base}.py"
        i = 2
        while p.exists():
            p = DIR / f"{base}_{i}.py"
            i += 1
        p.write_text(TEMPLATE.format(name=name.replace('"', "'"), desc=f"Мой плагин «{name}»",
                                     triggers=json.dumps(trigger, ensure_ascii=False)), "utf-8")
        self.scan()
        return p.name

    def rpc_plugin_toggle(self, file, on):
        dis = set(self.core.cfg.get("plugins.disabled") or [])
        (dis.discard if on else dis.add)(file)
        self.core.cfg.set("plugins.disabled", sorted(dis))
        self.core.emit("plugins")
        return on

    def rpc_plugin_delete(self, file):
        p = DIR / Path(file).name
        trash = DIR / ".deleted"
        trash.mkdir(exist_ok=True)
        if p.exists():
            os.replace(p, trash / f"{int(time.time())}_{p.name}")  # kept, not destroyed
        self.scan()
        return True

    def rpc_plugin_test(self, file, text=""):
        item = self.items.get(Path(file).name)
        if not item:
            raise ValueError("плагин не найден")
        if item["error"]:
            return {"ok": False, "error": item["error"]}
        if self.missing(item):
            return {"ok": False, "need_perms": self.missing(item), "error": "нужны разрешения"}
        args = {}
        for rx in item["rx"]:
            m = rx.match(text) if text else None
            if m:
                args = {k: v for k, v in m.groupdict().items() if v is not None}
                break
        t0 = time.time()
        self.last_effects = []
        res, err = self.call(item, text or (item["triggers"][0] if item["triggers"] else ""), args)
        return {"ok": not err, "result": res, "error": err, "ms": int((time.time() - t0) * 1000), "args": args,
                "effects": [[str(x)[:120] for x in e] for e in self.last_effects]}

    def rpc_plugins_folder(self):
        os.startfile(DIR)
        return True

    # ---------------------------------------------------------------- AI helper
    def rpc_plugin_ai_agents(self):
        """Who can help: the AI from Settings, and the bridges that are installed."""
        prov = self.core.cfg.provider()
        out = [{"id": "api", "title": f"ИИ из настроек ({prov.get('title')}{', ' + prov['model'] if prov.get('model') else ''})"}]
        for k, st in self.core.bridges.status().items():
            if st.get("found"):
                out.append({"id": k, "title": f"Мост: {st['title']}"})
        return out

    def rpc_plugin_ai(self, request, via="api", source="", error="", file=None, form=None):
        """The AI writes or fixes a plugin. Returns {"code", "parts", "note", "error"}; nothing is saved — the user
        reviews the code in the editor and saves it. `form` = the editor's {name, description, triggers, body}."""
        if form and (form.get("body") or "").strip():
            source = _compose(form.get("name") or "Плагин", form.get("description") or "", form.get("triggers") or [],
                              form["body"])[0]
        task = request.strip() or ("Исправь ошибку." if error else "Улучши плагин.")
        prompt = (AI_GUIDE + "\n\n" +
                  (f"Текущий код плагина ({file}):\n```python\n{source}\n```\n\n" if source.strip() else
                   "Плагина ещё нет — напиши его с нуля.\n\n") +
                  (f"При проверке была ошибка:\n```\n{error[-2000:]}\n```\n\n" if error else "") +
                  f"Задача пользователя: {task}\n\nВерни ВЕСЬ файл плагина целиком одним блоком ```python ... ``` и "
                  f"после него одну-две строки по-русски, что сделано.")
        if via != "api":
            text = self.core.bridges.ask(via, prompt, workdir=str(DIR), timeout=600)
        else:
            from .llm import complete
            prov = self.core.cfg.provider()
            if prov.get("kind") == "bridge":
                text = self.core.bridges.ask(prov["agent"], prompt, workdir=str(DIR), timeout=600)
            else:
                text = complete(prov, [{"role": "system", "content": "Ты опытный Python-разработчик. Пиши рабочий, "
                                                                     "простой код без лишних зависимостей."},
                                       {"role": "user", "content": prompt}], temperature=0.2, max_tokens=4000)
        m = re.search(r"```(?:python|py)?\s*\n(.*?)```", text or "", re.S)
        code = (m.group(1) if m else text or "").strip() + "\n"
        note = re.sub(r"```.*?```", "", text or "", flags=re.S).strip()[:600]
        err = None
        try:
            tree = ast.parse(code)
            if not any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body):
                err = "в ответе нет функции run(text, args, api)"
        except SyntaxError as e:
            err = f"синтаксическая ошибка в строке {e.lineno}: {e.msg}"
        suggested = None
        if not file:
            nm = re.search(r'^NAME\s*=\s*["\'](.+?)["\']', code, re.M)
            base = re.sub(r"[^\w-]+", "_", (nm.group(1) if nm else "plugin").lower()).strip("_") or "plugin"
            suggested = base + ".py"
            i = 2
            while (DIR / suggested).exists():
                suggested = f"{base}_{i}.py"
                i += 1
        return {"code": code, "parts": None if err else _split(code), "note": note, "error": err, "file": suggested}


# ====================================================================== voice rules
def register(R, core):
    @R(r"^(?:создай|сделай|добавь|новый)\s+(?:новый\s+)?плагин(?:\s+(.+))?$")
    def _new(m, t):
        name = (m.group(1) or "Новый плагин").strip()
        f = core.plugins.rpc_plugin_new(name.capitalize(), name.lower())
        core.show_tab("plugins")
        core.emit("plugin_open", file=f)
        return f"Создал заготовку плагина «{name}». Она открыта во вкладке «Плагины»: впишите фразы и код или попросите ИИ."

    @R(r"^(?:какие|мои|покажи)\s+(?:у\s+меня\s+)?плагин\w*$|^список\s+плагинов$")
    def _list(m, t):
        items = list(core.plugins.items.values())
        core.show_tab("plugins")
        if not items:
            return "Плагинов пока нет. Скажите «создай плагин …» или положите .py файл в папку data\\plugins."
        return f"Плагинов {len(items)}: " + ", ".join(i["name"] for i in items[:8]) + "."

    @R(r"^(?:открой|покажи)\s+папку\s+плагинов$")
    def _folder(m, t):
        core.plugins.rpc_plugins_folder()
        return True

    @R(r"^(?:выключи|отключи)\s+плагин\s+(.+)$|^(?:включи)\s+плагин\s+(.+)$")
    def _toggle(m, t):
        item = core.plugins.get(m.group(1) or m.group(2))
        if not item:
            return "Такого плагина нет."
        core.plugins.rpc_plugin_toggle(item["file"], bool(m.group(2)))
        return f"Плагин «{item['name']}» {'включён' if m.group(2) else 'выключен'}."
