"""Build the browser version of Danas Piano Tutor into web/ (and optionally dist/).

    python tools/build_web.py                  # core.zip, lessons, assets into web/
    python tools/build_web.py --vendor         # also download Pyodide + numpy + Bravura into web/vendor/
    python tools/build_web.py --dist           # also copy a clean, servable tree to dist/

The browser app needs: the `raw` package without its Qt layers (zipped),
the example lessons, the splash photo, the scripting reference, and —
for a self-hosted, CDN-free deployment — the Pyodide runtime files and the
Bravura music font. Only what is listed here ends up in dist/.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
DIST = ROOT / "dist"
sys.path.insert(0, str(ROOT))

PYODIDE_VERSION = "0.27.7"
PYODIDE_CDN = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/"
PYODIDE_FILES = ["pyodide.js", "pyodide.asm.js", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"]
BRAVURA_URL = "https://github.com/steinbergmedia/bravura/raw/master/redist/woff/Bravura.woff2"
# The interface faces (docs/TECHNICAL.md §14.6): serif for titles, sans for
# reading, mono for anything that changes while you play. Self-hosted,
# because the production CSP allows fonts from 'self' only.
GOOGLE_CSS = "https://fonts.googleapis.com/css2?family={}&display=swap"
UI_FONTS = {
    "InstrumentSerif": "Instrument+Serif",
    "InstrumentSans": "Instrument+Sans:wght@400;500;600",
    "JetBrainsMono": "JetBrains+Mono:wght@400;500",
}
BRAVURA_LICENSE_URL = "https://github.com/steinbergmedia/bravura/raw/master/LICENSE.txt"

EXCLUDE_PACKAGES = ("raw/ui/", "raw/app/")
MAX_FILE_MB = 20


def build_core_zip() -> Path:
    out = WEB / "core.zip"
    buf = io.BytesIO()
    count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted((ROOT / "raw").rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if any(rel.startswith(p) for p in EXCLUDE_PACKAGES) or "__pycache__" in rel:
                continue
            z.write(path, rel)
            count += 1
    out.write_bytes(buf.getvalue())
    print(f"core.zip: {count} modules, {out.stat().st_size // 1024} KB")
    return out


def copy_lessons() -> None:
    from raw.teach.authoring import load_lesson_file

    target = WEB / "lessons"
    target.mkdir(parents=True, exist_ok=True)
    for old in target.glob("*.json"):
        old.unlink()
    index = []
    for path in sorted((ROOT / "docs" / "examples" / "lessons").glob("*.json")):
        result = load_lesson_file(path)
        if not result.ok:
            print(f"skip {path.name}: {result.errors}")
            continue
        shutil.copy2(path, target / path.name)
        index.append({
            "file": path.name,
            "title": path.stem[3:].replace("_", " ").title() if path.stem[:2].isdigit() else path.stem,
            "lessons": [{"title": les.title, "level": les.level} for les in result.lessons],
        })
    (target / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    print(f"lessons: {len(index)} files")


def copy_assets() -> None:
    assets = WEB / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "assets" / "tutor_splash.jpg", assets / "tutor_splash.jpg")
    shutil.copy2(ROOT / "docs" / "LESSON_FORMAT.md", assets / "LESSON_FORMAT.md")
    from raw.teach import web_api

    (assets / "about.json").write_text(web_api.about(), encoding="utf-8")
    print("assets: splash photo, LESSON_FORMAT.md, about.json")


def _download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        print(f"  have {target.name}")
        return
    print(f"  get  {url}")
    with urllib.request.urlopen(url, timeout=120) as resp:
        data = resp.read()
    target.write_bytes(data)
    print(f"       {len(data) // 1024} KB  sha256 {hashlib.sha256(data).hexdigest()[:16]}…")


def vendor_ui_fonts() -> None:
    """Download the latin woff2 of each interface face and write fonts.css.

    Google serves per-subset files; we keep `latin` and `latin-ext`, which is
    what the interface needs, and name them <Family>-<weight>-<subset>.woff2.
    A failure here is not fatal: styles.css falls back to system faces.
    """
    import re

    fonts = WEB / "vendor" / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    for stale in fonts.glob("*latin-ext.woff2"):
        stale.unlink()
    rules = []
    for name, query in UI_FONTS.items():
        try:
            req = urllib.request.Request(
                GOOGLE_CSS.format(query),
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                css = resp.read().decode("utf-8")
        except Exception as exc:                       # offline build: keep going
            print(f"  skip {name}: {exc}")
            continue
        blocks = re.findall(r"/\*\s*([\w-]+)\s*\*/\s*@font-face\s*\{(.*?)\}", css, re.S)
        for subset, body in blocks:
            if subset != "latin":                  # the interface is English
                continue
            url = re.search(r"url\((https://[^)]+\.woff2)\)", body)
            weight = re.search(r"font-weight:\s*([\d ]+)", body)
            style = "italic" if "font-style: italic" in body else "normal"
            if not url or style == "italic":
                continue
            w = (weight.group(1).strip().split()[-1] if weight else "400")
            target = fonts / f"{name}-{w}-{subset}.woff2"
            _download(url.group(1), target)
            rules.append(
                f"@font-face {{ font-family: \"{name}\"; font-style: normal; font-weight: {w};\n"
                f"  font-display: swap; src: url(\"fonts/{target.name}\") format(\"woff2\"); }}"
            )
    if rules:
        (WEB / "vendor" / "fonts.css").write_text("\n".join(rules) + "\n", encoding="utf-8")
        print(f"ui fonts: {len(rules)} faces")


def vendor() -> None:
    py = WEB / "vendor" / "pyodide"
    for name in PYODIDE_FILES:
        _download(PYODIDE_CDN + name, py / name)
    lock = json.loads((py / "pyodide-lock.json").read_text(encoding="utf-8"))
    numpy_file = lock["packages"]["numpy"]["file_name"]
    _download(PYODIDE_CDN + numpy_file, py / numpy_file)
    fonts = WEB / "vendor" / "fonts"
    _download(BRAVURA_URL, fonts / "Bravura.woff2")
    _download(BRAVURA_LICENSE_URL, fonts / "Bravura-LICENSE.txt")
    vendor_ui_fonts()
    (py / "VERSION").write_text(PYODIDE_VERSION, encoding="utf-8")


def write_precache() -> None:
    """List every file the service worker should hold, with a content version."""
    files = ["./", "index.html", "styles.css", "sw.js", "core.zip"]
    if (WEB / "vendor" / "fonts.css").exists():
        files.append("vendor/fonts.css")
    files += sorted(p.relative_to(WEB).as_posix() for p in (WEB / "src").glob("*.js"))
    files += sorted(p.relative_to(WEB).as_posix() for p in (WEB / "assets").iterdir() if p.is_file())
    files += sorted(p.relative_to(WEB).as_posix() for p in (WEB / "lessons").glob("*.json"))
    files += sorted("listen/" + p.name for p in (ROOT / "listen").iterdir() if p.is_file())
    vendor = WEB / "vendor"
    if vendor.exists():
        files += sorted(p.relative_to(WEB).as_posix() for p in vendor.rglob("*") if p.is_file() and p.suffix != ".txt")
    digest = hashlib.sha256()
    for rel in files:
        path = (ROOT / rel) if rel.startswith("listen/") else (WEB / rel)
        if path.is_file():
            digest.update(rel.encode())
            digest.update(path.read_bytes())
    version = digest.hexdigest()[:12]
    (WEB / "precache.json").write_text(json.dumps({"version": version, "files": files}, indent=1), encoding="utf-8")
    print(f"precache.json: {len(files)} files, version {version}")


def make_dist() -> None:
    if DIST.exists():
        shutil.rmtree(DIST)
    shutil.copytree(WEB, DIST, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store", "Thumbs.db"))
    # Danas Ear (the microphone pitch listener) ships beside the tutor at /listen/.
    shutil.copytree(ROOT / "listen", DIST / "listen", ignore=shutil.ignore_patterns("__pycache__", ".DS_Store", "Thumbs.db"))
    bad = []
    for path in DIST.rglob("*"):
        if path.is_dir():
            continue
        rel = path.relative_to(DIST).as_posix()
        if path.suffix == ".py":
            bad.append(f"{rel}: loose Python file")
        if path.suffix == ".json" and not (rel.startswith("lessons/") or rel.startswith("vendor/") or rel.startswith("assets/") or rel == "precache.json"):
            bad.append(f"{rel}: unexpected JSON")
        if path.stat().st_size > MAX_FILE_MB * 1024 * 1024:
            bad.append(f"{rel}: over {MAX_FILE_MB} MB")
    if bad:
        raise SystemExit("dist refused:\n  " + "\n  ".join(bad))
    total = sum(p.stat().st_size for p in DIST.rglob("*") if p.is_file())
    print(f"dist/: {total // 1024 // 1024} MB")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vendor", action="store_true", help="download Pyodide, numpy and Bravura into web/vendor/")
    ap.add_argument("--dist", action="store_true", help="copy a servable tree to dist/")
    args = ap.parse_args(argv)
    WEB.mkdir(exist_ok=True)
    build_core_zip()
    copy_lessons()
    copy_assets()
    if args.vendor:
        vendor()
    write_precache()
    if args.dist:
        make_dist()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
