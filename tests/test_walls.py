import math

import numpy as np
import pytest
from wall_fixtures import EXPECTED, PX_PER_CM, plan_image

import walls
from walls import Wall

PPC = PX_PER_CM


def seg_dist(p: tuple[float, float], w: Wall) -> float:
    d = np.subtract(w.p2, w.p1)
    t = np.clip(np.subtract(p, w.p1) @ d / (d @ d), 0, 1)
    return float(np.linalg.norm(np.add(w.p1, t * d) - p))


def along(p1: tuple[float, float], p2: tuple[float, float], f: float, off: float = 0.0) -> tuple[float, float]:
    d = np.subtract(p2, p1) / math.dist(p1, p2)
    q = np.add(p1, f * np.subtract(p2, p1)) + off * np.array([-d[1], d[0]])
    return float(q[0]), float(q[1])


@pytest.fixture(scope="module")
def faces() -> list[Wall]:
    return walls.segments(plan_image(), PPC)


def test_segments_find_all_faces(faces: list[Wall]) -> None:
    for p1, p2, t in EXPECTED:
        for side in (-t / 2, t / 2):
            for f in (0.2, 0.8):
                q = along(p1, p2, f, side)
                assert min(seg_dist(q, s) for s in faces) <= 2, (p1, p2, side, f)


def test_segments_drop_hatching(faces: list[Wall]) -> None:
    angles = [math.degrees(math.atan2(s.p2[1] - s.p1[1], s.p2[0] - s.p1[0])) % 90 for s in faces]
    assert [a for a in angles if 10 < a < 80] == []


def test_pair_recovers_centre_and_thickness(faces: list[Wall]) -> None:
    paired = walls.pair(faces, PPC)
    for p1, p2, t in EXPECTED:
        for f in (0.2, 0.8):
            q = along(p1, p2, f)
            hit = [w for w in paired if seg_dist(q, w) <= 1 and abs(w.thickness - t) <= 2]
            assert hit, (p1, p2, f)


def face(y: float, x1: float = 0, x2: float = 100, tilt: float = 0) -> Wall:
    return Wall((x1, y), (x2, y + (x2 - x1) * math.tan(math.radians(tilt))))


def test_pair_rejects_gap_outside_range() -> None:
    assert len(walls.pair([face(0), face(20 * PPC)], PPC)) == 1
    assert walls.pair([face(0), face(50 * PPC)], PPC) == []
    assert walls.pair([face(0), face(5 * PPC)], PPC) == []


def test_pair_requires_overlap() -> None:
    assert len(walls.pair([face(0), face(16, 30, 130)], PPC)) == 1  # 70 % overlap
    assert walls.pair([face(0), face(16, 50, 150)], PPC) == []  # 50 %


def test_pair_parallel_tolerance() -> None:
    assert len(walls.pair([face(0), face(16, tilt=1)], PPC)) == 1
    assert walls.pair([face(0), face(16, tilt=3)], PPC) == []


def test_merge_collinear_across_door_gap() -> None:
    door = 80 * PPC
    (m,) = walls.merge([Wall((0, 0), (100, 0), 16), Wall((100 + door, 1), (300, 1), 16)], PPC)
    assert m.p1[0] == pytest.approx(0) and m.p2[0] == pytest.approx(300) and abs(m.p1[1]) <= 1
    assert len(walls.merge([Wall((0, 0), (100, 0), 16), Wall((100 + 120 * PPC, 0), (300, 0), 16)], PPC)) == 2
    assert len(walls.merge([Wall((0, 0), (300, 0), 16), Wall((0, 30 * PPC), (300, 30 * PPC), 16)], PPC)) == 2


def test_snap_l_and_t_junctions() -> None:
    top, left = Wall((8, 0), (300, 0), 16), Wall((0, 8), (0, 300), 16)
    stem = Wall((150, 8), (150, 200), 10)
    t, lft, s = walls.snap([top, left, stem], PPC)
    assert t.p1 == pytest.approx((0, 0)) and lft.p1 == pytest.approx((0, 0))
    assert s.p1 == pytest.approx((150, 0)) and s.p2 == pytest.approx((150, 200))
    assert t.p2 == pytest.approx((300, 0))


def test_detect_end_to_end() -> None:
    found = walls.detect(plan_image(), PPC)
    assert len(found) == len(EXPECTED)
    for p1, p2, t in EXPECTED:
        assert any(
            seg_dist(p1, w) <= 2
            and seg_dist(p2, w) <= 2
            and min(math.dist(p1, w.p1), math.dist(p1, w.p2)) <= 3
            and min(math.dist(p2, w.p1), math.dist(p2, w.p2)) <= 3
            and abs(w.thickness - t) <= 2
            for w in found
        ), (p1, p2, t, found)
