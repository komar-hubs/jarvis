"""Microphone diagnostics for a friend's PC: records 2 s from every microphone and writes mic_report.txt.
Run mic_check.bat and keep talking while it checks."""
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
lines = []


def out(s=""):
    print(s, flush=True)
    lines.append(s)


def main():
    import numpy as np
    import sounddevice as sd
    from jarvis.stt import list_mics, mic_access_problem

    out(f"Windows: {platform.platform()}")
    out(f"Python: {sys.version.split()[0]} {sys.executable}")
    out(f"base: {sys.base_prefix}")
    out(f"sounddevice {sd.__version__}, PortAudio: {sd.get_portaudio_version()[1]}")
    problem = mic_access_problem()
    out("Проблема доступа: " + (problem or "не найдена"))
    out()
    apis = sd.query_hostapis()
    try:
        out(f"Устройство по умолчанию: {sd.query_devices(kind='input')['name']}")
    except Exception as e:
        out(f"Устройство по умолчанию: ошибка {e}")
    out("Все входы:")
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            out(f"  [{i}] {apis[d['hostapi']]['name']}: {d['name']} ({int(d['default_samplerate'])} Гц, "
                f"{d['max_input_channels']} кан.)")
    out()
    out(">>> ГОВОРИТЕ что-нибудь вслух всё время проверки (около 2 секунд на микрофон) <<<")
    time.sleep(2)
    for m in [{"id": None, "name": "по умолчанию"}] + list_mics():
        dev = m["id"]
        res = None
        for rate, ch in ((16000, 1), (None, None)):
            try:
                info = sd.query_devices(dev, "input")
                r = rate or int(info["default_samplerate"])
                c = ch or max(1, min(2, int(info["max_input_channels"])))
                a = sd.rec(int(2 * r), samplerate=r, channels=c, dtype="int16", device=dev)
                sd.wait()
                x = a.astype(np.float32)
                res = (r, c, int(np.abs(x).max()), float(np.sqrt(np.mean(x ** 2))))
                break
            except Exception as e:
                res = f"ошибка: {e}"
        if isinstance(res, tuple):
            r, c, peak, rms = res
            verdict = ("ТИШИНА (одни нули) — доступ заблокирован или микрофон выключен" if peak == 0 else
                       "очень тихо" if peak < 500 else "есть звук")
            out(f"  {m['name']}: {r} Гц/{c} кан., пик {peak}, rms {rms:.0f} -> {verdict}")
        else:
            out(f"  {m['name']}: {res}")
    out()
    out("Пик < 500 при речи = тихо (Джарвис сам усилит до 10 раз). Одни нули = Windows не даёт звук программе.")


try:
    main()
except Exception as e:
    import traceback
    out("СБОЙ: " + "".join(traceback.format_exception(e))[-1500:])
rep = ROOT / "mic_report.txt"
rep.write_text("\n".join(lines), encoding="utf-8")
print(f"\nОтчёт сохранён: {rep}\nОтправьте этот файл другу.")
