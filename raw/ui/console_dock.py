"""Scripting console.

Deliberately NOT advertised as a sandbox. Restricting builtins and imports in
Python is not a security boundary — anyone who wants out gets out in one line.
The honest position is a trust prompt: run scripts you wrote. Real isolation
would mean a subprocess with OS-level restrictions, which is a later job.
"""

from __future__ import annotations

import io
import traceback
from contextlib import redirect_stdout

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from ..core.assets import SynthAsset
from ..synth import engine
from .theme import COLORS

BANNER = """Retro Audio Workstation console.
Scripts run with your full user permissions — this is not a sandbox.
Type help() for the API.
"""

HELP = """API
  presets()                       list built-in recipes
  new(name, preset='laser')       create a SynthAsset from a preset
  find(name)                      look up an asset by variable name
  assets()                        list asset names
  render(name, **overrides)       render to an AudioBuffer
  play(name_or_buffer, **ov)      render and play
  stretch(name, to=0.5)           preview at a different length
  set(name, **fields)             edit a sound/instrument (brightness, duration,
                                  amplitude, seed...) or a pattern (tempo,
                                  steps, swing, tracks). Undoable.
  save(path) / load(path)         project io
Sound slots
  slots()                         list slots and what they are bound to
  bind('kick', 'sfx_hit')         bind a slot to a sound
  bind('kick', None)              unbind (renders a placeholder click)
  unbind('kick')                  same as above
Sheet music
  sheet('pattern_lofi', 'out.musicxml')  piano score for MuseScore etc.
Authoring format (docs/AUTHORING_FORMAT.md)
  author(json_text)               import a document from a string
  author_file(path)               import a document from a file
  export_author()                 dump this project as an authoring document
Objects
  project, controller, engine, np
Everything you do here goes through the same command stack as the GUI, so
Ctrl+Z works on it.
"""


