"""Voice control in games: a phrase said while a game is on the screen presses keys or mouse buttons — no "Джарвис",
no AI, no network. "Люмос" -> 1, "щит" -> Q held for half a second, "рывок" -> Shift+W.

- Works out of the box: in a game "жми пробел", "нажми е три раза", "зажми шифт", "клик" press the named key, with
  no list at all. The list adds own words ("прыжок" -> space).
- Only the whole phrase counts (fuzzy, so "лумос" still works; up to three letters only exactly, because the game's own
  sound comes back as "Ли?", "Бли"), and only while a game is in front (mode "auto"), so ordinary talk presses
  nothing. Mode "on" works everywhere, "off" turns it off.
- A phrase may belong to one game ("game": exe): taught by voice inside a game, it is that game's.
- The keys go out as scan codes with a real hold (60 ms by default): games read scan codes and miss a 12 ms tap.
- While a game is in front the end of a phrase is detected sooner (350 ms of silence instead of ~0.6 s).
- Phrases for invented words are best taught by voice: "С голоса" in Settings remembers exactly how the recogniser
  hears the word.

Config game.voice: {"mode": "auto"|"on"|"off", "end_ms": 350,
                    "binds": [{"id", "phrases": [..], "keys": "q", "hold": 60, "game": "" | "spider-man.exe"}]}.
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
    "basic": [  # the usual keys of most PC games
        {"phrases": ["прыжок", "прыгай"], "keys": "space"}, {"phrases": ["присесть", "пригнись"], "keys": "ctrl"},
        {"phrases": ["перезарядка"], "keys": "r"}, {"phrases": ["инвентарь"], "keys": "i"},
        {"phrases": ["карта"], "keys": "m"}, {"phrases": ["пауза", "меню"], "keys": "esc"},
        {"phrases": ["вперед", "беги"], "keys": "w:1500"},
    ],
}

# "жми пробел", "нажми е три раза", "зажми шифт", "кнопка эф", "клик": a key by its name, no list needed
KEY_CMD = re.compile(r"^(?:(?P<hold>зажми|держи|удерживай|зажать)|жми|нажми|нажимай|нажать|тыкни|кнопк[аиу]|клавиш[аиу])\s+"
                     r"(?P<keys>.+?)(?:\s+(?P<n>\d+|два|две|три|четыре|пять)\s+раз[а]?)?$")
CLICK = re.compile(r"^(?:клик|кликни|щелкни|тыкни)$")
COUNT = {"два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5}
MOUSE = [(r"(?:лев\w*\s+)?(?:кнопк\w+\s+)?мыш\w*|лкм|лев\w+(?:\s+кнопк\w+)?|клик\w*", "lmb"),
         (r"прав\w+(?:\s+кнопк\w+)?(?:\s+мыш\w*)?|пкм", "rmb"),
         (r"колес\w*(?:\s+мыш\w*)?|средн\w+(?:\s+кнопк\w+)?(?:\s+мыш\w*)?|скм", "mmb")]


def norm(text, wake=()):
    s = (text or "").lower().replace("ё", "е")
    s = re.sub(r"[^\w\s-]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for w in wake:                         # "Джарвис, люмос" is the same spell
        s = re.sub(rf"^{re.escape(w.lower())}\w*\s+", "", s)
    return s


def threshold(phrase):
    """One misheard letter is forgiven ("лумос" for "люмос"); a different word is not ("что"). Up to three letters
    only an exact match counts: the game's sound comes back as "Ли?", "Бли", "Ей"."""
    n = len(phrase)
    return 101 if n <= 3 else 80 if n <= 5 else 78 if n <= 9 else 75


def spoken_keys(spoken):
    """"пробел" -> "space", "шифт ку" -> "shift+q", "один" -> "1", "левую кнопку мыши" -> "lmb". "" if not a key."""
    s = re.sub(r"^(?:на\s+)?(?:клавиш\w*|кнопк\w*)\s+(?!мыш)", "", norm(spoken))
    for rx, k in MOUSE:
        if re.fullmatch(rx, s):
            return k
    # "привяжи прыжок к пробелу": the case ending off ("пробелу" -> "пробел"); short letter names ("ку") stay
    for cand in dict.fromkeys((s, re.sub(r"(?<=[а-я]{3})(?:у|е|ю|ом|ой)\b", "", s))):
        try:
            keys = S.parse_keys(cand)
            S.parse_game_keys(keys)
            return keys
        except Exception:
            pass
    return ""


