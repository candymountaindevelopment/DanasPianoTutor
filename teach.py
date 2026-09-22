"""Launch Danas Piano Tutor: python teach.py [lesson.json]

Runs from any working directory. Checks the interpreter and the required
packages first so a wrong Python (an old one behind the .py file
association, or one without PyQt6) gives a readable message instead of a
traceback.
"""

import os
import sys

REQUIRED = (3, 12)
PACKAGES = {"PyQt6": "PyQt6", "numpy": "numpy", "sounddevice": "sounddevice"}


def _fail(message):
    print(message, file=sys.stderr)
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, message, "Danas Piano Tutor", 0x10)
        except Exception:
            pass
    return 1


def _check():
    if sys.version_info < REQUIRED:
        return ("This Python is %s; Danas Piano Tutor needs %d.%d or newer.\n\n"
                "Run it with:\n    py -3.14 teach.py\nor double-click 'Danas Piano Tutor.bat'."
                % (sys.version.split()[0], REQUIRED[0], REQUIRED[1]))
    missing = []
    for module, package in PACKAGES.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        return ("Python %s at\n%s\nis missing: %s.\n\nInstall with:\n    \"%s\" -m pip install -r requirements.txt"
                % (sys.version.split()[0], sys.executable, ", ".join(missing), sys.executable))
    return None


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    os.chdir(here)
    problem = _check()
    if problem:
        return _fail(problem)
    from raw.teach.main import main as run

    return run()


if __name__ == "__main__":
    raise SystemExit(main())
