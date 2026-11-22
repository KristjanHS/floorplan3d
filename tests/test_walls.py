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


def test_pair_thick_walls() -> None:
    drawn = [((100, 100), (500, 100), 32), ((100, 100), (100, 500), 32)]  # 40 cm
    paired = walls.pair(walls.segments(plan_image(drawn, None), PPC), PPC)
    for p1, p2, t in drawn:
        for f in (0.2, 0.8):
            q = along(p1, p2, f)
            assert [w for w in paired if seg_dist(q, w) <= 1 and abs(w.thickness - t) <= 1], (p1, p2, f, paired)


def face(y: float, x1: float = 0, x2: float = 100, tilt: float = 0) -> Wall:
    return Wall((x1, y), (x2, y + (x2 - x1) * math.tan(math.radians(tilt))))


def test_pair_rejects_gap_outside_range() -> None:
    assert len(walls.pair([face(0), face(20 * PPC)], PPC)) == 1
    assert walls.pair([face(0), face(50 * PPC)], PPC) == []
    assert walls.pair([face(0), face(5 * PPC)], PPC) == []


def test_pair_requires_overlap() -> None:
    assert len(walls.pair([face(0, 0, 50), face(16, 15, 65)], PPC)) == 1  # 70 % of 50 px, 35 px < MIN_SEG_CM
    assert len(walls.pair([face(0, 0, 200), face(16, 100, 300)], PPC)) == 1  # 50 %, 100 px >= MIN_SEG_CM
    assert walls.pair([face(0, 0, 60), face(16, 30, 90)], PPC) == []  # 50 %, 30 px


def test_pair_uses_each_face_stretch_once() -> None:
    assert len(walls.pair([face(0), face(16), face(32)], PPC)) == 1


def test_pair_symmetric_in_face_order() -> None:
    a, b = face(0, 0, 1000, tilt=0.5), face(16, 800, 1000)
    (ab,), (ba,) = walls.pair([a, b], PPC), walls.pair([b, a], PPC)
    assert ab.thickness == pytest.approx(ba.thickness, abs=0.1)
    assert np.ravel(sorted([ab.p1, ab.p2])) == pytest.approx(np.ravel(sorted([ba.p1, ba.p2])), abs=0.5)


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


def test_snap_ignores_line_extension_past_wall_end() -> None:
    w = Wall((300 + 100 * PPC, 8), (380, 200), 10)
    assert walls.snap([Wall((0, 0), (300, 0), 16), w], PPC)[1] == w


def test_snap_keeps_short_wall_from_collapsing() -> None:
    stub = Wall((200, 5), (200, 30), 16)
    assert walls.snap([Wall((0, 0), (400, 0), 32), stub], PPC)[1] == stub


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


def test_detect_thick_hatched_walls() -> None:
    ppc, t = 1.1, 44  # 40 cm: 45° hatch strokes inside are 56 cm, past MIN_SEG_CM
    drawn = [((100, 100), (700, 100), t), ((100, 100), (100, 600), t), ((400, 100), (400, 500), t)]
    found = walls.detect(plan_image(drawn, None, (800, 800)), ppc)
    expected = [((100, 100), (722, 100)), ((100, 100), (100, 622)), ((400, 100), (400, 522))]
    assert len(found) == len(expected), found
    for p1, p2 in expected:
        assert any(
            min(math.dist(p1, w.p1), math.dist(p1, w.p2)) <= 3
            and min(math.dist(p2, w.p1), math.dist(p2, w.p2)) <= 3
            and abs(w.thickness - t) <= 2
            for w in found
        ), (p1, p2, found)


def test_detect_staggered_t_junctions() -> None:
    drawn = [((100, 400), (1000, 400), 16), ((300, 100), (300, 400), 10), ((600, 400), (600, 800), 10)]
    found = walls.detect(plan_image(drawn, None, (900, 1100)), PPC)
    (corridor,) = [w for w in found if abs(w.p1[1] - w.p2[1]) < 5]
    assert sorted([corridor.p1[0], corridor.p2[0]]) == pytest.approx([92, 1008], abs=3)


