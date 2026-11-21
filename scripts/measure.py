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


def px_origin(floor: plans.Floor, raw_extent: list[float]) -> Point:
    """Work-PNG px at the model's (0, 0): upstream flips Y, and export_blend moves the bbox min to the origin.

    Holds only if upstream applied no rotation/scale and the .blend was built from this very vertex
    data (raw_extent: the report's extent_raw_units), so both are checked.
    """
    data = floor.work / floor.name / "ftb" / "Data" / "0"
    t = json.loads((data / "transform.txt").read_text())
    if t["rotation"] != [0, 0, 0] or t["scale"] != [1, 1, 1]:
        raise SystemExit(
            f"{data}/transform.txt: rotation {t['rotation']}, scale {t['scale']} — px mapping assumes none"
        )
    pts = [p for part in MESH_PARTS for p in _points(json.loads((data / f"{part}_verts.txt").read_text()))]
    if not pts:
        raise SystemExit(f"no upstream vertex data in {data}")
    lo = [min(p[i] for p in pts) for i in (0, 1)]
    hi = [max(p[i] for p in pts) for i in (0, 1)]
    extent = [h - lo_ for h, lo_ in zip(hi, lo, strict=True)]
    if any(abs(e - r) > 1e-3 for e, r in zip(extent, raw_extent[:2], strict=True)):
        raise SystemExit(
            f"vertex data extent {extent} != report extent_raw_units {raw_extent[:2]}: "
            "ftb data and .blend are from different runs — make models"
        )
    return lo[0] * UPSTREAM_PX_PER_UNIT, hi[1] * UPSTREAM_PX_PER_UNIT


def to_world(px: Point, origin: Point, mpp: float) -> Point:
    """Work-PNG px -> world XY metres in the scaled export."""
    return (px[0] - origin[0]) * mpp, (origin[1] - px[1]) * mpp


def to_px(world: Point, origin: Point, mpp: float) -> Point:
    """World XY metres -> work-PNG px (inverse of to_world)."""
    return origin[0] + world[0] / mpp, origin[1] - world[1] / mpp


def scaled_report(floor: plans.Floor) -> dict:
    """The floor's export report, refusing anything but a scaled export at the current calibration."""
    path = floor.outdir / f"{export.stem(floor)}_report.json"
    if not path.is_file():
        raise SystemExit(f"{path} missing — measure needs a scaled export (calibration in plans.yaml + make models)")
    report = json.loads(path.read_text())
    if not report["scaled"] or floor.calibration is None:
        raise SystemExit(
            f"{path} is unscaled — measure needs a scaled export (calibration in plans.yaml + make models)"
        )
    if not math.isclose(report["metres_per_px"], floor.calibration.metres_per_px, rel_tol=1e-9):
        raise SystemExit(f"{path}: metres_per_px {report['metres_per_px']} != plans.yaml calibration — make models")
    return report


def measure(floor: plans.Floor, p1: Point, p2: Point, blender: str) -> tuple[float, Point, Point]:
    """Span in metres between the first wall hits from the p1-p2 midpoint towards p1 and towards p2.

    p1/p2 sit on the inner wall faces, so each hit must land near its point; returns the hits in work-PNG px.
    """
    if p1 == p2:
        raise SystemExit(f"p1 and p2 are the same point {p1}")
    report = scaled_report(floor)
    mpp, z = report["metres_per_px"], report["extent_m"][2] / 2
    origin = px_origin(floor, report["extent_raw_units"])
    (x1, y1), (x2, y2) = to_world(p1, origin, mpp), to_world(p2, origin, mpp)
    mid, d = [(x1 + x2) / 2, (y1 + y2) / 2, z], [x2 - x1, y2 - y1, 0.0]
    blend = floor.outdir / f"{export.stem(floor)}.blend"
    cmd = [blender, "-noaudio", "--factory-startup", "--disable-autoexec", "--background", str(blend)]
    cmd += ["--python-exit-code", "1", "--python", str(BLENDER_SCRIPT), "--", "--origin", *map(str, mid)]
    cmd += ["--dir", *(str(-v) for v in d), "--dir", *map(str, d)]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    lines = [line for line in out.splitlines() if line.startswith(MARK)]
    if len(lines) != 1:
        raise SystemExit(f"measure_blend printed {len(lines)} result lines:\n{out[-2000:]}")
    hits = json.loads(lines[0].removeprefix(MARK))
    tol = export.TOLERANCE * math.dist((x1, y1), (x2, y2))
    for side, hit, p in zip(("p1", "p2"), hits, ((x1, y1), (x2, y2)), strict=True):
        if hit is None:
            raise SystemExit(f"no wall hit on the {side} side")
        got, want = math.dist(hit[:2], mid[:2]), math.dist(p, mid[:2])
        if abs(got - want) > tol:  # a far wall, or the midpoint inside a wall (ray_cast hits back faces too)
            raise SystemExit(
                f"{side}-side wall at {got:.3f} m from midpoint, expected ≈ {want:.3f} m (missing or undetected "
                f"wall); hit at work-PNG px {_fmt(to_px(hit[:2], origin, mpp))}"
            )
    return math.dist(hits[0][:2], hits[1][:2]), to_px(hits[0][:2], origin, mpp), to_px(hits[1][:2], origin, mpp)


def _fmt(p: Point) -> str:
    return f"({p[0]:.1f}, {p[1]:.1f})"


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
    p.add_argument("--p1", type=point, required=True, help="work-PNG px X,Y on the inner wall face")
    p.add_argument("--p2", type=point, required=True, help="work-PNG px X,Y on the opposite inner wall face")
    p.add_argument("--expect", type=float, help="printed metres; exit non-zero beyond ±3 %%")
    p.add_argument("--plans", type=Path, default=plans.ROOT / "plans.yaml")
    p.add_argument("--blender", default=shutil.which("blender") or "blender")
    a = p.parse_args()
    if a.expect is not None and a.expect <= 0:
        p.error(f"--expect must be > 0 metres, got {a.expect}")
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
    print(f"{floor.name}: span {span:.3f} m; wall hits at work-PNG px p1-side {_fmt(h1)}, p2-side {_fmt(h2)}")
    if a.expect is not None:
        print(f"  expect {a.expect} m: diff {check(span, a.expect):+.1f} % OK")


if __name__ == "__main__":
    main()
