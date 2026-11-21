# Upstream PR candidates

## grebtsew/FloorplanToBlender3d
- OpenCV 5 rejects numpy-int point tuples: cast to float in `FloorplanToBlenderLib/calculate.py:25` (shim: `scripts/ftb_generate.py`).
- NumPy 2 removed `np.int0`: use `np.intp` in `FloorplanToBlenderLib/detect.py:361` (shim: `scripts/ftb_generate.py`).
- Door/window data paths joined with `\\`: use `os.path.join` in `Blender/floorplan_to_3dObject_in_blender.py:220-262`.
- `const.DOOR_MODEL` is CWD-relative: resolve against the package dir (`FloorplanToBlenderLib/const.py:51`; workaround: `scripts/convert.py` links `Images/`).
