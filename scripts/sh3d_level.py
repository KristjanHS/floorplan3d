"""Split a SweetHome3D level at a straight line into a new level, or raise the walls beyond a line.

Text-level edits of sh3d_src/<home>/Home.xml (every untouched byte kept). A line is `x=<v>` or `y=<v>`; `--side +`
picks the part with larger coordinates, `-` the smaller. A point within `--tol` cm of the line counts as on it.

split: walls with both ends on the side (or on the line) move to the new level, crossing ones are cut at the line and
their side half moves; rooms are clipped at the line; dimension lines, labels and polylines move only when every point
is on the side; pieces move by their centre. Joins: a cut half keeps the join of the end it contains, refs to a cut
wall are repointed to the half on the referrer's level, any other join across the two levels is dropped.

raise-walls: walls of a level on the side (crossing ones cut first, the two halves joined) get height = top - the
level's elevation, `heightAtEnd` dropped."""

import argparse
import math
import re
import sys
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import sh3d
from sh3d_xmltext import element_re, fmt_num, open_attrs, set_attrs

PIECE_TAGS = ("pieceOfFurniture", "doorOrWindow", "light", "furnitureGroup")
REF_ATTRS = ("wallAtStart", "wallAtEnd")
STAY, MOVE, ON, CUT = "stay", "move", "on-line", "cut"
Point = tuple[float, float]


@dataclass(frozen=True)
class Line:
    axis: int  # 0 = x, 1 = y
    value: float
    sign: int  # +1: the side is coord > value
    tol: float

    @staticmethod
    def parse(spec: str, side: str, tol: float) -> "Line":
        m = re.fullmatch(r"\s*([xy])\s*=\s*(\S+)\s*", spec)
        if m is None or side not in "+-" or len(side) != 1:
            raise sh3d.Sh3dError(f"line {spec!r} side {side!r}: want LINE=x=<v>|y=<v> SIDE=+|-")
        try:
            value = float(m.group(2))
        except ValueError as e:
            raise sh3d.Sh3dError(f"line {spec!r}: {m.group(2)!r} is not a number") from e
        return Line("xy".index(m.group(1)), value, 1 if side == "+" else -1, tol)

    def dist(self, p: Point) -> float:
        """Signed distance, > 0 on the side."""
        return (p[self.axis] - self.value) * self.sign

    def where(self, p: Point) -> str:
        d = self.dist(p)
        return MOVE if d > self.tol else STAY if d < -self.tol else ON

    def classify(self, points: list[Point]) -> str:
        """Every point on the side or the line (one strictly on the side) -> MOVE; points strictly on both sides -> CUT;
        else STAY (all on the line stays)."""
        sides = {self.where(p) for p in points}
        if {MOVE, STAY} <= sides:
            return CUT
        return MOVE if MOVE in sides else STAY

    def cut_point(self, a: Point, b: Point) -> tuple[Point, float]:
        t = (self.value - a[self.axis]) / (b[self.axis] - a[self.axis])
        p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        return (p[0], self.value) if self.axis else (self.value, p[1]), t


@dataclass
class Report:
    rows: list[tuple[str, str, str]] = field(default_factory=list)  # (kind, id, decision)

    def add(self, kind: str, eid: str | None, decision: str) -> None:
        self.rows.append((kind, eid or "?", decision))

    def table(self) -> list[str]:
        width = max((len(r[1]) for r in self.rows), default=2)
        return [f"{k:<14} {i:<{width}}  {d}" for k, i, d in self.rows]


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


def level_ids(text: str) -> dict[str, str]:
    """Level name -> id; a duplicated name is refused."""
    names: dict[str, str] = {}
    for m in element_re("level").finditer(text):
        a = open_attrs(m.group(0))
        if a.get("name") in names:
            raise sh3d.Sh3dError(f"two levels named {a.get('name')!r}")
        names[a.get("name", "")] = a.get("id", "")
    return names


def level_id(text: str, name: str) -> str:
    names = level_ids(text)
    if name not in names:
        raise sh3d.Sh3dError(f"no level named {name!r} (levels: {', '.join(names) or 'none'})")
    return names[name]


