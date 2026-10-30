"""Downloads the speech models once, for install.ps1: `python -m jarvis.prefetch`.

Kept out of the PowerShell script on purpose: Kaspersky's heuristic flagged install.ps1 as
HEUR:Trojan.PowerShell.Alien.gen once it ran inline code that measured memory and processor threads (a check that
malware uses to spot a sandbox). Here it is ordinary program code.
A weak laptop gets the light int8 GigaAM: 225 MB instead of 880, the same words."""
from . import system as S


def main():
    try:
        import onnx_asr
        q = "int8" if S.weak_pc() else None
        onnx_asr.load_model("gigaam-v3-ctc", quantization=q, providers=["CPUExecutionProvider"])
        print("GigaAM ok", "(light)" if q else "")
    except Exception as e:
        print("GigaAM FAIL:", e)
    try:
        from .config import Config
        from .tts import SileroEngine
        print("Silero ok" if SileroEngine(Config()).start() else "Silero FAIL")
    except Exception as e:
        print("Silero FAIL:", e)


if __name__ == "__main__":
    main()
