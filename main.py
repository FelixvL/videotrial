#!/usr/bin/env python3
"""
CineMarker — start de applicatie:   python main.py

Vereist: PyQt6, python-mpv, libmpv (C:\\mpv of naast dit bestand) en FFmpeg in PATH.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent

# libmpv-2.dll vindbaar maken: C:\mpv e.d. én de app-map zelf vóór op PATH
if sys.platform == "win32":
    for candidate in (r"C:\mpv", r"C:\Program Files\mpv", r"C:\Program Files (x86)\mpv"):
        if os.path.isdir(candidate):
            os.environ["PATH"] = candidate + os.pathsep + os.environ["PATH"]
    os.environ["PATH"] = str(APP_ROOT) + os.pathsep + os.environ["PATH"]

if __name__ == "__main__":
    from cinemarker.app import main
    sys.exit(main())
