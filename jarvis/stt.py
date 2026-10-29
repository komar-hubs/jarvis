"""Always-on speech recognition: microphone -> WebRTC VAD segmentation -> faster-whisper (GPU when available)."""
import collections
import os
import queue
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np

SR = 16000
FRAME = 480  # 30 ms

HALLUCINATIONS = [
    "продолжение следует", "субтитры", "спасибо за просмотр", "подписывайтесь", "редактор субтитров",
    "dimatorzok", "корректор", "amara.org", "thanks for watching", "до новых встреч", "ставьте лайки",
    "смотрите продолжение", "благодарю за внимание", "игорь негода",
]
EXACT_NOISE = {"динамичная музыка", "спокойная музыка", "музыка", "веселая музыка", "аплодисменты", "смех",
               "тишина", "шум", "стук", "кашель", "вздох", "звук", "you", "угу", "ага", "мм", "хм", "а", "и", "ну"}


def _add_cuda_dll_dirs():
    """faster-whisper (CTranslate2) needs cuBLAS/cuDNN; use pip nvidia-* wheels if they are installed."""
    if os.name != "nt":
        return
    roots = [Path(p) for p in sys.path if p and Path(p).is_dir()]
    seen = set()
    for r in roots:
        nv = r / "nvidia"
        if nv.is_dir():
            for b in nv.glob("*/bin"):
                if b not in seen:
                    seen.add(b)
                    try:
                        os.add_dll_directory(str(b))
                        os.environ["PATH"] = str(b) + os.pathsep + os.environ["PATH"]
                    except Exception:
                        pass


def cuda_available():
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def list_mics():
    """One entry per physical microphone. Windows exposes every device three times (MME, DirectSound, WASAPI);
    MME converts any sample rate for us, so its index is used, with the full name from the other APIs
    (MME cuts names to 31 characters)."""
    import sounddevice as sd
    out = []
    try:
        default_in = sd.default.device[0]
        apis = sd.query_hostapis()
        devs = list(enumerate(sd.query_devices()))
        full = [d["name"] for _, d in devs if d["max_input_channels"] > 0 and apis[d["hostapi"]]["name"] != "MME"]
        for i, d in devs:
            if d["max_input_channels"] <= 0 or apis[d["hostapi"]]["name"] != "MME":
                continue
            if re.search(r"(?i)mapper|переназначение", d["name"]):
                continue  # the "sound mapper" is the default device, offered separately
            name = next((f for f in full if f.startswith(d["name"]) and len(f) > len(d["name"])), d["name"])
            out.append({"id": i, "name": name, "api": "MME", "default": i == default_in})
        if not out:  # no MME (unusual): offer what there is
            for i, d in devs:
                api = apis[d["hostapi"]]["name"]
                if d["max_input_channels"] > 0 and "WDM-KS" not in api:
                    out.append({"id": i, "name": d["name"], "api": api, "default": i == default_in})
    except Exception as e:
        out.append({"id": None, "name": f"ошибка: {e}", "api": "", "default": True})
    return out


def mic_access_problem():
    """Why Windows may hand this process only silence. None if nothing suspicious is found."""
    if "windowsapps" in (sys.base_prefix + sys.executable).lower():
        return ("Джарвис запущен на Python из Microsoft Store: Windows не даёт такому Python микрофон. "
                "Запустите install.bat ещё раз — он поставит обычный Python с python.org и пересоздаст окружение.")
    try:
        import winreg
        base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for sub in (base, base + r"\NonPackaged"):
                try:
                    with winreg.OpenKey(root, sub) as k:
                        if str(winreg.QueryValueEx(k, "Value")[0]).lower() == "deny":
                            return ("Windows запрещает программам доступ к микрофону. Параметры → Конфиденциальность и "
                                    "защита → Микрофон: включите «Доступ к микрофону» и «Разрешить классическим "
                                    "приложениям доступ к микрофону».")
                except OSError:
                    pass
    except Exception:
        pass
    return None


_com_ready = threading.local()


def _default_capture_id():
    """Windows id of the default microphone (None if it cannot be read)."""
    try:
        import comtypes
        if not getattr(_com_ready, "ok", False):
            comtypes.CoInitialize()
            _com_ready.ok = True
        from pycaw.pycaw import AudioUtilities
        return AudioUtilities.GetMicrophone().GetId()
    except Exception:
        return None


