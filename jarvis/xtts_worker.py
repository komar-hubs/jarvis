"""XTTS v2 worker. Runs inside .xtts-venv. Protocol: one JSON per line on stdin/stdout.

argv[1] = reference wav (the voice to clone). The model speaks Russian with the timbre and accent of the reference."""
import json
import os
import sys
import wave

os.environ.setdefault("COQUI_TOS_AGREED", "1")


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    ref = sys.argv[1]
    try:
        import numpy as np
        import torch
        import TTS.tts.models.xtts as xmod

        def load_audio(path, sampling_rate):
            # wave-based loader: avoids torchaudio/torchcodec backend problems on Windows
            with wave.open(str(path)) as w:
                sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
                raw = w.readframes(w.getnframes())
            a = np.frombuffer(raw, {2: np.int16, 4: np.int32}[sw]).astype(np.float32)
            a /= np.iinfo({2: np.int16, 4: np.int32}[sw]).max
            if ch > 1:
                a = a.reshape(-1, ch).mean(1)
            if sr != sampling_rate:
                n = int(len(a) * sampling_rate / sr)
                a = np.interp(np.linspace(0, len(a) - 1, n), np.arange(len(a)), a).astype(np.float32)
            return torch.from_numpy(a).unsqueeze(0).clamp(-1, 1)

        xmod.load_audio = load_audio

        from TTS.api import TTS
        device = "cuda" if torch.cuda.is_available() else "cpu"
        api = TTS("tts_models/multilingual/multi-dataset/xtts_v2", progress_bar=False).to(device)
        model = api.synthesizer.tts_model
        latent, emb = model.get_conditioning_latents(audio_path=[ref], gpt_cond_len=12, max_ref_length=30)
        # warm-up so the first real phrase is fast
        model.inference("Готов.", "ru", latent, emb, temperature=0.65)
    except Exception as e:
        send({"error": f"{type(e).__name__}: {e}"})
        return
    send({"ready": True, "device": device})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            if req.get("cmd") == "quit":
                break
            if req.get("stream"):
                # streaming: first audio after ~20 GPT tokens instead of waiting for the whole phrase
                import base64
                for chunk in model.inference_stream(req["text"], req.get("lang", "ru"), latent, emb,
                                                    stream_chunk_size=int(req.get("chunk", 20)),
                                                    temperature=0.5, repetition_penalty=6.0, top_k=40, top_p=0.8,
                                                    speed=float(req.get("speed", 1.0)), enable_text_splitting=False):
                    wav = chunk.detach().float().cpu().numpy().reshape(-1)
                    pcm = (np.clip(wav, -1, 1) * 32767).astype(np.int16)
                    send({"chunk": base64.b64encode(pcm.tobytes()).decode()})
                send({"ok": True})
                continue
            out = model.inference(req["text"], req.get("lang", "ru"), latent, emb,
                                  temperature=0.5, repetition_penalty=6.0, top_k=40, top_p=0.8,
                                  speed=float(req.get("speed", 1.0)), enable_text_splitting=False)
            wav = np.asarray(out["wav"], dtype=np.float32)
            pcm = (np.clip(wav, -1, 1) * 32767).astype(np.int16)
            with wave.open(req["out"], "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(pcm.tobytes())
            send({"ok": True})
        except Exception as e:
            send({"error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
