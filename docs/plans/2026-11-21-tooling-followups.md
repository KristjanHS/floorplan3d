# Tooling follow-ups

Left open after setting up rules, make targets and hooks (commits `50a1dee`..`ef65f7e`). Build work
itself lives in `FLOORPLAN_TO_3D_SETUP.md`.

## 1. Deleting `.venv` silently swaps in a different pre-commit

- The installed hooks pin `INSTALL_PYTHON=<repo>/.venv/bin/python`. With `.venv` gone, commits still run,
  but on `~/.local/bin/pre-commit` (3.8.0), not the locked 4.7.0.
- Fix: re-run `make sync`. Optionally make `make setup-hooks` depend on `sync`.

## 4. Blender's bundled Python version may differ from 3.12

- ruff and pyright check against `requires-python` (3.12). `bpy` scripts run on Blender's bundled Python,
  which may be older. Syntax that is valid in 3.12 could pass `make check` and then fail inside Blender.
- Once the Blender version is known, set a per-path ruff `target-version` / pyright `pythonVersion` for
  the bpy scripts, or add a `$(BLENDER) --background --python-expr` smoke test.

## 5. Upstream writes into `vendor/` at runtime

- Once the submodule is added, confirm where `system.ini`, `Configs/` and `target/` land. Then either
  redirect them outside `vendor/` or set `ignore = untracked` in `.gitmodules`, as
  `.claude/rules/dev-workflow.md` says.
