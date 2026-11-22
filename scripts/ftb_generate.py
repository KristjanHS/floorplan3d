# SPDX-License-Identifier: GPL-3.0-or-later
# Glue that imports the GPL-3.0 upstream library, so it is GPL-3.0 too (the rest of scripts/ is MIT).
# convert.py runs it only as a sandboxed subprocess, never imports it.
"""Run upstream's detection for one config file; print the generated data dir (relative to the CWD)."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# -I drops the script dir from sys.path; the guard and upstream are imported by path.
sys.path[:0] = [str(HERE), str(HERE.parent / "vendor" / "FloorplanToBlender3d")]

import upstream_guard  # noqa: F401 — exits outside scripts/sandbox.py; keep it before every upstream import

# isort: split
import cv2
import numpy as np
from FloorplanToBlenderLib import execution, floorplan

_point_polygon_test = cv2.pointPolygonTest


def _point_polygon_test_floats(contour, pt, measure_dist):
    # Current OpenCV rejects upstream's numpy-int point tuples (calculate.py:25); upstream stays unpatched.
    return _point_polygon_test(contour, (float(pt[0]), float(pt[1])), measure_dist)


cv2.pointPolygonTest = _point_polygon_test_floats
# NumPy 2 removed np.int0, which detect.py:361 still calls.
np.int0 = np.intp  # pyright: ignore[reportAttributeAccessIssue]

if __name__ == "__main__":
    data_path = execution.simple_single(floorplan.new_floorplan(sys.argv[1]), show=False)
    print(data_path)
