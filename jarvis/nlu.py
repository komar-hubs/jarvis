"""Understanding of how people phrase things, before the rule router sees them.

- canonical(): "можешь открыть ютуб" / "хочу посмотреть котиков на ютубе" / "открой-ка" -> imperative commands.
- split_chain(): "сначала открой ютуб, потом найди там котиков и сделай громче" -> ordered steps; it splits on
  "потом/затем/после этого/;" always, and on "и" / "," only when an imperative verb follows, so
  "найди рецепт пиццы и пасты" stays one step.
- search_intent(): "найди в ютубе котиков", "найди котиков на ютубе", "покажи видео с котиками",
  "найди в стиме ..." -> (site, query) with the query cleaned and put in the nominative ("котики", not "котиков").
- delayed(): "через 10 минут выключи звук", "в 18:30 открой почту" -> (when, command).

Word forms come from pymorphy3 (offline dictionary). Without it everything still works, just less smart."""
import re
import time
from urllib.parse import quote_plus

_morph = None


def morph():
    global _morph
    if _morph is None:
        try:
            import pymorphy3
            _morph = pymorphy3.MorphAnalyzer()
        except Exception:
            _morph = False
    return _morph or None


def _parse(word):
    m = morph()
    return m.parse(word) if m else []


# ===================================================================== imperatives
VERB_STEMS = (r"открой|закрой|запусти|включи|выключи|отключи|поставь|найди|поищи|загугли|погугли|сделай|покажи|нажми|"
              r"кликни|щелкни|ткни|наведи|напиши|напечатай|введи|набери|скажи|напомни|добавь|удали|убери|отмени|сверни|"
              r"разверни|перемотай|промотай|переключи|переключись|перейди|убавь|прибавь|заблокируй|переведи|посчитай|запиши|"
              r"создай|сохрани|прочитай|скопируй|вставь|вырежи|выдели|расскажи|перенеси|очисти|запомни|проверь|узнай|"
              r"засеки|заведи|смени|поменяй|измени|листай|прокрути|пролистай|разбуди|сбрось|останови|верни|отметь|"
              r"отправь|ответь|перешли|подожди|жди|обнови|сфотографируй|повтори|продолжи|продолжай|играй|увеличь|"
              r"уменьши|приглуши|заглуши|перетащи|подвинь|двигай|сдвинь|активируй|покажись|спрячься|вруби|выруби|"
              r"посмотри|глянь|послушай|открыть|найти|включить")
NOUN_STARTS = (r"громкость|громче|тише|потише|погромче|пауза|паузу|таймер|будильник|скриншот|скрин|следующий|"
               r"следующее|следующая|предыдущий|предыдущее|стоп|яркость|режим|протокол|брифинг|сводка|погода|"
               r"новая\s+вкладка|полный\s+экран|звук")
CMD_HEAD = re.compile(rf"^(?:{VERB_STEMS}|{NOUN_STARTS})\b")


def is_imperative(word):
    if re.match(rf"^(?:{VERB_STEMS})$", word):
        return True
    return any("impr" in p.tag and p.score >= 0.3 for p in _parse(word))


def to_imperative(word):
    """открыть -> открой, посмотреть -> посмотри; anything else unchanged."""
    for p in _parse(word):
        if p.tag.POS == "INFN" and p.score >= 0.5:
            f = p.inflect({"impr", "excl", "sing"})
            if f:
                return f.word
    return word


FILLERS = r"(?:а\s+теперь|теперь|так|ну|слушай|короче|кстати|значит|эм|ээ|ладно|хорошо|окей|ок|итак|и)"
MODALS = (r"(?:я\s+)?(?:хочу|хотел\s+бы|хотела\s+бы|хочется|мне\s+нужно|мне\s+надо|мне\s+бы|нужно|надо|необходимо|"
          r"можно|прошу|попрошу|попробуй|постарайся|не\s+мог\s+бы\s+ты|не\s+могли\s+бы\s+вы|можешь\s+ли\s+ты|"
          r"сможешь|можешь|давай|будь\s+добр\w*|будь\s+любезен)")


