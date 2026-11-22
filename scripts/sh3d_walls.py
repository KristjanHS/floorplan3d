"""Auto walls <-> Home.xml: background px<->cm transform, traced walls, tagged write. Plan: docs/plans/2026-11-21-sh3d-auto-walls.md."""

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from lxml import etree

import sh3d
import walls as wl

AUTO_PROP = "floorplan3d.auto"  # Stage 5 tag; tagged walls are never ground truth
DARK = 128  # grey level below which a px counts as ink
INK_RADIUS_PX = 1  # a face sample hits if any px within this radius is ink

Point = tuple[float, float]


@dataclass(frozen=True)
class TracedWall:
    id: str
    level_id: str | None
    p1: Point  # cm
    p2: Point
    thickness: float  # cm


@dataclass(frozen=True)
class LevelBackground:
    level_id: str | None  # None: a home without levels
    image: Path
    scale: float  # plan cm per image px
    x_origin: float  # cm
    y_origin: float  # cm

    def px_to_cm(self, u: float, v: float) -> tuple[float, float]:
        # PlanComponent.paintBackgroundImage: translate(-origin) then scale(s)
        return u * self.scale - self.x_origin, v * self.scale - self.y_origin

    def cm_to_px(self, x: float, y: float) -> tuple[float, float]:
        return (x + self.x_origin) / self.scale, (y + self.y_origin) / self.scale


def _background(level_id: str | None, bg: etree._Element, tree: Path) -> LevelBackground:
    def f(attr: str, default: str | None = None) -> float:
        return float(bg.get(attr, default))  # pyright: ignore[reportArgumentType]

    dist = math.dist(
        (f("scaleDistanceXStart"), f("scaleDistanceYStart")), (f("scaleDistanceXEnd"), f("scaleDistanceYEnd"))
    )
    return LevelBackground(
        level_id, tree / bg.get("image", ""), f("scaleDistance") / dist, f("xOrigin", "0"), f("yOrigin", "0")
    )


def level_backgrounds(root: etree._Element, tree: Path) -> list[LevelBackground]:
    """Per level: its own backgroundImage, else the home's; levels with neither are skipped."""
    home_bg = root.find("backgroundImage")
    levels = root.findall("level")
    if not levels:
        return [] if home_bg is None else [_background(None, home_bg, tree)]
    out: list[LevelBackground] = []
    for level in levels:
        bg = level.find("backgroundImage")
        bg = home_bg if bg is None else bg
        if bg is not None:
            out.append(_background(level.get("id"), bg, tree))
    return out


def traced_walls(root: etree._Element) -> list[TracedWall]:
    out: list[TracedWall] = []
    for w in root.iter("wall"):
        if any(p.get("name") == AUTO_PROP for p in w.findall("property")):
            continue
        p1 = (float(w.get("xStart", "")), float(w.get("yStart", "")))
        p2 = (float(w.get("xEnd", "")), float(w.get("yEnd", "")))
        out.append(TracedWall(w.get("id", ""), w.get("level"), p1, p2, float(w.get("thickness", ""))))
    return out


def room_rings(root: etree._Element) -> list[tuple[str | None, list[Point]]]:
    return [
        (r.get("level"), [(float(p.get("x", "")), float(p.get("y", ""))) for p in r.findall("point")])
        for r in root.iter("room")
    ]


def faces_px(wall: TracedWall | wl.Wall, bg: LevelBackground) -> list[tuple[Point, Point]]:
    """Both wall faces (centre line offset +-thickness/2), in background image px."""
    (x1, y1), (x2, y2) = wall.p1, wall.p2
    length = math.dist(wall.p1, wall.p2) or 1.0
    nx, ny = -(y2 - y1) / length * wall.thickness / 2, (x2 - x1) / length * wall.thickness / 2
    return [(bg.cm_to_px(x1 + s * nx, y1 + s * ny), bg.cm_to_px(x2 + s * nx, y2 + s * ny)) for s in (1, -1)]


