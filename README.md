# sweethome3d-tools

Scanned floor-plan PDFs → 3D models (`.blend` / `.glb` / `.obj` + preview), scaled to metres, via
[FloorplanToBlender3d](https://github.com/grebtsew/FloorplanToBlender3d).

Status: bootstrap only — build plan in `docs/plans/FLOORPLAN_TO_3D_SETUP.md`.

## SweetHome3D editing

A git-tracked round-trip between the SweetHome3D GUI and an unpacked `Home.xml`, with auto-pull, DTD validation,
plan previews and room-gap wall generation. When to use the GUI, a `make` target or Claude Code:
[`docs/sweethome3d-workflow.md`](docs/sweethome3d-workflow.md).

`homes/`, `sh3d_src/` and `backups/` are local, untracked here.
`validate` and `push` rebuild the DTD from `SH3D_SRC_ZIP` (default `work/sh3d-src/SweetHome3D-7.6.1-src.zip`).

License: MIT for this repo's code, except `scripts/ftb_generate.py` (GPL-3.0: it imports upstream). The upstream submodule under `vendor/` is GPL-3.0.