# slang and shortenings -> the words the rules know (whole words only)
SLANG = {
    "врубай": "включи", "вруби": "включи", "врубить": "включи", "включай": "включи", "запускай": "запусти",
    "вырубай": "выключи", "выруби": "выключи", "вырубить": "выключи", "отруби": "выключи", "глянь": "посмотри",
    "чекни": "проверь", "чекай": "проверь", "зачекай": "проверь", "загуглить": "загугли", "гугли": "загугли",
    "скинь": "отправь", "кинь": "отправь", "закинь": "добавь", "черкни": "напиши", "накатай": "напиши",
    "тг": "телеграм", "телега": "телеграм", "телегу": "телеграм", "телеге": "телеграм", "тележку": "телеграм",
    "дс": "дискорд", "дискорде": "дискорд", "дискорда": "дискорд", "вэка": "вк", "вконтач": "вк",
    "инет": "интернет", "инете": "интернете", "инета": "интернета", "комп": "компьютер", "компа": "компьютер",
    "компе": "компьютере", "пк": "компьютер", "ноут": "компьютер", "музон": "музыку", "музло": "музыку",
    "музыку": "музыку", "песенку": "песню", "трэк": "трек", "видос": "видео", "видосик": "видео", "видосы": "видео",
    "ютубчик": "ютуб", "ютубе": "ютубе", "ютьюб": "ютуб", "ютюб": "ютуб", "хром": "хром", "браузерчик": "браузер",
    "звучок": "звук", "громкасть": "громкость", "погромче": "громче", "потише": "тише", "почтовик": "почту",
    "щас": "сейчас", "ща": "сейчас", "сёдня": "сегодня", "седня": "сегодня", "завтро": "завтра", "плз": "",
    "пж": "", "пжлст": "", "плиз": "", "пожалуйсто": "", "чё": "что", "че": "что", "шо": "что", "скока": "сколько",
    "скок": "сколько", "норм": "нормально", "спс": "спасибо",
    "пасиб": "спасибо", "сенкс": "спасибо", "погнали": "давай", "таймерок": "таймер",
    "будилку": "будильник", "напоминалку": "напоминание", "скрин": "скриншот", "скриншотик": "скриншот",
    "загрузы": "загрузки", "имейл": "письмо", "емейл": "письмо", "мейл": "письмо", "эмейл": "письмо",
}
# elliptic phrases: what is meant without the verb
ELLIPSIS = [
    (r"^(?:почту|письмо|письмецо|сообщение\s+на\s+почту)\s+(.+)$", r"напиши письмо \1"),     # "почту шефу"
    (r"^(?:громкость|звук)\s+(\d+)(?:\s*%|\s+процент\w*)?$", r"громкость \1"),
    (r"^(?:таймер)\s+(\d.*)$", r"поставь таймер на \1"),
    (r"^(?:будильник)\s+(?:на\s+)?(\d.*)$", r"поставь будильник на \1"),
    (r"^(?:ютуб|youtube)\s+(.+)$", r"найди на ютубе \1"),
    (r"^(?:гугл|google)\s+(.+)$", r"загугли \1"),
    (r"^(?:погода|погоду)(\s+.*)?$", r"какая погода\1"),
    (r"^(?:курс)\s+(доллар\w*|евро|юан\w*|валют\w*)$", r"курс \1"),
    (r"^(?:музыку|музыка)$", r"включи музыку"),
    (r"^(?:музыку|музыка)\s+(.+)$", r"включи песню \1"),
    (r"^(?:пауз[ау])$", r"пауза"),
]


def unslang(t):
    words = t.split()
    out = []
    for w in words:
        r = SLANG.get(w, w)
        if r:
            out.append(r)
    return " ".join(out)


