"""«Что делает Джарвис»: every background feature in one table — what it does, why it is there, where its data goes,
and a switch. An assistant listens to the microphone, looks at the screen and presses keys, so people are rightly
careful; the table answers "what is it doing behind my back?" in one place, and every row can be turned off.

Each row maps to a config key. "on"/"off" are the values written by the switch; "default" is the value when the
key is not in the config (a callable: computed, e.g. screen memory is off by default on weak laptops)."""
from . import system as S

# id, title, what it does, why, data, config key, on value, off value, default
ROWS = [
    ("listen", "Прослушивание микрофона", "Постоянно слушает микрофон, но реагирует только на «Джарвис».",
     "Голосовое управление без кнопок.", "Распознаётся на этом ПК, звук никуда не отправляется и не сохраняется.",
     "stt.always_listen", True, False, True, "Выключено — слушает только по Ctrl+Alt+J."),
    ("voice", "Только мой голос", "Выключение, удаление, отправку сообщений выполняет только по вашему голосу.",
     "Домашние и гости не выключат ПК и не напишут от вашего имени.", "Хранится только отпечаток голоса (256 чисел), записи нет; всё на этом ПК.",
     "voiceid.enabled", True, False, False, "Включается фразой «Джарвис, запомни мой голос»."),
    ("heard", "Журнал услышанного", "Записывает распознанные фразы и сработало ли имя (data/heard.log).",
     "Понять, почему Джарвис не отреагировал или ослышался.", "Текстовый файл на этом ПК, до 400 КБ.",
     "stt.heard_log", True, False, True, ""),
    ("screen", "Память экрана", "Раз в минуту распознаёт текст активного окна и хранит 7 дней.",
     "«Что я видел вчера про…», поиск по прошлому экрану.", "База и миниатюры в data/, только на этом ПК.",
     "screenmem.enabled", True, False, lambda cfg: not S.lite(cfg), ""),
    ("usage", "Учёт времени в программах", "Считает, сколько минут в какой программе и игре.",
     "Статистика дня, итоги недели, «сколько я играл».", "data/usage.json на этом ПК.",
     "extras.usage_tracking", True, False, True, ""),
    ("clip", "История копирования", "Помнит 25 последних скопированных текстов.",
     "«Что я копировал», вставить прошлое.", "Только в памяти, на диск не пишется; пароли не сохраняются.",
     "extras.clipboard_history", True, False, True, ""),
    ("msg", "Счётчик сообщений", "Смотрит заголовки окон Telegram, Discord, WhatsApp.",
     "Сказать «2 новых сообщения в Telegram».", "Только число из заголовка окна, текст не читается.",
     "extras.messengers", True, False, True, ""),
    ("phone", "Уведомления телефона", "Получает уведомления с телефона через KDE Connect и называет их.",
     "Не тянуться к телефону во время игры или работы.", "Текст читается вслух только после «да»; по Wi-Fi, без интернета.",
     "phone.announce", True, False, True, ""),
    ("calls", "Звонки телефона", "Говорит, кто звонит, и умеет принять звонок голосом.",
     "Не пропустить звонок в наушниках.", "Номер и имя из контактов телефона, локально.",
     "phone.calls", True, False, True, ""),
    ("phonelink", "iPhone через «Связь с телефоном»", "Читает уведомления приложения Windows «Связь с телефоном».",
     "Уведомления и звонки iPhone, как у Android через KDE Connect.", "Только уведомления этого приложения, на этом ПК; другие программы не читаются.",
     "phone.phonelink", True, False, True, "Когда Android подключён через KDE Connect, не работает: иначе были бы дубли."),
    ("away", "«Ушёл — вернулся»", "Если телефон пропал из Wi-Fi больше чем на 3 минуты — пауза музыки.",
     "Встретить сводкой, что пришло, пока вас не было.", "Проверка связи с телефоном в домашней сети.",
     "phone.presence", True, False, True, ""),
    ("lock", "Блокировка ПК, когда ушли", "Вместе с «Ушёл — вернулся» блокирует компьютер.",
     "Никто не сядет за ваш ПК.", "Обычная блокировка Windows (Win+L).",
     "phone.presence_lock", True, False, True, ""),
    ("watch", "Сторож ПК", "Предупреждает о нехватке памяти, перегреве и месте на диске.",
     "Узнать до того, как станет плохо.", "Проверки на этом ПК, ничего не отправляется.",
     "extras.watchdog", True, False, True, ""),
    ("sentinel", "Сторож безопасности", "Замечает новое в автозагрузке, неподписанные программы из Temp, майнеры, правку hosts, "
     "выключенный Защитник.", "Заметить вирус или чужую программу.", "Проверки на этом ПК, ничего не отправляется.",
     "sentinel.enabled", True, False, True, ""),
    ("ram", "Очистка памяти", "Когда занято больше 90% памяти, ужимает фоновые программы (их никто не закрывает).",
     "Игра и браузер не тормозят на слабом ПК.", "Только на этом ПК. Программы со звуком и игры не трогаются.",
     "background.ram", True, False, True, ""),
    ("clean", "Чистка временных файлов", "Когда на диске меньше 8 ГБ, удаляет старый мусор из временных папок.",
     "Место на диске.", "Только временные файлы Windows и браузеров.",
     "background.clean", True, False, True, ""),
    ("mail", "Разбор почты", "Раз в полчаса убирает старые рассылки в папку Newsletters.",
     "Чистый «Входящие».", "Ваш почтовый ящик по IMAP; письма никуда не отправляются.",
     "background.mail_sort", True, False, False, ""),
    ("sort", "Сортировка «Загрузок»", "Раскладывает «Загрузки» по папкам раз в 6 часов.",
     "Порядок в загрузках.", "Файлы остаются на ПК; «верни загрузки» всё возвращает.",
     "background.sort_downloads", True, False, False, ""),
    ("backup", "Резервная копия", "Раз в неделю копирует настройки, команды и заметки.",
     "Ничего не потерять при сбое.", "В «Документы\\Jarvis backups» на этом ПК.",
     "backup.auto", True, False, True, ""),
    ("update", "Проверка обновлений", "При запуске узнаёт, есть ли новая версия.",
     "Получать исправления.", "Читает один файл с номером версии с GitHub, о вас ничего не отправляет.",
     "update.auto_check", True, False, True, ""),
    ("duck", "Приглушение музыки", "Пока Джарвис говорит, музыка и видео становятся тише.",
     "Слышно ответ.", "Громкость программ на этом ПК; после ответа возвращается.",
     "duck_media", True, False, True, ""),
    ("audio", "Сторож звука", "Замечает, если звук выключился или резко просел, и пишет в журнал, кто это сделал.",
     "Найти причину пропадающего звука.", "Строка в журнале на этом ПК.",
     "audio_watch", True, False, True, ""),
    ("game", "Голос в играх", "Пока игра на экране, фразы без «Джарвис» нажимают клавиши.",
     "Управлять игрой голосом.", "Локально, без ИИ и интернета; только в играх.",
     "game.voice.mode", "auto", "off", "auto", ""),
    ("break", "Напоминания о перерывах", "После 1,5 часа без паузы предлагает размяться (в игре — тихой карточкой).",
     "Глаза и спина.", "Ничего не хранится.",
     "extras.break_min", 90, 0, 90, ""),
    ("audiomem", "Память звука", "Держит последние минуты звука компьютера (колонки, наушники) в оперативной памяти.",
     "«Что он сказал?», «перескажи последние 5 минут» видео, стрима, созвона.",
     "Только в оперативной памяти, на диск не пишется; распознаётся на этом ПК и только когда спросите.",
     "audiomem.enabled", True, False, True, "Голос самого Джарвиса не сохраняется."),
    ("files", "Поиск по файлам", "Читает текст документов на Рабочем столе, в Документах и Загрузках.",
     "«Найди файл про…», поиск по смыслу, а не только по названию.", "Индекс data/files.db на этом ПК.",
     "filesearch.enabled", True, False, lambda cfg: not S.lite(cfg), "Во время игры ставится на паузу."),
    ("homework", "Домашка из чата класса", "Ищет задания в уведомлениях чатов класса с телефона.",
     "Домашка записывается сама, к нужному уроку.", "Только чаты, отмеченные во вкладке «Учёба»; без ИИ, на этом ПК.",
     "school.auto_hw", True, False, True, ""),
    ("hud", "HUD поверх окон", "Маленькая плашка в углу, пока Джарвис слушает или говорит.",
     "Видно, что он услышал.", "Ничего не хранится.",
     "overlay", True, False, True, ""),
    # not switches: what only ever happens on request
    ("ai", "Запросы к ИИ", "Только когда вы сами спросили или попросили.",
     "Ответы, тексты, подсказки по игре.", "Ollama — на этом ПК; облачный ИИ — по вашему ключу, только текст запроса.",
     None, None, None, None, "Фоновых запросов к ИИ нет."),
    ("keys", "Нажатия и мышь", "Только по вашей команде: «нажми», «сделай за меня», макросы, голос в играх.",
     "Делать дела за вас.", "Клавиатура и мышь не записываются, кроме записи макроса по вашей команде.",
     None, None, None, None, "Сообщения отправляются только после «да»."),
    ("trans", "Переводчик экрана", "По вашей просьбе распознаёт текст окна и рисует перевод поверх.",
     "Игры, программы и сайты на чужом языке.", "Распознанный текст (не картинка) уходит в Google Переводчик; без сети — в ваш ИИ.",
     None, None, None, None, "Ctrl+Alt+T — один раз, Ctrl+Alt+L — на лету."),
    ("keysdata", "Ключи и пароли", "Ключи ИИ и пароли почты хранятся у вас.",
     "Работа почты и облачного ИИ.", "data/config.json на этом ПК; в обновления и резервные копии ключи не попадают.",
     None, None, None, None, ""),
]


