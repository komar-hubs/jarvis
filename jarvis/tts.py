"""Text-to-speech. Engines: xtts (voice clone, Russian with a British accent), edge (online neural), sapi (offline fallback).

speak() splits text into sentences and synthesises the next sentence while the previous one plays,
so the first words come out fast even for long answers."""
import asyncio
import io
import json
import os
import re
import subprocess
import tempfile
import threading
import wave
from pathlib import Path

import numpy as np

import hashlib
import time

from .audio import StreamFX, TailTrimmer, jarvis_effect
from .config import DATA, ROOT, VOICES

CACHE_DIR = DATA / "cache" / "tts"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def prune_cache(max_age_days=21, max_mb=300):
    """Every short AI sentence was cached forever: drop phrases unused for 3 weeks, then the oldest above 300 MB."""
    try:
        files = sorted(((p.stat().st_mtime, p.stat().st_size, p) for p in CACHE_DIR.glob("*.npy")), key=lambda x: x[0])
        cutoff, total = time.time() - max_age_days * 86400, sum(s for _, s, _ in files)
        for mtime, size, p in files:
            if mtime < cutoff or total > max_mb * 2**20:
                p.unlink(missing_ok=True)
                total -= size
    except Exception:
        pass

XTTS_PY = ROOT / ".xtts-venv" / "Scripts" / "python.exe"
REF_DEFAULT = VOICES / "jarvis_ref.wav"

REF_TEXT = ("Good evening, sir. All systems are online and running at full capacity. "
            "I have taken the liberty of reviewing your schedule for tomorrow. "
            "Shall I prepare the suit, or would you prefer a quiet evening for once? "
            "As always, I am at your service.")


def clean_for_speech(text):
    text = re.sub(r"```.*?```", " Код показан на экране. ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "ссылка", text)
    text = re.sub(r"[*_#>|~]+", " ", text)
    text = re.sub(r"<jarvis>.*?</jarvis>", " ", text, flags=re.S)
    # never say the wake word out loud: the microphone would hear it and trigger itself
    text = re.sub(r"(?i)\b(?:джарвис|jarvis|j\.a\.r\.v\.i\.s\.?)\w*", "ваш ассистент", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def split_sentences(text, max_len=220):
    parts = re.split(r"(?<=[.!?…;:])\s+|\n+", text)
    out = []
    for p in parts:
        p = p.strip()
        while len(p) > max_len:
            cut = p.rfind(",", 0, max_len)
            cut = cut if cut > 40 else max_len
            out.append(p[:cut + 1].strip())
            p = p[cut + 1:].strip()
        if p:
            out.append(p)
    return [s for s in out if re.search(r"\w", s)]


def read_wav(path_or_bytes):
    f = io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, (bytes, bytearray)) else open(path_or_bytes, "rb")
    with wave.open(f) as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        sw = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    dtype = {2: np.int16, 4: np.int32}.get(sw, np.int16)
    a = np.frombuffer(raw, dtype).astype(np.float32) / np.iinfo(dtype).max
    if ch > 1:
        a = a.reshape(-1, ch).mean(1)
    return a, sr


def write_wav(path, data, sr):
    pcm = (np.clip(data, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def decode_mp3(data):
    import miniaudio
    d = miniaudio.decode(data, output_format=miniaudio.SampleFormat.FLOAT32, nchannels=1, sample_rate=24000)
    return np.frombuffer(d.samples, dtype=np.float32).copy(), 24000


# ---------------------------------------------------------------- engines
class EdgeEngine:
    name = "edge"

    def __init__(self, cfg):
        self.cfg = cfg
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True, name="edge-tts").start()

    async def _synth(self, text, voice, rate, pitch):
        import edge_tts
        com = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        buf = bytearray()
        async for chunk in com.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)

    def synth(self, text, voice=None, rate=None, pitch=None):
        v = self.cfg.get("voice")
        err = None
        for _ in range(3):  # the service sometimes drops a request
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self._synth(text, voice or v["edge_voice"], rate or v["edge_rate"], pitch or v["edge_pitch"]),
                    self.loop)
                return decode_mp3(fut.result(timeout=30))
            except Exception as e:
                err = e
                time.sleep(0.4)  # the service drops requests in bursts
        raise err


