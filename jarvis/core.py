"""Jarvis core: ties together voice in/out, intent router, AI chat with PC-control tools, planner, commands and bridges."""
import datetime as dt
import json
import os
import queue
import random
import re
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path

from . import info
from . import mouse as MS
from . import nlu
from . import system as S
from .audio import Player
from .bridges import AGENTS, TOOL_MARK, Bridges
from .updater import Updater, current_version
from .commands import ACTION_TYPES, SOURCE_TYPES, ChainRunner, Commands
from .config import DATA, PROVIDERS, Config, JsonStore
from .intents import Router, normalize, wake_split
from .llm import LLMError, list_models, stream_chat
from .planner import MONTHS, Planner, parse_duration, parse_when, plural, say_duration
from .stt import Listener, cuda_available, list_mics, mic_access_problem
from .tts import TTS, REF_DEFAULT, XttsEngine

STOP_WORDS = {"стоп", "хватит", "замолчи", "тихо", "стой", "достаточно", "заткнись", "отмена", "отставить", "выключи"}

TOOLS_DOC = """Инструменты (поле "tool" и аргументы):
- open_app {"name": "telegram"} — запустить программу по названию
- close_app {"name": "chrome"} — закрыть программу
- open {"target": "https://site.ru" или путь к файлу/папке}
- search {"query": "...", "site": "google|youtube|yandex|wiki|images|news|steam|vk|avito|ozon|wb|market|ali|github|
  habr|kinopoisk|maps|ymusic|twitch|rutube|reddit|pinterest|translate"} — поиск на сайте. query — то, что вбили бы
  в строку поиска: без слов «видео», «найди», «на ютубе», в именительном падеже («найди в ютубе котиков» → query
  «котики», site youtube; «найди картинки смешных собак» → «смешные собаки», site images)
- youtube {"query": "..."} — поиск на YouTube (то же, что search с site youtube)
- timer {"duration": "5 минут", "label": "чай"}
- remind {"when": "через 10 минут" | "завтра в 9:00", "text": "..."}
- alarm {"when": "в 7:30"}
- add_task {"text": "...", "when": "завтра в 15:00" (необязательно)}
- tasks {} — получить список задач (данные)
- reminders {} — активные таймеры, напоминания и будильники (данные)
- note {"text": "..."} — сохранить заметку (видна во вкладке «Заметки»)
- shopping {"add": ["молоко", "хлеб"], "remove": ["сыр"], "clear": false} — список покупок
- shopping_list {} — что в списке покупок и заметки (данные)
- cancel_timer {"which": "чай" | "на 5 минут" | "все"} — отменить таймер
- delete_reminder {"which": "про чайник" | "на 7:30" | "все", "alarm": true для будильников} — удалить напоминание/будильник
- snooze {"minutes": 10} — отложить только что сработавшее напоминание или таймер
- theme {"name": "jarvis|mark3|nano|emerald|stealth"} — тема интерфейса (синяя, красно-золотая, фиолетовая, зелёная, белая)
- voice {"speaker": "aidar|eugene|baya|kseniya|xenia"} — сменить свой голос (aidar, eugene мужские; baya, kseniya, xenia женские)
- night {"on": true|false} — ночной режим: тёмный интерфейс и тихий голос
- volume {"level": 0-100} или {"delta": 10}
- media {"action": "play|pause|toggle|stop|next|prev|seek|seek_to|restart|rate", "seconds": 30, "position": "1:20",
  "rate": 1.5, "app": "chrome|spotify|..."} — управление тем, что играет (YouTube и видео в браузере, Spotify,
  Яндекс Музыка, плееры): seek — перемотка на seconds (отрицательное — назад), seek_to — на время position,
  restart — с начала, rate — скорость. app необязателен (по умолчанию то, что играет сейчас)
- media_info {} — что играет: название, приложение, позиция и длительность в секундах, можно ли перематывать (данные)
- window {"action": "minimize|maximize|close|focus|hide", "title": "часть заголовка окна"}
- hotkey {"keys": "ctrl+shift+esc"}
- type {"text": "..."} — напечатать текст в активное окно
Мышь и экран (порядок надёжности: ui_elements + click_element → click_text → vision_click → click по координатам):
- ui_elements {} — кнопки, ссылки, поля, вкладки активного окна и панели задач с номерами id, ролью и координатами
  (данные). Вызови перед кликами, если не знаешь, что на экране
- click_element {"id": 12, "button": "left|right", "double": false} — кликнуть по элементу из ui_elements
- click_text {"text": "надпись или «вторая ссылка» / «кнопка Отправить»", "button": "left|right", "double": false}
- vision_click {"what": "иконка шестерёнки справа вверху"} — найти глазами (для иконок без подписи) и кликнуть
- type_into {"field": "Поиск", "text": "...", "enter": true} — кликнуть в поле по его названию и ввести текст
  (field пустое — в активное поле)
- scroll_to {"text": "Комментарии", "direction": "down|up"} — листать, пока надпись не появится
- hover {"text": "..."} — навести курсор; drag_text {"from": "файл", "to": "Корзина"} — перетащить
- screen_text {} — весь текст экрана (OCR) с координатами (данные)
- click {"x": 100, "y": 200, "button": "left|right|middle", "double": false} — клик по координатам
  (без x, y — там, где сейчас курсор)
- mouse_move {"x": 100, "y": 200} или {"dx": 50, "dy": 0}; drag {"x1": 0, "y1": 0, "x2": 100, "y2": 100}
- scroll {"direction": "down|up|left|right", "amount": 5}; mouse_position {} — курсор и размер экрана (данные)
- wait {"seconds": 1.5} — пауза между действиями (например, пока открывается программа)
Действия выполняются по порядку тегов: можно открыть программу, подождать, кликнуть по надписи и напечатать текст.
Отмена и история:
- undo {"steps": 1} — отменить последние действия Джарвиса (таймеры, напоминания, громкость, открытые вкладки,
  напечатанный текст…); history {} — что Джарвис делал недавно (данные)
- delayed {"when": "через 10 минут" | "в 18:30", "command": "выключи звук"} — выполнить команду позже
Почта (если подключена):
- mail_list {"unread": true, "from": "Иван", "query": "тема", "limit": 5} — письма (данные, у каждого id)
- mail_read {"id": "..."} — текст письма (данные)
- mail_send {"to": "адрес или имя", "subject": "...", "body": "...", "reply": false} — Джарвис покажет черновик и
  спросит подтверждение (reply true — ответ на последнее прочитанное письмо). Сам текст письма вслух не читай
- mail_delete {"id": "..."} — в корзину (с подтверждением)
- events {} — что сторож ПК заметил за сутки (данные)
Люди (кого как называет пользователь):
- contacts {} — люди, их почта и роли (шеф, мама, коллега…) (данные). «Шефу», «маме» — ищи здесь по роли
- remember_person {"name": "Антон", "role": "шеф", "email": "..."} — запомнить, кто есть кто (навсегда)
Отслеживание (сообщу сам, когда случится):
- watch {"kind": "youtube|twitch|page|process|download", "target": "канал / логин / url / программа",
  "text": "что должно появиться на странице (для page)"}
Маркер на экране (показать пользователю, куда смотреть или нажать, когда объясняешь; сам ничего не нажимает):
- highlight {"text": "кнопка Настройки", "style": "circle|box|underline|arrow|fill", "label": "Нажмите сюда"}
  — обвести / подчеркнуть / показать стрелкой то, что на экране; {"clear": true} — убрать
Телефон пользователя (Android через KDE Connect, если подключён):
- phone {"action": "call|sms|reply|ring|share|photo|status", "to": "мама или номер", "text": "…"} — call: набрать номер
  на телефоне (откроется звонилка, нажать «позвонить» нужно на телефоне); sms: отправить SMS (сначала спроси
  «Отправить?»); reply: ответить на свежее сообщение мессенджера на телефоне (to — от кого, можно пусто: последнее;
  Джарвис сам спросит подтверждение); ring: заставить телефон звонить, чтобы найти; share: текст или ссылку на телефон
Фон (без мыши и клавиатуры, пользователь продолжает работать):
- download {"url": "https://...", "name": "необязательно"} — скачать файл в Загрузки
- cleanup {"what": "temp|ram|mail"} — почистить временные файлы, освободить память, разобрать рассылки
Если задача длинная и не требует экрана (скачать, разобрать, проверить), делай её этими инструментами, а не
мышью: пользователь в это время работает.
- run_command {"name": "название протокола пользователя"}
- weather {"city": "необязательно"} — получить погоду (данные)
- system {} — загрузка CPU/RAM/GPU (данные)
- source {"name": "название интеграции"} — получить данные интеграции
- clipboard {} — прочитать буфер обмена (данные)
- remember {"text": "факт о пользователе"}
- screenshot {}
- bridge {"agent": "claude|codex|antigravity|gemini", "prompt": "задача для агента-программиста"}
- now_playing {} — что сейчас играет (данные)
- find_file {"name": "часть имени файла"} — найти файл на ПК и показать его в проводнике
- dictation {} — включить диктовку (всё сказанное печатается в активное окно)
- create_command {"name": "Рабочий режим", "triggers": ["рабочий режим"], "reply": "фраза", "actions": [{"type": "app", "value": "telegram"}, {"type": "open", "value": "https://github.com"}]}
  — создать протокол пользователя. Типы шагов: app, open, close, say, wait, search, hotkey, type, volume, media, timer, notify, screenshot
- delete_command {"name": "..."} — удалить протокол
- focus {"minutes": 45, "task": "необязательно"} или {"off": true} — режим фокуса: таймер, тишина, напоминания не отвлекаться
- habit {"name": "вода", "add": 1} — отметить привычку (вода, зарядка, чтение…)
- usage {} — сколько пользователь сегодня провёл в программах и за ПК (данные)
- conversation {"on": true|false} — режим разговора без обращения по имени (для игр и долгих бесед)
- dnd {"minutes": 60} — «не беспокоить»: не говорить первым (0 — выключить)
- clipboard_history {} — последние скопированные тексты (данные)
"""
WEB_DOC = ("\nУ тебя есть свои инструменты WebSearch и WebFetch (интернет) и Read (файлы и картинки). Используй WebSearch "
           "только для свежих фактов, которых нет в контексте: новости, цены, курсы, расписания, результаты матчей, "
           "события после твоего обучения. Не ищи в сети то, что знаешь сам. Чтобы посмотреть на экран пользователя, "
           "Джарвис пришлёт путь к скриншоту — открой его инструментом Read.\n")
SHELL_DOC = "- shell {\"cmd\": \"команда cmd\"} — выполнить консольную команду (данные)\n"
WARM_PHRASES = ["Ищу.", "Секунду, ищу.", "Вот что я нашёл.", "Ищу на YouTube.", "Открываю.", "Открываю Википедию.",
                "Звук включён.", "Отменено, сэр.", "Таймеры отменены.", "Активных таймеров нет.", "Запомнил.",
                "Скопировано.", "Блокирую.", "Снимок экрана сохранён.", "Простите, сэр, что-то пошло не так."]
RISKY_TOOLS = {"shell", "type", "hotkey", "close_app", "run_command", "bridge", "window", "create_command",
               "remember", "delete_command", "click", "click_text", "mouse_move", "drag", "scroll", "click_element",
               "vision_click", "type_into", "drag_text", "mail_send", "mail_delete", "delayed", "undo", "download",
               "cleanup", "remember_person"}
DATA_TOOLS = {"tasks", "weather", "system", "source", "clipboard", "shell", "now_playing", "screen_text",
              "mouse_position", "reminders", "media_info", "shopping_list", "usage", "clipboard_history",
              "ui_elements", "history", "mail_list", "mail_read", "events", "undo", "contacts", "watch"}