def level_elevation(text: str, lid: str) -> float:
    for m in element_re("level").finditer(text):
        a = open_attrs(m.group(0))
        if a.get("id") == lid:
            return float(a["elevation"])
    raise sh3d.Sh3dError(f"no level id {lid!r}")


@dataclass
class Part:
    """A wall on the level after deciding: the whole wall, or one half of a cut one."""

    id: str
    level: str
    half: str  # MOVE (ends on the side) or STAY
    start: Point
    end: Point
    refs: dict[str, str | None]  # wallAtStart / wallAtEnd
    cut_end: str | None = None  # the ref at the cut: the halves' own join (or none), never repointed
    heights: dict[str, str | None] = field(default_factory=dict)  # interpolated height / heightAtEnd of a half

    def xml(self, original: str) -> str:
        (x1, y1), (x2, y2) = self.start, self.end
        now = open_attrs(original)
        keys, values = ("xStart", "yStart", "xEnd", "yEnd"), (x1, y1, x2, y2)
        coords = {k: fmt_num(v) for k, v in zip(keys, values, strict=True) if float(now[k]) != v}  # unchanged: kept
        return set_attrs(original, id=self.id, level=self.level, **self.refs, **coords, **self.heights)


def wall_points(a: dict[str, str]) -> tuple[Point, Point]:
    return (float(a["xStart"]), float(a["yStart"])), (float(a["xEnd"]), float(a["yEnd"]))


def cut_wall(a: dict[str, str], line: Line, side_level: str, stay_level: str) -> tuple[Part, Part]:
    """The two halves of a crossing straight wall, in wall direction; the stay half keeps the id, the side half gets a
    new one. The cut ends carry no join; the outer ends keep theirs."""
    if float(a.get("arcExtent") or 0) != 0:
        raise sh3d.Sh3dError(f"wall {a.get('id')} is an arc crossing the line — cut it by hand first")
    s, e = wall_points(a)
    p, t = line.cut_point(s, e)
    first = Part("", "", "", s, p, {"wallAtStart": a.get("wallAtStart"), "wallAtEnd": None}, "wallAtEnd")
    second = Part("", "", "", p, e, {"wallAtStart": None, "wallAtEnd": a.get("wallAtEnd")}, "wallAtStart")
    if "height" in a and "heightAtEnd" in a:
        h0, h1 = float(a["height"]), float(a["heightAtEnd"])
        hp = fmt_num(h0 + (h1 - h0) * t)
        first.heights, second.heights = {"heightAtEnd": hp}, {"height": hp}
    side, stay = (first, second) if line.dist(s) > 0 else (second, first)
    side.id, side.level, side.half = new_id("wall"), side_level, MOVE
    stay.id, stay.level, stay.half = a["id"], stay_level, STAY
    return first, second


