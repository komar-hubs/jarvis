"""Stress marks for the Silero voice. Silero's own accentor guesses rare words, names, brand names and English words
wrong ("частая багулина — ударения неправильные"). A "+" before a vowel fixes the stress of a word, and Silero keeps
it; this module puts those marks in:

- a built-in list of words Jarvis says all the time (program names, persona names);
- the user's own corrections, taught by voice: «ударение в слове звонит на и», «в слове творог ударение на второй
  слог», or «ты неправильно сказал творог» — Jarvis asks where the stress goes; «забудь ударение в слове творог».
  Kept in config voice.stress {word: "твор+ог"} and applied to every form spelled the same.
- English words (transliterated by textnorm) get the stress on the first syllable, as most of them have it.
Only for Silero: Edge and the Windows voice place stress well on their own."""
import re

VOWELS = "аеёиоуыэюя"
# words Silero tends to stress wrong; the user's corrections win over these
BUILTIN = {
    "джарвис": "дж+арвис", "джарвиса": "дж+арвиса", "джарвису": "дж+арвису", "пятница": "п+ятница", "пятницу": "п+ятницу",
    "альтрон": "альтр+он", "ютуб": "ю+туб", "ютубе": "ю+тубе", "телеграм": "телегр+ам", "телеграме": "телегр+аме",
    "дискорд": "диск+орд", "дискорде": "диск+орде", "спотифай": "спотиф+ай", "ватсап": "вотс+ап", "вотсап": "вотс+ап",
    "майнкрафт": "майнкр+афт", "виндовс": "в+индовс", "гугл": "г+угл", "яндекс": "+яндекс", "стима": "ст+има",
    "звонит": "звон+ит", "позвонит": "позвон+ит", "включит": "включ+ит", "включишь": "включ+ишь",
    "торты": "т+орты", "щавель": "щав+ель", "каталог": "катал+ог", "договор": "догов+ор", "квартал": "кварт+ал",
    "красивее": "крас+ивее", "обеспечение": "обесп+ечение", "приговор": "пригов+ор", "оптовый": "опт+овый",
    "загрузка": "загр+узка", "запуск": "з+апуск", "пробки": "пр+обки",
}
ORD = {"первый": 1, "первую": 1, "первой": 1, "второй": 2, "вторую": 2, "третий": 3, "третью": 3, "третьей": 3,
       "четвертый": 4, "четвертую": 4, "пятый": 5, "пятую": 5, "шестой": 6, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6}
LETTER_NAMES = {"а": "а", "е": "е", "ё": "ё", "йо": "ё", "и": "и", "о": "о", "у": "у", "ы": "ы", "э": "э", "ю": "ю", "я": "я"}


def mark(word, spec):
    """("творог", "на о" / "на второй слог" / "о") -> ("твор+ог", "") or (None, why)."""
    w = word.lower().replace("+", "")
    spec = spec.lower().replace("ё", "ё").strip()
    spec = re.sub(r"^(?:на|ставь|ставить)\s+", "", spec)
    spec = re.sub(r"\s*(?:букву|гласную)\s*", " ", spec).strip()
    vow = [i for i, c in enumerate(w) if c in VOWELS]
    if not vow:
        return None, f"в слове «{word}» нет гласных"
    m = re.match(r"^(\S+)(?:\s+(?:слог\w*|гласн\w*))?$", spec)
    tok = m.group(1) if m else spec
    if tok in ORD:
        n = ORD[tok]
        if n > len(vow):
            return None, f"в слове «{word}» всего {len(vow)} слог{'а' if 2 <= len(vow) <= 4 else 'ов' if len(vow) > 4 else ''}"
        i = vow[n - 1]
    elif tok in LETTER_NAMES:
        ch = LETTER_NAMES[tok]
        hits = [i for i in vow if w[i] == ch]
        if not hits:
            return None, f"в слове «{word}» нет буквы «{ch}»"
        if len(hits) > 1:
            return None, f"в слове «{word}» несколько «{ch}»: скажите, какой слог по счёту, например «на второй слог»"
        i = hits[0]
    else:
        return None, "не понял, куда ударение: скажите «на и» или «на второй слог»"
    return w[:i] + "+" + w[i:], ""


def apply(text, user=None):
    """Puts "+" marks into Russian text for Silero: the user's words, then the built-in list."""
    table = dict(BUILTIN)
    table.update({k.lower(): v for k, v in (user or {}).items()})

    def rep(m):
        w = m.group(0)
        low = w.lower()
        if "+" in w or low not in table:
            return w
        out = table[low]
        return out[:1].upper() + out[1:] if w[:1].isupper() else out
    return re.sub(r"(?<![\w+])[а-яё]+(?![\w+])", rep, text, flags=re.I)


