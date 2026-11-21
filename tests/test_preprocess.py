import json
from dataclasses import replace
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
    shape.draw_line((80, 350), (520, 350))  # dimension line: the wall mask drops it
    shape.finish(width=1.5, color=(0, 0, 0), morph=morph)
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
    assert meta["wall_min_px"] == 6
    assert 0.5 < meta["mask_ink_ratio"] < 0.97
    assert meta["outline_cover"] >= preprocess.MIN_OUTLINE_COVER
    column = out[:, out.shape[1] // 2] == 0
    assert column.any()
    top = int(np.argmax(column))
    assert column[top : top + 4].sum() <= 3  # a 3-px outline frame sits above the ~10-px wall


def test_preprocess_without_wall_mask_keeps_all_ink_and_draws_no_frame(floor):
    meta = preprocess.preprocess(replace(floor, wall_min_px=0))
    out = read(floor.png, cv2.IMREAD_GRAYSCALE)
    assert meta["mask_ink_ratio"] == 1.0
    column = out[:, out.shape[1] // 2] == 0
    assert column.any()
    top = int(np.argmax(column))
    assert column[top : top + 4].all()  # the wall itself is the topmost ink


def test_remove_specks_keeps_walls():
    img = np.full((100, 100), 255, np.uint8)
    img[10:90, 10:16] = 0  # wall, 480 px
    img[50:52, 50:52] = 0  # speck, 4 px
    out = preprocess.remove_specks(img, min_px=30)
    assert (out[10:90, 10:16] == 0).all()
    assert (out[50:52, 50:52] == 255).all()


def test_wall_mask_keeps_walls_and_drops_thin_ink():
    img = np.full((420, 500), 255, np.uint8)
    img[20:380, 20:480] = 0
    img[60:340, 60:440] = 255  # 40-px ring
    img[60:340, 200:212] = 0  # 12-px partition
    img[60:340, 300:305] = 0  # 5-px partition
    img[400:402, 20:480] = 0  # 2-px dimension line
    for x in range(90, 170, 12):
        img[100:120, x : x + 3] = 0  # 3-px text strokes
    img[150:165, 350:365] = 0
    img[155:160, 355:360] = 255  # 15×15 glyph, 5-px stroke
    walls = img.copy()
    walls[60:340, 300:305] = walls[400:402] = walls[100:120, 90:170] = walls[150:165, 350:365] = 255
    junctions = np.zeros(img.shape, bool)
    junctions[[*range(60, 66), *range(334, 340)], 300:305] = True  # the 5-px partition may leave nubs on the ring

    out = preprocess.wall_mask(img, min_px=6, min_area=300)
    assert set(np.unique(out)) <= {0, 255}
    assert (out[(walls == 255) & ~junctions] == 255).all()  # thin partition, line, text, glyph gone
    assert np.count_nonzero(out[walls == 0] == 0) / np.count_nonzero(walls == 0) > 0.99


def test_wall_mask_drops_bold_glyphs():
    img = np.full((200, 200), 255, np.uint8)
    img[20:40, 20:28] = img[32:40, 20:40] = 0  # 8-px-stroke "L" glyph, 256 px
    img[60:160, 100:108] = 0  # 8-px wall stub, 800 px
    out = preprocess.wall_mask(img, min_px=6, min_area=300)
    assert (out[20:40, 20:40] == 255).all()
    assert np.count_nonzero(out[60:160, 100:108] == 0) > 0.95 * 800  # opening only rounds its corners


@pytest.mark.parametrize("min_px", [4, 6])
def test_wall_mask_keeps_a_diagonal_wall(min_px: int):
    img = np.full((300, 300), 255, np.uint8)
    cv2.line(img, (50, 50), (250, 250), 0, 14)  # 45°, 14 px thick
    ink = img == 0
    out = preprocess.wall_mask(img, min_px=min_px, min_area=300)
    assert np.count_nonzero(out[ink] == 0) / np.count_nonzero(ink) > 0.99


@pytest.mark.parametrize(("cut", "expect_ok"), [(False, True), (True, False)], ids=["door-gap", "split"])
def test_outline_cover(cut: bool, expect_ok: bool):
    img = np.full((460, 600), 255, np.uint8)
    img[80:380, 100:500] = 0
    img[90:370, 110:490] = 255
    img[370:380, 280:330] = 255  # door
    if cut:
        img[:, 290:310] = 255  # 20-px gap through both long walls
    assert (preprocess.outline_cover(img) >= preprocess.MIN_OUTLINE_COVER) == expect_ok


def test_plans_rejects_degenerate_calibration(tmp_path):
    y = tmp_path / "plans.yaml"
    y.write_text("floors:\n  - name: a\n    pdf: a.pdf\n    calibration: {p1: [5, 5], p2: [5, 5], metres: 3}\n")
    with pytest.raises(ValueError, match="distinct"):
        plans.load(y)


def test_plans_rejects_a_negative_wall_min_px(tmp_path):
    y = tmp_path / "plans.yaml"
    y.write_text("floors:\n  - name: a\n    pdf: a.pdf\n    wall_min_px: -1\n")
    with pytest.raises(ValueError, match="wall_min_px"):
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
