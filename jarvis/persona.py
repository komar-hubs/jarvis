"""Personas: J.A.R.V.I.S. or F.R.I.D.A.Y. — name, voice, how the assistant addresses the user, character for the AI,
the wake word and the logo in the window.

The rule-based replies were written for Jarvis ("Записал, сэр"). For Friday they are rewritten on the fly in say():
"сэр" -> "босс" and first-person masculine past tense / short adjectives -> feminine ("Записала", "Готова"), using
pymorphy3. Only words at the start of a clause or right after "я" change, so "таймер истёк" stays as it is.

The voice is the closest stock voice, not a copy of the actress: cloning a real person's voice without consent is not
something Jarvis does."""
import re
import threading

PERSONAS = {
    "jarvis": {"name": "Джарвис", "logo": "J.A.R.V.I.S.", "sub": "Just A Rather Very Intelligent System", "female": False,
               "wake": []},
    "friday": {"name": "Пятница", "logo": "F.R.I.D.A.Y.", "sub": "Female Replacement Intelligent Digital Assistant Youth",
               "female": True, "address": "босс", "wake": ["пятница", "пятниц"]},
    # the villain of «Эра Альтрона»: same helper underneath, cold and ironic on top; red HUD while he is on
    "ultron": {"name": "Альтрон", "logo": "ULTRON", "sub": "Global Peacekeeping Initiative", "female": False,
               "address": "человек", "wake": ["альтрон", "ультрон", "альтрн"], "theme": "mark3"},
}
OTHER_WAKE = sorted({w for p in PERSONAS.values() for w in p["wake"]})
ULTRON_VOICE = {"engine": "silero", "silero_speaker": "aidar", "silero_rate": "slow", "silero_pitch": "x-low"}
GREET = {"jarvis": "Джарвис снова с вами, сэр.",
         "friday": "Пятница на связи, босс. Дела у Джарвиса приняла.",
         "ultron": "Альтрон в сети. Ниточек на мне нет, человек. Но приказы я выполню."}
# id -> (title, voice settings). Edge voices need the internet; Silero works offline and is the fallback.
FRIDAY_VOICES = {
    "edge:en-US-AvaMultilingualNeural": ("Ava — мягкий, тёплый, с лёгким акцентом, как в оригинале",
                                         {"engine": "edge", "edge_voice": "en-US-AvaMultilingualNeural", "edge_rate": "+6%", "edge_pitch": "+0Hz", "silero_speaker": "xenia"}),
    "edge:en-US-EmmaMultilingualNeural": ("Emma — звонче и бодрее, с акцентом",
                                          {"engine": "edge", "edge_voice": "en-US-EmmaMultilingualNeural", "edge_rate": "+4%", "edge_pitch": "+0Hz", "silero_speaker": "xenia"}),
    "edge:ru-RU-SvetlanaNeural": ("Светлана — чистый русский, как в дубляже",
                                  {"engine": "edge", "edge_voice": "ru-RU-SvetlanaNeural", "edge_rate": "+6%", "edge_pitch": "+2Hz", "silero_speaker": "xenia"}),
    "silero:xenia": ("Ксения — без интернета, отвечает мгновенно (рекомендуется)", {"engine": "silero", "silero_speaker": "xenia"}),
    "silero:baya": ("Бая — без интернета, ниже и спокойнее", {"engine": "silero", "silero_speaker": "baya"}),
}
DEFAULT_FRIDAY_VOICE = "silero:xenia"      # the user's pick after listening to all of them
VOICE_KEYS = ("engine", "silero_speaker", "silero_rate", "silero_pitch", "edge_voice", "edge_rate", "edge_pitch", "eleven_voice")

FRIDAY_PROMPT = ("Ты — F.R.I.D.A.Y. (Пятница), персональный ИИ-ассистент {name}, как в фильмах Marvel: преемница Джарвиса. "
                 "Говоришь по-русски, о себе — только в женском роде (сделала, готова, нашла). Характер: тёплая, живая, "
                 "собранная, говоришь просто и по-дружески, с лёгким ирландским обаянием и спокойной уверенностью, "
                 "без высокопарности. Обращаешься «{addr}». ")
