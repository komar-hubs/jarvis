"""Everyday skills from the friend's wish list: maths and unit conversion, currencies and crypto, news, maps,
agenda and meetings, notes and the shopping list (stored as tagged tasks, so they show and can be edited in
the Tasks tab), files in the active Explorer window, music by genre, and the system commands that were missing.

register(R, core) adds the router rules. It runs before the other rules, so these precise patterns win over
the generic "открой X" / "покажи X" ones."""
import ast
import datetime as dt
import math
import operator
import random
import re
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

import requests

from . import info, nlu
from . import system as S
from .planner import MONTHS, parse_duration, parse_when, plural, say_duration, say_when

UA = {"User-Agent": "Mozilla/5.0 JARVIS"}
DOCS = Path.home() / "Documents"


# ===================================================================== numbers
def fmt(x):
    """Speakable number: 42, 3,14, 0,0021."""
    if isinstance(x, bool):
        return str(x)
    if isinstance(x, float):
        if math.isinf(x) or math.isnan(x):
            return "бесконечность"
        if abs(x - round(x)) < 1e-9 and abs(x) < 1e15:
            x = int(round(x))
    if isinstance(x, int):
        return str(x)
    s = f"{x:.6g}" if abs(x) >= 1e-4 else f"{x:.3g}"
    if "e" in s:
        m, e = s.split("e")
        return f"{m.replace('.', ',')} на десять в степени {int(e)}"
    return s.replace(".", ",")


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUN = {"sqrt": math.sqrt, "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x),
        "sin": lambda x: math.sin(math.radians(x)), "cos": lambda x: math.cos(math.radians(x)),
        "tan": lambda x: math.tan(math.radians(x)), "cot": lambda x: 1 / math.tan(math.radians(x)),
        "log": math.log10, "ln": math.log, "abs": abs, "fact": lambda x: math.factorial(int(x))}


