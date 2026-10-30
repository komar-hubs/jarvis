"""Settings storage. Everything lives in <root>/data so the folder can be copied to another PC."""
import copy
import json
import os
import secrets
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
UI_DIR = ROOT / "ui"
VOICES = DATA / "voices"
for d in (DATA, VOICES, DATA / "screenshots", DATA / "cache"):
    d.mkdir(parents=True, exist_ok=True)

# Catalog of AI providers. kind: openai = OpenAI-compatible /chat/completions,
# ollama = native Ollama API, anthropic = Messages API, bridge = local agent CLI.
PROVIDERS = {
    "ollama":     {"title": "Ollama (локально)", "kind": "ollama", "base_url": "http://localhost:11434", "model": "qwen3.5:9b", "key": False},
    "lmstudio":   {"title": "LM Studio (локально)", "kind": "openai", "base_url": "http://localhost:1234/v1", "model": "", "key": False},
    "llamacpp":   {"title": "llama.cpp / Jan / KoboldCpp (локально)", "kind": "openai", "base_url": "http://localhost:8080/v1", "model": "", "key": False},
    "openai":     {"title": "OpenAI", "kind": "openai", "base_url": "https://api.openai.com/v1", "model": "", "key": True},
    "anthropic":  {"title": "Anthropic Claude", "kind": "anthropic", "base_url": "https://api.anthropic.com", "model": "claude-sonnet-5-5", "key": True},
    "gemini":     {"title": "Google Gemini", "kind": "openai", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "", "key": True},
    "openrouter": {"title": "OpenRouter (сотни моделей)", "kind": "openai", "base_url": "https://openrouter.ai/api/v1", "model": "", "key": True},
    "groq":       {"title": "Groq (очень быстро)", "kind": "openai", "base_url": "https://api.groq.com/openai/v1", "model": "", "key": True},
    "deepseek":   {"title": "DeepSeek", "kind": "openai", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "key": True},
    "mistral":    {"title": "Mistral", "kind": "openai", "base_url": "https://api.mistral.ai/v1", "model": "", "key": True},
    "xai":        {"title": "xAI Grok", "kind": "openai", "base_url": "https://api.x.ai/v1", "model": "", "key": True},
    "together":   {"title": "Together AI", "kind": "openai", "base_url": "https://api.together.xyz/v1", "model": "", "key": True},
    "yandex":     {"title": "YandexGPT (OpenAI-совместимый)", "kind": "openai", "base_url": "https://llm.api.cloud.yandex.net/v1", "model": "gpt://<folder_id>/yandexgpt/latest", "key": True},
    "custom":     {"title": "Свой OpenAI-совместимый сервер", "kind": "openai", "base_url": "http://localhost:8000/v1", "model": "", "key": True},
    "claude_code": {"title": "Мост: Claude CLI", "kind": "bridge", "agent": "claude", "model": "claude-haiku-5-5"},
    "codex":       {"title": "Мост: OpenAI Codex CLI", "kind": "bridge", "agent": "codex", "model": ""},
    "antigravity": {"title": "Мост: Google Antigravity (agy)", "kind": "bridge", "agent": "antigravity", "model": "gemini-3.8-flash-low"},
    "gemini_cli":  {"title": "Мост: Gemini CLI", "kind": "bridge", "agent": "gemini", "model": ""},
}

