"""Wall geometry, no XML: LSD face segments -> parallel pairs -> merged, snapped centre lines. Plan: docs/plans/2026-11-21-sh3d-auto-walls.md."""

import math
from dataclasses import dataclass, field
from itertools import combinations

import cv2
import numpy as np

GAP_CM = (8.0, 40.0)
GAP_SLACK_PX = 1.0  # LSD face localisation error; a wall drawn at exactly 40 cm measures ~40.2
MIN_SEG_CM = 50.0  # hatch diagonals across an L corner exceed the wall-thickness diagonal
STROKE_PX = 5.0  # LSD edges of one drawn stroke closer than this collapse into one face; pixels, not plan scale
PARALLEL_DEG = 2.0
MIN_OVERLAP = 0.6
MERGE_OFFSET_CM, MERGE_GAP_CM, MERGE_DEG = 5.0, 100.0, 2.0
SNAP_CM = 15.0
ROOM_GAP_CM = (1.0, 40.0)  # an empty strip this wide between traced rooms is a wall; touching rooms are open
MATCH_DIST_CM, MATCH_DEG, MATCH_COVER = 10.0, 3.0, 0.5
PASS_RATIO = 0.70

Point = tuple[float, float]


@dataclass(frozen=True)
class Wall:
    p1: Point
    p2: Point
    thickness: float = 0.0  # 0 for a single face segment

    @property
    def length(self) -> float:
        return math.dist(self.p1, self.p2)


def _axes(w: Wall) -> tuple[np.ndarray, np.ndarray]:
    d = np.subtract(w.p2, w.p1) / (w.length or 1.0)
    return d, np.array([-d[1], d[0]])


def _angle(a: Wall, b: Wall) -> float:
    return math.degrees(math.acos(min(1.0, abs(float(_axes(a)[0] @ _axes(b)[0])))))


def _span(base: Wall, w: Wall) -> tuple[float, float, float]:
    """w's endpoints projected on base's axis (t0 <= t1, from base.p1) and w's midpoint offset along base's normal."""
    d, n = _axes(base)
    ts = [float(np.subtract(p, base.p1) @ d) for p in (w.p1, w.p2)]
    mid = np.add(w.p1, w.p2) / 2 - base.p1
    return min(ts), max(ts), float(mid @ n)


def _joinable(a: Wall, b: Wall, offset: float, gap: float, deg: float) -> bool:
    if _angle(a, b) > deg:
        return False
    t0, t1, off = _span(a, b)
    return abs(off) <= offset and max(t0 - a.length, -t1, 0.0) <= gap


def _fuse(group: list[Wall]) -> Wall:
    base = group[0]
    d, n = _axes(base)
    spans = [_span(base, w) for w in group]
    weights = [w.length for w in group]
    off = float(np.average([s[2] for s in spans], weights=weights))
    o = np.asarray(base.p1) + n * off
    p1, p2 = o + d * min(s[0] for s in spans), o + d * max(s[1] for s in spans)
    thickness = float(np.average([w.thickness for w in group], weights=weights))
    return Wall((float(p1[0]), float(p1[1])), (float(p2[0]), float(p2[1])), thickness)


def _join(walls: list[Wall], offset: float, gap: float, deg: float) -> list[Wall]:
    """Fuse near-collinear walls (direction, normal offset, axial gap) until nothing changes."""
    while True:
        groups: list[list[Wall]] = []
        for w in sorted(walls, key=lambda w: -w.length):
            group = next((g for g in groups if _joinable(g[0], w, offset, gap, deg)), None)
            if group is None:
                groups.append([w])
            else:
                group.append(w)
        if len(groups) == len(walls):
            return walls
        walls = [_fuse(g) for g in groups]


def segments(img: np.ndarray, px_per_cm: float) -> list[Wall]:
    """Face lines in a grey image: LSD, both edges of a stroke collapsed, shorter than MIN_SEG_CM dropped."""
    lines = cv2.createLineSegmentDetector().detect(img)[0]
    if lines is None:
        return []
    faces = [Wall((x1, y1), (x2, y2)) for x1, y1, x2, y2 in lines.reshape(-1, 4).tolist()]
    faces = _join(faces, STROKE_PX, STROKE_PX, PARALLEL_DEG)
    return [f for f in faces if f.length >= MIN_SEG_CM * px_per_cm and _axial(f)]


def _axial(w: Wall) -> bool:
    """Within PARALLEL_DEG of 0°/90°: 45° hatch in thick walls outlives MIN_SEG_CM; diagonal walls are hand-traced."""
    d = _axes(w)[0]
    return math.degrees(math.asin(min(1.0, min(abs(float(d[0])), abs(float(d[1])))))) <= PARALLEL_DEG


