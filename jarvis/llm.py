"""LLM providers with streaming: OpenAI-compatible, Ollama native, Anthropic Messages, and agent CLI bridges."""
import json

import requests


class LLMError(Exception):
    pass


def _sse_lines(resp):
    for raw in resp.iter_lines(decode_unicode=False):
        if not raw:
            continue
        line = raw.decode("utf-8", "replace")
        if line.startswith("data:"):
            yield line[5:].strip()


def stream_chat(prov, messages, temperature=0.6, max_tokens=1500, bridges=None, cancel=lambda: False):
    """Yields text chunks. prov = Config.provider() dict. messages = [{role, content}], first may be system."""
    kind = prov.get("kind")
    if kind == "bridge":
        if bridges is None:
            raise LLMError("мосты недоступны")
        systems = [m["content"] for m in messages if m["role"] == "system"]
        static = systems[0] if systems else ""
        context = " ".join(s.replace("Контекст: ", "", 1) for s in systems[1:])
        convo = [m for m in messages if m["role"] != "system"][-10:]
        yield from bridges.chat(prov["agent"], static, context, convo, prov.get("model"), cancel=cancel)
        return
    base = (prov.get("base_url") or "").rstrip("/")
    key = prov.get("api_key") or ""
    model = prov.get("model") or ""
    if not model:
        raise LLMError(f"Для провайдера «{prov.get('title')}» не выбрана модель. Откройте Настройки → ИИ.")
    if prov.get("key") and not key:
        raise LLMError(f"Для «{prov.get('title')}» нужен API-ключ. Откройте Настройки → ИИ.")

    if kind == "ollama":
        body = {"model": model, "messages": messages, "stream": True, "think": False,
                # small context keeps a 7-9B model fully on a 12 GB GPU next to Whisper and XTTS
                "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": int(prov.get("num_ctx") or 4096)},
                "keep_alive": "5m"}  # free VRAM soon after the chat goes quiet
        with requests.post(base + "/api/chat", json=body, stream=True, timeout=(5, 90)) as r:
            if r.status_code == 400 and "think" in r.text:
                body.pop("think")
                r = requests.post(base + "/api/chat", json=body, stream=True, timeout=(5, 90))
            _check(r)
            for line in r.iter_lines():
                if cancel():
                    return
                if not line:
                    continue
                d = json.loads(line)
                if d.get("error"):
                    raise LLMError(d["error"])
                c = (d.get("message") or {}).get("content")
                if c:
                    yield c
                if d.get("done"):
                    break
        return

    if kind == "anthropic":
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        msgs = [m for m in messages if m["role"] != "system"]
        body = {"model": model, "max_tokens": max_tokens, "messages": msgs, "stream": True, "temperature": temperature}
        if system:
            body["system"] = system
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        with requests.post(base + "/v1/messages", json=body, headers=headers, stream=True, timeout=(10, 90)) as r:
            _check(r)
            for data in _sse_lines(r):
                if cancel():
                    return
                try:
                    ev = json.loads(data)
                except Exception:
                    continue
                if ev.get("type") == "content_block_delta" and ev["delta"].get("type") == "text_delta":
                    yield ev["delta"]["text"]
                elif ev.get("type") == "error":
                    raise LLMError(ev.get("error", {}).get("message", "error"))
        return

    # OpenAI-compatible
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if "openrouter" in base:
        headers.update({"HTTP-Referer": "https://github.com/jarvis-assistant", "X-Title": "JARVIS"})
    body = {"model": model, "messages": messages, "stream": True, "temperature": temperature, "max_tokens": max_tokens}
    with requests.post(base + "/chat/completions", json=body, headers=headers, stream=True, timeout=(10, 90)) as r:
        if r.status_code == 400 and "max_tokens" in r.text:
            body.pop("max_tokens")
            body["max_completion_tokens"] = max_tokens
            r = requests.post(base + "/chat/completions", json=body, headers=headers, stream=True, timeout=(10, 90))
        if r.status_code == 400 and "temperature" in r.text:
            body.pop("temperature")
            r = requests.post(base + "/chat/completions", json=body, headers=headers, stream=True, timeout=(10, 90))
        _check(r)
        for data in _sse_lines(r):
            if cancel():
                return
            if data == "[DONE]":
                break
            try:
                ev = json.loads(data)
            except Exception:
                continue
            if ev.get("error"):
                raise LLMError(str(ev["error"]))
            ch = (ev.get("choices") or [{}])[0]
            c = (ch.get("delta") or {}).get("content")
            if c:
                yield c


def _check(r):
    if r.status_code >= 400:
        try:
            msg = r.json()
            msg = msg.get("error", msg)
            if isinstance(msg, dict):
                msg = msg.get("message", msg)
        except Exception:
            msg = r.text[:300]
        raise LLMError(f"HTTP {r.status_code}: {msg}")


def complete(prov, messages, **kw):
    return "".join(stream_chat(prov, messages, **kw))


_no_vision = set()


def vision_supported(prov):
    return prov.get("kind") in ("openai", "anthropic") and bool(prov.get("model")) and prov.get("id") not in _no_vision


def vision_ask(prov, prompt, png, max_tokens=400, system=None, timeout=60):
    """One question about one picture (PNG bytes), no streaming. Raises LLMError if the model has no eyes."""
    import base64
    b64 = base64.b64encode(png).decode()
    base = (prov.get("base_url") or "").rstrip("/")
    key = prov.get("api_key") or ""
    if prov.get("kind") == "anthropic":
        body = {"model": prov["model"], "max_tokens": max_tokens, "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            {"type": "text", "text": prompt}]}]}
        if system:
            body["system"] = system
        r = requests.post(base + "/v1/messages", json=body, timeout=(10, timeout),
                          headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        _vision_check(prov, r)
        return "".join(b.get("text", "") for b in r.json().get("content", []))
    if prov.get("kind") != "openai":
        raise LLMError("этот провайдер не принимает картинки")
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": [
        {"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}]
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = {"model": prov["model"], "messages": msgs, "max_tokens": max_tokens, "temperature": 0}
    r = requests.post(base + "/chat/completions", json=body, headers=headers, timeout=(10, timeout))
    if r.status_code == 400 and ("max_tokens" in r.text or "temperature" in r.text):
        body.pop("temperature", None)
        body["max_completion_tokens"] = body.pop("max_tokens")
        r = requests.post(base + "/chat/completions", json=body, headers=headers, timeout=(10, timeout))
    _vision_check(prov, r)
    return ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""


def _vision_check(prov, r):
    if r.status_code == 400 and any(w in r.text.lower() for w in ("image", "vision", "multimodal", "content type")):
        _no_vision.add(prov.get("id"))
        raise LLMError("модель не видит картинки")
    _check(r)


def list_models(prov):
    kind = prov.get("kind")
    base = (prov.get("base_url") or "").rstrip("/")
    key = prov.get("api_key") or ""
    if kind == "ollama":
        r = requests.get(base + "/api/tags", timeout=5)
        _check(r)
        return sorted(m["name"] for m in r.json().get("models", []))
    if kind == "anthropic":
        r = requests.get(base + "/v1/models", headers={"x-api-key": key, "anthropic-version": "2023-06-01"}, timeout=10)
        _check(r)
        return [m["id"] for m in r.json().get("data", [])]
    if kind == "bridge":
        return {"claude": ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"]}.get(prov.get("agent"), [])
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    r = requests.get(base + "/models", headers=headers, timeout=10)
    _check(r)
    data = r.json()
    items = data.get("data", data if isinstance(data, list) else [])
    return sorted(m.get("id", "").replace("models/", "") for m in items if m.get("id"))
