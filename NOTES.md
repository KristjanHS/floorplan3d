# Notes

## Versions
- Upstream `vendor/FloorplanToBlender3d` @ `2da4c95` (vetted: `docs/security-review-FloorplanToBlender3d.md`).
- Blender 5.2.4 LTS, `~/.local/opt/blender-5.2.4-linux-x64/`, tarball sha256 `2fd14c50987426897ba60237ee996edf2b4151f4b1a72a460ca20be58c1d2d86`.
- Upstream run-path deps at current versions via `uv add` (never its `requirements.txt` pins); exact versions in `uv.lock`.

## Upstream quirks
- `make upstream-test` skips `Testing/visuals/` (`cv2.imshow` aborts without a display) and pre-creates `Configs/` (`test_config` reads `../Configs`).
- Upstream code runs under `scripts/sandbox.py` (bwrap: no network, read-only FS except named dirs).
- Runtime shims in `scripts/ftb_generate.py` (upstream stays unpatched): float points for `cv2.pointPolygonTest` (OpenCV 5), `np.int0` alias (NumPy 2).
- No doors/windows on Linux: the Blender script joins their data paths with `\\` (`floorplan_to_3dObject_in_blender.py:220-262`).
- `convert.py` links `Images/` into the scratch dir: door/window templates load CWD-relative (`const.DOOR_MODEL`).
