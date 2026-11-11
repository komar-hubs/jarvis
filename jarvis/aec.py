"""Subtracting the PC's own sound from the microphone: Jarvis's voice, a video, music, a game.

Everything the speakers play is captured as it is sent to them (WASAPI loopback, the capture of jarvis/audiomem.py);
an adaptive filter learns how that sound reaches the microphone through the speakers and the room and subtracts it
before the speech recogniser hears it. The idea of Discord's and Zoom's echo cancellers (Speex/WebRTC "MDF":
partitioned-block frequency-domain adaptive filter).

On the benchmark of synthetic phrases (Silero voices) mixed with sound played "through the speakers and a room",
GigaAM's word errors: Jarvis's reply over the user 81% -> 21%, a video in the background 27% -> 16%, music 7.7% -> 6.7%;
the wake word was heard 10% -> 55%, 60% -> 71%, 81% -> 84% (nothing got worse, no false wakes).

- The two devices have different clocks and buffers: the speakers->microphone delay (20-150 ms) is measured by
  cross-correlation (GCC-PHAT) every few seconds while something plays, and the filter covers 270 ms after it.
- Silence in the speakers (and headphones, where nothing leaks into the microphone): the frame passes untouched.
- A safety: a frame that came out louder than it went in (the filter went wrong) is replaced by the original.
Switch: config stt.aec (Settings → «Приватность»). Nothing is stored."""
import threading
import time

import numpy as np

SR = 16000