def test_score_tolerance_edges() -> None:
    traced = {"t": Wall((0, 0), (200, 0), 15)}
    for off, tilt, hit in ((9, 0, True), (11, 0, False), (0, 2.9, True), (0, 3.1, False)):
        dy = 100 * math.tan(math.radians(tilt))
        g = Wall((0, off - dy), (200, off + dy), 15)
        assert (walls.score([g], traced).unmatched == []) is hit, (off, tilt)
    long = Wall((0, 0), (2000, 2000 * math.tan(math.radians(2.9))), 15)  # 5 cm off at t's middle, 50 cm at its own
    assert walls.score([long], traced).unmatched == []


def test_score_skips_zero_length_traced() -> None:
    assert walls.score([], {"dot": Wall((5, 5), (5, 5), 15)}).total == 0


def test_score_unions_split_pieces() -> None:
    traced = {"t": Wall((0, 0), (200, 0), 15)}
    a, b = Wall((0, 0), (60, 0), 15), Wall((120, 0), (180, 0), 15)  # 30 % each, 60 % together
    assert walls.score([a, b], traced).unmatched == []
    assert walls.score([a, Wall((0, 0), (60, 0), 15)], traced).unmatched == ["t"]  # overlap counted once


def test_score_pass_threshold() -> None:
    traced = {f"t{i}": Wall((0, 100 * i), (200, 100 * i), 15) for i in range(10)}
    traced["outer"] = Wall((0, -100), (200, -100), 45)  # thicker than GAP_CM: not ground truth
    gen = [Wall((0, 100 * i), (200, 100 * i), 15) for i in range(7)] + [Wall((500, 0), (500, 300), 15)]
    s = walls.score(gen, traced)
    assert (s.total, s.ratio, s.passed, s.extras) == (10, 0.7, True, 1)
    assert not walls.score(gen[:6], traced).passed
    empty = walls.score(gen, {"outer": traced["outer"]})
    assert (empty.ratio, empty.passed) == (None, False)


def rect(x0: float, y0: float, x1: float, y1: float) -> list[tuple[float, float]]:
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def test_gap_walls_between_facing_rooms_only() -> None:
    # two 10 cm shafts 15 cm apart: own opposite edges (10) and the far edges (35) are in range but no wall
    (w,) = walls.gap_walls([rect(0, 0, 10, 200), rect(25, 0, 35, 200)], [])
    assert w.thickness == pytest.approx(15)
    assert sorted([w.p1, w.p2]) == [pytest.approx((17.5, 0)), pytest.approx((17.5, 200))]


def test_holes_flag_wide_strips_without_nearer_partner() -> None:
    wide = [rect(0, 0, 100, 200), rect(164, 0, 264, 200)]
    assert walls.gap_walls(wide, []) == []
    (h,) = walls.holes(wide, [])
    assert h.thickness == pytest.approx(64)
    assert walls.holes([rect(0, 0, 100, 200), rect(115, 0, 150, 200), rect(164, 0, 264, 200)], []) == []  # shadowed
    assert walls.holes([rect(0, 0, 100, 200), rect(215, 0, 315, 200)], []) == []  # beyond HOLE_CM


def test_holes_not_fused_with_collinear_walls() -> None:
    # a 15 cm wall (y 0..50) and a 64 cm strip (y 50..200) share the centre line x=100
    rooms = [rect(0, 0, 92.5, 50), rect(107.5, 0, 200, 50), rect(0, 50, 68, 200), rect(132, 50, 232, 200)]
    (h,) = walls.holes(rooms, [])
    assert h.thickness == pytest.approx(64)
    assert sorted([h.p1[1], h.p2[1]]) == pytest.approx([50, 200])


