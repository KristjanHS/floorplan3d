# SPDX-License-Identifier: GPL-3.0-or-later
# Glue that imports the GPL-3.0 upstream library, so it is GPL-3.0 too (the rest of scripts/ is MIT).
# Runs only as a sandboxed subprocess (make probe, tests/test_ftb_probe.py), never imported.
"""Per-stage extent of upstream's wall pipeline for one floor.ini, and the first stage that loses the drawing.

S0 ink · S1 wall_filter · S2 precise_boxes · S3 outer_contours (the filter mask) · S4 boxes kept by it ·
S5 Data/0/wall_vertical_verts · S6 the export report's extent_raw_units (all meshes: walls, floor, rooms).
"""

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# -I drops the script dir from sys.path; ftb_generate (shims) and upstream are imported by path.
sys.path[:0] = [str(HERE), str(HERE.parent / "vendor" / "FloorplanToBlender3d")]

import cv2
import numpy as np
from FloorplanToBlenderLib import IO, calculate, const, detect, floorplan

import ftb_generate  # noqa: F401 — applies its cv2/numpy shims
import plans

DROP_RATIO = 0.90  # a stage whose x or y extent is < 0.90 × S0's lost part of the drawing
FACE_TOL = 0.03  # S1 face counts when the ray hits within ±3 % of the p1–p2 span (+ DILATE_PX) of the point
# wall_filter dilates walls by this many px, so its faces sit that far into the room by construction.
DILATE_PX = const.WALL_FILTER_DILATE_ITERATIONS * (const.WALL_FILTER_KERNEL_SIZE[0] // 2)
# Later stages must stay within this of S1's hit: passing through a wall moves it ≥ ink + 2·DILATE_PX.
SHIFT_PX = 2 * DILATE_PX
GEOMETRY = ("S1", "S2", "S4", "S5", "S6")  # S0 is the reference; S3 is a filter mask, not walls
FACE_STAGES = ("S1", "S2", "S4", "S5")
RAY_STEP = 0.5

Point = tuple[float, float]
Segment = tuple[Point, Point]


def parse_target(s: str, scale: tuple[float, float] | None = None) -> dict:
    """`[id=]x1,y1:x2,y2` in work-PNG px; scale = (basis, downscale) when picked on another work PNG."""
    tid, _, coords = s.rpartition("=")
    p1, p2 = (tuple(float(v) for v in half.split(",")) for half in coords.split(":"))
    if len(p1) != 2 or len(p2) != 2 or p1 == p2:
        raise SystemExit(f"bad target {s!r}: want [id=]x1,y1:x2,y2 with distinct points")
    if scale:
        p1, p2 = plans.at_downscale(p1, *scale), plans.at_downscale(p2, *scale)
    return {"id": tid or coords, "p1": p1, "p2": p2}


def mask_bbox(mask: np.ndarray) -> list[float] | None:
    ys, xs = np.nonzero(mask)
    return None if xs.size == 0 else [float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())]


def points_bbox(pts: list[Point]) -> list[float] | None:
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def box_segments(boxes: list[np.ndarray]) -> list[Segment]:
    segs = []
    for box in boxes:
        pts = [(float(p[0][0]), float(p[0][1])) for p in box]
        segs += [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]
    return segs


def verts_segments(walls: list) -> list[Segment]:
    """wall_vertical_verts: per wall, per edge a quad [cur ground, cur top, next ground, next top], px / 100."""
    k = const.PIXEL_TO_3D_SCALE
    return [((q[0][0] * k, q[0][1] * k), (q[2][0] * k, q[2][1] * k)) for wall in walls for q in wall]


def raster_hit(mask: np.ndarray, origin: Point, d: Point) -> float | None:
    """Distance along unit d from origin to the first non-zero pixel; None if the ray leaves the image."""
    h, w = mask.shape
    for i in range(int(2 * math.hypot(w, h) / RAY_STEP)):
        t = i * RAY_STEP
        x, y = round(origin[0] + t * d[0]), round(origin[1] + t * d[1])
        if not (0 <= x < w and 0 <= y < h):
            return None
        if mask[y, x]:
            return t
    return None


