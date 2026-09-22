"""Development server for the browser app with the production security headers.

    python tools/serve_web.py            # serves web/ on http://127.0.0.1:8765
    python tools/serve_web.py --dist     # serves dist/ instead
    python tools/serve_web.py --listen   # serves listen/ (Danas Ear, microphone allowed)

It sends the same Content-Security-Policy and related headers as
deploy/Caddyfile, so anything that would break under the real CSP breaks
here first. Not for public use — it is Python's http.server.
"""

from __future__ import annotations

import argparse
import functools
import http.server
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; style-src 'self'; "
        "font-src 'self'; img-src 'self' data: blob:; media-src blob:; connect-src 'self'; manifest-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=(), midi=(self)",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cache-Control": "no-cache",
}


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".wasm": "application/wasm", ".woff2": "font/woff2", ".js": "text/javascript", ".mjs": "text/javascript",
        ".json": "application/json", ".zip": "application/zip", ".whl": "application/octet-stream",
    }

    def translate_path(self, path: str) -> str:
        # Danas Ear lives beside the tutor at /listen/ (as in dist/ and the Caddyfile).
        clean = path.split("?", 1)[0].split("#", 1)[0]
        if clean == "/listen" or clean.startswith("/listen/"):
            rel = clean[len("/listen"):].lstrip("/") or "index.html"
            return str(ROOT / "listen" / rel)
        return super().translate_path(path)

    def end_headers(self) -> None:
        for k, v in HEADERS.items():
            if k == "Permissions-Policy" and self.path.startswith("/listen"):
                v = v.replace("microphone=()", "microphone=(self)")
            self.send_header(k, v)
        super().end_headers()

    def log_message(self, fmt, *args) -> None:  # quieter
        if "200" not in (args[1] if len(args) > 1 else ""):
            super().log_message(fmt, *args)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", action="store_true")
    ap.add_argument("--listen", action="store_true", help="serve the Danas Ear pitch listener instead of the tutor")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    directory = ROOT / ("listen" if args.listen else "dist" if args.dist else "web")
    if args.listen:
        HEADERS["Permissions-Policy"] = HEADERS["Permissions-Policy"].replace("microphone=()", "microphone=(self)")
    else:
        print("Danas Ear is at /listen/")
    handler = functools.partial(Handler, directory=str(directory))
    with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as httpd:
        print(f"serving {directory} at http://127.0.0.1:{args.port}/ with production headers")
        httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