def _free(used: list[tuple[float, float]], t0: float, t1: float, tol: float) -> bool:
    return all(min(t1, u1) - max(t0, u0) <= tol for u0, u1 in used)


def _between(a: Wall, b: Wall) -> tuple[float, float, np.ndarray, np.ndarray]:
    """Overlap length, face gap at the overlap midpoint and the centre-line ends; symmetric in a and b."""
    da, db = _axes(a)[0], _axes(b)[0]
    d = da + (db if da @ db >= 0 else -db)
    d = d / np.linalg.norm(d)
    n = np.array([-d[1], d[0]])
    o = (np.add(a.p1, a.p2) + np.add(b.p1, b.p2)) / 4
    ta, tb = (sorted(float(np.subtract(p, o) @ d) for p in (w.p1, w.p2)) for w in (a, b))
    s0, s1 = max(ta[0], tb[0]), min(ta[1], tb[1])
    m = (s0 + s1) / 2
    qa, qb = (
        np.asarray(w.p1) + dw * ((m - float(np.subtract(w.p1, o) @ d)) / float(dw @ d)) for w, dw in ((a, da), (b, db))
    )
    c = (qa + qb) / 2
    return s1 - s0, abs(float((qa - qb) @ n)), c + d * (s0 - m), c + d * (s1 - m)


def pair(faces: list[Wall], px_per_cm: float) -> list[Wall]:
    """Centre-line walls from parallel, overlapping faces GAP_CM apart; nearest gap first, each face stretch used once."""
    lo, hi = GAP_CM[0] * px_per_cm - GAP_SLACK_PX, GAP_CM[1] * px_per_cm + GAP_SLACK_PX
    cands: list[tuple[float, int, int, np.ndarray, np.ndarray]] = []
    for i, j in combinations(range(len(faces)), 2):
        a, b = faces[i], faces[j]
        if _angle(a, b) > PARALLEL_DEG:
            continue
        overlap, gap, p, q = _between(a, b)
        if overlap < min(MIN_OVERLAP * min(a.length, b.length), MIN_SEG_CM * px_per_cm) or not lo <= gap <= hi:
            continue
        cands.append((gap, i, j, p, q))
    return _claim(cands, faces)


def _claim(cands: list[tuple[float, int, int, np.ndarray, np.ndarray]], faces: list[Wall]) -> list[Wall]:
    """Nearest gap first; a candidate survives only while both its face stretches are unclaimed."""
    used: dict[int, list[tuple[float, float]]] = {}
    out: list[Wall] = []
    for gap, i, j, p, q in sorted(cands, key=lambda c: c[0]):
        stretch: dict[int, tuple[float, float]] = {}
        for k in (i, j):
            t0, t1 = sorted(float(np.subtract(x, faces[k].p1) @ _axes(faces[k])[0]) for x in (p, q))
            stretch[k] = (t0, t1)
        if not all(_free(used.get(k, []), *stretch[k], STROKE_PX) for k in (i, j)):
            continue
        for k in (i, j):
            used.setdefault(k, []).append(stretch[k])
        out.append(Wall((float(p[0]), float(p[1])), (float(q[0]), float(q[1])), gap))
    return out


def merge(walls: list[Wall], px_per_cm: float) -> list[Wall]:
    """Join collinear pieces, bridging gaps up to MERGE_GAP_CM (door openings)."""
    return _join(walls, MERGE_OFFSET_CM * px_per_cm, MERGE_GAP_CM * px_per_cm, MERGE_DEG)


def _intersect(a: Wall, b: Wall) -> np.ndarray | None:
    da, db = _axes(a)[0], _axes(b)[0]
    cross = float(da[0] * db[1] - da[1] * db[0])
    if abs(cross) < math.sin(math.radians(PARALLEL_DEG)):
        return None
    r = np.subtract(b.p1, a.p1)
    return np.asarray(a.p1) + da * float((r[0] * db[1] - r[1] * db[0]) / cross)


def snap(walls: list[Wall], px_per_cm: float) -> list[Wall]:
    """Move each endpoint near another wall onto the centre-line intersection (L and T junctions)."""
    out: list[Wall] = []
    for i, w in enumerate(walls):
        ends = [np.asarray(w.p1, float), np.asarray(w.p2, float)]
        for k, e in enumerate(ends):
            best: tuple[float, np.ndarray] | None = None
            for j, v in enumerate(walls):
                x = None if j == i else _intersect(w, v)
                if x is None:
                    continue
                tol = SNAP_CM * px_per_cm + (w.thickness + v.thickness) / 2
                t = float(np.subtract(x, v.p1) @ _axes(v)[0])
                dist = float(np.linalg.norm(x - e))
                if -tol <= t <= v.length + tol and dist <= tol and (best is None or dist < best[0]):
                    best = (dist, x)
            if best is not None:
                ends[k] = best[1]
        if math.dist(*ends) < MIN_SEG_CM * px_per_cm / 2:
            ends = [np.asarray(w.p1, float), np.asarray(w.p2, float)]
        out.append(Wall((float(ends[0][0]), float(ends[0][1])), (float(ends[1][0]), float(ends[1][1])), w.thickness))
    return out