class ElevenLabsEngine:
    """ElevenLabs cloud TTS (REST). Key: voice.eleven_key in config or the ELEVENLABS_API_KEY env var.
    Flash v2.5 costs ~0.5 credit per character, multilingual v2 ~1 credit; short phrases are cached by TTS."""
    name = "elevenlabs"
    API = "https://api.elevenlabs.io/v1"
    VOICE_DEFAULT = "onwK4e9ZLuTAKqWW03F9"  # Daniel: British male, deep, closest premade voice to Jarvis
    MODEL_DEFAULT = "eleven_flash_v2_5"

    def __init__(self, cfg):
        self.cfg = cfg
        self.dead_until = 0  # after a 401/quota error stop hammering the API for a while, fall back to silero

    def key(self):
        return (self.cfg.get("voice.eleven_key") or os.environ.get("ELEVENLABS_API_KEY") or "").strip()

    def synth(self, text):
        import requests
        key = self.key()
        if not key:
            raise RuntimeError("нет API-ключа ElevenLabs")
        if time.time() < self.dead_until:
            raise RuntimeError("ElevenLabs временно отключён (ключ или кредиты)")
        v = self.cfg.get("voice")
        model = v.get("eleven_model") or self.MODEL_DEFAULT
        body = {"text": text, "model_id": model,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.8, "speed": float(v.get("eleven_speed", 1.0))}}
        if "flash" in model or "turbo" in model:
            body["language_code"] = "ru"  # only these models accept it; stops Russian being read as another language
        r = requests.post(f"{self.API}/text-to-speech/{v.get('eleven_voice') or self.VOICE_DEFAULT}",
                          params={"output_format": "mp3_44100_128"},
                          headers={"xi-api-key": key, "Content-Type": "application/json"}, json=body, timeout=20)
        if r.status_code in (401, 402) or "quota_exceeded" in r.text:
            self.dead_until = time.time() + 600
        if r.status_code != 200:
            raise RuntimeError(f"ElevenLabs HTTP {r.status_code}: {r.text[:200]}")
        return decode_mp3(r.content)

    def info(self):
        """Remaining credits for the settings panel."""
        import requests
        key = self.key()
        if not key:
            return {"ok": False, "error": "ключ не задан"}
        r = requests.get(f"{self.API}/user/subscription", headers={"xi-api-key": key}, timeout=10)
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        j = r.json()
        used, limit = j.get("character_count", 0), j.get("character_limit", 0)
        self.dead_until = 0
        return {"ok": True, "tier": j.get("tier", ""), "used": used, "limit": limit, "left": max(0, limit - used)}


class SapiEngine:
    name = "sapi"

    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()

    def synth(self, text):
        import pythoncom
        import win32com.client
        with self.lock:
            pythoncom.CoInitialize()
            path = Path(tempfile.gettempdir()) / "jarvis_sapi.wav"
            voice = win32com.client.Dispatch("SAPI.SpVoice")
            for tok in voice.GetVoices():
                if "ru" in tok.GetAttribute("Language").lower() or "419" in tok.GetAttribute("Language"):
                    voice.Voice = tok
                    break
            stream = win32com.client.Dispatch("SAPI.SpFileStream")
            stream.Open(str(path), 3)
            voice.AudioOutputStream = stream
            voice.Speak(text)
            stream.Close()
            return read_wav(str(path))


