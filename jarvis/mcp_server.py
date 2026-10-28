"""MCP (Model Context Protocol) stdio server: lets Claude Code, Codex, Antigravity and Gemini CLI talk to Jarvis.

Register it from the Jarvis "Мосты" tab, or manually:
    claude mcp add -s user jarvis -- python <path>/jarvis/mcp_server.py
Standard library only, so it starts instantly from any Python."""
import json
import sys
import urllib.request
from pathlib import Path

CFG = Path(__file__).resolve().parent.parent / "data" / "config.json"

TOOLS = [
    {"name": "jarvis_say", "description": "Say a phrase out loud with the Jarvis voice on the user's PC (e.g. to report that a long task is finished).",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "jarvis_notify", "description": "Show a Windows notification from Jarvis.",
     "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "text": {"type": "string"}}, "required": ["text"]}},
    {"name": "jarvis_add_task", "description": "Add a task to the user's Jarvis planner. 'when' is optional, Russian phrase like 'завтра в 15:00'.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}, "when": {"type": "string"}}, "required": ["text"]}},
    {"name": "jarvis_tasks", "description": "List the user's open tasks from the Jarvis planner.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "jarvis_remind", "description": "Create a reminder. 'when' is a Russian phrase: 'через 20 минут', 'завтра в 9:00'.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}, "when": {"type": "string"}}, "required": ["text", "when"]}},
    {"name": "jarvis_timer", "description": "Start a timer, duration like '10 минут' or seconds.",
     "inputSchema": {"type": "object", "properties": {"duration": {"type": "string"}, "label": {"type": "string"}}, "required": ["duration"]}},
    {"name": "jarvis_command", "description": "Execute a Jarvis voice-style command as text, e.g. 'открой телеграм', 'громкость 30', or a user protocol name.",
     "inputSchema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "jarvis_open", "description": "Open a URL, file or folder on the user's PC.",
     "inputSchema": {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}},
    {"name": "jarvis_system", "description": "Get PC stats: CPU, RAM, GPU, disk, battery.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def rpc(method, params):
    cfg = json.loads(CFG.read_text("utf-8"))
    req = urllib.request.Request(
        f"http://127.0.0.1:{cfg.get('port', 8765)}/api/rpc",
        data=json.dumps({"method": method, "params": params}).encode(),
        headers={"Content-Type": "application/json", "X-Jarvis-Token": cfg["token"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.loads(r.read())
    if not res.get("ok"):
        raise RuntimeError(res.get("error"))
    return res.get("result")


def call_tool(name, a):
    if name == "jarvis_say":
        rpc("say", {"text": a["text"]})
        return "spoken"
    if name == "jarvis_notify":
        rpc("notify", {"title": a.get("title", "J.A.R.V.I.S."), "text": a["text"]})
        return "ok"
    if name == "jarvis_add_task":
        t = rpc("task_add", {"text": (a["text"] + " " + a.get("when", "")).strip()})
        return f"task added: {t['text']} due={t.get('due')}"
    if name == "jarvis_tasks":
        return json.dumps([{"text": t["text"], "due": t.get("due"), "done": t["done"]}
                           for t in rpc("tasks", {}) if not t["done"]], ensure_ascii=False)
    if name == "jarvis_remind":
        r = rpc("reminder_add", {"text": a["text"], "phrase": a["when"]})
        return f"reminder at {r['at']}"
    if name == "jarvis_timer":
        d = str(a["duration"])
        p = {"seconds": int(d)} if d.isdigit() else {"phrase": d}
        rpc("timer_add", {**p, "label": a.get("label", "")})
        return "timer started"
    if name == "jarvis_command":
        rpc("execute", {"text": a["command"]})
        return "sent"
    if name == "jarvis_open":
        rpc("open", {"target": a["target"]})
        return "opened"
    if name == "jarvis_system":
        return json.dumps(rpc("stats", {}), ensure_ascii=False)
    raise ValueError("unknown tool " + name)


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        mid, method = msg.get("id"), msg.get("method")
        if mid is None:
            continue  # notification
        try:
            if method == "initialize":
                result = {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
                          "capabilities": {"tools": {}},
                          "serverInfo": {"name": "jarvis", "version": "1.0.0"}}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                p = msg.get("params", {})
                try:
                    text = call_tool(p.get("name"), p.get("arguments") or {})
                    result = {"content": [{"type": "text", "text": str(text)}]}
                except Exception as e:
                    result = {"content": [{"type": "text", "text": f"Jarvis error: {e}. Is Jarvis running?"}], "isError": True}
            elif method == "ping":
                result = {}
            else:
                out = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}}
                sys.stdout.write(json.dumps(out) + "\n")
                sys.stdout.flush()
                continue
            out = {"jsonrpc": "2.0", "id": mid, "result": result}
        except Exception as e:
            out = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
