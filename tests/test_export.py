import json
import shutil
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
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


def test_wall_check_and_blender_scale_use_the_resolved_calibration(tmp_path: Path, monkeypatch):
    """A basis calibration must scale export at today's downscale: 100 px @0.4 = 200 px @0.8 -> 0.01 m/px."""
    cal = plans.Calibration((0.0, 0.0), (100.0, 0.0), 2.0, basis=0.4)
    floor = plans.Floor(name="f", pdf=tmp_path / "f.pdf", calibration=cal, work=tmp_path, output=tmp_path / "out")
    floor.meta.write_text(json.dumps({"downscale": 0.8}))
    img = np.full((200, 400), 255, np.uint8)
    img[50:150, 50:350] = 0  # 300 x 100 px drawn
    cv2.imwrite(str(floor.png), img)
    check = export.wall_check(floor, {"extent_m": [3.0, 1.0, 2.6]})
    assert check["drawn_m"] == pytest.approx([3.0, 1.0]) and check["status"] == "OK"

    calls = []
    monkeypatch.setattr(export.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    floor.raw_blend.parent.mkdir(parents=True)
    floor.raw_blend.touch()
    with pytest.raises(FileNotFoundError):  # the stubbed Blender writes no report
        export.export(floor, "blender")
    (cmd,) = calls
    assert cmd[cmd.index("--metres-per-px") + 1] == repr(0.01)
