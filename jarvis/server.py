"""Local HTTP + WebSocket server for the UI and external integrations (MCP bridge, scripts).

Security: every RPC call needs the X-Jarvis-Token header (random per install, stored in data/config.json).
Browsers cannot attach that header cross-origin without a CORS preflight we never allow, so web pages can't
drive Jarvis. WebSocket checks the token and the Origin."""
import asyncio
import json
import sys
import threading

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import UI_DIR


class Server:
    def __init__(self, core):
        self.core = core
        self.token = core.cfg.get("token")
        self.port = int(core.cfg.get("port", 8765))
        self.clients = set()
        self.loop = None
        app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
        self.app = app

        @app.middleware("http")
        async def no_stale_ui(request: Request, call_next):
            # the UI files change with updates: always revalidate, or WebView2 keeps serving old scripts
            resp = await call_next(request)
            if request.url.path == "/" or request.url.path.startswith("/ui/"):
                resp.headers["Cache-Control"] = "no-cache"
            return resp

        @app.get("/", response_class=HTMLResponse)
        async def index():
            html = (UI_DIR / "index.html").read_text("utf-8")
            return html.replace("__JARVIS_TOKEN__", self.token)

        @app.get("/api/ping")
        async def ping():
            return {"ok": True, "app": "jarvis"}

        @app.post("/api/show")
        async def show(request: Request):
            if request.headers.get("x-jarvis-token") != self.token:
                return JSONResponse({"error": "forbidden"}, 403)
            core.show_window()
            return {"ok": True}

        @app.post("/api/rpc")
        def rpc(request_body: dict, request: Request):
            if request.headers.get("x-jarvis-token") != self.token:
                return JSONResponse({"error": "forbidden"}, 403)
            try:
                res = core.rpc(request_body.get("method"), request_body.get("params") or {})
                return {"ok": True, "result": res}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        @app.websocket("/ws")
        async def ws(sock: WebSocket):
            origin = sock.headers.get("origin", "")
            if sock.query_params.get("t") != self.token or (origin and "127.0.0.1" not in origin and "localhost" not in origin):
                await sock.close(code=4403)
                return
            await sock.accept()
            self.loop = asyncio.get_running_loop()
            self.clients.add(sock)
            try:
                while True:
                    await sock.receive_text()
            except WebSocketDisconnect:
                pass
            except Exception:
                pass
            finally:
                self.clients.discard(sock)

        app.mount("/ui", StaticFiles(directory=str(UI_DIR)), name="ui")

    async def _send_all(self, msg):
        dead = []
        for c in list(self.clients):
            try:
                await c.send_text(msg)
            except Exception:
                dead.append(c)
        for c in dead:
            self.clients.discard(c)

    def broadcast(self, event):
        if not self.loop or not self.clients:
            return
        msg = json.dumps(event, ensure_ascii=False, default=str)
        asyncio.run_coroutine_threadsafe(self._send_all(msg), self.loop)

    @staticmethod
    def _loop_error(loop, context):
        # WebView2 drops its sockets without a goodbye: the Proactor loop logged a traceback for each one
        # (5000+ in data/jarvis.log). Those resets are normal and are dropped silently.
        exc = context.get("exception")
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        loop.default_exception_handler(context)

    def start(self):
        cfg = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning", access_log=False,
                             ws="websockets-sansio")
        self.server = uvicorn.Server(cfg)
        self.failed = False

        def run():
            loop = asyncio.SelectorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.set_exception_handler(self._loop_error)
            try:
                loop.run_until_complete(self.server.serve())
            except BaseException:  # uvicorn calls sys.exit(1) when the port is busy
                self.failed = True
        threading.Thread(target=run, daemon=True, name="http").start()