def segment_hit(segs: list[Segment], origin: Point, d: Point) -> float | None:
    """Distance along unit d from origin to the nearest segment crossing; None if none."""
    best = None
    for (ax, ay), (bx, by) in segs:
        ex, ey = bx - ax, by - ay
        den = d[0] * ey - d[1] * ex
        if abs(den) < 1e-12:
            continue
        wx, wy = ax - origin[0], ay - origin[1]
        t = (wx * ey - wy * ex) / den
        u = (wx * d[1] - wy * d[0]) / den
        if t >= 0 and -1e-9 <= u <= 1 + 1e-9 and (best is None or t < best):
            best = t
    return best


def faces(target: dict, hit, ref: list[dict] | None = None) -> list[dict]:
    """Per side (p1, p2): ray from the p1–p2 midpoint towards that point; offset = hit − point (− = early).

    Without ref (S1) the face must sit within FACE_TOL·span + DILATE_PX of the point; with ref, within SHIFT_PX
    of ref's present hit (a face p misplaced by a few px still localizes where it is lost).
    """
    (x1, y1), (x2, y2) = target["p1"], target["p2"]
    span = math.dist(target["p1"], target["p2"])
    mid = ((x1 + x2) / 2, (y1 + y2) / 2)
    out = []
    for i, (px, py) in enumerate((target["p1"], target["p2"])):
        d = ((px - mid[0]) / (span / 2), (py - mid[1]) / (span / 2))
        t = hit(mid, d)
        offset = None if t is None else round(t - span / 2, 2)
        r = ref[i] if ref and ref[i]["present"] else None
        centre, tol = (0.0, FACE_TOL * span + DILATE_PX) if r is None else (r["offset_px"], SHIFT_PX)
        out.append({"offset_px": offset, "present": offset is not None and abs(offset - centre) <= tol})
    return out


def probe(ini: Path, report: Path | None, targets: list[dict]) -> dict:
    fp = floorplan.new_floorplan(str(ini))
    image: str = fp.image_path  # pyright: ignore[reportAttributeAccessIssue] — set from the ini at runtime
    _, gray, _ = IO.read_image(image, fp)  # convert's path: same denoise, no rescale

    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    wall_img = detect.wall_filter(gray)
    boxes, _ = detect.precise_boxes(wall_img)
    contour, _ = detect.outer_contours(gray)
    kept = calculate.remove_walls_not_in_contour(boxes, contour)

    bboxes: dict[str, list[float] | None] = {
        "S0": mask_bbox(ink),
        "S1": mask_bbox(wall_img),
        "S2": points_bbox([pt for s in box_segments(boxes) for pt in s]),
        "S3": points_bbox([(float(p[0][0]), float(p[0][1])) for p in contour]),
        "S4": points_bbox([pt for s in box_segments(kept) for pt in s]),
    }
    s2, s4 = box_segments(boxes), box_segments(kept)
    hits = {
        "S1": lambda o, d: raster_hit(wall_img, o, d),
        "S2": lambda o, d: segment_hit(s2, o, d),
        "S4": lambda o, d: segment_hit(s4, o, d),
    }
    notes = []
    verts = ini.parent / const.BASE_PATH / "0" / f"{const.WALL_VERTICAL_VERTS}{const.SAVE_DATA_FORMAT}"
    if verts.is_file():
        s5 = verts_segments(json.loads(verts.read_text()))
        bboxes["S5"] = points_bbox([pt for s in s5 for pt in s])
        hits["S5"] = lambda o, d, s=s5: segment_hit(s, o, d)
    else:
        notes.append(f"S5 skipped: {verts} missing")

    extents = {k: None if b is None else [b[2] - b[0], b[3] - b[1]] for k, b in bboxes.items()}
    if report is not None and report.is_file():
        if verts.is_file() and report.stat().st_mtime < verts.stat().st_mtime:
            notes.append(f"S6 skipped: {report} is older than this run's vertex data")
        else:
            raw = json.loads(report.read_text())["extent_raw_units"]
            extents["S6"] = [raw[0] * const.PIXEL_TO_3D_SCALE, raw[1] * const.PIXEL_TO_3D_SCALE]
    else:
        notes.append("S6 skipped: no export report")

    ref = extents["S0"]
    if ref is None or min(ref) <= 0:
        raise SystemExit(f"{image}: no ink to probe")
    stages = {
        k: {
            "bbox": bboxes.get(k),
            "extent": e,
            "ratio": None if e is None else [round(e[i] / ref[i], 4) for i in (0, 1)],
        }
        for k, e in extents.items()
    }
    for t in targets:
        t["faces"] = {"S1": faces(t, hits["S1"])}
        t["faces"] |= {k: faces(t, hits[k], t["faces"]["S1"]) for k in FACE_STAGES[1:] if k in hits}

    verdict, reason = None, None
    for k in GEOMETRY:
        if k not in stages:
            continue
        ratio = stages[k]["ratio"]
        lost = [t["id"] for t in targets if k in t["faces"] and not all(f["present"] for f in t["faces"][k])]
        if ratio is None or min(ratio) < DROP_RATIO:
            verdict, reason = k, "no walls left" if ratio is None else f"extent ratio {ratio} < {DROP_RATIO}"
        elif lost:
            verdict, reason = k, f"target face lost: {', '.join(lost)}"
        if verdict:
            break
    return {"image": image, "stages": stages, "targets": targets, "verdict": verdict, "reason": reason, "notes": notes}


