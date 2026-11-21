---
paths:
  - "**/*.py"
  - "pyproject.toml"
  - "uv.lock"
last_verified: 2026-11-21
---
## Imports & Dependencies

- `uv` is the source of truth: runtime deps via `uv add`, dev/test via `uv add --group dev`; never `pip install` into the venv. Commit `uv.lock`.
- On `ModuleNotFoundError`, run `uv sync` first; never set `PYTHONPATH` to paper over it.
- Upstream's requirements under `vendor/` are not ours to edit — mirror what we need into `pyproject.toml` and log the source in `NOTES.md`.
- `constraint-dependencies` = floor, not install — anything imported directly needs `[project.dependencies]`.
