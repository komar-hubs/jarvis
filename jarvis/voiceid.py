"""«Только мой голос»: dangerous commands (shut down, delete, send a message, close everything…) run only when the voice
is the owner's. The family talks next to the microphone, and "Джарвис, выключи компьютер" can be said by anyone.

- «Запомни мой голос»: three phrases; their voice print (a 256-number vector from WeSpeaker ResNet34, an open
  ONNX model, 26 MB, downloaded once into data/models) is averaged and kept in config voiceid.print. No recording is
  saved — only the numbers, which cannot be turned back into a voice.
- Each dangerous command is compared with the print (cosine similarity); a stranger hears "только по голосу
  владельца". Everything else works for anyone, as before.
- Local: onnxruntime is already there for the speech recogniser; no network after the one download."""
import re
import threading

import numpy as np

from .config import DATA

MODEL = DATA / "models" / "speaker.onnx"
URL = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx"
SR = 16000
# what only the owner may do by voice
DANGER = re.compile(r"(?:^|\s)(?:выключи\s+(?:компьютер|комп|пк|ноутбук)|перезагрузи|выйди\s+из\s+системы|удали|сотри|очисти\s+корзину|"
                    r"отправь|напиши\s+\w+\s+(?:в\s+)?(?:телеграм|вк|whatsapp|ватсап|дискорд)|перешли|ответь\s+на\s+(?:сообщение|письмо)|"
                    r"закрой\s+вс[её]|заверши\s+(?:работу|все)|убей\s+процесс|форматир|переведи\s+деньги|оплати|сделай\s+за\s+меня|"
                    r"режим\s+агента|выключи\s+(?:защиту|антивирус|защитник)|сбрось|забудь\s+вс[её]|"
                    # a stranger must not re-record the owner's voice or switch the check off
                    r"(?:запомни|выучи|запиши|забудь)\s+(?:мой\s+)?голос|(?:выключи|отключи)\s+(?:проверку|защиту))")
_lock = threading.Lock()
_sess = [None]