def table(res: dict) -> str:
    names = {"S0": "ink", "S1": "wall_filter", "S2": "precise_boxes", "S3": "outer_contour", "S4": "contour kept",
             "S5": "wall verts", "S6": "report extent (all meshes)"}  # fmt: skip
    lines = [f"{'stage':<31} {'extent px':>17}  ratio x / y"]
    for k, s in res["stages"].items():
        e = "-" if s["extent"] is None else f"{s['extent'][0]:7.1f} × {s['extent'][1]:7.1f}"
        r = "-" if s["ratio"] is None else f"{s['ratio'][0]:.3f} / {s['ratio'][1]:.3f}"
        lines.append(f"{k} {names[k]:<28} {e:>17}  {r}")
    for t in res["targets"]:
        cells = []
        for k, fs in t["faces"].items():
            cells.append(f"{k} " + "/".join(("y" if f["present"] else "n") + f"({f['offset_px']})" for f in fs))
        lines.append(f"target {t['id']}: " + "  ".join(cells))
    lines += res["notes"]
    lines.append(f"verdict: {res['verdict'] or 'none'}" + (f" — {res['reason']}" if res["reason"] else ""))
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("ini", type=Path)
    p.add_argument("--report", type=Path, help="export report json (S6)")
    p.add_argument("--target", action="append", default=[], help="[id=]x1,y1:x2,y2 work-PNG px (repeatable)")
    p.add_argument("--basis", type=float, help="downscale the targets were picked at (needs --meta)")
    p.add_argument("--meta", type=Path, help="preprocess meta json holding the current work PNG's downscale")
    p.add_argument("--out", type=Path, help="write the result json here")
    a = p.parse_args()
    scale = None
    if a.basis is not None:
        if not 0 < a.basis <= 1:
            p.error(f"--basis is a preprocess downscale in (0, 1], got {a.basis}")
        if a.meta is None or not a.meta.is_file():
            p.error(f"--basis needs --meta with the work PNG's downscale, got {a.meta}")
        scale = (a.basis, float(json.loads(a.meta.read_text())["downscale"]))
    res = probe(a.ini, a.report, [parse_target(s, scale) for s in a.target])
    if a.out:
        a.out.write_text(json.dumps(res, indent=2) + "\n")
    print(table(res))


if __name__ == "__main__":
    main()