class _Converter:
    """Device-native audio (any sample rate, mono or stereo int16) -> 16 kHz mono int16 frames of 30 ms,
    for microphones that refuse to open at 16 kHz."""

    def __init__(self, rate, channels):
        self.ch = channels
        self.step = rate / SR
        self.k = max(1, int(round(self.step)))  # moving average before decimation: cheap anti-aliasing
        self.tail = np.zeros(0, np.float32)
        self.buf = np.zeros(0, np.float32)
        self.pos = 0.0
        self.out = np.zeros(0, np.int16)

    def push(self, raw):
        x = np.frombuffer(raw, np.int16).astype(np.float32)
        if self.ch > 1:
            x = x[: len(x) // self.ch * self.ch].reshape(-1, self.ch).mean(axis=1)
        if self.k > 1:
            xx = np.concatenate([self.tail, x])
            if len(xx) < self.k:
                self.tail = xx
                return []
            self.tail = xx[-(self.k - 1):]
            x = np.convolve(xx, np.full(self.k, 1.0 / self.k, np.float32), mode="valid")
        self.buf = np.concatenate([self.buf, x])
        if len(self.buf) > 1 and len(self.buf) - 1 >= self.pos:
            n = int((len(self.buf) - 1 - self.pos) // self.step) + 1
            y = np.interp(self.pos + np.arange(n) * self.step, np.arange(len(self.buf)), self.buf)
            nxt = self.pos + n * self.step
            drop = min(int(nxt), len(self.buf))
            self.buf = self.buf[drop:]
            self.pos = nxt - drop
            self.out = np.concatenate([self.out, np.clip(y, -32768, 32767).astype(np.int16)])
        frames = []
        while len(self.out) >= FRAME:
            frames.append(self.out[:FRAME].tobytes())
            self.out = self.out[FRAME:]
        return frames


class Listener:
    def __init__(self, cfg, on_utterance, on_level=None, on_state=None, log=print, is_speaking=lambda: False):
        self.cfg = cfg
        self.on_utterance = on_utterance
        self.on_level = on_level or (lambda v: None)
        self.on_state = on_state or (lambda s: None)
        self.log = log
        self.is_speaking = is_speaking
        self.model = None
        self.model_name = None
        self.device = None
        self.frames = queue.Queue(maxsize=400)
        self.stream = None
        self.conv = None            # resampler when the microphone only works in its own format
        self.mic_name = ""
        self.opened_at = 0.0
        self.heard_any = False      # any non-zero sample since the microphone was opened
        self.silent_warned = False
        self.quiet_checked = False
        self.gain = 1.0             # automatic digital gain for quiet microphones
        self.dev_name = ""          # PortAudio name of the open microphone (to find it in Windows audio settings)
        self._measure = None        # list collecting raw levels during a microphone test
        self.force_until = 0.0
        self.running = False
        self.force_next = False          # push-to-talk / chat mic: accept next phrase without wake word
        self.force_target = "voice"      # voice | chat
        self.recording = False
        self.mic_error = None
        self.cur_dev = (None, False)     # (device, native format) that is open now
        self.dead = {}                   # (device, native) -> when it opened fine but delivered no sound: skipped for 10 min
        self.model_lock = threading.Lock()
        self.last_level_emit = 0
        # phrases are recognised one after another by a single worker: with a thread per phrase a short second
        # phrase could be handled before the first one
        self.jobs = queue.Queue()
        threading.Thread(target=self._jobs_worker, daemon=True, name="stt-jobs").start()

    # ------------------------------------------------------------ model
    def engine(self):
        return self.cfg.get("stt.engine", "gigaam")

    def _load_gigaam(self):
        """GigaAM v3 CTC (Sber) via onnx-asr: best Russian accuracy, ~0.3 s per phrase on any CPU,
        and as a CTC model it cannot invent phrases that were never said (Whisper's "открой браузер")."""
        import onnx_asr
        t = time.time()
        try:
            # cached copy first: without this onnx-asr asks the HuggingFace Hub on every start,
            # and a slow/blocked network then hangs recognition loading for minutes
            hub = Path(os.environ.get("HF_HUB_CACHE") or Path.home() / ".cache" / "huggingface" / "hub")
            snaps = [d for d in (hub / "models--istupakov--gigaam-v3-onnx" / "snapshots").glob("*")
                     if any(d.glob("*ctc*.onnx"))]
            if not snaps:
                raise FileNotFoundError("not cached")
            path = max(snaps, key=lambda d: d.stat().st_mtime)
            m = onnx_asr.load_model("gigaam-v3-ctc", path, providers=["CPUExecutionProvider"])
        except Exception:
            self.log("Скачиваю модель распознавания GigaAM (~250 МБ)...")
            m = onnx_asr.load_model("gigaam-v3-ctc", providers=["CPUExecutionProvider"])
        m.recognize(np.zeros(SR // 2, np.float32), sample_rate=SR)  # warm-up
        self.model, self.model_name, self.device = m, "gigaam-v3-ctc", "cpu"
        self.log(f"Распознавание речи: GigaAM v3 на CPU ({time.time() - t:.1f} с)")
        self.on_state("idle")
        return True

    def load_model(self):
        with self.model_lock:
            if self.engine() == "gigaam":
                if self.model is not None and self.model_name == "gigaam-v3-ctc":
                    return True
                self.on_state("loading")
                try:
                    return self._load_gigaam()
                except Exception as e:
                    self.log(f"GigaAM не загрузился ({str(e)[:150]}), пробую Whisper")
            name = self.cfg.get("stt.model", "large-v3-turbo")
            want = self.cfg.get("stt.device", "auto")
            if self.model is not None and self.model_name == name:
                return True
            if not (want in ("auto", "cuda") and cuda_available()) and name in ("large-v3-turbo", "large-v3", "medium"):
                name = "small"  # a large Whisper on a CPU takes ~7 s per phrase
            _add_cuda_dll_dirs()
            from faster_whisper import WhisperModel
            self.on_state("loading")
            tries = []
            if want in ("auto", "cuda") and cuda_available():
                tries.append(("cuda", "int8_float16"))  # ~40% less VRAM than float16, same accuracy in practice
            tries.append(("cpu", "int8"))
            for dev, ct in tries:
                try:
                    t = time.time()
                    try:  # cached model: skip the slow HF Hub check
                        m = WhisperModel(name, device=dev, compute_type=ct, local_files_only=True)
                    except Exception:
                        self.log(f"Скачиваю модель распознавания {name}...")
                        m = WhisperModel(name, device=dev, compute_type=ct)
                    # warm-up (also checks that CUDA libraries really load)
                    list(m.transcribe(np.zeros(SR, np.float32), language="ru", beam_size=1)[0])
                    self.model, self.model_name, self.device = m, name, dev
                    self.log(f"Распознавание речи: {name} на {dev.upper()} ({time.time() - t:.1f} с)")
                    self.on_state("idle")
                    return True
                except Exception as e:
                    self.log(f"Whisper {name} на {dev}: {str(e)[:200]}")
            self.on_state("error")
            return False

    def transcribe(self, audio):
        if self.model is None:
            return "", {}
        if self.model_name == "gigaam-v3-ctc":
            text = (self.model.recognize(audio, sample_rate=SR) or "").strip()
            bare = re.sub(r"[^\w\s]", "", text.lower()).strip()
            if not bare or bare in EXACT_NOISE or len(bare) < 2:
                return "", {}
            # GigaAM writes lowercase without punctuation: make it read nicely in the chat and log
            words = text.split()
            if words and words[0].lower() in ("джарвис", "jarvis", "джарвиз", "жарвис") and len(words) > 1:
                text = words[0] + ", " + " ".join(words[1:])
            text = text[0].upper() + text[1:]
            if re.search(r"^(?:\w+,\s)?(?:что|как|кто|где|когда|почему|зачем|сколько|какой|какая|какое|какие|"
                         r"чей|куда|откуда|можно ли|ли)\b", text.lower()):
                text += "?"
            return text, {"no_speech": 0.0, "logprob": 0.0}
        # only the wake word as a hint: example commands in the prompt made Whisper "hear" them in noise
        prompt = "Джарвис."
        segs, info = self.model.transcribe(
            audio, language=self.cfg.get("stt.language", "ru"), beam_size=5 if self.device == "cuda" else 1,
            vad_filter=False, condition_on_previous_text=False, initial_prompt=prompt,
            without_timestamps=True, temperature=0.0, no_speech_threshold=0.6)
        parts, meta = [], {"no_speech": 0.0, "logprob": 0.0}
        for s in segs:
            if s.no_speech_prob > 0.75 and s.avg_logprob < -0.9:
                continue
            parts.append(s.text)
            meta["no_speech"] = max(meta["no_speech"], s.no_speech_prob)
            meta["logprob"] = min(meta["logprob"], s.avg_logprob)
        text = " ".join(parts).strip()
        low = text.lower()
        bare = re.sub(r"[^\w\s]", "", low).strip()
        if bare in EXACT_NOISE or any(h in low for h in HALLUCINATIONS) or not re.search(r"\w", text):
            return "", meta
        if re.sub(r"[^\w]", "", low) in ("джарвис", "jarvis") and meta["no_speech"] > 0.5:
            return "", meta  # the prompt echoed back from silence
        # Whisper sometimes repeats the whole phrase twice: "X? X?" -> "X?"
        parts_s = [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]
        dedup = [p for i, p in enumerate(parts_s) if i == 0 or p.lower() != parts_s[i - 1].lower()]
        text = " ".join(dedup)
        return text, meta

    def align_end(self, audio, sr, text):
        """Where does `text` end inside synthesized `audio`? Whisper word timestamps find the last word of the
        text, everything after it is TTS babble. Returns seconds, or None if the audio does not match the text."""
        if self.model is None or not len(audio) or self.model_name == "gigaam-v3-ctc":
            return None  # word timestamps need Whisper
        from rapidfuzz import fuzz
        n = int(len(audio) * SR / sr)
        a16 = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
        with self.model_lock:
            segs, _ = self.model.transcribe(a16, language="ru", beam_size=1, word_timestamps=True,
                                            condition_on_previous_text=False, vad_filter=False)
            words = [(re.sub(r"[^\w]", "", w.word.lower()), w.end) for s in segs for w in (s.words or [])]
        target = [w for w in re.findall(r"\w+", text.lower().replace("ё", "е"))]
        words = [(w.replace("ё", "е"), e) for w, e in words if w]
        if not words or not target:
            return None
        heard = " ".join(w for w, _ in words)
        if fuzz.partial_ratio(" ".join(target), heard) < 70:
            return None  # garbled: let the caller re-synthesize
        last = target[-1]
        lo = max(0, len(target) - 3)
        for i, (w, end) in enumerate(words):
            if i >= lo and (fuzz.ratio(w, last) >= 65 or (last.isdigit() and w.isdigit())):
                return end
        return None

    # ------------------------------------------------------------ audio
    def _callback(self, indata, frames, t, status):
        try:
            conv = self.conv
            if conv is None:
                self.frames.put_nowait(bytes(indata))
            else:
                for f in conv.push(bytes(indata)):
                    self.frames.put_nowait(f)
        except queue.Full:
            pass
        except Exception:
            pass

    def start(self):
        if self.running:
            return
        self.running = True
        threading.Thread(target=self._boot, daemon=True, name="stt").start()

    def _boot(self):
        if not self.load_model():
            self.log("Распознавание недоступно: модель не загрузилась")
        self.open_mic()
        self._loop()

    def _wanted_mic(self, sd):
        """Configured device index, re-found by name when Windows renumbered the devices (USB headset replugged)."""
        dev, ref = self.cfg.get("stt.mic"), self.cfg.get("stt.mic_ref") or {}
        if dev is None or ref.get("id") != dev or not ref.get("name"):
            return dev  # nothing remembered for this choice (the user picked another microphone since)
        name = ref["name"]
        try:
            devs = sd.query_devices()
            if dev < len(devs) and devs[dev]["name"] == name and devs[dev]["max_input_channels"] > 0:
                return dev
            for i, d in enumerate(devs):
                if d["name"] == name and d["max_input_channels"] > 0:
                    self.cfg.set("stt.mic", i)
                    self.cfg.set("stt.mic_ref", {"id": i, "name": name})
                    return i
        except Exception:
            pass
        return dev

    def _try_open(self, sd, dev, native):
        info = sd.query_devices(dev, "input")
        api = sd.query_hostapis(info["hostapi"])["name"]
        extra = None
        if native:  # the device's own format, converted to 16 kHz mono here
            rate, ch = int(info["default_samplerate"]), max(1, min(2, int(info["max_input_channels"])))
        else:
            rate, ch = SR, 1
            if "WASAPI" in api:
                extra = sd.WasapiSettings(auto_convert=True)  # shared-mode WASAPI only takes the mixer rate
        stream = sd.RawInputStream(samplerate=rate, blocksize=int(rate * 0.03), channels=ch, dtype="int16",
                                   device=dev, callback=self._callback, extra_settings=extra)
        self.conv = None if (rate == SR and ch == 1) else _Converter(rate, ch)
        stream.start()
        self.stream = stream
        self.mic_name = f"{info['name']} ({api}, {rate} Гц)"
        self.dev_name = info["name"]
        self.opened_at = time.time()
        self.heard_any = False
        self.quiet_checked = False

    def measure(self, secs=3.0):
        """Raw microphone levels (before the automatic gain) for `secs` seconds: for the "check microphone" button."""
        self._measure = []
        time.sleep(secs)
        vals, self._measure = self._measure or [], None
        if not vals:
            return {"frames": 0}
        a = np.array(vals, np.float32)
        return {"frames": len(vals), "loud": float(np.percentile(a, 95)), "noise": float(np.percentile(a, 20)),
                "gain": round(self.gain, 1), "mic": self.mic_name}

    def open_mic(self, refresh=False):
        """Wanted device -> default device -> other microphones; each first at 16 kHz mono, then in its own format.
        refresh: re-read the device list (PortAudio lists devices once at start, so a re-plugged headset stayed
        invisible until Jarvis was restarted)."""
        import sounddevice as sd
        if self.stream:
            try:
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        if refresh:
            try:
                sd._terminate()
                sd._initialize()
            except Exception as e:
                self.log(f"Список микрофонов не обновился: {e}", "warn")
        want = self._wanted_mic(sd)
        cands = [want, None]
        try:
            apis = sd.query_hostapis()
            ins = [(i, d) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0
                   and not re.search(r"(?i)стерео\s*микшер|stereo\s*mix|loopback|what\s*u\s*hear", d["name"])]
            # MME first, then WASAPI: a driver that stalls in one often works in the other
            for api in ("MME", "WASAPI"):
                cands += [i for i, d in ins if api in apis[d["hostapi"]]["name"]]
        except Exception:
            pass
        now = time.time()
        self.dead = {k: t for k, t in self.dead.items() if now - t < 600}
        errors, seen = [], set()
        for dev in cands:
            if dev in seen:
                continue
            seen.add(dev)
            for native in (False, True):
                if (dev, native) in self.dead:
                    continue            # it opened before and gave no sound: the others get their turn
                try:
                    self._try_open(sd, dev, native)
                except Exception as e:
                    errors.append(f"{dev}: {e}")
                    continue
                self.cur_dev = (dev, native)
                self.mic_error = None
                if dev == want and want is not None:
                    self.cfg.set("stt.mic_ref", {"id": dev, "name": sd.query_devices(dev)["name"]})
                if dev != want or native:
                    self.log(f"Микрофон: {self.mic_name}" + ("" if dev == want else " — выбранный не открылся"), "warn")
                return True
        if self.dead and not errors:
            self.dead.clear()           # every device was tried and went quiet: start over with all of them
            return self.open_mic()
        self.mic_error = "; ".join(errors[-3:]) or "нет микрофонов"
        self.log(f"Микрофон не открылся: {self.mic_error}", "error")
        return False

    def _vm_hint(self):
        """Once per run: inside a virtual machine the microphone only exists if the hypervisor passes it through."""
        if getattr(self, "_vm_said", False):
            return ""
        self._vm_said = True
        try:
            from . import system as S
            vm = S.virtual_machine()
        except Exception:
            vm = ""
        if not vm:
            return ""
        how = {"VirtualBox": "VirtualBox: Устройства → Аудио → Включить аудиовход",
               "VMware": "VMware: добавьте звуковую карту и подключите микрофон (VM → Removable Devices → Sound Card)",
               "Hyper-V": "Hyper-V: Расширенный сеанс и «Микрофон» в локальных ресурсах подключения"}.get(vm, "в настройках виртуальной машины включите вход звука")
        return f"Это виртуальная машина ({vm}): микрофон работает, только если его пробросили. {how}."

    def restart_mic(self):
        self.open_mic()

    def listen_once(self, target="voice"):
        self.force_next = True
        self.force_target = target
        # a click on the reactor without speaking must not turn the next random phrase, minutes later,
        # into a command ("Мигрит" -> AI -> volume 0)
        self.force_until = time.time() + 10
        self.on_state("listening")

    def _loop(self):
        import webrtcvad
        vad = webrtcvad.Vad(int(self.cfg.get("stt.vad", 2)))
        pre = collections.deque(maxlen=20)      # 600 ms pre-roll: the soft "Дж" of the name must not be cut
        recent = collections.deque(maxlen=8)
        voiced = []
        in_speech = False
        seg_tts = False
        silence = 0
        noise = 300.0
        # auto gain: microphone noise floor and speech level before gain. The speech level learned last time is
        # the starting point: from 1500 the first phrases after a start went in almost unamplified and were lost
        noise_raw = 50.0
        speech_raw = float(self.cfg.get("stt.learned_level") or 1500.0)
        speech_levels = collections.deque(maxlen=1500)  # raw levels of speech-like frames, ~45 s of talking
        ref_level, n_frames, last_saved, saved_level = speech_raw, 0, time.time(), speech_raw
        peak_seen = 0.0
        starved = 0
        zero_run = 0
        last_dev_check, default_id = 0.0, None
        while self.running:
            try:
                buf = self.frames.get(timeout=1)
                starved = 0
            except queue.Empty:
                # no audio for 3 s: the device was unplugged or switched (headset) -> reopen it
                starved += 1
                # a slow laptop may hold the sound back for several seconds (speech synthesis keeps the processor busy)
                # and then deliver it by itself: 3 s was far too nervous and cut the listening for nothing. Patience
                # is longer while Jarvis speaks; a stream that Windows itself reports as stopped is dealt with sooner.
                stopped = self.stream is not None and not getattr(self.stream, "active", True)
                speaking = bool(self.is_speaking())
                if starved >= (25 if speaking else 8) or (stopped and starved >= 2):
                    stalled, starved = starved, 0
                    # the same device reopened over and over was never going to help ("Python использует микрофон", and
                    # still no sound): remember it as stalled and let the next one try
                    self.dead[self.cur_dev] = time.time()
                    self.log(f"Микрофон «{self.dev_name or 'по умолчанию'}» не отдаёт звук {stalled} с"
                             + (" (пока Джарвис говорил)" if speaking else "") + (", поток остановлен" if stopped else "")
                             + ": пробую другой…", "warn")
                    vm = self._vm_hint()
                    if vm:
                        self.log(vm, "warn")
                    try:
                        self.open_mic(refresh=True)
                    except Exception as e:
                        self.mic_error = str(e)
                continue
            pcm = np.frombuffer(buf, np.int16)
            raw = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)) + 1e-6)
            if not self.heard_any:
                if raw > 1.5:
                    self.heard_any = True
                elif not self.silent_warned and time.time() - self.opened_at > 8:
                    self.silent_warned = True
                    self.on_state("mic_silent")  # only zeros since opening: access is blocked or the mic is muted
            # a device that delivered sound and now gives only exact zeros was unplugged under the stream
            # (MME keeps sending silence instead of stopping): reopen it with a fresh device list
            zero_run = zero_run + 1 if (self.heard_any and raw < 1.0) else 0
            if zero_run > 400:  # 12 s
                zero_run = 0
                self.log("Микрофон отдаёт только тишину, переподключаю…", "warn")
                self.open_mic(refresh=True)
                continue
            # "по умолчанию" follows Windows: a headset that became the default input is picked up
            if self.cfg.get("stt.mic") is None and time.time() - last_dev_check > 5:
                last_dev_check = time.time()
                did = _default_capture_id()
                if default_id is not None and did and did != default_id:
                    self.log("Windows сменила микрофон по умолчанию — переключаюсь", "warn")
                    default_id = did
                    self.open_mic(refresh=True)
                    continue
                default_id = did or default_id
            m = self._measure
            if m is not None:
                m.append(raw)
            peak_seen = max(peak_seen * 0.99995, raw)
            if self.cfg.get("stt.auto_gain", True):
                # quiet microphones (Discord boosts them itself, this Realtek gives speech peaks of ~36 out of
                # 32767): raise the level so VAD, the energy gate and the recogniser hear speech
                noise_raw = (0.9 * noise_raw + 0.1 * raw) if raw < noise_raw else (0.999 * noise_raw + 0.001 * raw)
                # a knock, a cough into the mic, a plug: far above usual speech -> not speech, not counted
                if raw > noise_raw * 3 and (raw < speech_raw * 8 or len(speech_levels) < 20):
                    rate = 0.15 if len(speech_levels) < 60 else 0.03  # learn fast at first, then steady
                    speech_raw = (1 - rate) * speech_raw + rate * raw
                    speech_levels.append(raw)
                n_frames += 1
                if n_frames % 30 == 0 and len(speech_levels) >= 50:
                    # level and cap follow the usual loudness of speech (median / 90th percentile), not the loudest
                    # peak ever: one knock on the desk used to drop the cap from x60 to x1 for ~7 minutes, and a
                    # quiet microphone went deaf
                    arr = np.fromiter(speech_levels, np.float32)
                    ref_level = float(np.percentile(arr, 90))
                    speech_raw = float(np.median(arr))
                elif len(speech_levels) < 50:
                    ref_level = speech_raw
                cap = 60.0 if ref_level < 300 else 4.0 if ref_level < 1200 else 1.0
                target = float(self.cfg.get("stt.gain") or 0) or max(1.0, min(1500.0 / max(speech_raw, 0.5), cap))
                self.gain += (target - self.gain) * 0.05
                if self.gain > 1.05:
                    pcm = np.clip(pcm.astype(np.float32) * self.gain, -32768, 32767).astype(np.int16)
                    buf = pcm.tobytes()
                if time.time() - last_saved > 120 and abs(speech_raw - saved_level) > 0.15 * saved_level:
                    last_saved, saved_level = time.time(), speech_raw
                    self.cfg.set("stt.learned_level", round(speech_raw, 1))
            if not self.quiet_checked and time.time() - self.opened_at > 20 and len(speech_levels) >= 100:
                # judged only after real speech: a silent room in the first 20 s is not a "quiet microphone"
                self.quiet_checked = True
                if ref_level < 80:
                    self.on_state("mic_quiet")  # Core raises the Windows input level
            rms = raw * self.gain if self.gain > 1.05 else raw
            now = time.time()
            if now - self.last_level_emit > 0.06:
                self.last_level_emit = now
                self.on_level(min(1.0, rms / 6000))
            if not self.cfg.get("stt.always_listen", True) and not self.force_next:
                pre.clear()
                continue
            try:
                is_sp = vad.is_speech(buf, SR)
            except Exception:
                is_sp = False
            # energy gate on top of VAD: ignore quiet background chatter
            if not in_speech:
                noise = 0.97 * noise + 0.03 * rms if not is_sp else noise
            loud = rms > max(160.0, noise * 2.0)  # softer: a quietly said name must still pass
            is_sp = is_sp and loud
            recent.append(is_sp)
            if not in_speech:
                pre.append(buf)
                if sum(recent) >= 5:
                    in_speech = True
                    voiced = list(pre)
                    seg_tts = self.is_speaking()
                    silence = 0
                    self.recording = True
                    if self.force_next:
                        self.on_state("listening")
            else:
                voiced.append(buf)
                if not seg_tts and len(voiced) % 5 == 0 and self.is_speaking():
                    seg_tts = True  # the assistant started talking during this phrase
                silence = 0 if is_sp else silence + 1
                max_sil = int(self.cfg.get("stt.silence_ms", 650)) // 30
                too_long = len(voiced) > 600  # 18 s
                if silence >= max_sil or too_long:
                    in_speech = False
                    self.recording = False
                    recent.clear()
                    pre.clear()
                    seg = b"".join(voiced)
                    voiced = []
                    if len(seg) / 2 / SR < 0.35:
                        continue
                    audio = np.frombuffer(seg, np.int16).astype(np.float32) / 32768.0
                    forced, target = self.force_next and time.time() < self.force_until, self.force_target
                    self.force_next = False
                    during_tts = seg_tts or self.is_speaking()
                    self.jobs.put((audio, forced, target, during_tts))

    def _jobs_worker(self):
        while True:
            args = self.jobs.get()
            try:
                self._handle(*args)
            except Exception as e:
                self.log(f"Распознавание: {e}", "warn")

    def _handle(self, audio, forced, target, during_tts):
        if self.model is None:
            return
        self.on_state("thinking") if forced else None
        t = time.time()
        with self.model_lock:
            text, meta = self.transcribe(audio)
        meta.update({"forced": forced, "target": target, "during_tts": during_tts,
                     "stt_ms": int((time.time() - t) * 1000), "dur": round(len(audio) / SR, 2)})
        if forced:
            self.force_next = False
        if text:
            self.on_utterance(text, meta)
        elif forced:
            self.on_state("idle")
