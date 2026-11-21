"""Face-to-face span of a room in output/<floor>/<floor>.blend, between two work-PNG points inside it."""

import argparse
import json
import math
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import export
import plans

BLENDER_SCRIPT = plans.ROOT / "scripts" / "measure_blend.py"
UPSTREAM_PX_PER_UNIT = 100  # upstream const.PIXEL_TO_3D_SCALE (export_blend.py runs in Blender; can't import it)
MESH_PARTS = ("wall_vertical", "wall_horizontal", "room", "floor")  # what upstream builds meshes from on Linux
MARK = "MEASURE_HITS "

Point = tuple[float, float]


def _points(nested: list) -> Iterator[list]:
    for v in nested:
        if v and isinstance(v[0], (int, float)):
            yield v
        else:
            yield from _points(v)


def px_origin(floor: plans.Floor) -> Point:
    """Work-PNG px at the model's (0, 0): upstream flips Y, and export_blend moves the bbox min to the origin."""
    data = floor.work / floor.name / "ftb" / "Data" / "0"
    pts = [p for part in MESH_PARTS for p in _points(json.loads((data / f"{part}_verts.txt").read_text()))]
    if not pts:
        raise SystemExit(f"no upstream vertex data in {data}")
    return min(p[0] for p in pts) * UPSTREAM_PX_PER_UNIT, max(p[1] for p in pts) * UPSTREAM_PX_PER_UNIT


def to_world(px: Point, origin: Point, mpp: float) -> Point:
    """Work-PNG px -> world XY metres in the scaled export."""
    return (px[0] - origin[0]) * mpp, (origin[1] - px[1]) * mpp


def measure(floor: plans.Floor, p1: Point, p2: Point, blender: str) -> tuple[float, list[float], list[float]]:
    """Span in metres between the first wall hits from the p1-p2 midpoint towards p1 and towards p2."""
    path = floor.outdir / f"{floor.name}_report.json"
    if not path.is_file():
        raise SystemExit(f"{path} missing — measure needs a scaled export (calibration in plans.yaml + make models)")
    report = json.loads(path.read_text())
    mpp, z = report["metres_per_px"], report["extent_m"][2] / 2
    origin = px_origin(floor)
    (x1, y1), (x2, y2) = to_world(p1, origin, mpp), to_world(p2, origin, mpp)
    mid, d = [(x1 + x2) / 2, (y1 + y2) / 2, z], [x2 - x1, y2 - y1, 0.0]
    blend = floor.outdir / f"{floor.name}.blend"
    cmd = [blender, "-noaudio", "--factory-startup", "--disable-autoexec", "--background", str(blend)]
    cmd += ["--python-exit-code", "1", "--python", str(BLENDER_SCRIPT), "--", "--origin", *map(str, mid)]
    cmd += ["--dir", *(str(-v) for v in d), "--dir", *map(str, d)]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    lines = [line for line in out.splitlines() if line.startswith(MARK)]
    if len(lines) != 1:
        raise SystemExit(f"measure_blend printed {len(lines)} result lines:\n{out[-2000:]}")
    hits = json.loads(lines[0].removeprefix(MARK))
    for side, hit in zip(("p1", "p2"), hits, strict=True):
        if hit is None:
            raise SystemExit(f"no wall hit on the {side} side")
    return math.dist(hits[0][:2], hits[1][:2]), hits[0], hits[1]


def check(span: float, expect: float) -> float:
    """Diff % of span vs the printed value; exits non-zero beyond export.TOLERANCE."""
    diff = (span - expect) / expect
    if abs(diff) > export.TOLERANCE:
        raise SystemExit(f"FAIL: {span:.3f} m vs expected {expect} m, diff {100 * diff:+.1f} %")
    return 100 * diff


def point(s: str) -> Point:
    x, y = s.split(",")
    return float(x), float(y)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("floor")
    p.add_argument("--p1", type=point, required=True, help="work-PNG px X,Y near one wall")
    p.add_argument("--p2", type=point, required=True, help="work-PNG px X,Y near the opposite wall")
    p.add_argument("--expect", type=float, help="printed metres; exit non-zero beyond ±3 %%")
    p.add_argument("--plans", type=Path, default=plans.ROOT / "plans.yaml")
    p.add_argument("--blender", default=shutil.which("blender") or "blender")
    a = p.parse_args()
    blender = shutil.which(a.blender)
    if blender is None:
        raise SystemExit(f"blender not found: {a.blender}")
    floor = next((f for f in plans.load(a.plans) if f.name == a.floor), None)
    if floor is None:
        raise SystemExit(f"no floor {a.floor!r} in {a.plans}")
    try:
        span, h1, h2 = measure(floor, a.p1, a.p2, os.path.realpath(blender))
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"Blender measure failed for {floor.name}:\n{e.stdout[-2000:]}\n{e.stderr[-2000:]}") from e
    hit = "({:.3f}, {:.3f})".format
    print(f"{floor.name}: span {span:.3f} m; wall hits p1-side {hit(*h1[:2])}, p2-side {hit(*h2[:2])}")
    if a.expect is not None:
        print(f"  expect {a.expect} m: diff {check(span, a.expect):+.1f} % OK")


if __name__ == "__main__":
    main()