class ConsoleDock(QWidget):
    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.c = controller
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)

        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(9)

        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setFont(mono)
        self.output.setPlainText(BANNER)
        v.addWidget(self.output, 1)

        self.input = QLineEdit()
        self.input.setFont(mono)
        self.input.setPlaceholderText(">>>")
        self.input.returnPressed.connect(self._run)
        v.addWidget(self.input)

        self._history: list[str] = []
        self._history_index = 0
        self.input.installEventFilter(self)
        self._namespace = self._build_namespace()

    # ------------------------------------------------------------------- api

    def _build_namespace(self) -> dict:
        import numpy as np

        c = self.c

        def presets():
            return list(engine.PRESET_NAMES)

        def new(name, preset="laser"):
            return c.add_synth_asset(name, engine.preset(preset))

        def find(name):
            return c.project.assets.by_name(name)

        def assets():
            return [a.name for a in c.project.assets.sorted()]

        def _resolve(target):
            if isinstance(target, str):
                a = c.project.assets.by_name(target)
                if a is None:
                    raise KeyError(f"no asset named {target!r}")
                return a
            return target

        def render(target, **overrides):
            return c.render(_resolve(target), overrides, preview=False)

        def play(target, **overrides):
            if hasattr(target, "samples"):
                c.play(target)
                return target
            return c.preview(_resolve(target), overrides)

        def stretch(target, to=None, mode=None):
            ov = {}
            if to is not None:
                ov["duration"] = to
            if mode is not None:
                ov["stretch_mode"] = mode
            return play(target, **ov)

        def set_(target, **fields):
            """Edit a sound, an instrument, or a pattern. Undoable either way."""
            from ..core.commands import Composite, SetAssetField

            asset = _resolve(target)
            label = f"Set {', '.join(fields)}"

            if hasattr(asset, "params"):
                params = asset.params.copy()
                unknown = [k for k in fields if not hasattr(params, k)]
                if unknown:
                    raise AttributeError(
                        f"unknown parameter(s) {', '.join(unknown)}; "
                        f"try: {', '.join(sorted(vars(params)))}"
                    )
                for k, val in fields.items():
                    setattr(params, k, val)
                c.modify_asset(asset.uid, SetAssetField(asset.uid, "params", params, label))
                print(f"{asset.name}: " + ", ".join(f"{k}={v!r}" for k, v in fields.items()))
                return

            unknown = [k for k in fields if not hasattr(asset, k)]
            if unknown:
                raise AttributeError(
                    f"{asset.type_name} has no field(s) {', '.join(unknown)}; "
                    f"try: {', '.join(sorted(k for k in vars(asset) if not k.startswith('_')))}"
                )
            commands = [SetAssetField(asset.uid, k, v, label) for k, v in fields.items()]
            c.modify_asset(
                asset.uid,
                commands[0] if len(commands) == 1 else Composite(commands, label),
            )
            print(f"{asset.name}: " + ", ".join(f"{k}={v!r}" for k, v in fields.items()))

        def author(text):
            result = c.import_authored(text)
            print(result.report())
            return result

        def author_file(path):
            from pathlib import Path

            return author(Path(path).read_text(encoding="utf-8"))

        def export_author():
            import json

            return json.dumps(c.export_authored(), indent=2)

        def slots():
            table = c.project.slots
            if not table:
                print("no slots yet — add one with bind('kick', 'sfx_hit')")
                return
            width = max(len(n) for n in table)
            for name in sorted(table):
                slot = table[name]
                asset = c.project.assets.get(slot.asset) if slot.asset else None
                extra = f"   {slot.overrides}" if slot.overrides else ""
                print(f"{name:<{width}}  {asset.name if asset else '— unbound —'}{extra}")

        def bind(name, target=None):
            uid = _resolve(target).uid if target is not None else None
            c.bind_slot(str(name), uid)
            slots()

        def unbind(name):
            bind(name, None)

        def sheet(target, path):
            from ..core.assets import PatternAsset
            from ..export.sheet import pattern_to_musicxml, write_musicxml

            asset = _resolve(target)
            if not isinstance(asset, PatternAsset):
                raise TypeError("sheet() needs a pattern")
            result = pattern_to_musicxml(asset, c.project)
            written = write_musicxml(result, path)
            print(f"wrote {written}: {result.note_count} notes, {result.measures} bar(s)")
            for w in result.warnings:
                print("  warning:", w)

        def help_():
            print(HELP)

        return {
            "__builtins__": __builtins__,
            "np": np,
            "engine": engine,
            "controller": c,
            "project": c.project,
            "presets": presets,
            "new": new,
            "find": find,
            "assets": assets,
            "render": render,
            "play": play,
            "stretch": stretch,
            "set": set_,
            "slots": slots,
            "bind": bind,
            "unbind": unbind,
            "sheet": sheet,
            "author": author,
            "author_file": author_file,
            "export_author": export_author,
            "save": lambda p: c.save_project(p),
            "load": lambda p: c.open_project(p),
            "help": help_,
        }

    # ----------------------------------------------------------------- input

    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Up and self._history:
                self._history_index = max(0, self._history_index - 1)
                self.input.setText(self._history[self._history_index])
                return True
            if event.key() == Qt.Key.Key_Down and self._history:
                self._history_index = min(len(self._history), self._history_index + 1)
                self.input.setText(
                    self._history[self._history_index]
                    if self._history_index < len(self._history)
                    else ""
                )
                return True
        return super().eventFilter(obj, event)

    def _run(self) -> None:
        source = self.input.text().strip()
        if not source:
            return
        self.input.clear()
        self._history.append(source)
        self._history_index = len(self._history)
        self._append(f">>> {source}")

        self._namespace["project"] = self.c.project
        buffer = io.StringIO()
        try:
            with redirect_stdout(buffer):
                try:
                    result = eval(compile(source, "<console>", "eval"), self._namespace)
                    if result is not None:
                        print(repr(result))
                except SyntaxError:
                    exec(compile(source, "<console>", "exec"), self._namespace)
        except Exception:
            buffer.write(traceback.format_exc(limit=2))
        text = buffer.getvalue().rstrip()
        if text:
            self._append(text)

    def _append(self, text: str) -> None:
        self.output.appendPlainText(text)
        self.output.verticalScrollBar().setValue(self.output.verticalScrollBar().maximum())
