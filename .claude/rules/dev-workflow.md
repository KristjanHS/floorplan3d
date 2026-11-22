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
- Never edit tracked upstream files — keep the pin clean; work around quirks in `scripts/` and log them in `NOTES.md`.
- Point upstream's generated config/output (`system.ini`, `Configs/`, `target/`) outside `vendor/` where it allows; else set `ignore = untracked` for the submodule in `.gitmodules`.
- Version bump: `git -C vendor/FloorplanToBlender3d fetch && git -C … checkout <sha>`, then `git add vendor/FloorplanToBlender3d` + a `NOTES.md` entry. Fresh clone: `git submodule update --init`.
- Call upstream as a subprocess, never import it into our scripts — keeps the MIT/GPL-3.0 split clean.
- Run upstream, and any probe importing it, only under `scripts/sandbox.py` (`--rw <scratch>`), never bare `.venv/bin/python`.

## Blender scripts
- Run headless: `$(BLENDER) --background [in.blend] --python-exit-code 1 --python scripts/x.py -- <args>` — the `.blend` goes before `--python`, or the script silently runs on the default scene.
- Parse only `sys.argv` after `--`. `bpy` scripts get Blender's bundled Python: stdlib, Blender modules (`bmesh`, `mathutils`) and numpy — no opencv/pymupdf/pyyaml.
- Do the uv-side work (YAML, calibration maths) in uv scripts and hand results to the `bpy` script via args or JSON.

## Docker / architecture
- Upstream Docker image is a fallback only — check `which blender` and prefer the local install.
- Flat `scripts/` is the design: no subpackages or abstractions under ~2K LOC, no generic iteration for N=2.