def rewrite_walls(
    text: str, lid: str, line: Line, report: Report, side_level: str, on_side: dict[str, str | None], join_halves: bool
) -> str:
    """Decide every wall on level `lid`, cut the crossing ones, then fix joins among the level's walls (+ the new ones):
    a ref to a cut wall goes to the half on the referrer's level (same level: the half on the referrer's end's side),
    a ref whose target ended on another level is dropped. `on_side` = extra attributes of a wall (or half) that ends
    up on the side."""
    plans: list[tuple[re.Match[str], dict[str, str], list[Part]]] = []
    final: dict[str, str] = {}  # wall id -> level after
    halves: dict[str, dict[str, str]] = {}  # cut wall id -> {MOVE: id, STAY: id}
    for m in element_re("wall").finditer(text):
        a = open_attrs(m.group(0))
        if a.get("level") != lid:
            continue
        s, e = wall_points(a)
        decision = line.classify([s, e])
        report.add("wall", a.get("id"), decision)
        if decision == CUT:
            first, second = cut_wall(a, line, side_level, lid)
            parts = [first, second]
            halves[a["id"]] = {p.half: p.id for p in parts}
            if join_halves:
                first.refs["wallAtEnd"], second.refs["wallAtStart"] = second.id, first.id
        else:
            level = side_level if decision == MOVE else lid
            parts = [Part(a["id"], level, decision, s, e, {k: a.get(k) for k in REF_ATTRS})]
        final |= {p.id: p.level for p in parts}
        plans.append((m, a, parts))

    def target(ref: str | None, level: str, end: Point) -> str | None:
        if ref is None or ref not in final:
            return ref  # outside this level: untouched
        if ref in halves:
            split = side_level != lid
            ref = halves[ref][MOVE if (level == side_level if split else line.dist(end) > 0) else STAY]
        return ref if final[ref] == level else None

    out, at = [], 0
    for m, a, parts in plans:
        out.append(text[at : m.start()])
        at = m.end()
        for p in parts:
            for k, end in (("wallAtStart", p.start), ("wallAtEnd", p.end)):
                if k != p.cut_end:
                    p.refs[k] = target(p.refs[k], p.level, end)
            extra = on_side if p.half == MOVE else {}
            if len(parts) == 2:
                out.append(set_attrs(p.xml(m.group(0)), **extra))
                continue
            changed = {k: v for k, v in p.refs.items() if v != a.get(k)}
            if p.level != lid:
                changed["level"] = p.level
            out.append(set_attrs(m.group(0), **changed, **extra) if changed or extra else m.group(0))
    return "".join([*out, text[at:]])


def clip(points: list[Point], line: Line, keep_side: bool) -> list[Point]:
    """Sutherland-Hodgman against the half plane (side if keep_side, else the other one), cut exactly at the line."""
    inside = (lambda p: line.dist(p) >= 0) if keep_side else (lambda p: line.dist(p) <= 0)
    out: list[Point] = []
    for i, cur in enumerate(points):
        prev = points[i - 1]
        if inside(cur):
            if not inside(prev):
                out.append(line.cut_point(prev, cur)[0])
            out.append(cur)
        elif inside(prev):
            out.append(line.cut_point(prev, cur)[0])
    return [p for i, p in enumerate(out) if p != out[i - 1]] if len(out) > 1 else out


POINT_RE = re.compile(r"""([ \t]*)<point x=(['"])([^'"]*)\2 y=\2([^'"]*)\2\s*/>\n?""")


def room_with_points(room_xml: str, points: list[Point], originals: tuple[dict[float, str], dict[float, str]]) -> str:
    """Replace the room's <point> block; original vertices keep their exact x/y strings."""
    found = list(POINT_RE.finditer(room_xml))
    indent, q = found[0].group(1), found[0].group(2)
    block = ""
    for p in points:
        x, y = (originals[i].get(p[i]) or fmt_num(p[i]) for i in (0, 1))
        block += f"{indent}<point x={q}{x}{q} y={q}{y}{q}/>\n"
    return room_xml[: found[0].start()] + block + room_xml[found[-1].end() :]


def split_rooms(text: str, lid: str, nid: str, line: Line, report: Report) -> str:
    def fix(m: re.Match[str]) -> str:
        xml, a = m.group(0), open_attrs(m.group(0))
        if a.get("level") != lid:
            return xml
        found = POINT_RE.findall(xml)
        originals = ({float(x): x for _, _, x, _ in found}, {float(y): y for _, _, _, y in found})
        pts = [(float(x), float(y)) for _, _, x, y in POINT_RE.findall(xml)]
        decision = line.classify(pts)
        report.add("room", a.get("id"), decision)
        if decision == MOVE:
            return set_attrs(xml, level=nid)
        if decision == STAY:
            return xml
        signs = [d > 0 for d in map(line.dist, pts) if d != 0]
        if sum(a != b for a, b in zip(signs, signs[1:] + signs[:1], strict=True)) > 2:
            raise sh3d.Sh3dError(f"room {a.get('id')} crosses the line more than once — split it by hand first")
        stay, side = clip(pts, line, False), clip(pts, line, True)
        moved = set_attrs(room_with_points(xml, side, originals), id=new_id("room"), level=nid)
        return room_with_points(xml, stay, originals) + moved

    return element_re("room").sub(fix, text)


