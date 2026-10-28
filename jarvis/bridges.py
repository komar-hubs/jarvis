"""Bridges to local coding agents: Claude Code, OpenAI Codex CLI, Google Antigravity CLI (agy), Gemini CLI.

Two directions:
  Jarvis -> agent: run the CLI headless (print mode) with a prompt, stream its output into the UI, speak the result.
  agent -> Jarvis: register jarvis/mcp_server.py as an MCP server, so the agent can make Jarvis speak, add tasks, etc.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from .config import ROOT

CREATE_NO_WINDOW = 0x08000000
HOME = Path.home()
LOCAL = Path(os.environ.get("LOCALAPPDATA", HOME / "AppData/Local"))
ROAMING = Path(os.environ.get("APPDATA", HOME / "AppData/Roaming"))

AGENTS = {
    "claude": {"title": "Claude Code", "exe": ["claude"],
               "paths": [HOME / ".local/bin/claude.exe", ROAMING / "npm/claude.cmd", HOME / ".claude/local/claude.exe"],
               "site": "https://docs.claude.com/en/docs/claude-code", "install": "npm install -g @anthropic-ai/claude-code"},
    "codex": {"title": "OpenAI Codex CLI", "exe": ["codex"],
              "paths": [ROAMING / "npm/codex.cmd"], "site": "https://github.com/openai/codex",
              "install": "npm install -g @openai/codex"},
    "antigravity": {"title": "Google Antigravity", "exe": ["agy", "antigravity"],
                    "paths": [LOCAL / "agy/bin/agy.exe"], "site": "https://antigravity.google",
                    "install": "Установите Antigravity и выполните «agy install»"},
    "gemini": {"title": "Gemini CLI", "exe": ["gemini"], "paths": [ROAMING / "npm/gemini.cmd"],
               "site": "https://github.com/google-gemini/gemini-cli", "install": "npm install -g @google/gemini-cli"},
}


AGENT_PROVIDER = {"claude": "claude_code", "codex": "codex", "antigravity": "antigravity", "gemini": "gemini_cli"}


TOOL_MARK = "\x00TOOL:"  # in-band marker in the text stream: the agent started a tool (web search, file read)


class BridgeTimeout(RuntimeError):
    pass


class _StreamSession:
    """Shared plumbing for warm CLI chat processes. stdout is read by a background thread into a queue,
    so a silent or hung CLI can never block Jarvis: every turn has an idle timeout and an overall cap,
    and a cancelled or timed-out turn kills the process (a half-read turn would desync the stream)."""

    IDLE = 40       # s without any event while the model writes
    IDLE_TOOL = 75  # s without events while a web search / file read runs
    CAP = 150       # s for the whole turn

    def _spawn(self, argv, workdir):
        self.p = subprocess.Popen(argv, cwd=str(workdir), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                                  creationflags=CREATE_NO_WINDOW, env=dict(os.environ, NO_COLOR="1"), bufsize=1)
        import queue
        self.q = queue.Queue()
        self.lock = threading.Lock()
        self.turns = 0
        self.in_tool = False

        def pump(out=self.p.stdout, q=self.q):
            try:
                for line in out:
                    q.put(line)
            except Exception:
                pass
            q.put(None)
        threading.Thread(target=pump, daemon=True).start()

    def alive(self):
        return self.p.poll() is None

    def close(self):
        try:
            self.p.kill()
        except Exception:
            pass

    def _send(self, obj):
        if not self.lock.acquire(timeout=2):
            # the previous turn is still running (should not happen: Core cancels it first)
            self.close()
            raise RuntimeError("предыдущий запрос ещё выполняется")
        if not self.alive():
            self.lock.release()
            raise RuntimeError("процесс агента завершился")
        try:
            self.p.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
            self.p.stdin.flush()
        except Exception:
            self.lock.release()
            self.close()
            raise RuntimeError("процесс агента завершился")
        self.turns += 1
        self.in_tool = False

    def _events(self, cancel):
        """Parsed JSON events of the current turn. The caller holds the lock (taken in _send) and must release it."""
        import queue
        import time
        t0 = last = time.time()
        while True:
            if cancel():
                self.close()
                return
            now = time.time()
            idle = self.IDLE_TOOL if self.in_tool else self.IDLE
            if now - last > idle or now - t0 > self.CAP:
                self.close()
                raise BridgeTimeout(f"ИИ не ответил за {int(now - t0)} с — запрос прерван")
            try:
                line = self.q.get(timeout=0.25)
            except queue.Empty:
                continue
            if line is None:
                raise RuntimeError(f"{self.NAME} завершился")
            last = time.time()
            try:
                yield json.loads(line)
            except Exception:
                continue


class ClaudeSession(_StreamSession):
    """Long-lived `claude -p --input-format stream-json` process: one user message per line on stdin,
    answer streamed back as partial-message events until a `result` event ends the turn."""

    NAME = "Claude CLI"
    # read-only abilities for the chat brain: fresh facts from the web and looking at screenshots/files
    TOOLS = "WebSearch,WebFetch,Read"

    def __init__(self, exe, system, model, workdir, key, web=True):
        if not exe:
            raise RuntimeError("Claude CLI не найден")
        Path(workdir).mkdir(parents=True, exist_ok=True)
        self.key = key
        tools = self.TOOLS if web else ""
        argv = [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--include-partial-messages", "--no-session-persistence", "--setting-sources", "", "--tools", tools,
                "--system-prompt", system]
        if tools:
            argv += ["--allowedTools", tools, "--add-dir", str(ROOT / "data" / "screenshots")]
        if model:
            argv += ["--model", model]
        self._spawn(argv, workdir)

    def ask(self, text, cancel=lambda: False):
        self._send({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}})
        done = False
        try:
            got = False
            for ev in self._events(cancel):
                t = ev.get("type")
                if t == "stream_event":
                    e = ev.get("event", {})
                    et = e.get("type")
                    if et == "content_block_start":
                        cb = e.get("content_block", {})
                        if cb.get("type") in ("tool_use", "server_tool_use"):
                            self.in_tool = True
                            yield TOOL_MARK + str(cb.get("name"))  # Core says "checking..." meanwhile
                        elif cb.get("type") == "text" and got:
                            yield "\n"  # text after a tool call starts a new paragraph
                    elif et == "content_block_delta" and e.get("delta", {}).get("type") == "text_delta":
                        got = True
                        self.in_tool = False
                        yield e["delta"]["text"]
                elif t == "result":
                    done = True
                    if ev.get("is_error"):
                        raise RuntimeError(ev.get("result") or "ошибка Claude")
                    if not got and ev.get("result"):
                        yield ev["result"]
                    return
        finally:
            if not done:
                self.close()  # cancelled, timed out or abandoned mid-turn
            self.lock.release()


class AgySession(_StreamSession):
    """Long-lived Antigravity CLI chat: `agy --input-format stream-json --output-format stream-json -p=`.
    Input lines: {"event": "user", "message": {"role": "user", "content": [{"type": "text", "text": ...}]}}.
    Output: step_update events with text_delta, then a result event. agy has no system-prompt flag,
    so the persona goes into the first message of the session."""

    NAME = "Antigravity CLI"

    def __init__(self, exe, system, model, workdir, key):
        if not exe:
            raise RuntimeError("Antigravity CLI (agy) не найден")
        Path(workdir).mkdir(parents=True, exist_ok=True)
        self.key = key
        self.system = system
        self.first = True
        argv = [exe, "--input-format", "stream-json", "--output-format", "stream-json"]
        if model:
            argv += ["--model", model]
        argv.append("-p=")  # agy needs the print flag with an empty inline prompt in stdin mode
        self._spawn(argv, workdir)

    def ask(self, text, cancel=lambda: False):
        import re
        if self.first:
            text = (f"[Инструкции ассистента — соблюдай их во всём разговоре, не используй инструменты для "
                    f"работы с файлами и терминалом]\n{self.system}\n\n[Сообщение пользователя]\n{text}")
            self.first = False
        self._send({"event": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}})
        done = False
        try:
            got, head = False, ""
            for ev in self._events(cancel):
                kind = ev.get("event")
                if kind == "step_update":
                    su = ev.get("step_update") or {}
                    st = su.get("step_type")
                    d = su.get("text_delta") or ""
                    if st == "agent_response" and d:
                        if not got:
                            head += d  # answers start with a "[V]" marker that may arrive split
                            if len(head) < 5 and "\n" not in head:
                                continue
                            d, head = re.sub(r"^\s*\[V\]\s*", "", head), ""
                            if not d:
                                continue
                        got = True
                        self.in_tool = False
                        yield d
                    elif st not in (None, "user_input", "agent_response") and su.get("state") == "ACTIVE":
                        self.in_tool = True
                        yield TOOL_MARK + str(st)
                elif kind == "result":
                    done = True
                    r = ev.get("result") or {}
                    if r.get("status") != "SUCCESS":
                        raise RuntimeError(r.get("error") or r.get("status") or "ошибка Antigravity")
                    if not got:
                        rest = re.sub(r"^\s*\[V\]\s*", "", head or r.get("response") or "")
                        if rest:
                            yield rest
                    return
        finally:
            if not done:
                self.close()
            self.lock.release()


def python_console():
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        exe = exe.with_name("python.exe")
    return str(exe)


class Bridges:
    def __init__(self, cfg, log=print, emit=lambda *a, **k: None):
        self.cfg = cfg
        self.log = log
        self.emit = emit
        self.running = {}
        self.sessions = {}

    def find(self, agent):
        custom = self.cfg.get(f"bridges.{agent}.path") or ""
        if custom and Path(custom).exists():
            return custom
        a = AGENTS[agent]
        for p in a["paths"]:
            if Path(p).exists():
                return str(p)
        if agent == "claude":
            # CLI bundled with the Claude desktop app: %APPDATA%\Claude\claude-code\<version>\<hash>\claude.exe
            # the desktop app is an MSIX package: outside the package its AppData lives in
            # %LOCALAPPDATA%\Packages\Claude_<id>\LocalCache\Roaming, inside it is virtualised to %APPDATA%
            cands = list(ROAMING.glob("Claude/claude-code/*/*/claude.exe"))
            cands += list(LOCAL.glob("Packages/Claude_*/LocalCache/Roaming/Claude/claude-code/*/*/claude.exe"))
            found = sorted(cands, key=lambda p: [int(x) if x.isdigit() else 0 for x in p.parent.parent.name.split(".")])
            if found:
                return str(found[-1])
        for e in a["exe"]:
            w = shutil.which(e + ".cmd") or shutil.which(e + ".exe") or shutil.which(e)
            if w and not w.lower().endswith(".ps1"):
                return w
        return None

    def status(self):
        out = {}
        for k, a in AGENTS.items():
            p = self.find(k)
            out[k] = {"title": a["title"], "path": p, "found": bool(p), "site": a["site"], "install": a["install"],
                      "busy": k in self.running, "workdir": self.cfg.get(f"bridges.{k}.workdir") or "",
                      "allow_edits": bool(self.cfg.get(f"bridges.{k}.allow_edits"))}
        return out

    def _argv(self, agent, exe, prompt, workdir, edits, out_file):
        """Returns (argv, stdin_text)."""
        model = self.model_for(agent)
        if agent == "claude":
            argv = [exe, "-p", "--output-format", "text"] + (["--model", model] if model else [])
            if edits:
                argv += ["--permission-mode", "acceptEdits"]
            return argv, prompt
        if agent == "codex":
            argv = [exe, "exec", "--skip-git-repo-check", "-C", workdir, "-s",
                    "workspace-write" if edits else "read-only", "-o", out_file, "-"]
            return argv, prompt
        if agent == "antigravity":
            argv = [exe, "-p", prompt, "--output-format", "text"]
            if edits:
                argv += ["--mode", "accept-edits"]
            return argv, None
        if agent == "gemini":
            argv = [exe]
            if edits:
                argv += ["--approval-mode", "auto_edit"]
            return argv, prompt
        raise ValueError(agent)

    def model_for(self, agent):
        pid = AGENT_PROVIDER.get(agent)
        m = self.cfg.get(f"ai.providers.{pid}.model") if pid else None
        if m is None and pid:
            from .config import PROVIDERS
            m = PROVIDERS[pid].get("model", "")
        return m or ""

    def chat(self, agent, system, context, convo, model=None, cancel=lambda: False):
        """Chat turn for the AI brain. Claude keeps one warm process (no ~2-3 s start-up per question)
        and remembers the dialogue itself; other agents get a one-shot run with the history in the prompt."""
        model = model if model is not None else self.model_for(agent)
        last = convo[-1]["content"] if convo else ""
        msg = f"[Контекст: {context}]\n{last}" if context else last
        if agent in ("claude", "antigravity"):
            for attempt in (0, 1):
                s, fresh = self._session(agent, system, model)
                text = msg
                if fresh and len(convo) > 1:
                    hist = "\n".join(f"{'Пользователь' if m['role'] == 'user' else 'Ассистент'}: {m['content']}"
                                     for m in convo[:-1][-8:])
                    text = f"Предыдущий диалог (для контекста):\n{hist}\n\n{msg}"
                started = False
                try:
                    for ch in s.ask(text, cancel):
                        started = True
                        yield ch
                    return
                except BridgeTimeout:
                    self.sessions.pop(agent, None)
                    raise
                except Exception:
                    s.close()
                    self.sessions.pop(agent, None)
                    # a dead warm process (killed by a cancelled turn) or a wrong model name: one fresh retry
                    if started or attempt or cancel():
                        raise
                    if agent == "antigravity" and model and fresh:
                        model = ""  # the model name may not exist on this account: use agy's default
            return
        hist = "\n".join(f"{'Пользователь' if m['role'] == 'user' else 'Ассистент'}: {m['content']}" for m in convo[:-1])
        prompt = (f"Предыдущий диалог:\n{hist}\n\n" if hist else "") + msg
        yield from self.stream(agent, system, prompt, model, cancel)

    def _session(self, agent, system, model):
        """Warm chat process per agent; recreated when the persona, model or web setting changes."""
        web = bool(self.cfg.get("ai.web_tools", True))
        key = (hash(system), model, web)
        s = self.sessions.get(agent)
        if s is not None and s.key == key and s.alive() and s.turns < 40:
            return s, False
        if s:
            s.close()
        wd = ROOT / "data" / "agent_chat"
        s = (ClaudeSession(self.find(agent), system, model, wd, key, web=web) if agent == "claude"
             else AgySession(self.find(agent), system, model, wd, key))
        self.sessions[agent] = s
        return s, True

    def warm(self, system, model=None, agent="claude"):
        """Start the chat process in advance so the first question is fast."""
        if not self.find(agent):
            return
        self._session(agent, system, model if model is not None else self.model_for(agent))

    def list_models(self, agent):
        if agent == "claude":
            return ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"]
        if agent == "antigravity" and self.find(agent):
            r = subprocess.run([self.find(agent), "models"], capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=40, creationflags=CREATE_NO_WINDOW)
            return [ln.split()[0] for ln in r.stdout.splitlines() if ln.strip() and not ln.startswith("Fetching")]
        return []

    def stream(self, agent, system, prompt, model=None, cancel=lambda: False):
        """Chat mode: run the agent CLI with the Jarvis persona and yield answer text as it arrives.
        Runs in an isolated empty folder without tools, so it answers like a chat model."""
        exe = self.find(agent)
        if not exe:
            raise RuntimeError(f"{AGENTS[agent]['title']} не найден. Установка: {AGENTS[agent]['install']}")
        model = model if model is not None else self.model_for(agent)
        wd = ROOT / "data" / "agent_chat"
        wd.mkdir(parents=True, exist_ok=True)
        stdin_text = prompt
        mode = "text"
        if agent == "claude":
            # --setting-sources "" skips user plugins/hooks; --tools "" makes it a pure chat model
            argv = [exe, "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
                    "--no-session-persistence", "--setting-sources", "", "--tools", "", "--system-prompt", system]
            if model:
                argv += ["--model", model]
            mode = "claude"
        elif agent == "codex":
            argv = [exe, "exec", "--json", "--skip-git-repo-check", "-C", str(wd), "-s", "read-only"]
            if model:
                argv += ["-m", model]
            argv.append("-")
            stdin_text = f"{system}\n\n{prompt}"
            mode = "codex"
        elif agent == "antigravity":
            argv = [exe, "-p", f"{system}\n\n{prompt}", "--output-format", "text"]
            if model:
                argv += ["--model", model]
            stdin_text = None
        else:  # gemini
            argv = [exe] + (["-m", model] if model else [])
            stdin_text = f"{system}\n\n{prompt}"
        p = subprocess.Popen(argv, cwd=str(wd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                             creationflags=CREATE_NO_WINDOW, env=dict(os.environ, NO_COLOR="1"))
        self.running[agent] = p
        killed = []

        def watchdog(t0=__import__("time").time()):
            # stdout reads block: a silent or hung CLI is killed on cancel or after the overall cap
            import time
            while p.poll() is None:
                if cancel() or time.time() - t0 > _StreamSession.CAP:
                    killed.append("cancel" if cancel() else "timeout")
                    p.kill()
                    return
                time.sleep(0.3)
        threading.Thread(target=watchdog, daemon=True).start()
        try:
            if stdin_text is not None:
                p.stdin.write(stdin_text)
            p.stdin.close()
            got = False
            for line in p.stdout:
                if cancel():
                    p.kill()
                    return
                if mode == "text":
                    got = True
                    yield line
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if mode == "claude":
                    if ev.get("type") == "stream_event":
                        e = ev.get("event", {})
                        if e.get("type") == "content_block_delta" and e.get("delta", {}).get("type") == "text_delta":
                            got = True
                            yield e["delta"]["text"]
                    elif ev.get("type") == "result":
                        if ev.get("is_error") or ev.get("subtype") not in (None, "success"):
                            raise RuntimeError(ev.get("result") or ev.get("subtype"))
                        if not got and ev.get("result"):
                            yield ev["result"]
                else:  # codex jsonl
                    item = ev.get("item") or {}
                    if ev.get("type") == "item.completed" and item.get("type") in ("agent_message", "assistant_message"):
                        got = True
                        yield (item.get("text") or "") + "\n"
                    elif ev.get("type") in ("error", "turn.failed"):
                        raise RuntimeError(ev.get("message") or json.dumps(ev.get("error"), ensure_ascii=False))
            p.wait()
            if killed == ["cancel"]:
                return
            if killed:
                raise BridgeTimeout(f"{AGENTS[agent]['title']} не ответил за {_StreamSession.CAP} с — запрос прерван")
            if not got:
                err = (p.stderr.read() or "").strip()
                raise RuntimeError(err[-400:] or f"{AGENTS[agent]['title']} не ответил (код {p.returncode})")
        finally:
            self.running.pop(agent, None)
            if p.poll() is None:
                p.kill()

    def ask(self, agent, prompt, workdir=None, quiet=False, timeout=900):
        """Blocking call; returns the agent's final answer text."""
        exe = self.find(agent)
        if not exe:
            raise RuntimeError(f"{AGENTS[agent]['title']} не найден. Установка: {AGENTS[agent]['install']}")
        workdir = workdir or self.cfg.get(f"bridges.{agent}.workdir") or str(HOME)
        edits = bool(self.cfg.get(f"bridges.{agent}.allow_edits"))
        out_file = str(Path(tempfile.gettempdir()) / f"jarvis_{agent}_last.txt")
        if os.path.exists(out_file):
            os.remove(out_file)
        argv, stdin_text = self._argv(agent, exe, prompt, workdir, edits, out_file)
        self.running[agent] = True
        self.emit("bridge", agent=agent, state="start", prompt=prompt[:500])
        lines = []
        try:
            p = subprocess.Popen(argv, cwd=workdir, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                 creationflags=CREATE_NO_WINDOW, env=dict(os.environ, NO_COLOR="1"))
            self.running[agent] = p
            if stdin_text is not None:
                p.stdin.write(stdin_text)
            p.stdin.close()
            timer = threading.Timer(timeout, p.kill)
            timer.start()
            for line in p.stdout:
                line = line.rstrip()
                lines.append(line)
                if not quiet or len(lines) < 400:
                    self.emit("bridge", agent=agent, state="line", line=line)
            p.wait()
            timer.cancel()
        finally:
            self.running.pop(agent, None)
        text = "\n".join(lines).strip()
        if agent == "codex" and os.path.exists(out_file):
            text = Path(out_file).read_text("utf-8", "replace").strip() or text
        self.emit("bridge", agent=agent, state="done", text=text[-4000:])
        if p.returncode not in (0, None) and not text:
            raise RuntimeError(f"{AGENTS[agent]['title']} завершился с кодом {p.returncode}")
        return text

    def ask_async(self, agent, prompt, on_done, workdir=None):
        def run():
            try:
                on_done(self.ask(agent, prompt, workdir), None)
            except Exception as e:
                on_done(None, str(e))
        threading.Thread(target=run, daemon=True).start()

    def stop(self, agent):
        p = self.running.get(agent)
        if hasattr(p, "kill"):
            p.kill()
            return True
        return False

    # ---------------------------------------------------------------- MCP registration
    def mcp_install(self, agent):
        exe = self.find(agent)
        if not exe:
            raise RuntimeError(f"{AGENTS[agent]['title']} не найден")
        py = python_console()
        server = str(ROOT / "jarvis" / "mcp_server.py")
        if agent == "claude":
            argv = [exe, "mcp", "add", "-s", "user", "jarvis", "--", py, server]
        elif agent == "codex":
            argv = [exe, "mcp", "add", "jarvis", "--", py, server]
        elif agent == "antigravity":
            argv = [exe, "mcp", "add", "jarvis", py, server]
        else:
            argv = [exe, "mcp", "add", "-s", "user", "jarvis", py, server]
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=60, creationflags=CREATE_NO_WINDOW)
        return (r.stdout + r.stderr).strip() or f"код {r.returncode}"

    def mcp_manual(self):
        py = python_console()
        server = str(ROOT / "jarvis" / "mcp_server.py")
        return {"command": py, "args": [server],
                "json": {"mcpServers": {"jarvis": {"command": py, "args": [server]}}}}