def canonical(t):
    """Normalized text (lower case, no punctuation) -> the same request as a plain imperative command."""
    t = unslang(t.strip())
    for rx, rep in ELLIPSIS:
        if re.match(rx, t):
            t = re.sub(rx, rep, t).strip()
            break
    for _ in range(3):  # "а теперь, ну, открой" -> "открой"
        t2 = re.sub(rf"^{FILLERS}\s+", "", t)
        if t2 == t:
            break
        t = t2
    t = re.sub(r"\b(\w+)-ка\b", r"\1", t)              # "открой-ка"
    t = re.sub(r"\s+(?:пожалуйста|плиз|пж|please)$", "", t)
    m = re.match(rf"^{MODALS}\s+(\w+)(.*)$", t)
    if m:
        verb = to_imperative(m.group(1))
        if verb != m.group(1) or is_imperative(m.group(1)):
            t = verb + m.group(2)
    else:
        first = t.split(" ", 1)
        if first and first[0]:
            imp = to_imperative(first[0])
            if imp != first[0]:
                t = imp + (" " + first[1] if len(first) > 1 else "")
    # same request, the word the rules know: "послушай музыку" = "включи музыку"
    t = re.sub(r"^(?:послушай|послушаем|послушать)\s+", "включи ", t)
    return t.strip()


# ===================================================================== chains
STRONG = re.compile(r"\s*(?:;|,?\s*(?:(?:а|и)\s+)?(?:потом|затем|после\s+этого|после\s+чего|далее|следом|в\s+конце|"
                    r"напоследок|в\s+самом\s+конце|во-вторых|в-третьих|в-четвертых|и\s+ещ[её]|а\s+ещ[её]|а\s+также)\b)\s*")
WEAK = re.compile(r"\s*(?:,|\s\bи\b|\s\bа\b|\s\bплюс\b)\s*")
LEAD = re.compile(r"^(?:сначала|для\s+начала|сперва|во-первых|первым\s+делом|сейчас)\s+")


def _protect_quotes(raw):
    """«…» and "…" stay whole: 'напиши «привет, как дела» и отправь' must not split inside the quotes."""
    keep = []

    def sub(m):
        keep.append(m.group(0))
        return f"\x00{len(keep) - 1}\x00"
    return re.sub(r"«[^»]*»|\"[^\"]*\"", sub, raw), keep


def _restore(s, keep):
    return re.sub(r"\x00(\d+)\x00", lambda m: keep[int(m.group(1))], s)


def _starts_command(piece):
    w = re.sub(r"^[\s,]+", "", piece.lower().replace("ё", "е"))
    w = re.sub(rf"^{FILLERS}\s+", "", w)
    w = re.sub(rf"^{MODALS}\s+", "", w)
    first = (re.findall(r"[\w-]+", w) or [""])[0]
    if not first:
        return False
    if CMD_HEAD.match(w):
        return True
    return is_imperative(first.replace("-ка", "")) or any(
        p.tag.POS == "INFN" and p.score >= 0.5 for p in _parse(first)) and bool(re.match(rf"^{MODALS}\s", piece.strip().lower()))


TEXT_VERB = re.compile(r"^(?:напиши|напечатай|введи|набери|впиши|скажи|ответь|отправь|перешли|запиши|продиктуй)\b")
AFTER_TEXT = re.compile(r"^(?:сохрани|отправь|нажми|закрой|сверни|открой|выключи|включи|сделай|поставь|найди|запусти|"
                        r"перейди|переключись|скопируй|выдели|удали|подожди)\b")