def detect(img: np.ndarray, px_per_cm: float) -> list[Wall]:
    return snap(merge(pair(segments(img, px_per_cm), px_per_cm), px_per_cm), px_per_cm)


def _cover(t: Wall, g: Wall) -> tuple[float, float] | None:
    """Stretch of traced t (cm along t from t.p1) covered by generated g within MATCH_DEG and MATCH_DIST_CM, else None."""
    if _angle(t, g) > MATCH_DEG:
        return None
    d, n = _axes(t)
    s0, s1, _ = _span(t, g)
    s0, s1 = max(s0, 0.0), min(s1, t.length)
    if s1 <= s0:
        return None
    dg = _axes(g)[0]
    m = (s0 + s1) / 2
    q = np.asarray(g.p1) + dg * ((m - float(np.subtract(g.p1, t.p1) @ d)) / float(dg @ d))
    return (s0, s1) if abs(float(np.subtract(q, t.p1) @ n)) <= MATCH_DIST_CM else None


@dataclass
class Score:
    total: int  # traced walls with thickness inside GAP_CM (the ground truth)
    unmatched: list[str] = field(default_factory=list)
    extras: int = 0  # generated walls near no traced wall; reported, not gated

    @property
    def ratio(self) -> float | None:
        return (self.total - len(self.unmatched)) / self.total if self.total else None

    @property
    def passed(self) -> bool:
        return self.ratio is not None and self.ratio >= PASS_RATIO


def score(generated: list[Wall], traced: dict[str, Wall]) -> Score:
    """Plan cm. A traced wall matches when the union of generated stretches near it covers MATCH_COVER of its length."""
    truth = {k: t for k, t in traced.items() if GAP_CM[0] <= t.thickness <= GAP_CM[1]}
    out = Score(len(truth))
    for k, t in truth.items():
        covered, end = 0.0, 0.0
        for s0, s1 in sorted(c for g in generated if (c := _cover(t, g))):
            covered += max(0.0, s1 - max(s0, end))
            end = max(end, s1)
        if covered < MATCH_COVER * t.length:
            out.unmatched.append(k)
    out.extras = sum(1 for g in generated if not any(_cover(t, g) for t in traced.values()))
    return out


def outward(ring: list[Point]) -> list[Wall]:
    """Polygon edges oriented so the outside is on each edge's right: (d[1], -d[0])."""
    area = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]))
    ring = ring if area > 0 else ring[::-1]
    return [Wall(a, b) for a, b in zip(ring, ring[1:] + ring[:1]) if math.dist(a, b) > 0]


def _facing(a: Wall, b: Wall) -> bool:
    ma, mb = np.add(a.p1, a.p2) / 2, np.add(b.p1, b.p2) / 2
    (da, _), (db, _) = _axes(a), _axes(b)
    return float((mb - ma) @ (da[1], -da[0])) > 0 and float((ma - mb) @ (db[1], -db[0])) > 0


def _faces(w: Wall) -> list[Wall]:
    """A wall's two long faces, outward() oriented (body on the left)."""
    n, h = _axes(w)[1], w.thickness / 2
    p1, p2 = np.asarray(w.p1, float), np.asarray(w.p2, float)
    ring = [(float(x), float(y)) for x, y in (p1 + n * h, p2 + n * h, p2 - n * h, p1 - n * h)]
    return outward(ring)[::2]


def gap_walls(rooms: list[list[Point]], anchors: list[Wall]) -> list[Wall]:
    """Plan cm. A wall centred in each ROOM_GAP_CM strip a room edge faces (another room, or a traced anchor's face);
    merged, then snapped onto each other and the anchors (not returned). Anchor-to-anchor gaps are not rooms' business."""
    lo, hi = ROOM_GAP_CM
    edges = [(True, e) for r in rooms for e in outward(r)] + [(False, f) for a in anchors for f in _faces(a)]
    cands: list[tuple[float, int, int, np.ndarray, np.ndarray]] = []
    for i, j in combinations(range(len(edges)), 2):
        (ra, a), (rb, b) = edges[i], edges[j]
        if not (ra or rb) or _angle(a, b) > PARALLEL_DEG or not _facing(a, b):
            continue
        overlap, gap, p, q = _between(a, b)
        if overlap > 0 and lo <= gap <= hi:
            cands.append((gap, i, j, p, q))
    merged = merge(_claim(cands, [e for _, e in edges]), 1.0)
    return snap(merged + anchors, 1.0)[: len(merged)]