class Core:
    def __init__(self):
        self.cfg = Config()
        self.emit_hook = None
        self.ui_hooks = {}
        self.logs = []
        self.state = "idle"
        self.sleeping = False
        self.pending = None
        self.follow_until = 0.0
        self.follow_pending = False
        self.follow_chain = 0        # nameless turns in a row inside follow windows (background talk must not loop)
        self.conversation = False    # "давай поговорим": every phrase is for Jarvis until "конец разговора"
        self.conv_last = 0.0
        self.dnd_until = 0.0         # "не беспокой": no proactive speech (messengers, breaks, PC warnings)
        self.alarm_active = False
        self.cancel_gen = 0
        self.ai_busy = 0
        self.notice = None       # the fired reminder/timer card shown with "Готово" / "Отложить"
        self.last_fired = None   # (kind, item) of the last fired one, for "отложи на 10 минут"
        self.capture = None      # (fn, until): the next phrase is data, not a command ("создай заметку" -> its text)
        self.dictation_file = None
        self.dialog_q = queue.Queue()
        self.chat_store = JsonStore("chat")
        from .persona import Persona
        self.persona = Persona(self)       # Jarvis or Friday: name, voice, address, wake word, logo

        self.player = Player(on_level=lambda v: self.emit("tts_level", v=round(v, 3)))
        self.tts = TTS(self.cfg, self.player, log=self.log, on_state=self._tts_state)
        self.listener = Listener(self.cfg, self.on_utterance, on_level=lambda v: self.emit("mic_level", v=round(v, 3)),
                                 on_state=self._stt_state, log=self.log, is_speaking=self.echo_window)
        self.tts.aligner = self.listener.align_end
        self.tts.aligner_ready = lambda: self.listener.model is not None and self.listener.model_name != "gigaam-v3-ctc"
        self.apps = S.AppIndex(log=self.log)
        self.planner = Planner(self.on_fire, on_change=lambda what: self.emit("planner", what=what))
        self.commands = Commands()
        self.chain = ChainRunner(self)
        self.bridges = Bridges(self.cfg, log=self.log, emit=self.emit)
        self.updater = Updater(self.cfg, log=self.log, emit=self.emit)
        self.ctx = nlu.Context()       # what the previous steps opened: "открой ютуб и найди там котиков"
        from .journal import Journal
        from .mail import Mail
        from .sentinel import Initiative, Sentinel
        self.journal = Journal(self)   # undo / corrections: wraps system.* and planner methods
        self.initiative = Initiative(self)
        self.sentinel = Sentinel(self)
        self.mail = Mail(self)
        from .background import Background
        from .people import People
        from .watchers import Watchers
        self.people = People(self)         # "шеф" -> Антон, learned from corrections
        self.watchers = Watchers(self)     # "скажи, когда выйдет новое видео у …"
        self.background = Background(self)  # downloads, cleanup, memory, mail sorting — no mouse, no keyboard
        from .plugins import Plugins
        self.plugins = Plugins(self)       # your own functions: data/plugins/*.py
        from .automation import Automations
        from .backup import Backup
        from .notes import Notebook
        self.automations = Automations(self)  # "когда запустилась игра / отключили зарядку — сделать …"
        self.notebook = Notebook(self)     # the «Заметки» tab and voice notes
        self.backup = Backup(self)         # zip of settings, commands, plugins, notes; weekly automatic copy
        from .phone import Phone
        self.phone = Phone(self)           # Android through KDE Connect: notifications, calls, "найди телефон", SMS
        from .quick import Quick
        self.quick = Quick(self)           # the quick panel over any window (Ctrl+Alt+Q)
        from .marker import Marker
        self.marker = Marker(self)         # circles / underlines / arrows drawn over the screen
        from .screenmem import ScreenMemory
        self.screenmem = ScreenMemory(self)  # "где я видел …": the active window's text once a minute, local OCR
        from .boot import Boot
        self.boot = Boot(self)             # the suit's power-up when the PC starts (ui/boot.html)
        from .macros import Macros
        self.macros = Macros(self)         # "запиши макрос": clicks and keys recorded and played back
        from .extras import Extras
        self.extras = Extras(self)   # before the router: it registers voice rules of its own
        self.router = Router(self)
        threading.Thread(target=self._dialog_worker, daemon=True, name="dialog").start()
        threading.Thread(target=self._ticker, daemon=True, name="ticker").start()

    # ================================================================== plumbing
    def emit(self, type_, **data):
        if self.emit_hook:
            try:
                self.emit_hook({"type": type_, **data})
            except Exception:
                pass

    def log(self, msg, level="info"):
        entry = {"t": dt.datetime.now().strftime("%H:%M:%S"), "msg": str(msg), "level": level}
        self.logs.append(entry)
        self.logs = self.logs[-300:]
        print(f"[{entry['t']}] {msg}")
        self.emit("log", **entry)

    def set_state(self, s):
        if s != self.state:
            self.state = s
            self.emit_state()

    def emit_state(self):
        now = time.time()
        self.emit("state", state=self.state, sleeping=self.sleeping, follow=now < self.follow_until,
                  follow_left=max(0, round(self.follow_until - now)), conversation=self.conversation,
                  dictation=getattr(self, "dictating", False))

    def _tts_state(self, s):
        if s == "speaking":
            self.set_state("speaking")
        elif s == "idle":
            if self.follow_pending:
                self.follow_pending = False
                self._open_follow()
            self.set_state("idle")
            self.emit_state()

    def _open_follow(self):
        sec = float(self.cfg.get("stt.follow_up_sec", 10) or 0)
        if sec > 0:
            self.follow_until = time.time() + sec
            self.emit_state()

    def set_conversation(self, on, announce=True):
        """Conversation mode: phrases without the name are accepted until "конец разговора" or silence."""
        on = bool(on)
        self.conversation = on
        self.conv_last = time.time()
        self.follow_chain = 0
        self.emit_state()
        if announce:
            self.say(random.choice(["Слушаю вас, можно без имени. Скажите «конец разговора», когда закончим.",
                                    "Режим разговора. Говорите без обращения, сэр."]) if on
                     else random.choice(["Возвращаюсь в ожидание.", "Хорошо поговорили, сэр.", "Режим разговора выключен."]))
        return on

    def proactive_ok(self, focus_too=False):
        """Jarvis may speak up on his own (new messages, breaks, PC warnings): not asleep, not "не беспокой",
        not busy talking, and not in focus mode unless the message is about focus itself."""
        if self.sleeping or time.time() < self.dnd_until or self.alarm_active or self.tts_busy() or self.ai_busy:
            return False
        if getattr(self, "dictating", False):
            return False
        return focus_too or not self.extras.focus_active()

    def _stt_state(self, s):
        if s in ("loading", "error"):
            self.emit("stt_status", status=s)
        if s == "listening":
            self.set_state("listening")
        elif s == "idle" and self.state == "listening":
            self.set_state("idle")
        elif s == "thinking":
            self.set_state("thinking")
        elif s == "mic_quiet":
            threading.Thread(target=self._fix_quiet_mic, daemon=True).start()
        elif s == "mic_silent":
            self._mic_warning(mic_access_problem() or (
                "Микрофон передаёт полную тишину. Проверьте, что он не выключен кнопкой на гарнитуре и в Windows, "
                "и выберите другой в Настройки → Голосовой ввод → Микрофон."))

    def _fix_quiet_mic(self):
        """A microphone that never got loud in its first 20 s: raise its Windows input level once."""
        name = self.listener.dev_name
        try:
            info = S.mic_level(name)
        except Exception as e:
            self.log(f"Громкость микрофона: {e}", "warn")
            return
        if not info:
            return
        if info["muted"] or info["level"] < 0.95:
            S.set_mic_level(1.0, name)
            msg = (f"Микрофон «{info['name']}» очень тихий: громкость в Windows была {int(info['level'] * 100)}%"
                   + (" и он был выключен" if info["muted"] else "") + ". Поднял до 100%.")
            self.log(msg, "warn")
            self.notify("Микрофон", msg)
        else:
            self.log("Микрофон очень тихий даже на 100% громкости. Если вас плохо слышно: Панель управления → Звук → "
                     "Запись → микрофон → Свойства → Уровни → «Усиление микрофона» +20 дБ; проверьте разъём.", "warn")

    def rpc_mic_test(self):
        """3 s of raw microphone levels with a verdict, for Settings → "Проверить микрофон"."""
        r = self.listener.measure(3.0)
        try:
            r["windows"] = S.mic_level(self.listener.dev_name)
        except Exception:
            r["windows"] = None
        loud = r.get("loud", 0)
        r["verdict"] = ("микрофон не передаёт звук" if not r.get("frames") else
                        "одна тишина: микрофон выключен или Windows не даёт звук" if loud < 1.5 else
                        "очень тихо — Джарвис усилит, но лучше поднять громкость микрофона" if loud < 150 else
                        "тихо, Джарвис усиливает автоматически" if loud < 600 else "отлично")
        return r

    def rpc_mic_boost(self):
        return S.set_mic_level(1.0, self.listener.dev_name)

    def _mic_warning(self, why):
        self.log(why, "warn")
        self.notify("Джарвис не слышит микрофон", why)
        self.emit("chat_msg", msg={"role": "assistant", "content": why, "source": "error",
                                   "ts": dt.datetime.now().isoformat(timespec="seconds")})
        self.say("Сэр, я вас не слышу: микрофон передаёт тишину. Подробности на экране.")

    def tts_busy(self):
        return self.player.busy() or bool(self.tts.current_text)

    def start(self):
        self.tts.warmup(info.QUIPS_ACK + info.QUIPS_DONE + WARM_PHRASES)  # voice model first: it loads longest
        self.listener.start()
        self.extras.start()
        self.sentinel.start()
        self.watchers.start()
        self.background.start()
        self.plugins.start()
        self.automations.start()
        self.backup.start()
        self.phone.start()
        self.screenmem.start()
        threading.Thread(target=lambda: nlu.morph(), daemon=True).start()  # word-form dictionary: load ahead
        agent = {"claude_code": "claude", "antigravity": "antigravity"}.get(self.cfg.get("ai.provider"))
        if agent:
            threading.Thread(target=lambda: self.bridges.warm(self.system_prompt(with_context=False), agent=agent),
                             daemon=True).start()
        self.log(f"J.A.R.V.I.S. {current_version()} запущен")
        if self.cfg.get("update.auto_check", True):
            threading.Thread(target=self._auto_update_check, daemon=True).start()
        if "windowsapps" in (sys.base_prefix + sys.executable).lower():
            # Microsoft Store Python is a packaged app: Windows gives it silence instead of the microphone
            threading.Timer(6, lambda: self._mic_warning(mic_access_problem())).start()
        if self.boot.should_play():
            self.boot.schedule()                       # the full-screen power-up greets instead (ui/boot.html)
        elif self.cfg.get("setup_done") and self.cfg.get("greeting", True):
            self.player.sound("boot")
            threading.Timer(1.6, lambda: self.say(self.greeting_phrase() + " " + random.choice(
                ["Все системы в норме.", "Системы онлайн.", "Готов к работе.", "Чем займёмся?"]))).start()

    def say(self, text, chat=False):
        if not text or text is True:
            return
        text = self.persona.speech(text)   # Friday: "сэр" -> "босс", "записал" -> "записала"
        self.emit("jarvis_say", text=text)
        print(f"[{dt.datetime.now():%H:%M:%S}] Джарвис: {text}")
        if chat:
            self._chat_add("assistant", text, "voice")
        self.tts.speak(text)

    def in_front(self):
        """The Jarvis window is the active one: the user sees its own toasts and cards right now."""
        try:   # pid first: no message to any window; the title then tells the main window from the HUD
            return S.foreground_pid() == os.getpid() and S.active_title() == "J.A.R.V.I.S."
        except Exception:
            return False

    def notify(self, title, text):
        """A toast inside Jarvis. A Windows notification as well only when enabled AND the Jarvis window is not
        in front — otherwise the same message showed twice (inside the window and in the corner of the screen)."""
        self.emit("toast", title=title, text=text)
        if self.cfg.get("windows_notifications", False) and not self.in_front():
            self._windows_toast(title, text)

    def _windows_toast(self, title, text):
        def go():
            try:
                from win11toast import notify
                notify(title, text, app_id="J.A.R.V.I.S.")
            except Exception:
                pass
        threading.Thread(target=go, daemon=True).start()

    def listen_next(self, timeout=8.0):
        """After a bare "Джарвис": accept the next phrase without the wake word (one phrase only).
        A time window instead of a one-shot flag: the user may start talking while "Да, сэр?" still plays,
        and the echo of that reply must not use up the chance."""
        self.accept_until = time.time() + timeout
        self.set_state("listening")

    def dictation(self, on):
        """Dictation mode: everything said is typed into the active window until "конец диктовки"
        (or appended to self.dictation_file: "запиши мою речь в файл")."""
        self.dictating = bool(on)
        self.emit_state()

    def _dictate(self, text, norm):
        if re.search(r"(конец|стоп|хватит|закончи|выключи)\w*\s+(диктовк|запис)|^диктовка\s+(стоп|выключ|конец)", norm):
            self.dictation(False)
            path = getattr(self, "dictation_file", None)
            self.dictation_file = None
            if path and Path(path).exists():
                self.say("Запись сохранена в Документы.")
                S.open_target(str(path))
            else:
                self.say("Диктовка завершена.")
            return
        t = text.strip()
        repl = [(r"(?i)\s*\bзапятая\b", ","), (r"(?i)\s*\bточка\b", "."), (r"(?i)\s*\bвопросительный знак\b", "?"),
                (r"(?i)\s*\bвосклицательный знак\b", "!"), (r"(?i)\s*\bдвоеточие\b", ":"),
                (r"(?i)\s*\b(?:новая строка|с новой строки|абзац)\b\s*", "\n")]
        for rx, s in repl:
            t = re.sub(rx, s, t)
        if t:
            path = getattr(self, "dictation_file", None)
            if path:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(t if t.endswith("\n") else t + " ")
            else:
                S.type_text(t if t.endswith("\n") else t + " ")
            self.emit("transcript", text="диктовка: " + t)

    def follow_up(self):
        """Open the "speak without the name" window after an answer: right away if nothing is being said,
        else when the voice stops (the window used to be set but never checked, so it did nothing)."""
        if self.tts_busy():
            self.follow_pending = True
        else:
            self.follow_pending = False
            self._open_follow()

    def ui(self, name, *a):
        fn = self.ui_hooks.get(name)
        if fn:
            try:
                fn(*a)
            except Exception as e:
                self.log(f"UI {name}: {e}", "warn")

    def show_window(self):
        self.ui("show")

    def hide_window(self):
        self.ui("hide")

    def show_tab(self, tab):
        self.emit("show_tab", tab=tab)

    def quit_soon(self):
        threading.Timer(2.5, lambda: self.ui("quit")).start()

    def after_speech(self, fn, delay=0.2):
        def go():
            time.sleep(0.3)
            while self.tts_busy():
                time.sleep(0.1)
            time.sleep(delay)
            fn()
        threading.Thread(target=go, daemon=True).start()

    def set_sleep(self, on):
        self.sleeping = on
        if on:
            self.follow_until = 0
            self.conversation = False
        self.emit_state()

    def ask_confirm(self, fn, question):
        self.pending = (fn, time.time())
        self.say(question)
        self.follow_up()

    def stop_all(self):
        self.cancel_gen += 1
        self.tts.stop()
        self.stop_alarm()

    def stop_alarm(self):
        self.alarm_active = False
        self.player.stop()

    # ================================================================== voice input
    def echo_window(self):
        """True while the assistant speaks and ~1 s after: the mic may still be hearing the speakers."""
        return self.tts_busy() or time.time() - self.player.last_end < 1.0

    def _echo_score(self, text):
        """How much the phrase looks like what Jarvis is saying / just said (0..100)."""
        from rapidfuzz import fuzz
        spoken = " ".join(x for x in (self.tts.current_text, self.tts.last_text) if x).lower().replace("ё", "е")
        t = (text or "").lower().replace("ё", "е")
        if not spoken or not t:
            return 0
        words = re.findall(r"\w+", t)
        sw = set(re.findall(r"\w+", spoken))
        overlap = 100 * sum(1 for w in words if w in sw) / max(1, len(words))
        return max(fuzz.partial_ratio(t, spoken), overlap if len(words) >= 2 else 0)

    def _is_echo(self, text):
        return self._echo_score(text) >= 72

    def _heard_log(self, text, woke, meta):
        """Everything the microphone recognised, with the wake decision: data/heard.log explains
        "he did not react to his name" (was it heard at all, and as what?)."""
        try:
            p = DATA / "heard.log"
            if p.exists() and p.stat().st_size > 400_000:
                p.replace(p.with_suffix(".old.log"))
            with open(p, "a", encoding="utf-8") as f:
                f.write(f"{dt.datetime.now():%m-%d %H:%M:%S} {'WAKE' if woke else '----'} "
                        f"{'tts ' if meta.get('during_tts') else ''}{meta.get('stt_ms', '')}ms | {text}\n")
        except Exception:
            pass

    def on_utterance(self, text, meta):
        wake = self.cfg.get("stt.wake_words", [])
        woke, rest = wake_split(text, wake)       # only "Джарвис, ..." at the very beginning counts
        if woke:
            text = ("Джарвис, " + rest) if rest else "Джарвис"  # "Гарвис, открой..." -> canonical name for the router
        self._heard_log(text, woke, meta)
        norm = normalize(text, wake)
        self.emit("heard", text=text, meta=meta)
        if meta.get("target") == "test":
            self.set_state("idle")
            return
        if meta.get("target") == "chat":
            self.dialog_q.put(("chat", text, "voice"))
            return
        echo = meta.get("during_tts") or self.echo_window()
        if echo and not woke:
            # barge-in: the microphone hears our own voice and then "Джарвис, стоп". The assistant never says
            # its name, so a wake word in the middle of a phrase during speech can only be the user.
            m = re.search(r"(?i)\b(?:" + "|".join(re.escape(w) for w in wake) + r")\w*", text)
            if m:
                text = text[m.start():]
                woke = True
                norm = normalize(text, wake)
        if echo and not woke and self._is_echo(norm or text):
            return  # our own voice came back through the speakers
        if getattr(self, "dictating", False):
            if woke and norm in STOP_WORDS:
                self.dictation(False)
                return
            self._dictate(re.sub(r"^\W*(?:джарвис|jarvis)\W*", "", text, flags=re.I) if woke else text, norm)
            return
        if getattr(self.phone, "searching", False) and re.search(
                r"\b(?:нашел|нашла|нашелся|нашлась|нашли|хватит|стоп|стой|все|тут|вот\s+он)\b", (norm or "").replace("ё", "е")):
            # "найди телефон" is ringing: the user is walking around the room, "нашёл" works without the name
            self.phone.stop_search()
            self.say("Отлично, сэр.")
            return
        if norm in STOP_WORDS and (woke or self.alarm_active):
            self.stop_all()
            self.clear_notice()
            self.set_state("idle")
            return
        if self.notice and time.time() < self.notice["until"]:
            # a reminder/timer card is up: "отложи на 5 минут" / "готово" work without the name
            m = re.match(r"^(?:отложи|отложить|напомни позже)\s*(?:на\s+)?(.*)$", norm)
            if m:
                secs = parse_duration(m.group(1)) if m.group(1) else 0
                self.say(self.snooze(max(1, (secs or 600) // 60)))
                return
            if re.match(r"^(?:готово|понял|поняла|ок|окей|хорошо|спасибо|принято|выключи|отключи)$", norm):
                self.rpc_notice("done")
                self.say(random.choice(["Принято.", "Хорошо, сэр.", "Понял."]))
                return
        if self.sleeping:
            if woke and re.search(r"просн|подъем|включ|вернись|ты тут", norm):
                self.dialog_q.put(("voice", text, "voice"))
            return
        now = time.time()
        confirm = self.pending and now - self.pending[1] < 20 and re.match(
            r"^(да|нет|подтверждаю|отмена|выполняй|не надо|точно|давай)\b", norm)
        window = now < getattr(self, "accept_until", 0)
        # nameless phrases: right after an answer (follow window), in conversation mode — and now also while
        # Jarvis is still answering the user (barge-in). Speech during the reply used to be dropped whatever it was,
        # so "Что делаешь?" said over the answer vanished; only the speakers' own echo is dropped now.
        nameless = False
        if not (meta.get("forced") or woke or confirm or window):
            fillers = {"", "ага", "угу", "мм", "м", "э", "эм", "хм", "ну", "а", "о", "ой", "так"}
            if norm not in fillers:
                in_dialog = self.conversation or (now < self.follow_until and self.follow_chain < 6)
                answering = now - getattr(self, "last_turn", 0) < 90 and self.follow_chain < 6
                if not echo and in_dialog:
                    nameless = True
                elif echo and (in_dialog or answering) and len(norm.split()) >= 2 and self._echo_score(norm or text) < 55:
                    nameless = True  # barge-in without the name: clearly not our own voice
                    self.log(f"Перебивание без имени: «{text}»")
        if not (meta.get("forced") or woke or confirm or window or nameless):
            return
        self.accept_until = 0
        self.follow_until = 0
        self.follow_chain = self.follow_chain + 1 if nameless else 0
        self.last_turn = now  # the user is talking to Jarvis: his reply to this may be interrupted without the name
        if self.conversation:
            self.conv_last = now
            if re.match(r"^(?:конец|закончим|заканчиваем|завершить|заверши|выйди из|выход из|стоп|хватит)\s*"
                        r"(?:режима\s+)?(?:разговор\w*|диалог\w*|болтать|общени\w*)$|^(?:пока|до связи|до встречи|"
                        r"все спасибо|всё спасибо|на этом все|на этом всё)$", norm):
                self.emit("transcript", text=text, stt_ms=meta.get("stt_ms"))
                self.set_conversation(False)
                return
        if self.tts_busy():
            self.tts.stop()  # barge-in: "Джарвис, ..." interrupts the current answer
        if self.cfg.get("stt.chime", True) and not self.night_on():
            self.player.sound("wake")
        self.emit("transcript", text=text, stt_ms=meta.get("stt_ms"), nameless=nameless)
        self.dialog_q.put(("voice", text, "voice_free" if nameless else "voice"))

    def execute(self, text, source="text"):
        """Typed command from the UI command line (acts like voice)."""
        self.dialog_q.put(("voice", text, source))

    def _dialog_worker(self):
        while True:
            kind, text, source = self.dialog_q.get()
            hint = None
            if source == "voice_free":
                source = "voice"
                # A question or request in the 2nd person right after Jarvis spoke is for him: no filter at all.
                # The old hint let the model drop "Что делаешь?" and "Чем мы займёмся" as "not for me".
                if not self._addressed_to_me(text):
                    hint = ("[Фраза сказана без обращения по имени (" + ("режим разговора" if self.conversation else
                            "сразу после твоего ответа") + "). Почти всегда это продолжение разговора с тобой — отвечай. "
                            "Только если она ОЧЕВИДНО обращена к другому человеку (по имени, «мам», спор двух людей) "
                            "или это звук из видео/игры, ответь ровно одним символом «—».]")
            try:
                self.set_state("thinking")
                if kind == "chat":
                    self.spawn_chat(text, source=source)
                else:
                    self._chat_add("user", text, source)
                    res = self.router.handle_chain(text)
                    if res is False:
                        self.spawn_chat(text, source=source, add_user=False, extra=hint)
                    elif isinstance(res, str):
                        # "открой блокнот и напиши привет": the router opened Notepad, the AI writes the rest
                        self.spawn_chat(text, source=source, add_user=False,
                                        extra=f"[Первую часть команды я уже выполнил. Сделай только это: {res}]")
                    else:
                        self.follow_up()
            except Exception as e:
                self.log(f"Ошибка обработки: {e}\n{traceback.format_exc()[-600:]}", "error")
                self.player.sound("error")
                self.say("Простите, сэр, что-то пошло не так.")
            finally:
                if not self.tts_busy() and not self.ai_busy:
                    self.set_state("idle")

    @staticmethod
    def _addressed_to_me(text):
        """A no-name phrase that is obviously for the assistant: a question to "ты", a request, a command."""
        from .nlu import is_imperative
        t = (text or "").lower().replace("ё", "е")
        words = re.findall(r"[а-яa-z-]+", t)
        if not words:
            return False
        # talking to someone else by name or "мам"/"пап": let the model judge, not this shortcut
        from .nlu import _parse
        if re.search(r"\b(мама|мам|ма|мамуль|папа|пап|бабушка|бабуль|дед|деда|сынок|доча|дочка|братан|бро)\b", t) or \
                any("Name" in p.tag for w in words[:2] for p in _parse(w)[:1]):
            return False
        if re.search(r"\b(ты|тебя|тебе|тобой|твой|твоя|твои|сам|давай|можешь|расскажи|скажи|покажи|объясни|"
                     r"займемся|поговорим|продолжай|продолжи|подробнее|еще|а\s+если|а\s+почему|почему|зачем|"
                     r"как\s+думаешь|что\s+думаешь|а\s+ты|спасибо|понятно|ясно|круто|отлично)\b", t):
            return True
        if t.rstrip().endswith("?") and len(words) <= 8:
            return True
        return is_imperative(words[0])

    def _show_step(self, hit):
        """Before the AI clicks: a quick ring around the target, so the user sees what is about to be pressed."""
        if not self.cfg.get("agent.show_steps", True) or not hit.get("rect"):
            return
        try:
            self.marker.show([{"rect": tuple(int(v) for v in hit["rect"]), "style": "circle"}], seconds=1.5)
            time.sleep(0.45)
        except Exception:
            pass

    def spawn_chat(self, text, source="text", add_user=True, extra=None, agent=False):
        """AI answers run in their own thread: voice commands keep working while the model thinks,
        and a new question supersedes an unfinished answer instead of queueing behind it."""
        if self.ai_busy:
            self.cancel_gen += 1
        gen = self.cancel_gen
        self.ai_busy += 1
        self.set_state("thinking")

        def run():
            g = self.journal.begin(text, "ai")
            try:
                self.chat(text, source=source, add_user=add_user, extra=extra, gen=gen, agent=agent)
            except Exception as e:
                self.log(f"Ошибка ИИ: {e}\n{traceback.format_exc()[-600:]}", "error")
                self.say("Простите, сэр, что-то пошло не так.")
            finally:
                self.journal.end(g)
                self.ai_busy -= 1
                if not self.ai_busy and not self.tts_busy():
                    self.set_state("idle")
        threading.Thread(target=run, daemon=True).start()

    def run_phrase(self, phrase):
        """Any phrase as if the user said it (queued after what is running now)."""
        self.dialog_q.put(("voice", phrase, "voice"))

    def run_phrase_sync(self, phrase):
        """The same, but waits until it is done: a "Фраза Джарвису" step of a protocol, then the next step."""
        g = None
        try:
            res = self.router.handle_chain(phrase)
            if res is False:
                g = self.journal.begin(phrase, "ai")
                self.ai_step(phrase, f"[Это шаг протокола пользователя. Выполни: «{phrase}».]")
        finally:
            if g:
                self.journal.end(g)
        t0 = time.time()
        while self.tts_busy() and time.time() - t0 < 15:
            time.sleep(0.1)

    def ai_step(self, text, note):
        """One step of a chain that the rules do not know, done by the AI right now (blocking), so the steps
        after it run after it: "открой блокнот, напиши стих про осень и сохрани"."""
        gen = self.cancel_gen
        self.ai_busy += 1
        self.set_state("thinking")
        g = self.journal.begin(text, "ai")
        try:
            self.chat(text, source="voice", add_user=False, extra=note, gen=gen, step=True)
        except Exception as e:
            self.log(f"Ошибка ИИ (шаг цепочки): {e}", "error")
        finally:
            self.journal.end(g)
            self.ai_busy -= 1
        # let the spoken part of this step finish before the next step acts
        t0 = time.time()
        while self.tts_busy() and time.time() - t0 < 12:
            time.sleep(0.1)

    # ================================================================== AI chat
    def context_line(self):
        """Fast-changing context (time, weather): sent with each message, so persistent AI sessions stay valid."""
        now = dt.datetime.now()
        wd = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"][now.weekday()]
        c = self.cfg
        notes = "; ".join(n["text"] for n in c.get("notes", [])[-30:]) or "пока ничего"
        cmds = ", ".join(x["name"] for x in self.commands.store.items if x.get("enabled", True)) or "нет"
        srcs = ", ".join(x["name"] for x in self.commands.sources.items) or "нет"
        try:
            import win32gui
            active = win32gui.GetWindowText(win32gui.GetForegroundWindow())
            active = "" if active in ("J.A.R.V.I.S.", "JARVIS HUD") else active
        except Exception:
            active = ""
        # memory and protocol lists live here too: changing them must not restart the warm AI session
        return (f"Сейчас {now:%H:%M}, {wd}, {now.day} {MONTHS[now.month - 1]} {now.year}. "
                + (f"Активное окно пользователя: «{active[:120]}». " if active else "") +
                f"Город: {c.get('location.city')}. {self._weather_line()}"
                f"Память о пользователе: {notes}. Протоколы пользователя (run_command): {cmds}. "
                f"Интеграции (source): {srcs}.").strip()

    def system_prompt(self, with_context=True):
        c = self.cfg
        name = c.get("user.name") or "пользователя"
        addr = c.get("user.address") or "сэр"
        p = ((self.persona.prompt_head(name, addr) or
              f"Ты — J.A.R.V.I.S. (Джарвис), персональный ИИ-ассистент {name}, как в фильмах о Железном человеке. "
              f"Говоришь по-русски, безупречно вежливо, с лёгкой британской иронией, обращаешься «{addr}». ") +
             "Отвечай кратко — 1-3 предложения, если не просят подробно: ответы озвучиваются голосом. "
             "В обычных ответах без markdown, таблиц и эмодзи; код и списки — только если просят.\n"
             "Каждое сообщение пользователя начинается с [Контекст: ...] — время, погода, память о пользователе, "
             "его протоколы. Используй это, но не пересказывай без просьбы. Если пользователь просит открыть сайт, "
             "который ты знаешь, открывай его адрес инструментом open (например rutracker.org, kinopoisk.ru).\n"
             + (self.context_line() + "\n" if with_context else ""))
        if c.get("ai.agent_tools", True):
            p += ("\nТы управляешь компьютером пользователя (Windows). Чтобы выполнить действие, вставь в ответ тег "
                  '<jarvis>{"tool": "open_app", "name": "telegram"}</jarvis> — можно несколько тегов. '
                  "Никогда не называй себя по имени (слово-активатор не должно звучать в ответах). Пользователь тег не видит. Не говори, что сделал действие, если не вставил тег. "
                  "Если для ответа нужны данные (tasks, weather для другого города, system, source, clipboard), "
                  "выведи ТОЛЬКО тег без текста и ничего не выдумывай — результат придёт следующим сообщением, тогда и ответишь. "
                  "Для действий (открыть, таймер, громкость…) — короткая фраза и тег, например: Открываю. <jarvis>…</jarvis>\n"
                  "Если просят написать или напечатать текст (письмо, сообщение, ответ в чат) — сочини его сам и вставь "
                  "инструментом type в активное окно, а вслух скажи только «Печатаю» — сам текст вслух не читай.\n"
                  "Громкость (volume, media) меняй только если прямо просят про громкость или звук. «Замолчи», «тихо», "
                  "«хватит», «стоп» значат: перестань говорить — громкость при этом не трогай. Фразы, явно сказанные не "
                  "тебе (разговор с кем-то), оставляй без действий и отвечай коротко.\n"
                  "Таймеры, напоминания и будильники ставь и удаляй только своими инструментами (timer, remind, alarm, "
                  "cancel_timer, delete_reminder) — никогда не открывай для этого другие программы вроде «Часов» Windows.\n"
                  "Закрыть вкладку — hotkey ctrl+w, закрыть все вкладки браузера — ctrl+shift+w; полноэкранное видео в "
                  "браузере — hotkey f (выход — esc). Не отказывайся закрывать вкладки и окна, если об этом просят.\n"
                  "Прежде чем вставлять теги, про себя продумай: чего пользователь хочет в итоге, в каком окне это "
                  "произойдёт, что уже сделано на прошлых шагах и какой текст на самом деле нужно ввести («напиши в нём "
                  "привет» — печатать только «привет»). Если просьба двусмысленна, а действие необратимо (отправить, "
                  "удалить, закрыть с потерей данных) — сначала уточни одним вопросом.\n"
                  "Понимай сленг, сокращения и неполные фразы: «врубай музон», «тг», «дс», «скинь», «чекни почту», "
                  "«почту шефу» (= написать письмо руководителю, кто это — смотри contacts), «таймер 5 мин».\n"
                  "Понимай намерение, а не буквальные слова: «найди в ютубе котиков» — это search site youtube с "
                  "query «котики»; «хочу послушать что-нибудь спокойное» — музыка; «закинь в покупки молоко» — "
                  "shopping. Слова-паразиты, «пожалуйста», название сайта и падежные окончания в запрос не попадают.\n"
                  "Если в просьбе несколько действий («сначала…, потом…, и…») — выполни их все по порядку тегами в "
                  "одном ответе; между открытием программы и вводом текста ставь wait 1-2 секунды.\n"
                  "Если пользователь поправляет тебя («не то», «не котиков, а собачек», «я имел в виду…») — сначала "
                  "undo, затем правильное действие. Уточняющий вопрос задавай, только если без него никак.\n"
                  "Для кликов по интерфейсу сначала ui_elements (или screen_text), потом click_element по номеру; "
                  "для иконок без подписи — vision_click. Не кликай по координатам наугад.\n"
                  + TOOLS_DOC + (SHELL_DOC if c.get("ai.allow_shell") else ""))
        prov = self.cfg.provider()
        if prov.get("agent") in ("claude", "antigravity") and c.get("ai.web_tools", True):
            p += WEB_DOC
        humor = int(c.get("ai.humor", 60) or 0)
        p += ("\nУровень юмора: " + str(humor) + "%. " +
              ("Отвечай строго по делу, без шуток." if humor < 20 else
               "Иногда позволяй себе сухую британскую иронию." if humor < 55 else
               "Охотно шути, подкалывай по-дружески, в духе Джарвиса и TARS." if humor < 85 else
               "Юмор на максимуме: остроумные подколки почти в каждом ответе, но задачу всё равно выполняй."))
        if c.get("ai.persona_extra"):
            p += "\nДополнительно: " + c.get("ai.persona_extra")
        return p

    def _weather_line(self):
        """Current weather cached for 15 min, so the AI can answer 'what's the weather' without a tool round."""
        cache = getattr(self, "_wcache", None)
        if not cache or time.time() - cache[0] > 900:
            try:
                loc = self.cfg.get("location")
                w = info.weather(loc["lat"], loc["lon"])
                line = (f"Погода сейчас ({loc['city']}): {w['temp']}°C, ощущается {w['feels']}, {w['desc']}, "
                        f"днём {w['min']}…{w['max']}, осадки {w['rain']}%. Завтра: {w['tomorrow']['desc']}, "
                        f"{w['tomorrow']['min']}…{w['tomorrow']['max']}°C.\n")
            except Exception:
                line = ""
            self._wcache = cache = (time.time(), line)
        return cache[1]

    def _chat_add(self, role, content, source="text", **extra):
        msg = {"role": role, "content": content, "ts": dt.datetime.now().isoformat(timespec="seconds"),
               "source": source, **extra}
        self.chat_store.items.append(msg)
        self.chat_store.items = self.chat_store.items[-300:]
        self.chat_store.save()
        self.emit("chat_msg", msg=msg)
        return msg

    def _history(self):
        n = int(self.cfg.get("ai.history", 16))
        msgs = [m for m in self.chat_store.items if m["role"] in ("user", "assistant") and not m.get("hidden")][-n:]
        return [{"role": m["role"], "content": m["content"]} for m in msgs]

    def chat(self, text, source="text", add_user=True, speak=None, extra=None, gen=None, step=False, agent=False):
        """extra: hidden text appended to the user's message for the model only (e.g. a screenshot path).
        gen: cancel generation of this request; stop_all() or a newer question bumps cancel_gen.
        step: one step of a chain (no follow-up window yet: the chain goes on)."""
        gen = self.cancel_gen if gen is None else gen
        if add_user:
            self._chat_add("user", text, source)
        speak = (source == "voice" or self.cfg.get("ai.speak_chat", True)) if speak is None else speak
        prov = self.cfg.provider()
        messages = [{"role": "system", "content": self.system_prompt(with_context=False)},
                    {"role": "system", "content": "Контекст: " + self.context_line()}] + self._history()
        if extra and messages[-1]["role"] == "user":
            messages[-1] = {"role": "user", "content": messages[-1]["content"] + "\n" + extra}
        # agent ("сделай за меня …"): more steps, and it always goes on until the task is done or the steps run out
        for _ in range(int(self.cfg.get("agent.max_steps", 12)) if agent else 5):
            answer, tools, web_used = self._stream_answer(prov, messages, speak, gen)
            if answer is None or gen != self.cancel_gen:
                return
            results = self._run_tools(tools, untrusted=web_used, gen=gen)
            data = {k: v for k, v in results if v is not None}
            if not data or gen != self.cancel_gen:
                break
            # screen reading and failed actions are steps of a longer task: the model may continue with tags
            acting = agent or any(k in ("screen_text", "ui_elements", "mouse_position") or str(v).startswith("ошибка")
                                  for k, v in data.items())
            messages = messages + [
                {"role": "assistant", "content": answer},
                {"role": "user", "content": "Результаты инструментов: " + json.dumps(data, ensure_ascii=False)[:7000]
                 + ("\nПродолжи выполнять задачу пользователя тегами, если нужно, или кратко ответь." if acting
                    else "\nОтветь пользователю по этим данным кратко, без тегов.")}]
        if not step:
            self.follow_up()

    def _stream_answer(self, prov, messages, speak, gen):
        feeder = self.tts.stream() if speak else None
        self.emit("chat_stream", state="start", provider=prov.get("title"))
        full = ""
        t0 = time.time()
        web_used = False
        said_wait = False
        try:
            for chunk in stream_chat(prov, messages, temperature=float(self.cfg.get("ai.temperature", 0.6)),
                                     bridges=self.bridges, cancel=lambda: gen != self.cancel_gen):
                if chunk.startswith(TOOL_MARK):
                    tool = chunk[len(TOOL_MARK):]
                    web_used = web_used or any(k in tool.lower() for k in ("web", "search", "url", "browser", "fetch", "http"))
                    self.emit("chat_stream", state="tool", name=tool)
                    if feeder and not said_wait:
                        said_wait = True
                        feeder.say_now(random.choice(["Секунду, сэр, проверяю.", "Сейчас уточню.", "Минуту, смотрю."])
                                       if tool != "Read" else "Смотрю.")
                    continue
                full += chunk
                self.emit("chat_stream", state="chunk", text=chunk)
                if feeder:
                    feeder.feed(chunk)
        except (LLMError, Exception) as e:
            if feeder:
                feeder.close()
            if gen != self.cancel_gen:
                self.emit("chat_stream", state="end", ms=int((time.time() - t0) * 1000))
                return None, [], False
            err = str(e)
            if "Connection" in err or "Max retries" in err:
                err = f"Не могу связаться с «{prov.get('title')}». Проверьте, что сервер запущен и адрес верный."
                if prov.get("id") == "ollama":   # a fresh install: point to the one-click setup
                    err += " Ollama ещё не установлена? Настройки → Искусственный интеллект → «Бесплатный локальный ИИ в один клик»."
            elif "Read timed out" in err:
                err = f"«{prov.get('title')}» слишком долго не отвечает — запрос прерван."
            self.emit("chat_stream", state="error", text=err)
            self._chat_add("assistant", err, "error")
            self.say("Модуль ИИ не ответил вовремя, сэр. Попробуйте ещё раз." if "прерван" in err
                     else "Боюсь, ИИ-модуль недоступен, сэр. Подробности в чате.")
            return None, [], False
        if feeder:
            feeder.close()
        if gen != self.cancel_gen:  # superseded by a newer question or "стоп": drop the rest, run no tools
            self.emit("chat_stream", state="end", ms=int((time.time() - t0) * 1000))
            return None, [], False
        tools = []
        for raw in re.findall(r"<jarvis>(.*?)(?:</jarvis>|\Z)", full, re.S):  # tolerate a missing closing tag
            try:
                tools.append(json.loads(raw.strip()))
            except Exception:
                m = re.search(r"\{.*\}", raw, re.S)
                if m:
                    try:
                        tools.append(json.loads(m.group(0)))
                    except Exception:
                        pass
        if not tools:  # some models drop the tag and write the bare JSON: {"tool": "hotkey", "keys": "tab"}
            for raw in re.findall(r"\{\s*\"tool\"\s*:[^{}]*\}", full):
                try:
                    tools.append(json.loads(raw))
                except Exception:
                    pass
        visible = re.sub(r"<jarvis>.*?(?:</jarvis>|\Z)", "", full, flags=re.S)
        visible = re.sub(r"\{\s*\"tool\"\s*:[^{}]*\}?", "", visible).replace("</jarvis>", "").strip()
        self.emit("chat_stream", state="end", ms=int((time.time() - t0) * 1000))
        if visible in ("—", "-", "–", "...", "…") and not tools:
            # a nameless phrase the model judged as not meant for it: stay silent, keep the window closed
            self.log("Фраза без имени — не мне, пропускаю", "info")
            return None, [], False
        if visible:  # tool-only turns (data requests) are not shown as empty bubbles
            self._chat_add("assistant", visible, "ai", tools=[t.get("tool") for t in tools], provider=prov.get("title"))
        return full, tools, web_used

    def _run_tools(self, tools, untrusted=False, gen=None):
        """untrusted: the answer was written after reading web pages, which may contain injected instructions,
        so actions that type, run commands or change things are refused for this turn."""
        out = []
        for t in tools:
            if gen is not None and gen != self.cancel_gen:
                break  # "стоп" in the middle of a multi-step action
            name = t.get("tool")
            args = t.get("args") if isinstance(t.get("args"), dict) else t
            if untrusted and name in RISKY_TOOLS:
                self.log(f"ИИ хотел выполнить «{name}» после чтения веб-страниц — заблокировано", "warn")
                continue
            try:
                res = self._tool(name, args)
                self.log(f"ИИ → {name} {json.dumps({k: v for k, v in args.items() if k != 'tool'}, ensure_ascii=False)[:120]}")
                out.append((name, res if name in DATA_TOOLS else None))
            except Exception as e:
                self.log(f"Инструмент {name}: {e}", "error")
                out.append((name, f"ошибка: {e}"))
        return out

    def _tool(self, name, a):
        g = lambda *keys: next((a[k] for k in keys if a.get(k) not in (None, "")), "")
        if name == "open_app":
            ok, nm = S.launch_app(g("name", "app"), self.apps, self.cfg.get("aliases"))
            return ok
        if name == "close_app":
            return S.close_app(g("name", "app"), self.apps, self.cfg.get("aliases"))
        if name in ("open", "open_url"):
            return S.open_target(g("target", "url", "path"))
        if name == "search":
            site = (g("site") or "google").lower()
            q = g("query", "q")
            if site not in nlu.ENGINE or site == "google":
                return S.search_web(q, self.cfg.get("search_url"))
            return S.open_target(nlu.search_url(site, q))
        if name == "youtube":
            return S.open_target(nlu.search_url("youtube", g("query", "q")))
        # ---- screen and mouse (jarvis/mouse.py)
        if name == "ui_elements":
            return MS.snapshot_elements(int(a.get("limit") or 80)) or "элементов не найдено — используй screen_text"
        if name == "click_element":
            e = MS.element_by_id(a.get("id"))
            if not e:
                raise ValueError("нет такого номера — сначала вызови ui_elements")
            self._show_step(e)
            MS.click(e["x"], e["y"], button=g("button") or "left", double=bool(a.get("double")))
            self.journal.record(f"кликнул по «{e['name'][:40]}»", None, "click")
            return True
        if name in ("vision_click", "click_image"):
            hit = MS.vision_find(self, g("what", "text", "query"))
            if not hit:
                raise ValueError("не нашёл это на экране")
            MS.click(hit["x"], hit["y"], button=g("button") or "left", double=bool(a.get("double")))
            return True
        if name == "type_into":
            if not MS.type_into(self, g("field", "label"), g("text"), enter=bool(a.get("enter")),
                                replace=a.get("replace") not in (False, "false", 0)):
                raise ValueError("поле не найдено")
            return True
        if name == "scroll_to":
            if not MS.scroll_to(self, g("text", "query"), "up" if g("direction") == "up" else "down"):
                raise ValueError("так и не появилось на экране")
            return True
        if name == "hover":
            hit = MS.locate(self, g("text", "query"))
            if not hit:
                raise ValueError("не нашёл на экране")
            MS.move(hit["x"], hit["y"])
            return True
        if name == "drag_text":
            if not MS.drag_between(self, g("from"), g("to")):
                raise ValueError("не нашёл, что или куда тащить")
            return True
        # ---- journal
        if name == "undo":
            return self.journal.undo(int(float(a.get("steps") or 1)))
        if name == "history":
            return [{"команда": x["text"], "действия": x["items"], "отменено": x["undone"], "время": x["at"]}
                    for x in self.journal.snapshot(8)]
        # ---- mail (jarvis/mail.py)
        if name.startswith("mail_"):
            fn = getattr(self.mail, "tool_" + name, None)
            if fn:
                return fn(a)
        if name == "delayed":
            when, _ = parse_when(str(g("when", "time")))
            if not when:
                raise ValueError("не понял время")
            self.planner.add_reminder("Команда: " + g("command"), when, command=g("command"))
            return True
        if name == "events":
            return [{"время": e["at"], "что": e["text"], "важность": e["priority"]} for e in self.initiative.summary(24)][:10]
        if name == "watch":
            return self.watchers.tool_watch(a)
        if name == "download":
            return self.background.tool_download(a)
        if name == "cleanup":
            return self.background.tool_cleanup(a)
        if name == "contacts":
            return [{"имя": p["name"], "почта": p.get("emails", []), "роли": p.get("roles", [])} for p in self.people.people][:40]
        if name == "remember_person":
            from .people import role_of
            role = role_of(g("role")) or (g("role") or "").lower()
            if role:
                p = self.people.bind_role(role, g("name"), g("email") or None)
            else:
                p = self.people.add(g("name"), g("email") or None)
            return self.people.describe(p)
        if name == "timer":
            d = g("duration", "time")
            secs = parse_duration(str(d)) or int(float(a.get("seconds") or 60))
            return self.planner.add_timer(secs, g("label"))
        if name in ("remind", "alarm"):
            when, _ = parse_when(str(g("when", "time")))
            if not when:
                raise ValueError("не понял время")
            return self.planner.add_reminder(g("text") or ("Будильник" if name == "alarm" else "Напоминание"),
                                             when, alarm=name == "alarm")
        if name == "add_task":
            when, rest = parse_when(str(g("when"))) if g("when") else (None, "")
            return self.planner.add_task(g("text"), when)
        if name == "tasks":
            return [{"n": i + 1, "text": x["text"], "due": x.get("due")} for i, x in enumerate(self.planner.sorted_tasks(True)[:15])]
        if name == "volume":
            if a.get("delta") not in (None, ""):
                return S.change_volume(int(a["delta"]))
            return S.set_volume(int(g("level", "value") or 50))
        if name == "media":
            act = (g("action") or "toggle").lower()
            act = {"play_pause": "toggle", "playpause": "toggle", "previous": "prev", "forward": "seek", "back": "seek",
                   "position": "seek_to", "speed": "rate", "start": "restart"}.get(act, act)
            val = None
            if act == "seek":
                val = float(a.get("seconds") or a.get("value") or 10) * (-1 if g("action") == "back" else 1)
            elif act == "seek_to":
                p = str(g("position", "seconds", "value"))
                mm = re.match(r"^(?:(\d+):)?(\d+):(\d{2})$", p)
                val = (int(mm.group(1) or 0) * 3600 + int(mm.group(2)) * 60 + int(mm.group(3))) if mm else float(p or 0)
            elif act == "rate":
                val = float(a.get("rate") or a.get("value") or 1)
            res = S.media_control(act, val, g("app") or None)
            if not res.get("ok"):
                if act in ("toggle", "play", "pause", "next", "prev") and not res.get("error"):
                    S.media({"toggle": "play", "pause": "play"}.get(act, act))
                    return True
                raise ValueError(res.get("error") or f"плеер «{res.get('app')}» не поддерживает это действие")
            return True
        if name == "media_info":
            return S.media_sessions() or "ничего не играет"
        if name == "window":
            return S.window_action(g("title", "name"), g("action") or "minimize")
        if name == "hotkey":
            return S.hotkey(g("keys", "combo"))
        if name == "type":
            S.type_text(g("text"))  # in order with the other tags: "type" then hotkey "enter" sends the message
            return True
        if name == "reminders":
            return {"timers": [{"label": t.get("label") or "", "left_sec": int(self.planner.timer_left(t))}
                               for t in self.planner.timers.items],
                    "reminders": [{"text": r["text"], "at": r["at"], "alarm": bool(r.get("alarm")), "repeat": r.get("repeat")}
                                  for r in self.planner.reminders.items if r.get("active")][:20]}
        if name == "cancel_timer":
            ts = self.planner.find_timers(g("which", "label", "name"))
            if not ts:
                raise ValueError("такого таймера нет")
            for t in ts:
                self.planner.cancel_timer(t["id"])
            self.stop_alarm()
            return len(ts)
        if name == "delete_reminder":
            al = a.get("alarm")
            rs = self.planner.find_reminders(g("which", "text", "name"), None if al in (None, "") else bool(al))
            if not rs:
                raise ValueError("такого напоминания нет")
            for r in rs:
                self.planner.delete_reminder(r["id"])
            return len(rs)
        if name == "note":
            text = g("text")
            if not text:
                raise ValueError("пустая заметка")
            self.notebook.add(text[:1].upper() + text[1:])
            return True
        if name == "shopping":
            items = [x for x in self.planner.tasks.items if "покупки" in (x.get("tags") or []) and not x.get("done")]
            if a.get("clear"):
                for x in items:
                    self.planner.delete_task(x["id"])
            for r in a.get("remove") or []:
                for x in items:
                    if str(r).lower() in x["text"].lower():
                        self.planner.delete_task(x["id"])
            have = {x["text"].lower() for x in items}
            for it in a.get("add") or []:
                if str(it).strip() and str(it).lower() not in have:
                    self.planner.add_task(str(it).strip().capitalize(), None, tags=["покупки"])
            return True
        if name == "shopping_list":
            pick = lambda tag: [x["text"] for x in self.planner.tasks.items if tag in (x.get("tags") or []) and not x.get("done")]
            return {"покупки": pick("покупки"),
                    "заметки": [self.notebook.title(n) for n in sorted(self.notebook.items, key=lambda n: n.get("updated", ""))[-10:]]}
        if name == "snooze":
            return self.snooze(int(float(a.get("minutes") or 10)))
        if name == "theme":
            return self.set_theme(g("name", "theme"))
        if name == "voice":
            return self.set_voice(g("speaker", "name", "voice"))
        if name == "night":
            return self.set_night(a.get("on") not in (False, "false", 0, "0", "off"))
        if name == "screen_text":
            return S.screen_text_lines()
        if name == "mouse_position":
            return S.mouse_position()
        if name in ("click", "double_click", "right_click"):
            if g("text", "label"):
                return self._tool("click_text", a)
            num = lambda k: int(float(a[k])) if a.get(k) not in (None, "") else None
            S.mouse_click(num("x"), num("y"), button="right" if name == "right_click" else (g("button") or "left"),
                          double=name == "double_click" or bool(a.get("double")))
            return True
        if name == "click_text":
            label = g("text", "label", "query")
            try:   # show where it is about to click (the marker), then click there
                pre = MS.find(label, small=True)
            except Exception:
                pre = None
            if pre:
                self._show_step(pre)
                MS.click(pre["x"], pre["y"], button=g("button") or "left", double=bool(a.get("double")))
                return True
            hit = S.click_text(label, button=g("button") or "left", double=bool(a.get("double")))
            if not hit:
                raise ValueError(f"надпись «{label}» не найдена на экране")
            return True
        if name == "mouse_move":
            if a.get("dx") not in (None, "") or a.get("dy") not in (None, ""):
                S.mouse_move_rel(int(float(a.get("dx") or 0)), int(float(a.get("dy") or 0)))
            else:
                S.mouse_move(int(float(a["x"])), int(float(a["y"])))
            return True
        if name == "drag":
            S.mouse_drag(*(int(float(a[k])) for k in ("x1", "y1", "x2", "y2")))
            return True
        if name == "scroll":
            S.scroll(g("direction") or "down", min(30, int(float(a.get("amount") or 5))))
            return True
        if name == "wait":
            time.sleep(max(0.0, min(10.0, float(a.get("seconds") or 1))))
            return True
        if name == "run_command":
            c = self.commands.get(g("name"))
            if c:
                self.run_command(c, {})
            return bool(c)
        if name == "weather":
            loc = self.cfg.get("location")
            if g("city"):
                loc = info.geocode(g("city")) or loc
            w = info.weather(loc["lat"], loc["lon"])
            w["city"] = loc["city"]
            return w
        if name == "system":
            s = S.system_stats()
            return {k: s[k] for k in ("cpu", "ram", "ram_used", "ram_total", "disk_free", "battery", "gpu", "uptime_h")}
        if name == "source":
            src = self.commands.get(g("name"), "source")
            return self.commands.fetch_source(src) if src else "интеграция не найдена"
        if name == "clipboard":
            return S.clipboard_get()[:3000]
        if name == "remember":
            notes = self.cfg.get("notes", [])
            notes.append({"text": g("text"), "at": dt.datetime.now().isoformat(timespec="minutes")})
            self.cfg.set("notes", notes[-200:])
            return True
        if name == "screenshot":
            return str(S.screenshot())
        if name == "bridge":
            self.bridge_task(g("agent") or "claude", g("prompt", "task"))
            return True
        if name == "shell":
            if not self.cfg.get("ai.allow_shell"):
                return "консольные команды для ИИ отключены в настройках"
            cmd = g("cmd", "command")
            if re.search(r"(?i)(taskkill|stop-process|kill|pkill)\b.*\b(pythonw?|msedgewebview2|jarvis)(\.exe)?\b", cmd):
                # "заверши процесс пайтон" once killed Jarvis itself mid-answer
                return "отказано: эта команда завершит самого Джарвиса. Для выхода пользователь говорит «Джарвис, выключись»."
            return S.run_shell(cmd)[:4000]
        if name == "now_playing":
            return S.now_playing() or "ничего не играет"
        if name == "find_file":
            threading.Thread(target=self.find_files, args=(g("name", "query"),), daemon=True).start()
            return True
        if name == "dictation":
            self.after_speech(lambda: self.dictation(True), 0.1)
            return True
        if name == "create_command":
            acts = [x for x in (a.get("actions") or []) if isinstance(x, dict) and x.get("type") in ACTION_TYPES]
            trig = [str(x).lower() for x in (a.get("triggers") or []) if str(x).strip()]
            if not g("name") or not acts:
                raise ValueError("нужны name и actions")
            cmd = self.commands.save_command({"name": g("name"), "triggers": trig or [g("name").lower()],
                                              "reply": g("reply"), "actions": acts, "enabled": True})
            self.emit("toast", title="Новая команда", text=cmd["name"])
            return True
        if name == "delete_command":
            c = self.commands.get(g("name"))
            if c:
                self.commands.delete_command(c["id"])
            return bool(c)
        if name == "phone":
            return self.phone.tool_phone(a)
        if name == "highlight":
            return self.marker.tool_highlight(a)
        ext = getattr(self.extras, "tool_" + str(name), None)
        if ext:
            return ext(a)
        raise ValueError(f"неизвестный инструмент {name}")

    def ask_ai_once(self, prompt):
        prov = self.cfg.provider()
        msgs = [{"role": "system", "content": self.system_prompt().split("\nТы управляешь")[0]},
                {"role": "user", "content": prompt}]
        try:
            return re.sub(r"<jarvis>.*?</jarvis>", "", "".join(stream_chat(prov, msgs, bridges=self.bridges)), flags=re.S).strip()
        except Exception as e:
            return f"ИИ недоступен: {e}"

    @staticmethod
    def summarize_for_voice(text, limit=280):
        text = re.sub(r"```.*?```", " ", text or "", flags=re.S)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) <= limit:
            return text
        cut = text[:limit]
        dot = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        return (cut[:dot + 1] if dot > 80 else cut + "…") + " Полный ответ на экране."

    # ================================================================== commands, sources, bridges
    def run_command(self, cmd, vars_=None):
        threading.Thread(target=self.chain.run, args=(cmd, vars_ or {}), daemon=True).start()

    def run_source(self, src):
        try:
            val = self.commands.fetch_source(src)
            self.say(self.commands.source_phrase(src, val))
        except Exception as e:
            self.say(f"Не удалось получить данные «{src['name']}».")
            self.log(f"Интеграция {src['name']}: {e}", "error")

    def bridge_task(self, agent, prompt):
        title = AGENTS.get(agent, {}).get("title", agent)
        if not self.bridges.find(agent):
            self.say(f"{title} не установлен на этом компьютере, сэр.")
            return
        self.say(f"Передаю задачу в {title}. Сообщу, когда будет готово.")
        self._chat_add("user", f"[{title}] {prompt}", "bridge")

        def done(text, err):
            if err:
                self._chat_add("assistant", f"{title}: {err}", "error")
                self.say(f"{title} сообщает об ошибке.")
                return
            self._chat_add("assistant", text or "(пустой ответ)", "bridge", agent=title)
            self.player.sound("notify")
            self.say(f"{title} закончил. " + self.summarize_for_voice(text))
            self.notify(title, (text or "")[:200])
        self.bridges.ask_async(agent, prompt, done)

    # ================================================================== planner events
    def on_fire(self, kind, item):
        """One card in Jarvis (main window + HUD) with "Готово" / "Отложить" instead of Windows notifications,
        which piled up in the Action Center and came twice."""
        addr = (self.cfg.get("user.address") or "сэр").capitalize()
        if item.get("command"):
            # "через 10 минут выключи звук": a scheduled command runs as if it was said now
            self.log(f"Отложенная команда: {item['command']}")
            self.emit("toast", title="Выполняю по расписанию", text=item["command"])
            self.dialog_q.put(("voice", item["command"], "scheduled"))
            return
        self.last_fired = (kind, dict(item))
        if kind == "timer":
            lbl = f" «{item['label']}»" if item.get("label") else ""
            text = f"Таймер{lbl} на {say_duration(item['duration'])} истёк"
        else:
            text = item.get("text") or ""
        title = {"timer": "Таймер", "reminder": "Напоминание", "alarm": "Будильник", "task": "Задача"}[kind]
        # one card where the user is looking: the main window when it is in front, else the HUD (+ Windows if enabled)
        front = self.in_front()
        self.notice = {"kind": kind, "title": title, "text": text, "id": item.get("id"), "hud": not front,
                       "until": time.time() + (120 if kind in ("timer", "alarm") else 60)}
        self.emit("notice", **self.notice)
        if self.cfg.get("windows_notifications", False) and not front:
            self._windows_toast(title, text)
        night = self.night_on()
        if kind == "timer":
            self._alarm_loop(f"{addr}, таймер{lbl} истёк.", 2 if night else 4)
        elif kind == "reminder":
            self.player.sound("notify")
            self.after_speech(lambda: self.say(f"{addr}, напоминаю: {item['text']}."), 0.1)
        elif kind == "alarm":
            self._alarm_loop(None, 30, wake_up=True)
        elif kind == "task":
            self.player.sound("notify")
            self.say(f"{addr}, наступил срок задачи: {item['text']}.")
        self.emit("fired", kind=kind, item=item)

    def _alarm_loop(self, phrase, repeats, wake_up=False):
        def go():
            self.alarm_active = True
            if phrase:
                self.player.sound("alarm")
                self.say(phrase)
            for i in range(repeats):
                if not self.alarm_active:
                    return
                self.player.sound("alarm")
                time.sleep(2.2)
                if wake_up and i == 1:
                    self.say(self.greeting_phrase() + " Пора вставать. Скажите «стоп», чтобы выключить будильник.")
                    time.sleep(5)
            self.alarm_active = False
        threading.Thread(target=go, daemon=True).start()

    def greeting_phrase(self):
        h = dt.datetime.now().hour
        part = "Доброй ночи" if h < 5 else "Доброе утро" if h < 12 else "Добрый день" if h < 18 else "Добрый вечер"
        name = self.cfg.get("user.name")
        addr = self.cfg.get("user.address") or "сэр"
        return f"{part}, {name if name and random.random() < 0.4 else addr}."

    def look(self, question):
        """Screenshot -> AI vision. With the Claude bridge the model opens the image itself (Read tool)."""
        from PIL import Image
        path = S.screenshot()
        small = DATA / "screenshots" / "look.png"
        img = Image.open(path)
        img.thumbnail((1600, 1600))
        img.save(small)
        q = question.strip() or "Что у меня на экране? Опиши кратко главное."
        prov = self.cfg.provider()
        from .llm import vision_supported
        if vision_supported(prov):
            # the model gets the real picture (it used to get only OCR text: no images, icons or layout)
            def go():
                self.ai_busy += 1
                self.set_state("thinking")
                try:
                    ans = MS.describe_screen(self, q)
                finally:
                    self.ai_busy -= 1
                if ans:
                    self._chat_add("assistant", ans, "ai", provider="зрение")
                    self.say(self.summarize_for_voice(ans, 400))
                    self.follow_up()
                else:
                    self._look_text(q)
            threading.Thread(target=go, daemon=True).start()
            return
        self._look_text(q, prov)

    def _look_text(self, q, prov=None):
        prov = prov or self.cfg.provider()
        small = DATA / "screenshots" / "look.png"
        if prov.get("kind") == "bridge" and prov.get("agent") == "claude":
            self.spawn_chat(q + " (смотрю на экран)", source="voice",
                            extra=f"[Скриншот экрана пользователя сохранён в {small} — открой его инструментом Read и "
                                  f"ответь по тому, что видно. Отвечай кратко.]")
        else:
            # models without image input still get the screen as text (Windows OCR)
            try:
                lines = [x["text"] for x in S.screen_text_lines(160)]
            except Exception as e:
                self.log(f"OCR: {e}", "warn")
                lines = []
            if not lines:
                self.say("Не могу разглядеть экран, сэр.")
                return
            self.spawn_chat(q + " (смотрю на экран)", source="voice",
                            extra="[Текст на экране пользователя (распознан OCR, сверху вниз):\n"
                                  + "\n".join(lines)[:5000] + "\nОтветь по нему кратко.]")

    SELECTION_TASKS = {
        "translate": ("Переведи текст {lang}. Сохрани форматирование и переносы строк.", True),
        "fix": ("Исправь орфографию, пунктуацию и грамматику. Стиль и смысл не меняй.", True),
        "improve": ("Улучши текст: сделай его понятнее и грамотнее, смысл сохрани.", True),
        "shorten": ("Сократи текст примерно вдвое, сохранив главное.", True),
        "formal": ("Перепиши текст в вежливом деловом стиле.", True),
        "explain": ("Объясни простыми словами, что здесь написано, в 2-3 предложениях.", False),
        "summary": ("Перескажи главное в 2-3 предложениях.", False),
        "answer": ("Ответь на этот вопрос или выполни задание из текста. Кратко.", False),
    }

    def selection_action(self, task, extra=""):
        """Works on the text selected in any app: copies it, asks the AI, then either pastes the result
        over the selection (translate, fix...) or speaks it (explain, summary)."""
        old = S.clipboard_get() or ""
        S.clipboard_set("")
        S.hotkey("ctrl+c")
        sel = ""
        for _ in range(10):
            time.sleep(0.08)
            sel = S.clipboard_get() or ""
            if sel:
                break
        if not sel.strip():
            S.clipboard_set(old)
            self.say("Сначала выделите текст, сэр.")
            return
        instr, replace = self.SELECTION_TASKS[task]
        if task == "translate":
            m = re.search(r"на (английск|русск|немецк|французск|испанск|китайск|японск|украинск)\w*", extra)
            target = (m.group(1) + "ий") if m else ("английский" if re.search(r"[а-яё]", sel.lower()) else "русский")
            instr = instr.format(lang=f"на {target} язык")
        self.say(random.choice(["Секунду.", "Сейчас сделаю.", "Минуту, сэр."]))
        prompt = (f"{instr}\nОтветь ТОЛЬКО итоговым текстом — без кавычек, вступлений, пояснений и обращений.\n\n"
                  f"Текст:\n{sel[:6000]}")
        result = self.ask_ai_once(prompt).strip()
        if not result or result.startswith("ИИ недоступен"):
            S.clipboard_set(old)
            self.say("ИИ сейчас недоступен, сэр.")
            return
        self._chat_add("assistant", result, "ai", provider="выделенный текст")
        if replace:
            S.clipboard_set(result)
            S.hotkey("ctrl+v")
            time.sleep(0.4)
            S.clipboard_set(old)
            self.say(random.choice(["Готово, сэр.", "Заменил.", "Исправлено."]) if task != "translate" else "Перевёл.")
        else:
            self.say(result)

    def find_files(self, query):
        found = S.find_files(query) or (S.find_files(S.translit(query)) if re.search(r"[а-яё]", query.lower()) else [])
        self.emit("files_found", query=query, files=[str(p) for p in found])
        if not found:
            self.say(f"Не нашёл файлов «{query}», сэр.")
            return
        S.reveal(found[0])
        n = len(found)
        self.say(f"Нашёл {n} {plural(n, 'файл', 'файла', 'файлов')}. Показываю первый: {found[0].name}." if n > 1
                 else f"Нашёл: {found[0].name}.")

    WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]

    def briefing(self, evening=None):
        """Morning protocol (or evening summary after 18:00): spoken, and shown as a card on the screen.
        Every section is optional: a missing network only drops weather and rates."""
        now = dt.datetime.now()
        evening = now.hour >= 18 if evening is None else evening
        addr = self.cfg.get("user.address") or "сэр"
        speech, card = [], []   # card: (icon, title, line)
        speech.append(self.greeting_phrase())
        speech.append(f"Сегодня {self.WEEKDAYS[now.weekday()]}, {now.day} {MONTHS[now.month - 1]}, {now:%H:%M}.")

        # weather with advice
        try:
            loc = self.cfg.get("location")
            w = info.weather(loc["lat"], loc["lon"])
            if evening:
                tm = w["tomorrow"]
                speech.append(f"Завтра {tm['desc']}, от {tm['min']} до {tm['max']} градусов"
                              + (f", вероятность осадков {tm['rain']} процентов." if (tm.get("rain") or 0) >= 40 else "."))
                card.append(("weather", "Погода завтра", f"{tm['desc']}, {tm['min']}…{tm['max']}°C, осадки {tm['rain']}%"))
            else:
                line = f"За окном {w['temp']}°, ощущается как {w['feels']}°, {w['desc']}. Днём {w['min']}…{w['max']}°."
                speech.append(f"За окном {w['temp']} градусов, {w['desc']}, днём до {w['max']}.")
                t = w["feels"]
                tip = ("Одевайтесь очень тепло." if t <= -10 else "Шапка и перчатки не помешают." if t <= 0 else
                       "Без куртки не обойтись." if t <= 10 else "Лёгкая куртка будет кстати." if t <= 17 else
                       "Жарко — не забывайте про воду." if w["max"] >= 27 else "")
                if w.get("rain_at"):
                    tip = f"Около {w['rain_at']} возможен дождь — возьмите зонт. " + tip
                if (w.get("wind") or 0) >= 10:
                    tip += f" Сильный ветер, {w['wind']} метров в секунду."
                if (w.get("uv") or 0) >= 6:
                    tip += " Высокий ультрафиолет."
                if tip.strip():
                    speech.append(tip.strip())
                sun = f" Восход {w['sunrise']}, закат {w['sunset']}." if w.get("sunrise") else ""
                card.append(("weather", "Погода", line + sun + (" " + tip.strip() if tip.strip() else "")))
        except Exception:
            pass

        # the plan
        day = now.date() + (dt.timedelta(days=1) if evening else dt.timedelta())
        tasks = self.planner.tasks_for_day(day)
        overdue = [t for t in self.planner.sorted_tasks(True) if t.get("due") and t["due"][:10] < now.date().isoformat()]
        if tasks:
            names = "; ".join(t["text"] for t in tasks[:2])
            speech.append(f"{'На завтра' if evening else 'На сегодня'} {len(tasks)} "
                          f"{plural(len(tasks), 'задача', 'задачи', 'задач')}: {names}.")
            card.append(("tasks", "Задачи", " · ".join(t["text"] for t in tasks[:5])))
        else:
            speech.append("Задач на завтра нет." if evening else "Задач на сегодня нет — свободный день.")
        if overdue:
            speech.append(f"И {len(overdue)} {plural(len(overdue), 'просроченная', 'просроченные', 'просроченных')}.")
            card.append(("warn", "Просрочено", " · ".join(t["text"] for t in overdue[:4])))
        if evening:
            done = [t for t in self.planner.tasks.items if t.get("done") and (t.get("done_at") or "")[:10] == now.date().isoformat()]
            if done:
                speech.append(f"Сегодня вы закрыли {len(done)} {plural(len(done), 'задачу', 'задачи', 'задач')}. Неплохо, {addr}.")
                card.append(("ok", "Сделано сегодня", " · ".join(t["text"] for t in done[:5])))
        rems = sorted((r for r in self.planner.reminders.items if r.get("active") and r["at"][:10] == day.isoformat()),
                      key=lambda r: r["at"])
        if rems:
            first = rems[0]
            speech.append(f"{'Напоминаний' if not first.get('alarm') else 'Напоминаний и будильников'}: {len(rems)}, "
                          f"первое в {first['at'][11:16]} — {first['text']}.")
            card.append(("bell", "Напоминания", " · ".join(f"{r['at'][11:16]} {r['text']}" for r in rems[:5])))
        if self.planner.timers.items:
            card.append(("timer", "Таймеры", f"активно: {len(self.planner.timers.items)}"))

        # money and the machine
        if not evening:
            try:
                cur = info.currency_trend()
                usd = cur.get("USD")
                if usd:
                    rub = lambda x: (f"{int(x)} {plural(int(x), 'рубль', 'рубля', 'рублей')} "
                                     f"{round(x * 100) % 100} {plural(round(x * 100) % 100, 'копейка', 'копейки', 'копеек')}")
                    kop = lambda x: (rub(x) if x >= 1 else
                                     f"{round(x * 100)} {plural(round(x * 100), 'копейку', 'копейки', 'копеек')}")
                    ch = usd[1]
                    trend = "" if abs(ch) < 0.05 else (f", вырос на {kop(abs(ch))}" if ch > 0 else f", подешевел на {kop(abs(ch))}")
                    speech.append(f"Доллар — {rub(usd[0])}{trend}.")
                    card.append(("money", "Курсы ЦБ", " · ".join(f"{k} {v[0]:.2f} ({'+' if v[1] >= 0 else ''}{v[1]:.2f})"
                                                                  for k, v in cur.items())))
            except Exception:
                pass
        try:
            st = S.system_stats()
            warn = []
            if isinstance(st.get("disk_free"), (int, float)) and st["disk_free"] < 10:
                warn.append(f"на диске осталось {st['disk_free']:.0f} гигабайт")
            b = st.get("battery")
            if isinstance(b, (int, float)) and b < 25:
                warn.append(f"заряд батареи {int(b)} процентов")
            if warn:
                speech.append("Внимание: " + ", ".join(warn) + ".")
                card.append(("warn", "Система", ", ".join(warn)))
        except Exception:
            pass
        try:
            ub = self.extras.usage_brief(evening)
            if ub:
                speech.append(ub[0])
                card.append(("ok" if evening else "timer", "За компьютером", ub[1]))
        except Exception:
            pass
        info_u = getattr(self.updater, "info", None)
        if info_u and info_u.get("available"):
            speech.append("Кстати, доступно обновление.")
            card.append(("update", "Обновление", f"версия {info_u['latest']} — Настройки → Обновления"))

        speech.append(random.choice(
            ["Спокойной ночи, сэр." if evening else "Хорошего дня, сэр.", "Приступим?" if not evening else "Отдыхайте, сэр.",
             "Все системы в норме." if not evening else "Завтра будет продуктивный день.",
             "Если что — я на связи."]))
        self.emit("briefing", title="Вечерняя сводка" if evening else "Утренний протокол",
                  date=f"{self.WEEKDAYS[now.weekday()].capitalize()}, {now.day} {MONTHS[now.month - 1]}", items=card)
        self.say(" ".join(speech))

    def _duck_loop(self):
        """Music and videos get quieter only while the assistant's voice plays, then come back.
        It used to duck while "listening"/"thinking" too: a state that got stuck kept videos quiet after
        answers, and an exception on restore forgot the saved levels."""
        try:
            n = S.restore_ducked()
            if n:
                self.log(f"Вернул громкость {n} программам после прошлого запуска")
        except Exception as e:
            self.log(f"Громкость программ: {e}", "warn")
        ducked, last_busy = False, 0.0
        while True:
            time.sleep(0.15)
            if self.tts_busy() or self.player.busy():
                last_busy = time.time()
            want = self.cfg.get("duck_media", True) and time.time() - last_busy < 0.8
            try:
                if want and not ducked:
                    S.duck(True, level=float(self.cfg.get("duck_level", 0.5)))
                    ducked = True
                elif not want and ducked:
                    S.duck(False)
                    ducked = False
            except Exception as e:
                self.log(f"Приглушение звука: {e}", "warn")
                time.sleep(1)  # keep the state and retry: the saved levels must come back

    def _maybe_briefing(self):
        """Scheduled morning briefing: at the set time, or at the first activity within 4 hours after it."""
        bt = (self.cfg.get("briefing_time") or "").strip()
        m = re.match(r"^(\d{1,2})[:.](\d{2})$", bt)
        if not m or self.sleeping:
            return
        now = dt.datetime.now()
        today = now.date().isoformat()
        if self.cfg.get("briefing_last") == today:
            return
        start = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
        if not (start <= now <= start + dt.timedelta(hours=4)):
            return
        if S.idle_seconds() > 90 or self.tts_busy():
            return  # nobody at the PC yet: wait for the first activity
        self.cfg.set("briefing_last", today)
        self.briefing()

    def _ticker(self):
        threading.Thread(target=self._duck_loop, daemon=True, name="duck").start()
        S.system_stats()  # prime cpu_percent
        while True:
            time.sleep(2)
            try:
                self.emit("stats", **S.system_stats())
            except Exception:
                pass
            try:
                self._maybe_briefing()
            except Exception as e:
                self.log(f"Брифинг: {e}", "warn")
            if self.follow_until and time.time() > self.follow_until:
                self.follow_until = 0
                self.follow_chain = 0
                self.emit_state()
            if (self.conversation and time.time() - self.conv_last > float(self.cfg.get("stt.conversation_idle_sec", 120))
                    and not self.tts_busy() and not self.ai_busy):
                self.set_conversation(False, announce=False)
                self.log("Режим разговора выключен: долго тихо")
            try:
                self.extras.tick()
            except Exception as e:
                self.log(f"Модули: {e}", "warn")
            try:
                self.initiative.tick()
                self.mail.poll()
            except Exception as e:
                self.log(f"Инициатива/почта: {e}", "warn")
            if self.pending and time.time() - self.pending[1] > 30:
                self.pending = None
            try:
                self._night_tick()
            except Exception as e:
                self.log(f"Ночной режим: {e}", "warn")
            if self.notice and time.time() > self.notice["until"]:
                self.clear_notice()
            # "Джарвис" with nothing after it left the state on "listening" forever (and kept media ducked)
            if (self.state == "listening" and time.time() > getattr(self, "accept_until", 0) + 1
                    and not self.listener.force_next and not self.listener.recording):
                self.set_state("idle")

    # ================================================================== night mode
    def night_on(self):
        return bool(self.cfg.get("night.enabled", False))

    def set_night(self, on, announce=False):
        """Night mode: dim warm interface, quieter voice, no interface sounds or wake chime."""
        on = bool(on)
        self.cfg.set("night.enabled", on)
        self.emit("night", on=on)
        if announce:
            self.say("Ночной режим. Говорю тише, сэр." if on else "Дневной режим.")
        return on

    def _night_tick(self):
        """Scheduled night mode (Settings → Ночной режим → по расписанию): switch only at the boundaries,
        so a manual change in between is respected."""
        if not self.cfg.get("night.auto", False):
            return
        now = dt.datetime.now().strftime("%H:%M")
        a, b = self.cfg.get("night.from", "23:00"), self.cfg.get("night.to", "08:00")
        inside = (a <= now < b) if a < b else (now >= a or now < b)
        if inside != getattr(self, "_night_auto_last", None):
            if getattr(self, "_night_auto_last", None) is not None or inside:
                self.set_night(inside)
            self._night_auto_last = inside

    def rpc_night(self, on=None):
        return self.set_night(not self.night_on() if on is None else bool(on))

    # ================================================================== fired reminders / timers
    def clear_notice(self):
        self.notice = None
        self.emit("notice", kind=None)

    def rpc_notice(self, action="done", minutes=10):
        """Buttons of a fired reminder/timer card (main window and HUD): done | snooze."""
        n = self.notice or ({"kind": self.last_fired[0], "text": self.last_fired[1].get("text") or
                             self.last_fired[1].get("label") or ""} if self.last_fired else None)
        self.stop_alarm()
        self.clear_notice()
        if action != "snooze" or not n:
            return "ok"
        return self.snooze(int(minutes or 10), n)

    def snooze(self, minutes=10, n=None):
        n = n or self.notice or ({"kind": self.last_fired[0], "text": self.last_fired[1].get("text") or
                                  self.last_fired[1].get("label") or ""} if self.last_fired else None)
        if not n:
            return "Откладывать нечего, сэр."
        self.stop_alarm()
        self.clear_notice()
        if n["kind"] == "timer":
            self.planner.add_timer(minutes * 60, n.get("text") or "")
        else:
            self.planner.add_reminder(n.get("text") or "Напоминание", dt.datetime.now() + dt.timedelta(minutes=minutes),
                                      alarm=n["kind"] == "alarm")
        return f"Отложил на {say_duration(minutes * 60)}."

    # ================================================================== RPC for the UI
    def rpc(self, method, p):
        fn = next((getattr(o, "rpc_" + method) for o in (self, self.extras, self.mail, self.sentinel, self.initiative,
                                                          self.people, self.watchers, self.background, self.plugins,
                                                          self.automations, self.notebook, self.backup, self.persona, self.phone,
                                                          self.quick, self.marker, self.screenmem, self.macros, self.boot)
                   if hasattr(o, "rpc_" + method)), None)
        if not fn:
            raise ValueError(f"unknown method {method}")
        return fn(**(p or {}))

    def rpc_state(self):
        return {"state": self.state, "sleeping": self.sleeping, "config": self.cfg.public(), "logs": self.logs[-80:],
                "providers": PROVIDERS, "action_types": ACTION_TYPES, "source_types": SOURCE_TYPES,
                "version": current_version(),
                "stt": {"model": self.listener.model_name, "device": self.listener.device, "mic_error": self.listener.mic_error,
                        "mic": self.listener.mic_name, "gain": round(self.listener.gain, 1)},
                "xtts": {"installed": XttsEngine.available(), "ready": self.tts.engines["xtts"].ready,
                         "error": self.tts.engines["xtts"].error},
                "night": self.night_on(), "notice": self.notice, "conversation": self.conversation,
                "focus": self.extras.focus_state()}

    # --- the tray menu (ui/traymenu.html)
    def rpc_tray_state(self):
        dnd = time.time() < self.dnd_until
        label = ("режим сна" if self.sleeping else "диктовка" if getattr(self, "dictating", False) else
                 {"listening": "слушаю", "thinking": "думаю", "speaking": "говорю"}.get(self.state, "в сети"))
        return {"sleeping": self.sleeping, "dnd": dnd, "dnd_until": dt.datetime.fromtimestamp(self.dnd_until).strftime("%H:%M") if dnd else "",
                "theme": self.cfg.get("theme", "jarvis"), "night": self.night_on(), "label": label}

    def rpc_tray_action(self, action):
        if action == "sleep":
            self.set_sleep(not self.sleeping)
            return True
        if action == "dnd":
            self.dnd_until = 0 if time.time() < self.dnd_until else time.time() + 3600
            self.emit("hub", what="dnd")
            return True
        self.ui("menu_hide")
        if action == "show":
            self.show_window()
        elif action == "listen":
            self.rpc_listen()
        elif action == "stop":
            self.stop_all()
        elif action == "quit":
            self.say("Отключаюсь. До встречи, " + (self.cfg.get("user.address") or "сэр") + ".")
            self.quit_soon()
        return True

    def rpc_polish_text(self, text, kind="email"):
        res = self.ask_ai_once("Исправь ошибки и сделай этот текст " + ("письма " if kind == "email" else "") +
                               "понятнее и вежливее, смысл и язык сохрани. Ответь только итоговым текстом:\n\n" + text)
        if not res or res.startswith("ИИ недоступен"):
            raise RuntimeError("ИИ недоступен")
        return res

    def rpc_journal(self):
        return self.journal.snapshot(25)

    def rpc_journal_undo(self, id):
        msg = self.journal.undo_id(int(id))
        self.say(msg)
        return msg

    def rpc_eleven_info(self):
        try:
            return self.tts.engines["elevenlabs"].info()
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def rpc_config_set(self, patch):
        old_mic, old_model = self.cfg.get("stt.mic"), (self.cfg.get("stt.model"), self.cfg.get("stt.engine"))
        # api keys: empty string in patch means "keep existing"
        for pid, pv in (patch.get("ai", {}).get("providers") or {}).items():
            if isinstance(pv, dict) and pv.get("api_key") == "":
                pv.pop("api_key")
            if isinstance(pv, dict):
                pv.pop("api_key_set", None)
        if isinstance(patch.get("mail"), dict):
            patch["mail"].pop("accounts", None)  # mailboxes change only through mail_account_save (passwords)
        pv = patch.get("voice")
        if isinstance(pv, dict):
            pv.pop("eleven_key_set", None)
            if pv.get("eleven_key") == "":
                pv.pop("eleven_key")
        self.cfg.update(patch)
        if self.cfg.get("stt.mic") != old_mic:
            self.listener.restart_mic()
        if (self.cfg.get("stt.model"), self.cfg.get("stt.engine")) != old_model:
            self.listener.model = None
            threading.Thread(target=self.listener.load_model, daemon=True).start()
        if "voice" in patch and patch["voice"].get("engine") in ("xtts", "silero", "elevenlabs"):
            self.tts.warmup()
        if "autostart" in patch:
            self.rpc_autostart(bool(patch["autostart"]))
        return self.cfg.public()

    def rpc_autostart(self, enabled):
        import sys
        from .config import ROOT
        exe = Path(sys.executable)
        pyw = exe.with_name("pythonw.exe") if exe.with_name("pythonw.exe").exists() else exe
        S.set_autostart(enabled, f'"{pyw}" "{ROOT / "run.py"}" --hidden')
        self.cfg.set("autostart", enabled)
        return enabled

    # --- AI
    def rpc_models(self, pid):
        prov = self.cfg.provider(pid)
        if prov.get("kind") == "bridge":
            return self.bridges.list_models(prov.get("agent"))
        return list_models(prov)

    def rpc_test_provider(self, pid):
        prov = self.cfg.provider(pid)
        t = time.time()
        txt = "".join(stream_chat(prov, [{"role": "user", "content": "Ответь одним словом: работает?"}],
                                  max_tokens=20, bridges=self.bridges))
        return {"ok": True, "text": txt.strip()[:200], "ms": int((time.time() - t) * 1000)}

    def rpc_chat_send(self, text):
        self.dialog_q.put(("chat", text, "text"))
        return True

    def rpc_chat_history(self):
        return self.chat_store.items[-150:]

    def rpc_chat_clear(self):
        self.chat_store.items = []
        self.chat_store.save()
        return True

    def rpc_chat_stop(self):
        self.stop_all()
        return True

    def rpc_execute(self, text):
        self.execute(text)
        return True

    # --- voice
    def rpc_say(self, text):
        self.say(text)
        return True

    THEMES = ["jarvis", "mark3", "nano", "emerald", "stealth"]

    def set_theme(self, name=None):
        """Switch the interface colour theme (cycles when name is None)."""
        if name not in self.THEMES:
            cur = self.cfg.get("theme", "jarvis")
            name = self.THEMES[(self.THEMES.index(cur) + 1) % len(self.THEMES)] if cur in self.THEMES else "jarvis"
        self.cfg.set("theme", name)
        self.emit("theme", name=name)
        return name

    VOICES = {"aidar": "Айдар", "eugene": "Евгений", "baya": "Бая", "kseniya": "Ксения", "xenia": "Ксения 2"}
    VOICE_ALIASES = {"айдар": "aidar", "евгений": "eugene", "женя": "eugene", "бая": "baya", "ксения": "kseniya",
                     "ксюша": "kseniya", "ксения два": "xenia", "мужской": "aidar", "женский": "baya"}

    def set_voice(self, speaker):
        """Switch the Silero voice (and to the Silero engine, the only one with these voices)."""
        sp = (speaker or "").strip().lower()
        sp = self.VOICE_ALIASES.get(sp, sp)
        if sp not in self.VOICES:
            raise ValueError("нет такого голоса: " + ", ".join(self.VOICES.values()))
        self.tts.stop()
        self.cfg.set("voice.silero_speaker", sp)
        if self.cfg.get("voice.engine") != "silero":
            self.cfg.set("voice.engine", "silero")
        self.emit("toast", title="Голос", text=self.VOICES[sp])
        return self.VOICES[sp]

    def rpc_set_theme(self, name):
        return self.set_theme(name)

    def rpc_notify(self, text, title="J.A.R.V.I.S."):
        self.notify(title, text)
        return True

    def rpc_debug_hear(self, text, voice="eugene"):
        """Testing aid: synthesize `text` with Silero and push it through the real recognition pipeline,
        as if the user had said it into the microphone. Returns what GigaAM/Whisper heard."""
        eng = self.tts.engines["silero"]
        if not eng.start():
            raise RuntimeError(eng.error)
        old = self.cfg.get("voice.silero_speaker")
        self.cfg.data["voice"]["silero_speaker"] = voice
        try:
            data, sr = eng.synth(text)
        finally:
            self.cfg.data["voice"]["silero_speaker"] = old
        from .audio import resample
        audio = resample(data, sr, 16000) * 0.8
        t = time.time()
        with self.listener.model_lock:
            heard, _ = self.listener.transcribe(audio)
        self.listener._handle(audio, False, "voice", False)
        return {"heard": heard, "stt_ms": int((time.time() - t) * 1000)}

    def rpc_tts_stop(self):
        self.stop_all()
        return True

    def rpc_voice_test(self, text=None):
        self.say(text or f"{self.greeting_phrase()} Так звучит мой голос. Надеюсь, вам нравится британский акцент.")
        return True

    def rpc_voices(self):
        return [
            {"id": "ru-RU-DmitryNeural", "title": "Дмитрий (русский, мужской)"},
            {"id": "ru-RU-SvetlanaNeural", "title": "Светлана (русский, женский)"},
            {"id": "en-US-AvaMultilingualNeural", "title": "Ava — женский, мягкий, русский с лёгким акцентом"},
            {"id": "en-US-EmmaMultilingualNeural", "title": "Emma — женский, звонкий, русский с акцентом"},
            {"id": "en-AU-WilliamMultilingualNeural", "title": "William — мультиязычный, говорит по-русски с акцентом"},
            {"id": "en-US-AndrewMultilingualNeural", "title": "Andrew — мультиязычный, русский с акцентом"},
            {"id": "en-US-BrianMultilingualNeural", "title": "Brian — мультиязычный, русский с акцентом"},
            {"id": "en-GB-RyanNeural", "title": "Ryan — британский английский (только английский текст)"},
            {"id": "en-GB-ThomasNeural", "title": "Thomas — британский английский (только английский текст)"},
        ]

    def rpc_xtts_reload(self):
        self.tts.engines["xtts"].reload()
        self.tts.engines["xtts"].error = None
        self.tts.warmup()
        return True

    def rpc_ref_voice(self, path=None, edge_voice=None):
        """Set reference for voice cloning: a wav file path, or regenerate from an edge British voice."""
        if path:
            src = Path(path)
            if src.suffix.lower() != ".wav":
                raise ValueError("Нужен файл .wav (6-30 секунд чистой речи)")
            shutil.copy(src, REF_DEFAULT)
        else:
            if REF_DEFAULT.exists():
                REF_DEFAULT.unlink()
            from . import tts as T
            eng = self.tts.engines["edge"]
            data, sr = eng.synth(T.REF_TEXT, voice=edge_voice or "en-GB-RyanNeural", rate="-4%", pitch="-2Hz")
            T.write_wav(REF_DEFAULT, data, sr)
        self.cfg.set("voice.xtts_ref", "")
        return self.rpc_xtts_reload()

    def rpc_mics(self):
        return list_mics()

    def rpc_listen(self, target="voice"):
        self.listener.listen_once(target)
        if self.cfg.get("stt.chime", True):
            self.player.sound("wake")
        return True

    def rpc_sleep(self, on):
        self.set_sleep(bool(on))
        return on

    # --- planner
    def rpc_tasks(self):
        return self.planner.sorted_tasks()

    def rpc_task_add(self, text, due=None, priority=2, notes="", parse=True):
        when = due
        if parse and not due:
            when, rest = parse_when(text)
            text = rest or text
        return self.planner.add_task(text, when, int(priority), notes=notes)

    def rpc_task_update(self, id, **kw):
        return self.planner.update_task(id, **kw)

    def rpc_task_delete(self, id):
        return self.planner.delete_task(id)

    def rpc_task_restore(self, task):
        return self.planner.restore_task(task)

    def rpc_tasks_clear_done(self):
        return self.planner.clear_done()

    def rpc_reminders(self):
        return sorted(self.planner.reminders.items, key=lambda r: (not r.get("active"), r["at"]))

    def rpc_reminder_add(self, text, at=None, phrase=None, repeat=None, alarm=False):
        if phrase:
            when, rest = parse_when(phrase)
            text = text or rest
        else:
            when = dt.datetime.fromisoformat(at)
        if not when:
            raise ValueError("Не понял время")
        return self.planner.add_reminder(text, when, repeat or None, bool(alarm))

    def rpc_reminder_update(self, id, **kw):
        self.planner.update_reminder(id, **kw)
        return True

    def rpc_reminder_delete(self, id):
        self.planner.delete_reminder(id)
        return True

    def rpc_conversation(self, on=None):
        return self.set_conversation(not self.conversation if on is None else bool(on))

    def rpc_timers(self):
        return [{**t, "left": self.planner.timer_left(t)} for t in self.planner.timers.items]

    def rpc_timer_add(self, seconds=None, label="", phrase=None):
        secs = int(seconds) if seconds else parse_duration(phrase or "")
        if not secs:
            raise ValueError("Не понял длительность")
        return self.planner.add_timer(secs, label)

    def rpc_timer_cancel(self, id=None):
        self.stop_alarm()
        return self.planner.cancel_timer(id)

    def rpc_timer_pause(self, id, pause=True):
        self.planner.pause_timer(id, pause)
        return True

    def rpc_stopwatch(self, action="get"):
        pl = self.planner
        {"start": pl.sw_start, "stop": pl.sw_stop, "reset": pl.sw_reset, "lap": pl.sw_lap}.get(action, lambda: None)()
        return {"value": pl.sw_value(), "running": pl.stopwatch["start"] is not None, "laps": pl.stopwatch["laps"]}

    def rpc_notes(self):
        return self.cfg.get("notes", [])

    def rpc_notes_set(self, notes):
        self.cfg.set("notes", notes)
        return True

    # --- commands & sources
    def rpc_commands(self):
        return self.commands.store.items

    def rpc_command_save(self, cmd):
        return self.commands.save_command(cmd)

    def rpc_command_delete(self, id):
        self.commands.delete_command(id)
        return True

    def rpc_command_run(self, id, vars=None):
        c = self.commands.get(id)
        if not c:
            raise ValueError("команда не найдена")
        self.run_command(c, vars or {})
        return True

    def rpc_sources(self):
        return self.commands.sources.items

    def rpc_source_save(self, src):
        return self.commands.save_source(src)

    def rpc_source_delete(self, id):
        self.commands.delete_source(id)
        return True

    def rpc_source_test(self, src, speak=False):
        val = self.commands.fetch_source(src)
        phrase = self.commands.source_phrase(src, val)
        if speak:
            self.say(phrase)
        return {"value": val if isinstance(val, (int, float, str, bool, type(None))) else json.dumps(val, ensure_ascii=False)[:2000],
                "phrase": phrase}

    # --- bridges
    def rpc_bridges(self):
        return {"agents": self.bridges.status(), "mcp": self.bridges.mcp_manual()}

    def rpc_bridge_set(self, agent, **kw):
        cur = self.cfg.get(f"bridges.{agent}") or {}
        cur.update(kw)
        self.cfg.set(f"bridges.{agent}", cur)
        return self.bridges.status()[agent]

    def rpc_bridge_ask(self, agent, prompt):
        self.bridge_task(agent, prompt)
        return True

    def rpc_bridge_stop(self, agent):
        return self.bridges.stop(agent)

    def rpc_bridge_mcp(self, agent):
        return self.bridges.mcp_install(agent)

    # --- system
    def rpc_stats(self):
        return S.system_stats()

    def rpc_windows(self):
        return [{"title": w["title"], "proc": w["proc"]} for w in S.list_windows()]

    def rpc_window(self, title, action):
        return S.window_action(title, action)

    def rpc_apps(self, q=""):
        q = q.lower()
        return [a for a in self.apps.apps if q in a["name"].lower()][:300]

    def rpc_apps_rebuild(self):
        threading.Thread(target=self.apps.rebuild, daemon=True).start()
        return True

    def rpc_app_launch(self, name):
        return S.launch_app(name, self.apps, self.cfg.get("aliases"))

    def rpc_geocode(self, city):
        g = info.geocode(city)
        if not g:
            raise ValueError("Город не найден")
        self.cfg.set("location", g)
        return g

    def rpc_weather(self):
        loc = self.cfg.get("location")
        return {**info.weather(loc["lat"], loc["lon"]), "city": loc["city"]}

    def rpc_open(self, target):
        return S.open_target(target)

    # --- updates
    def rpc_update_check(self):
        try:
            return self.updater.check()
        except Exception as e:
            return {"current": current_version(), "error": str(e), "available": False}

    def rpc_update_apply(self):
        return self.update_now()

    def update_now(self, speak=False):
        def done(ok, what):
            if ok:
                self.say("Обновление установлено, сэр. Перезапускаюсь.")
                self.after_speech(self._restart, 0.3)
            elif speak:
                self.say("Вы уже на последней версии, сэр." if what == "уже последняя версия"
                         else "Не удалось обновиться, сэр. Подробности в журнале.")
        started = self.updater.apply_async(done)
        if started and speak:
            self.say("Скачиваю обновление.")
        return started

    def _restart(self):
        self.updater.restart()
        self.ui("quit")

    def _auto_update_check(self):
        """Once per start: tell the user about a new version (once per version)."""
        time.sleep(25)
        try:
            info = self.updater.check()
        except Exception as e:
            self.log(f"Проверка обновлений: {e}", "warn")
            return
        if not info.get("available"):
            return
        self.emit("update", status="available", **info)
        if self.cfg.get("update.notified") != info["latest"]:
            self.cfg.set("update.notified", info["latest"])
            self.notify("Доступно обновление Джарвиса",
                        f"Версия {info['latest']}. Настройки → Обновления → «Обновить», или скажите «Джарвис, обновись».")
            self.say("Сэр, доступно обновление. Скажите «обновись», когда будет удобно.")

    # --- first-run setup
    def rpc_setup_detect(self):
        import requests
        ollama = {"running": False, "models": []}
        try:
            r = requests.get("http://localhost:11434/api/tags", timeout=2)
            ollama = {"running": True, "models": [m["name"] for m in r.json().get("models", [])]}
        except Exception:
            pass
        lmstudio = False
        try:
            lmstudio = requests.get("http://localhost:1234/v1/models", timeout=1.5).ok
        except Exception:
            pass
        return {"cuda": cuda_available(), "mics": list_mics(), "ollama": ollama, "lmstudio": lmstudio,
                "bridges": self.bridges.status(), "xtts": XttsEngine.available(),
                "gpu": S.gpu_info()}

    def rpc_setup_finish(self, data):
        self.rpc_config_set(data)
        self.cfg.set("setup_done", True)
        self.say(f"Настройка завершена. {self.greeting_phrase()} Я к вашим услугам. "
                 "Просто скажите «Джарвис» и команду.")
        return True