def split_chain(raw):
    """One phrase -> ordered steps (strings in the user's words). One step -> [raw]."""
    text, keep = _protect_quotes(raw.strip())
    text = LEAD.sub("", text.strip(), count=1) if LEAD.match(text.lower()) else text
    # "открой телеграм, давай": the go-ahead at the end is not part of the command ("телеграм, давай" is no app)
    head = re.sub(r"[,\s]+(?:ну\s+)?(?:делай|сделай|давай|выполняй|действуй|жми|го)(?:\s+так|\s+это)?[\s.!]*$", "", text,
                  flags=re.I)
    cut = head != text and bool(head.strip()) and _starts_command(head)
    if cut:
        text = head
    steps = []
    for chunk in STRONG.split(text):
        chunk = chunk.strip(" ,")
        if not chunk:
            continue
        # weak separators: only where the next piece starts like a command
        pieces, start = [], 0
        for m in WEAK.finditer(chunk):
            if m.start() <= start:
                continue
            nxt = chunk[m.end():]
            cur = chunk[start:m.start()].strip(" ,").lower()
            # "вот так, делай", "открой телеграм, давай": a lone "делай / давай" is a go-ahead, not a step of its own
            if re.fullmatch(r"\s*(?:ну\s+)?(?:делай|сделай|давай|выполняй|действуй|продолжай|жми|го)(?:\s+так|\s+это)?[\s.!]*", nxt.lower()):
                continue
            # "мне скучно, включи музыку": the first part is not a command, so this is one request for the AI
            if start == 0 and not _starts_command(cur):
                continue
            if TEXT_VERB.match(cur):
                # inside text to write ("напиши маме привет и скажи, что я опоздаю") only a clear next action splits
                if not AFTER_TEXT.match(nxt.strip().lower()):
                    continue
            if _starts_command(nxt):
                pieces.append(chunk[start:m.start()])
                start = m.end()
        pieces.append(chunk[start:])
        steps += [LEAD.sub("", p.strip(" ,")) for p in pieces if p.strip(" ,")]
    steps = [_restore(s, keep) for s in steps]
    return steps if len(steps) > 1 else [_restore(text, keep) if cut else raw]


PLACE_WORDS = (r"(?:в|во)\s+(?:(?:нов\w+|этом|этот|эту|том|открыт\w+|пуст\w+|перв\w+|последн\w+|активн\w+)\s+)?"
               r"(?:нем|нём|него|ней|нее|неё|них|этом|это|эту|этой|чат\w*|окн\w*|окошк\w*|пол[еяю]|строк\w*|"
               r"сообщени\w*|диалог\w*|переписк\w*|блокнот\w*|ворд\w*|документ\w*)|на\s+нем|на\s+нём|на\s+ней|туда|там|тут|"
               r"здесь|сюда|ему|ей|им|текст(?:ом)?|слово|слова|фразу|следующее|такое|что")


def text_to_type(raw):
    """'напиши в нем привет' -> 'привет', 'напиши туда «Как дела?»' -> 'Как дела?': the place words and the verb
    are not part of the text (Jarvis typed "в нем привет" into Antigravity)."""
    t = re.sub(r"(?i)^\W*(?:джарвис|jarvis)\w*[\s,.!:—-]*", "", (raw or "").strip())
    t = re.sub(r"(?i)^(?:а\s+)?(?:потом\s+|затем\s+|и\s+)?(?:напиши|напечатай|набери|введи|впиши|вбей|пиши)[\s,:]*", "", t)
    for _ in range(3):  # "в нем текст: привет", "туда что"
        t2 = re.sub(rf"(?i)^(?:{PLACE_WORDS})(?=[\s,:«\"]|$)[\s,:—-]*", "", t)
        if t2 == t:
            break
        t = t2
    q = re.fullmatch(r"\s*[«\"](.*)[»\"]\s*[.!]?\s*", t)
    if q:
        t = q.group(1)
    return t.strip()


# ===================================================================== delayed commands
def delayed(t):
    """'через 10 минут выключи звук' / 'в 18:30 открой почту' -> (datetime, 'выключи звук') or None.
    Only when a command follows the time: 'через 10 минут напомни ...' stays a reminder."""
    from .planner import parse_when, words_to_digits
    t = words_to_digits(t)
    m = re.match(r"^(через\s+(?:\d+\s*\S+(?:\s+\d+\s*\S+)?|полчаса|час|минуту|пару\s+минут)|"
                 r"(?:сегодня\s+|завтра\s+)?в\s+\d{1,2}(?:[:.]\d{2}|\s+час\w*)?(?:\s+(?:утра|вечера|дня|ночи))?)\s+(.+)$", t)
    if not m:
        return None
    rest = m.group(2).strip()
    if not _starts_command(rest) or re.match(r"^(?:напомни|разбуди|поставь\s+будильник)", rest):
        return None
    when, left = parse_when(m.group(1))
    if not when or left.strip():
        return None
    return when, rest


