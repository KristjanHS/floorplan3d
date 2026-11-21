import json
import shutil
from dataclasses import replace
from pathlib import Path

import cv2
import pytest

import export
import plans

BLENDER = shutil.which("blender")
CAL = plans.Calibration((100.0, 100.0), (500.0, 100.0), 4.0)  # 400 px = 4 m -> 0.01 m/px
SUFFIXES = (".blend", ".glb", ".obj", "_top.png", "_persp.png")

pytestmark = pytest.mark.skipif(BLENDER is None, reason="blender not on PATH")


def points(nested: list):
    for v in nested:
        if v and isinstance(v[0], (int, float)):
            yield v
        else:
            yield from points(v)


def raw_x_units(floor: plans.Floor) -> float:
    """X extent of upstream's own wall/room/floor vertex data (1 unit = 100 px), read without Blender."""
    data = floor.work / floor.name / "ftb" / "Data" / "0"
    parts = ("wall_vertical", "room", "floor")
    xs = [p[0] for part in parts for p in points(json.loads((data / f"{part}_verts.txt").read_text()))]
    return max(xs) - min(xs)


def outputs(floor: plans.Floor) -> list[Path]:
    return [floor.outdir / f"{export.stem(floor)}{s}" for s in SUFFIXES]


def test_export_scales_to_calibration(converted, tmp_path):
    floor = replace(converted, calibration=CAL, output=tmp_path)
    report = export.export(floor, str(BLENDER))
    assert all(p.stat().st_size > 0 for p in outputs(floor))
    x, _, z = report["extent_m"]
    assert z == pytest.approx(converted.wall_height_m, rel=0.01)
    assert x == pytest.approx(raw_x_units(floor) * 100 * CAL.metres_per_px, rel=0.05)
    img = cv2.imread(str(floor.png), cv2.IMREAD_GRAYSCALE)
    _, _, w, h = cv2.boundingRect(cv2.findNonZero((img == 0).astype("uint8")))
    assert report["wall_check"]["drawn_m"] == pytest.approx([w * CAL.metres_per_px, h * CAL.metres_per_px], rel=0.01)


def test_export_unscaled_is_labelled(converted, tmp_path):
    floor = replace(converted, output=tmp_path)
    report = export.export(floor, str(BLENDER))
    files = outputs(floor)
    assert all(p.stat().st_size > 0 and "UNSCALED" in p.name for p in files)
    assert report["label"] == "UNSCALED" and report["extent_m"] is None
