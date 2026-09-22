"""Assert the security headers of a running deployment (docs/WEB_PLAN.md §5.4).

    python tools/check_headers.py https://piano.example.com
    python tools/check_headers.py http://127.0.0.1:8080        # Caddy directly

Exits non-zero and lists every missing or weak header. Also checks that the
app's own files are reachable with the right content types and that nothing
that should stay private is served.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request

REQUIRED = {
    "content-security-policy": ["default-src 'none'", "script-src 'self' 'wasm-unsafe-eval'", "frame-ancestors 'none'"],
    "strict-transport-security": ["max-age="],
    "x-content-type-options": ["nosniff"],
    "referrer-policy": ["no-referrer"],
    "permissions-policy": ["camera=()", "microphone=()"],
    "cross-origin-opener-policy": ["same-origin"],
}
FORBIDDEN_IN_CSP = ["unsafe-inline", "http:", "*"]
MUST_EXIST = {
    "/": "text/html",
    "/src/app.js": "javascript",
    "/vendor/pyodide/pyodide.asm.wasm": "application/wasm",
    "/vendor/fonts/Bravura.woff2": "font/woff2",
    "/core.zip": "application/zip",
}
MUST_NOT_EXIST = ["/tests/test_teach.py", "/raw/teach/score.py", "/.git/HEAD", "/docs/TECHNICAL.md", "/.env"]


def fetch(url: str):
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "dpt-check/1"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}


def main(argv) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    base = argv[1].rstrip("/")
    problems: list[str] = []
    status, headers = fetch(base + "/")
    if status != 200:
        problems.append(f"GET / returned {status}")
    local = "127.0.0.1" in base or "localhost" in base
    for name, needles in REQUIRED.items():
        if name == "strict-transport-security" and local:
            continue   # HSTS only means something over TLS; the dev server has none
        value = headers.get(name, "")
        if not value:
            problems.append(f"missing header {name}")
            continue
        for needle in needles:
            if needle not in value:
                problems.append(f"{name}: expected {needle!r} in {value!r}")
    csp = headers.get("content-security-policy", "")
    for bad in FORBIDDEN_IN_CSP:
        if bad in csp.replace("'self'", "").replace("https:", ""):
            problems.append(f"content-security-policy contains {bad!r}")
    if base.startswith("http://") and "127.0.0.1" not in base and "localhost" not in base:
        problems.append("site is served over plain http")
    for path, ctype in MUST_EXIST.items():
        status, h = fetch(base + path)
        if status != 200:
            problems.append(f"{path}: HTTP {status}")
        elif ctype not in h.get("content-type", ""):
            problems.append(f"{path}: content-type {h.get('content-type')!r}, expected {ctype}")
    for path in MUST_NOT_EXIST:
        status, _ = fetch(base + path)
        if status == 200:
            problems.append(f"{path} is served — the server root is wrong")
    if problems:
        print("FAIL")
        for p in problems:
            print("  -", p)
        return 1
    print(f"OK {base}: headers, content types and root look right")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
