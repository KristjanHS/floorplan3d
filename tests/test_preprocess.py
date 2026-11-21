import json
from pathlib import Path

import cv2
import numpy as np
import pymupdf
import pytest

import plans
import preprocess

SKEW = 3.0


def make_pdf(path: Path, skew: float) -> None:
    """Two-room plan with thick walls rotated by `skew` degrees, plus specks."""
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=400)
    pivot = pymupdf.Point(300, 200)
    morph = (pivot, pymupdf.Matrix(skew))
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(80, 80, 520, 320))
    shape.draw_line((300, 80), (300, 320))
    shape.finish(width=6, color=(0, 0, 0), morph=morph)
    for x in range(100, 500, 40):
        shape.draw_circle((x, 360), 0.4)
    shape.finish(width=0, fill=(0, 0, 0))
    shape.commit()
    doc.save(path)


def read(path: Path, flags: int = cv2.IMREAD_COLOR) -> np.ndarray:
    img = cv2.imread(str(path), flags)
    assert img is not None, path
    return img


@pytest.fixture
def floor(tmp_path) -> plans.Floor:
    pdf = tmp_path / "plan.pdf"
    make_pdf(pdf, SKEW)
    return plans.Floor(name="t", pdf=pdf, max_side=1000, work=tmp_path / "work")


def test_preprocess_deskews_and_downscales(floor):
    meta = preprocess.preprocess(floor)
    out = read(floor.png, cv2.IMREAD_GRAYSCALE)
    assert abs(abs(meta["deskew_deg"]) - SKEW) < 0.3
    assert abs(preprocess.skew_angle(out)) < 0.3
    assert max(out.shape) == 1000
    assert meta["downscale"] == pytest.approx(1000 / 1250, abs=1e-3)
    assert set(np.unique(out)) <= {0, 255}
    assert read(floor.grid).shape[:2] == out.shape


def test_remove_specks_keeps_walls():
    img = np.full((100, 100), 255, np.uint8)
    img[10:90, 10:16] = 0  # wall, 480 px
    img[50:52, 50:52] = 0  # speck, 4 px
    out = preprocess.remove_specks(img, min_px=30)
    assert (out[10:90, 10:16] == 0).all()
    assert (out[50:52, 50:52] == 255).all()


def test_plans_rejects_degenerate_calibration(tmp_path):
    y = tmp_path / "plans.yaml"
    y.write_text("floors:\n  - name: a\n    pdf: a.pdf\n    calibration: {p1: [5, 5], p2: [5, 5], metres: 3}\n")
    with pytest.raises(ValueError, match="distinct"):
        plans.load(y)


def basis_floor(tmp_path: Path, basis: float | None, downscale: float | None) -> plans.Floor:
    """A floor calibrated at `basis` whose current work PNG was made at `downscale` (None = no meta yet)."""
    cal = plans.Calibration((100.0, 50.0), (300.0, 50.0), 4.0, basis)
    floor = plans.Floor(name="f", pdf=tmp_path / "f.pdf", calibration=cal, work=tmp_path)
    if downscale is not None:
        floor.meta.write_text(json.dumps({"downscale": downscale}))
    return floor


def test_calibration_basis_rescales_to_the_current_work_png(tmp_path):
    cal = basis_floor(tmp_path, 0.403, 0.806).calibration_px()
    assert cal is not None
    assert cal.p1 == pytest.approx((200.0, 100.0)) and cal.p2 == pytest.approx((600.0, 100.0))
    assert cal.pixels == pytest.approx(400.0)  # doubled
    assert cal.metres_per_px == pytest.approx(0.01)  # halved from 4 m / 200 px


def test_calibration_without_basis_is_current_px_and_needs_no_meta(tmp_path):
    floor = basis_floor(tmp_path, None, None)
    assert floor.calibration_px() == floor.calibration


def test_calibration_basis_without_meta_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="run preprocess"):
        basis_floor(tmp_path, 0.403, None).calibration_px()


@pytest.mark.parametrize("basis", [0, -0.4, 1.5])
def test_plans_rejects_a_basis_outside_downscale_range(tmp_path, basis):
    y = tmp_path / "plans.yaml"
    y.write_text(
        f"floors:\n  - name: a\n    pdf: a.pdf\n    calibration: {{p1: [5, 5], p2: [9, 5], metres: 3, basis: {basis}}}\n"
    )
    with pytest.raises(ValueError, match="basis"):
        plans.load(y)


def test_plans_loads_a_basis(tmp_path):
    y = tmp_path / "plans.yaml"
    y.write_text(
        "floors:\n  - name: a\n    pdf: a.pdf\n    calibration: {p1: [5, 5], p2: [9, 5], metres: 3, basis: 0.4}\n"
    )
    (floor,) = plans.load(y)
    assert floor.calibration is not None and floor.calibration.basis == 0.4