def latin_stress(word):
    """A transliterated English word of 2+ syllables: the stress on the first syllable ("тундерструк" -> "т+ундерструк")."""
    vow = [i for i, c in enumerate(word.lower()) if c in VOWELS]
    if len(vow) < 2 or "+" in word:
        return word
    return word[:vow[0]] + "+" + word[vow[0]:]


class Stress:
    """Settings → Голос → «Ударения»: the same dictionary as the voice commands."""

    def __init__(self, core):
        self.core = core

    def _table(self):
        return dict(self.core.cfg.get("voice.stress") or {})

    def rpc_stress_list(self):
        return {"items": [{"word": k, "marked": v} for k, v in sorted(self._table().items())],
                "builtin": len(BUILTIN)}

    def rpc_stress_set(self, word, spec):
        word = (word or "").strip().lower().replace("+", "")
        if "+" in (spec or "") and spec.replace("+", "").lower() == word:
            marked = spec.lower()                 # typed with the mark: "твор+ог"
        else:
            marked, why = mark(word, spec)
            if not marked:
                raise ValueError(why)
        t = self._table()
        t[word] = marked
        self.core.cfg.set("voice.stress", t)
        return self.rpc_stress_list()

    def rpc_stress_delete(self, word):
        t = self._table()
        t.pop((word or "").lower(), None)
        self.core.cfg.set("voice.stress", t)
        return self.rpc_stress_list()

    def rpc_stress_say(self, word):
        self.core.say(word)
        return True


def register(R, core):
    def table():
        return dict(core.cfg.get("voice.stress") or {})

    def save(word, marked):
        t = table()
        t[word.lower()] = marked
        core.cfg.set("voice.stress", t)

    def plain(marked):
        i = marked.index("+")
        return marked[:i] + marked[i + 1].upper() + marked[i + 2:]

    @R(r"^(?:ударение|ударяй|ставь\s+ударение)\s+в\s+слове\s+([а-яё-]+)\s+(?:ставь\s+|делай\s+)?(на\s+.+)$|"
       r"^в\s+слове\s+([а-яё-]+)\s+ударение\s+(на\s+.+)$|^(?:говори|произноси)\s+([а-яё-]+)\s+с\s+ударением\s+(на\s+.+)$")
    def _set(m, t):
        word = m.group(1) or m.group(3) or m.group(5)
        spec = m.group(2) or m.group(4) or m.group(6)
        marked, why = mark(word, spec)
        if not marked:
            return f"Не получилось: {why}."
        save(word, marked)
        return f"Запомнил: {marked}. Буду говорить «{plain(marked)}»."

    @R(r"^(?:ты\s+)?(?:неправильно|не\s+так|неверно)\s+(?:произносишь|говоришь|сказал\w*|ставишь\s+ударение\s+в)\s+(?:слово\s+)?([а-яё-]+)$|"
       r"^(?:в\s+слове\s+)?([а-яё-]+)\s+(?:неправильное|не\s+то)\s+ударение$")
    def _wrong(m, t):
        import time
        word = (m.group(1) or m.group(2)).lower()

        def got(answer):
            marked, why = mark(word, answer)
            if not marked:
                core.say(f"Не понял: {why}.")
                return True
            save(word, marked)
            core.say(f"Запомнил, «{plain(marked)}».")
            return True
        core.capture = (got, time.time() + 15)
        return f"Куда ставить ударение в слове «{word}»? Скажите букву, например «на о», или слог, например «на второй слог»."

    @R(r"^(?:забудь|удали|сбрось)\s+ударение\s+в\s+слове\s+([а-яё-]+)$")
    def _forget(m, t):
        tb = table()
        if tb.pop(m.group(1).lower(), None) is None:
            return f"Для «{m.group(1)}» я своего ударения не запоминал."
        core.cfg.set("voice.stress", tb)
        return f"Забыл. «{m.group(1)}» снова как получится у голоса."

    @R(r"^(?:какие|покажи)\s+(?:ты\s+)?(?:запомнил\s+)?ударения$|^мои\s+ударения$")
    def _list(m, t):
        tb = table()
        if not tb:
            return "Своих ударений вы мне пока не задавали. Скажите, например: «ударение в слове творог на второй слог»."
        return "Запомнил: " + ", ".join(plain(v) for v in list(tb.values())[:12]) + "."
