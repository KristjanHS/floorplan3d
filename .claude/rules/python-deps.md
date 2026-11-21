---
paths:
  - "**/*.py"
  - "pyproject.toml"
  - "uv.lock"
last_verified: 2026-11-21
---
# Imports & Dependencies

- `uv` is the source of truth: runtime deps via `uv add`, dev/test via `uv add --group dev`; never `pip install` into the venv. Commit `uv.lock`.
- Third-party `ModuleNotFoundError` → `uv sync` first. Our own `scripts/` modules in tests → `[tool.pytest.ini_options] pythonpath = ["scripts"]`, not an exported `PYTHONPATH`.
- Upstream deps: `uv add opencv-python numpy scipy pillow matplotlib pyfiglet` at current versions — never `uv add -r` upstream's `requirements.txt` (CVE-laden 2022 pins, `docs/security-review-FloorplanToBlender3d.md`); never edit upstream's file.
