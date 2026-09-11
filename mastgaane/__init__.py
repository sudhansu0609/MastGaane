"""MastGaane - late-night Instagram Reels -> songs -> playlists."""
import sys as _sys

__version__ = "0.1.0"

for _stream in (_sys.stdout, _sys.stderr):  # Windows consoles default to cp1252; logs contain unicode
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass
