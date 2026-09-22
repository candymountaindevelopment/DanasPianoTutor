"""Piano Tutor entry point: `python teach.py [lesson.json]`."""

from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication


def build_app(argv=None):
    argv = list(sys.argv if argv is None else argv)
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("Danas Piano Tutor")
    app.setOrganizationName("RAW")

    from ..ui.teach.splash import SplashScreen
    from ..ui.teach.window import EXAMPLES_DIR, TeachWindow

    window = TeachWindow()
    window.splash = SplashScreen()
    path = next((a for a in argv[1:] if a.lower().endswith(".json")), None)
    if path:
        window.load_path(path)
    else:
        first = sorted(EXAMPLES_DIR.glob("*.json")) if EXAMPLES_DIR.exists() else []
        if first:
            window.load_path(first[0])
        else:
            window.script.apply()
    return app, window


def main(argv=None) -> int:
    app, window = build_app(argv)
    window.show()
    window.splash.show_centered_on(window)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
