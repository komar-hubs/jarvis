"""Voice control in games: a phrase said while a game is on the screen presses keys or mouse buttons — no "Джарвис",
no AI, no network. "Люмос" -> 1, "щит" -> Q held for half a second, "рывок" -> Shift+W.

- Only the whole phrase counts (fuzzy, so "лумос" still works), and only while a game is in front (mode "auto"), so
  ordinary talk does not press anything. Mode "on" works everywhere, "off" turns it off.
- The keys go out as scan codes with a real hold (60 ms by default): games read scan codes and miss a 12 ms tap.
- While a game is in front the end of a phrase is detected sooner (350 ms of silence instead of ~0.6 s).
- Phrases for invented words are best taught by voice: "Записать с голоса" in Settings remembers exactly how the
  recogniser hears the word.

Config game.voice: {"mode": "auto"|"on"|"off", "end_ms": 350, "binds": [{"id", "phrases": [..], "keys": "q", "hold": 60}]}.
Anti-cheat in some online games ignores programmatic input: nothing can be done about that from here."""
import queue
import re
import threading
import time
import uuid

from . import system as S

PRESETS = {
    "spells": [  # an example: the keys are to be changed to the game's own
        {"phrases": ["люмос"], "keys": "1"}, {"phrases": ["акцио"], "keys": "2"},
        {"phrases": ["протего"], "keys": "3"}, {"phrases": ["экспеллиармус", "экспелиармус"], "keys": "4"},
    ],
    "shooter": [
        {"phrases": ["перезарядка", "перезаряди"], "keys": "r"}, {"phrases": ["граната"], "keys": "g"},
        {"phrases": ["аптечка", "лечись"], "keys": "4"}, {"phrases": ["карта"], "keys": "m"},
    ],
}


