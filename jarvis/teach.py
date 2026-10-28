"""Teaching Jarvis new commands by voice.

  "когда я говорю «доброе утро», открой почту и скажи погоду"  -> a command: trigger "доброе утро", one step
                                                                 "Фраза Джарвису" with the rest (chains work)
  "научись новой команде"                                       -> asks the phrase, then what to do
  "добавь к команде доброе утро ещё включи музыку"              -> one more step
  "что ты делаешь, когда я говорю доброе утро" / "забудь команду доброе утро"

The command lands in the Commands tab like any other, so it can be edited there later."""
import re
import threading

from .commands import _norm

def _clean(s):
    return re.sub(r"^[«\"„“'\s]+|[»\"“”'\s.!?]+$", "", s or "").strip()


def register(R, core):
    C = lambda: core.commands

    def find_by_phrase(phrase):
        n = _norm(phrase)
        for c in C().store.items:
            if n in [_norm(t) for t in c.get("triggers", [])] or _norm(c["name"]) == n:
                return c
        return C().get(phrase)

    def describe(c):
        from .commands import ACTION_TYPES
        steps = []
        for a in c.get("actions", []):
            t = a.get("type")
            title = ACTION_TYPES.get(t, {}).get("title", t).split(" (")[0].lower()
            steps.append(a.get("value") if t == "voice" else title + (f" {a.get('value')}" if a.get("value") else ""))
        return "; ".join(s for s in steps if s) or "ничего"

    def save(phrase, todo, replace=None):
        phrase, todo = _clean(phrase).lower(), _clean(todo)
        if len(phrase) < 2 or len(todo) < 2:
            return "Не разобрал, на какую фразу и что делать. Скажите так: «когда я говорю доброе утро, открой почту»."
        cmd = dict(replace) if replace else {"name": phrase[:1].upper() + phrase[1:], "reply": "", "enabled": True}
        cmd["triggers"] = list(dict.fromkeys((replace or {}).get("triggers", []) + [phrase]))
        cmd["actions"] = [{"type": "voice", "value": todo}]
        saved = C().save_command(cmd)
        core.emit("commands")
        core.journal.record(f"новая команда «{phrase}»", lambda: C().delete_command(saved["id"]), "command")
        return f"Запомнил. Теперь на «{phrase}» я сделаю: {todo}. Скажите «отмени», если что-то не так."

    def learn(phrase, todo):
        old = find_by_phrase(phrase)
        if old and old.get("actions"):
            core.ask_confirm(lambda: core.say(save(phrase, todo, old)),
                             f"На «{_clean(phrase)}» уже есть команда: {describe(old)}. Заменить?")
            return True
        return save(phrase, todo)

    def split(rest):
        """Speech comes without commas: "доброе утро открой почту и погоду" -> ("доброе утро", "открой почту и погоду").
        The action starts at "то" / "делай" or at the first imperative verb (pymorphy: открой, включи, скажи)."""
        from .nlu import morph
        toks = rest.split()
        for i, w in enumerate(toks):
            if i and w in ("то", "делай", "сделай", "выполняй", "выполни", "это", "значит", "означает", "ты"):
                j = i + 1 + (toks[i + 1:i + 2] in (["должен"], ["будешь"], ["должна"]))
                return " ".join(toks[:i]), " ".join(toks[j:])
        m = morph()
        for i, w in enumerate(toks):
            if i and m and any("impr" in p.tag for p in m.parse(w)[:2]):
                return " ".join(toks[:i]), " ".join(toks[i:])
        return None, None

    @R(r"^(?:когда|если)\s+я\s+(?:говорю|скажу|произношу|произнесу)\s+(.+)$|^(?:научись(?!\s+новой\s+команде$)|запомни\s+команду)\s+(.+)$"
       r"|^на\s+(?:фразу|слова?)\s+(.+)$")
    def _when(m, t):
        phrase, todo = split(next(g for g in m.groups() if g))
        if not phrase or not todo:
            return "Не понял, где фраза, а где действие. Скажите, например: «когда я говорю доброе утро, открой почту»."
        return learn(phrase, todo)

    @R(r"^(?:научись\s+новой\s+команде|давай\s+научу\s+тебя|новая\s+голосовая\s+команда|создай\s+голосовую\s+команду)$")
    def _dialog(m, t):
        def go():
            phrase = core.chain.ask("На какую фразу?")
            if not phrase:
                return
            todo = core.chain.ask(f"Что делать, когда вы говорите «{_clean(phrase)}»? Можно несколько действий через «и».")
            if not todo:
                return
            r = learn(phrase, todo)
            if r is not True:
                core.say(r)
        threading.Thread(target=go, daemon=True).start()
        return True

    @R(r"^(?:добавь|допиши)\s+(?:к|в)\s+команд\w+\s+(.+?)\s+(?:ещё|еще|шаг)\s+(.+)$")
    def _append(m, t):
        c = find_by_phrase(m.group(1))
        if not c:
            return f"Не нашёл команду «{_clean(m.group(1))}»."
        c = dict(c)
        c["actions"] = list(c.get("actions", [])) + [{"type": "voice", "value": _clean(m.group(2))}]
        C().save_command(c)
        core.emit("commands")
        return f"Добавил в «{c['name']}»: {_clean(m.group(2))}."

    @R(r"^что\s+ты\s+(?:делаешь|сделаешь)\s*,?\s*(?:когда|если)\s+я\s+(?:говорю|скажу)\s+(.+)$")
    def _what(m, t):
        c = find_by_phrase(_clean(m.group(1)))
        if not c:
            return f"На «{_clean(m.group(1))}» своей команды нет."
        return f"Команда «{c['name']}»: {describe(c)}."

    @R(r"^(?:забудь|удали|сотри)\s+(?:команду|протокол)\s+(.+)$")
    def _forget(m, t):
        c = find_by_phrase(_clean(m.group(1)))
        if not c:
            return f"Не нашёл команду «{_clean(m.group(1))}»."

        def go():
            C().delete_command(c["id"])
            core.emit("commands")
            core.say(f"Забыл команду «{c['name']}».")
        core.ask_confirm(go, f"Удалить команду «{c['name']}»?")
        return True