def move_by_points(text: str, tag: str, lid: str, nid: str, line: Line, report: Report) -> str:
    """Dimension lines, labels, polylines: move only when every point is on the side (or the line, one strictly)."""

    def fix(m: re.Match[str]) -> str:
        xml, a = m.group(0), open_attrs(m.group(0))
        if a.get("level") != lid:
            return xml
        if tag == "dimensionLine":
            pts = list(wall_points(a))
        elif tag == "label":
            pts = [(float(a["x"]), float(a["y"]))]
        else:
            pts = [(float(x), float(y)) for _, _, x, y in POINT_RE.findall(xml)]
        decision = line.classify(pts)
        report.add(tag, a.get("id"), MOVE if decision == MOVE else STAY + (" (crosses)" if decision == CUT else ""))
        return set_attrs(xml, level=nid) if decision == MOVE else xml

    return element_re(tag).sub(fix, text)


def move_pieces(text: str, lid: str, nid: str, line: Line, report: Report) -> str:
    """Top-level pieces (a group with everything in it) move when their centre is on the side."""
    rx = re.compile(rf"[ \t]*<({'|'.join(PIECE_TAGS)})\b[^>]*?(?:/>|(?<!/)>.*?</\1>)[ \t]*\n?", re.DOTALL)
    level_attr = re.compile(rf"""(\slevel=(['"])){re.escape(lid)}\2""")

    def fix(m: re.Match[str]) -> str:
        xml, a = m.group(0), open_attrs(m.group(0))
        if m.group(1) == "furnitureGroup" and "<furnitureGroup" in xml[1:]:
            raise sh3d.Sh3dError(f"nested furnitureGroup in {a.get('id')} — ungroup it first")
        if a.get("level") != lid:
            return xml
        moves = line.dist((float(a["x"]), float(a["y"]))) > 0
        report.add(m.group(1), a.get("id"), MOVE if moves else STAY)
        return level_attr.sub(lambda r: f"{r.group(1)}{nid}{r.group(2)}", xml) if moves else xml

    return rx.sub(fix, text)


def split_text(
    text: str, from_name: str, new_name: str, line: Line, elevation: float, floor_thickness: float, height: float
) -> tuple[str, Report, str]:
    """The whole split on Home.xml text; returns the text, the classification and the new level's id."""
    lid = level_id(text, from_name)
    if new_name in level_ids(text):
        raise sh3d.Sh3dError(f"level {new_name!r} already exists")
    nid = new_id("level")
    report = Report()
    text = rewrite_walls(text, lid, line, report, nid, {}, join_halves=False)
    text = split_rooms(text, lid, nid, line, report)
    for tag in ("polyline", "dimensionLine", "label"):
        text = move_by_points(text, tag, lid, nid, line, report)
    text = move_pieces(text, lid, nid, line, report)
    last = list(element_re("level").finditer(text))[-1]
    indent = re.match(r"[ \t]*", last.group(0)).group(0)  # type: ignore[union-attr]
    q = "'" if "'" in last.group(0) else '"'
    attrs = {
        "id": nid,
        "name": new_name,
        "elevation": str(elevation),
        "floorThickness": str(floor_thickness),
        "height": str(height),
        "elevationIndex": "0",
    }
    level = indent + "<level " + " ".join(f"{k}={q}{v}{q}" for k, v in attrs.items()) + "/>\n"
    nl = "" if last.group(0).endswith("\n") else "\n"
    return text[: last.end()] + nl + level + text[last.end() :], report, nid


def raise_text(text: str, level_name: str, line: Line, top: float) -> tuple[str, Report]:
    lid = level_id(text, level_name)
    height = fmt_num(top - level_elevation(text, lid))
    report = Report()
    text = rewrite_walls(text, lid, line, report, lid, {"height": height, "heightAtEnd": None}, True)
    return text, report


def level_counts(text: str) -> Counter[tuple[str, str]]:
    """(level id, tag) -> count, for the summary."""
    c: Counter[tuple[str, str]] = Counter()
    for tag in ("wall", "room", "dimensionLine", "label", "polyline", *PIECE_TAGS):
        for m in element_re(tag).finditer(text):
            c[(open_attrs(m.group(0)).get("level", ""), tag)] += 1
    return c