def norm(text, wake=()):
    s = (text or "").lower().replace("ё", "е")
    s = re.sub(r"[^\w\s-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for w in wake:                         # "Джарвис, люмос" is the same spell
        s = re.sub(rf"^{re.escape(w.lower())}\w*\s+", "", s)
    return s


def threshold(phrase):
    """One misheard letter is forgiven ("шит" for "щит", "лумос" for "люмос"); a different word is not ("что")."""
    n = len(phrase)
    return 66 if n <= 3 else 80 if n <= 5 else 78 if n <= 9 else 75


class GameVoice:
    def __init__(self, core):
        self.core = core
        self.cfg = core.cfg
        self._act = (0.0, False, "")
        self.learning = None              # (bind id, until): the next phrase becomes a variant of that bind
        self.last = {}                    # bind id -> time of the last press (a doubled recognition presses once)
        self.q = queue.Queue()
        threading.Thread(target=self._worker, daemon=True, name="game-keys").start()

    # ---------------------------------------------------------------- state
    def conf(self):
        return self.cfg.get("game.voice") or {}

    def binds(self):
        return [b for b in (self.conf().get("binds") or []) if b.get("phrases") and b.get("keys")]

    def active(self):
        """(on, game exe). Cached for a second: it is asked for every 30 ms audio frame."""
        t, on, exe = self._act
        if time.time() - t < 1.0:
            return on, exe
        mode = self.conf().get("mode", "auto")
        exe = ""
        try:
            exe = S.active_exe()
        except Exception:
            pass
        if mode == "off" or not self.binds():
            on = False
        elif mode == "on":
            on = True
        else:
            try:
                on = self.core.extras.is_game(exe)
            except Exception:
                on = False
        self._act = (time.time(), on, exe)
        return on, exe

    def fast(self):
        """For the recogniser: True while phrases should end sooner."""
        return self.active()[0]

    def end_ms(self):
        return int(self.conf().get("end_ms", 350))

    # ---------------------------------------------------------------- the phrase
    def match(self, text):
        from rapidfuzz import fuzz
        t = norm(text, self.cfg.get("stt.wake_words", []))
        if not t or len(t) > 40:
            return None, 0
        best, score = None, 0
        for b in self.binds():
            for p in b["phrases"]:
                pn = norm(p)
                if not pn:
                    continue
                sc = 100 if t == pn else fuzz.ratio(t, pn)
                if sc >= threshold(pn) and sc > score:
                    best, score = b, sc
        return best, score

    def handle(self, text, meta=None):
        """From core.on_utterance, before the wake word is looked for. True: the phrase was a game command."""
        if self.learning:
            bid, until = self.learning
            if time.time() < until:
                self.learning = None
                return self._learned(bid, text)
            self.learning = None
        on, exe = self.active()
        if not on:
            return False
        b, score = self.match(text)
        if not b:
            return False
        now = time.time()
        if now - self.last.get(b.get("id"), 0) < 0.4:
            return True
        self.last[b.get("id")] = now
        self.q.put(b)
        self.core.log(f"Игра: «{text.strip()}» → {b['keys']}" + (f" ({score}%)" if score < 100 else ""))
        return True

    def _worker(self):
        while True:
            b = self.q.get()
            try:
                S.game_press(b["keys"], int(b.get("hold") or 60))
            except Exception as e:
                self.core.log(f"Игра: клавиши «{b.get('keys')}» не нажались: {e}", "warn")

    def _learned(self, bid, text):
        t = norm(text, self.cfg.get("stt.wake_words", []))
        conf = self.conf()
        for b in conf.get("binds") or []:
            if b.get("id") == bid:
                if t and t not in [norm(p) for p in b["phrases"]]:
                    b["phrases"].append(t)
                self._save(conf)
                self.core.emit("game_voice")
                self.core.say(f"Запомнил: «{t}».")
                return True
        return True

    def _save(self, conf):
        self.cfg.set("game.voice", conf)
        self._act = (0.0, False, "")

    # ---------------------------------------------------------------- the interface
    @staticmethod
    def check_keys(keys):
        try:
            S.parse_game_keys(keys)
            return ""
        except Exception as e:
            return str(e)

    def rpc_game_state(self):
        on, exe = self.active()
        c = self.conf()
        return {"mode": c.get("mode", "auto"), "end_ms": self.end_ms(), "binds": c.get("binds") or [],
                "active": on, "exe": exe, "presets": list(PRESETS)}

    def rpc_game_save(self, binds=None, mode=None, end_ms=None):
        c = self.conf()
        if binds is not None:
            clean = []
            for b in binds:
                phrases = [p.strip() for p in (b.get("phrases") or []) if str(p).strip()]
                keys = (b.get("keys") or "").strip()
                if not phrases and not keys:
                    continue
                err = self.check_keys(keys) if keys else "нет клавиши"
                if err:
                    raise ValueError(f"«{', '.join(phrases) or '?'}»: {err}")
                clean.append({"id": b.get("id") or uuid.uuid4().hex[:8], "phrases": phrases, "keys": keys,
                              "hold": max(20, min(5000, int(b.get("hold") or 60)))})
            c["binds"] = clean
        if mode in ("auto", "on", "off"):
            c["mode"] = mode
        if end_ms:
            c["end_ms"] = max(200, min(900, int(end_ms)))
        self._save(c)
        return self.rpc_game_state()

    def rpc_game_preset(self, name):
        c = self.conf()
        have = {norm(p) for b in c.get("binds") or [] for p in b["phrases"]}
        for p in PRESETS.get(name, []):
            if not any(norm(x) in have for x in p["phrases"]):
                c.setdefault("binds", []).append({"id": uuid.uuid4().hex[:8], "phrases": list(p["phrases"]), "keys": p["keys"], "hold": 60})
        self._save(c)
        return self.rpc_game_state()

    def rpc_game_learn(self, id):
        """"Записать с голоса": the next phrase heard (no name needed) becomes a variant of this bind."""
        self.learning = (id, time.time() + 10)
        self.core.say("Скажите фразу.")
        return True

    def rpc_game_check(self, keys):
        return self.check_keys(keys)


# ====================================================================== voice rules
def register(R, core):
    G = lambda: core.gamevoice

    @R(r"^(?:включи|запусти|активируй)\s+(?:игровой\s+голос|голос\s+в\s+игр\w+|голосовое\s+управление(?:\s+в\s+игр\w+)?|"
       r"режим\s+заклинаний|заклинания|игровые\s+команды)$")
    def _on(m, t):
        c = G().conf()
        c["mode"] = "on" if c.get("mode") == "on" else "auto"     # "auto": while a game is in front
        G()._save(c)
        if not G().binds():
            return "Включил, сэр, но фраз пока нет: добавьте их в Настройки → «Голос в играх» или скажите «научи заклинание люмос на клавишу один»."
        return "Голос в играх включён: в игре фразы работают без имени."

    @R(r"^(?:выключи|отключи|останови)\s+(?:игровой\s+голос|голос\s+в\s+игр\w+|голосовое\s+управление(?:\s+в\s+игр\w+)?|"
       r"режим\s+заклинаний|заклинания|игровые\s+команды)$")
    def _off(m, t):
        c = G().conf()
        c["mode"] = "off"
        G()._save(c)
        return "Голос в играх выключен."

    @R(r"^(?:научи|запомни|добавь)\s+(?:заклинание|игровую\s+команду|фразу\s+для\s+игры)\s+(.+?)\s+(?:на|клавишей|кнопкой)\s+"
       r"(?:клавишу\s+|кнопку\s+)?(.+)$")
    def _teach(m, t):
        phrase, spoken = m.group(1).strip(), m.group(2).strip()
        try:
            keys = S.parse_keys(spoken)
        except Exception:
            keys = spoken
        mouse = {"левую кнопку мыши": "lmb", "левая кнопка мыши": "lmb", "правую кнопку мыши": "rmb", "правая кнопка мыши": "rmb",
                 "колесо": "mmb", "лкм": "lmb", "пкм": "rmb"}
        keys = mouse.get(spoken, keys)
        err = G().check_keys(keys)
        if err:
            return f"Не понял клавишу «{spoken}», сэр. Скажите, например, «на клавишу один», «на шифт ку» или «на левую кнопку мыши»."
        c = G().conf()
        c.setdefault("binds", []).append({"id": uuid.uuid4().hex[:8], "phrases": [norm(phrase)], "keys": keys, "hold": 60})
        if c.get("mode") == "off":
            c["mode"] = "auto"
        G()._save(c)
        core.emit("game_voice")
        return f"Готово: «{norm(phrase)}» нажимает {keys.upper()}. В игре говорите без имени."

    @R(r"^(?:удали|забудь)\s+(?:заклинание|игровую\s+команду)\s+(.+)$")
    def _forget(m, t):
        what = norm(m.group(1))
        c = G().conf()
        before = len(c.get("binds") or [])
        c["binds"] = [b for b in c.get("binds") or [] if what not in [norm(p) for p in b["phrases"]]]
        if len(c["binds"]) == before:
            return f"Заклинания «{what}» нет, сэр."
        G()._save(c)
        core.emit("game_voice")
        return f"Забыл «{what}»."

    @R(r"^(?:какие|покажи|перечисли)\s+(?:у\s+меня\s+)?(?:заклинания|игровые\s+команды)$")
    def _list(m, t):
        bs = G().binds()
        if not bs:
            return "Игровых фраз пока нет, сэр."
        return "В игре работают: " + "; ".join(f"{b['phrases'][0]} — {b['keys'].upper()}" for b in bs[:8]) + "."
