# Tooling follow-ups

Status: ✅ SHIPPED 2026-11-21 — item 1 fixed, item 4 closed as refuted.

Left open after setting up rules, make targets and hooks (commits `50a1dee`..`ef65f7e`). Build work
itself lives in `FLOORPLAN_TO_3D_SETUP.md`.

## 1. Deleting `.venv` silently swaps in a different pre-commit

- The installed hooks pin `INSTALL_PYTHON=<repo>/.venv/bin/python`. With `.venv` gone, commits still run,
  but on `~/.local/bin/pre-commit` (3.8.0), not the locked 4.7.0.
- Fixed: `.pre-commit-config.yaml` sets `minimum_pre_commit_version: '4.7.0'` → 3.8.0 now exits 1 with
  an InvalidConfigError; recover with `make sync`.

## 4. Blender's bundled Python version may differ from 3.12

- Refuted: Blender 5.2.4 LTS bundles Python 3.13.13, newer than 3.12; `test_export.py` and
  `test_measure.py` already run both bpy scripts inside real Blender.