class GameVoice:
    def __init__(self, core):
        self.core = core
        self.cfg = core.cfg
        self._act = (0.0, False, "")
        self.learning = None              # (bind id, until): the next phrase becomes a variant of that bind
        self.last = {}                    # bind id / keys -> time of the last press (a doubled recognition presses once)
        self._miss_at = 0.0
        self.q = queue.Queue()
        threading.Thread(target=self._worker, daemon=True, name="game-keys").start()

    # ---------------------------------------------------------------- state
    def conf(self):
        return self.cfg.get("game.voice") or {}

    def binds(self, exe=None):
        """Usable binds; with exe, only those for every game or for that one."""
        out = [b for b in (self.conf().get("binds") or []) if b.get("phrases") and b.get("keys")]
        if exe is not None:
            out = [b for b in out if not b.get("game") or b["game"].lower() == (exe or "").lower()]
        return out

    def active(self):
        """(on, game exe). Cached for a second: it is asked for every 30 ms audio frame. On in any game even with no
        list: "жми пробел" needs none."""
        t, on, exe = self._act
        if time.time() - t < 1.0:
            return on, exe
        mode = self.conf().get("mode", "auto")
        exe = ""
        try:
            exe = S.active_exe()
        except Exception:
            pass
        if mode == "off":
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
    def match(self, text, exe=None):
        """(bind, score, best near miss (phrase, score))."""
        from rapidfuzz import fuzz
        t = norm(text, self.cfg.get("stt.wake_words", []))
        if not t or len(t) > 40:
            return None, 0, None
        best, score, near = None, 0, None
        for b in self.binds(exe):
            for p in b["phrases"]:
                pn = norm(p)
                if not pn:
                    continue
                sc = 100 if t == pn else fuzz.ratio(t, pn)
                if (t == pn or sc >= threshold(pn)) and sc > score:
                    best, score = b, sc
                elif sc >= 55 and len(t) > 3 and (not near or sc > near[1]):
                    near = (pn, int(sc))
        return best, score, near

    def key_command(self, text):
        """"жми пробел" -> {"keys": "space", "hold": 60}; "зажми шифт" holds a second; "нажми е три раза" repeats."""
        t = norm(text, self.cfg.get("stt.wake_words", []))
        if CLICK.match(t):
            return {"id": "key:lmb", "keys": "lmb", "hold": 60}
        m = KEY_CMD.match(t)
        if not m:
            return None
        keys = spoken_keys(m.group("keys"))
        if not keys:
            return None
        n = m.group("n")
        n = int(n) if n and n.isdigit() else COUNT.get(n or "", 1)
        n = max(1, min(10, n))
        hold = 1000 if m.group("hold") else 60
        return {"id": f"key:{keys}:{n}:{hold}", "keys": ", 90, ".join([keys] * n), "hold": hold}

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
        b, score, near = self.match(text, exe)
        if not b:
            b, score = self.key_command(text), 100
        if not b:
            if near and time.time() - self._miss_at > 10:
                self._miss_at = time.time()
                self.core.log(f"Игра: «{text.strip()}» не нажато (похоже на «{near[0]}», {near[1]}%)")
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

    def teach(self, phrase, spoken):
        """A phrase from a voice rule -> (reply). Inside a game the phrase becomes that game's."""
        phrase = norm(phrase)
        keys = spoken_keys(spoken)
        if not phrase or not keys:
            return (f"Не понял клавишу «{spoken}», сэр. Скажите, например, «на пробел», «на клавишу один», «на шифт ку» "
                    f"или «на левую кнопку мыши».")
        exe = ""
        try:
            fg = S.active_exe()
            exe = fg if self.core.extras.is_game(fg) else ""
        except Exception:
            pass
        c = self.conf()
        c["binds"] = [b for b in c.get("binds") or [] if not (phrase in [norm(p) for p in b["phrases"]]
                                                                and (b.get("game") or "") == exe)]
        c["binds"].append({"id": uuid.uuid4().hex[:8], "phrases": [phrase], "keys": keys, "hold": 60, "game": exe})
        if c.get("mode") == "off":
            c["mode"] = "auto"
        self._save(c)
        self.core.emit("game_voice")
        where = f" в {self.core.extras.app_info(exe)[0]}" if exe else " во всех играх"
        return f"Готово: «{phrase}» нажимает {keys.upper()}{where}. В игре говорите без имени."

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
        try:
            seen = list(self.cfg.get("extras.games_seen") or [])
            if exe and on and exe.lower() not in seen:
                seen.append(exe.lower())
            used = [b.get("game") for b in c.get("binds") or [] if b.get("game")]
            games = [{"exe": g, "name": self.core.extras.app_info(g)[0]} for g in dict.fromkeys(seen[::-1] + used)]
        except Exception:
            games = []
        return {"mode": c.get("mode", "auto"), "end_ms": self.end_ms(), "binds": c.get("binds") or [],
                "active": on, "exe": exe, "presets": list(PRESETS), "games": games}

    def rpc_game_save(self, binds=None, mode=None, end_ms=None):
        c = self.conf()
        if binds is not None:
            clean = []
            for b in binds:
                phrases = [p.strip() for p in (b.get("phrases") or []) if str(p).strip()]
                keys = (b.get("keys") or "").strip()
                if not phrases and not keys:
                    continue
                if keys and self.check_keys(keys):
                    keys = spoken_keys(keys) or keys       # "пробел" typed in Russian is a key too
                err = self.check_keys(keys) if keys else "нет клавиши"
                if err:
                    raise ValueError(f"«{', '.join(phrases) or '?'}»: {err}")
                clean.append({"id": b.get("id") or uuid.uuid4().hex[:8], "phrases": phrases, "keys": keys,
                              "hold": max(20, min(5000, int(b.get("hold") or 60))), "game": (b.get("game") or "").lower()})
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
                c.setdefault("binds", []).append({"id": uuid.uuid4().hex[:8], "phrases": list(p["phrases"]),
                                                  "keys": p["keys"], "hold": 60, "game": ""})
        if c.get("mode") == "off":
            c["mode"] = "auto"
        self._save(c)
        return self.rpc_game_state()

    def rpc_game_learn(self, id):
        """"С голоса": the next phrase heard (no name needed) becomes a variant of this bind."""
        self.learning = (id, time.time() + 10)
        self.core.say("Скажите фразу.")
        return True

    def rpc_game_check(self, keys):
        return self.check_keys(keys)


