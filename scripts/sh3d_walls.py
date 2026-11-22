"""Auto walls <-> Home.xml: background px<->cm transform, traced walls, tagged write. Plan: docs/plans/2026-11-21-sh3d-auto-walls.md."""

import argparse
import codecs
import hashlib
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from lxml import etree

import sh3d
import walls as wl

AUTO_PROP = "floorplan3d.auto"  # Stage 5 tag, value = geometry sig; tagged walls are never ground truth
AUTO_ID = "auto-"
SIG_TOL_CM = 0.01  # SH3D stores floats: a resave may reprint a coordinate, an edit moves it further
WALL_RE = re.compile(r"[ \t]*<wall\b[^>]*?(?:/>|(?<!/)>.*?</wall>)[ \t]*\n?", re.DOTALL)
AUTO_PROP_RE = re.compile(rf"""\s*<property name=(['"]){re.escape(AUTO_PROP)}\1 value=(['"])([^'"]*)\2\s*/>""")
ATTR_RE = re.compile(r"""(\w+)=(['"])(.*?)\2""")
GEOMETRY = ("xStart", "yStart", "xEnd", "yEnd", "thickness")
WRITTEN_ATTRS = {"id", "level", *GEOMETRY, "height"}
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


def _num(v: float) -> str:
    out = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if out == "-0" else out


def _sig(w: wl.Wall, height: str) -> str:
    return ",".join(_num(v) for v in (*w.p1, *w.p2, w.thickness)) + (f",{_num(float(height))}" if height else "")


def _wall_id(level: str | None, sig: str) -> str:
    return AUTO_ID + hashlib.sha256(f"{level}|{sig}".encode()).hexdigest()[:12]


def _open_attrs(wall_xml: str) -> dict[str, str]:
    return {k: v for k, _, v in ATTR_RE.findall(wall_xml[: wall_xml.index(">")])}


def _unedited(wall_xml: str, prop: re.Match[str]) -> bool:
    """Exactly as written: same attributes and id, no added children, geometry + height within SIG_TOL_CM."""
    attrs = _open_attrs(wall_xml)
    if set(attrs) - WRITTEN_ATTRS or re.search(r"<(?!/?wall\b)", wall_xml.replace(prop.group(0), "", 1)):
        return False
    try:
        sig = [float(v) for v in prop.group(3).split(",")]
        now = [float(attrs[k]) for k in (*GEOMETRY, "height")[: len(sig)]]
    except (KeyError, ValueError):
        return False
    close = len(sig) in (5, 6) and all(abs(a - b) <= SIG_TOL_CM for a, b in zip(now, sig, strict=True))
    return close and attrs.get("id") == _wall_id(attrs.get("level"), prop.group(3))


def untag_edited(text: str) -> tuple[str, int]:
    """Tagged walls changed in the GUI (geometry, height, level, style, children): drop the tag, they become hand-traced."""
    n = 0

    def fix(m: re.Match[str]) -> str:
        nonlocal n
        prop = AUTO_PROP_RE.search(m.group(0))
        if prop is None or _unedited(m.group(0), prop):
            return m.group(0)
        n += 1
        return m.group(0).replace(prop.group(0), "", 1)

    return WALL_RE.sub(fix, text), n


def _wall_xml(level: str | None, w: wl.Wall, height: str) -> tuple[str, str]:
    sig = _sig(w, height)
    wid = _wall_id(level, sig)
    lv = f" level='{level}'" if level else ""
    (x1, y1), (x2, y2) = w.p1, w.p2
    coords = f"xStart='{_num(x1)}' yStart='{_num(y1)}' xEnd='{_num(x2)}' yEnd='{_num(y2)}'"
    h = f" height='{height}'" if height else ""
    return wid, (
        f"  <wall id='{wid}'{lv} {coords}{h} thickness='{_num(w.thickness)}'>\n"
        f"    <property name='{AUTO_PROP}' value='{sig}'/>\n  </wall>\n"
    )


