import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


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
