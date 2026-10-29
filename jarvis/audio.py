"""Audio output: one player thread, interruptible playback, generated UI sounds and the Jarvis voice effect."""
import collections
import re
import threading
import time

import numpy as np
import sounddevice as sd

SR = 24000


def _tone(freqs, dur, vol=0.25, attack=0.005, decay=None, sr=SR):
    t = np.arange(int(sr * dur)) / sr
    sig = np.zeros_like(t)
    for f in freqs:
        sig += np.sin(2 * np.pi * f * t)
    sig /= max(1, len(freqs))
    env = np.minimum(1, t / attack) * np.exp(-t / (decay or dur / 3))
    return (sig * env * vol).astype(np.float32)


def _cat(*parts):
    return np.concatenate(parts).astype(np.float32)


def _sil(d):
    return np.zeros(int(SR * d), np.float32)


def _suit():
    """The suit powering up (the boot screen): a low hum rising, a breath of air, three chimes and a soft chord."""
    rng = np.random.default_rng(7)
    n = int(SR * 2.6)
    t = np.arange(n) / SR
    # the hum: 48 -> 120 Hz with a little harmonic grit, swelling in and settling
    f = 48 + 72 * np.clip(t / 1.4, 0, 1) ** 1.6
    ph = 2 * np.pi * np.cumsum(f) / SR
    hum = (np.sin(ph) + .35 * np.sin(2 * ph) + .15 * np.sin(3 * ph)) * np.clip(t / .5, 0, 1) * np.exp(-np.clip(t - 1.5, 0, None) * 2.2) * .22
    # the air: noise brightening with the spin-up
    noise = rng.standard_normal(n)
    a = np.exp(-2 * np.pi * (300 + 2400 * np.clip(t / 1.3, 0, 1)) / SR)
    air = np.zeros(n)
    acc = 0.0
    for i in range(n):           # a simple moving one-pole low-pass (its cutoff rises)
        acc = a[i] * acc + (1 - a[i]) * noise[i]
        air[i] = acc
    air *= np.sin(np.pi * np.clip(t / 1.5, 0, 1)) * .12
    out = hum + air
    # three chimes and the chord that holds
    for start, freqs, vol, dec in ((1.15, [880], .16, .35), (1.32, [1109], .15, .35), (1.49, [1319], .16, .45),
                                   (1.7, [440, 659, 880, 1319], .13, .9)):
        k = int(SR * start)
        tone = _tone(freqs, 2.6 - start, vol, attack=.004, decay=dec)
        out[k:k + len(tone)] += tone[:n - k]
    peak = np.max(np.abs(out)) or 1
    return (out / peak * .55).astype(np.float32)


SOUNDS = {
    "wake": _cat(_tone([880, 1320], 0.09, 0.18), _tone([1320, 1760], 0.12, 0.16)),
    "done": _cat(_tone([1320], 0.07, 0.12), _tone([990], 0.1, 0.1)),
    "error": _cat(_tone([330, 311], 0.18, 0.2), _tone([247], 0.25, 0.2)),
    "notify": _cat(_tone([784, 1175], 0.14, 0.2), _sil(0.04), _tone([1047, 1568], 0.25, 0.2)),
    "boot": _cat(*[_tone([220 * (1.12 ** i), 440 * (1.12 ** i)], 0.07, 0.08 + i * 0.01) for i in range(10)],
                 _tone([880, 1318, 1760], 0.7, 0.18, decay=0.35)),
    "alarm": _cat(*[_cat(_tone([988, 1480], 0.16, 0.3), _sil(0.06)) for _ in range(4)], _sil(0.4)),
    "suit": _suit(),
}


def jarvis_effect(x, sr):
    """Subtle studio/HUD colouring: high-pass, gentle compression and a short room reflection."""
    if x.size == 0:
        return x
    # one-pole high-pass ~90 Hz
    a = np.exp(-2 * np.pi * 90 / sr)
    y = np.empty_like(x)
    prev_x = prev_y = 0.0
    # vectorised enough for short clips; use lfilter-like loop via numpy cumulative trick is overkill
    try:
        from scipy.signal import lfilter
        y = lfilter([1, -1], [1, -a], x).astype(np.float32)
    except Exception:
        for i, v in enumerate(x):
            prev_y = a * (prev_y + v - prev_x)
            prev_x = v
            y[i] = prev_y
    # soft compression
    y = np.tanh(y * 1.6) / np.tanh(1.6)
    # early reflections (small metallic room)
    out = y.copy()
    for delay_ms, g in ((23, 0.16), (37, 0.11), (53, 0.07)):
        d = int(sr * delay_ms / 1000)
        out[d:] += y[:-d] * g
    peak = np.max(np.abs(out)) or 1
    return (out / peak * 0.92).astype(np.float32)


class StreamFX:
    """Stateful version of jarvis_effect for streamed chunks (no per-chunk normalisation, reverb carries over)."""

    def __init__(self, sr):
        self.sr = sr
        self.a = np.exp(-2 * np.pi * 90 / sr)
        self.zi = None
        self.taps = [(int(sr * ms / 1000), g) for ms, g in ((23, 0.16), (37, 0.11), (53, 0.07))]
        self.hist = np.zeros(max(d for d, _ in self.taps), np.float32)

    def process(self, x):
        from scipy.signal import lfilter, lfilter_zi
        if self.zi is None:
            self.zi = lfilter_zi([1, -1], [1, -self.a]) * 0
        y, self.zi = lfilter([1, -1], [1, -self.a], x, zi=self.zi)
        y = (np.tanh(y * 1.6) / np.tanh(1.6)).astype(np.float32)
        buf = np.concatenate([self.hist, y])
        h = len(self.hist)
        out = y.copy()
        for d, g in self.taps:
            out += buf[h - d:h - d + len(y)] * g
        self.hist = buf[-h:]
        return (out * 0.8).astype(np.float32)