def write_walls(text: str, generated: list[tuple[str | None, wl.Wall, str]]) -> str:
    """Home.xml text in, text out: drop tagged walls, insert (level, wall, height) after the last remaining wall (DTD
    order: walls precede rooms), clear refs to dropped ids not re-inserted. Every other byte is kept."""
    removed: set[str] = set()

    def drop(m: re.Match[str]) -> str:
        if AUTO_PROP_RE.search(m.group(0)) is None:
            return m.group(0)
        removed.add(_open_attrs(m.group(0))["id"])
        return ""

    text = WALL_RE.sub(drop, text)
    ids = {_open_attrs(m.group(0)).get("id") for m in WALL_RE.finditer(text)}  # an edited wall keeps its auto- id
    block, new_ids = "", set()
    for level, w, height in generated:
        wid, xml = _wall_xml(level, w, height)
        if wid not in ids and wid not in new_ids:
            new_ids.add(wid)
            block += xml
    gone = removed - new_ids
    text = re.sub(
        r"""\s(?:wallAtStart|wallAtEnd)=(['"])([^'"]*)\1""", lambda m: "" if m.group(2) in gone else m.group(0), text
    )
    last = None
    for last in WALL_RE.finditer(text):
        pass
    if last is not None:
        at = last.end()
    else:
        nxt = re.search(r"[ \t]*<(?:room|polyline|dimensionLine|label)\b|[ \t]*</home>", text)
        if nxt is None:
            raise sh3d.Sh3dError("Home.xml: no </home>")
        at = nxt.start()
    return text[:at] + block + text[at:]


def generate(root: etree._Element) -> tuple[list[tuple[str | None, wl.Wall, str]], dict[str | None, tuple[int, int]]]:
    """Room-gap walls per level, minus those duplicating a hand-traced wall; (level, wall, height) + per-level counts."""
    traced, rings = traced_walls(root), room_rings(root)
    levels = [(lv.get("id"), lv.get("height", "")) for lv in root.findall("level")]
    out: list[tuple[str | None, wl.Wall, str]] = []
    counts: dict[str | None, tuple[int, int]] = {}
    for level_id, height in levels or [(None, root.get("wallHeight", ""))]:
        level_rooms = [r for lv, r in rings if lv == level_id and len(r) >= 3]
        anchors = [wl.Wall(w.p1, w.p2, w.thickness) for w in traced if w.level_id == level_id]
        gen = wl.gap_walls(level_rooms, anchors)
        keep = [w for w in gen if not wl.matches(w, anchors)]
        out += [(level_id, w, height) for w in keep]
        counts[level_id] = (len(keep), len(gen) - len(keep))
    return out, counts


def write(src: Path, work: Path, name: str) -> None:
    """Room-gap walls into sh3d_src/<name>/Home.xml, tagged; re-run replaces only unedited tagged walls."""
    dtd = sh3d.load_dtd(work / "sh3d" / "SweetHome3D.dtd")
    sh3d.require_clean(src, name)
    tree = src / name
    xml = tree / "Home.xml"
    before = xml.read_bytes()
    bom = codecs.BOM_UTF8 if before.startswith(codecs.BOM_UTF8) else b""
    text, untagged = untag_edited(sh3d.home_xml_bytes(xml).decode())
    root = etree.fromstring(text.encode(), etree.XMLParser(resolve_entities=False, no_network=True))
    generated, counts = generate(root)
    xml.write_bytes(bom + write_walls(text, generated).encode())
    errors = sh3d.validate_errors(tree, dtd)
    if errors:
        xml.write_bytes(before)
        raise sh3d.Sh3dError(f"write: {len(errors)} error(s), Home.xml restored\n" + "\n".join(errors))
    for i, (n, skipped) in enumerate(counts.values()):
        print(f"L{i}: {n} walls written, {skipped} skipped (match a traced wall)")
    print(f"{untagged} edited auto walls kept as hand-traced")


COMMANDS = {"overlay": overlay, "rooms": rooms, "write": write}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sh3d_walls", description=__doc__)
    parser.add_argument("--src", type=Path, default=sh3d.ROOT / "sh3d_src")
    parser.add_argument("--work", type=Path, default=sh3d.ROOT / "work")
    parser.add_argument("--homes", type=Path, default=sh3d.ROOT / "homes")
    parser.add_argument("--backups", type=Path, default=sh3d.ROOT / "backups")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for cmd in COMMANDS:
        sub.add_parser(cmd).add_argument("name")
    args = parser.parse_args(argv)
    try:
        sh3d.check_name(args.name)
        dtd = args.work / "sh3d" / "SweetHome3D.dtd"
        sh3d.sync(sh3d.Roots(homes=args.homes, src=args.src, backups=args.backups, work=args.work, dtd=dtd), args.name)
        COMMANDS[args.cmd](args.src, args.work, args.name)
    except sh3d.Sh3dError as e:
        print(f"sh3d_walls: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