DEFAULTS = {
    "setup_done": False,
    "token": "",
    "port": 8770,
    "user": {"name": "", "address": "сэр"},
    "voice": {
        "engine": "silero",          # silero (fast local Russian) | xtts (voice clone) | edge | sapi
        "silero_speaker": "aidar",
        "silero_rate": "medium",
        "silero_pitch": "medium",
        "edge_voice": "ru-RU-DmitryNeural",
        "edge_rate": "+5%",
        "edge_pitch": "-6Hz",
        "xtts_ref": "",              # path to reference wav, empty = data/voices/jarvis_ref.wav
        "xtts_speed": 1.05,
        "eleven_key": "",            # ElevenLabs API key (or ELEVENLABS_API_KEY env var)
        "eleven_voice": "onwK4e9ZLuTAKqWW03F9",  # Daniel, British male
        "eleven_model": "eleven_flash_v2_5",     # flash: ~0.5 credit/char, fast | eleven_multilingual_v2: 1 credit, richer
        "eleven_speed": 1.0,
        "volume": 0.9,
        "effect": True,              # light "HUD" processing
        "enabled": True,
    },
    "stt": {
        "engine": "gigaam",          # gigaam (fast Russian, CPU) | whisper
        "model": "large-v3-turbo",
        "device": "auto",
        "mic": None,
        "wake_words": ["джарвис", "jarvis", "жарвис", "джервис", "джарвиз", "дарвис", "джарвес", "чарвис", "джарви"],
        "always_listen": True,
        "follow_up_sec": 10,         # after an answer, the next phrase works without "Джарвис" for this long
        "conversation_idle_sec": 120,  # conversation mode ends after this much silence
        "vad": 2,
        "silence_ms": 550,
        "ptt_hotkey": "ctrl+alt+j",
        "show_hotkey": "ctrl+alt+space",
        "chime": True,
    },
    "ai": {
        "provider": "ollama",        # free, local, no key; the setup wizard installs it in one click
        "providers": {},             # per provider overrides: base_url, api_key, model
        "temperature": 0.6,
        "history": 16,
        "agent_tools": True,
        "persona_extra": "",
        "speak_chat": True,
        "humor": 60,                 # 0..100, TARS style: how much irony goes into answers
    },
    # productivity and PC care (jarvis/extras.py)
    "extras": {
        "clipboard_history": True,   # last 25 copied texts, memory only (never written to disk)
        "usage_tracking": True,      # time per program per day (data/usage.json)
        "messengers": True,          # "2 новых сообщения в Telegram" from window titles
        "watchdog": True,            # warn about RAM, temperatures, low disk
        "break_min": 90,             # suggest a break after this many minutes at the PC, 0 = off
        "water_min": 0,              # remind to drink water every N minutes, 0 = off
        "focus_apps": ["telegram", "discord", "willicord", "whatsapp", "vk", "viber"],
        "distractions": ["youtube", "vk.com", "вконтакте", "tiktok", "twitch", "reddit", "instagram", "pikabu",
                         "steam", "кинопоиск", "rutube"],
    },
    "habits": ["вода", "зарядка", "чтение", "прогулка"],
    # speaking first (jarvis/sentinel.py): 0 never, 1 only critical, 2 important, 3 everything worth a word
    "initiative": {"level": 2},
    # the PC sentinel: autostart, unsigned programs from Temp, miners, hung windows, hosts, Defender, network
    "sentinel": {"enabled": True},
    # e-mail over IMAP/SMTP (jarvis/mail.py); passwords never leave this file for the interface
    "mail": {"accounts": [], "check_min": 3, "vip": [], "contacts": {}},
    "mouse": {"vision": True},
    # quiet routine work (jarvis/background.py): free RAM / clean temp when needed; mail sorting is opt-in
    "background": {"enabled": True, "mail_sort": False},
    # messages in Telegram / Discord / WhatsApp: the app used without a name, and "Отправить?" before Enter
    "messenger": {"default": "telegram", "confirm": True},
    "plugins": {"disabled": [], "timeout": 30},   # data/plugins/*.py       # ask the vision model when nothing on the screen matches by name
    "bridges": {
        "claude": {"path": "", "workdir": "", "allow_edits": False},
        "codex": {"path": "", "workdir": "", "allow_edits": False},
        "antigravity": {"path": "", "workdir": "", "allow_edits": False},
        "gemini": {"path": "", "workdir": "", "allow_edits": False},
    },
    "location": {"city": "Москва", "lat": 55.7558, "lon": 37.6173},
    "search_url": "https://www.google.com/search?q={q}",
    "music_service": "youtube",   # youtube | ytmusic | yandex: where "включи песню …" plays (skills.music)
    "autostart": False,
    "theme": "jarvis",
    # night mode: dim warm interface, quieter voice (volume factor), no interface sounds; optional schedule
    "night": {"enabled": False, "auto": False, "from": "23:00", "to": "08:00", "volume": 0.45},
    "windows_notifications": False,  # reminders show as a card inside Jarvis; Windows toasts piled up twice
    "duck_level": 0.5,               # other apps' volume while Jarvis speaks
    # one-button updates: GitHub releases of this repository (published by tools/publish_update.py)
    "update": {"github": "komar-hubs/jarvis", "auto_check": True, "notified": ""},
    "ui_sounds": True,
    "start_hidden": False,
    "greeting": True,
    "notes": [],
    "aliases": {},                   # spoken name -> app name / path
}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, path=DATA / "config.json"):
        self.path = Path(path)
        self.lock = threading.RLock()
        raw = {}
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text("utf-8"))
            except Exception:
                raw = {}
        self.data = _merge(DEFAULTS, raw)
        if raw and raw.get("cfg_version", 1) < 2:
            # v2: GigaAM + Silero replaced Whisper + XTTS/Edge as defaults (much faster, no GPU needed),
            # "Джарвис" is required at the start of every phrase, and the port moved off 8765
            d = self.data
            d["stt"]["engine"] = "gigaam"
            if d["voice"].get("engine") in ("xtts", "edge", "sapi"):
                d["voice"]["engine"] = "silero"
            d["stt"]["follow_up_sec"] = 0
            if d["stt"].get("silence_ms", 650) == 650:
                d["stt"]["silence_ms"] = 550
            if d.get("port") == 8765:
                d["port"] = 8770
        if raw and raw.get("cfg_version", 1) < 3:
            # v3: the follow-up window works again (it was forced to 0 and never checked), so turn it on
            if not self.data["stt"].get("follow_up_sec"):
                self.data["stt"]["follow_up_sec"] = 10
        if raw and raw.get("cfg_version", 1) < 4:
            # v4: the PC watchdog speaks through the initiative (only when it is a good moment), so it is on again
            self.data["extras"]["watchdog"] = True
        self.data["cfg_version"] = 4
        if not self.data.get("token"):
            self.data["token"] = secrets.token_hex(16)
        self.save()

    def get(self, path, default=None):
        cur = self.data
        for p in path.split("."):
            if not isinstance(cur, dict) or p not in cur:
                return default
            cur = cur[p]
        return cur

    def set(self, path, value):
        with self.lock:
            parts = path.split(".")
            cur = self.data
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            cur[parts[-1]] = value
            self.save()

    def update(self, patch):
        with self.lock:
            self.data = _merge(self.data, patch)
            self.save()

    def save(self):
        with self.lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), "utf-8")
            os.replace(tmp, self.path)

    def provider(self, pid=None):
        pid = pid or self.data["ai"]["provider"]
        base = dict(PROVIDERS.get(pid, PROVIDERS["custom"]))
        base.update(self.data["ai"]["providers"].get(pid, {}))
        base["id"] = pid
        return base

    def public(self):
        """Config without secrets for the UI (api keys are masked)."""
        d = copy.deepcopy(self.data)
        for pid, p in d["ai"]["providers"].items():
            if p.get("api_key"):
                p["api_key_set"] = True
                p["api_key"] = ""
        if d["voice"].get("eleven_key"):
            d["voice"]["eleven_key_set"] = True
            d["voice"]["eleven_key"] = ""
        for a in (d.get("mail") or {}).get("accounts") or []:
            a["password_set"] = bool(a.pop("password", None))
        d.pop("token", None)
        return d


class JsonStore:
    """Tiny JSON list store used for tasks, commands, chat history, etc."""

    def __init__(self, name, default=None):
        self.path = DATA / f"{name}.json"
        self.lock = threading.RLock()
        self.items = default if default is not None else []
        if self.path.exists():
            try:
                self.items = json.loads(self.path.read_text("utf-8"))
            except Exception:
                pass

    def save(self):
        with self.lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.items, ensure_ascii=False, indent=2), "utf-8")
            os.replace(tmp, self.path)
