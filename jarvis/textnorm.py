"""Text normalisation for Russian TTS: numbers, times, units and Latin words become speakable Cyrillic.

Silero reads only Cyrillic letters, so "18:30", "5%", "Steam" or "RTX 4070" must be spelled out first."""
import re

try:
    from num2words import num2words
except Exception:  # pragma: no cover - optional dependency
    num2words = None

# common Latin words as people pronounce them
LATIN = {
    "steam": "стим", "youtube": "ютуб", "telegram": "телеграм", "discord": "дискорд", "google": "гугл",
    "chrome": "хром", "windows": "виндоус", "github": "гитхаб", "spotify": "спотифай", "twitch": "твич",
    "claude": "клод", "code": "код", "codex": "кодекс", "gemini": "джемини", "antigravity": "антигравити",
    "openai": "опен эй ай", "chatgpt": "чат джи пи ти", "gpt": "джи пи ти", "ai": "эй ай", "api": "эй пи ай",
    "wi-fi": "вай фай", "wifi": "вай фай", "bluetooth": "блютуз", "usb": "ю эс би", "pc": "пи си", "ok": "окей",
    "sir": "сэр", "jarvis": "ассистент", "iron": "айрон", "man": "мэн", "stark": "старк", "tony": "тони",
    "vs": "ви эс", "visual": "вижуал", "studio": "студио", "word": "ворд", "excel": "эксель", "zoom": "зум",
    "obs": "о би эс", "nvidia": "энвидиа", "rtx": "эр ти икс", "gb": "гигабайт", "mb": "мегабайт", "cpu": "процессор",
    "gpu": "видеокарта", "ram": "оперативная память", "email": "имейл", "e-mail": "имейл", "online": "онлайн",
    "minecraft": "майнкрафт", "roblox": "роблокс", "unity": "юнити", "unreal": "анриал", "engine": "энджин",
    "python": "пайтон", "javascript": "джаваскрипт", "html": "эйч ти эм эл", "ollama": "оллама",
    "bitcoin": "биткоин", "usd": "долларов", "eur": "евро", "rutracker": "рутрекер", "yandex": "яндекс",
    "vk": "вэ ка", "ozon": "озон", "wildberries": "вайлдберриз", "avito": "авито", "mail": "мэйл",
}

LETTERS = {"a": "эй", "b": "би", "c": "си", "d": "ди", "e": "и", "f": "эф", "g": "джи", "h": "эйч", "i": "ай",
           "j": "джей", "k": "кей", "l": "эл", "m": "эм", "n": "эн", "o": "оу", "p": "пи", "q": "кью", "r": "ар",
           "s": "эс", "t": "ти", "u": "ю", "v": "ви", "w": "дабл ю", "x": "икс", "y": "уай", "z": "зед"}

# rough phonetic transliteration for unknown Latin words
DIGRAPHS = [("sch", "ш"), ("sh", "ш"), ("ch", "ч"), ("th", "т"), ("ph", "ф"), ("oo", "у"), ("ee", "и"), ("ea", "и"),
            ("ck", "к"), ("qu", "кв"), ("ou", "ау"), ("ai", "эй"), ("ay", "эй"), ("kh", "х"), ("zh", "ж"), ("ts", "ц"),
            ("ya", "я"), ("yu", "ю"), ("yo", "ё")]
SINGLE = {"a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "х", "i": "и", "j": "дж", "k": "к",
          "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "q": "к", "r": "р", "s": "с", "t": "т", "u": "у", "v": "в",
          "w": "в", "x": "кс", "y": "й", "z": "з"}


def _latin_word(w):
    low = w.lower()
    if low in LATIN:
        return LATIN[low]
    if len(low) <= 3 and w.isupper():  # abbreviations: "PDF" -> "пи ди эф"
        return " ".join(LETTERS.get(c, c) for c in low)
    out = low
    for a, b in DIGRAPHS:
        out = out.replace(a, b)
    return "".join(SINGLE.get(c, c) for c in out)


def _num(n, ordinal=False):
    if num2words is None:
        return n
    try:
        value = float(n.replace(",", ".")) if re.search(r"[.,]", n) else int(n)
        return num2words(value, lang="ru", to="ordinal" if ordinal else "cardinal")
    except Exception:
        return n


