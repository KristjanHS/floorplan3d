"""Import for effect, before any upstream import: exits unless running inside scripts/sandbox.py.

sandbox.py ro-binds the repo root, so a writable root means the host, where upstream's code would run
unconfined (network, home dir) and leave __pycache__/ under vendor/ (tests/conftest.py fails on that).
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

if os.access(ROOT, os.W_OK):
    sys.exit(f"{Path(sys.argv[0]).name} runs upstream FloorplanToBlender3d code: run it only via scripts/sandbox.py")
