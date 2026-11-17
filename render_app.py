import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

CAPTURE_DIR = Path(os.getenv("ZARA_CAPTURE_DIR", "/tmp/zara_gif_capture"))
CAPTURE_TOKEN = os.getenv("ZARA_CAPTURE_TOKEN", "")
CAPTURE_NAME = re.compile(r"[a-z0-9_-]+\.(?:gif|mp4|webm)")


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in {"/", "/health", "/healthz"}:
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        parsed = urlparse(self.path)
        if parsed.path in {"/verify", "/verify/"}:
            path = Path(__file__).with_name("webapp") / "index.html"
            if not path.is_file():
                self.send_response(404)
                self.end_headers()
                return
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path.startswith("/capture/"):
            token = parse_qs(parsed.query).get("token", [""])[0]
            if not CAPTURE_TOKEN or token != CAPTURE_TOKEN:
                self.send_response(404)
                self.end_headers()
                return
            if parsed.path == "/capture/":
                files = (
                    sorted(path.name for path in CAPTURE_DIR.iterdir() if path.is_file())
                    if CAPTURE_DIR.is_dir()
                    else []
                )
                body = json.dumps(files).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            name = parsed.path.rsplit("/", 1)[-1]
            if not CAPTURE_NAME.fullmatch(name):
                self.send_response(404)
                self.end_headers()
                return
            path = CAPTURE_DIR / name
            if not path.is_file():
                self.send_response(404)
                self.end_headers()
                return
            data = path.read_bytes()
            content_type = (
                "image/gif"
                if name.endswith(".gif")
                else "video/mp4"
                if name.endswith(".mp4")
                else "video/webm"
            )
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


def start_health_server() -> ThreadingHTTPServer:
    port = int(os.getenv("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="zara-health-server",
        daemon=True,
    )
    thread.start()
    print(f"Health server listening on 0.0.0.0:{port}", flush=True)
    return server


if __name__ == "__main__":
    start_health_server()
    import bot

    bot.main()