class SileroEngine:
    """Silero TTS v5 (Russian): local, ~0.1 s per second of speech even on a CPU, never babbles.
    Model: models.silero.ai (CC BY-NC-SA, personal use)."""
    name = "silero"
    URL = "https://models.silero.ai/models/tts/ru/v5_ru.pt"
    SPEAKERS = ["aidar", "eugene", "baya", "kseniya", "xenia"]

    def __init__(self, cfg, log=print):
        self.cfg = cfg
        self.log = log
        self.model = None
        self.ready = False
        self.error = None
        self.lock = threading.Lock()
        self.path = DATA / "models" / "silero_v5_ru.pt"
        self.retry_at = 0.0         # a failed start is not repeated at every phrase
        self.fails = 0

    def _download(self):
        """The model file, resumable: a dropped connection continues from the same byte, a few attempts, progress in the log.
        (It used to restart from zero with no timeout, and again on every spoken phrase: "постоянно качает Silero".)"""
        import requests
        url = self.cfg.get("voice.silero_url") or self.URL
        tmp = self.path.with_suffix(".part")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        err = None
        for attempt in range(4):
            have = tmp.stat().st_size if tmp.exists() else 0
            try:
                with requests.get(url, stream=True, timeout=(15, 45), headers={"Range": f"bytes={have}-"} if have else {}) as r:
                    if r.status_code == 416:                       # the file is complete already
                        break
                    r.raise_for_status()
                    resumed = have > 0 and r.status_code == 206
                    total = int(r.headers.get("content-length") or 0) + (have if resumed else 0)
                    got, shown = (have if resumed else 0), time.time()
                    with open(tmp, "ab" if resumed else "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            f.write(chunk)
                            got += len(chunk)
                            if time.time() - shown > 10:
                                shown = time.time()
                                self.log(f"Голос Silero: скачано {got // 2**20} из {total // 2**20 or '?'} МБ")
                    if total and got < total:
                        raise IOError(f"обрыв связи на {got // 2**20} из {total // 2**20} МБ")
                err = None
                break
            except Exception as e:
                err = e
                self.log(f"Голос Silero: попытка {attempt + 1} из 4 не вышла ({str(e)[:90]})", "warn")
                time.sleep(2 + attempt * 3)
        if err is not None:
            raise RuntimeError(f"не удалось скачать голос с models.silero.ai ({str(err)[:80]}). Скачанное сохранено, следующая попытка продолжит "
                               f"с того же места. Можно скачать файл вручную: {url} -> {self.path}")
        if tmp.stat().st_size < 20 * 2**20:
            tmp.unlink(missing_ok=True)
            raise RuntimeError("скачался слишком маленький файл (не модель): проверьте интернет, прокси и VPN")
        os.replace(tmp, self.path)

    def start(self):
        if not self.ready and time.time() < self.retry_at:
            return False
        with self.lock:
            if self.ready:
                return True
            try:
                import torch
                if not self.path.exists():
                    self.log("Скачиваю голос Silero (~60 МБ)...")
                    self._download()
                torch.set_num_threads(max(2, min(6, (os.cpu_count() or 4) // 2)))
                model = torch.package.PackageImporter(str(self.path)).load_pickle("tts_models", "model")
                dev = "cuda" if torch.cuda.is_available() and self.cfg.get("voice.silero_gpu", True) else "cpu"
                model.to(torch.device(dev))
                model.apply_tts(text="Готов.", speaker="aidar", sample_rate=48000)  # first call is ~4 s, do it now
                self.model = model
                self.ready = True
                self.error = None
                self.log(f"Голос Silero готов ({dev})")
                return True
            except Exception as e:
                self.error = f"Silero: {e}"
                self.log(self.error)
                self.fails += 1
                self.retry_at = time.time() + min(600, 60 * self.fails)   # 1, 2, 3 … 10 minutes, not at every phrase
                if self.path.exists() and "скач" not in str(e):           # the file is there but does not load: broken, fetch again later
                    try:
                        self.path.replace(self.path.with_suffix(".bad"))
                    except OSError:
                        pass
                return False

    def synth(self, text):
        if not self.ready:
            if not self.path.exists():
                # the file has to be downloaded (minutes, maybe): never hold the speaking thread for it. It runs in the
                # background and the next engine (edge / Windows voice) speaks meanwhile
                if not self.lock.locked() and time.time() >= self.retry_at:
                    threading.Thread(target=self.start, daemon=True, name="silero-start").start()
                raise RuntimeError(self.error or "Голос Silero скачивается, пока говорю резервным голосом")
            # the file is there: loading takes a few seconds — wait for it, the phrase stays in the same voice (as before)
            if not self.start():
                raise RuntimeError(self.error)
        from .textnorm import normalize_ru
        t = normalize_ru(text)
        if not re.search(r"[а-яё]", t, re.I):
            raise RuntimeError("нечего произносить")
        spk = self.cfg.get("voice.silero_speaker", "aidar")
        rate = self.cfg.get("voice.silero_rate", "medium")
        pitch = self.cfg.get("voice.silero_pitch", "medium")
        with self.lock:
            if rate == "medium" and pitch == "medium":
                audio = self.model.apply_tts(text=t, speaker=spk, sample_rate=48000, put_accent=True, put_yo=True)
            else:
                ssml = f'<speak><prosody rate="{rate}" pitch="{pitch}">{t}</prosody></speak>'
                audio = self.model.apply_tts(ssml_text=ssml, speaker=spk, sample_rate=48000)
        return audio.numpy().astype(np.float32), 48000


class XttsEngine:
    """Runs xtts_worker.py in the isolated .xtts-venv, keeps the model loaded."""
    name = "xtts"

    def __init__(self, cfg, log=print):
        self.cfg = cfg
        self.log = log
        self.proc = None
        self.lock = threading.Lock()
        self.ready = False
        self.error = None

    @staticmethod
    def available():
        return XTTS_PY.exists()

    def ref_path(self):
        p = self.cfg.get("voice.xtts_ref") or ""
        return Path(p) if p and Path(p).exists() else REF_DEFAULT

    def start(self):
        if not hasattr(self, "_start_lock"):
            self._start_lock = threading.Lock()
        import time
        if self.error and time.time() - getattr(self, "failed_at", 0) < 90:
            return False  # do not hammer a broken install
        if not self._start_lock.acquire(blocking=False):
            return False  # already loading in another thread
        try:
            ok = self._start()
            if not ok:
                self.failed_at = time.time()
            else:
                self.error = None
            return ok
        finally:
            self._start_lock.release()

    def _start(self):
        if self.proc and self.proc.poll() is None and self.ready:
            return True
        if not self.available():
            self.error = "XTTS не установлен (нет .xtts-venv). Запустите install_xtts.bat"
            return False
        ensure_reference(self.cfg)
        env = dict(os.environ, PYTHONIOENCODING="utf-8", COQUI_TOS_AGREED="1")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.proc = subprocess.Popen(
            [str(XTTS_PY), "-u", str(ROOT / "jarvis" / "xtts_worker.py"), str(self.ref_path())],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", env=env, creationflags=flags)
        threading.Thread(target=self._drain_err, daemon=True).start()
        line = self.proc.stdout.readline()
        try:
            msg = json.loads(line)
        except Exception:
            msg = {"error": line.strip() or "worker died"}
        if msg.get("ready"):
            self.ready = True
            self.log(f"XTTS готов ({msg.get('device')})")
            return True
        self.error = msg.get("error", "XTTS не запустился")
        self.log("XTTS ошибка: " + self.error)
        return False

    def _drain_err(self):
        p = self.proc
        for line in p.stderr:
            line = line.strip()
            if line and ("Error" in line or "Traceback" in line):
                self.log("[xtts] " + line[:300])

    def synth(self, text, trim=True):
        if not self.ready:
            # never block speech on model loading: load in background, fall back to edge meanwhile
            threading.Thread(target=self.start, daemon=True).start()
            raise RuntimeError(self.error or "XTTS загружается, временно говорю резервным голосом")
        with self.lock:
            out = Path(tempfile.gettempdir()) / f"jarvis_xtts_{threading.get_ident()}.wav"
            req = {"text": text, "out": str(out), "speed": float(self.cfg.get("voice.xtts_speed", 1.0))}
            self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            line = self.proc.stdout.readline()
            if not line:
                self.ready = False
                raise RuntimeError("XTTS worker crashed")
            msg = json.loads(line)
            if msg.get("error"):
                raise RuntimeError(msg["error"])
            data, sr = read_wav(str(out))
            return (TailTrimmer(text, req["speed"], sr).feed(data) if trim else data), sr

    def synth_stream(self, text, cancelled=lambda: False):
        """Yields float32 chunks at 24 kHz as XTTS generates them."""
        if not self.ready:
            threading.Thread(target=self.start, daemon=True).start()
            raise RuntimeError(self.error or "XTTS загружается")
        import base64
        with self.lock:
            req = {"text": text, "stream": True, "speed": float(self.cfg.get("voice.xtts_speed", 1.0))}
            self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            trim = TailTrimmer(text, req["speed"])
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    self.ready = False
                    raise RuntimeError("XTTS worker crashed")
                msg = json.loads(line)
                if msg.get("error"):
                    raise RuntimeError(msg["error"])
                if msg.get("ok"):
                    return
                if "chunk" in msg and not cancelled() and not trim.done:
                    pcm = np.frombuffer(base64.b64decode(msg["chunk"]), np.int16)
                    part = trim.feed(pcm.astype(np.float32) / 32767.0)  # drops trailing babble
                    if len(part):
                        yield part

    def reload(self):
        with self.lock:
            if self.proc:
                self.proc.kill()
            self.proc = None
            self.ready = False


def ensure_reference(cfg):
    """Build a British male reference clip with edge-tts (en-GB-RyanNeural) if none exists."""
    if REF_DEFAULT.exists():
        return REF_DEFAULT
    eng = EdgeEngine(cfg)
    data, sr = eng.synth(REF_TEXT, voice="en-GB-RyanNeural", rate="-4%", pitch="-2Hz")
    write_wav(REF_DEFAULT, data, sr)
    return REF_DEFAULT


# ---------------------------------------------------------------- facade
class TTS:
    """Speaks sentence by sentence. Per sentence: phrase cache (instant) -> XTTS streaming
    (first sound after ~0.3-0.5 s) -> whole-sentence synthesis with edge / sapi as fallback."""

    def __init__(self, cfg, player, log=print, on_state=None):
        self.cfg = cfg
        self.player = player
        self.log = log
        self.on_state = on_state or (lambda s: None)
        self.engines = {"edge": EdgeEngine(cfg), "sapi": SapiEngine(cfg), "xtts": XttsEngine(cfg, log),
                        "silero": SileroEngine(cfg, log), "elevenlabs": ElevenLabsEngine(cfg)}
        self.gen = 0
        self.last_text = ""
        self.current_text = ""
        self.mem = {}
        threading.Thread(target=prune_cache, daemon=True, name="tts-prune").start()
        # set by Core: Whisper word alignment that finds where the real phrase ends (cuts XTTS babble)
        self.aligner = None
        self.aligner_ready = lambda: False

    SHORT = 45  # phrases up to this length are rendered whole and verified instead of streamed

    def warmup(self, phrases=()):
        engine = self.cfg.get("voice.engine")
        if engine not in ("xtts", "silero", "elevenlabs"):
            return

        def go():
            if engine in ("silero", "elevenlabs"):
                # local fallback must be ready too; acknowledgements get pre-rendered for zero latency
                # (for elevenlabs this costs credits once, then the phrases come from the cache)
                if not self.engines["silero"].start() and engine == "silero":
                    return
                if engine == "elevenlabs" and not self.engines["elevenlabs"].key():
                    return
                for p in phrases:
                    while self.current_text:
                        time.sleep(0.3)
                    try:
                        p = clean_for_speech(p)
                        if self._cache_get(p) is None:
                            data, sr = self.engines[engine].synth(p)
                            self._cache_put(p, self._fx_whole(data, sr))
                    except Exception:
                        if engine == "elevenlabs":
                            break  # bad key or no credits: do not retry every phrase
                return
            threading.Thread(target=self.engines["silero"].start, daemon=True).start()  # local fallback
            x = self.engines["xtts"]
            if not x.start():
                return
            for _ in range(120):  # verification needs Whisper; it loads in parallel
                if self.aligner_ready():
                    break
                time.sleep(0.5)
            # pre-render frequent short phrases so acknowledgements play instantly and clean
            for p in phrases:
                while self.current_text:
                    time.sleep(0.5)  # do not compete with live speech
                try:
                    p = clean_for_speech(p)
                    if self._cache_get(p) is None:
                        data = self._render_verified(p)
                        if data is not None:
                            self._cache_put(p, data)
                except Exception:
                    break
        threading.Thread(target=go, daemon=True, name="tts-warmup").start()

    def _render_verified(self, s, tries=2):
        """Whole-phrase XTTS + Whisper check: cut right after the last word, retry once if garbled."""
        x = self.engines["xtts"]
        speed = float(self.cfg.get("voice.xtts_speed", 1.0))
        fallback = None
        use_align = self.aligner is not None and self.aligner_ready()
        for _ in range(tries if use_align else 1):
            data, sr = x.synth(s, trim=not use_align)
            if not use_align:
                return self._fx_whole(data, sr)
            end = self.aligner(data, sr, s)
            if end is None:
                fallback = fallback or (data, sr)
                continue
            data = data[:min(len(data), int((end + 0.12) * sr))].copy()
            if len(data) > 600:
                data[-600:] *= np.linspace(1, 0, 600, dtype=np.float32)
            return self._fx_whole(data, sr)
        if fallback:
            data, sr = fallback
            return self._fx_whole(TailTrimmer(s, speed, sr).feed(data), sr)
        return None

    # ----- cache
    def _key(self, text):
        v = self.cfg.get("voice")
        eng = v.get("engine", "edge")
        if eng == "xtts":
            ref = self.engines["xtts"].ref_path()
            sig = f"xtts|{ref}|{ref.stat().st_mtime if ref.exists() else 0}|{v.get('xtts_speed')}"
        elif eng == "silero":
            sig = f"silero|{v.get('silero_speaker', 'aidar')}|{v.get('silero_rate')}|{v.get('silero_pitch')}"
        elif eng == "elevenlabs":
            sig = f"elevenlabs|{v.get('eleven_voice')}|{v.get('eleven_model')}|{v.get('eleven_speed')}"
        else:
            sig = f"{eng}|{v.get('edge_voice')}|{v.get('edge_rate')}|{v.get('edge_pitch')}"
        # norm2: number reading changed (dates, "две минуты", "до шестнадцати"): old cached audio must not be reused
        sig += f"|{v.get('effect')}|norm2|{text.strip().lower()}"
        return hashlib.sha1(sig.encode()).hexdigest()

    def _cache_get(self, text):
        if len(text) > 90:
            return None
        k = self._key(text)
        if k in self.mem:
            return self.mem[k]
        p = CACHE_DIR / f"{k}.npy"
        if p.exists():
            try:
                self.mem[k] = np.load(p)
                try:
                    os.utime(p)  # "recently used" for prune_cache()
                except OSError:
                    pass
                return self.mem[k]
            except Exception:
                pass
        return None

    def _cache_put(self, text, data24k, key=None):
        if len(text) > 90 or data24k is None or not len(data24k):
            return
        k = key or self._key(text)
        if key and key != self._key(text):
            return  # the voice settings changed while this was being synthesized
        self.mem[k] = data24k
        try:
            np.save(CACHE_DIR / f"{k}.npy", data24k.astype(np.float32))
        except Exception:
            pass

    def _fx_whole(self, data, sr):
        from .audio import resample
        data = resample(data, sr, 24000)
        return jarvis_effect(data, 24000) if self.cfg.get("voice.effect", True) else data

    # ----- one sentence
    def _say_sentence(self, s, gen):
        """Queue audio for one sentence; returns when all of it is queued (playback continues)."""
        vol = float(self.cfg.get("voice.volume", 0.9))
        if self.cfg.get("night.enabled", False):
            vol *= float(self.cfg.get("night.volume", 0.45))
        self.player.volume = vol
        key = self._key(s)  # taken before synthesis: a voice change meanwhile must not file old audio under the new voice
        cached = self._cache_get(s)
        if cached is not None:
            self.player.play(cached, 24000)
            return
        if (self.cfg.get("voice.engine") == "xtts" and self.engines["xtts"].ready and len(s) <= self.SHORT
                and self.aligner_ready()):
            try:
                data = self._render_verified(s)
                if data is not None and gen == self.gen:
                    self.player.play(data, 24000)
                    self._cache_put(s, data, key)
                    return
            except Exception as e:
                self.log(f"TTS xtts: {e}")
        if self.cfg.get("voice.engine") == "xtts" and self.engines["xtts"].ready:
            fx = StreamFX(24000) if self.cfg.get("voice.effect", True) else None
            parts = []
            try:
                for chunk in self.engines["xtts"].synth_stream(s, cancelled=lambda: gen != self.gen):
                    if gen != self.gen:
                        continue  # drain the worker, play nothing
                    c = fx.process(chunk) if fx else chunk
                    parts.append(c)
                    self.player.play(c, 24000)
                if parts and gen == self.gen:
                    self._cache_put(s, np.concatenate(parts), key)
                return
            except Exception as e:
                self.log(f"TTS xtts stream: {e}")
        for name in dict.fromkeys([self.cfg.get("voice.engine", "silero"), "silero", "edge", "sapi"]):
            if name == "xtts" and not self.engines["xtts"].ready:
                continue
            try:
                data, sr = self.engines[name].synth(s)
                data = self._fx_whole(data, sr)
                if gen == self.gen:
                    self.player.play(data, 24000)
                    if name == self.cfg.get("voice.engine"):
                        self._cache_put(s, data, key)
                return
            except Exception as e:
                self.log(f"TTS {name}: {e}")

    def _wait_done(self, gen):
        while self.player.voice_busy() and gen == self.gen:
            time.sleep(0.03)

    # ----- public
    def speak(self, text, block=False):
        text = clean_for_speech(text)
        if not text or not self.cfg.get("voice.enabled", True):
            return
        self.last_text = text
        self.gen += 1
        gen = self.gen
        # "speaking" from this very moment: the worker thread set it a little later, and follow_up() called right
        # after say() saw silence and opened the no-name window at the start of the reply, so it ran out mid-speech
        self.current_text = text
        t = threading.Thread(target=self._speak_worker, args=(text, gen), daemon=True)
        t.start()
        if block:
            t.join()

    def _speak_worker(self, text, gen):
        self.current_text = text
        self.on_state("speaking")
        for s in split_sentences(text):
            if gen != self.gen:
                return
            self._say_sentence(s, gen)
        self._wait_done(gen)
        if gen == self.gen:
            self.current_text = ""
            self.on_state("idle")

    def stop(self):
        self.gen += 1
        self.player.stop()
        self.current_text = ""
        self.on_state("idle")

    def stream(self):
        """Feeder for LLM token streams: feed(chunk) speaks completed sentences, close() flushes."""
        return SentenceFeeder(self)


class SentenceFeeder:
    def __init__(self, tts):
        self.tts = tts
        self.buf = ""
        self.q = []
        tts.gen += 1
        self.gen = tts.gen
        self.cv = threading.Condition()
        self.closed = False
        self.spoken = []
        threading.Thread(target=self._worker, daemon=True).start()

    def say_now(self, text):
        """Queue a filler phrase immediately (e.g. while the AI searches the web)."""
        self._push(text)

    def feed(self, chunk):
        if getattr(self, "muted", False):
            return
        self.buf += chunk
        self.buf = re.sub(r"<jarvis>.*?</jarvis>|\{\s*\"tool\"\s*:[^{}]*\}", " ", self.buf, flags=re.S)
        if re.search(r"\{\s*\"tool\"[^}]*$|\{\s*\"?t?o?o?l?$", self.buf):
            return  # a bare JSON tool call is still arriving: never read it aloud
        # web answers end with a list of links: show it in the chat, never read it aloud
        m = re.search(r"(?im)^\W*(?:sources|источники|ссылки)\s*:", self.buf)
        if m:
            self.buf = self.buf[:m.start()]
            self.muted = True
            self._push(self.buf)
            self.buf = ""
            return
        if "<jarvis>" in self.buf:
            return
        m = None
        for m in re.finditer(r"[.!?…\n](\s|$)", self.buf):
            pass
        # the first sentence goes out as soon as it ends, later ones in bigger pieces
        if m and m.end() > (8 if not self.spoken and not self.q else 25):
            ready, self.buf = self.buf[:m.end()], self.buf[m.end():]
            self._push(ready)

    def _push(self, text):
        text = clean_for_speech(text)
        if not text:
            return
        with self.cv:
            self.q.extend(split_sentences(text))
            self.cv.notify()

    def close(self):
        self.buf = re.sub(r"<jarvis>.*|\{\s*\"tool\".*", " ", self.buf, flags=re.S).replace("</jarvis>", " ")
        self._push(self.buf)
        self.buf = ""
        with self.cv:
            self.closed = True
            self.cv.notify()

    def _worker(self):
        tts = self.tts
        if not tts.cfg.get("voice.enabled", True):
            return
        started = False
        while True:
            with self.cv:
                while not self.q and not self.closed:
                    self.cv.wait(0.5)
                    if self.gen != tts.gen:
                        return
                if not self.q and self.closed:
                    break
                s = self.q.pop(0)
            if self.gen != tts.gen:
                return
            if not started:
                tts.on_state("speaking")
                started = True
            self.spoken.append(s)
            tts.current_text = " ".join(self.spoken)
            tts._say_sentence(s, self.gen)
        tts.last_text = " ".join(self.spoken)
        tts._wait_done(self.gen)
        if self.gen == tts.gen and started:
            tts.current_text = ""
            tts.on_state("idle")