def test_claim_clips_partly_shadowed_strip() -> None:
    # room B shadows only y 0..100 of the 64 cm strip between A and C; the rest stays a hole
    rooms = [rect(0, 0, 100, 200), rect(115, 0, 150, 100), rect(164, 0, 264, 200)]
    (w,) = [w for w in walls.gap_walls(rooms, []) if w.thickness == pytest.approx(15)]
    assert sorted([w.p1, w.p2]) == [pytest.approx((107.5, 0)), pytest.approx((107.5, 100))]
    (h,) = walls.holes(rooms, [])
    assert h.thickness == pytest.approx(64)
    assert sorted([h.p1, h.p2]) == [pytest.approx((132, 100)), pytest.approx((132, 200))]
    # a shadow mid-strip leaves two pieces, never re-merged across it
    mid = [rect(0, 0, 100, 300), rect(115, 100, 150, 200), rect(164, 0, 264, 300)]
    ys = sorted(sorted([h.p1[1], h.p2[1]]) for h in walls.holes(mid, []))
    assert ys == [pytest.approx([0, 100]), pytest.approx([200, 300])]


def test_gap_walls_touching_rooms_open() -> None:
    assert walls.gap_walls([rect(0, 0, 100, 100), rect(100, 0, 200, 100)], []) == []
    assert walls.gap_walls([rect(0, 0, 100, 100), rect(100.5, 0, 200, 100)], []) == []
    assert len(walls.gap_walls([rect(0, 0, 100, 100), rect(101.5, 0, 200, 100)], [])) == 1


def test_gap_walls_no_wall_across_narrow_open_room() -> None:
    assert walls.gap_walls([rect(0, 0, 100, 200), rect(100, 0, 120, 200), rect(120, 0, 220, 200)], []) == []


def test_gap_walls_same_direction_edges_claim_nothing() -> None:
    # a duplicate trace shares the room's top edge; the 15 cm strip above still gets its wall
    assert len(walls.gap_walls([rect(0, 0, 100, 100), rect(0, 0, 100, 50), rect(0, -115, 100, -15)], [])) == 1


def test_gap_walls_min_overlap() -> None:
    assert walls.gap_walls([rect(0, 0, 100, 100), rect(115, 97, 200, 200)], []) == []


def test_gap_walls_room_to_wall_face_snaps_to_anchor() -> None:
    outer = Wall((-22.5, -100), (-22.5, 300), 45)  # inner face x = 0
    room = rect(19.5, 0, 300, 200)
    top = Wall((-45, -22.5), (300, -22.5), 45)  # inner face y = 0, touching the room: open, but an anchor
    (w,) = walls.gap_walls([room], [outer, top])
    assert w.thickness == pytest.approx(19.5)
    assert sorted([w.p1, w.p2]) == [pytest.approx((9.75, -22.5)), pytest.approx((9.75, 200))]


def test_gap_walls_room_slack_to_wall_face_is_touching() -> None:
    outer = Wall((-22.5, -100), (-22.5, 300), 45)  # inner face x = 0
    assert walls.gap_walls([rect(3, 0, 300, 200)], [outer]) == []
    (w,) = walls.gap_walls([rect(-100, 0, 0, 200), rect(3, 0, 300, 200)], [])  # same 3 cm between rooms: a wall
    assert w.thickness == pytest.approx(3)


def test_gap_walls_skip_anchor_to_anchor_gaps() -> None:
    a, b = Wall((0, 0), (0, 300), 20), Wall((30, 0), (30, 300), 20)  # faces 10 cm apart, no room between
    assert walls.gap_walls([], [a, b]) == []


def test_matches_traced_within_tolerance() -> None:
    g = Wall((0, 0), (200, 0), 15)
    assert walls.matches(g, [Wall((-50, 9), (300, 9), 10)])
    assert not walls.matches(g, [Wall((-50, 11), (300, 11), 10)])
    assert not walls.matches(g, [Wall((0, 0), (80, 0), 10)])  # covers 40 % < MATCH_COVER