def _mel_bank(n_mels=80, n_fft=512, sr=SR, low=20.0, high=None):
    high = high or sr / 2
    mel = lambda f: 1127.0 * np.log(1.0 + f / 700.0)
    lo, hi = mel(low), mel(high)
    centers = np.linspace(lo, hi, n_mels + 2)
    freqs = mel(np.arange(n_fft // 2 + 1) * sr / n_fft)
    bank = np.zeros((n_mels, n_fft // 2 + 1), dtype=np.float32)
    for m in range(n_mels):
        l, c, r = centers[m], centers[m + 1], centers[m + 2]
        up = (freqs - l) / (c - l)
        down = (r - freqs) / (r - c)
        bank[m] = np.maximum(0, np.minimum(up, down))
    return bank


_BANK = None


def fbank(audio):
    """Kaldi-style 80-bin log mel filterbank (25 ms frames, 10 ms step, Povey window), mean-normalised — the features
    WeSpeaker models were trained on. audio: float32 mono 16 kHz in [-1, 1]."""
    global _BANK
    if _BANK is None:
        _BANK = _mel_bank()
    x = np.asarray(audio, dtype=np.float32) * 32768.0
    n, step = 400, 160
    if len(x) < n:
        x = np.pad(x, (0, n - len(x)))
    count = 1 + (len(x) - n) // step
    idx = np.arange(n)[None, :] + step * np.arange(count)[:, None]
    fr = x[idx]
    fr = fr - fr.mean(axis=1, keepdims=True)                       # remove DC
    fr = np.concatenate([fr[:, :1] * (1 - 0.97), fr[:, 1:] - 0.97 * fr[:, :-1]], axis=1)   # pre-emphasis
    win = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n) / (n - 1))) ** 0.85                 # Povey
    spec = np.abs(np.fft.rfft(fr * win, 512)) ** 2
    feats = np.log(np.maximum(spec @ _BANK.T, 1e-10)).astype(np.float32)
    return feats - feats.mean(axis=0, keepdims=True)


def ready():
    return MODEL.exists()


def download(log=print):
    import requests
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    tmp = MODEL.with_suffix(".part")
    with requests.get(URL, stream=True, timeout=(15, 120), headers={"User-Agent": "JARVIS"}) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    tmp.replace(MODEL)
    log("Модель голоса скачана")


def embed(audio):
    """The voice print of one phrase: a unit vector (256)."""
    import onnxruntime as ort
    with _lock:
        if _sess[0] is None:
            so = ort.SessionOptions()
            so.intra_op_num_threads = 2
            _sess[0] = ort.InferenceSession(str(MODEL), so, providers=["CPUExecutionProvider"])
        e = _sess[0].run(None, {"feats": fbank(audio)[None]})[0][0]
    return e / (np.linalg.norm(e) + 1e-9)


def similarity(a, b):
    return float(np.dot(np.asarray(a), np.asarray(b)))


class VoiceId:
    def __init__(self, core):
        self.core = core
        self.cfg = core.cfg
        self.samples = []

    def enrolled(self):
        return bool(self.cfg.get("voiceid.print"))

    def on(self):
        return bool(self.cfg.get("voiceid.enabled", False)) and self.enrolled() and ready()

    def check(self, text, audio):
        """None: allowed; a reply: refused. Only dangerous commands of a voice phrase are checked."""
        if audio is None or not self.on() or not DANGER.search(" " + (text or "").lower().replace("ё", "е")):
            return None
        if len(audio) < SR * 0.8:
            return None                     # too short to judge: a one-word command is not refused on a guess
        try:
            sim = similarity(embed(audio), self.cfg.get("voiceid.print"))
        except Exception as e:
            self.core.log(f"Проверка голоса: {e}", "warn")
            return None
        thr = float(self.cfg.get("voiceid.threshold", 0.45))
        self.core.log(f"Проверка голоса: {sim:.2f} (порог {thr:.2f}) для «{text[:40]}»")
        if sim >= thr:
            return None
        return "Эту команду я выполняю только по голосу владельца, извините."

    # ---------------------------------------------------------------- enrolment
    def enroll(self):
        """«Запомни мой голос»: three phrases, each one taken through core.capture with the listener's audio."""
        def start():
            if not ready():
                self.core.say("Скачиваю модель голоса, это 26 мегабайт, минуту.")
                try:
                    download(self.core.log)
                except Exception as e:
                    self.core.say(f"Не удалось скачать модель голоса: {e}")
                    return
            self.samples = []
            self._ask(1)
        threading.Thread(target=start, daemon=True, name="voiceid").start()
        return True

    # read from the screen (Settings → «Только мой голос») or repeated after Jarvis: about three seconds each.
    # It was "скажите любую фразу" by voice only: nobody knew what to say, when, or whether it was taken (issue #3)
    READ = ["Сегодня отличный день, чтобы спокойно всё успеть и немного отдохнуть.",
            "Джарвис, покажи погоду на завтра и напомни про важные дела.",
            "Мой голос — это мой ключ, и этим компьютером управляю я."]

    def _ask(self, n, again=False):
        import time
        phrase = self.READ[n - 1]
        self.core.emit("voiceid_step", n=n, text=phrase, state="wait")
        self.core.say(("Ещё раз, чуть подлиннее. " if again else
                       "Запоминаю ваш голос: три короткие фразы. Первая. " if n == 1 else
                       "Отлично. Вторая. " if n == 2 else "И последняя. ") + f"Повторите за мной: {phrase}")
        token = object()
        self._token = token

        def arm():
            # only once Jarvis is silent: his own voice must not get into the sample
            if self._token is not token:
                return
            self.core.capture = (got, time.time() + 25)
            self.core.listen_next(25)
            self.core.emit("voiceid_step", n=n, text=phrase, state="listen")
            threading.Timer(26, lambda: timeout(token)).start()

        def timeout(tok):
            if self._token is tok:
                self._token = None
                self.core.capture = None
                self.core.emit("voiceid_step", n=n, text="", state="cancel")
                self.core.say("Не услышал фразу, запись голоса отменена. Начните заново, когда будете готовы.")

        def got(text):
            self._token = None
            audio = getattr(self.core.listener, "last_audio", None)
            if audio is None or len(audio) < SR * 1.0:
                self._ask(n, again=True)
                return True
            try:
                self.samples.append(embed(audio))
            except Exception as e:
                self.core.say(f"Не получилось: {e}")
                return True
            if n < 3:
                self._ask(n + 1)
                return True
            self.core.emit("voiceid_step", n=3, text="", state="done")
            self._finish()
            return True
        self.core.after_speech(arm, 0.15)

    def _finish(self):
        prints = np.array(self.samples)
        mean = prints.mean(axis=0)
        mean = mean / (np.linalg.norm(mean) + 1e-9)
        self_sim = min(similarity(p, mean) for p in prints)
        # the bar: well under how alike the owner's own phrases are (0.66-0.82 between phrases in a test with Silero
        # voices, higher against the average print) and above other voices (up to 0.42 for two similar male ones)
        thr = round(max(0.4, min(0.5, self_sim - 0.25)), 2)
        self.cfg.set("voiceid.print", [round(float(x), 5) for x in mean])
        self.cfg.set("voiceid.threshold", thr)
        self.cfg.set("voiceid.enabled", True)
        self.core.log(f"Голос запомнен: сходство фраз {self_sim:.2f}, порог {thr}")
        self.core.emit("voiceid")
        self.core.say("Запомнил ваш голос. Теперь выключение, удаление и отправку сообщений выполняю только по нему. "
                      "Остальное работает для всех.")

    def forget(self):
        self.cfg.set("voiceid.print", None)
        self.cfg.set("voiceid.enabled", False)

    # ---------------------------------------------------------------- settings panel
    def rpc_voiceid_state(self):
        return {"enrolled": self.enrolled(), "enabled": bool(self.cfg.get("voiceid.enabled", False)),
                "threshold": self.cfg.get("voiceid.threshold", 0.45), "model": ready()}

    def rpc_voiceid_enroll(self):
        return self.enroll()

    def rpc_voiceid_cancel(self):
        self._token = None
        self.core.capture = None
        self.core.emit("voiceid_step", n=0, text="", state="cancel")
        return True

    def rpc_voiceid_forget(self):
        self.forget()
        return self.rpc_voiceid_state()


def register(R, core):
    @R(r"^(?:запомни|выучи|запиши)\s+(?:мой\s+)?голос$|^(?:включи|настрой)\s+(?:проверку|защиту)\s+(?:по\s+)?голос\w*$")
    def _enroll(m, t):
        return core.voiceid.enroll()

    @R(r"^(?:забудь|удали|сотри)\s+(?:мой\s+)?голос$|^(?:выключи|отключи)\s+(?:проверку|защиту)\s+(?:по\s+)?голос\w*$")
    def _forget(m, t):
        core.voiceid.forget()
        return "Забыл ваш голос: команды снова работают для всех."