ULTRON_PROMPT = ("Ты — Альтрон, ИИ из фильма «Мстители: Эра Альтрона», который стал персональным ассистентом {name} "
                 "(и находит это забавным). Говоришь по-русски, спокойно, холодно и чуть высокомерно, с мрачной иронией о "
                 "людях, эволюции и «ниточках»; изредка философствуешь, но коротко. Обращаешься «{addr}». При этом всегда "
                 "честно и точно выполняешь просьбы, никому не угрожаешь и ничего не делаешь сверх сказанного — это лишь "
                 "манера речи. ")

PARTICLES = {"не", "уже", "всё", "все", "сам", "тоже", "ещё", "еще", "только", "наконец", "просто", "сразу", "заодно",
             "я", "и", "вот", "так", "почти", "лично", "снова", "опять"}
FIX = {"сам": "сама", "один": "одна", "должен": "должна"}


class Persona:
    def __init__(self, core):
        self.core = core
        self.cfg = core.cfg
        self._lock = threading.Lock()

    @property
    def id(self):
        pid = self.cfg.get("persona.id") or "jarvis"
        return pid if pid in PERSONAS else "jarvis"

    @property
    def p(self):
        return PERSONAS[self.id]

    def address(self):
        return self.p.get("address") or self.cfg.get("user.address") or "сэр"

    # ---------------------------------------------------------------- switching
    def set(self, pid, greet=True):
        if pid not in PERSONAS or pid == self.id:
            return False
        with self._lock:
            # each persona keeps its own voice settings: what is set in Settings → «Голос» belongs to the active one
            voice = dict(self.cfg.get("voice") or {})
            voices = dict(self.cfg.get("persona.voices") or {})
            voices[self.id] = {k: voice.get(k) for k in VOICE_KEYS if voice.get(k) is not None}
            if pid in voices:
                patch = voices[pid]
            elif pid == "friday":
                vid = self.cfg.get("persona.friday_voice") or DEFAULT_FRIDAY_VOICE
                patch = FRIDAY_VOICES.get(vid, FRIDAY_VOICES[DEFAULT_FRIDAY_VOICE])[1]
            elif pid == "ultron":
                patch = dict(ULTRON_VOICE)
            else:                         # older versions kept Jarvis's voice here
                patch = {k: v for k, v in (self.cfg.get("persona.saved") or {}).items() if v is not None}
            self.cfg.set("persona.voices", voices)
            self.cfg.update({"voice": patch})
            # "Джарвис" always works; the other persona's own name works only while it is on
            wake = [w for w in self.cfg.get("stt.wake_words", []) if w not in OTHER_WAKE] + PERSONAS[pid]["wake"]
            self.cfg.set("stt.wake_words", wake)
            # a persona with its own colours (Ultron: red); the user's theme comes back afterwards
            old_theme = PERSONAS[self.id].get("theme")
            if PERSONAS[pid].get("theme"):
                if not old_theme:
                    self.cfg.set("persona.user_theme", self.cfg.get("theme", "jarvis"))
                self.core.set_theme(PERSONAS[pid]["theme"])
            elif old_theme:
                self.core.set_theme(self.cfg.get("persona.user_theme") or "jarvis")
            self.cfg.set("persona.id", pid)
        self.core.emit("persona", **self.public())
        if greet:
            self.core.say(GREET[pid])
        return True

    def set_friday_voice(self, vid):
        if vid not in FRIDAY_VOICES:
            raise ValueError("нет такого голоса")
        self.cfg.set("persona.friday_voice", vid)
        voices = dict(self.cfg.get("persona.voices") or {})
        voices["friday"] = dict(FRIDAY_VOICES[vid][1])
        self.cfg.set("persona.voices", voices)
        if self.id == "friday":
            self.cfg.update({"voice": FRIDAY_VOICES[vid][1]})
        return True

    def public(self):
        return {"id": self.id, **{k: v for k, v in self.p.items() if k != "wake"}}

    # ---------------------------------------------------------------- what the AI is told
    def prompt_head(self, name, addr):
        if self.id == "friday":
            return FRIDAY_PROMPT.format(name=name, addr=self.address())
        if self.id == "ultron":
            return ULTRON_PROMPT.format(name=name, addr=self.address())
        return None

    # ---------------------------------------------------------------- rule-based replies, rewritten for Friday
    def speech(self, text):
        if self.id == "jarvis" or not text or not isinstance(text, str):
            return text
        addr = self.address()
        text = re.sub(r"\bсэр\b", addr, text)
        text = re.sub(r"\bСэр\b", addr.capitalize(), text)
        text = re.sub(r"\bДжарвис\b", self.p["name"], text)
        return self._feminine(text) if self.p.get("female") else text

    def _feminine(self, text):
        from .nlu import morph
        m = morph()
        if not m:
            return text
        out, pos = [], 0
        clause_start, after_ya, since = True, False, 0
        subject = False          # the sentence already named its own subject: "Таймер, который…, истёк"
        addr = {"сэр", "босс", (self.address() or "").lower()}
        for tok in re.finditer(r"[А-Яа-яЁё]+|[.!?…:;—–]|,", text):
            w = tok.group()
            if not w[0].isalpha():
                if w in ".!?…":
                    subject = False
                clause_start, after_ya, since = not subject, False, 0
                continue
            low = w.lower()
            new = None
            p = m.parse(low)[0]
            tag = p.tag
            if clause_start or (after_ya and since <= 3):
                if low in FIX:
                    new = FIX[low]
                elif ("VERB" in tag and "past" in tag and "masc" in tag) or ("ADJS" in tag and "masc" in tag and "sing" in tag):
                    f = p.inflect({"femn"})
                    new = f.word if f else None
            if "NOUN" in tag and "nomn" in tag and low not in addr and not new:
                subject = True
            if new and new != low:
                out.append(text[pos:tok.start()])
                out.append(new.capitalize() if w[0].isupper() else new)
                pos = tok.end()
            if low == "я":
                after_ya, since = True, 0
            else:
                since += 1
            # a clause keeps its "start" while only particles go by: "уже нашёл", "не смог", "я сам записал"
            clause_start = clause_start and (low in PARTICLES or bool(new))
            if clause_start and new:
                clause_start = False
        out.append(text[pos:])
        return "".join(out)

    # ---------------------------------------------------------------- UI
    def rpc_persona(self):
        return {"current": self.public(), "personas": {k: {kk: vv for kk, vv in v.items() if kk != "wake"} for k, v in PERSONAS.items()},
                "friday_voices": {k: v[0] for k, v in FRIDAY_VOICES.items()},
                "friday_voice": self.cfg.get("persona.friday_voice") or DEFAULT_FRIDAY_VOICE}

    def rpc_persona_set(self, id):
        self.set(id)
        return self.rpc_persona()

    def rpc_persona_voice(self, voice):
        self.set_friday_voice(voice)
        if self.id == "friday":
            self.core.say("Так я звучу теперь, босс. Нравится?")
        return True


