# Task: set up a repo that turns my scanned floor-plan PDFs into 3D models with FloorplanToBlender3d

You are Claude Code. Build a small, reproducible pipeline repo. Work step by step, verify each step, and stop and ask me if a decision is irreversible or a check fails twice.

## Goal
Input: scanned (raster) floor-plan PDFs, one floor per page or file.
Output per floor: `.blend` file + `.glb` + `.obj` (for Sweet Home 3D / other tools) + a PNG preview render, **scaled to real metres**.

## Ground rules
- Upstream: https://github.com/grebtsew/FloorplanToBlender3d (GPL-3.0). Do **not** copy its code into my repo; add it as a git submodule pinned to a specific commit. Every pin bump re-runs §Re-run on every pin bump in `docs/security-review-FloorplanToBlender3d.md` first.
- My floor plans are private: `input/` and `output/` must be in `.gitignore`. Never commit or upload them anywhere.
- Python-first. Use a venv (`uv` if available, otherwise `python -m venv`). Pin dependencies.
- Target OS: Linux (also runs under WSL). Blender runs headless (`blender -b`).
- Blender: newest LTS official portable tarball, SHA256-checked, in `~/.local/opt/`, symlinked to `~/.local/bin/blender`, no sudo; Makefile `BLENDER ?= blender`; version + sha256 in `NOTES.md`.
- Isolation: upstream code (library, its tests, the Blender build) runs under `bwrap --unshare-net`, read-only FS except `work/<floor>`.
- License: this repo MIT (`LICENSE`); upstream submodule stays GPL-3.0 — README says so.
- Don't guess upstream behaviour — **read its source** to confirm anything below marked VERIFY.

## Known facts / things to VERIFY in upstream
- README states: Blender > 2.93, Python >= 3.8, `pip install -r requirements.txt`.
- VERIFY entry point: README mentions `create_blender_project_from_floorplan.py`, but the repo root may have `main.py` instead.
- VERIFY prompts: the script is interactive ("Follow instructions"). Find what it asks and make it run non-interactively (CLI args or config file; never through `main.py`'s `input()` dialog, review F4) — prefer config over patching upstream.
- First run creates `system.ini` and `Configs/default.ini`. VERIFY the keys (Blender path, image path, transform/scale) by reading the code.
- Output lands in `./target/floorplan.blender` (VERIFY actual name/extension).
- Detection works best on **small** images — downscale large ones.
- Pinned old deps (e.g. opencv-python) may fail to install on current Python; if so, bump the minimal version needed and record why in `NOTES.md`.
- No Docker fallback: the prebuilt image `grebtsew/floorplan-to-blender` is NO-GO (review §Verdict).

## Repo layout
```
floorplan3d/
  README.md          # how to run, for me (short)
  NOTES.md           # decisions, upstream quirks found, version bumps
  Makefile           # make setup | make preprocess | make convert | make export | make models
  plans.yaml         # per-floor settings (see below)
  input/             # my PDFs (gitignored)
  work/              # intermediate PNGs (gitignored)
  output/            # results (gitignored)
  vendor/FloorplanToBlender3d/   # git submodule
  scripts/
    preprocess.py    # PDF -> clean, downscaled PNG
    convert.py       # runs upstream non-interactively per floor
    export_blend.py  # run inside Blender: scale, export .glb/.obj, render preview
```

## plans.yaml
```yaml
floors:
  - name: ground
    pdf: input/ground.pdf
    page: 1
    # scale calibration: two pixel points on the cleaned PNG + real distance
    calibration: { p1: [x, y], p2: [x, y], metres: 4.20 }
    wall_height_m: 2.6
```
If calibration is missing, still run, but label the output `UNSCALED` and tell me.

## Steps
1. `git init`, create layout, `.gitignore`, add submodule pinned at `2da4c95` (vetted: `docs/security-review-FloorplanToBlender3d.md`), set up venv, install upstream's run-path deps at current versions (never its `requirements.txt` pins) + `pymupdf`, `pyyaml`. Install Blender per the ground rule (download approved 2026-11-21) and check `blender --version`.
2. Run upstream's own example/test (`Testing/`, `pytest`) once to confirm the install works before touching my files.
3. `preprocess.py`: render PDF page with PyMuPDF at ~150 DPI → grayscale → deskew → binarize (Otsu/adaptive) → remove small specks → downscale so the long side is ≤ max_side (default 1800 px), plus a wall mask and an outline frame. Save the PNG plus the scale factor used, so calibration points stay valid, and `<name>_grid.png` with pixel coordinates labelled every 100 px for picking calibration points.
4. `convert.py`: for each floor, call upstream non-interactively, applying fixes F2–F4 in `docs/security-review-FloorplanToBlender3d.md`; keep the unscaled result in `work/<name>/<name>_raw.blend` (export rescales it into `output/<name>/<name>.blend`, so re-runs never double-scale).
5. `export_blend.py` (via `blender -b <file> --python scripts/export_blend.py -- ...`): apply metres-per-pixel scale from calibration, set wall height, export `.glb` and `.obj`, render a top-down + perspective PNG preview.
6. `make models` runs everything end-to-end, per floor, rerunning only steps whose inputs changed.

## Verification (required before saying "done")
- Done = both: pytest e2e on a fixture PDF built from upstream `Images/Examples/example.png`, and the user's real PDFs in `input/` (dropped in at step 3).
- Look at each preview PNG yourself and compare with the source plan image: walls present, no big gaps, rooms not merged.
- Check one known wall length in the exported model against `calibration.metres` (±3%).
- Report per floor: OK / problems seen / suggested fix (e.g. different downscale, stronger cleanup, manual erase of text/dimension lines).

## Expect and report honestly
Automatic detection on scanned plans is fragile (text, dimension lines, furniture symbols confuse it). Doors/windows may be wrong or missing. The output is a **starting shell** to refine (in Blender or by tracing in Sweet Home 3D), not a finished model.

## Remaining (steps 1–6 shipped from `407f4b8`)
- Floors: the page → floor mapping lives in the gitignored `plans.yaml`; prefer the 600 dpi source; rafters/roof pages are not floors.
- Verification per floor: scale-check against measured interior dimensions.
- Upstream drops outer wall contours (fixture x extent −24.8 %): investigate before trusting real-floor shells; record in NOTES.md.
