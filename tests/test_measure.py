import json
import os
import shutil
from dataclasses import replace

import pytest

import export
import measure
import plans

BLENDER = shutil.which("blender")
CAL = plans.Calibration((0.0, 0.0), (200.0, 0.0), 5.0)  # 0.025 m/px: not the export test's 0.01
KLK = ((450.0, 100.0), (515.0, 100.0))  # inside the example's KLK room, near its left and right walls


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
    (p1, p2), mpp = KLK, CAL.metres_per_px
    left, right = wall_faces_px(scaled, p1[1], (p1[0] + p2[0]) / 2)
    span, h1, h2 = measure.measure(scaled, p1, p2, str(BLENDER))
    assert span == pytest.approx((right - left) * mpp, abs=0.5 * mpp)
    assert h1[0] < h2[0]  # the p1-side hit lies towards p1


def test_measure_fails_loudly_without_a_wall(scaled):
    with pytest.raises(SystemExit, match="no wall hit on the p1 side"):
        measure.measure(scaled, (5.0, 100.0), (60.0, 100.0), str(BLENDER))
