"""Messages in desktop messengers: "напиши Ване привет", "открой телеграм и напиши Ване привет",
"скинь в дискорд Пете что я опаздываю", "напиши в вк Пете что я занят, распиши красиво".

Jarvis brings the messenger to the front (starting it if needed), opens the chat through the app's own search
(Telegram: the search field, Discord: Ctrl+K, WhatsApp: Ctrl+F, ВКонтакте: its web messenger in the browser), types
the text and — unless turned off in Settings — asks "Отправить?" before pressing Enter, so a wrong chat never gets
the message.

"…распиши красиво": the AI turns the gist into a finished message in the user's voice (no invented facts), the text is
read back in the question, and only "да" sends it."""
import re
import threading
import time

from . import system as S

APPS = {
    "telegram": {"exe": ("telegram.exe", "ayugram.exe", "kotatogram.exe", "64gram.exe"), "launch": "telegram", "title": "Telegram"},
    "discord": {"exe": ("discord.exe", "vesktop.exe", "willicord.exe"), "launch": "discord", "title": "Discord"},
    "whatsapp": {"exe": ("whatsapp.exe", "whatsapp.root.exe"), "launch": "whatsapp", "title": "WhatsApp"},
    # no desktop app: the web messenger in whatever browser is the default
    "vk": {"exe": (), "web": "https://vk.com/im", "title": "ВКонтакте", "in": "ВКонтакте",
           "titles": ("вконтакте", "vk.com", "| vk", "— vk", "- vk", "vk мессенджер")},
}
BROWSERS = ("chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "browser.exe", "brave.exe", "vivaldi.exe", "yandex.exe")
APP_WORDS = (r"(телеграм\w*|телеграмм\w*|тг|телег\w*|дискорд\w*|дс|ватсап\w*|вотсап\w*|whatsapp|telegram|discord|"
             r"вконтакте|в\s?контакте|вк|vk|вэ\s?ка|ве\s?ка)")
# "красиво", "распиши от себя", "вежливо": the text is composed, not typed as said
STYLE_TAIL = re.compile(r"(?:[,\s]+(?:и\s+)?(?:там\s+)?)?(?:распиши(?:\s+это|\s+его|\s+там)?|расписать|распишите|сформулируй|оформи|придумай\s+текст|"
                        r"напиши\s+красиво|скажи\s+красиво)?(?:\s+(?:от\s+себя|своими\s+словами|там))*"
                        r"(?:\s+(?:красиво|вежливо|покрасивее|развёрнуто|развернуто|по-человечески|тепло|культурно|грамотно))*[\s.!]*$", re.I)
# a bare "красиво" can be part of the message ("платье красиво"): it counts only with a request around it
STYLE_WORD = re.compile(r"(?i)\b(?:распиши\w*|расписать|сформулируй\w*|оформи\w*|придумай\s+текст|от\s+себя|своими\s+словами|"
                        r"покрасивее|развёрнуто|развернуто|по-человечески|вежливо|(?:и|но|там|ещё|еще|только|напиши|скажи)\s+красиво)\b")


def app_of(word):
    w = re.sub(r"\s+", "", (word or "").lower())
    if re.match(r"тел|тг|telegram", w):
        return "telegram"
    if re.match(r"дис|дс|discord", w):
        return "discord"
    if re.match(r"ват|вот|whatsapp", w):
        return "whatsapp"
    if re.match(r"вк|vk|вконтакте|вэка|века", w):
        return "vk"
    return None


def active_messenger():
    exe = S.active_exe()
    return next((k for k, v in APPS.items() if exe in v["exe"]), None)


def _window_of(app):
    a = APPS[app]
    for w in S.list_windows():
        proc, title = (w["proc"] or "").lower(), (w["title"] or "").lower()
        if a["exe"]:
            if proc in a["exe"]:
                return w["hwnd"]
        elif proc in BROWSERS and any(t in title for t in a["titles"]):
            return w["hwnd"]
    return None


def bring(core, app):
    """Front window of the messenger; starts it when it is not running. Returns the hwnd or None."""
    a = APPS[app]
    if a.get("web"):
        return _bring_web(core, app)
    h = _window_of(app)
    if not h:
        S.launch_app(a["launch"], core.apps, core.cfg.get("aliases"))
        for _ in range(40):
            time.sleep(0.25)
            h = _window_of(app)
            if h:
                time.sleep(1.2)  # let it draw the chat list
                break
    if not h:
        return None
    S.focus_window(h)
    time.sleep(0.35)
    return h


def _bring_web(core, app, url=None):
    """The web messenger: the address goes to the default browser (a new tab in the open window, which comes forward
    by itself); then we wait for a window of this site, and check that the user is logged in."""
    import os
    a = APPS[app]
    os.startfile(url or a["web"])
    h = None
    for _ in range(48):                       # up to 12 s
        time.sleep(0.25)
        h = _window_of(app)
        if h:
            break
    if not h and S.active_exe() in BROWSERS:   # the title may not carry the site's name yet
        import win32gui
        h = win32gui.GetForegroundWindow()
    if not h:
        return None
    S.focus_window(h)
    time.sleep(2.6)                            # a single-page site: let the chat list draw
    try:
        from . import mouse
        text = " ".join(l["text"] for l in mouse.ocr(max_age=0))
        if re.search(r"(?i)войти\s+в\s*контакте|телефон\s+или\s+почт|войдите|sign\s?in|создать\s+аккаунт", text) and \
                not re.search(r"(?i)поиск|сообщени", text):
            raise RuntimeError(f"в {a['in']} не выполнен вход: войдите в аккаунт в браузере")
    except RuntimeError:
        raise
    except Exception:
        pass                                   # OCR is a help, not a condition
    return h


def _vk_link(core, who):
    """A saved direct link (config messenger.vk_links: {"петя": "https://vk.com/im?sel=123456"}) opens the chat with no search."""
    links = {k.lower(): v for k, v in (core.cfg.get("messenger.vk_links") or {}).items()}
    return links.get((who or "").lower())


def _vk_open_chat(core, who):
    from . import mouse
    link = _vk_link(core, who)
    if link:
        if not _bring_web(core, "vk", link):
            raise RuntimeError("браузер с ВКонтакте не открылся")
        time.sleep(0.8)
        return
    if not _bring_web(core, "vk"):
        raise RuntimeError("браузер с ВКонтакте не открылся")
    box = None
    for q in ("Поиск", "Поиск по сообщениям", "Поиск по диалогам", "Search"):
        try:
            box = mouse.find(q, near_role={"edit", "combobox"})
        except Exception:
            box = None
        if box:
            break
    if not box:
        raise RuntimeError("не вижу поиска в ВКонтакте: откройте сообщения vk.com/im и попробуйте ещё раз")
    mouse.click(box["x"], box["y"])
    time.sleep(0.3)
    S.hotkey("ctrl+a")
    S.type_text(who)
    time.sleep(1.7)                            # the results
    # the first result below the search field whose text looks like the name (the typed text itself is above it)
    best = None
    for ln in mouse.ocr(max_age=0):
        if ln["y"] <= box["y"] + 28 or ln["x"] > box["x"] + 700:
            continue
        sc = mouse.score(who, ln["text"])
        if sc >= 78 and (best is None or ln["y"] < best["y"]):
            best = ln
    if not best:
        raise RuntimeError(f"не нашёл «{who}» в ВКонтакте")
    mouse.click(best["x"] + best["w"] // 2, best["y"] + best["h"] // 2)
    time.sleep(1.0)


def open_chat(core, app, who):
    if app == "vk":
        _vk_open_chat(core, who)
        try:                                   # the message field is usually focused by itself; make sure
            from . import mouse
            f = mouse.find("Напишите сообщение", near_role={"edit"}) or mouse.find("Написать сообщение", near_role={"edit"})
            if f:
                mouse.click(f["x"], f["y"])
                time.sleep(0.2)
        except Exception:
            pass
        return
    if not bring(core, app):
        raise RuntimeError(f"{APPS[app]['title']} не запускается")
    if app == "discord":
        S.hotkey("ctrl+k")
    elif app == "whatsapp":
        S.hotkey("ctrl+f")
    else:
        S.hotkey("esc")
        time.sleep(0.15)
        S.hotkey("esc")
        time.sleep(0.2)
        from . import mouse
        hit = None
        try:
            hit = mouse.find("поле поиска") or mouse.find("Поиск") or mouse.find("Search")
        except Exception:
            pass
        if hit:
            mouse.click(hit["x"], hit["y"])
        else:
            S.hotkey("ctrl+f")
    time.sleep(0.35)
    S.hotkey("ctrl+a")
    S.type_text(who)
    time.sleep(1.3)  # search results
    S.hotkey("enter")
    time.sleep(0.8)


def send(core, app, who, text, composed=False):
    open_chat(core, app, who)
    S.type_text(text)
    title = APPS[app]["title"]
    core.journal.record(f"набрал сообщение для {who} в {title}", None, "message")
    if composed:
        # a composed text is read back: the user hears exactly what goes out (long ones stay on the screen)
        shown = text if len(text) <= 230 else text[:220].rsplit(" ", 1)[0] + "…"
        ask = f"Для {who} в {APPS[app].get('in', title)} набрано: «{shown}». Отправить?"
    else:
        ask = f"Сообщение для {who} набрано в {title}. Отправить?"
    if core.cfg.get("messenger.confirm", True) or composed:
        core.ask_confirm(lambda: (S.hotkey("enter"), core.say("Отправлено.")), ask)
    else:
        time.sleep(0.2)
        S.hotkey("enter")
        core.say(f"Отправил {who}.")


def contact_name(core, word):
    """'ване' -> 'Ваня', 'шефу' -> the person behind the role, 'маме' -> 'Мама' (as chats are often named)."""
    from .people import lemma, role_of
    if role_of(word):
        p, _ = core.people.resolve(word)
        return p["name"] if p else lemma(word).capitalize()
    return lemma(word).capitalize()


def is_person_word(core, word):
    from .nlu import _parse
    from .people import role_of
    if role_of(word) or core.people.by_name(word):
        return True
    return any(("Name" in p.tag or "Surn" in p.tag) and p.score >= 0.1 for p in _parse(word.lower())[:4])


# ====================================================================== the AI writes the message
FORMAL = re.compile(r"(?i)шеф|начальник|босс|директор|руководител|клиент|заказчик|преподавател|учител|декан|коллег")
COMPOSE_RULES = (
    "Ты помогаешь человеку написать сообщение в мессенджере от его лица. Тебе дают суть и имя адресата. "
    "Напиши готовый текст, который человек отправит как есть: от первого лица, по-русски, живо, грамотно и красиво, "
    "но без пафоса и канцелярита. Структура: короткое приветствие с именем адресата, затем суть своими красивыми "
    "словами, а не сухой пересказ, затем тёплое завершение (благодарность за понимание, пожелание хорошего дня "
    "или обещание вернуться к разговору). Всего 3–4 предложения, не меньше трёх. "
    "Если адресат явно начальник, клиент, преподаватель или малознакомый человек, пиши на «вы», вежливо и сдержанно; "
    "если близкий (друг, родственник, обычное имя), пиши на «ты», тепло и просто. "
    "Не выдумывай фактов: никаких причин, времени, обещаний и подробностей, которых нет в сути. "
    "Допустимо только общее, например «отвечу, как только освобожусь». "
    "Без эмодзи, без кавычек, без подписи, без пояснений и вступлений вроде «Конечно». Один абзац, без переносов строк. "
    "Выведи только сам текст сообщения.")


def fallback_text(who, gist, formal):
    """No AI (or it failed): a polite frame around the gist, nothing invented."""
    g = gist.strip().rstrip(".!") or "у меня сейчас дела"
    g = g[:1].upper() + g[1:]
    hello = f"Здравствуйте, {who}!" if formal else f"Привет, {who}!"
    tail = "Отвечу, как только освобожусь. Спасибо за понимание!" if formal else "Отвечу, как только освобожусь. Спасибо, что понимаешь!"
    return f"{hello} {g}. {tail}"


def compose(core, who, who_word, gist, app_title):
    """The message text from the gist. Never raises: on any failure the fallback text is returned. Returns (text, by_ai)."""
    formal = bool(FORMAL.search(who_word or "")) or bool(FORMAL.search(who or ""))
    try:
        from .llm import stream_chat
        prov = core.cfg.provider()
        msgs = [{"role": "system", "content": COMPOSE_RULES},
                {"role": "user", "content": f"Адресат: {who}" + (f" (человек сказал: «{who_word}»)" if who_word and who_word.lower() != who.lower() else "")
                 + f"\nМессенджер: {app_title}\nСуть сообщения, как её сказал человек: {gist}"}]
        out = "".join(stream_chat(prov, msgs, temperature=0.7, bridges=getattr(core, "bridges", None)))
    except Exception as e:
        core.log(f"Сообщение от ИИ не вышло: {e}", "warn")
        return fallback_text(who, gist, formal), False
    out = re.sub(r"(?s)<think>.*?</think>|<jarvis>.*?</jarvis>", "", out or "")
    out = re.sub(r"[`*_#>]+", "", out)
    out = re.sub(r"\s+", " ", out).strip().strip("«»\"“”")
    if len(out) < 12 or out.lower().startswith(("ии недоступен", "не могу", "извините", "к сожалению")):
        return fallback_text(who, gist, formal), False
    return out[:700], True


# ====================================================================== voice rules
def register(R, core):
    VERB = r"(?:напиши|написать|отправь|скинь|черкни|кинь|передай)"
    MSG = rf"^{VERB}\s+(?:в\s+{APP_WORDS}\s+)?(?:сообщение\s+)?([а-яё]+):?\s+(?:в\s+{APP_WORDS}\s+)?(?:что\s+|:\s*)?(.+)$"
    STYLED_FIRST = rf"^({VERB})\s+(?:красиво|вежливо|покрасивее|развёрнуто|развернуто|тепло)\s+(.+)$"

    def handle(m, t, styled=False):
        app_w, who, app_w2, text = m.group(1), m.group(2), m.group(3), m.group(4)
        if who in ("письмо", "заметку", "задачу", "стих", "текст", "код", "в", "на", "мне", "сообщение", "смс", "sms",
                   "эсэмэску", "это", "ссылку", "красиво", "вежливо") or \
                re.match(r"^(?:письмо|на\s+почту)", text):
            return False
        if not is_person_word(core, who):
            return False
        app = app_of(app_w or app_w2) or active_messenger()
        if not app and time.time() - core.ctx.app_at < 60:
            app = next((k for k in APPS if APPS[k]["exe"] and _window_of(k) and S.active_exe() in APPS[k]["exe"]), None)
        app = app or core.cfg.get("messenger.default", "telegram")
        # the text as said (case, punctuation), not the normalized command
        raw = getattr(core.router, "raw", "") or text
        mm = re.search(rf"(?i){who}\s*[:,]?\s+(?:(?:в|во)\s+\w+\s+)?(?:что\s+|:\s*)?(.+)$", raw)
        body = (mm.group(1) if mm else text).strip()
        # a style request: "…что я занят, распиши красиво" -> the gist without the request itself
        trimmed = STYLE_TAIL.sub("", body).strip(" ,.")
        styled = styled or (trimmed != body.strip(" ,.") and bool(STYLE_WORD.search(body)))
        if styled and trimmed:
            body = trimmed
        body = body[:1].upper() + body[1:]
        name = contact_name(core, who)

        def go():
            try:
                if styled:
                    core.say("Секунду, составлю текст.")
                    final, by_ai = compose(core, name, who, body, APPS[app]["title"])
                    if not by_ai:
                        core.log("Сообщение составлено по шаблону (ИИ недоступен)", "info")
                    send(core, app, name, final, composed=True)
                else:
                    send(core, app, name, body)
            except Exception as e:
                core.log(f"Мессенджер: {e}", "error")
                core.say(f"Не получилось: {e}")
        threading.Thread(target=go, daemon=True).start()
        return True

    @R(STYLED_FIRST)
    def _msg_styled_first(m, t):
        t2 = f"{m.group(1)} {m.group(2)}"
        mm = re.match(MSG, t2)
        return handle(mm, t2, styled=True) if mm else False

    @R(MSG)
    def _msg(m, t):
        return handle(m, t)