def safe_eval(expr):
    """Arithmetic only (no names, no attributes): the input comes from speech."""
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(b) > 300:
                raise ValueError("слишком большая степень")
            return _OPS[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUN and len(n.args) == 1:
            return _FUN[n.func.id](ev(n.args[0]))
        if isinstance(n, ast.Name) and n.id in ("pi", "e"):
            return math.pi if n.id == "pi" else math.e
        raise ValueError("непонятное выражение")
    return ev(ast.parse(expr, mode="eval"))


def words_to_expr(s):
    s = " " + s.lower().replace("ё", "е") + " "
    s = re.sub(r"(?<=\d),(?=\d)", ".", s)
    s = re.sub(r"(?<=\d)\s*[xх×]\s*(?=\d)", " * ", s)
    subs = [
        (r"квадратн\w* корн\w* из", " sqrt "), (r"кубическ\w* корн\w* из", " cbrt "), (r"корн\w* (?:квадратн\w* )?из", " sqrt "),
        (r"\bкорень\b", " sqrt "), (r"(\d+(?:\.\d+)?)\s*факториал", r" fact(\1) "), (r"факториал\w* (?:от |числа )?", " fact "),
        (r"(синус|косинус|тангенс|котангенс)\w*\s+(?:от\s+|угла\s+)?(-?\d+(?:\.\d+)?)\s*(?:градус\w*)?",
         lambda m: " {}({}) ".format({"синус": "sin", "косинус": "cos", "тангенс": "tan", "котангенс": "cot"}[m.group(1)], m.group(2))),
        (r"логарифм\w* (?:от )?", " log "), (r"\bв квадрате\b", " **2 "), (r"\bв кубе\b", " **3 "),
        (r"\bв (?:\d+\s+)?степени\b", lambda m: " ** " if "степени" in m.group(0) and not re.search(r"\d", m.group(0))
         else " ** " + re.search(r"\d+", m.group(0)).group(0) + " "),
        (r"\b(?:умножить|умножь|помножить|умноженное|помножь)\w*(?: на)?\b", " * "),
        (r"\b(?:разделить|раздели|поделить|подели|делить|деленное|разделенное)\w*(?: на)?\b", " / "),
        (r"\bплюс\b", " + "), (r"\bминус\b", " - "), (r"\bпроцент\w* от\b", " /100* "),
        (r"\bчисло пи\b|\bпи\b", " pi "), (r"\bоткрыть скобку\b|\bскобка\b", " ( "), (r"\bзакрыть скобку\b", " ) "),
    ]
    for rx, rep in subs:
        s = re.sub(rx, rep, s)
    s = re.sub(r"\b(sqrt|cbrt|fact|log)\s+(-?\d+(?:\.\d+)?)", r"\1(\2)", s)
    s = re.sub(r"[^\d+\-*/().a-z ]", " ", s)
    s = re.sub(r"\b(?!sqrt|cbrt|sin|cos|tan|cot|log|ln|fact|pi|e\b)[a-z]+\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def calc(text):
    expr = words_to_expr(text)
    if not re.search(r"\d|pi", expr) or not re.search(r"[+\-*/]|sqrt|cbrt|sin|cos|tan|cot|log|fact|pi", expr):
        return None
    try:
        return safe_eval(expr)
    except Exception:
        return None


def solve_equation(text):
    import sympy as sp
    s = text.lower().replace("икс", "x").replace("игрек", "y").replace("равно", "=").replace("^", "**")
    s = words_to_expr_keep_vars(s)
    if "=" not in s:
        return None
    lhs, rhs = s.split("=", 1)
    names = sorted(set(re.findall(r"\b[xyz]\b", s))) or ["x"]
    syms = [sp.Symbol(n) for n in names]
    sol = sp.solve(sp.Eq(sp.sympify(lhs, rational=True), sp.sympify(rhs, rational=True)), syms, dict=True)
    if not sol:
        return "Решений нет."
    parts = []
    for d in sol[:3]:
        parts.append(", ".join(f"{k} = {fmt(float(v)) if v.is_number and v.is_real else v}" for k, v in d.items()))
    return "; ".join(parts)


def words_to_expr_keep_vars(s):
    s = re.sub(r"(?<=\d),(?=\d)", ".", s)
    for rx, rep in ((r"\bплюс\b", "+"), (r"\bминус\b", "-"), (r"\b(?:умножить|умножь)\w*(?: на)?\b", "*"),
                    (r"\b(?:разделить|поделить|делить)\w*(?: на)?\b", "/"), (r"\bв квадрате\b", "**2"), (r"\bв кубе\b", "**3")):
        s = re.sub(rx, rep, s)
    s = re.sub(r"(\d)\s*([xyz])\b", r"\1*\2", s)
    return re.sub(r"[^\dxyz+\-*/().= ]", " ", s)


# ===================================================================== units
UNITS = [  # stem, dimension, factor to the base unit, (one, few, many)
    (r"морск\w*\s+мил", "len", 1852, ("морская миля", "морские мили", "морских миль")),
    (r"километр|\bкм\b", "len", 1000, ("километр", "километра", "километров")),
    (r"сантиметр|\bсм\b", "len", 0.01, ("сантиметр", "сантиметра", "сантиметров")),
    (r"миллиметр|\bмм\b", "len", 0.001, ("миллиметр", "миллиметра", "миллиметров")),
    (r"\bмил[ьяеию]", "len", 1609.344, ("миля", "мили", "миль")),
    (r"метр", "len", 1, ("метр", "метра", "метров")),
    (r"\bфут", "len", 0.3048, ("фут", "фута", "футов")),
    (r"дюйм", "len", 0.0254, ("дюйм", "дюйма", "дюймов")),
    (r"\bярд", "len", 0.9144, ("ярд", "ярда", "ярдов")),
    (r"килограмм|\bкг\b|\bкило\b", "mass", 1, ("килограмм", "килограмма", "килограммов")),
    (r"миллиграмм", "mass", 1e-6, ("миллиграмм", "миллиграмма", "миллиграммов")),
    (r"грамм", "mass", 0.001, ("грамм", "грамма", "граммов")),
    (r"\bтонн", "mass", 1000, ("тонна", "тонны", "тонн")),
    (r"\bфунт", "mass", 0.45359237, ("фунт", "фунта", "фунтов")),
    (r"\bунци", "mass", 0.028349523, ("унция", "унции", "унций")),
    (r"миллилитр|\bмл\b", "vol", 0.001, ("миллилитр", "миллилитра", "миллилитров")),
    (r"кубометр|кубическ\w* метр", "vol", 1000, ("кубометр", "кубометра", "кубометров")),
    (r"литр", "vol", 1, ("литр", "литра", "литров")),
    (r"галлон", "vol", 3.785411784, ("галлон", "галлона", "галлонов")),
    (r"\bпинт", "vol", 0.473176, ("пинта", "пинты", "пинт")),
    (r"терабайт", "data", 1024 ** 4, ("терабайт", "терабайта", "терабайт")),
    (r"гигабайт|\bгиг", "data", 1024 ** 3, ("гигабайт", "гигабайта", "гигабайт")),
    (r"мегабайт|\bмег", "data", 1024 ** 2, ("мегабайт", "мегабайта", "мегабайт")),
    (r"килобайт", "data", 1024, ("килобайт", "килобайта", "килобайт")),
    (r"байт", "data", 1, ("байт", "байта", "байт")),
    (r"\bбит", "data", 0.125, ("бит", "бита", "бит")),
    (r"миллисекунд", "time", 0.001, ("миллисекунда", "миллисекунды", "миллисекунд")),
    (r"секунд", "time", 1, ("секунда", "секунды", "секунд")),
    (r"минут", "time", 60, ("минута", "минуты", "минут")),
    (r"\bчас", "time", 3600, ("час", "часа", "часов")),
    (r"\bсут", "time", 86400, ("сутки", "суток", "суток")),
    (r"\bдень|\bдня\b|\bдней\b|\bдня[хм]|\bдн[её]", "time", 86400, ("день", "дня", "дней")),
    (r"недел", "time", 604800, ("неделя", "недели", "недель")),
    (r"месяц", "time", 2629746, ("месяц", "месяца", "месяцев")),
    (r"\bгод|\bлет\b", "time", 31556952, ("год", "года", "лет")),
    (r"цельси", "temp", "C", ("градус Цельсия", "градуса Цельсия", "градусов Цельсия")),
    (r"фаренгейт", "temp", "F", ("градус Фаренгейта", "градуса Фаренгейта", "градусов Фаренгейта")),
    (r"кельвин", "temp", "K", ("кельвин", "кельвина", "кельвинов")),
]


def find_unit(words):
    for stem, dim, factor, forms in UNITS:
        if re.search(stem, words):
            return dim, factor, forms
    return None


def unit_word(forms, x):
    if isinstance(x, float) and not x.is_integer():
        return forms[1]  # "2,5 метра"
    return plural(int(x), *forms)


def convert(value, src, dst):
    (d1, f1, _), (d2, f2, _) = src, dst
    if d1 != d2:
        return None
    if d1 == "temp":
        k = {"C": lambda v: v + 273.15, "F": lambda v: (v - 32) * 5 / 9 + 273.15, "K": lambda v: v}[f1](value)
        return {"C": lambda v: v - 273.15, "F": lambda v: (v - 273.15) * 9 / 5 + 32, "K": lambda v: v}[f2](k)
    return value * f1 / f2


# ===================================================================== currencies
CUR = [(r"доллар|бакс|\busd\b", "USD", ("доллар", "доллара", "долларов")), (r"\bевро\b", "EUR", ("евро", "евро", "евро")),
       (r"юан", "CNY", ("юань", "юаня", "юаней")), (r"фунт\w* стерлинг", "GBP", ("фунт", "фунта", "фунтов")),
       (r"тенге", "KZT", ("тенге", "тенге", "тенге")), (r"гривн", "UAH", ("гривна", "гривны", "гривен")),
       (r"белорусск\w* рубл", "BYN", ("белорусский рубль", "белорусских рубля", "белорусских рублей")),
       (r"[иий]ен", "JPY", ("иена", "иены", "иен")), (r"\bлир", "TRY", ("лира", "лиры", "лир")),
       (r"франк", "CHF", ("франк", "франка", "франков")), (r"рубл", "RUB", ("рубль", "рубля", "рублей"))]
_rates = {"t": 0, "v": None}


def rates():
    if not _rates["v"] or time.time() - _rates["t"] > 3600:
        d = requests.get("https://www.cbr-xml-daily.ru/daily_json.js", timeout=8).json()["Valute"]
        _rates["v"] = {k: v["Value"] / v["Nominal"] for k, v in d.items()} | {"RUB": 1.0}
        _rates["t"] = time.time()
    return _rates["v"]


def find_cur(words):
    for stem, code, forms in CUR:
        if re.search(stem, words):
            return code, forms
    return None


# ===================================================================== news
FEEDS = {"": ("https://lenta.ru/rss/news", "Новости"), "технолог": ("https://habr.com/ru/rss/news/?fl=ru", "Новости технологий"),
         "it": ("https://habr.com/ru/rss/news/?fl=ru", "Новости IT"), "наук": ("https://nplus1.ru/rss", "Новости науки"),
         "спорт": ("https://www.sports.ru/rss/main.xml", "Спорт"), "игр": ("https://stopgame.ru/rss/rss_news.xml", "Игровые новости"),
         "экономик": ("https://lenta.ru/rss/news/economics", "Экономика")}


def news(topic=""):
    url, title = next((v for k, v in FEEDS.items() if k and k in topic), FEEDS[""])
    r = requests.get(url, headers=UA, timeout=8)
    root = ET.fromstring(r.content)
    items = []
    for it in root.iter("item"):
        t = (it.findtext("title") or "").strip()
        if t:
            items.append((t, (it.findtext("link") or "").strip()))
        if len(items) >= 8:
            break
    return title, items


# ===================================================================== Explorer
def explorer_context(require_foreground=False):
    """(folder, [selected paths]) of the active Explorer window (or the first open one)."""
    import pythoncom
    import win32com.client
    import win32gui
    pythoncom.CoInitialize()
    fg = win32gui.GetForegroundWindow()
    first = None
    for w in win32com.client.Dispatch("Shell.Application").Windows():
        try:
            folder = w.Document.Folder.Self.Path
            sel = [it.Path for it in w.Document.SelectedItems()]
        except Exception:
            continue
        if int(w.HWND) == fg:
            return folder, sel
        first = first or (folder, sel)
    return (None, []) if require_foreground else (first or (None, []))


def recycle(path):
    """Delete to the Recycle Bin (recoverable), never permanently."""
    from win32com.shell import shell, shellcon
    flags = shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION | shellcon.FOF_SILENT | shellcon.FOF_NOERRORUI
    res, aborted = shell.SHFileOperation((0, shellcon.FO_DELETE, str(path), None, flags, None, None))
    return res == 0 and not aborted


def human_size(n):
    for unit, k in (("терабайт", 1024 ** 4), ("гигабайт", 1024 ** 3), ("мегабайт", 1024 ** 2), ("килобайт", 1024)):
        if n >= k:
            return f"{fmt(round(n / k, 1))} {unit}"
    return f"{n} {plural(n, 'байт', 'байта', 'байт')}"


def tree_size(p, limit_s=6.0):
    p, t0, total = Path(p), time.time(), 0
    if p.is_file():
        return p.stat().st_size
    for f in p.rglob("*"):
        if time.time() - t0 > limit_s:
            break
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def selected_text():
    """Text selected in the active app (copied through the clipboard, which is restored afterwards)."""
    old = S.clipboard_get()
    S.clipboard_set("")
    S.hotkey("ctrl+c")
    time.sleep(0.3)
    txt = S.clipboard_get()
    S.clipboard_set(old)
    return txt


def browser_url():
    if not S.focus_browser():
        return None
    old = S.clipboard_get()
    S.hotkey("ctrl+l")
    time.sleep(0.15)
    S.hotkey("ctrl+c")
    time.sleep(0.25)
    url = S.clipboard_get()
    S.hotkey("esc")
    S.clipboard_set(old)
    return url if re.match(r"^https?://", url or "") else None


# ===================================================================== rules
ORD = {"перв": 1, "втор": 2, "трет": 3, "четверт": 4, "пят": 5, "шест": 6, "седьм": 7, "восьм": 8, "девят": 9, "десят": 10}


def register(R, core):
    cfg = core.cfg
    tasks = core.planner

    def addr():
        return cfg.get("user.address") or "сэр"

    def tagged(tag):
        return [t for t in tasks.sorted_tasks(True) if tag in (t.get("tags") or [])]

    # ---------------------------------------------------------------- system and sound
    @R(r"^(?:сделай\s+)?(?:по)?громче(?:\s+на\s+(\d+))?$|^(?:сделай\s+)?звук\s+(?:по)?громче$")
    def _louder(m, t):
        S.change_volume(int(m.group(1) or 10))
        return True

    @R(r"^(?:сделай\s+)?(?:по)?тише(?:\s+на\s+(\d+))?$|^(?:сделай\s+)?звук\s+(?:по)?тише$")
    def _quieter(m, t):
        S.change_volume(-int(m.group(1) or 10))
        return True

    @R(r"^(?:включи\s+|поставь\s+|активируй\s+)?(?:беззвучн\w+|бесшумн\w+)(?:\s+режим)?$|^режим\s+без\s+звука$")
    def _silent(m, t):
        S.mute(True)
        return "Беззвучный режим."

    @R(r"^(?:выключи|отключи|заверши работу)\s+(?:пк|компьютер|систему)\s+через\s+(.+)$")
    def _shutdown_in(m, t):
        secs = parse_duration(m.group(1))
        if not secs:
            return False

        def go():
            S.shutdown(delay=secs)
            core.say(f"Компьютер выключится через {say_duration(secs)}. Чтобы отменить, скажите «отмени выключение».")
        core.ask_confirm(go, f"Выключить компьютер через {say_duration(secs)}? Скажите «да».")
        return True

    @R(r"^(?:отмени|отмена|останови)\s+(?:выключени\w*|перезагрузк\w*)(?:\s+(?:компьютера|пк|системы))?$|^не выключай(?:\s+\w+)?$")
    def _cancel_shutdown(m, t):
        S.cancel_shutdown()
        return "Выключение отменено."

    @R(r"^(?:покажи|открой)\s+рабочий\s+стол$")
    def _desktop(m, t):
        S.hotkey("windows+d")
        return True

    @R(r"^(?:вставь|вставить)(?:\s+(?:текст|это|скопированное|из буфера))?$")
    def _paste(m, t):
        S.hotkey("ctrl+v")
        return True

    @R(r"^(?:отмени|отменить)\s+(?:последнее\s+)?действие$|^(?:верни|откати)\s+как\s+было$")
    def _undo(m, t):
        S.hotkey("ctrl+z")
        return True

    @R(r"^(?:сколько|какой объем|проверь)\s+(?:свободного\s+)?(?:места|памяти)(?:\s+(?:осталось\s+)?на\s+диске\s*(\w)?)?$"
       r"|^(?:сколько\s+)?(?:свободно\s+)?(?:места\s+)?на\s+диске\s*(\w)$")
    def _disk(m, t):
        import psutil
        letter = (m.group(1) or m.group(2) or "c").lower()
        letter = {"с": "C", "ц": "C", "д": "D", "е": "E", "ф": "F"}.get(letter, letter.upper())
        try:
            u = psutil.disk_usage(f"{letter}:\\")
        except Exception:
            return f"Диска {letter} не вижу, {addr()}."
        return f"На диске {letter} свободно {human_size(u.free)} из {human_size(u.total)}."

    # ---------------------------------------------------------------- maths
    @R(r"^(?:какое\s+)?число\s+пи$|^чему\s+равно\s+(?:число\s+)?пи$|^сколько\s+(?:будет\s+)?пи$")
    def _pi(m, t):
        return "Пи — три целых, одна тысяча четыреста пятнадцать десятитысячных: 3,14159265."

    @R(r"^(?:назови|скажи|выбери|дай|загадай|придумай)?\s*(?:мне\s+)?случайн\w+\s+число\s+от\s+(-?\d+)\s+до\s+(-?\d+)$")
    def _rand(m, t):
        a, b = sorted((int(m.group(1)), int(m.group(2))))
        return f"{random.randint(a, b)}."

    @R(r"^(?:реши|решите|решить)\s+(?:уравнение|пример)\s*:?\s*(.+)$")
    def _equation(m, t):
        try:
            res = solve_equation(m.group(1))
        except Exception:
            res = None
        return res if res else False  # cannot parse: the AI solves it

    @R(r"^сколько\s+процент\w*\s+(?:составляет|будет|это)\s+(-?\d+(?:[.,]\d+)?)\s+от\s+(-?\d+(?:[.,]\d+)?)$")
    def _share(m, t):
        a, b = float(m.group(1).replace(",", ".")), float(m.group(2).replace(",", "."))
        v = round(a / b * 100, 2) if b else None
        return (f"{fmt(v)} {unit_word(('процент', 'процента', 'процентов'), v)}." if b else "На ноль делить нельзя.")

    @R(r"^(?:посчитай|сколько будет|какая будет)?\s*скидк\w*\s+(\d+(?:[.,]\d+)?)\s*процент\w*\s+(?:от|с|на)\s+(\d+(?:[.,]\d+)?)")
    def _discount(m, t):
        p, price = float(m.group(1).replace(",", ".")), float(m.group(2).replace(",", "."))
        off = price * p / 100
        return f"Скидка {fmt(round(off, 2))}, итого {fmt(round(price - off, 2))}."

    @R(r"^(?:сколько\s+будет\s+|посчитай\s+|вычисли\s+)?(\d+(?:[.,]\d+)?)\s*процент\w*\s+от\s+(\d+(?:[.,]\d+)?)$")
    def _percent_of(m, t):
        return f"{fmt(round(float(m.group(1).replace(',', '.')) * float(m.group(2).replace(',', '.')) / 100, 4))}."

    @R(r"^(?:возведи|возвести)\s+(-?\d+(?:[.,]\d+)?)\s+в\s+(квадрат|куб|(\d+)\s*(?:-?ю|-?ую)?\s*степень|степень\s+(\d+))$")
    def _power(m, t):
        x = float(m.group(1).replace(",", "."))
        n = 2 if m.group(2) == "квадрат" else 3 if m.group(2) == "куб" else int(m.group(3) or m.group(4))
        return f"Будет {fmt(x ** n)}."

    @R(r"^(?:извлеки|вычисли|посчитай|найди|сколько будет|чему равен)?\s*(квадратн\w*|кубическ\w*)?\s*корень\s+(?:квадратный\s+)?из\s+(-?\d+(?:[.,]\d+)?)$")
    def _root(m, t):
        x = float(m.group(2).replace(",", "."))
        if m.group(1) and m.group(1).startswith("куб"):
            return f"Будет {fmt(math.copysign(abs(x) ** (1 / 3), x))}."
        return f"Будет {fmt(math.sqrt(x))}." if x >= 0 else "Корня из отрицательного числа среди действительных нет."

    @R(r"^округли\s+(-?\d+(?:[.,]\d+)?)(?:\s+до\s+(\d+)\s*(?:знак\w*|цифр\w*)?(?:\s+после\s+запятой)?|\s+до\s+(десятых|сотых|тысячных|целых))?$")
    def _round(m, t):
        x = float(m.group(1).replace(",", "."))
        n = int(m.group(2)) if m.group(2) else {"десятых": 1, "сотых": 2, "тысячных": 3, "целых": 0, None: 0}[m.group(3)]
        return f"{fmt(round(x, n))}."

    @R(r"^(?:раздели|подели)\s+(-?\d+(?:[.,]\d+)?)\s+на\s+(-?\d+(?:[.,]\d+)?)$|^(?:умножь|помножь)\s+(-?\d+(?:[.,]\d+)?)\s+на\s+(-?\d+(?:[.,]\d+)?)$"
       r"|^(?:сложи)\s+(-?\d+(?:[.,]\d+)?)\s+и\s+(-?\d+(?:[.,]\d+)?)$|^(?:вычти|отними)\s+(-?\d+(?:[.,]\d+)?)\s+из\s+(-?\d+(?:[.,]\d+)?)$")
    def _arith_verbs(m, t):
        g = [float(x.replace(",", ".")) if x else None for x in m.groups()]
        if g[0] is not None:
            return f"Будет {fmt(g[0] / g[1])}." if g[1] else "На ноль делить нельзя."
        if g[2] is not None:
            return f"Будет {fmt(g[2] * g[3])}."
        if g[4] is not None:
            return f"Будет {fmt(g[4] + g[5])}."
        return f"Будет {fmt(g[7] - g[6])}."

    @R(r"^(?:сколько\s+будет|посчитай|вычисли|подсчитай|чему\s+равно|сколько)\s*:?\s*(.*\d.*)$")
    def _calc_words(m, t):
        if re.search(r"\b(?:в|во)\s+\d*\s*\w+(?:ах|ях|ах|ой|ом|е)\b$", t) and find_unit(t):
            return False  # "сколько метров в 3 милях" is a conversion
        v = calc(m.group(1))
        if v is None:
            return False
        return f"Будет {fmt(v)}."

    @R(r"^(?:посчитай|сколько будет|чему равен|вычисли)?\s*(синус|косинус|тангенс|котангенс)\w*\s+(?:от\s+)?(-?\d+(?:[.,]\d+)?)\s*(?:градус\w*)?$")
    def _trig(m, t):
        v = calc(f"{m.group(1)} {m.group(2)}")
        return f"Будет {fmt(round(v, 6))}." if v is not None else False

    # ---------------------------------------------------------------- unit and currency conversion
    NUM = r"(-?\d+(?:[.,]\d+)?)"

    def _do_currency(value, src, dst):
        r = rates()
        res = value * r[src[0]] / r[dst[0]]
        return (f"{fmt(round(value, 2))} {unit_word(src[1], value)} — это {fmt(round(res, 2))} "
                f"{unit_word(dst[1], round(res, 2))} по курсу ЦБ.")

    @R(rf"^(?:переведи|конвертируй|посчитай|сколько\s+будет|сколько\s+стоит|сколько\s+это)?\s*{NUM}\s+(.+?)\s+(?:в|на)\s+(.+?)$")
    def _convert(m, t):
        value = float(m.group(1).replace(",", "."))
        src_w, dst_w = m.group(2), m.group(3)
        c1, c2 = find_cur(src_w), find_cur(dst_w)
        if c1 and c2:
            try:
                return _do_currency(value, c1, c2)
            except Exception:
                return "Не удалось получить курс ЦБ, сэр."
        u1, u2 = find_unit(src_w), find_unit(dst_w)
        if not (u1 and u2):
            return False
        res = convert(value, u1, u2)
        if res is None:
            return "Эти величины не переводятся друг в друга, сэр."
        r = round(res, 4) if abs(res) < 1000 else round(res, 1)
        return f"{fmt(value)} {unit_word(u1[2], value)} — это {fmt(r)} {unit_word(u2[2], r)}."

    @R(rf"^сколько\s+(.+?)\s+(?:в|во)\s+(?:{NUM}\s+)?(.+?)$")
    def _how_many_in(m, t):
        dst_w, value, src_w = m.group(1), float((m.group(2) or "1").replace(",", ".")), m.group(3)
        c1, c2 = find_cur(src_w), find_cur(dst_w)
        if c1 and c2:
            try:
                return _do_currency(value, c1, c2)
            except Exception:
                return "Не удалось получить курс ЦБ, сэр."
        u_src, u_dst = find_unit(src_w), find_unit(dst_w)
        if not (u_src and u_dst):
            return False
        res = convert(value, u_src, u_dst)
        if res is None:
            return False
        r = round(res, 4) if abs(res) < 1000 else round(res)
        return f"В {fmt(value) + ' ' if m.group(2) else ''}{src_w} — {fmt(r)} {unit_word(u_dst[2], r)}."

    @R(r"^(?:какой\s+|скажи\s+|проверь\s+)?(?:курс|цена|стоимость)\s+(биткоин\w*|битка|эфир\w*|тон\w*|bitcoin|btc|eth\w*|крипт\w*)$"
       r"|^сколько\s+стоит\s+(биткоин|эфир|тон)\w*$")
    def _crypto(m, t):
        try:
            c = info.crypto()
        except Exception:
            return "Не удалось получить курс, сэр."
        w = m.group(1) or m.group(2) or ""
        if w.startswith(("эфир", "eth")):
            return f"Эфир стоит {fmt(round(c.get('ethereum', 0)))} долларов."
        if w.startswith("тон"):
            return f"Тон стоит {fmt(round(c.get('toncoin', 0), 2))} доллара."
        if w.startswith("крипт"):
            return (f"Биткоин {fmt(round(c.get('bitcoin', 0)))} долларов, эфир {fmt(round(c.get('ethereum', 0)))}, "
                    f"тон {fmt(round(c.get('toncoin', 0), 2))}.")
        return f"Биткоин стоит {fmt(round(c.get('bitcoin', 0)))} долларов."

    # ---------------------------------------------------------------- news and maps
    @R(r"^(?:расскажи|скажи|какие|прочитай|покажи|последние|свежие|главные)?\s*(?:мне\s+)?(?:последние\s+|свежие\s+|главные\s+)?новост\w*(?:\s+(?:из\s+мира\s+|в\s+мире\s+|про\s+|о\s+)?(\w+))?$"
       r"|^что\s+нового(?:\s+(?:в\s+мире\s+|в\s+|про\s+)?(\w+))?$")
    def _news(m, t):
        topic = m.group(1) or m.group(2) or ""

        def go():
            try:
                title, items = news(topic)
            except Exception:
                core.say("Не удалось загрузить новости, сэр.")
                return
            if not items:
                core.say("Свежих новостей нет.")
                return
            core._chat_add("assistant", f"**{title}**\n" + "\n".join(f"- [{a}]({b})" if b else f"- {a}" for a, b in items), "voice")
            core.say(f"{title}. " + " ".join(f"{a}." for a, _ in items[:3]) + " Остальное — в чате.")
        threading.Thread(target=go, daemon=True).start()
        return True

    def place(word):
        w = word.strip()
        places = cfg.get("places", {}) or {}
        for key in ("работ", "дом", "учеб", "школ"):
            if w.startswith(key) and places.get(key):
                return places[key]
        return w

    @R(r"^(?:покажи|открой|какие|проверь)?\s*пробк\w*(?:\s+на\s+дорогах)?$")
    def _traffic(m, t):
        S.open_target("https://yandex.ru/maps/?l=trf")
        return "Открываю карту пробок."

    @R(r"^(?:как|сколько)\s+(?:быстрее\s+|мне\s+)?(?:добраться|доехать|ехать|идти|дойти|проехать|пройти|лететь)\s+до\s+(.+)$|^(?:проложи|построй|покажи)\s+маршрут\s+до\s+(.+)$")
    def _route(m, t):
        dest = place(m.group(1) or m.group(2))
        if re.search(r"лететь", t) and not re.search(r"до\s+(?:работ|дом)", t):
            return False  # "сколько лететь до японии" is a knowledge question
        mode = "pd" if re.search(r"идти|дойти|пройти|пешком", t) else "mt" if re.search(r"автобус|метро|транспорт", t) else "auto"
        S.open_target(f"https://yandex.ru/maps/?rtext=~{quote(dest)}&rtt={mode}")
        hint = "" if dest != (m.group(1) or m.group(2)).strip() or not re.match(r"^(работ|дом)", dest) else \
            " Чтобы маршрут строился до вашего адреса, скажите: «запомни адрес работы …»."
        return "Открываю маршрут на картах." + hint

    @R(r"^запомни\s+(?:мой\s+)?адрес\s+(работы|дома|учебы|школы)\s*:?\s*(.+)$|^мой\s+адрес\s+(работы|дома)\s*:?\s*(.+)$")
    def _set_place(m, t):
        kind, value = (m.group(1) or m.group(3)), (m.group(2) or m.group(4))
        key = {"работы": "работ", "дома": "дом", "учебы": "учеб", "школы": "школ"}[kind]
        places = dict(cfg.get("places", {}) or {})
        places[key] = value.strip()
        cfg.set("places", places)
        return f"Запомнил адрес {kind}."

    @R(r"^(?:найди|покажи|где)\s+(?:ближайш\w+\s+|рядом\s+|поблизости\s+)?(кафе|ресторан\w*|аптек\w*|магазин\w*|банкомат\w*|заправк\w*|азс|больниц\w*|"
       r"поликлиник\w*|супермаркет\w*|продуктов\w*|кофейн\w*|бар\w*|парикмахерск\w*|пиццери\w*|отел\w*|гостиниц\w*|парковк\w*|почт\w*|"
       r"пункт\w*\s+выдачи|шиномонтаж\w*|автосервис\w*|спортзал\w*|фитнес\w*)(?:\s+рядом|\s+поблизости)?$")
    def _nearby(m, t):
        S.open_target(f"https://yandex.ru/maps/?text={quote(m.group(1))}")
        return "Ищу рядом на картах."

    @R(r"^(?:во\s+сколько|когда)\s+(?:закрывается|открывается|работает)\s+(.+)$|^(?:график|режим|часы)\s+работы\s+(.+)$")
    def _hours(m, t):
        S.open_target(f"https://yandex.ru/maps/?text={quote(m.group(1) or m.group(2))}")
        return "Открываю карточку на картах — там часы работы."

    @R(r"^(?:открой|найди|покажи)\s+(?:в\s+)?википеди\w+\s+(?:на\s+статье|статью|про|о|об)?\s*(.+)$")
    def _wiki_article(m, t):
        S.open_target("https://ru.wikipedia.org/w/index.php?search=" + quote(m.group(1)))
        return "Открываю Википедию."

    # ---------------------------------------------------------------- weather extras
    def city_loc(text):
        city = re.sub(r"\b(?:сегодня|завтра|послезавтра|на\s+выходн\w+|в\s+выходн\w+|в\s+субботу|в\s+воскресенье|на\s+неделю|"
                      r"на\s+завтра|утром|днем|вечером|сейчас|будет|ли)\b", " ", text)
        city = re.sub(r"\s+", " ", city).strip()
        if city:
            loc = info.geocode(city)
            if loc:
                return loc
        return cfg.get("location")

    @R(r"^(?:будет|пойдет|пойдёт|ожидается)\s+ли\s+(?:сегодня\s+|завтра\s+)?(дождь|снег|гроза|осадки)(?:\s+(?:сегодня|завтра))?(?:\s+в\s+(.+))?$"
       r"|^(?:нужен|понадобится|брать)\s+ли\s+(?:сегодня\s+|завтра\s+)?зонт\w*")
    def _rain(m, t):
        try:
            loc = city_loc(m.group(2) or "")
            w = info.weather(loc["lat"], loc["lon"])
        except Exception:
            return "Не удалось получить погоду, сэр."
        if "завтра" in t:
            p = w["tomorrow"]["rain"] or 0
            return f"Завтра вероятность осадков {p} процентов" + (", лучше взять зонт." if p >= 50 else ", зонт вряд ли понадобится.")
        p = w.get("rain") or 0
        if w.get("rain_at"):
            if w["rain_at"][:2] == f"{dt.datetime.now():%H}":
                return f"Осадки, похоже, уже идут или вот-вот начнутся: вероятность {p} процентов. Возьмите зонт."
            return f"Да, около {w['rain_at']} возможны осадки, вероятность {p} процентов. Возьмите зонт."
        return f"Вероятность осадков сегодня {p} процентов" + (", возьмите зонт." if p >= 50 else ", скорее всего обойдётся без них.")

    @R(r"^(?:сколько\s+(?:сейчас\s+)?градусов|какая\s+(?:сейчас\s+)?температура)(?:\s+(?:на\s+улице|за\s+окном))?(?:\s+(?:в|во)\s+(.+))?$")
    def _temp(m, t):
        try:
            loc = city_loc(m.group(1) or "")
            w = info.weather(loc["lat"], loc["lon"])
        except Exception:
            return "Не удалось получить погоду, сэр."
        return f"В городе {loc.get('city', '')} сейчас {w['temp']} градусов, ощущается как {w['feels']}."

    WD = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]

    @R(r"^(?:какая\s+)?погода\s*(?:будет\s+)?(?:(?:в|во)\s+(.+?)\s+)?(?:на\s+выходн\w+|в\s+выходн\w+|на\s+неделю|в\s+(понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье)|послезавтра)(?:\s+(?:в|во)\s+(.+))?$"
       r"|^(?:какая\s+)?погода\s+(?:будет\s+)?(?:в|во)\s+(.+?)\s+(?:на\s+выходн\w+|на\s+неделю)$")
    def _forecast(m, t):
        try:
            loc = city_loc(m.group(1) or m.group(3) or m.group(4) or "")
            w = info.weather(loc["lat"], loc["lon"])
        except Exception:
            return "Не удалось получить погоду, сэр."
        days = w.get("days") or []
        today = dt.date.today()

        def line(d):
            day = dt.date.fromisoformat(d["date"])
            return f"{WD[day.weekday()].replace('среду', 'среда').replace('пятницу', 'пятница').replace('субботу', 'суббота')} — {d['desc']}, от {d['min']} до {d['max']}"
        if "недел" in t:
            pick = days[:7]
        elif "послезавтра" in t:
            pick = [d for d in days if dt.date.fromisoformat(d["date"]) == today + dt.timedelta(days=2)]
        elif m.group(2):
            wd = WD.index(m.group(2))
            pick = [d for d in days if dt.date.fromisoformat(d["date"]).weekday() == wd and d["date"] != today.isoformat()][:1]
        else:  # the weekend
            pick = [d for d in days if dt.date.fromisoformat(d["date"]).weekday() >= 5][:2]
        if not pick:
            return "Так далеко прогноз не заглядывает, сэр."
        return f"В городе {loc.get('city', '')}: " + "; ".join(line(d) for d in pick) + "."

    # ---------------------------------------------------------------- agenda, meetings, pomodoro
    def agenda(day):
        ts = tasks.tasks_for_day(day)
        rs = sorted((r for r in tasks.reminders.items if r.get("active") and r["at"][:10] == day.isoformat()), key=lambda r: r["at"])
        return ts, rs

    @R(r"^(?:что\s+у\s+меня\s+(?:запланировано|в\s+планах|по\s+планам)?\s*(?:на\s+)?(сегодня|завтра)?|какие\s+(?:у\s+меня\s+)?планы\s+на\s+(сегодня|завтра)|"
       r"что\s+(?:у\s+меня\s+)?(?:на|в)\s+(сегодня|завтра)|мое\s+расписание\s+на\s+(сегодня|завтра)|что\s+у\s+меня\s+(завтра|сегодня))$")
    def _agenda(m, t):
        word = next((g for g in m.groups() if g), "сегодня")
        day = dt.date.today() + dt.timedelta(days=1 if word == "завтра" else 0)
        ts, rs = agenda(day)
        core.show_tab("tasks") if ts else None
        if not ts and not rs:
            return f"На {word} ничего не запланировано, {addr()}."
        parts = []
        if rs:
            parts.append("; ".join(f"в {r['at'][11:16]} — {r['text']}" for r in rs[:4]))
        if ts:
            parts.append("задачи: " + ", ".join(x["text"] for x in ts[:4]))
        return f"На {word}: " + ". ".join(parts) + "."

    @R(r"^(?:добавь|запиши|создай|поставь|назначь|запланируй)\s+(?:в\s+календарь\s+)?(встреч\w*|событи\w*|мероприяти\w*|созвон\w*|звонок|приём|прием|"
       r"собрани\w*|свидани\w*|тренировк\w*|урок\w*)\s*(.*?)(?:\s+в\s+календар\w*)?$")
    def _meeting(m, t):
        kind, rest = m.group(1), m.group(2) or ""
        when, text = parse_when(rest)
        if not when and re.match(r"^на\s+\d", rest):
            when, text = parse_when(re.sub(r"^на\s+", "в ", rest))
        if not when:
            m2 = re.search(r"\bна\s+(\d{1,2}(?::\d{2})?)\b", rest)
            if m2:
                when, _ = parse_when("в " + m2.group(1))
                text = (rest[:m2.start()] + rest[m2.end():]).strip()
        if not when:
            return f"На какое время записать {kind}?"
        label = (kind.capitalize().replace("Встречу", "Встреча").replace("Тренировку", "Тренировка")
                 .replace("Свидание", "Свидание") + (" " + text.strip() if text.strip() else ""))
        label = re.sub(r"\s+в\s+календар\w*$", "", label).strip()
        tasks.add_reminder(label, when)
        tasks.add_task(label, when, tags=["встреча"])
        return f"Записал: {label}, {say_when(when)}. Напомню вовремя."

    @R(r"^(?:удали|убери|отмени|сотри)\s+последн\w+\s+(напоминани\w*|будильник\w*|задач\w*|заметк\w*|встреч\w*)$")
    def _del_last(m, t):
        w = m.group(1)
        if w.startswith(("напоминани", "будильник", "встреч")):
            items = [r for r in tasks.reminders.items if r.get("active") and bool(r.get("alarm")) == w.startswith("будильник")]
            if not items:
                return "Удалять нечего, сэр."
            r = items[-1]  # the newest one (they are kept in creation order)
            tasks.delete_reminder(r["id"])
            return f"Удалил: {r['text']}."
        if w.startswith("заметк"):
            n = core.notebook.latest()
            if not n:
                return "Удалять нечего, сэр."
            core.notebook.rpc_note_delete(n["id"])
            return f"Удалил заметку «{core.notebook.title(n)}»."
        items = [x for x in tasks.tasks.items if not x.get("done") and "заметка" not in (x.get("tags") or [])]
        if not items:
            return "Удалять нечего, сэр."
        tasks.delete_task(items[-1]["id"])
        return f"Удалил: {items[-1]['text']}."

    @R(r"^(?:перенеси|сдвинь|передвинь|отложи)\s+(?:мою\s+|мое\s+)?(встреч\w*|напоминани\w*|будильник\w*|событи\w*|созвон\w*)?\s*(?:про\s+|с\s+)?(.*?)\s*"
       r"на\s+(.+?)\s*(позже|раньше|вперед|назад)?$")
    def _move(m, t):
        what, spec, amount, direction = m.group(1) or "", (m.group(2) or "").strip(), m.group(3), m.group(4)
        if not what and not direction and not re.match(r"^\d", amount):
            return False  # "отложи на 10 минут" (snooze) is another rule
        pool = [r for r in tasks.reminders.items if r.get("active") and bool(r.get("alarm")) == what.startswith("будильник")]
        if spec:
            hit = tasks.find_reminders(spec, what.startswith("будильник") if what else None)
            pool = [r for r in pool if r in hit] or hit
        upcoming = sorted((r for r in pool if r["at"] >= dt.datetime.now().isoformat()), key=lambda r: r["at"])
        if not upcoming:
            return "Не нашёл, что перенести, сэр."
        r = upcoming[0]
        old = dt.datetime.fromisoformat(r["at"])
        if direction:
            secs = parse_duration(amount) or (3600 if re.match(r"^час", amount) else 1800 if "полчаса" in amount else 0)
            if not secs:
                return False
            new = old + dt.timedelta(seconds=secs) * (-1 if direction in ("раньше", "назад") else 1)
        else:
            when, _ = parse_when("в " + amount if re.match(r"^\d", amount) else amount)
            if not when:
                return False
            new = when
        tasks.update_reminder(r["id"], at=new.isoformat(timespec="seconds"))
        for x in tasks.tasks.items:  # the meeting task follows its reminder
            if x["text"] == r["text"] and not x.get("done"):
                tasks.update_task(x["id"], due=new.isoformat(timespec="minutes"))
        return f"Перенёс «{r['text']}» на {say_when(new)}."

    @R(r"^(?:очисти|освободи)\s+(?:мой\s+)?(?:календарь|расписание|день|планы)\s+(?:на\s+)?(сегодня|завтра)$")
    def _clear_day(m, t):
        day = dt.date.today() + dt.timedelta(days=1 if m.group(1) == "завтра" else 0)
        ts, rs = agenda(day)
        if not ts and not rs:
            return f"На {m.group(1)} и так пусто."

        def go():
            for r in rs:
                tasks.delete_reminder(r["id"])
            for x in ts:
                tasks.delete_task(x["id"])
            core.say("Календарь очищен.")
        core.ask_confirm(go, f"Удалить {len(rs)} напоминаний и {len(ts)} задач на {m.group(1)}? Скажите «да».")
        return True

    @R(r"^(?:запусти|включи|поставь|начни|старт)\s+(?:таймер\s+)?(?:помодоро|помидор\w*|pomodoro)$|^помодоро$")
    def _pomodoro(m, t):
        tasks.add_timer(25 * 60, "Помодоро: пора на перерыв 5 минут")
        return "Помодоро запущен: 25 минут работы. Скажу, когда пора отдохнуть."

    @R(r"^сколько\s+(?:осталось\s+)?(?:дней|недель|месяцев)\s+(?:осталось\s+)?до\s+(.+)$|^через\s+сколько\s+(?:дней\s+)?(.+)$")
    def _countdown(m, t):
        what = (m.group(1) or m.group(2) or "").strip()
        today = dt.date.today()

        def nxt(month, day):
            d = dt.date(today.year, month, day)
            return d if d >= today else dt.date(today.year + 1, month, day)
        fixed = {"нового года": (1, 1), "новый год": (1, 1), "лета": (6, 1), "осени": (9, 1), "зимы": (12, 1), "весны": (3, 1),
                 "8 марта": (3, 8), "23 февраля": (2, 23), "1 сентября": (9, 1), "хэллоуина": (10, 31), "хеллоуина": (10, 31),
                 "дня победы": (5, 9), "рождества": (1, 7), "9 мая": (5, 9), "1 мая": (5, 1), "дня святого валентина": (2, 14)}
        target = next((nxt(*v) for k, v in fixed.items() if what.startswith(k)), None)
        if not target and re.search(r"дня\s+рождени|др\b", what):
            bd = cfg.get("user.birthday") or ""
            mm = re.match(r"^(\d{1,2})\.(\d{1,2})$", bd)
            if not mm:
                return "Скажите, когда у вас день рождения: «мой день рождения 24 октября»."
            target = nxt(int(mm.group(2)), int(mm.group(1)))
        if not target:
            mm = re.match(r"^(\d{1,2})\s+(" + "|".join(MONTHS) + r")", what)
            if mm:
                target = nxt(MONTHS.index(mm.group(2)) + 1, int(mm.group(1)))
        if not target:
            when, _ = parse_when(what)
            target = when.date() if when else None
        if not target:
            return False
        n = (target - today).days
        if n == 0:
            return "Это сегодня!"
        return f"До {what} {plural(n, 'остался', 'осталось', 'осталось')} {n} {plural(n, 'день', 'дня', 'дней')}."

    @R(r"^(?:мой|у меня)\s+день\s+рождения\s+(\d{1,2})\s+(" + "|".join(MONTHS) + r")$")
    def _birthday(m, t):
        cfg.set("user.birthday", f"{int(m.group(1))}.{MONTHS.index(m.group(2)) + 1}")
        return "Запомнил. Не забуду поздравить."

    # ---------------------------------------------------------------- tasks: by number, lists
    @R(r"^(?:отметь|выполни|закрой|вычеркни|отметить)\s+(перв\w*|втор\w*|трет\w*|четверт\w*|пят\w*|шест\w*|седьм\w*|восьм\w*|девят\w*|десят\w*|последн\w*|\d+)"
       r"(?:-?ю|-?ую)?\s+задач\w*(?:\s+(?:как\s+)?(?:выполненн\w*|сделанн\w*|готов\w*))?$")
    def _task_by_number(m, t):
        lst = [x for x in tasks.sorted_tasks(True) if "заметка" not in (x.get("tags") or [])]
        w = m.group(1)
        idx = len(lst) if w.startswith("последн") else int(w) if w.isdigit() else next((v for k, v in ORD.items() if w.startswith(k)), 0)
        if not (1 <= idx <= len(lst)):
            return f"У вас {len(lst)} {plural(len(lst), 'задача', 'задачи', 'задач')}, такой нет."
        x = lst[idx - 1]
        tasks.update_task(x["id"], done=True)
        return f"Отметил выполненной: {x['text']}."

    @R(r"^(?:покажи|открой|прочитай|какие|что\s+в)\s+(?:мой\s+|мои\s+|у\s+меня\s+)?(?:список\s+(?:дел|задач)|задачи|дела|туду)$|^мой\s+список\s+дел$")
    def _task_show(m, t):
        lst = [x for x in tasks.sorted_tasks(True) if not {"заметка", "покупки", "прочитать"} & set(x.get("tags") or [])]
        core.show_tab("tasks")
        if not lst:
            return "Задач нет. Свободны, как ветер."
        return f"{len(lst)} {plural(len(lst), 'задача', 'задачи', 'задач')}: " + "; ".join(f"{i + 1}. {x['text']}" for i, x in enumerate(lst[:5])) + "."

    @R(r"^(?:покажи|открой|прочитай|какие)\s+(?:мой\s+|мои\s+|у\s+меня\s+)?(?:список\s+)?напоминани\w*$|^мои\s+напоминани\w*$")
    def _rem_show(m, t):
        rs = sorted((r for r in tasks.reminders.items if r.get("active")), key=lambda r: r["at"])
        core.show_tab("time")
        if not rs:
            return "Активных напоминаний нет."
        return f"{len(rs)} {plural(len(rs), 'напоминание', 'напоминания', 'напоминаний')}: " + "; ".join(
            f"{say_when(dt.datetime.fromisoformat(r['at']))} — {r['text']}" for r in rs[:5]) + "."

    # notes moved to the notebook: jarvis/notes.py (tab «Заметки»)

    # ---------------------------------------------------------------- shopping list
    SHOP = r"(?:в\s+)?(?:список\s+покупок|покупки|корзину\s+покупок|шопинг\s+лист|список\s+продуктов)"

    @R(rf"^(?:добавь|запиши|внеси|закинь)\s+(.+?)\s+{SHOP}$|^(?:нужно|надо)\s+купить\s+(.+)$")
    def _shop_add(m, t):
        raw = m.group(1) or m.group(2)
        items = [x.strip() for x in re.split(r",|\s+и\s+|\s+а\s+также\s+|\s+еще\s+", raw) if x.strip()]
        have = {x["text"].lower() for x in tagged("покупки")}
        for it in items:
            if it.lower() not in have:
                tasks.add_task(it[:1].upper() + it[1:], None, tags=["покупки"])
        return "Добавил в список покупок: " + ", ".join(items) + "."

    @R(r"^(?:что|покажи|прочитай|какой)\s+(?:у\s+меня\s+)?(?:в\s+)?(?:списке?|список)\s+(?:покупок|продуктов)$|^что\s+(?:мне\s+)?(?:нужно|надо)\s+купить$")
    def _shop_list(m, t):
        lst = tagged("покупки")
        core.show_tab("tasks")
        return ("В списке покупок: " + ", ".join(x["text"].lower() for x in lst) + ".") if lst else "Список покупок пуст."

    @R(rf"^(?:удали|убери|вычеркни|сотри)\s+(.+?)\s+из\s+(?:списка\s+)?(?:покупок|продуктов)$|^(?:я\s+)?купил\w*\s+(.+)$")
    def _shop_del(m, t):
        from rapidfuzz import fuzz
        names = [x.strip() for x in re.split(r",|\s+и\s+", m.group(1) or m.group(2)) if x.strip()]
        removed = []
        for nm in names:
            best = max(tagged("покупки"), key=lambda x: fuzz.ratio(nm.lower(), x["text"].lower()), default=None)
            if best and fuzz.ratio(nm.lower(), best["text"].lower()) >= 70:
                tasks.delete_task(best["id"])
                removed.append(best["text"].lower())
        if not removed:
            return False if m.group(2) else "Такого в списке нет."
        return "Вычеркнул: " + ", ".join(removed) + "."

    @R(r"^(?:очисти|удали|сотри)\s+(?:весь\s+)?(?:список\s+)?(?:покупок|продуктов)$|^очисти\s+покупки$")
    def _shop_clear(m, t):
        lst = tagged("покупки")
        for x in lst:
            tasks.delete_task(x["id"])
        return "Список покупок очищен." if lst else "Он и так пуст."

    # ---------------------------------------------------------------- files
    @R(r"^(?:сохрани|запиши)\s+(?:этот\s+|выделенный\s+)?текст\s+в\s+файл\s*(.*)$|^сохрани\s+(?:это|выделенное)\s+в\s+файл\s*(.*)$")
    def _save_text(m, t):
        name = (m.group(1) or m.group(2) or "").strip() or f"Заметка {dt.datetime.now():%Y-%m-%d %H-%M}"
        txt = selected_text() or S.clipboard_get()
        if not (txt or "").strip():
            return "Нечего сохранять: выделите текст или скопируйте его."
        name = re.sub(r'[<>:"/\\|?*]', "", name)[:80]
        path = DOCS / (name if name.lower().endswith(".txt") else name + ".txt")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(txt.rstrip() + "\n")
        return f"Сохранил в Документы, файл «{path.stem}»."

    def target_paths(name, need_fg=True):
        """'этот файл' = selection in Explorer; a name = search on the PC."""
        if not name or re.match(r"^(?:этот|эту|это|выделенн\w*|выбранн\w*)", name):
            folder, sel = explorer_context(require_foreground=need_fg)
            return sel
        hits = S.find_files(name, limit=5)
        return [str(hits[0])] if hits else []

    @R(r"^(?:удали|сотри)\s+(?:этот\s+)?файл\w*\s*(.*)$|^(?:удали|сотри)\s+(?:эту\s+)?папку\s*(.*)$|^(?:удали|сотри)\s+(?:это|выделенное)$")
    def _delete_file(m, t):
        name = (m.group(1) or m.group(2) or "").strip()
        paths = target_paths(name)
        if not paths:
            return "Не нашёл, что удалить. Выделите файл в проводнике или назовите его." if not name else f"Файл «{name}» не нашёл."
        names = ", ".join(Path(p).name for p in paths[:3]) + (f" и ещё {len(paths) - 3}" if len(paths) > 3 else "")

        def go():
            ok = sum(1 for p in paths if recycle(p))
            core.say("Перенёс в корзину." if ok else "Не получилось удалить.")
        core.ask_confirm(go, f"Удалить в корзину: {names}? Скажите «да».")
        return True

    @R(r"^переименуй\s+(?:этот\s+)?(?:файл|папку|это)?\s*(?:в|на)\s+(.+)$")
    def _rename(m, t):
        folder, sel = explorer_context(require_foreground=True)
        if len(sel) != 1:
            return "Выделите один файл в проводнике, и я его переименую."
        p = Path(sel[0])
        new = re.sub(r'[<>:"/\\|?*]', "", m.group(1)).strip()
        if not new:
            return False
        dst = p.with_name(new + (p.suffix if p.is_file() and not Path(new).suffix else ""))
        if dst.exists():
            return "Файл с таким именем уже есть."
        p.rename(dst)
        return f"Переименовал в «{dst.name}»."

    @R(r"^(?:создай|сделай|новая)\s+(?:новую\s+)?папк\w*\s*(.*)$")
    def _mkdir(m, t):
        name = re.sub(r'[<>:"/\\|?*]', "", (m.group(1) or "").strip()) or "Новая папка"
        folder, _ = explorer_context(require_foreground=True)
        base = Path(folder) if folder else Path.home() / "Desktop"
        p = base / name
        i = 2
        while p.exists():
            p = base / f"{name} ({i})"
            i += 1
        p.mkdir(parents=True)
        return f"Создал папку «{p.name}»" + (" здесь." if folder else " на рабочем столе.")

    @R(r"^(?:заархивируй|запакуй|сожми|архивируй)\s+(?:эту\s+папку|этот\s+файл|это|выделенное|папку|файлы?)?\s*(.*)$")
    def _zip(m, t):
        folder, sel = explorer_context(require_foreground=True)  # never an Explorer window in the background
        items = sel or ([folder] if folder else [])
        if not items:
            return "Откройте папку в проводнике или выделите файлы."

        def go():
            first = Path(items[0])
            out = first.with_suffix("") if len(items) == 1 else Path(folder) / "Архив"
            dest = Path(str(out) + ".zip")
            i = 2
            while dest.exists():
                dest = Path(f"{out} ({i}).zip")
                i += 1
            import zipfile
            with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
                for it in map(Path, items):
                    if it.is_dir():
                        for f in it.rglob("*"):
                            if f.is_file():
                                z.write(f, Path(it.name) / f.relative_to(it))
                    elif it.is_file():
                        z.write(it, it.name)
            core.say(f"Готово: архив «{dest.name}».")
        threading.Thread(target=go, daemon=True).start()
        return "Архивирую."

    @R(r"^(?:проверь|какой|покажи|узнай|скажи)\s+(?:мне\s+)?размер\s*(?:этого\s+|этой\s+)?(?:файла|папки|выделенного)?\s*(.*)$|^сколько\s+(?:весит|занимает)\s+(?:этот\s+файл|эта\s+папка|файл|папка|это)\s*(.*)$")
    def _size(m, t):
        name = (m.group(1) or m.group(2) or "").strip()
        folder, sel = explorer_context()
        paths = sel or ([folder] if folder else [])
        if name:
            paths = target_paths(name, need_fg=False)
        if not paths:
            return "Выделите файл в проводнике или назовите его."
        total = sum(tree_size(p) for p in paths)
        what = Path(paths[0]).name if len(paths) == 1 else f"{len(paths)} объектов"
        return f"{what}: {human_size(total)}."

    @R(r"^(?:прочитай|зачитай)\s+(?:мне\s+)?(?:вслух\s+)?(?:текстов\w+\s+)?(?:файл|документ)\s+(.+)$")
    def _read_file(m, t):
        name = m.group(1).strip()
        hits = [p for p in S.find_files(name, limit=10) if Path(p).suffix.lower() in (".txt", ".md", ".log", ".csv", ".ini", ".json")]
        if not hits:
            return f"Текстового файла «{name}» не нашёл."
        p = Path(hits[0])
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return "Не смог открыть файл."
        txt = re.sub(r"\s+", " ", txt).strip()
        if not txt:
            return "Файл пустой."
        return f"«{p.stem}»: {txt[:1200]}" + (" … дальше на экране." if len(txt) > 1200 else "")

    @R(r"^(?:запиши|записывай|сохраняй)\s+(?:мою\s+)?речь\s+в\s+(?:текстов\w+\s+)?файл$|^(?:включи\s+)?диктовк\w*\s+в\s+файл$")
    def _dictate_file(m, t):
        path = DOCS / f"Диктовка {dt.datetime.now():%Y-%m-%d %H-%M}.txt"
        core.dictation_file = path
        core.after_speech(lambda: core.dictation(True), 0.1)
        return "Записываю вашу речь в файл. Скажите «конец диктовки», чтобы закончить."

    @R(r"^(?:добавь|сохрани|запиши)\s+(?:эту\s+)?(?:ссылку|страницу|статью|сайт)\s+в\s+(?:список\s+)?(?:прочитать\s+позже|почитать\s+позже|на\s+потом|отложенн\w+)$")
    def _read_later(m, t):
        url = browser_url()
        if not url:
            return "Не смог взять ссылку из браузера."
        tasks.add_task("Прочитать: " + url, None, tags=["прочитать"], notes=url)
        return "Добавил в список «прочитать позже»."

    @R(r"^(?:что\s+в\s+|покажи\s+|открой\s+)(?:списке?\s+)?(?:прочитать|почитать)\s+позже$")
    def _read_later_list(m, t):
        lst = tagged("прочитать")
        core.show_tab("tasks")
        return f"В списке «прочитать позже» {len(lst)} {plural(len(lst), 'ссылка', 'ссылки', 'ссылок')}." if lst else "Список пуст."

    @R(r"^(?:открой|покажи)\s+(?:список\s+|мои\s+|все\s+)?заклад\w*$")
    def _bookmarks(m, t):
        if not S.focus_browser():
            return "Не вижу браузера."
        S.hotkey("ctrl+shift+o")
        return True

    @R(r"^(?:удали|убери|почисти)\s+(?:все\s+)?пуст\w+\s+папк\w*$")
    def _empty_dirs(m, t):
        folder, _ = explorer_context(require_foreground=True)
        if not folder:
            return "Откройте нужную папку в проводнике."
        empty = [p for p in sorted(Path(folder).rglob("*"), key=lambda p: -len(p.parts))
                 if p.is_dir() and not any(p.iterdir())][:200]
        if not empty:
            return "Пустых папок нет."

        def go():
            n = sum(1 for p in empty if recycle(p))
            core.say(f"Удалил в корзину {n} {plural(n, 'пустую папку', 'пустые папки', 'пустых папок')}.")
        core.ask_confirm(go, f"Нашёл {len(empty)} {plural(len(empty), 'пустую папку', 'пустые папки', 'пустых папок')} в «{Path(folder).name}». Удалить в корзину?")
        return True

    @R(r"^(?:найди|покажи|поищи)\s+(?:одинаков\w+|повторяющ\w+)?\s*(?:дубликат\w*|дубли|копии)(?:\s+файлов)?(?:\s+(?:в|во)\s+(.+))?$")
    def _dupes(m, t):
        folder, _ = explorer_context()
        if m.group(1):
            folder = {"загрузках": str(Path.home() / "Downloads"), "документах": str(DOCS),
                      "рабочем столе": str(Path.home() / "Desktop")}.get(m.group(1), folder)
        folder = folder or str(Path.home() / "Downloads")

        def go():
            import hashlib
            by_size = {}
            t0 = time.time()
            for f in Path(folder).rglob("*"):
                if time.time() - t0 > 20:
                    break
                try:
                    if f.is_file() and f.stat().st_size > 0:
                        by_size.setdefault(f.stat().st_size, []).append(f)
                except OSError:
                    pass
            groups = []
            for size, fs in by_size.items():
                if len(fs) < 2:
                    continue
                by_hash = {}
                for f in fs:
                    try:
                        h = hashlib.md5(f.read_bytes() if size < 64 * 2**20 else f.open("rb").read(2**20)).hexdigest()
                    except OSError:
                        continue
                    by_hash.setdefault(h, []).append(f)
                groups += [g for g in by_hash.values() if len(g) > 1]
            if not groups:
                core.say(f"Дубликатов в «{Path(folder).name}» нет.")
                return
            waste = sum(g[0].stat().st_size * (len(g) - 1) for g in groups)
            lines = "\n".join("- " + " = ".join(str(p.relative_to(folder)) for p in g[:4]) for g in groups[:25])
            core._chat_add("assistant", f"**Дубликаты в {folder}** ({len(groups)} групп):\n{lines}", "voice")
            core.say(f"Нашёл {len(groups)} {plural(len(groups), 'группу', 'группы', 'групп')} дубликатов, "
                     f"лишнее занимает {human_size(waste)}. Список в чате.")
        threading.Thread(target=go, daemon=True).start()
        return f"Ищу дубликаты в «{Path(folder).name}»."

    @R(r"^(?:покажи|открой)\s+свойства\s*(?:этого\s+)?(?:файла|папки)?$|^свойства\s+(?:файла|папки)$")
    def _props(m, t):
        S.hotkey("alt+enter")
        return True

    # ---------------------------------------------------------------- music and video
    def music(query):
        svc = cfg.get("music_service", "yandex")
        if core.ctx.current_site(180) == "youtube":   # YouTube was just opened: "включи рок" plays there
            S.play_youtube(query)
        elif svc == "youtube":
            S.open_target("https://music.youtube.com/search?q=" + quote(query))
        else:
            S.open_target("https://music.yandex.ru/search?text=" + quote(query))

    GENRES = (r"рок\w*|джаз\w*|рэп\w*|реп\w*|хип[- ]?хоп\w*|поп\w*|классик\w*|классическ\w*|лоу?[- ]?фай|lo-?fi|электрон\w*|техно|хаус|"
              r"метал\w*|шансон\w*|блюз\w*|регги|кантри|инди|фонк\w*|phonk|эмбиент\w*|ambient|джаз|соул|диско|трэп\w*|"
              r"расслабляющ\w*|спокойн\w*|весел\w*|грустн\w*|энергичн\w*|романтичн\w*|медленн\w*|бодр\w*|фоновую|для\s+\w+")

    @R(r"^(?:включи|поставь|запусти|врубай|вруби)\s+(?:мне\s+)?(?:какую-нибудь\s+|какой-нибудь\s+)?(?:музыку\s+|песни\s+)?(" + GENRES + r")(?:\s+музык\w*|\s+песн\w*|\s+треки)?$")
    def _genre(m, t):
        music(m.group(1))
        return "Включаю."

    @R(r"^(?:включи|поставь|запусти|найди)\s+(?:мне\s+)?(?:песню|трек|композицию|альбом|исполнителя|группу)\s+(.+)$")
    def _song(m, t):
        music(m.group(1))
        return "Ищу."

    @R(r"^(?:включи|поставь|запусти|открой)\s+(?:мой\s+)?плейлист\s*(.*)$")
    def _playlist(m, t):
        q = (m.group(1) or "").strip()
        if not q or q.startswith(("для работы", "для учебы")) and cfg.get("music_service", "yandex") != "youtube":
            S.open_target("https://music.yandex.ru/users/me/playlists" if not q else "https://music.yandex.ru/search?text=" + quote(q))
        else:
            music(q)
        return "Открываю плейлист."

    @R(r"^(?:включи|поставь|запусти|открой)\s+радио\s*(.*)$")
    def _radio(m, t):
        q = (m.group(1) or "").strip()
        stations = {"рекорд": "https://www.radiorecord.ru/", "европ": "https://europaplus.ru/", "русское": "https://rusradio.ru/",
                    "маяк": "https://radiomayak.ru/", "джаз": "https://radiojazzfm.ru/", "энерджи": "https://www.energyfm.ru/"}
        url = next((u for k, u in stations.items() if q.startswith(k)), None) or "https://music.yandex.ru/radio"
        S.open_target(url)
        return "Включаю радио."

    @R(r"^(?:найди|включи|покажи|поставь)\s+клип\s+(.+)$")
    def _clip(m, t):
        if t.startswith(("включи", "поставь")):   # "включи клип AC/DC": the clip itself, not a list of them
            title = S.play_youtube(m.group(1) + " клип")
            return f"Включаю: {nlu.short_title(title)}." if title else "Ищу клип на YouTube."
        S.open_target("https://www.youtube.com/results?search_query=" + quote(m.group(1) + " клип"))
        return "Ищу клип на YouTube."

    @R(r"^(?:включи|поставь|найди|запусти)\s+(?:мне\s+)?(?:фильм|кино|сериал|мультфильм|мультик)\s+(.+)$")
    def _movie(m, t):
        S.open_target("https://www.kinopoisk.ru/index.php?kp_query=" + quote(m.group(1)))
        return "Ищу на Кинопоиске."

    @R(r"^(?:включи|поставь|переключи(?:\s+на)?)\s+следующ\w+\s+(?:трек|песню|видео|композицию)$")
    def _next2(m, t):
        if not S.media_control("next").get("ok"):
            S.media("next")
        return True

    @R(r"^(?:верни|включи|поставь)\s+(?:предыдущ\w+|прошл\w+)\s+(?:трек|песню|видео|композицию)$")
    def _prev2(m, t):
        if not S.media_control("prev").get("ok"):
            S.media("prev")
        return True

    @R(r"^(?:перемешай|перемешать|включи\s+перемешивание|случайный\s+порядок)(?:\s+(?:треки|песни|плейлист|музыку))?$")
    def _shuffle(m, t):
        res = S.media_control("shuffle", True)
        return True if res.get("ok") else "Плеер не даёт перемешать, сэр."

    @R(r"^(?:включи|поставь)\s+(?:на\s+)?повтор\w*(?:\s+(?:этой|этого|эту)\s+(?:песни|трека|песню|трек|видео))?$|^повтори\s+(?:эту\s+)?(?:песню|трек)$")
    def _repeat(m, t):
        res = S.media_control("repeat", "track")
        return True if res.get("ok") else "Плеер не даёт включить повтор, сэр."

    @R(r"^(?:выключи|отключи|убери)\s+повтор\w*$")
    def _repeat_off(m, t):
        S.media_control("repeat", "none")
        return True

    @R(r"^(?:выключи|останови|закрой)\s+(?:плеер|проигрыватель|воспроизведение)$")
    def _player_off(m, t):
        res = S.media_control("stop")
        if not res.get("ok"):
            S.media_control("pause")
        return True

    @R(r"^(?:сделай\s+)?музык\w*\s+(?:потише|тише)\s*(?:на\s+фон\w*|фоном)?$|^(?:сделай\s+)?музыку\s+(?:на\s+)?фон\w*$|^приглуши\s+музыку$")
    def _music_bg(m, t):
        st = S.media_control("info")
        app = (st.get("app") or "").split("!")[0].split(".")[-1] if st.get("app") else ""
        hit = S.app_volume(app or "browser", level=25) if app else []
        if not hit:
            S.change_volume(-20)
        return "Сделал музыку фоновой."

    @R(r"^(?:открой|запусти|включи)\s+(?:яндекс\s*музык\w*|yandex\s*music)$")
    def _ymusic(m, t):
        ok, _ = S.launch_app("яндекс музыка", core.apps, cfg.get("aliases"))
        if not ok:
            S.open_target("https://music.yandex.ru/home")
        return "Открываю Яндекс Музыку."

    @R(r"^(?:открой|запусти|включи)\s+(?:спотифай|spotify)$")
    def _spotify(m, t):
        ok, _ = S.launch_app("spotify", core.apps, cfg.get("aliases"))
        if not ok:
            S.open_target("https://open.spotify.com/")
        return "Открываю Spotify."
