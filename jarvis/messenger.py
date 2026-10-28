"""Messages in desktop messengers: "напиши Ване привет", "открой телеграм и напиши Ване привет",
"скинь в дискорд Пете что я опаздываю".

Jarvis brings the messenger to the front (starting it if needed), opens the chat through the app's own search
(Telegram: the search field, Discord: Ctrl+K, WhatsApp: Ctrl+F), types the text and — unless turned off in Settings —
asks "Отправить?" before pressing Enter, so a wrong chat never gets the message."""
import re
import threading
import time

from . import system as S

APPS = {
    "telegram": {"exe": ("telegram.exe", "ayugram.exe", "kotatogram.exe", "64gram.exe"), "launch": "telegram", "title": "Telegram"},
    "discord": {"exe": ("discord.exe", "vesktop.exe", "willicord.exe"), "launch": "discord", "title": "Discord"},
    "whatsapp": {"exe": ("whatsapp.exe", "whatsapp.root.exe"), "launch": "whatsapp", "title": "WhatsApp"},
}
APP_WORDS = r"(телеграм\w*|телеграмм\w*|тг|телег\w*|дискорд\w*|дс|ватсап\w*|вотсап\w*|whatsapp|telegram|discord)"


def app_of(word):
    w = (word or "").lower()
    if re.match(r"тел|тг|telegram", w):
        return "telegram"
    if re.match(r"дис|дс|discord", w):
        return "discord"
    if re.match(r"ват|вот|whatsapp", w):
        return "whatsapp"
    return None


def active_messenger():
    exe = S.active_exe()
    return next((k for k, v in APPS.items() if exe in v["exe"]), None)


def _window_of(app):
    for w in S.list_windows():
        if (w["proc"] or "").lower() in APPS[app]["exe"]:
            return w["hwnd"]
    return None


def bring(core, app):
    """Front window of the messenger; starts it when it is not running. Returns the hwnd or None."""
    h = _window_of(app)
    if not h:
        S.launch_app(APPS[app]["launch"], core.apps, core.cfg.get("aliases"))
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


def open_chat(core, app, who):
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


def send(core, app, who, text):
    open_chat(core, app, who)
    S.type_text(text)
    title = APPS[app]["title"]
    core.journal.record(f"набрал сообщение для {who} в {title}", None, "message")
    if core.cfg.get("messenger.confirm", True):
        core.ask_confirm(lambda: (S.hotkey("enter"), core.say("Отправлено.")),
                         f"Сообщение для {who} набрано в {title}. Отправить?")
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


# ====================================================================== voice rules
def register(R, core):
    VERB = r"(?:напиши|написать|отправь|скинь|черкни|кинь|передай)"

    @R(rf"^{VERB}\s+(?:в\s+{APP_WORDS}\s+)?(?:сообщение\s+)?([а-яё]+):?\s+(?:в\s+{APP_WORDS}\s+)?(?:что\s+|:\s*)?(.+)$")
    def _msg(m, t):
        app_w, who, app_w2, text = m.group(1), m.group(2), m.group(3), m.group(4)
        if who in ("письмо", "заметку", "задачу", "стих", "текст", "код", "в", "на", "мне", "сообщение", "смс", "sms",
                   "эсэмэску", "это", "ссылку") or \
                re.match(r"^(?:письмо|на\s+почту)", text):
            return False
        if not is_person_word(core, who):
            return False
        app = app_of(app_w or app_w2) or active_messenger()
        if not app and time.time() - core.ctx.app_at < 60:
            app = next((k for k in APPS if _window_of(k) and S.active_exe() in APPS[k]["exe"]), None)
        app = app or core.cfg.get("messenger.default", "telegram")
        # the text as said (case, punctuation), not the normalized command
        raw = getattr(core.router, "raw", "") or text
        mm = re.search(rf"(?i){who}\s*[:,]?\s+(?:в\s+\w+\s+)?(?:что\s+|:\s*)?(.+)$", raw)
        body = (mm.group(1) if mm else text).strip()
        body = body[:1].upper() + body[1:]
        name = contact_name(core, who)

        def go():
            try:
                send(core, app, name, body)
            except Exception as e:
                core.log(f"Мессенджер: {e}", "error")
                core.say(f"Не получилось: {e}")
        threading.Thread(target=go, daemon=True).start()
        return True