# ===================================================================== search
# (key, title, spoken site pattern, search url)
ENGINES = [
    ("youtube", "YouTube", r"ютуб\w*|ютьюб\w*|ютюб\w*|youtube|you\s+tube|ютубчик\w*", "https://www.youtube.com/results?search_query={q}"),
    ("rutube", "Rutube", r"рутуб\w*|rutube", "https://rutube.ru/search/?query={q}"),
    ("ymusic", "Яндекс Музыку", r"яндекс\s*музык\w*", "https://music.yandex.ru/search?text={q}"),
    ("market", "Яндекс Маркет", r"яндекс\s*маркет\w*|маркет\w*", "https://market.yandex.ru/search?text={q}"),
    ("maps", "карты", r"(?:яндекс\s*|гугл\s*)?карт(?:ах|е|у|ы|а)", "https://yandex.ru/maps/?text={q}"),
    ("wiki", "Википедию", r"википеди\w*|вики", "https://ru.wikipedia.org/w/index.php?search={q}"),
    ("yandex", "Яндекс", r"яндекс\w*|yandex", "https://yandex.ru/search/?text={q}"),
    ("google", "Google", r"гугл\w*|google|интернет\w*|сети", "https://www.google.com/search?q={q}"),
    ("steam", "Steam", r"стим\w*|steam", "https://store.steampowered.com/search/?term={q}"),
    ("vk", "ВКонтакте", r"вк|вконтакт\w*|контакт\w*|vk", "https://vk.com/search?c%5Bq%5D={q}"),
    ("avito", "Авито", r"авито\w*|avito", "https://www.avito.ru/rossiya?q={q}"),
    ("ozon", "Озон", r"озон\w*|ozon", "https://www.ozon.ru/search/?text={q}"),
    ("wb", "Wildberries", r"вайлдбер+из\w*|валдбер+из\w*|валбер+из\w*|вб|wildberries", "https://www.wildberries.ru/catalog/0/search.aspx?search={q}"),
    ("ali", "AliExpress", r"алиэкспресс\w*|али\s*экспресс\w*|aliexpress|али", "https://aliexpress.ru/wholesale?SearchText={q}"),
    ("github", "GitHub", r"гитхаб\w*|github", "https://github.com/search?q={q}"),
    ("habr", "Хабр", r"хабр\w*|habr", "https://habr.com/ru/search/?q={q}"),
    ("kinopoisk", "Кинопоиск", r"кинопоиск\w*", "https://www.kinopoisk.ru/index.php?kp_query={q}"),
    ("twitch", "Twitch", r"твич\w*|twitch", "https://www.twitch.tv/search?term={q}"),
    ("tiktok", "TikTok", r"тик\s*ток\w*|tiktok", "https://www.tiktok.com/search?q={q}"),
    ("pinterest", "Pinterest", r"пинтерест\w*|pinterest", "https://www.pinterest.com/search/pins/?q={q}"),
    ("reddit", "Reddit", r"реддит\w*|reddit", "https://www.reddit.com/search/?q={q}"),
    ("spotify", "Spotify", r"спотифа\w*|spotify", "https://open.spotify.com/search/{q}"),
    ("translate", "переводчик", r"переводчик\w*|гугл\s*переводчик\w*", "https://translate.google.com/?sl=auto&tl=ru&text={q}"),
    ("images", "картинки", r"картинк\w*|изображени\w*", "https://www.google.com/search?tbm=isch&q={q}"),
    ("news", "новости", r"новост\w*", "https://news.google.com/search?q={q}&hl=ru&gl=RU&ceid=RU:ru"),
]
ENGINE = {k: (title, url) for k, title, _, url in ENGINES}
# a content word without a site: "найди видео с котиками" -> YouTube
CONTENT = [(r"видео\w*|видос\w*|ролик\w*|клип\w*|видеоролик\w*|стрим\w*|летсплей\w*|обзор(?:ы|ов)?\s+на", "youtube"),
           (r"картинк\w*|изображени\w*|фото(?:графи\w*)?|фотк\w*|обои|рисун\w*|арт(?:ы)?|мем(?:ы|чики)?", "images"),
           (r"новост\w*", "news")]
