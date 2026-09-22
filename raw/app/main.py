"""Application entry point."""

from __future__ import annotations

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from ..synth import engine
from .controller import Controller


def build_app(argv=None, demo: bool = True):
    argv = list(sys.argv if argv is None else argv)
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName("Retro Audio Workstation")
    app.setOrganizationName("RAW")

    from ..ui.main_window import MainWindow

    controller = Controller()
    window = MainWindow(controller)

    project_arg = next((a for a in argv[1:] if a.endswith(".json")), None)
    if project_arg:
        controller.open_project(project_arg)
    elif demo:
        for name in engine.PRESET_NAMES:
            controller.add_synth_asset(f"sfx_{name}", engine.preset(name))
        controller.stack.mark_clean()
        controller.select(controller.project.assets.by_name("sfx_laser").uid)
        controller.status("Demo sounds loaded — select one and press Space to hear it")
    return app, window, controller


def main(argv=None) -> int:
    app, window, _ = build_app(argv)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
