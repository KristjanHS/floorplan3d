# sweethome3d-tools

Sweet Home 3D tools: git-tracked editing of your 3D home.
Upstream is pinned at `2da4c95` and was reviewed before use ([security review](docs/security-review-FloorplanToBlender3d.md)); its code runs inside `bwrap --unshare-all`.

## Quick start

Needs Linux or WSL, [uv](https://docs.astral.sh/uv/), Python 3.12, Blender 5.2.4 LTS (`blender` on `PATH` or `BLENDER=`) and `bwrap`. SH3D editing also needs the Sweet Home 3D 7.6.1 source zip in `work/sh3d-src/` (fetch command in `NOTES.md`).

```bash
git clone --recurse-submodules https://github.com/KristjanHS/sweethome3d-tools
cd sweethome3d-tools && make sync
cp plans.example.yaml plans.yaml   # set each floor's PDF and two calibration points
make models
```

## What it does

- PDF to `.blend`, `.glb` and `.obj` plus a preview image, scaled to metres from two calibration points.
- Round-trips a Sweet Home 3D home and its unpacked `Home.xml` with `make pull`, `make push` and `make validate` (DTD check). [docs/sweethome3d-workflow.md](docs/sweethome3d-workflow.md) says when to reach for the GUI, a `make` target or Claude Code.
- Draws walls into the gaps between rooms with `make walls`; a re-run replaces only the walls you left unedited.
- `homes/`, `sh3d_src/` and `backups/` hold your own homes; the repo ships none. Put a saved home at `homes/<name>.sh3d` and `make pull NAME=<name>` unpacks it to `sh3d_src/<name>/`.

## Tests

`make check` runs the whitespace check, ruff, pyright and pytest. There is no CI; `make setup-hooks` runs the same gate as a pre-push hook.

## Limits

- Linux or WSL only. The upstream sandbox needs `bwrap`. `make push` and `make window-lib` end in `wslpath -w`, so they exit non-zero outside WSL.
- Upstream runs on OpenCV 5 and NumPy 2 only through 2 local shims ([upstream-pr-candidates.md](docs/upstream-pr-candidates.md)).
- Plan images shrink to an 1800 px long side unless `max_side` in `plans.yaml` says otherwise.

Built with Claude Code, following the method in [claudeconf](https://github.com/KristjanHS/claudeconf).

## License

MIT for this repo's code, except `scripts/ftb_generate.py` (GPL-3.0, because it imports upstream). The upstream submodule under `vendor/` is GPL-3.0.