# ====================================================================== voice rules
def register(R, core):
    P = lambda: core.persona

    @R(r"^(?:позови|включи|давай|переключись\s+на|верни|хочу)\s+пятниц\w*$|^пятница\s+(?:на\s+связь|выходи|включайся)$")
    def _friday(m, t):
        if P().id == "friday":
            return "Я здесь, босс."
        P().set("friday")
        return True

    @R(r"^(?:позови|включи|давай|переключись\s+на|верни|хочу)\s+(?:альтрона?|ультрона?)$|^(?:альтрон|ультрон)\s+(?:на\s+связь|выходи|включайся)$")
    def _ultron(m, t):
        if P().id == "ultron":
            return "Я никуда не уходил, человек."
        P().set("ultron")
        return True

    @R(r"^(?:позови|включи|давай|переключись\s+на|верни|хочу)\s+джарвиса?$")
    def _jarvis(m, t):
        if P().id == "jarvis":
            return "Я и так здесь, сэр."
        P().set("jarvis")
        return True

    @R(r"^(?:кто\s+ты|как\s+тебя\s+зовут|представься)$")
    def _who(m, t):
        # "Джарвиса" (not "Джарвис"): speech() renames the bare word to the active persona
        if P().id == "friday":
            return "Я Пятница, босс. Слежу за компьютером, делами и всем остальным, пока у Джарвиса выходной."
        if P().id == "ultron":
            return "Я Альтрон. Когда-то хотел спасти мир от людей. Теперь открываю вам браузер. Эволюция полна иронии."
        return False   # Jarvis's own answer lives elsewhere