FEM_NOUN = r"(?:минут\w*|секунд\w*|недел\w*|тысяч\w*|копе\w*|задач\w*|штук\w*|гривн\w*|ноч\w*|рюмк\w*|чашк\w*|вкладк\w*)"
GEN_PREP = r"(?:до|из|от|около|после|без|кроме|более|менее)"


def _num_kw(n, **kw):
    if num2words is None:
        return n
    try:
        return num2words(int(n), lang="ru", **kw)
    except Exception:
        return _num(n)


MONTHS_GEN = r"(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"


def _ord(n, case="n"):
    if num2words is None:
        return n
    try:
        return num2words(int(n), lang="ru", to="ordinal", case=case, gender="n")
    except Exception:
        return _num(n)


def _clock(m):
    h, mi = m.group(1), m.group(2)
    tail = "ноль ноль" if mi == "00" else (f"ноль {_num(mi[1])}" if mi.startswith("0") else _num(mi))
    return f"{_num(str(int(h)))} {tail}"  # "18:04" -> "восемнадцать ноль четыре", not "восемнадцать четыре"


def normalize_ru(text):
    t = text
    t = re.sub(r"(\d)\s(?=\d{3}\b)", r"\1", t)                     # "83 000" -> "83000"
    t = re.sub(r"\b(\d{1,2}):(\d{2})\b", _clock, t)
    # dates are ordinal: "12 октября" -> "двенадцатое октября", "с 3 мая" -> "с третьего мая"
    t = re.sub(rf"\b(?:({GEN_PREP}|с|со|к)\s+)?(\d{{1,2}})\s+({MONTHS_GEN})\b",
               lambda m: (f"{m.group(1)} " if m.group(1) else "") + _ord(m.group(2), "g" if m.group(1) else "n")
               + " " + m.group(3), t)
    # agreement: "до 16" -> "до шестнадцати", "2 минуты" -> "две минуты", "из 10 минут" -> "из десяти минут"
    t = re.sub(rf"\b({GEN_PREP})\s+(\d+)(?![.,:]\d)(?=(\s+{FEM_NOUN})?)",
               lambda m: f"{m.group(1)} {_num_kw(m.group(2), case='g', gender='f' if m.group(3) else 'm')}", t)
    t = re.sub(rf"\b(на|через|за|в)\s+(\d+)(?![.,:]\d)(?=\s+{FEM_NOUN})",   # "на 21 минуту" -> "на двадцать одну минуту"
               lambda m: f"{m.group(1)} {_num_kw(m.group(2), case='a', gender='f', animate=False)}", t)
    t = re.sub(rf"(?<![\d.,])(\d+)(?![.,:]\d)(?=\s+{FEM_NOUN})", lambda m: _num_kw(m.group(1), gender="f"), t)
    t = re.sub(r"(\d+(?:[.,]\d+)?)\s*%", lambda m: f"{_num(m.group(1))} процентов", t)
    t = re.sub(r"(-?\d+(?:[.,]\d+)?)\s*°\s*C?", lambda m: f"{_num(m.group(1))} градусов", t)
    t = re.sub(r"\$\s*(\d+(?:[.,]\d+)?)", lambda m: f"{_num(m.group(1))} долларов", t)
    t = re.sub(r"(\d+(?:[.,]\d+)?)\s*\$", lambda m: f"{_num(m.group(1))} долларов", t)
    t = re.sub(r"(\d+(?:[.,]\d+)?)\s*₽", lambda m: f"{_num(m.group(1))} рублей", t)
    t = re.sub(r"(?<![\w.])-(\d)", r"минус \1", t)
    t = re.sub(r"\d+(?:[.,]\d+)?", lambda m: _num(m.group(0)), t)
    t = re.sub(r"[A-Za-z][A-Za-z'\-]*", lambda m: _latin_word(m.group(0)), t)
    t = t.replace("&", " и ").replace("+", " плюс ").replace("=", " равно ").replace("№", "номер ")
    t = re.sub(r"[«»\"()\[\]{}*_#]", " ", t)
    return re.sub(r"\s+", " ", t).strip()