class TailTrimmer:
    """XTTS often babbles a few syllables after the real end of a short phrase ("Готово, сэр. Паньта!").
    Speech length is predictable from the text (~0.068 s per character), so after ~80 % of the expected
    duration the first pause ends the phrase, and a hard cap cuts anything far too long."""

    FRAME = 480  # 20 ms at 24 kHz

    def __init__(self, text, speed=1.0, sr=24000):
        chars = (len(re.sub(r"[^\w]", "", text)) + 0.5 * len(re.findall(r"[,;:—-]", text))
                 + 3 * len(re.findall(r"\d", text)))  # digits are read as whole words
        expected = chars * 0.068 / max(0.5, speed) + 0.2
        # never stop at the pause of the last comma ("Готово, сэр."): the part after it must get a chance
        m = list(re.finditer(r"[,;:—-]", text.rstrip(" .!?…")))
        before = len(re.sub(r"[^\w]", "", text[:m[-1].start()])) / max(1, chars) if m else 0
        self.min_len = int(max(expected * 0.85, before * expected + 0.3) * sr)
        self.max_len = int((expected * 1.25 + 0.3) * sr)
        self.gap = int(0.15 * sr)
        self.pos = 0
        self.quiet = 0
        self.done = False
        self.peak = 0.02

    def feed(self, x):
        """Returns the part of x to play; sets self.done when the phrase is over."""
        if self.done:
            return x[:0]
        out_end = len(x)
        for i in range(0, len(x), self.FRAME):
            fr = x[i:i + self.FRAME]
            rms = float(np.sqrt(np.mean(fr ** 2))) if len(fr) else 0
            self.peak = max(self.peak, rms)
            g = self.pos + i
            if rms < self.peak * 0.08:
                if self.quiet == 0:
                    self.quiet_start = g
                self.quiet += len(fr)
            else:
                self.quiet = 0
            # only a pause that STARTS after the threshold ends the phrase (comma pauses before it don't)
            if self.quiet >= self.gap and getattr(self, "quiet_start", 0) >= self.min_len:
                out_end, self.done = max(0, i - self.quiet + self.FRAME * 3), True
                break
            if g >= self.max_len:
                out_end, self.done = i, True
                break
        y = x[:out_end].copy()
        if self.done and len(y) > 600:
            y[-600:] *= np.linspace(1, 0, 600, dtype=np.float32)  # 25 ms fade, no click
        self.pos += len(x)
        return y


def resample(x, sr_from, sr_to):
    if sr_from == sr_to or x.size == 0:
        return x.astype(np.float32)
    n = int(len(x) * sr_to / sr_from)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


class Player:
    """One persistent output stream fed from a clip queue by the audio callback.

    Streamed TTS chunks play back-to-back without gaps or clicks, stop() is instant,
    and opening a device per clip (≈100 ms each) is gone."""

    def __init__(self, on_level=None):
        self.q = collections.deque()
        self.lock = threading.Lock()
        self.cur = None
        self.cur_kind = None
        self.pos = 0
        self.volume = 0.9
        self.on_level = on_level
        self.level = 0.0
        self.last_end = 0.0
        self.stream = None
        self._open()
        threading.Thread(target=self._level_loop, daemon=True, name="player-level").start()

    def _open(self):
        try:
            self.stream = sd.OutputStream(samplerate=SR, channels=1, dtype="float32", blocksize=480,
                                          latency="low", callback=self._cb)
            self.stream.start()
        except Exception as e:
            print("[audio] output open error:", e)
            self.stream = None

    def _cb(self, outdata, frames, t, status):
        out = outdata[:, 0]
        filled = 0
        voice = False
        with self.lock:
            while filled < frames:
                if self.cur is None:
                    if not self.q:
                        break
                    self.cur, self.cur_kind = self.q.popleft()
                    self.pos = 0
                n = min(frames - filled, len(self.cur) - self.pos)
                out[filled:filled + n] = self.cur[self.pos:self.pos + n] * self.volume
                if self.cur_kind == "voice":
                    voice = True
                filled += n
                self.pos += n
                if self.pos >= len(self.cur):
                    self.cur = None
                    if not self.q:
                        self.last_end = time.time()
        if filled < frames:
            out[filled:] = 0
        self.level = float(np.sqrt(np.mean(out[:filled] ** 2)) * 4) if voice and filled else 0.0

    def _level_loop(self):
        last = -1
        while True:
            time.sleep(0.05)
            if self.on_level and (self.level > 0 or last != 0):
                self.on_level(self.level)
                last = self.level
            if self.stream is None:
                time.sleep(2)
                self._open()

    def play(self, data, sr=SR, kind="voice"):
        clip = resample(np.asarray(data, np.float32), sr, SR)
        with self.lock:
            self.q.append((clip, kind))

    def sound(self, name):
        if name in SOUNDS:
            self.play(SOUNDS[name], SR, "sound")

    def stop(self):
        with self.lock:
            self.q.clear()
            if self.cur is not None:
                self.last_end = time.time()
            self.cur = None

    def busy(self):
        return self.cur is not None or bool(self.q)

    def voice_busy(self):
        with self.lock:
            return (self.cur is not None and self.cur_kind == "voice") or any(k == "voice" for _, k in self.q)