class Features:
    def __init__(self, core):
        self.core = core
        self.cfg = core.cfg

    def value(self, row):
        key, default = row[5], row[8]
        if not key:
            return None
        return self.cfg.get(key, default(self.cfg) if callable(default) else default)

    def is_on(self, row):
        v = self.value(row)
        if row[5] is None:
            return None
        if isinstance(row[6], bool):
            return bool(v)
        if isinstance(row[6], int):
            return bool(v)                      # break_min: any number above 0
        return v != row[7]

    def rpc_features(self):
        return [{"id": r[0], "title": r[1], "what": r[2], "why": r[3], "data": r[4], "note": r[9],
                 "switch": r[5] is not None, "on": self.is_on(r),
                 "badge": "" if r[5] else ("на этом ПК" if r[0] == "keysdata" else "по запросу")} for r in ROWS]

    def rpc_feature_set(self, id, on):
        row = next((r for r in ROWS if r[0] == id), None)
        if not row or not row[5]:
            raise ValueError("эту функцию нельзя выключить")
        key = row[5]
        if key == "voiceid.enabled" and on and not self.cfg.get("voiceid.print"):
            raise ValueError("Сначала скажите «Джарвис, запомни мой голос»: я попрошу три фразы")
        if key == "extras.break_min" and on:
            val = int(self.cfg.get("extras.break_min_prev", 90) or 90)
        else:
            val = row[6] if on else row[7]
        if key == "extras.break_min" and not on:
            cur = int(self.cfg.get("extras.break_min", 90) or 0)
            if cur:
                self.cfg.set("extras.break_min_prev", cur)
        if key == "game.voice.mode":
            c = self.cfg.get("game.voice") or {}
            c["mode"] = val
            self.cfg.set("game.voice", c)
            try:
                self.core.gamevoice._act = (0.0, False, "")
            except Exception:
                pass
        else:
            self.cfg.set(key, val)
        self.core.log(f"«{row[1]}»: {'включено' if on else 'выключено'} (Настройки → Что делает Джарвис)")
        return self.rpc_features()


