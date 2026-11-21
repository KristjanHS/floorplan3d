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
- Upstream deps: `uv add -r vendor/FloorplanToBlender3d/requirements.txt`, then relax pins that fail on current Python and log why in `NOTES.md`; never edit upstream's file.