# ====================================================================== voice rules
def register(R, core):
    G = lambda: core.gamevoice
    WHAT = (r"(?:игровой\s+голос|голос\s+в\s+игр\w+|голосовое\s+управление(?:\s+в\s+игр\w+)?|управление\s+голосом(?:\s+в\s+игр\w+)?|"
            r"режим\s+заклинаний|заклинания|игровые\s+команды)")

    @R(r"^(?:включи|запусти|активируй)\s+" + WHAT + r"$")
    def _on(m, t):
        c = G().conf()
        c["mode"] = "on" if c.get("mode") == "on" else "auto"     # "auto": while a game is in front
        G()._save(c)
        return ("Голос в играх включён: в игре говорите без имени — «жми пробел», «нажми е три раза»"
                + (", и ваши фразы." if G().binds() else ". Свои слова: «научи заклинание прыжок на пробел»."))

    @R(r"^(?:выключи|отключи|останови)\s+" + WHAT + r"$")
    def _off(m, t):
        c = G().conf()
        c["mode"] = "off"
        G()._save(c)
        return "Голос в играх выключен."

    # "научи заклинание люмос на клавишу один", "добавь команду прыжок на пробел", "привяжи удар к левой кнопке мыши",
    # "когда я скажу прыжок, жми пробел", "на слово прыжок нажимай пробел"
    @R(r"^(?:научи|запомни|добавь|сделай)\s+(?:заклинание|игровую\s+команду|команду\s+для\s+игры|фразу\s+для\s+игры|"
       r"игровую\s+фразу)\s+(.+?)\s+(?:на|клавишей|кнопкой)\s+(?:клавишу\s+|кнопку\s+)?(.+)$")
    def _teach(m, t):
        return G().teach(m.group(1), m.group(2))

    @R(r"^привяжи\s+(?:слово\s+|фразу\s+|заклинание\s+)?(.+?)\s+(?:к|на)\s+(?:клавише\s+|клавишу\s+|кнопке\s+|кнопку\s+)?(.+)$")
    def _bind(m, t):
        return G().teach(m.group(1), m.group(2))

    @R(r"^(?:когда|если)\s+(?:я\s+)?(?:скажу|говорю|произнесу)\s+(.+?)\s+(?:то\s+)?(?:жми|нажимай|нажми|нажимать)\s+"
       r"(?:клавишу\s+|кнопку\s+)?(.+)$|^на\s+слово\s+(.+?)\s+(?:жми|нажимай|нажми)\s+(?:клавишу\s+|кнопку\s+)?(.+)$")
    def _when(m, t):
        phrase, keys = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        return G().teach(phrase, keys)

    @R(r"^(?:удали|забудь)\s+(?:заклинание|игровую\s+команду|игровую\s+фразу)\s+(.+)$")
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

    @R(r"^(?:какие|покажи|перечисли)\s+(?:у\s+меня\s+)?(?:заклинания|игровые\s+команды|игровые\s+фразы)$")
    def _list(m, t):
        bs = G().binds()
        if not bs:
            return ("Своих игровых фраз пока нет, сэр. В игре и так работает «жми пробел», «нажми е три раза»; свои слова: "
                    "«научи заклинание прыжок на пробел».")
        return "В игре работают: " + "; ".join(f"{b['phrases'][0]} — {b['keys'].upper()}" for b in bs[:8]) + "."