SEARCH_VERB = (r"(?:найди|поищи|ищи|загугли|погугли|поиск|найти|поискать|покажи|посмотри|глянь|включи|поставь|запусти|"
               r"открой|вбей|забей|отыщи|выведи)")
FILLER_Q = (r"(?:мне|нам|пожалуйста|там|тут|здесь|какие-нибудь|какое-нибудь|какой-нибудь|какую-нибудь|что-нибудь|что-то|"
            r"чего-нибудь|немного|пару|несколько|ещ[её]|плиз)")


def site_from_url(url):
    u = (url or "").lower()
    for key, host in (("youtube", "youtube.com"), ("youtube", "youtu.be"), ("rutube", "rutube.ru"), ("wiki", "wikipedia.org"),
                      ("steam", "steampowered.com"), ("vk", "vk.com"), ("avito", "avito.ru"), ("ozon", "ozon.ru"),
                      ("wb", "wildberries.ru"), ("market", "market.yandex"), ("ymusic", "music.yandex"),
                      ("maps", "yandex.ru/maps"), ("github", "github.com"), ("habr", "habr.com"), ("twitch", "twitch.tv"),
                      ("kinopoisk", "kinopoisk.ru"), ("ali", "aliexpress"), ("reddit", "reddit.com"),
                      ("pinterest", "pinterest."), ("tiktok", "tiktok.com"), ("spotify", "spotify.com"),
                      ("yandex", "yandex.ru/search"), ("google", "google.")):
        if host in u:
            return key
    return None


def _is_adj(word):
    ps = _parse(word)
    return bool(ps) and ps[0].tag.POS in ("ADJF", "PRTF", "NUMR") or word in ("какие-нибудь", "какое-нибудь", "новые", "новое")


def nominative(phrase):
    """'смешных котиков' -> 'смешные котики', 'котиков и собачек' -> 'котики и собачки'.
    Only a leading noun group is changed: 'рецепт пиццы', 'как варить борщ', 'марвел савенджес' stay as they are."""
    if not morph() or not phrase:
        return phrase
    parts = re.split(r"(\s+и\s+)", phrase)
    return "".join(p if re.fullmatch(r"\s+и\s+", p) else _nominative_np(p) for p in parts)


OBLIQUE = {"gent", "accs", "datv", "ablt", "loct", "gen2", "acc2", "loc2"}


def _nominative_np(phrase):
    words = phrase.split()
    if not words or len(words) > 6:
        return phrase
    head = None
    for i, w in enumerate(words[:4]):
        if not re.fullmatch(r"[а-яё-]+", w):
            return phrase
        ps = _parse(w)
        if not ps or not ps[0].is_known:
            return phrase
        top = ps[0]
        if top.tag.POS == "NOUN":
            head = i
            break
        if top.tag.POS not in ("ADJF", "PRTF"):
            return phrase
    if head is None:
        return phrase
    noun = _parse(words[head])[0]
    if noun.tag.case not in OBLIQUE:
        return phrase
    number = noun.tag.number or "sing"
    out = list(words)
    f = noun.inflect({"nomn", number})
    if not f:
        return phrase
    out[head] = f.word
    gender = noun.tag.gender
    for i in range(head):
        a = _parse(words[i])[0]
        want = {"nomn", number} | ({gender} if number == "sing" and gender else set())
        g = a.inflect(want)
        if g:
            out[i] = g.word
    return " ".join(out)