def ink_rate(gray: np.ndarray, faces: list[tuple[Point, Point]]) -> float:
    """Share of samples (1 px apart) along the faces that land on ink; off-image samples count as misses."""
    ink = cv2.dilate((gray < DARK).astype(np.uint8), np.ones((2 * INK_RADIUS_PX + 1,) * 2, np.uint8))
    h, w = gray.shape
    hits = total = 0
    for (u1, v1), (u2, v2) in faces:
        n = max(int(math.dist((u1, v1), (u2, v2))), 1)
        us = np.rint(np.linspace(u1, u2, n + 1)).astype(int)
        vs = np.rint(np.linspace(v1, v2, n + 1)).astype(int)
        inside = (us >= 0) & (us < w) & (vs >= 0) & (vs < h)
        total += len(us)
        hits += int(ink[vs[inside], us[inside]].sum())
    return hits / total if total else 0.0


def overlay(src: Path, work: Path, name: str) -> None:
    tree = src / name
    root = sh3d.read_home(tree)
    walls = traced_walls(root)
    traces = {sh3d.sha256_file(p): p.name for p in sorted(work.glob("*_trace.png"))}
    out_dir = work / "walls"
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, bg in enumerate(level_backgrounds(root, tree)):
        gray = cv2.imread(str(bg.image), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            raise sh3d.Sh3dError(f"{bg.image}: not a readable image")
        faces = [f for w in walls if w.level_id == bg.level_id for f in faces_px(w, bg)]
        canvas = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        for (u1, v1), (u2, v2) in faces:
            cv2.line(canvas, (round(u1), round(v1)), (round(u2), round(v2)), (0, 200, 0), 1)
        out = out_dir / f"{name}-L{i}.png"
        cv2.imwrite(str(out), canvas)
        image = traces.get(sh3d.sha256_file(bg.image), "no *_trace.png match")
        print(f"L{i}: {len(faces) // 2} walls, ink {ink_rate(gray, faces):.2f}, image = {image} -> {out}")


BLUE, GREEN, MAGENTA, ORANGE = (255, 0, 0), (0, 200, 0), (255, 0, 255), (0, 140, 255)  # BGR


def rooms(src: Path, work: Path, name: str) -> None:
    """Dry run, Home.xml untouched: walls from gaps between traced rooms, drawn per level for the eyeball."""
    tree = src / name
    root = sh3d.read_home(tree)
    traced, rings = traced_walls(root), room_rings(root)
    out_dir = work / "walls"
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, bg in enumerate(level_backgrounds(root, tree)):
        gray = cv2.imread(str(bg.image), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            raise sh3d.Sh3dError(f"{bg.image}: not a readable image")
        level_rooms = [r for lv, r in rings if lv == bg.level_id and len(r) >= 3]
        anchors = [wl.Wall(w.p1, w.p2, w.thickness) for w in traced if w.level_id == bg.level_id]
        gen, hol = wl.gap_walls(level_rooms, anchors), wl.holes(level_rooms, anchors)
        canvas = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        for ring in level_rooms:
            pts = np.rint([bg.cm_to_px(x, y) for x, y in ring]).astype(np.int32)
            cv2.polylines(canvas, [pts], True, BLUE, 1)
        for group, colour, width in ((anchors, GREEN, 1), (gen, MAGENTA, 1), (hol, ORANGE, 2)):
            for w in group:
                for (u1, v1), (u2, v2) in faces_px(w, bg):
                    cv2.line(canvas, (round(u1), round(v1)), (round(u2), round(v2)), colour, width)
        out = out_dir / f"{name}-L{i}-rooms.png"
        cv2.imwrite(str(out), canvas)
        span = f" {min(w.thickness for w in gen):.1f}-{max(w.thickness for w in gen):.1f} cm" if gen else ""
        widths = f" ({', '.join(f'{w.thickness:.0f}' for w in hol)} cm)" if hol else ""
        print(f"L{i}: {len(level_rooms)} rooms, {len(gen)} walls{span}, {len(hol)} holes{widths} -> {out}")


COMMANDS = {"overlay": overlay, "rooms": rooms}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sh3d_walls", description=__doc__)
    parser.add_argument("--src", type=Path, default=sh3d.ROOT / "sh3d_src")
    parser.add_argument("--work", type=Path, default=sh3d.ROOT / "work")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for cmd in COMMANDS:
        sub.add_parser(cmd).add_argument("name")
    args = parser.parse_args(argv)
    try:
        sh3d.check_name(args.name)
        COMMANDS[args.cmd](args.src, args.work, args.name)
    except sh3d.Sh3dError as e:
        print(f"sh3d_walls: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
