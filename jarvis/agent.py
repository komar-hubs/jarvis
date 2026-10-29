"""Two AI helpers that work only when asked (no background requests):

- "Джарвис, сделай за меня: …" — the AI does a multi-step task in any program by itself: reads the window's
  buttons and fields as text (cheap, no screenshots), clicks and types, checks the result, up to 12 steps; before
  each click the marker rings the target, so the user sees every step. "стоп" stops it. Hard rules in the prompt:
  no passwords or card data, no paying, no sending or deleting without the user's "да".
- "Джарвис, подскажи по игре: что собрать?" — one look at the game screen by a vision model: build, where to go,
  what an icon means. One request per question."""
import threading

AGENT_RULES = ("[Режим «сделай за меня». Выполни задачу сам на компьютере пользователя, шаг за шагом, тегами "
               "инструментов. Порядок: ui_elements → действие (click_element, type_into, hotkey, open_app…) → снова "
               "ui_elements, чтобы проверить результат. Между шагами ничего не пиши словами — только теги. Скриншоты и "
               "vision_click — только если без них никак. Никогда не вводи пароли и данные карт, ничего не покупай и не "
               "оплачивай, не отправляй сообщения и письма, ничего не удаляй — в таких местах остановись и спроси "
               "пользователя. Когда задача выполнена или выполнить её нельзя — ответь одной короткой фразой без тегов.]")


def register(R, core):
    @R(r"^(?:сделай\s+за\s+меня|сделай\s+сам|выполни\s+задачу|автопилот|режим\s+агента)\s*[,:]?\s+(.+)$")
    def _agent(m, t):
        task = m.group(1).strip()
        if len(task) < 4:
            return False
        core.say("Принято, сэр. Делаю — скажите «стоп», если что-то пойдёт не так.")
        core.spawn_chat(task, source="voice", extra=AGENT_RULES, agent=True)
        return True

    @R(r"^(?:подскажи|помоги|посоветуй)\s+(?:мне\s+)?(?:по\s+игре|в\s+игре|с\s+игрой)\s*[,:]?\s*(.*)$|"
       r"^(?:что|как|куда|где|кого|чем)\s+(.+?)\s+в\s+(?:этой\s+)?игре$|"
       r"^(?:что\s+мне\s+(?:собрать|купить|прокачать|качать|делать\s+дальше)|куда\s+мне\s+(?:идти|дальше))(.*)$")
    def _game(m, t):
        return game_help(t)

    # a question asked while a game is on the screen is about the game: "где шкафчик со снаряжением?",
    # "почему здесь не получается пройти?" went to the chat, which could not see the screen
    @R(r"^(?:где|куда)\s+(?!я\s+(?:видел|видела|был))\S.{2,}$|"
       r"^(?:как|почему|зачем|что|чем)\b.*\b(?:здесь|тут|пройти|победить|убить|открыть|найти|попасть|залезть|забраться|"
       r"решить|собрать|купить|прокачать|делать|идти|достать|взять|использовать|сломать|не\s+получается|не\s+могу)\b.*$")
    def _game_question(m, t):
        from . import system as S
        from .llm import vision_supported
        if not core.extras.is_game(S.active_exe()) or not vision_supported(core.cfg.provider()):
            return False                  # not in a game, or no eyes: the ordinary chat answers
        return game_help(t)

    def game_help(t):
        q = t.strip()
        from . import system as S
        title = S.active_title() or "игра"
        prov = core.cfg.provider()
        from .llm import vision_supported
        if not vision_supported(prov):
            return ("Для подсказок по игре нужен ИИ, который видит картинки, сэр: например, Gemini, GPT-4o или Claude. "
                    "Выберите его в Настройки → Искусственный интеллект.")

        def go():
            from . import mouse as MS
            ans = MS.describe_screen(core, f"Пользователь играет: окно «{title}». Посмотри на этот скриншот игры и ответь на "
                                           f"вопрос как опытный игрок: {q}. Коротко и конкретно: что сделать, куда идти, "
                                           f"что собрать. Если по скриншоту не понять — так и скажи.")
            core.say(ans or "Не разглядел, сэр. Попробуйте спросить, когда на экране будет нужное место.")
        threading.Thread(target=go, daemon=True, name="game-helper").start()
        return True