# «открой настройки голоса / игр / приватности»: the Settings tab at that section (ui/settings_nav.js)
SECTIONS = [(r"голос\w*|озвучк\w*|ударени\w*|личност\w*", "voice"), (r"слух\w*|микрофон\w*|распознаван\w*|вызов\w*", "hear"),
            (r"ии|интеллект\w*|нейросет\w*|ии-?модул\w*", "ai"), (r"игр\w*|клип\w*", "games"), (r"телефон\w*", "phone"),
            (r"дел\w*|забот\w*|напоминани\w*|макрос\w*|памят\w*\s+экрана", "help"), (r"компьютер\w*|пк|обслуживани\w*|копи\w*", "pc"),
            (r"обновлени\w*", "upd"), (r"приватност\w*|безопасност\w*|что\s+ты\s+делаешь", "priv"), (r"основн\w*|профил\w*|темы?", "main")]


def register(R, core):
    import re

    @R(r"^(?:открой|покажи|перейди\s+в|зайди\s+в)\s+настройк\w+\s+(.+)$|^(?:открой|покажи)\s+(.+?)\s+в\s+настройках$")
    def _section(m, t):
        what = (m.group(1) or m.group(2)).strip()
        sid = next((sid for rx, sid in SECTIONS if re.fullmatch(rf"(?:раздел\s+)?(?:{rx})", what)), None)
        if not sid:
            return False
        core.show_tab("settings")
        core.emit("settings_section", id=sid)
        return True
