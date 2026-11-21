# floorplan3d

Scanned floor-plan PDFs → 3D models (`.blend` / `.glb` / `.obj` + preview), scaled to metres, via
[FloorplanToBlender3d](https://github.com/grebtsew/FloorplanToBlender3d).

Status: bootstrap only — build plan in `docs/plans/FLOORPLAN_TO_3D_SETUP.md`.

## SweetHome3D editing workflow

`homes/`, `sh3d_src/` and `backups/` are local, untracked here.

1. Edit in the SweetHome3D GUI, save to `homes/<name>.sh3d`, **close it**.
2. `make pull NAME=<name>` → `git -C sh3d_src add -A && git -C sh3d_src commit -m "pull: GUI edits"`.
3. Ask Claude Code for an edit (move a piece, paint a wall, add furniture).
4. Claude Code: `make inspect NAME=<name>` → edit `sh3d_src/<name>/Home.xml` → `make validate NAME=<name>` →
   `make preview NAME=<name>` (reads `work/sh3d/<name>/preview.png`) → commit in `sh3d_src`.
5. Close the file in SweetHome3D, then `make push NAME=<name>`: refuses if `homes/<name>.sh3d` changed since the pull,
   backs it up to `backups/`, repacks. Commit the updated `.sh3d_meta.json` in `sh3d_src`, then open the Windows
   path `make push` prints.

`validate` and `push` rebuild the DTD from `SH3D_SRC_ZIP` (default `work/sh3d-src/SweetHome3D-7.6.1-src.zip`).

License: MIT for this repo's code, except `scripts/ftb_generate.py` (GPL-3.0: it imports upstream). The upstream submodule under `vendor/` is GPL-3.0.
