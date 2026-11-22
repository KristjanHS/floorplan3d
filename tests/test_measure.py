import json
import os
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

import export
import measure
import plans

BLENDER = shutil.which("blender")
pytestmark = pytest.mark.blender
CAL = plans.Calibration((0.0, 0.0), (200.0, 0.0), 5.0)  # 0.025 m/px: not the export test's 0.01
KLK = (482.5, 100.0)  # inside the example's KLK room, between its left and right walls


def test_to_world_flips_y():
    assert measure.to_world((110.0, 150.0), (10.0, 200.0), 0.01) == pytest.approx((1.0, 0.5))


def test_check_gates_at_tolerance():
    assert measure.check(1.029, 1.0) == pytest.approx(2.9)
    for span in (1.031, 0.969):
        with pytest.raises(SystemExit, match="FAIL"):
            measure.check(span, 1.0)


@pytest.fixture(scope="module")
def scaled(converted, tmp_path_factory: pytest.TempPathFactory) -> plans.Floor:
    floor = replace(converted, calibration=CAL, output=tmp_path_factory.mktemp("measure"))
    assert BLENDER is not None
    export.export(floor, os.path.realpath(BLENDER))
    return floor


def wall_faces_px(floor: plans.Floor, y: float, x_mid: float) -> tuple[float, float]:
    """Upstream's own wall faces crossing PNG row y, nearest each side of x_mid, read from its vertex data.

    Not the PNG's black pixels: upstream's wall detection moves faces a few px off the drawn ones
    (KLK: 80 px drawn vs 72.4 px modelled), and the model is built from this data.
    """
    path = floor.work / floor.name / "ftb" / "Data" / "0" / "wall_vertical_verts.txt"
    k = measure.UPSTREAM_PX_PER_UNIT
    xs = []
    for wall in json.loads(path.read_text()):
        for (xa, ya, _), _, (xb, yb, _), _ in wall:  # quad: start ground/top, end ground/top
            (xa, ya), (xb, yb) = (xa * k, ya * k), (xb * k, yb * k)
            if ya != yb and min(ya, yb) <= y <= max(ya, yb):
                xs.append(xa + (y - ya) * (xb - xa) / (yb - ya))
    return max(x for x in xs if x < x_mid), min(x for x in xs if x > x_mid)


def test_measure_span_matches_upstream_walls(scaled):
    (x, y), mpp = KLK, CAL.metres_per_px
    left, right = wall_faces_px(scaled, y, x)
    span, h1, h2 = measure.measure(scaled, (left, y), (right, y), str(BLENDER))
    assert span == pytest.approx((right - left) * mpp, abs=0.5 * mpp)
    assert h1 == pytest.approx((left, y), abs=0.5) and h2 == pytest.approx((right, y), abs=0.5)  # hits back in px


def test_measure_basis_rescales_points_picked_on_another_work_png(scaled):
    (x, y), half = KLK, scaled.downscale() / 2  # points picked on a work PNG at half today's size
    left, right = wall_faces_px(scaled, y, x)
    span, h1, _ = measure.measure(scaled, (left / 2, y / 2), (right / 2, y / 2), str(BLENDER), basis=half)
    assert span == pytest.approx((right - left) * CAL.metres_per_px, abs=0.5 * CAL.metres_per_px)
    assert h1 == pytest.approx((left, y), abs=0.5)  # hits come back in current work px


def test_scaled_report_accepts_the_same_calibration_given_at_another_basis(scaled):
    half = scaled.downscale() / 2
    same = plans.Calibration((0.0, 0.0), (100.0, 0.0), CAL.metres, basis=half)  # = CAL's 200 px today
    assert measure.scaled_report(replace(scaled, calibration=same))["metres_per_px"] == pytest.approx(CAL.metres_per_px)


def test_measure_rejects_a_midpoint_inside_a_wall(scaled):
    """ray_cast hits back faces: from inside a wall both rays stop at its own faces, a thickness-wide span."""
    (x, y) = KLK
    inner = wall_faces_px(scaled, y, x)[0]
    outer = wall_faces_px(scaled, y, inner - 0.5)[0]
    assert inner - outer > 2  # a real wall thickness, so the midpoint below is inside it
    mid = (inner + outer) / 2
    with pytest.raises(SystemExit, match="p1-side wall at .*missing or undetected wall"):
        measure.measure(scaled, (mid - 30, y), (mid + 30, y), str(BLENDER))


def test_measure_rejects_a_far_wall(scaled):
    (x, y) = KLK
    left, right = wall_faces_px(scaled, y, x)
    with pytest.raises(SystemExit, match="p2-side wall at .*missing or undetected wall"):
        measure.measure(scaled, (left, y), ((left + right) / 2, y), str(BLENDER))


def test_measure_fails_loudly_without_a_wall(scaled):
    with pytest.raises(SystemExit, match="no wall hit on the p1 side"):
        measure.measure(scaled, (5.0, 100.0), (60.0, 100.0), str(BLENDER))


@pytest.mark.parametrize(
    ("calibration", "message"),
    [(None, "UNSCALED_report.json missing"), (plans.Calibration((0.0, 0.0), (100.0, 0.0), 5.0), "make models")],
    ids=["calibration_removed", "calibration_changed"],
)
def test_measure_refuses_a_stale_scaled_export(scaled, calibration, message):
    with pytest.raises(SystemExit, match=message):
        measure.measure(replace(scaled, calibration=calibration), (450.0, 100.0), (515.0, 100.0), str(BLENDER))


def test_px_origin_refuses_data_from_another_run(scaled, tmp_path: Path):
    extent = measure.scaled_report(scaled)["extent_raw_units"]
    with pytest.raises(SystemExit, match="different runs"):
        measure.px_origin(scaled, [extent[0] + 0.01, extent[1]])
    data = tmp_path / scaled.name / "ftb" / "Data" / "0"
    shutil.copytree(scaled.work / scaled.name / "ftb" / "Data" / "0", data)
    transform = json.loads((data / "transform.txt").read_text())
    (data / "transform.txt").write_text(json.dumps(transform | {"rotation": [0, 0, 90]}))
    with pytest.raises(SystemExit, match="rotation"):
        measure.px_origin(replace(scaled, work=tmp_path), extent)