def summary(text: str, ids: list[str]) -> list[str]:
    names = {v: k for k, v in level_ids(text).items()}
    c = level_counts(text)
    tags = ("wall", "room", "dimensionLine", "label", "polyline", *PIECE_TAGS)
    return [
        f"{names.get(i, i)}: " + ", ".join(f"{c[(i, t)]} {t}" for t in tags if c[(i, t)]) or f"{names.get(i, i)}: empty"
        for i in ids
    ]


def apply(roots: sh3d.Roots, name: str, edit: Callable[[str], tuple[str, Report, list[str]]], dry_run: bool) -> None:
    """sync (auto-pull a GUI save) -> refuse a dirty tree -> edit -> write -> validate, restoring on any error."""
    sh3d.sync(roots, name)
    sh3d.require_clean(roots.src, name)
    tree = roots.tree(name)
    xml = tree / "Home.xml"
    before = xml.read_bytes()
    bom = b"\xef\xbb\xbf" if before.startswith(b"\xef\xbb\xbf") else b""
    text, report, ids = edit(before[len(bom) :].decode("utf-8"))
    print("\n".join(report.table()))
    if dry_run:
        print(f"dry run: {Counter(d.split()[0] for _, _, d in report.rows)}")
        print("\n".join(summary(text, ids)))
        return
    dtd = sh3d.load_dtd(roots.dtd)
    xml.write_bytes(bom + text.encode("utf-8"))
    errors = sh3d.validate_errors(tree, dtd)
    if errors:
        xml.write_bytes(before)
        raise sh3d.Sh3dError(f"{len(errors)} error(s), Home.xml restored\n" + "\n".join(errors))
    print("\n".join(summary(text, ids)))
    print(f"commit {tree}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sh3d_level", description=__doc__)
    parser.add_argument("--src", type=Path, default=sh3d.ROOT / "sh3d_src")
    parser.add_argument("--work", type=Path, default=sh3d.ROOT / "work")
    parser.add_argument("--homes", type=Path, default=sh3d.ROOT / "homes")
    parser.add_argument("--backups", type=Path, default=sh3d.ROOT / "backups")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for cmd in ("split", "raise-walls"):
        p = sub.add_parser(cmd)
        p.add_argument("name")
        p.add_argument("--line", required=True, help="x=<v> or y=<v>")
        p.add_argument("--side", required=True, choices=["+", "-"])
        p.add_argument("--tol", type=float, default=5.0)
        p.add_argument("--dry-run", action="store_true")
    sp = sub.choices["split"]
    sp.add_argument("--from", dest="from_level", required=True)
    sp.add_argument("--new", required=True)
    sp.add_argument("--elevation", type=float, required=True)
    sp.add_argument("--floor-thickness", type=float, required=True)
    sp.add_argument("--height", type=float, required=True)
    rp = sub.choices["raise-walls"]
    rp.add_argument("--level", required=True)
    rp.add_argument("--top", type=float, required=True)
    args = parser.parse_args(argv)
    try:
        sh3d.check_name(args.name)
        if not all(math.isfinite(v) for v in (args.tol, *[getattr(args, k, 0.0) for k in ("elevation", "top")])):
            raise sh3d.Sh3dError("non-finite number")
        line = Line.parse(args.line, args.side, args.tol)
        dtd = args.work / "sh3d" / "SweetHome3D.dtd"
        roots = sh3d.Roots(homes=args.homes, src=args.src, backups=args.backups, work=args.work, dtd=dtd)

        def edit(text: str) -> tuple[str, Report, list[str]]:
            if args.cmd == "split":
                out, report, nid = split_text(
                    text, args.from_level, args.new, line, args.elevation, args.floor_thickness, args.height
                )
                return out, report, [level_id(out, args.from_level), nid]
            out, report = raise_text(text, args.level, line, args.top)
            return out, report, [level_id(out, args.level)]

        apply(roots, args.name, edit, args.dry_run)
    except sh3d.Sh3dError as e:
        print(f"sh3d_level: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
