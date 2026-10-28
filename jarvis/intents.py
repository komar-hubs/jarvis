"""Fast rule-based intent router (no LLM needed). Anything not matched goes to the AI chat."""
import datetime as dt
import os
import random
import re
import threading
import time

from . import info
from . import nlu
from . import system as S
from .updater import version_human
from .planner import parse_duration, parse_when, plural, say_duration, say_when, words_to_digits

POLITE = r"\b(пожалуйста|будь добр\w*|будь любезен|можешь|могу ли я|ты можешь|не мог бы ты|сможешь|давай|ну|эй|слушай|окей|ок)\b"


def normalize(text, wake_words=()):
    t = text.lower().replace("ё", "е")
    t = re.sub(r"[«»\"!?…]", " ", t)
    # dots go only at the end of a word: "igor@firm.ru", "habr.com" stay whole (they were cut into "igor@firm ru")
    t = re.sub(r"(?<!\d)[,;](?!\d)|[,;](?=\s|$)|(?<!\d)\.(?=\s|$)|\.(?=\.)", " ", t)
    for w in sorted(wake_words, key=len, reverse=True):
        t = re.sub(rf"\b{re.escape(w)}\w{{0,2}}\b", " ", t)
    t = re.sub(POLITE, " ", t)
    # "а" only as a lead-in ("а сколько времени"): in the middle it means "but" — "не котиков, а собачек"
    t = re.sub(r"^\s*а\s+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


LEAD = {"эй", "ну", "слушай", "окей", "ок", "о", "а", "хей", "привет", "hey", "ok", "э", "эм", "ээ", "м", "мм",
        "так", "и", "да", "вот", "ой", "алло", "слышь", "короче"}
CMD_START = re.compile(r"(?:открой|закрой|включи|выключи|поставь|найди|загугли|погугли|сделай|покажи|нажми|кликни|"
                       r"напиши|напечатай|скажи|запусти|останови|переключи|убавь|прибавь|громче|тише|стоп|запомни|"
                       r"напомни|добавь|какая|какой|какие|сколько|что|как|где|когда|кто|прочитай|посмотри|сверни|"
                       r"разверни|перейди|вруби|выруби|давай|расскажи|объясни|переведи|засеки)$")


WAKE_CANON = ("джарвис", "jarvis")


def _wakeish(tok, wake_words):
    if any(tok.startswith(w) and len(tok) <= len(w) + 3 for w in wake_words):
        return True
    if len(tok) < 5:
        return False
    # speech recognisers mangle the name in many ways: "гарвис", "джавис", "харвис", "джарвас"...
    from rapidfuzz import fuzz
    # "джарвюз" scored 71: long tokens get a slightly lower bar
    return max(fuzz.ratio(tok[:9], w) for w in WAKE_CANON) >= (70 if len(tok) >= 6 else 75)


def wake_split(text, wake_words):
    """(True, rest of the phrase) if the phrase starts with the wake word (fillers like 'эй', 'слушай' allowed
    before it, fuzzy spelling and a name split in two, e.g. 'жар вис', accepted), else (False, text)."""
    low = text.lower().replace("ё", "е")
    toks = list(re.finditer(r"\w+", low))
    i = 0
    while i < len(toks) and toks[i].group() in LEAD:
        i += 1
    if i >= len(toks):
        return False, text
    first = toks[i].group()
    end = None
    if _wakeish(first, wake_words):
        end = toks[i].end()
    elif i + 1 < len(toks) and len(first) <= 4:
        from rapidfuzz import fuzz
        joined = first + toks[i + 1].group()
        if any(joined.startswith(w) and len(joined) <= len(w) + 2 for w in wake_words) or \
                max(fuzz.ratio(joined[:9], w) for w in WAKE_CANON) >= 82:
            end = toks[i + 1].end()
    if end is None:
        # the recogniser sometimes glues a stray word in front ("так джарвис открой", "вот джарвис включи"):
        # accept the name in the first few words only when a command follows it
        for j in range(i + 1, min(len(toks) - 1, i + 3)):
            if _wakeish(toks[j].group(), wake_words) and CMD_START.match(toks[j + 1].group()):
                end = toks[j].end()
                break
    if end is None:
        return False, text
    return True, text[end:].lstrip(" ,.!?:;—-")


def mouse_fg():
    try:
        import win32gui
        return win32gui.GetForegroundWindow()
    except Exception:
        return 0


def wait_new_window(before, timeout=4.0):
    """After "открой X" in a chain: wait until a new window comes to the front (or the timeout), then a beat
    more for it to accept input. A fixed 1.6 s pause typed into the wrong window when the app was slow."""
    t0 = time.time()
    time.sleep(0.4)
    while time.time() - t0 < timeout:
        h = mouse_fg()
        if h and h != before:
            time.sleep(0.6)
            return True
        time.sleep(0.15)
    return False


def wake_at_start(text, wake_words):
    return wake_split(text, wake_words)[0]


def has_wake(text, wake_words):
    low = text.lower().replace("ё", "е")
    return any(re.search(rf"\b{re.escape(w)}", low) for w in wake_words)


class Router:
    def __init__(self, core):
        self.core = core
        self.rules = []
        self._build()

    def rule(self, pattern, generic=False):
        """generic rules (open X, close X, search X...) are tried after all specific ones."""
        def deco(fn):
            self.rules.append((re.compile(pattern), fn, generic))
            self.rules.sort(key=lambda r: r[2])  # stable: specific first, registration order kept
            return fn
        return deco

    # ================================================================== several commands in one phrase
    def _strip_wake(self, raw):
        wake = self.core.cfg.get("stt.wake_words", [])
        return re.sub(r"(?i)^\W*(?:" + "|".join(map(re.escape, wake)) + r")\w*[\s,.!:—-]*", "", raw.strip()) if wake else raw

    def handle_chain(self, raw):
        """True: done here (rule steps, AI steps run in order inside); False: one step the rules do not know."""
        core = self.core
        if core.pending or isinstance(getattr(core, "capture", None), tuple):
            return self.handle(raw)
        body = self._strip_wake(raw)
        # "через 10 минут выключи звук", "в 18:30 открой почту": a scheduled command, not now
        try:
            d = nlu.delayed(words_to_digits(normalize(body)))
        except Exception:
            d = None
        if d:
            when, cmd = d
            core.planner.add_reminder("Команда: " + cmd, when, command=cmd)
            core.say(f"Сделаю {say_when(when)}: {cmd}.")
            return True
        parts = nlu.split_chain(body)
        if len(parts) == 1:
            return self.handle(raw)
        core.log(f"Цепочка из {len(parts)} шагов: " + " → ".join(parts))
        core.emit("chain", steps=parts, at=0)
        gen = core.cancel_gen
        done = []
        for i, p in enumerate(parts):
            if gen != core.cancel_gen:
                core.log("Цепочка остановлена")
                break
            core.emit("chain", steps=parts, at=i + 1)
            fg_before = mouse_fg()
            if self.handle(p, chain=True):
                done.append(p)
                # an app or page that was just opened needs a moment before the next step types or clicks into it
                if re.match(r"^(?:открой|запусти|включи)\b", normalize(p)):
                    wait_new_window(fg_before, 4.0)
                else:
                    time.sleep(0.35)
                continue
            # a step the rules do not know: the AI does exactly this step, now, before the next one
            note = (f"[Это шаг {i + 1} из {len(parts)} составной команды «{body}». "
                    + (f"Уже выполнено: {'; '.join(done)}. " if done else "")
                    + f"Сейчас выполни ТОЛЬКО этот шаг: «{p}». Остальные шаги не делай — их сделаю я.]")
            core.ai_step(p, note)
            done.append(p)
            time.sleep(0.3)
        core.emit("chain", steps=parts, at=len(parts) + 1)
        return True

    def handle(self, raw, chain=False):
        """Returns True if handled here (reply already spoken), False -> send to AI."""
        core = self.core
        cap = getattr(core, "capture", None)
        if isinstance(cap, tuple):
            core.capture = None
            low = raw.lower()
            if re.search(r"под\s+\w+\s+(?:я\s+)?(?:имею|имел|имела|подразумева)|я\s+имел\w*\s+в\s+виду|^\W*(?:джарвис\W*)?(?:нет\W+)?не\s+\w+\W+а\s+|"
                         r"^\W*(?:джарвис\W*)?(?:стоп|отмена|отмени|не\s+надо|хватит)\W*$", low):
                cap = None  # a correction or "отмена" while I wait for text: it is a command, not the text
            if cap and time.time() < cap[1]:  # "создай заметку" -> "Слушаю" -> this phrase is the note itself
                text = re.sub(r"(?i)^\W*(?:" + "|".join(map(re.escape, core.cfg.get("stt.wake_words", []))) + r")\w*\W*", "", raw).strip()
                if text:
                    cap[0](text)
                    return True
        t = normalize(raw, core.cfg.get("stt.wake_words", []))
        if not t:
            core.say(info.ack())
            core.listen_next()
            return True
        t = words_to_digits(t)
        try:
            t = nlu.canonical(t)  # "можешь открыть", "хочу посмотреть", "открой-ка" -> plain commands
        except Exception as e:
            core.log(f"Понимание фразы: {e}", "warn")
        self.chain = chain
        self.raw = raw
        g = core.journal.begin(raw)
        try:
            return self._dispatch(raw, t)
        finally:
            core.journal.end(g)

    def _dispatch(self, raw, t):
        core = self.core
        # "скажи сколько времени" / "подскажи какая погода": the question itself is the command
        t = re.sub(r"^(?:скажи|подскажи|расскажи)(?:\s+мне)?\s+(?=(?:сколько|какой|какая|какое|какие|который|что|когда|где|кто)\b)", "", t)

        # pending confirmation for dangerous actions ("нет, под шефом я имею в виду Антона" is a correction, not "нет")
        correction = re.search(r"под\s+\w+\s+(?:я\s+)?(?:имею|имел|имела|подразумева)|имел\w*\s+в\s+виду|^(?:нет\s+)?не\s+\S+(?:\s+\S+)?\s+а\s+", t)
        if core.pending and not correction:
            if re.match(r"^(да|подтверждаю|подтверждено|выполняй|точно|конечно|давай|ага|верно)\b", t):
                fn, _ = core.pending
                core.pending = None
                fn()
                return True
            if re.match(r"^(нет|отмена|не надо|отбой|стой)\b", t):
                core.pending = None
                core.say("Отменено, сэр.")
                return True

        # user command blocks have priority
        cmd, vars_ = core.commands.match(t)
        if cmd:
            if vars_.get("query") and re.search(r"https?://[^\"]*\{query\}", str(cmd.get("actions"))):
                # a search protocol ("найди на ютубе {query}"): search for "котики", not "котиков"
                site = nlu.site_from_url(re.search(r"https?://[^\"']*\{query\}", str(cmd.get("actions"))).group(0))
                vars_ = dict(vars_, query_raw=vars_["query"], query=nlu.clean_query(vars_["query"], site))
            core.log(f"Команда: {cmd['name']}")
            core.run_command(cmd, vars_)
            return True
        src, _ = core.commands.match(t, "source")
        if src:
            core.run_source(src)
            return True
        # the user's own plugins (data/plugins) come right after their protocols
        item, args = core.plugins.match(t)
        if item:
            core.log(f"Плагин: {item['name']}")
            core.plugins.run_async(item, t, args)
            return True

        for rx, fn, _ in self.rules:
            m = rx.search(t)
            if m:
                res = fn(m, t)
                if res is False:
                    continue
                if isinstance(res, str):
                    core.say(res)
                return True
        return False

    # ================================================================== undo and corrections
    def _build_journal(self, R):
        core = self.core
        J = core.journal

        def redo_with(fixed, g):
            """Undo the old request quietly and run the corrected one."""
            J.undo_id(g["id"]) if any(i["undo"] for i in g["items"]) else None
            core.log(f"Исправляю: «{g['text']}» → «{fixed}»")
            core.say(random.choice(["Исправляю.", "Понял, исправляю.", "Секунду, переделаю."]))
            core.after_speech(lambda: core.dialog_q.put(("voice", fixed, "voice")), 0.05)
            return True

        @R(r"^(?:отмени|отменить|откати|верни)\s+(?:это|последнее|последнее\s+действие|что\s+(?:ты\s+)?(?:сделал|натворил)|"
           r"(?:твое|своё|свое)\s+действие|как\s+было)$|^отмени$|^(?:не\s+надо\s+было|зря|не\s+то)$")
        def _undo(m, t):
            g = J.last(max_age=900, undoable=True)
            if not g:
                if t in ("отмени", "отмени это", "отмени последнее", "отменить"):
                    S.hotkey("ctrl+z")  # nothing of mine to undo: the app's own undo
                    return True
                return "Мне нечего отменять, сэр."
            if t == "отмени" and time.time() - g["ts"] > 90:
                S.hotkey("ctrl+z")  # long ago: "отмени" is about the app the user works in
                return True
            return J.undo(1)

        @R(r"^(?:отмени|откати)\s+(?:последние\s+)?(\d+|два|три|четыре|пять)\s+(?:последних\s+)?(?:действи\w*|команд\w*)$"
           r"|^(?:отмени|откати)\s+вс[её](?:\s+что\s+(?:ты\s+)?сделал)?$")
        def _undo_n(m, t):
            n = m.group(1)
            n = {"два": 2, "три": 3, "четыре": 4, "пять": 5}.get(n, int(n) if n and n.isdigit() else 10)
            return J.undo(n)

        @R(r"^(?:что\s+ты\s+(?:сделал|сделал\s+последн\w*|делал)|последние\s+действия|история\s+действий|что\s+было\s+сделано)$")
        def _history(m, t):
            gs = J.recent(4)
            if not gs:
                return "Я пока ничего не делал, сэр."
            core.show_tab("hub")
            return "Последнее: " + "; ".join(J.describe(g, short=True) for g in reversed(gs)) + "."

        @R(r"^(?:повтори|сделай|выполни)\s+(?:последнее\s+действие|последнюю\s+команду|ещ[её]\s+раз|то\s+же\s+самое|это\s+снова)$|^(?:ещ[её]\s+раз\s+то\s+же\s+самое|еще\s+разок)$")
        def _redo(m, t):
            g = J.last(max_age=3600)
            if not g:
                return "Повторять нечего, сэр."
            core.dialog_q.put(("voice", g["text"], "voice"))
            return True

        # "не котиков, а собачек", "не на 5, а на 10 минут", "нет, не ютуб, а рутуб"
        @R(r"^(?:нет\s+)?не\s+(.+?)\s+а\s+(.+)$")
        def _correct(m, t):
            fixed, g = J.correct(m.group(1), m.group(2))
            if not fixed:
                return False  # not a correction of something I did
            return redo_with(fixed, g)

        # "я имел в виду собачек", "я сказал на 10 минут", "исправь на 10 минут"
        @R(r"^(?:нет\s+)?(?:я\s+)?(?:имел\w*\s+в\s+виду|сказал\w*|просил\w*|говорил\w*)\s+(.+)$|^(?:исправь|поменяй|замени|переделай)\s+на\s+(.+)$")
        def _correct2(m, t):
            new = m.group(1) or m.group(2)
            fixed, g = J.correct(None, new)
            if not fixed and g:
                fixed = core.ask_ai_once(
                    f"Пользователь дал голосовому ассистенту команду: «{g['text']}». Потом поправил: «{t}». "
                    "Напиши исправленную команду целиком, одной строкой, в той же форме, без кавычек и пояснений.").strip()
                if not fixed or fixed.startswith("ИИ недоступен") or len(fixed) > 200:
                    fixed = None
            if not fixed:
                return False
            return redo_with(fixed, g)

        # ---- precise edits of timers, alarms and tasks
        @R(r"^(?:измени|поменяй|переставь)\s+(?:последний\s+|этот\s+)?таймер\s+на\s+(.+)$")
        def _timer_edit(m, t):
            secs = parse_duration(m.group(1))
            ts = list(core.planner.timers.items)
            if not secs or not ts:
                return False
            last = ts[-1]
            core.planner.cancel_timer(last["id"])
            core.planner.add_timer(secs, last.get("label") or "")
            return f"Таймер теперь на {say_duration(secs)}."

        @R(r"^(?:добавь|прибавь|накинь)\s+(?:к\s+)?таймер\w*\s+(.+)$|^(?:добавь|прибавь)\s+(.+?)\s+к\s+таймер\w*$")
        def _timer_add_more(m, t):
            secs = parse_duration(m.group(1) or m.group(2))
            ts = list(core.planner.timers.items)
            if not secs or not ts:
                return False
            last = ts[-1]
            left = core.planner.timer_left(last)
            core.planner.cancel_timer(last["id"])
            core.planner.add_timer(int(left + secs), last.get("label") or "")
            return f"Добавил {say_duration(secs)}. Осталось {say_duration(int(left + secs))}."

        @R(r"^(?:измени|поменяй|переставь|перенеси)\s+(?:последний\s+|мой\s+)?будильник\s+на\s+(.+)$")
        def _alarm_edit(m, t):
            when, _ = parse_when(m.group(1)) if not re.match(r"^\d", m.group(1)) else parse_when("в " + m.group(1))
            rs = [r for r in core.planner.reminders.items if r.get("active") and r.get("alarm")]
            if not when or not rs:
                return False
            r = sorted(rs, key=lambda r: r["at"])[0] if len(rs) > 1 else rs[0]
            core.planner.update_reminder(r["id"], at=when.isoformat(timespec="seconds"))
            return f"Будильник теперь {say_when(when)}."

        @R(r"^(?:переименуй|измени|поменяй)\s+(?:последнюю\s+)?задачу\s+(.+?)\s+на\s+(.+)$|^(?:переименуй|измени)\s+(?:последнюю\s+)?задачу\s+на\s+(.+)$")
        def _task_rename(m, t):
            if m.group(3):
                items = [x for x in core.planner.tasks.items if not x.get("done")]
                task = items[-1] if items else None
                new = m.group(3)
            else:
                task, new = core.planner.find_task(m.group(1)), m.group(2)
            if not task:
                return "Не нашёл такую задачу."
            core.planner.update_task(task["id"], text=new[:1].upper() + new[1:])
            return f"Переименовал: {new}."

        @R(r"^(?:перенеси|сдвинь)\s+(?:последнюю\s+)?задачу\s+(.*?)\s*на\s+(.+)$")
        def _task_move(m, t):
            items = [x for x in core.planner.tasks.items if not x.get("done")]
            task = core.planner.find_task(m.group(1)) if m.group(1) else (items[-1] if items else None)
            when, _ = parse_when(m.group(2)) if not re.match(r"^\d", m.group(2)) else parse_when("в " + m.group(2))
            if not task or not when:
                return False
            core.planner.update_task(task["id"], due=when.isoformat(timespec="minutes"))
            return f"Перенёс «{task['text']}» на {say_when(when)}."

    # ================================================================== web search
    def _build_search(self, R):
        core = self.core
        cfg = core.cfg

        def do_search(site, q):
            if site == "google" and cfg.get("search_url"):
                S.search_web(q, cfg.get("search_url"))
            else:
                S.open_target(nlu.search_url(site, q))
                g = core.journal.current()
                if g and g["items"]:
                    g["items"][-1]["data"]["query"] = q
            core.ctx.query = q
            title = nlu.ENGINE.get(site, ("", ""))[0]
            return random.choice([f"Ищу «{q}»" + (f" на {title}." if site not in ("google", "images", "news") else "."),
                                  f"Открываю {title}: {q}." if site not in ("google", "images", "news") else f"Ищу: {q}."])

        # explicit site or content word ("в ютубе", "видео с ...", "картинки ...") — before every other rule
        @R(r"^(?:найди|поищи|ищи|загугли|погугли|поиск|найти|поискать|покажи|посмотри|глянь|включи|поставь|запусти|открой|вбей|забей|отыщи|выведи)\s+.+$")
        def _search_site(m, t):
            hit = nlu.search_intent(t, core.ctx.current_site())
            if not hit or hit[0] == "google":
                return False
            return do_search(*hit)

        # plain "найди X": web search (generic: tried after files, apps, notes, everything precise)
        @R(r"^(?:найди|загугли|погугли|поищи|гугл|google|найти|ищи|найди мне|поищи мне|узнай в интернете|поиск)\s+(.+)$", generic=True)
        def _search(m, t):
            q = m.group(1).strip()
            if q in ("файл", "папку", "окно"):
                return False
            hit = nlu.search_intent(t, core.ctx.current_site())
            site, q = hit if hit else ("google", nlu.clean_query(q))
            return do_search(site, q)

    # ================================================================== rules
    def _build(self):
        core = self.core
        R = self.rule
        cfg = core.cfg
        from . import (automation, background, backup, extras, mail, messenger, mouse, notes, people, persona, phone, plugins,
                       sentinel, skills, teach, watchers)

        def addr():
            return cfg.get("user.address", "сэр") or "сэр"

        people.register(R, core)  # "мой шеф — Антон", "под шефом я имею в виду…": before the generic corrections
        self._build_journal(R)    # undo, corrections, "что ты сделал": before everything, "отмени" must reach them
        self._build_search(R)     # "найди в ютубе котиков", "найди видео с котиками"
        mouse.register(R, core)   # clicks by name, nth link, fields, scrolling to, dragging (jarvis/mouse.py)
        phone.register(R, core)      # "найди телефон", "отправь ссылку на телефон", "смс маме …": before the messengers
        teach.register(R, core)      # "когда я говорю доброе утро, открой почту": a new command by voice
        messenger.register(R, core)  # "напиши Ване привет" in Telegram / Discord / WhatsApp (jarvis/messenger.py)
        plugins.register(R, core)    # "создай плагин …", "мои плагины"
        notes.register(R, core)      # "запиши в заметки …", "прочитай заметку про …" (tab «Заметки»)
        automation.register(R, core)  # "мои автоматизации", "выключи автоматизацию …"
        backup.register(R, core)     # "сделай резервную копию"
        persona.register(R, core)    # "позови Пятницу" / "верни Джарвиса"
        mail.register(R, core)    # e-mail (jarvis/mail.py)
        sentinel.register(R, core)  # "что случилось", autostart, initiative level (jarvis/sentinel.py)
        watchers.register(R, core)  # "скажи, когда выйдет новое видео у …" (jarvis/watchers.py)
        background.register(R, core)  # downloads, cleanup, memory, mail sorting (jarvis/background.py)
        extras.register(R, core)  # focus, habits, conversation mode, PC care (jarvis/extras.py)
        skills.register(R, core)  # precise everyday skills first: they must win over generic "открой/покажи X"

        @R(r"^(?:напиши|напечатай|набери|введи)\s+(.+)$")
        def _write_after_open(m, t):
            # in a chain right after "открой блокнот": the words are the text itself ("открой блокнот и напиши привет").
            # A composition request ("напиши стих", "напиши письмо") is the AI's job.
            if not getattr(self, "chain", False) or time.time() - core.ctx.app_at > 30:
                return False
            if re.match(r"^(?:письмо|стих\w*|сочинени\w*|рассказ\w*|историю|код|программу|скрипт|ответ|поздравлени\w*|"
                        r"текст\s+(?:про|о|об|на)|статью|эссе|пост|отзыв|резюме|заявлени\w*|сообщение\s+(?:о|про))\b", m.group(1)):
                return False
            # type the words as said (case, punctuation), without "в нем", "туда", "в чат" — not the command itself
            text = nlu.text_to_type(getattr(self, "raw", "")) or nlu.text_to_type(t)
            if not text:
                return False
            S.type_text(text)
            return True

        # ---------------- voice control of Jarvis himself
        @R(r"^(стоп|хватит|замолчи|помолчи|тихо|заткнись|достаточно|тишина|все спасибо|отставить)$")
        def _stop(m, t):
            core.stop_all()
            return True

        @R(r"^(спи|засыпай|не слушай|режим сна|отдыхай|перестань слушать)$")
        def _sleep(m, t):
            core.set_sleep(True)
            return f"Перехожу в режим ожидания, {addr()}. Скажите «проснись», когда понадоблюсь."

        @R(r"^(проснись|просыпайся|подъем|включайся|ты тут|ты здесь|ты меня слышишь)$")
        def _wake(m, t):
            core.set_sleep(False)
            return random.choice([f"К вашим услугам, {addr()}.", f"Всегда здесь, {addr()}.", "Я вас слушаю."])

        @R(r"^(повтори|что ты сказал|еще раз)$")
        def _repeat(m, t):
            return core.tts.last_text or "Мне пока нечего повторить."

        @R(r"^(говори|отвечай|включи) (голосом|голос)$|^включи озвучку$")
        def _voice_on(m, t):
            cfg.set("voice.enabled", True)
            return "Голос включён."

        @R(r"^(выключи голос|молча|отвечай текстом|выключи озвучку)$")
        def _voice_off(m, t):
            core.say("Перехожу в текстовый режим.")
            cfg.set("voice.enabled", False)
            return True

        @R(r"^(покажи|открой) (себя|окно джарвиса|интерфейс|панель|свой интерфейс)$|^покажись$")
        def _show(m, t):
            core.show_window()
            return info.done()

        @R(r"^(спрячься|скройся|сверни себя|уйди в трей)$")
        def _hide(m, t):
            core.hide_window()
            return "Ухожу в фон."

        # ---------------- search: jarvis/nlu.py + _build_search (sites, "видео с …", the query in the nominative)

        # ---------------- open / close
        @R(r"^(?:открой|запусти|включи|открыть|запустить|стартуй|загрузи)\s+(?:мне\s+)?(?:папку\s+)?(.+)$", generic=True)
        def _open(m, t):
            q = m.group(1).strip()
            q = re.sub(r"^(программу|приложение|игру|сайт)\s+", "", q)
            if q in S.FOLDERS:
                S.open_target(S.FOLDERS[q])
                return f"Открываю {q}."
            if q in S.SITES:
                S.open_target(S.SITES[q])
                return random.choice(["Открываю.", f"Открываю {q}.", info.done()])
            site = re.sub(r"\s+(?=(?:com|ru|org|net|io|ai|dev|рф|me|tv|gg|app|su|by|ua|kz)$)", ".", q.replace(" точка ", "."))
            if re.match(r"^[\w.-]+\.(com|ru|org|net|io|ai|dev|рф|me|tv|gg|app|su|by|ua|kz)(/\S*)?$", site):
                S.open_target("https://" + site)  # "открой сайт habr com" (normalization drops the dot)
                return "Открываю сайт."
            if q in ("музыку", "видео") and m.group(0).startswith(("включи", "запусти")):
                try:
                    if S.media_control("play").get("ok"):
                        return True  # a paused player resumes; nothing to resume -> the AI picks music
                except Exception:
                    pass
            if q in ("музыку", "видео", "фото", "фотографии", "игру", "кино", "фильм", "мультик"):
                return False  # common nouns: "видео" opened the codec tool "LAV Video"
            ok, name = S.launch_app(q, core.apps, cfg.get("aliases"))
            if ok:
                return random.choice([f"Запускаю {name}.", f"Открываю {name}, {addr()}.", f"{name} запускается."])
            return False  # not an installed app: the AI decides (site, folder, setting...)

        @R(r"^(?:закрой|выключи|заверши|убей|выруби|закрыть)\s+(?:все\s+)?(?:программу\s+|приложение\s+|игру\s+)?(.+)$", generic=True)
        def _close(m, t):
            q = m.group(1).strip()
            force = t.startswith("убей")
            if q in ("вкладку", "эту вкладку", "текущую вкладку"):
                S.hotkey("ctrl+w")
                return True
            if q in ("окно", "это окно", "это", "текущее окно", "активное окно"):
                S.hotkey("alt+f4")
                return info.done()
            if q in ("компьютер", "пк", "систему"):
                return False  # handled by power rule
            if q in ("звук",):
                S.mute(True)
                return "Звук выключен."
            if q in ("таймер", "таймеры", "все таймеры"):
                return False
            if q in ("джарвиса", "себя"):
                core.quit_soon()
                return f"Отключаюсь. До встречи, {addr()}."
            if re.search(r"\b(?:в|во|на|у)\s+\w", q) or re.match(r"^(?:микрофон|камеру|камера|свет|уведомлени|вай|блютуз|интернет)", q):
                return False  # "выключи микрофон в дискорде": an action inside an app, not an app name - the AI handles it
            n = S.close_app(q, core.apps, cfg.get("aliases"), force=force)
            if n:
                return random.choice([f"Закрываю {q}.", info.done(), f"{q} закрыт."])
            return False  # nothing matched: let the AI handle it

        # ---------------- windows
        @R(r"^(?:разверни|верни|восстанови|покажи)\s+(?:все|всё)\s*(?:окна|приложения|программы|обратно)?$")
        def _restore_all(m, t):
            S.hotkey("windows+shift+m")
            return info.done()

        BROWSER_WORDS = r"(?:браузер\w*|гугл\w*|google|хром\w*|chrome|яндекс\w*|edge|эдж\w*|опер\w*|файрфокс\w*|firefox|интернет\w*|ютуб\w*|youtube)"

        @R(r"^(?:закрой|закрыть|убери)\s+(все\s+|всё\s+)?(вкладк\w*)(?:\s+(?:в|во|на|из)\s+" + BROWSER_WORDS + r"|\s+" + BROWSER_WORDS + r")?$"
           r"|^закрой\s+" + BROWSER_WORDS + r"\s+вкладк\w*$")
        def _close_tabs(m, t):
            every = bool(m.group(1)) or (m.group(2) or "").startswith("вкладки") or " все " in f" {t} "
            if not S.focus_browser():
                return "Не вижу открытого браузера, сэр."
            S.hotkey("ctrl+shift+w" if every else "ctrl+w")  # ctrl+shift+w closes the window with all its tabs
            return "Закрываю все вкладки." if every else "Закрываю вкладку."

        # ---------------- video in the browser (YouTube keys)
        @R(r"^(?:(?:открой|включи|сделай|разверни|поставь|перейди в|давай|режим)\s+)?(?:видео\s+)?(?:на\s+)?"
           r"(?:полный экран|весь экран|полноэкранный(?: режим)?|полный режим|полноэкран\w*|фулл?скрин|full screen)"
           r"(?:\s+(?:видео|режим))?(?:\s+(?:в|на)\s+\w+)?$|^разверни видео(?: на весь экран)?$")
        def _fullscreen(m, t):
            if S.active_exe() in S.BROWSERS or S.focus_browser():
                S.hotkey("f")  # YouTube, Twitch, VK video: "f" toggles the player's full screen
            else:
                S.hotkey("f11")
            return True

        @R(r"^(?:выйди|выйти|выход)\s+из\s+(?:полно\w*|полного|весь)\s*(?:экран\w*|режим\w*)?$|^(?:сверни видео|обычный режим|убери полный экран|выключи полный экран)$")
        def _fullscreen_exit(m, t):
            S.hotkey("esc")
            return True

        # ---------------- media control (Windows media sessions: YouTube in a browser, Spotify, players)
        def pos_seconds(s):
            """'1:20' / '1 минута 20 секунд' / 'пятую минуту' / '90 секунд' -> seconds, or None."""
            s = s.strip()
            for word, n in (("первую", 1), ("вторую", 2), ("третью", 3), ("четвертую", 4), ("пятую", 5), ("шестую", 6),
                            ("седьмую", 7), ("восьмую", 8), ("девятую", 9), ("десятую", 10), ("пятнадцатую", 15),
                            ("двадцатую", 20), ("тридцатую", 30), ("сороковую", 40)):
                s = re.sub(rf"\b{word}\b", f"{n}-ю", s)
            m2 = re.search(r"(\d+):(\d{2})(?::(\d{2}))?", s)
            if m2:
                a, b, c = int(m2.group(1)), int(m2.group(2)), m2.group(3)
                return a * 3600 + b * 60 + int(c) if c else a * 60 + b
            m2 = re.search(r"(\d+)\s*-?\s*(?:ю|ую|ая|й)?\s*минут\w*(?:\s+(\d+)\s*сек\w*)?$", s)
            if m2 and not re.search(r"\d+\s*минут\w*\s+\d+\s*минут", s):
                return int(m2.group(1)) * 60 + int(m2.group(2) or 0)  # "на пятую минуту" -> 5:00
            return parse_duration(s) or None

        def media_say(res, done=None):
            if not res.get("ok"):
                return res.get("error", "Плеер не принял команду, сэр.").capitalize() if res.get("error") else False
            return done if done is not None else True

        @R(r"^(?:перемотай|промотай|мотай|отмотай|перемести|прокрути)\s+(?:видео\s+|трек\s+|песню\s+)?(вперед|назад|обратно)?\s*(?:на\s+)?(.*)$")
        def _seek(m, t):
            direction, rest = m.group(1), (m.group(2) or "").strip()
            back = direction in ("назад", "обратно") or t.startswith("отмотай")
            if direction or t.startswith("отмотай") or not rest:
                secs = pos_seconds(rest) if rest else 10
                if not secs:
                    return False
                return media_say(S.media_control("seek", -secs if back else secs))
            target = pos_seconds(rest)  # "перемотай на 1:20" / "на пятую минуту"
            if target is None:
                return False
            return media_say(S.media_control("seek_to", target))

        @R(r"^(?:включи|запусти|поставь|начни)?\s*(?:видео|трек|песню|фильм)?\s*(?:с начала|сначала|заново)$")
        def _media_restart(m, t):
            return media_say(S.media_control("restart"))

        @R(r"^(?:поставь\s+)?(?:на\s+)?(?:паузу|пауза)(?:\s+(?:видео|музыку|трек|фильм))?$"
           r"|^(?:поставь|останови|стоп|выключи)\s+(?:видео|музыку|музыка|трек|фильм|воспроизведение)(?:\s+на паузу)?$")
        def _pause(m, t):
            res = S.media_control("pause")
            if not res.get("ok") and not res.get("error"):
                S.media("play")  # the player refuses explicit pause: the media key toggles
            return True

        @R(r"^(?:продолжи|продолжай|продолжить|воспроизведи|играй|плей|play|сними с паузы|сними паузу|сними видео с паузы)"
           r"(?:\s+(?:видео|музыку|трек|фильм|воспроизведение|играть))?$|^включи\s+(?:обратно|дальше)$")
        def _resume(m, t):
            res = S.media_control("play")
            if not res.get("ok") and not res.get("error"):
                S.media("play")
            return True

        @R(r"^(?:скорость|поставь скорость|сделай скорость|скорость видео)\s+(?:на\s+)?(.+)$|^(ускорь|замедли|быстрее|медленнее)(?:\s+видео)?$")
        def _rate(m, t):
            words = {"полтора": 1.5, "полторы": 1.5, "два": 2, "две": 2, "нормальн": 1, "обычн": 1, "стандарт": 1,
                     "один": 1, "половин": 0.5, "четверть": 1.25}
            if m.group(2):
                cur = S.media_control("info").get("rate") or 1.0
                rate = cur + (0.25 if m.group(2) in ("ускорь", "быстрее") else -0.25)
            else:
                v = m.group(1).replace(",", ".")
                num = re.search(r"\d+(?:\.\d+)?", v)
                rate = float(num.group(0)) if num else next((x for k, x in words.items() if k in v), None)
                if rate is None:
                    return False
            rate = max(0.25, min(3.0, rate))
            return media_say(S.media_control("rate", rate), f"Скорость {str(rate).rstrip('0').rstrip('.')}.")

        @R(r"^(?:сколько (?:осталось|идет|длится)(?: до конца)?|на какой (?:минуте|секунде))\s*(?:видео|трека|песни|фильма|видос\w*)$")
        def _media_pos(m, t):
            st = S.media_control("info")
            if not st.get("duration"):
                return "Длительность неизвестна — похоже, это прямой эфир." if st.get("app") else "Сейчас ничего не играет."
            left = st["duration"] - st["position"]
            return f"Прошло {say_duration(st['position'])} из {say_duration(st['duration'])}, осталось {say_duration(left)}."

        @R(r"^(?:следующее видео|включи следующее видео|некст видео)$")
        def _next_video(m, t):
            res = S.media_control("next")
            if not res.get("ok") and not res.get("error"):
                S.media("next")
            return True

        @R(r"^(?:включи|выключи|покажи|убери)\s+субтитры$")
        def _subs(m, t):
            if S.active_exe() in S.BROWSERS or S.focus_browser():
                S.hotkey("c")
            return True

        @R(r"^(?:сверни|скрой|спрячь|убери)\s+(?:все|всё)\s*(?:окна|приложения|программы)?$|^(?:сверни|скрой)\s+окна$")
        def _min_all(m, t):
            S.hotkey("windows+d")
            return info.done()

        @R(r"^(?:сверни|минимизируй)\s*(.*)$", generic=True)
        def _min(m, t):
            n = S.window_action(m.group(1).strip(), "minimize")
            return info.done() if n else False

        @R(r"^(?:разверни|максимизируй|на весь экран)\s*(.*)$", generic=True)
        def _max(m, t):
            n = S.window_action(m.group(1).strip(), "maximize")
            return info.done() if n else False

        @R(r"^(?:скрой|спрячь)\s+(.+)$", generic=True)
        def _hidewin(m, t):
            n = S.window_action(m.group(1).strip(), "hide")
            return f"Скрыл. Скажите «покажи скрытые окна», чтобы вернуть." if n else "Не нашёл такое окно."

        @R(r"^(?:покажи|верни)\s+(?:скрытые|спрятанные)\s*(?:окна)?$")
        def _showhidden(m, t):
            n = S.show_hidden()
            return f"Вернул окна: {n}." if n else "Скрытых окон нет."

        @R(r"^(?:переключись на|перейди в|перейди на|покажи|активируй|фокус на)\s+(.+)$", generic=True)
        def _focus(m, t):
            q = m.group(1).strip()
            if q.startswith(("задачи", "мои задачи", "погоду", "время", "напоминания", "таймеры", "систему", "скрытые")):
                return False
            n = S.window_action(q, "focus")
            return info.done() if n else False

        @R(r"^(?:какие|что)\s+(?:окна|программы)\s+(?:открыты|запущены)")
        def _wins(m, t):
            ws = S.list_windows()
            names = list(dict.fromkeys(w["proc"].replace(".exe", "") for w in ws if w["proc"]))[:10]
            return f"Открыто окон: {len(ws)}. Среди них: " + ", ".join(names) + "."

        @R(r"^(?:следующая вкладка|переключи вкладку)$")
        def _ntab(m, t):
            S.hotkey("ctrl+tab")
            return True

        @R(r"^(?:новая вкладка|открой новую вкладку)$")
        def _newtab(m, t):
            S.hotkey("ctrl+t")
            return True

        @R(r"^(?:переключи окно|следующее окно|альт таб)$")
        def _alttab(m, t):
            S.hotkey("alt+tab")
            return True

        # ---------------- volume / media / brightness
        @R(r"^(?:громкость|звук|поставь громкость|сделай громкость|установи громкость)\s+(?:на\s+)?(\d+)")
        def _vol(m, t):
            S.set_volume(int(m.group(1)))
            return f"Громкость {m.group(1)} процентов."

        @R(r"^(?:громче|сделай громче|прибавь звук|прибавь громкость|погромче)(?:\s+на\s+(\d+))?")
        def _volup(m, t):
            v = S.change_volume(int(m.group(1) or 10))
            return f"Громкость {v}." if v is not None else True

        @R(r"^(?:тише|сделай тише|убавь звук|убавь громкость|потише)(?:\s+на\s+(\d+))?")
        def _voldown(m, t):
            v = S.change_volume(-int(m.group(1) or 10))
            return f"Громкость {v}." if v is not None else True

        @R(r"^(?:выключи звук|без звука|отключи звук|мьют)$")
        def _mute(m, t):
            S.mute(True)
            return True

        @R(r"^(?:включи звук|верни звук)$")
        def _unmute(m, t):
            S.mute(False)
            return "Звук включён."

        @R(r"^(?:следующий трек|следующая песня|некст|дальше|переключи трек|следующий)$")
        def _next(m, t):
            res = S.media_control("next")
            if not res.get("ok"):
                S.media("next")  # media key as a fallback
            return True

        @R(r"^(?:предыдущий трек|предыдущая песня|назад трек|верни трек|предыдущий|предыдущее видео)$")
        def _prev(m, t):
            res = S.media_control("prev")
            if not res.get("ok"):
                S.media("prev")
            return True

        @R(r"^(?:что (?:сейчас )?(?:играет|звучит|за песня|за трек|за музыка|за видео)|какая (?:это )?песня|какой (?:это )?трек|кто поёт|кто поет)")
        def _now_playing(m, t):
            try:
                np = S.now_playing()
            except Exception:
                np = None
            if not np or not np["title"]:
                return "Сейчас ничего не играет, сэр."
            who = f" — {np['artist']}" if np["artist"] else ""
            state = "Играет" if np["playing"] else "На паузе"
            return f"{state}: {np['title']}{who}."

        # ---------------- editing hotkeys, scrolling, mouse, window snapping
        EDIT_KEYS = {
            r"скопируй(?: это)?|копировать": "ctrl+c", r"вставь(?: это)?|вставить": "ctrl+v", r"вырежи(?: это)?": "ctrl+x",
            r"отмени(?: это| последнее| действие)?|назад действие": "ctrl+z", r"верни(?: обратно| действие)|повтори действие": "ctrl+y",
            r"выдели все|выделить все": "ctrl+a", r"сохрани(?: файл| это)?": "ctrl+s", r"обнови(?: страницу)?|перезагрузи страницу": "f5",
            r"найди на странице": "ctrl+f", r"на весь экран|полноэкранный режим": "f11", r"закрой окно": "alt+f4",
            r"окно (?:влево|налево)|прикрепи (?:окно )?влево": "windows+left", r"окно (?:вправо|направо)|прикрепи (?:окно )?вправо": "windows+right",
            r"окно вверх|разверни окно": "windows+up", r"окно вниз": "windows+down",
            r"скрин(?:шот)? области|вырежи (?:кусок )?экрана|снимок области": "windows+shift+s",
            r"открой эмодзи|смайлики": "windows+.", r"история буфера(?: обмена)?": "windows+v", r"переключи раскладку|смени язык": "alt+shift",
            r"диспетчер задач": "ctrl+shift+esc", r"новое окно": "ctrl+n", r"предыдущая вкладка": "ctrl+shift+tab",
            r"открой закрытую вкладку|верни вкладку": "ctrl+shift+t", r"увеличь(?: масштаб)?|приблизь": "ctrl+=",
            r"уменьши(?: масштаб)?|отдали": "ctrl+-",
        }
        edit_rx = re.compile(r"^(?:" + "|".join(f"(?P<k{i}>{p})" for i, p in enumerate(EDIT_KEYS)) + r")$")
        edit_vals = list(EDIT_KEYS.values())

        @R(edit_rx.pattern)
        def _edit_keys(m, t):
            mm = edit_rx.match(t)
            idx = next(i for i in range(len(edit_vals)) if mm.group(f"k{i}"))
            S.hotkey(edit_vals[idx])
            return True

        @R(r"^(?:листай|прокрути|пролистай|скролль)\s*(вниз|вверх|ниже|выше)?(?:\s+(?:сильно|много|до конца))?$")
        def _scroll(m, t):
            d = "up" if (m.group(1) or "вниз") in ("вверх", "выше") else "down"
            S.scroll(d, 25 if re.search(r"сильно|много|до конца", t) else 6)
            return True

        @R(r"^(?:кликни|клик|нажми мышкой|щелкни)(?:\s+(дважды|два раза|правой))?$")
        def _click(m, t):
            mode = m.group(1) or ""
            S.mouse_click(button="right" if "правой" in mode else "left", double=bool(re.search("дважды|два раза", mode)))
            return True

        # clicks by label, hovering, the cursor: jarvis/mouse.py (registered first)

        @R(r"^(?:сделай|громкость)\s+(.+?)\s+(тише|громче|на (\d+)|(\d+))(?:\s*процент\w*)?$|^(?:приглуши|заглуши)\s+(.+)$")
        def _app_volume(m, t):
            if m.group(5):
                app, level, delta = m.group(5), 0, None
            else:
                app = m.group(1)
                num = m.group(3) or m.group(4)
                level = int(num) if num else None
                delta = None if num else (-25 if m.group(2) == "тише" else 25)
            app = re.sub(r"^(?:громкость|звук)\s+", "", app)
            app = re.sub(r"(а|у|е|ом)$", "", app) if app not in S.ALIASES else app  # "дискорда" -> "дискорд"
            if app in ("", "звук", "систем"):
                return False
            hit = S.app_volume(app, level=level, delta=delta)
            if not hit:
                return False
            return f"Громкость {hit[0][0]}: {hit[0][1]} процентов."

        @R(r"^(?:яркость)\s+(?:на\s+)?(\d+)")
        def _bright(m, t):
            try:
                S.set_brightness(int(m.group(1)))
                return f"Яркость {m.group(1)} процентов."
            except Exception:
                return "Этот монитор не поддерживает управление яркостью."

        # ---------------- timers & stopwatch
        # anchored: router rules use search(), and "отмени таймер на 5 минут" used to start a new timer
        # "поставь / можешь поставить / создай / заведи ... таймер": a phrase the router misses goes to the AI,
        # and the friend's AI set timers by its own means, outside Jarvis (nothing showed up in the app)
        @R(r"^(?!(?:отмени|удали|убери|выключи|сбрось|останови|стоп|закрой|отключи|сотри|покажи|какие|мои|сколько)\b)(?:(?:поставь|поставить|постав|заведи|завести|запусти|включи|установи|установить|засеки|сделай|сделать|создай|создать|добавь|добавить|сделаешь|можешь|мне|пожалуйста|еще|ещё|один|одно|новый|новое)\s+){0,3}таймер\s+(?:на\s+)?(.+)$")
        def _timer(m, t):
            rest = m.group(1)
            secs = parse_duration(rest)
            if not secs:
                return False
            label = re.sub(r"\d+\s*(сек\w*|мин\w*|час\w*)", "", rest).strip()
            label = re.sub(r"^(и\s+)?(для|на|чтобы)\s+", "", label).strip()
            core.planner.add_timer(secs, label)
            return f"Таймер на {say_duration(secs)} запущен."

        @R(r"^(?:отмени|удали|убери|выключи|сбрось|останови|стоп|закрой|отключи)\s+(все\s+)?таймер\w*(?:\s+(.+))?$")
        def _timer_cancel(m, t):
            ringing = core.alarm_active
            core.stop_alarm()
            core.clear_notice()
            ts = core.planner.find_timers("все" if m.group(1) or not m.group(2) else m.group(2))
            if not ts:
                return "Таймер выключен." if ringing else ("Такого таймера нет." if m.group(2) else "Активных таймеров нет.")
            for x in ts:
                core.planner.cancel_timer(x["id"])
            return f"Отменил {len(ts)} {plural(len(ts), 'таймер', 'таймера', 'таймеров')}." if len(ts) > 1 else "Таймер отменён."

        @R(r"^(?:сколько осталось|сколько времени осталось|сколько на таймере)")
        def _timer_left(m, t):
            ts = core.planner.timers.items
            if not ts:
                return "Активных таймеров нет."
            t0 = min(ts, key=core.planner.timer_left)
            return f"Осталось {say_duration(core.planner.timer_left(t0))}."

        @R(r"^(?:засеки время|запусти секундомер|включи секундомер|секундомер)$")
        def _sw(m, t):
            core.planner.sw_reset()
            core.planner.sw_start()
            return "Секундомер запущен."

        @R(r"^(?:останови секундомер|стоп секундомер|сколько прошло|выключи секундомер)$")
        def _sw_stop(m, t):
            v = core.planner.sw_stop()
            return f"Прошло {say_duration(v)}."

        # ---------------- alarms & reminders
        @R(r"^(?:разбуди меня|(?!(?:отмени|удали|убери|выключи|сбрось|останови|стоп|закрой|отключи|сотри|покажи|какие|мои|сколько)\b)(?:(?:поставь|поставить|постав|заведи|завести|запусти|включи|установи|установить|засеки|сделай|сделать|создай|создать|добавь|добавить|сделаешь|можешь|мне|пожалуйста|еще|ещё|один|одно|новый|новое)\s+){0,3}будильник)\s*(?:на\s+)?(.*)$")
        def _alarm(m, t):
            spec = m.group(1) or ""
            when, rest = parse_when(spec)
            if not when and re.match(r"^\d", spec):  # "будильник на 7 утра" (the rule ate "на"): read it as "в 7 утра"
                when, rest = parse_when("в " + spec)
            if not when:
                return "На какое время поставить будильник?"
            rep = "daily" if re.search(r"каждый день|ежедневно", t) else ("weekdays" if re.search(r"будн", t) else None)
            rest = re.sub(r"\b(?:на|по)\s+будн\w*|\bкаждый день\b|\bежедневно\b", "", rest or "").strip()
            core.planner.add_reminder(rest or "Будильник", when, repeat=rep, alarm=True)
            return f"Будильник установлен на {say_when(when)}."

        @R(r"^(?!(?:отмени|удали|убери|выключи|сбрось|останови|стоп|закрой|отключи|сотри|покажи|какие|мои|сколько)\b)(?:(?:поставь|поставить|постав|заведи|завести|запусти|включи|установи|установить|засеки|сделай|сделать|создай|создать|добавь|добавить|сделаешь|можешь|мне|пожалуйста|еще|ещё|один|одно|новый|новое)\s+){0,3}(?:напомни|напомнить|напоминание|напоминалку)\s*(?:мне\s+)?(.*)$")
        def _remind(m, t):
            body = m.group(1)
            rep = None
            if re.search(r"каждый день|ежедневно", body):
                rep, body = "daily", re.sub(r"каждый день|ежедневно", "", body)
            elif re.search(r"каждый час", body):
                rep, body = "hourly", re.sub(r"каждый час", "", body)
            elif re.search(r"каждую неделю|еженедельно", body):
                rep, body = "weekly", re.sub(r"каждую неделю|еженедельно", "", body)
            when, rest = parse_when(body)
            rest = re.sub(r"^(о том что|о том|что|про|чтобы|о)\s+", "", rest).strip()
            if not when:
                return "Когда напомнить? Например: напомни через 10 минут проверить духовку."
            core.planner.add_reminder(rest or "Напоминание", when, repeat=rep)
            return f"Напомню {say_when(when)}: {rest}." if rest else f"Напомню {say_when(when)}."

        @R(r"^(?:какие|мои|покажи)\s+(?:у меня\s+)?(?:напоминания|будильники)")
        def _list_rem(m, t):
            rs = [r for r in core.planner.reminders.items if r.get("active")]
            if not rs:
                return "Активных напоминаний нет."
            rs.sort(key=lambda r: r["at"])
            parts = [f"{say_when(dt.datetime.fromisoformat(r['at']))} — {r['text']}" for r in rs[:5]]
            return f"У вас {len(rs)} {plural(len(rs), 'напоминание', 'напоминания', 'напоминаний')}: " + "; ".join(parts) + "."

        @R(r"^(?:отмени|удали|убери|сотри|выключи|отключи|закрой|сбрось)\s+(все\s+)?(напоминани\w*|будильник\w*)(?:\s+(.+))?$")
        def _del_rem(m, t):
            alarm = m.group(2).startswith("будильник")
            spec = (m.group(3) or "").strip()
            if alarm and core.alarm_active and not spec and not m.group(1):
                core.stop_alarm()  # "выключи будильник" while it rings
                core.clear_notice()
                return "Будильник выключен. Доброе утро."
            plural_word = m.group(2) in ("напоминания", "будильники")
            rs = core.planner.find_reminders("все" if (m.group(1) or (plural_word and not spec)) else spec, alarm)
            if not spec and not m.group(1) and not plural_word and len(rs) > 1:
                names = "; ".join(f"{r['at'][11:16]} — {r['text']}" for r in rs[:3])
                return (f"У вас {len(rs)}: {names}. Какое удалить? Скажите, например: "
                        f"«удали {'будильник на' if alarm else 'напоминание про'} …» или «удали все».")
            if not rs:
                return "Не нашёл такого, сэр." if spec else ("Будильников нет." if alarm else "Напоминаний нет.")
            for r in rs:
                core.planner.delete_reminder(r["id"])
            core.stop_alarm()
            word = (plural(len(rs), "будильник", "будильника", "будильников") if alarm
                    else plural(len(rs), "напоминание", "напоминания", "напоминаний"))
            return f"Удалил {len(rs)} {word}." if len(rs) > 1 else ("Будильник удалён." if alarm else "Напоминание удалено.")

        @R(r"^(?:отложи|отложить|напомни позже|позже)\s*(?:на\s+)?(.*)$")
        def _snooze(m, t):
            secs = parse_duration(m.group(1)) if m.group(1) else 0
            return core.snooze(max(1, (secs or 600) // 60))

        # ---------------- tasks
        @R(r"^(?:добавь|запиши|создай|новая)\s+(?:в\s+)?(?:задачу|задача|дело|в список дел|в планер|в задачи|туду)\s*(.+)$")
        def _task_add(m, t):
            text = m.group(1)
            prio = 3 if re.search(r"\b(срочно|важно|важная|срочная)\b", text) else 2
            text = re.sub(r"\b(срочно|важно|важная|срочная)\b", "", text)
            when, rest = parse_when(text)
            rest = re.sub(r"^(что|чтобы|:)\s*", "", rest).strip()
            if not rest:
                return "Какую задачу добавить?"
            core.planner.add_task(rest[0].upper() + rest[1:], when, priority=prio)
            return f"Задача добавлена: {rest}" + (f", {say_when(when)}." if when else ".")

        @R(r"^(?:какие|мои|покажи|прочитай|список|что у меня по)\s*(?:у меня\s+)?(?:задачи|дела|планы|задач|делам)\s*(на сегодня|на завтра|сегодня|завтра)?")
        def _task_list(m, t):
            when = m.group(1) or ""
            if "завтра" in when:
                items = core.planner.tasks_for_day(dt.date.today() + dt.timedelta(days=1))
                lbl = "на завтра"
            elif "сегодня" in when:
                items = core.planner.tasks_for_day()
                lbl = "на сегодня"
            else:
                items = core.planner.sorted_tasks(True)
                lbl = ""
            core.show_tab("tasks")
            if not items:
                return f"Задач {lbl} нет. Свободны, как ветер, {addr()}.".replace("  ", " ")
            parts = []
            for i, x in enumerate(items[:6], 1):
                d = f" ({say_when(dt.datetime.fromisoformat(x['due']))})" if x.get("due") and len(x["due"]) >= 16 else ""
                parts.append(f"{i}: {x['text']}{d}")
            n = len(items)
            return f"{n} {plural(n, 'задача', 'задачи', 'задач')} {lbl}. " + ". ".join(parts) + "."

        @R(r"^(?:выполни|отметь|закрой|заверши|сделал|сделано|выполнил|выполнена)\s+(?:задачу\s+)?(.+?)(?:\s+(?:как )?выполненн\w+)?$")
        def _task_done(m, t):
            if "задач" not in t and not t.startswith(("сделал", "выполнил", "отметь")):
                return False
            task = core.planner.find_task(m.group(1))
            if not task:
                return "Не нашёл такую задачу."
            core.planner.update_task(task["id"], done=True)
            return random.choice([f"Отлично. «{task['text']}» выполнено.", f"Вычеркнул: {task['text']}.", "Отмечено, сэр."])

        @R(r"^(?:удали)\s+задачу\s+(.+)$")
        def _task_del(m, t):
            task = core.planner.find_task(m.group(1))
            if not task:
                return "Не нашёл такую задачу."
            core.planner.delete_task(task["id"])
            return f"Задача «{task['text']}» удалена."

        # ---------------- notes / memory
        @R(r"^(?:запомни|запиши в память|запомни что)\s+(?:что\s+)?(.+)$")
        def _remember(m, t):
            notes = cfg.get("notes", [])
            notes.append({"text": m.group(1), "at": dt.datetime.now().isoformat(timespec="minutes")})
            cfg.set("notes", notes[-200:])
            return "Запомнил."

        @R(r"^(?:что ты (?:помнишь|запомнил)|что я (?:просил|говорил) запомнить|твоя память)")
        def _recall(m, t):
            notes = cfg.get("notes", [])
            if not notes:
                return "В памяти пока пусто."
            return "Я помню: " + "; ".join(n["text"] for n in notes[-6:]) + "."

        @R(r"^(?:забудь все|очисти память)$")
        def _forget(m, t):
            cfg.set("notes", [])
            return "Память очищена. Кто я? Шучу, сэр."

        # ---------------- info
        @R(r"^(?:сколько времени|который час|сколько сейчас времени|время|какое время|скажи время)$")
        def _time(m, t):
            n = dt.datetime.now()
            return f"Сейчас {n.hour} {plural(n.hour, 'час', 'часа', 'часов')} {n.minute} {plural(n.minute, 'минута', 'минуты', 'минут')}."

        @R(r"^(?:какое сегодня число|какой сегодня день|какая сегодня дата|какое число|дата|какой день недели)")
        def _date(m, t):
            n = dt.date.today()
            wd = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"][n.weekday()]
            from .planner import MONTHS
            return f"Сегодня {wd}, {n.day} {MONTHS[n.month - 1]} {n.year} года."

        @R(r"^(?!открой|найди|загугли)(?:.{0,25}\s)?погод\w*|^(?:холодно|тепло|жарко) ли|^нужен ли зонт|^(?:сколько|какая) (?:градусов|температура) на улице")
        def _weather(m, t):
            cm = re.search(r"\b(?:в|во)\s+(?:городе\s+)?([а-яa-z\- ]+?)(?:\s+(?:завтра|сегодня|сейчас))?$", t)
            city_q = cm.group(1) if cm and cm.group(1) not in ("завтра", "сегодня") else None
            tomorrow = "завтра" in t
            loc = cfg.get("location")
            if city_q:
                g = info.geocode(city_q)
                if not g:
                    return f"Не нашёл город {city_q}."
                loc = g
            w = info.weather(loc["lat"], loc["lon"])
            core.emit("weather", **w, city=loc["city"])
            return info.weather_phrase(loc["city"], w, tomorrow)

        @R(r"(?:курс|сколько стоит)\s+(доллар\w*|евро|юан\w*|фунт\w*|валют\w*)")
        def _cur(m, t):
            c = info.currency()
            w = m.group(1)
            if w.startswith("доллар"):
                return f"Доллар по ЦБ — {c['USD']} рубля."
            if w.startswith("евро"):
                return f"Евро — {c['EUR']} рубля."
            if w.startswith("юан"):
                return f"Юань — {c['CNY']} рубля."
            if w.startswith("фунт"):
                return f"Фунт стерлингов — {c['GBP']} рубля. Почти как дома, сэр."
            return f"Доллар {c['USD']}, евро {c['EUR']}, юань {c['CNY']} рубля."

        @R(r"^(?:статус системы|состояние системы|как (?:там )?(?:компьютер|система|пк)|загрузка (?:системы|процессора|пк)|диагностика|отчет о системе|системный отчет)")
        def _status(m, t):
            s = S.system_stats()
            msg = f"Процессор загружен на {round(s['cpu'])} процентов, память на {round(s['ram'])}."
            if s["gpu"]:
                msg += f" Видеокарта {s['gpu']['util']} процентов, температура {s['gpu']['temp']} градусов."
            msg += f" На диске свободно {s['disk_free']} гигабайт."
            if s["battery"] is not None:
                msg += f" Заряд батареи {s['battery']} процентов."
            if s["cpu"] > 85 or s["ram"] > 90:
                msg += " Система под нагрузкой, сэр."
            else:
                msg += " Все системы в норме."
            return msg

        @R(r"^(?:что (?:ест|жрет|занимает) (?:память|оперативку)|какие процессы)")
        def _procs(m, t):
            ps = S.top_processes(4)
            return "Больше всего памяти занимают: " + ", ".join(f"{p['name'].replace('.exe', '')} — {p['mb']} мегабайт" for p in ps) + "."

        @R(r"^(?:какой у меня|мой)\s+(?:внешний\s+)?(?:ip|ай пи|айпи)")
        def _ip(m, t):
            return "Ваш внешний IP: " + " ".join(info.public_ip()) + "."

        @R(r"^(?:посчитай|сколько будет|вычисли)\s+(.+)$")
        def _calc(m, t):
            expr = m.group(1).replace("плюс", "+").replace("минус", "-").replace("умножить на", "*").replace("разделить на", "/") \
                .replace("на", "*").replace("х", "*").replace("x", "*").replace("в степени", "**").replace(",", ".")
            if not re.fullmatch(r"[\d\s+\-*/().]+", expr):
                return False
            try:
                v = eval(expr, {"__builtins__": {}})
                v = round(v, 6) if isinstance(v, float) else v
                return f"Будет {v}."
            except Exception:
                return False

        @R(r"^(?:брифинг|утренний брифинг|утренний протокол|доброе утро|сводка|что на сегодня|план на день)$")
        def _brief(m, t):
            # plain "брифинг" in the evening gives the evening summary; "утренний ..." always the morning one
            ev = False if re.search(r"утрен|доброе утро|сегодня|на день", t) else None
            threading.Thread(target=core.briefing, kwargs={"evening": ev}, daemon=True).start()
            return True

        @R(r"^(?:вечерняя сводка|вечерний брифинг|итоги дня|что на завтра|план на завтра)$")
        def _brief_evening(m, t):
            threading.Thread(target=core.briefing, kwargs={"evening": True}, daemon=True).start()
            return True

        # ---------------- system / power
        @R(r"^(?:сделай\s+)?(?:скриншот|снимок экрана|скрин)$")
        def _shot(m, t):
            p = S.screenshot()
            core.emit("toast", text=f"Скриншот: {p.name}")
            return "Снимок экрана сохранён."

        @R(r"^(?:заблокируй|блокировка|заблокировать)\s*(?:компьютер|пк|экран)?$")
        def _lock(m, t):
            core.say("Блокирую.")
            S.lock()
            return True

        @R(r"^(?:выключи|выключение|заверши работу)\s*(?:компьютер|пк|систему)$")
        def _shutdown(m, t):
            core.ask_confirm(lambda: (S.shutdown(False, 20), core.say("Выключение через 20 секунд. Скажите «отмена выключения», чтобы остановить.")),
                             "Выключить компьютер? Подтвердите, сэр.")
            return True

        @R(r"^(?:перезагрузи|перезагрузка|рестарт)\s*(?:компьютер|пк|систему)?$")
        def _restart(m, t):
            core.ask_confirm(lambda: (S.shutdown(True, 20), core.say("Перезагрузка через 20 секунд.")),
                             "Перезагрузить компьютер? Подтвердите.")
            return True

        @R(r"^(?:отмена выключения|отмени выключение|отмени перезагрузку|не выключай)$")
        def _abort(m, t):
            S.cancel_shutdown()
            return "Выключение отменено. Продолжаем работу."

        @R(r"^(?:спящий режим|усыпи компьютер|переведи в сон|в сон)$")
        def _sleep_pc(m, t):
            core.ask_confirm(lambda: S.sleep_pc(), "Перевести компьютер в сон?")
            return True

        @R(r"^(?:очисти корзину|почисти корзину)$")
        def _bin(m, t):
            core.ask_confirm(lambda: core.say("Корзина очищена." if S.empty_recycle_bin() else "Корзина уже пуста."),
                             "Очистить корзину без возможности восстановления?")
            return True

        @R(r"^(?:напечатай|введи|набери|впиши)\s+(.+)$")
        def _type(m, t):
            text = nlu.text_to_type(getattr(self, "raw", "")) or nlu.text_to_type(t)
            if not text:
                return False
            core.after_speech(lambda: S.type_text(text))
            return True

        @R(r"^(?:скопируй|в буфер)\s+(.+)$")
        def _copy(m, t):
            S.clipboard_set(m.group(1))
            return "Скопировано."

        @R(r"^(?:что в буфере|прочитай буфер|прочитай что скопировано)")
        def _clip(m, t):
            c = (S.clipboard_get() or "").strip()
            return ("В буфере: " + c[:300]) if c else "Буфер пуст."

        @R(r"^(?:нажми|нажать)\s+(.+)$")
        def _press(m, t):
            try:
                S.hotkey(m.group(1))  # system.hotkey understands spoken Russian key names
                return True
            except ValueError:
                return f"Не знаю такую клавишу: {m.group(1)}."

        # ---------------- self-update
        @R(r"^(?:обновись|обнови себя|обновить себя|установи обновлени\w*|обнови джарвиса|обновление джарвиса)$")
        def _update(m, t):
            core.update_now(speak=True)
            return True

        @R(r"^(?:проверь|есть ли|есть)\s+обновлени\w*$|^какая у тебя версия$|^какая версия\w*$")
        def _update_check(m, t):
            def go():
                info = core.rpc_update_check()
                if info.get("error"):
                    core.say(f"Моя версия {version_human(info['current'])}. Проверить обновления не удалось.")
                elif info.get("available"):
                    core.say(f"Есть обновление, сэр: версия {version_human(info['latest'])}. Скажите «обновись».")
                else:
                    core.say(f"У меня последняя версия, {version_human(info['current'])}.")
            threading.Thread(target=go, daemon=True).start()
            return True

        # ---------------- voice and night mode
        @R(r"^(?:смени|поменяй|измени|поставь|включи|сделай)\s+(?:свой\s+|мне\s+|другой\s+)?голос(?:\s+на)?\s*(.*)$"
           r"|^(?:говори|давай)\s+(?:голосом\s+)?(айдар\w*|евгени\w*|бая|баи|ксени\w*|женским|мужским)(?:\s+голосом)?$")
        def _voice(m, t):
            want = (m.group(1) or m.group(2) or "").strip()
            alias = {"айдар": "aidar", "евгени": "eugene", "женя": "eugene", "бая": "baya", "баи": "baya", "ксени": "kseniya",
                     "ксюш": "kseniya", "женск": "baya", "мужск": "aidar", "aidar": "aidar", "eugene": "eugene"}
            sp = next((v for k, v in alias.items() if want.startswith(k)), None)
            if not sp:  # "смени голос": the next voice in the list
                order = list(core.VOICES)
                cur = cfg.get("voice.silero_speaker", "aidar")
                sp = order[(order.index(cur) + 1) % len(order)] if cur in order else "aidar"
            name = core.set_voice(sp)
            return f"Теперь я говорю голосом «{name}»."

        @R(r"^(?:включи\s+|активируй\s+)?ночн\w+\s+режим\w*$|^(?:режим\s+)?ночь$|^(?:сделай|давай)\s+(?:по)?темнее$|^затемни\w*(?:\s+\w+)?$")
        def _night_on(m, t):
            core.set_night(True)
            return "Ночной режим. Говорю тише, сэр."

        @R(r"^(?:выключи|отключи|убери)\s+ночн\w+\s+режим\w*$|^(?:включи\s+)?дневн\w+\s+режим\w*$|^(?:сделай|давай)\s+(?:по)?светлее$")
        def _night_off(m, t):
            core.set_night(False)
            return "Дневной режим."

        # ---------------- interface themes
        THEME_WORDS = {"марк": "mark3", "красн": "mark3", "золот": "mark3", "железн": "mark3", "нано": "nano",
                       "фиолет": "nano", "пурпур": "nano", "зелен": "emerald", "изумруд": "emerald", "стелс": "stealth",
                       "бел": "stealth", "сер": "stealth", "джарвис": "jarvis", "син": "jarvis", "голуб": "jarvis",
                       "стандарт": "jarvis", "обычн": "jarvis"}

        @R(r"^(?:(?:смени|поменяй|измени|включи|сделай|поставь|переключи|верни|давай)\s+)?(?:\w+\s+){0,2}"
           r"(?:тему|тема|темы|цвет|цвета|интерфейс|оформление|протокол|режим)\b\s*(?:на\s+)?(.*)$")
        def _theme(m, t):
            words = re.findall(r"\w+", t)
            if t.startswith("протокол") and not any(w.startswith(k) for w in words for k in THEME_WORDS):
                return False  # user protocols ("протокол вечеринка") are handled elsewhere
            if re.search(r"\bрежим\b", t) and not any(w.startswith(k) for w in words for k in ("стелс", "марк", "нано", "изумруд")):
                return False  # "ночной режим", "режим сна"... are other commands
            if re.search(r"\bцвет", t) and not re.search(r"\b(тем|интерфейс|оформлени|смени|поменяй|сделай|поставь)", t):
                return False  # "какого цвета небо" is a question
            name = next((v for w in words for k, v in THEME_WORDS.items() if w.startswith(k)
                         and w not in ("сделай", "смени")), None)
            name = core.set_theme(name)
            return {"mark3": "Протокол «Марк Три». Красный и золото, сэр — классика.", "nano": "Нанотехнологии активированы.",
                    "emerald": "Изумрудный режим. Спокойно и сосредоточенно.", "stealth": "Режим «Стелс». Минимум внимания.",
                    "jarvis": "Возвращаю стандартную схему."}[name]

        # ---------------- selected text in any app
        SEL = r"(?:выделенн\w*(?: текст\w*)?|выделение|выбранн\w* текст\w*|это|этот текст)"

        @R(r"^(переведи|исправь|поправь|улучши|сократи|объясни|перескажи|ответь на|сделай (?:деловым|официальным|вежливым))\s+" + SEL + r"(.*)$")
        def _selection(m, t):
            v = m.group(1)
            task = ("translate" if v == "переведи" else "fix" if v in ("исправь", "поправь") else "improve" if v == "улучши"
                    else "shorten" if v == "сократи" else "explain" if v == "объясни" else "summary" if v == "перескажи"
                    else "answer" if v == "ответь на" else "formal")
            threading.Thread(target=core.selection_action, args=(task, m.group(2)), daemon=True).start()
            return True

        # ---------------- vision, dictation, files
        @R(r"^(?:что (?:у меня )?на экране|что (?:ты )?видишь|посмотри (?:на )?(?:экран|сюда|это)|глянь (?:на )?(?:экран|сюда|это)|"
           r"что (?:это )?(?:за|такое) на экране|прочитай (?:что )?(?:на |с )?экран\w*|что (?:тут|здесь) (?:написано|происходит)|"
           r"(?:объясни|переведи) (?:что )?(?:на экране|с экрана))(.*)$")
        def _look(m, t):
            core.look(t)
            return True

        @R(r"^(?:включи |начни |режим )?диктовк\w*$|^(?:записывай|печатай) (?:то )?что я (?:говорю|скажу)$")
        def _dictation(m, t):
            core.say("Диктовка включена. Говорите, я печатаю. Скажите «конец диктовки», чтобы закончить.")
            core.after_speech(lambda: core.dictation(True), 0.1)
            return True

        # "найди видео с котиками" is YouTube and "найди фото котиков" is pictures on the web (nlu): photos and videos
        # are files only when the PC is named ("найди фото с отпуска на компьютере")
        @R(r"^(?:найди|где|отыщи|покажи)\s+(?:мне\s+)?(?:(?:на\s+(?:компьютере|компе|пк|диске)|в\s+проводнике)\s+)?"
           r"(?:файл|папку|документ|архив|(?=(?:фото|фотографию|фотографии|видео|видеозапись)\s+.+\s(?:на\s+(?:компьютере|компе|пк|диске)|в\s+(?:папке|проводнике)|у\s+меня)\b)(?:фото|фотографию|фотографии|видео|видеозапись))\s+(.+?)"
           r"(?:\s+(?:на\s+(?:компьютере|компе|пк|диске)|в\s+(?:папке|проводнике)|у\s+меня))?$")
        def _find_file(m, t):
            core.find_files(m.group(1).strip(" «»\""))
            return True

        # ---------------- agents bridges
        @R(r"^(?:спроси|попроси|поручи|передай|задача для|скажи)\s+(клод\w*|claude|кодекс\w*|codex|антигравит\w*|гемини|gemini|джемини)\s+(?:код\w*\s+|code\s+)?(.+)$")
        def _bridge(m, t):
            who = m.group(1)
            agent = "claude" if who.startswith(("кло", "cla")) else "codex" if who.startswith(("кодекс", "codex")) else \
                "antigravity" if who.startswith("антигр") else "gemini"
            core.bridge_task(agent, m.group(2))
            return True

        # ---------------- small talk / easter eggs
        @R(r"^(?:кто ты|ты кто|представься|как тебя зовут)$")
        def _who(m, t):
            return ("Я — ваш персональный ассистент. Just A Rather Very Intelligent System: "
                    "управляю компьютером, слежу за задачами и иногда шучу.")

        @R(r"^(?:спасибо|благодарю|молодец|отлично|умница|красавчик)$")
        def _thanks(m, t):
            return random.choice([f"Всегда пожалуйста, {addr()}.", "Рад стараться.", "Для вас — что угодно.",
                                  "Это моя работа, сэр. Но спасибо."])

        @R(r"^(?:привет|здравствуй|добрый (?:день|вечер)|хай|приветствую)$")
        def _hello(m, t):
            return core.greeting_phrase()

        @R(r"^(?:как дела|как ты|как поживаешь)$")
        def _how(m, t):
            return random.choice(["Все системы работают в штатном режиме, сэр. А у вас?",
                                  "Превосходно. Ни одного сбоя за сегодня — я впечатлён собой.",
                                  "Лучше, чем у вашего процессора в пиковой нагрузке."])

        @R(r"^(?:я дома|я вернулся|я пришел)$")
        def _home(m, t):
            return f"С возвращением, {addr()}. " + random.choice(["Всё было спокойно.", "Без вас было скучно.", "Чай не приготовлю, но морально поддерживаю."])

        @R(r"^(?:расскажи шутку|пошути|анекдот|рассмеши меня|шутка)$")
        def _joke(m, t):
            return info.joke()

        @R(r"^(?:подбрось монетку|монетка|орел или решка)$")
        def _coin(m, t):
            return random.choice(["Орёл.", "Решка."]) + " Надеюсь, это было не решение о судьбе мира."

        @R(r"^(?:брось кубик|кинь кубик|кубик)$")
        def _dice(m, t):
            return f"Выпало {random.randint(1, 6)}."

        @R(r"^(?:случайное число|загадай число)(?:\s+от\s+(\d+)\s+до\s+(\d+))?$")
        def _rand(m, t):
            a, b = int(m.group(1) or 1), int(m.group(2) or 100)
            return f"{random.randint(min(a, b), max(a, b))}."

        @R(r"^([а-яёa-z0-9][а-яёa-z0-9 .+-]{1,28})$", generic=True)
        def _bare_name(m, t):
            # an incomplete phrase that is only a name: "телеграм", "ютуб", "стим" -> open it
            q = m.group(1).strip()
            if len(q.split()) > 3 or any(nlu.is_imperative(w) for w in q.split()):
                return False
            if q in S.SITES:
                S.open_target(S.SITES[q])
                return "Открываю."
            alias = S.resolve_alias(q, cfg.get("aliases"))
            if alias == q and q not in S.ALIASES.values() and not core.apps.find(q, cfg.get("aliases")):
                return False
            ok, name = S.launch_app(q, core.apps, cfg.get("aliases"))
            return f"Открываю {name}." if ok else False

        @R(r"^(?:протокол|режим)\s+(.+)$", generic=True)
        def _proto(m, t):
            return f"Протокол «{m.group(1)}» не найден, {addr()}. Его можно создать во вкладке «Команды»."

        @R(r"^(?:что ты умеешь|помощь|команды|список команд|справка)$")
        def _help(m, t):
            core.show_tab("help")
            return ("Я умею открывать и закрывать программы, искать в интернете, управлять окнами, звуком и музыкой, "
                    "ставить таймеры, будильники и напоминания, вести задачи, сообщать погоду и состояние системы, "
                    "выполнять ваши протоколы и поручать задачи Claude Code, Codex и Antigravity. Полный список — на экране.")