class EchoCanceller:
    B = 240                 # block: two per 30 ms microphone frame
    P = 18                  # partitions: 18 x 240 = 270 ms of echo path after the measured delay

    def __init__(self, cfg, log=print):
        self.cfg = cfg
        self.log = log
        self.lock = threading.Lock()
        self.ref = np.zeros(SR * 8, np.float32)      # the speakers' sound, the newest sample last
        self.ref_t = 0.0                               # wall time of the newest reference sample
        self.mic = np.zeros(SR * 3, np.float32)        # the raw microphone, for the delay measurement
        self.mic_t = 0.0
        self.delay = 0.06                              # speakers -> microphone, seconds (measured)
        self.delay_t = 0.0
        self.delay_ok = False
        self.active = False
        self.stats = {"frames": 0, "active": 0, "fallback": 0, "ms": 0.0, "resets": 0}
        self._on_t = 0.0
        self._on = True
        self.reset()

    # ---------------------------------------------------------------- state
    def reset(self):
        n = self.B + 1
        self.W = np.zeros((self.P, n), np.complex64)
        self.X = np.zeros((self.P, n), np.complex64)
        self.ptr = 0
        self.xbuf = np.zeros(2 * self.B, np.float32)
        self.pw = np.full(n, 1e-6, np.float32)

    def enabled(self):
        if time.time() - self._on_t > 2:
            self._on_t, self._on = time.time(), bool(self.cfg.get("stt.aec", False))
        return self._on

    # ---------------------------------------------------------------- the speakers' side
    def feed(self, x, t_end):
        """A piece of what the speakers play, 16 kHz mono (int16 or float), ending at wall time t_end. A gap since the
        previous piece (loopback sends nothing while the PC is silent) is filled with silence, so time stays aligned."""
        x = np.asarray(x, np.float32)
        if x.dtype != np.float32 or (len(x) and np.abs(x).max() > 2):
            x = x / 32768.0
        with self.lock:
            gap = int(round((t_end - len(x) / SR - self.ref_t) * SR)) if self.ref_t else 0
            if 0 < gap < len(self.ref):
                x = np.concatenate([np.zeros(gap, np.float32), x])
            elif gap >= len(self.ref):
                self.ref[:] = 0
            x = x[-len(self.ref):]
            self.ref = np.concatenate([self.ref[len(x):], x])
            self.ref_t = t_end

    def _ref_ending(self, t_end, n):
        """n reference samples that reached the microphone by t_end (shifted by the measured delay)."""
        with self.lock:
            back = int(round((self.ref_t - (t_end - self.delay)) * SR))
            if back < 0:                                   # not played yet (the loopback lags): silence
                back = 0
            end = len(self.ref) - back
            if end <= 0:
                return np.zeros(n, np.float32)
            seg = self.ref[max(0, end - n):end]
        return np.concatenate([np.zeros(n - len(seg), np.float32), seg]) if len(seg) < n else seg

    # ---------------------------------------------------------------- the microphone's side
    def process(self, frame, t_cap):
        """A 30 ms microphone frame (int16 bytes or array) captured at t_cap -> the frame without the PC's sound."""
        pcm = np.frombuffer(frame, np.int16) if isinstance(frame, (bytes, bytearray)) else frame
        x = pcm.astype(np.float32) / 32768.0
        self.stats["frames"] += 1
        self.mic = np.concatenate([self.mic[len(x):], x])
        self.mic_t = t_cap
        if not self.enabled() or not self.ref_t or t_cap - self.ref_t > 1.0:
            self.active = False                            # nothing played for a second: nothing to subtract
            return frame
        r = self._ref_ending(t_cap, len(x))
        if float(np.mean(r ** 2)) < 1e-7:
            self.active = False
            return frame
        self.active = True
        self.stats["active"] += 1
        if t_cap - self.delay_t > 3:
            self._measure_delay(t_cap)
        t0 = time.perf_counter()
        y = np.empty_like(x)
        for k in range(0, len(x), self.B):
            y[k:k + self.B] = self._block(x[k:k + self.B], r[k:k + self.B])
        self.stats["ms"] += (time.perf_counter() - t0) * 1000
        if float(np.mean(y ** 2)) > float(np.mean(x ** 2)) * 1.05:
            self.stats["fallback"] += 1                     # the filter went wrong on this frame: keep the original
            return frame
        out = np.clip(y * 32768.0, -32768, 32767).astype(np.int16)
        return out.tobytes() if isinstance(frame, (bytes, bytearray)) else out

    def _block(self, mic, ref, mu=0.4):
        B, P = self.B, self.P
        if len(mic) < B:                                   # a frame that is not a multiple of the block: pass through
            return mic
        self.xbuf = np.concatenate([self.xbuf[B:], ref])
        self.ptr = (self.ptr + 1) % P
        self.X[self.ptr] = np.fft.rfft(self.xbuf)
        idx = (self.ptr - np.arange(P)) % P
        Xo = self.X[idx]
        e = mic - np.fft.irfft((self.W * Xo).sum(0), 2 * B)[B:]
        E = np.fft.rfft(np.concatenate([np.zeros(B, np.float32), e]))
        self.pw = 0.9 * self.pw + 0.1 * (np.abs(Xo[0]) ** 2).astype(np.float32)
        G = mu * np.conj(Xo) * E / (self.pw * P + 1e-6)
        g = np.fft.irfft(G, 2 * B, axis=1)
        g[:, B:] = 0                                       # a linear, not circular, filter
        self.W += np.fft.rfft(g, axis=1).astype(np.complex64)
        return e.astype(np.float32)

    def _measure_delay(self, t_cap):
        """GCC-PHAT between the last 1.5 s of the microphone and the reference: where the speakers' sound shows up."""
        self.delay_t = t_cap - 2.5                         # not measured: try again in half a second
        n, maxlag = int(SR * 1.0), int(SR * 0.35)
        m = self.mic[-n:]
        with self.lock:
            # the reference over the same wall time as the microphone window, plus maxlag before it
            back = int(round((self.ref_t - t_cap) * SR))
            end = len(self.ref) - max(0, back)
            r = self.ref[max(0, end - n - maxlag):end].copy()
        if back < 0:                                       # the loopback is behind: its future is silence
            r = np.concatenate([r, np.zeros(-back, np.float32)])[-(n + maxlag):]
        if len(r) < n + maxlag or float(np.mean(r ** 2)) < 1e-6 or float(np.mean(m ** 2)) < 1e-7:
            return
        L = 1 << int(np.ceil(np.log2(len(r) + n)))
        # c[k] = sum r[i + k] * m[i]: the microphone's sample i is the reference's i + maxlag - delay
        F = np.fft.rfft(r, L) * np.conj(np.fft.rfft(m, L))
        cc = np.fft.irfft(F / (np.abs(F) + 1e-9), L)
        lags = cc[:maxlag + 1]
        k = int(np.argmax(lags))
        conf = float(lags[k] / (np.mean(np.abs(lags)) + 1e-9))
        d = (maxlag - k) / SR                              # 0..350 ms
        if conf < 6:
            return                                         # no clear echo (headphones, or only the user talks)
        d = max(0.0, d - 0.02)                             # a little margin: the filter covers what comes after
        # a change is taken only when two measurements in a row agree: repeating music sometimes gives a false peak,
        # and every change resets the filter
        cand, self._cand = getattr(self, "_cand", None), d
        if cand is None or abs(cand - d) > 0.01:
            self.delay_t = t_cap - 2.0                     # check again soon
            return
        self.delay_t = t_cap
        if abs(d - self.delay) > 0.025:
            self.delay = d
            self.reset()
            self.stats["resets"] += 1
        self.delay_ok = True

    def state(self):
        s = self.stats
        return {"enabled": self.enabled(), "active": self.active, "delay_ms": int(self.delay * 1000),
                "delay_ok": self.delay_ok, "frames": s["frames"], "active_frames": s["active"],
                "fallback": s["fallback"], "resets": s["resets"],
                "ms_per_frame": round(s["ms"] / max(1, s["active"]), 2)}
