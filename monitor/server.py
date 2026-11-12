"""Small LAN HTTP server. API returns status metadata only, never chat bodies."""
import argparse
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .source import CodexSource

PUBLIC = Path(__file__).resolve().parent.parent / "public"
FILES = {"/": ("index.html", "text/html; charset=utf-8"),
         "/app.js": ("app.js", "text/javascript; charset=utf-8"),
         "/style.css": ("style.css", "text/css; charset=utf-8"),
         "/icon.svg": ("icon.svg", "image/svg+xml"),
         "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json")}
FILES["/fonts/JetBrainsMono-SemiBold.woff2"] = ("fonts/JetBrainsMono-SemiBold.woff2", "font/woff2")


class State:
    def __init__(self, source):
        self.source = source
        self.lock = threading.Lock()
        self.data = {"tasks": [], "connected": False, "loading": True, "serverTime": time.time()}

    def refresh(self):
        while True:
            try:
                data = self.source.snapshot()
            except Exception as error:
                print(f"Source unavailable: {type(error).__name__}", flush=True)
                with self.lock:
                    data = dict(self.data, connected=False, loading=False,
                                error="暂时无法读取 Codex 状态", serverTime=time.time())
            with self.lock:
                self.data = data
            time.sleep(2)


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/api/status":
                with state.lock:
                    data = dict(state.data)
                if time.time() - data["serverTime"] > 12:
                    data.update(connected=False, error="状态同步超时，正在重连")
                body = json.dumps(data, ensure_ascii=False).encode()
                self.send_content(body, "application/json; charset=utf-8")
            elif path in FILES:
                filename, mime = FILES[path]
                self.send_content((PUBLIC / filename).read_bytes(), mime)
            else:
                self.send_error(404)

        def send_content(self, body, mime):
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; font-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    args = parser.parse_args()
    state = State(CodexSource(args.codex_home, PUBLIC.parent / ".runtime" / "tasks.json"))
    server = ThreadingHTTPServer((args.host, args.port), make_handler(state))
    threading.Thread(target=state.refresh, daemon=True).start()
    print(f"Codex Monitor: http://localhost:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
