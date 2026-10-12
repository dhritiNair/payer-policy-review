"""Local test server: serves the real PDF plus fake failures, for exercising fetch offline."""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF_PATH = ROOT / "sources" / "original" / "beds-mattresses.pdf"
KNEE_PDF_PATH = ROOT / "sources" / "original" / "surgery-knee.pdf"
KNEE_PREFIX = "/surgery-knee"   # Surgery of the Knee is served under this prefix; Beds and Mattresses keeps the bare paths


def routes(pdf_bytes, prefix=""):
    """path -> (status code, content type, body); every path starts with `prefix`"""
    html = b"<html><body><h1>Service temporarily unavailable</h1></body></html>"
    table = {
        "/ok.pdf": (200, "application/pdf", pdf_bytes),
        "/html-error": (200, "text/html", html),
        "/html-as-pdf": (200, "application/pdf", html),   # lies about its content type
        "/empty": (200, "application/pdf", b""),
        "/truncated.pdf": (200, "application/pdf", pdf_bytes[: len(pdf_bytes) // 2]),
        "/missing": (404, "text/html", b"Not found"),
        "/server-error": (500, "text/html", b"Internal error"),
    }
    return {prefix + path: v for path, v in table.items()}


def start(pdf_bytes=None):
    """Start the server on a free port in a background thread. Returns (server, base_url)."""
    table = routes(pdf_bytes if pdf_bytes is not None else PDF_PATH.read_bytes())
    table.update(routes(KNEE_PDF_PATH.read_bytes(), KNEE_PREFIX))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            code, ctype, body = table.get(self.path, (404, "text/plain", b"unknown route"))
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"
