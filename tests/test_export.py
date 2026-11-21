import json
import os
import shutil
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import cv2
import pymupdf
import pytest

import convert
import export
import plans
import preprocess

BLENDER = shutil.which("blender")
EXAMPLE = plans.ROOT / "vendor" / "FloorplanToBlender3d" / "Images" / "Examples" / "example.png"
CAL = plans.Calibration((100.0, 100.0), (500.0, 100.0), 4.0)  # 400 px = 4 m -> 0.01 m/px
WALL_H = 2.6
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


@pytest.fixture(scope="module")
def converted(tmp_path_factory: pytest.TempPathFactory) -> Iterator[plans.Floor]:
    tmp = tmp_path_factory.mktemp("e2e")
    assert tmp.parts[1] == "tmp"  # convert must copy the PNG past bwrap's /tmp tmpfs; this guards that
    img = cv2.imread(str(EXAMPLE), cv2.IMREAD_GRAYSCALE)
    assert img is not None
    h, w = img.shape
    pdf = tmp / "example.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page(width=w * 72 / 150, height=h * 72 / 150)
        page.insert_image(page.rect, filename=str(EXAMPLE))
        doc.save(pdf)
    floor = plans.Floor(name="ex", pdf=pdf, wall_height_m=WALL_H, work=tmp / "work", output=tmp / "out")
    preprocess.preprocess(floor)
    assert BLENDER is not None
    convert.convert(floor, os.path.realpath(BLENDER))
    yield floor


def outputs(floor: plans.Floor) -> list[Path]:
    return [floor.outdir / f"{export.stem(floor)}{s}" for s in SUFFIXES]


def test_export_scales_to_calibration(converted, tmp_path):
    floor = replace(converted, calibration=CAL, output=tmp_path)
    report = export.export(floor, str(BLENDER))
    assert all(p.stat().st_size > 0 for p in outputs(floor))
    x, _, z = report["extent_m"]
    assert z == pytest.approx(WALL_H, rel=0.01)
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
