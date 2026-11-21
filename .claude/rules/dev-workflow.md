---
paths:
  - "**/*.py"
  - "Makefile"
  - ".gitmodules"
  - "vendor/**"
last_verified: 2026-11-21
---
# Dev Workflow

## Python
- Run via `uv run …` or a `make` target, never bare `python3`/`pip`.
- Verify with pytest or a `make` target, not ad-hoc `python3 -c`; run pytest once per step, never re-run to "confirm".

## Upstream submodule (`vendor/FloorplanToBlender3d/`)
- Never edit files under `vendor/` (GPL-3.0, pinned) — work around quirks in `scripts/` and log them in `NOTES.md`.
- `git status` showing `vendor/… (modified content)` means something wrote into the submodule — revert it before committing.
- Version bump = checkout the new SHA inside the submodule, commit the pin, add a `NOTES.md` entry.

## Blender scripts
- Run headless: `blender --background --python-exit-code 1 --python scripts/x.py -- <args>`; parse only `sys.argv` after `--`.
- `bpy` scripts run on Blender's bundled Python: stdlib + `bpy`/`mathutils` only — no opencv/pymupdf/pyyaml; pass data in via args or JSON.
- Keep pure logic out of `bpy` scripts so pytest can import it under `uv run`.

## Docker / architecture
- Upstream Docker image is a fallback only — check `which blender` and prefer the local install.
- Flat `scripts/` is the design: no subpackages or abstractions under ~2K LOC, no generic iteration for N=2.
