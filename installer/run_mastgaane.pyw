"""No-console launcher for `mastgaane serve` (the local web UI + tray icon): the
Desktop/Start Menu shortcut written by installer\\Setup.cs points pythonw.exe here
instead of opening a console window. Day-to-day CLI use is still `uv run mastgaane ...`
from a terminal (see README.md); this script only covers the shortcut.
"""
import os
import sys

if sys.stdout is None or sys.stderr is None:  # pythonw.exe gives no console streams
    _null = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = sys.stdout or _null
    sys.stderr = sys.stderr or _null

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from mastgaane.cli import app  # noqa: E402

sys.argv = [sys.argv[0], "serve"]
app()