def clean_query(q, kind=None):
    """Spoken leftovers -> what to type into a search box."""
    q = " " + q.strip() + " "
    for _ in range(3):
        q = re.sub(rf"^\s*{FILLER_Q}\s+", " ", q)
    if kind in ("youtube", "images", "news", None):
        q = re.sub(r"^\s*(?:" + "|".join(rx for rx, _ in CONTENT) + r")\s+", " ", q)
    q = re.sub(r"^\s*(?:с|со|про|о|об|обо|на\s+тему|по\s+теме|по|где|в\s+котор\w+|котор\w+|из)\s+", " ", q)
    q = re.sub(rf"\s+{FILLER_Q}\s*$", " ", q)
    q = re.sub(r"\s+", " ", q).strip(" ,.")
    if re.match(r"^(?:как|что|почему|зачем|где|когда|кто|сколько|какой|какая|какие|чем|можно|ли)\b", q):
        return q  # a question: keep it word for word
    return nominative(q)


def _site_rx():
    return "|".join(f"(?P<{k}>{rx})" for k, _, rx, _ in ENGINES)


SITE_PHRASE = re.compile(rf"(?:\s|^)(?:на|в|во|по|через)\s+(?:сайте\s+|приложении\s+)?(?:{_site_rx()})(?=\s|$)")


def _which(m):
    return next(k for k, v in m.groupdict().items() if v)


def search_intent(t, ctx_site=None):
    """Normalized command -> (site key, query) or None. ctx_site: the site opened by the previous step
    ('открой ютуб и найди там котиков')."""
    m = re.match(rf"^{SEARCH_VERB}\s+(.+)$", t)
    if not m:
        return None
    verb = t.split(" ", 1)[0]
    body = " " + m.group(1) + " "
    if re.search(r"\s(?:на\s+(?:компьютере|компе|пк|диске|рабочем\s+столе)|в\s+(?:папке|проводнике|загрузках|документах)|у\s+меня)\s", body):
        return None  # files on this PC, not the web
    site = None
    sm = SITE_PHRASE.search(body)
    if sm:
        site = _which(sm)
        body = body[:sm.start()] + " " + body[sm.end():]
    elif verb in ("открой", "запусти", "включи") and re.match(rf"^\s*(?:{_site_rx()})\s+(?:с|со|про|где)\s+", body):
        # "открой ютуб с котиками" / "включи ютуб про космос"
        sm = re.match(rf"^\s*(?:{_site_rx()})\s+", body)
        site = _which(sm)
        body = body[sm.end():]
    elif re.search(r"(?:^|\s)(?:там|тут|в\s+нем|на\s+нем|в\s+ней|на\s+ней|на\s+этом\s+сайте)(?:\s|$)", body) and ctx_site:
        site = ctx_site
        body = re.sub(r"(?:^|\s)(?:там|тут|в\s+нем|на\s+нем|в\s+ней|на\s+ней|на\s+этом\s+сайте)(?=\s|$)", " ", body)
    if not site:
        for rx, key in CONTENT:
            # "видео с котиками", "смешные видео про котов" (up to two describing words before the content word)
            cm = re.match(rf"^\s*(?:{FILLER_Q}\s+)*((?:[а-яё-]+\s+){{0,2}}?)(?:{rx})(?=\s|$)", body)
            if cm and all(_is_adj(w) for w in cm.group(1).split()):
                site = key
                rest = re.sub(r"^\s*(?:с|со|про|о|об|обо|на\s+тему|по\s+теме|где|в\s+котор\w+|котор\w+)\s+", " ", body[cm.end():])
                body = " " + cm.group(1) + rest
                break
    if not site:
        if verb in ("открой", "запусти", "включи", "поставь", "покажи", "посмотри", "глянь", "выведи"):
            return None  # "открой телеграм", "покажи задачи": not a search
        site = "google"
    q = clean_query(body, site)
    if not q or q in ("его", "ее", "их", "это"):
        return None
    return site, q


def search_url(site, query):
    return ENGINE.get(site, ENGINE["google"])[1].replace("{q}", quote_plus(query))


# ===================================================================== chain context
class Context:
    """What the previous steps did: the site that was opened, the app that was launched, the last query."""

    def __init__(self):
        self.site = None
        self.site_at = 0.0
        self.app_at = 0.0
        self.query = None

    def note_url(self, url):
        s = site_from_url(url)
        if s:
            self.site, self.site_at = s, time.time()

    def current_site(self, max_age=600):
        return self.site if self.site and time.time() - self.site_at < max_age else None
