"""Sweet Home 3D `.sh3d` (a ZIP) <-> an unpacked, git-tracked source tree. Format facts: NOTES.md § SweetHome3D format."""

import argparse
import codecs
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from lxml import etree

ROOT = Path(__file__).resolve().parent.parent
HASH = "sha256"
NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
SH3D_VERSION_PINNED = "7.6.1"
DROP_HOME_ENTRY = False  # NOTES.md (a): Home.xml wins over Home in 7.6.1
DROPPED = frozenset({"ContentDigests", "Home"} if DROP_HOME_ENTRY else {"ContentDigests"})
META = ".sh3d_meta.json"
CONTENT_ATTRS = frozenset({"model", "icon", "planIcon", "image"})  # NOTES.md (e); + property value when type=CONTENT
# NOTES.md (f); pinned from the 7.6.1 source — test_numeric_attrs_match_source re-derives all three sets.
# NUMERIC: parse(Optional)Float/Decimal; INT: parseOptionalInteger/Long; COLOR: parseOptionalColor (hex).
NUMERIC_ATTRS = frozenset(
    [
        "angle",
        "arcExtent",
        "areaAngle",
        "areaXOffset",
        "areaYOffset",
        "ceilingShininess",
        "dashOffset",
        "depth",
        "depthInPlan",
        "diameter",
        "dropOnTopElevation",
        "elevation",
        "elevationEnd",
        "elevationStart",
        "endAngle",
        "endMarkSize",
        "fieldOfView",
        "floorShininess",
        "floorThickness",
        "fontSize",
        "grade",
        "height",
        "heightAtEnd",
        "heightInPlan",
        "latitude",
        "leftSideShininess",
        "longitude",
        "nameAngle",
        "nameXOffset",
        "nameYOffset",
        "northDirection",
        "offset",
        "paperBottomMargin",
        "paperHeight",
        "paperLeftMargin",
        "paperRightMargin",
        "paperTopMargin",
        "paperWidth",
        "pitch",
        "planScale",
        "power",
        "price",
        "rightSideShininess",
        "roll",
        "scale",
        "scaleDistance",
        "scaleDistanceXEnd",
        "scaleDistanceXStart",
        "scaleDistanceYEnd",
        "scaleDistanceYStart",
        "shininess",
        "startAngle",
        "subpartSizeUnderLight",
        "thickness",
        "valueAddedTaxPercentage",
        "videoSpeed",
        "wallDistance",
        "wallHeight",
        "wallLeft",
        "wallThickness",
        "wallTop",
        "wallWidth",
        "wallsAlpha",
        "width",
        "widthInPlan",
        "x",
        "xAxis",
        "xEnd",
        "xLower",
        "xOffset",
        "xOrigin",
        "xStart",
        "xUpper",
        "y",
        "yAxis",
        "yEnd",
        "yLower",
        "yOffset",
        "yOrigin",
        "yStart",
        "yUpper",
        "yaw",
        "z",
        "zLower",
        "zUpper",
    ]
)
INT_ATTRS = frozenset(
    [
        "creationDate",
        "elevationIndex",
        "modelFlags",
        "modelSize",
        "photoHeight",
        "photoQuality",
        "photoWidth",
        "videoFrameRate",
        "videoQuality",
        "videoWidth",
    ]
)
COLOR_ATTRS = frozenset(
    [
        "ceilingColor",
        "ceillingLightColor",
        "color",
        "floorColor",
        "groundColor",
        "leftSideColor",
        "lightColor",
        "outlineColor",
        "rightSideColor",
        "skyColor",
        "topColor",
    ]
)
NON_FINITE = frozenset({"NaN", "-NaN", "Infinity", "-Infinity"})
NUMBER_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
INT_RE = re.compile(r"[+-]?[0-9]+")  # fullmatch: Integer/Long.parseLong do not trim
COLOR_RE = re.compile(r"[0-9A-Fa-f]{1,8}")  # fullmatch: Long.parseLong(v, 16), %08X ARGB
SRC_ZIP = ROOT / "work" / "sh3d-src" / f"SweetHome3D-{SH3D_VERSION_PINNED}-src.zip"
SRC_ZIP_URL = (
    "https://sourceforge.net/projects/sweethome3d/files/SweetHome3D-source/"
    f"SweetHome3D-{SH3D_VERSION_PINNED}-src/SweetHome3D-{SH3D_VERSION_PINNED}-src.zip/download"
)
SRC_ZIP_SHA256 = "b82de76095b2b07fcab59637dc16bd8cd1c92e6208ed18b5e37a5d2a304beee6"
DTD_JAVA = f"SweetHome3D-{SH3D_VERSION_PINNED}-src/src/com/eteks/sweethome3d/io/HomeXMLHandler.java"
DTD_PATCHES = (  # NOTES.md: the shipped DTD fails lxml without these
    ("renderer CADATA", "renderer CDATA"),
    (
        "<!ATTLIST lightSourceMaterial\n      name #REQUIRED>",
        "<!ATTLIST lightSourceMaterial\n      name CDATA #REQUIRED>",
    ),
    ('endMarkSize CDATA "10";', 'endMarkSize CDATA "10"'),
)


class Sh3dError(Exception):
    pass


@dataclass(frozen=True)
class Roots:
    homes: Path
    src: Path
    backups: Path
    work: Path
    dtd: Path

    def sh3d(self, name: str) -> Path:
        return self.homes / f"{name}.sh3d"

    def tree(self, name: str) -> Path:
        return self.src / name


def sha256_file(path: Path) -> str:
    digest = hashlib.new(HASH)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise Sh3dError(f"bad name {name!r}: use letters, digits, '_' or '-'")


def check_entry_name(entry: str) -> None:
    path = PurePosixPath(entry)  # a leading "." refuses "..", META and .git/.GIT planting alike
    if path.is_absolute() or "\\" in entry or any(part.startswith(".") for part in path.parts):
        raise Sh3dError(f"refusing unsafe zip entry name {entry!r}")


def require_clean(src: Path, name: str) -> None:
    target = src.resolve()
    if not target.is_dir():
        raise Sh3dError(f"source root not found: {target}")
    inside = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, check=False
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise Sh3dError(f"{target} is not inside a git work tree — sh3d_src must be git-tracked")
    status = subprocess.run(
        ["git", "-C", str(target), "status", "--porcelain", "--", name], capture_output=True, text=True, check=True
    )
    if status.stdout.strip():
        raise Sh3dError(f"{target / name} has uncommitted changes — commit or discard them first")


def tree_shas(tree: Path) -> dict[str, str]:
    return {
        p.relative_to(tree).as_posix(): sha256_file(p)
        for p in sorted(tree.rglob("*"))
        if p.is_file() and p.name != META
    }


def pull(roots: Roots, name: str, force: bool = False) -> None:
    sh3d = roots.sh3d(name)
    if not sh3d.is_file():
        raise Sh3dError(f"{sh3d} not found")
    tree = roots.tree(name)
    require_clean(roots.src, name)
    if not force and (tree / META).is_file():
        recorded = json.loads((tree / META).read_text(encoding="utf-8")).get("files")  # absent in older meta
        if recorded is not None and recorded != tree_shas(tree):
            raise Sh3dError(f"{name} has edits not yet pushed — push first, or pull --force to discard them")
    source_sha = sha256_file(sh3d)
    with zipfile.ZipFile(sh3d) as zf:
        infos = zf.infolist()
        for info in infos:
            check_entry_name(info.filename)
        if tree.exists():
            shutil.rmtree(tree)
        tree.mkdir(parents=True)
        for info in infos:
            if info.is_dir() or info.filename in DROPPED:
                continue
            out = tree / info.filename
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(zf.read(info))
    meta = {
        "source_sha256": source_sha,
        "sh3d_version_pinned": SH3D_VERSION_PINNED,
        "entries": [{"name": i.filename, "compress_type": i.compress_type} for i in infos],
        "files": tree_shas(tree),
    }
    (tree / META).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"pull: ok {name} — {len(infos)} entries → {tree}")


def load_dtd(path: Path) -> etree.DTD:
    if not path.is_file():
        raise Sh3dError(f"DTD not found: {path} — run make sh3d-dtd")
    try:
        return etree.DTD(str(path))
    except etree.DTDParseError as e:
        raise Sh3dError(f"DTD {path} does not load: {e}") from e


def is_resource_ref(el: etree._Element, attr: str) -> bool:
    if attr in CONTENT_ATTRS:
        return True
    return el.tag == "property" and attr == "value" and el.get("type") == "CONTENT"


def resource_missing(tree: Path, ref: str) -> bool:
    if "://" in ref or ref.startswith("jar:"):
        return False
    path = PurePosixPath(ref)
    if path.is_absolute() or ".." in path.parts:
        return True
    return not (tree / ref).is_file()


def validate_errors(tree: Path, dtd: etree.DTD) -> list[str]:
    xml = tree / "Home.xml"
    if not xml.is_file():
        return [f"{xml} not found — pull first"]
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    try:
        root = etree.parse(str(xml), parser).getroot()
    except etree.XMLSyntaxError as e:
        return [f"Home.xml:{e.lineno}: malformed XML: {e.msg}"]
    errors: list[str] = []
    if not dtd.validate(root):
        errors += [f"Home.xml:{err.line}: DTD: {err.message}" for err in dtd.error_log]
    for el in root.iter(etree.Element):
        for attr, value in el.attrib.items():
            where = f"Home.xml:{el.sourceline}: <{el.tag} {attr}={value!r}>"
            if value in NON_FINITE:
                errors.append(f"{where} non-finite number")
            elif attr in NUMERIC_ATTRS and not NUMBER_RE.match(value):
                errors.append(f"{where} not a number")
            elif attr in INT_ATTRS and not INT_RE.fullmatch(value):
                errors.append(f"{where} not an integer")
            elif attr in COLOR_ATTRS and not COLOR_RE.fullmatch(value):
                errors.append(f"{where} not a hex colour")
            if is_resource_ref(el, attr) and resource_missing(tree, value):
                errors.append(f"{where} missing resource under {tree}")
    return errors


def validate(roots: Roots, name: str) -> None:
    errors = validate_errors(roots.tree(name), load_dtd(roots.dtd))
    if errors:
        raise Sh3dError(f"validate: {len(errors)} error(s) in {name}\n" + "\n".join(errors))
    print(f"validate: ok {name}")


def extract_dtd(java: str) -> str:
    m = re.search(r"<pre>\n(.*?)\n \* </pre>", java.replace("\r\n", "\n"), re.DOTALL)
    if not m:
        raise Sh3dError("no <pre> DTD javadoc in HomeXMLHandler.java")
    text = "\n".join(re.sub(r"^ \* ?", "", line) for line in m.group(1).split("\n")).replace("&lt;", "<") + "\n"
    for old, new in DTD_PATCHES:
        text = text.replace(old, new)
    return re.sub(r"\b(\w+)\?(?:\s*,\s*\1\?)+", deterministic_repeat, text)


def deterministic_repeat(m: re.Match[str]) -> str:
    tag, model = m.group(1), f"{m.group(1)}?"
    for _ in range(m.group(0).count("?") - 1):
        model = f"({tag}, {model})?"
    return model


def build_dtd(src_zip: Path, out: Path, sha256: str = SRC_ZIP_SHA256) -> None:
    if not src_zip.is_file():
        raise Sh3dError(f"source zip not found: {src_zip} — fetch it: curl -sSL -o {src_zip} {SRC_ZIP_URL}")
    if (got := sha256_file(src_zip)) != sha256:
        raise Sh3dError(f"{src_zip}: sha256 {got} != pinned {sha256} — refusing to read it")
    with zipfile.ZipFile(src_zip) as zf:
        try:
            java = zf.read(DTD_JAVA).decode("utf-8")
        except KeyError as e:
            raise Sh3dError(f"{DTD_JAVA} not in {src_zip}") from e
    text = extract_dtd(java)
    try:
        etree.DTD(io.BytesIO(text.encode("utf-8")))
    except etree.DTDParseError as e:
        raise Sh3dError(f"extracted DTD does not load: {e}") from e
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"dtd: ok → {out}")


def home_xml_bytes(path: Path) -> bytes:
    data = path.read_bytes().removeprefix(codecs.BOM_UTF8)
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise Sh3dError(f"{path} is not UTF-8: {e}") from e
    declared = re.match(rb"<\?xml[^>]*encoding=['\"]([^'\"]+)['\"]", data)
    if declared and declared.group(1).lower() not in (b"utf-8", b"utf8"):
        raise Sh3dError(f"{path} declares encoding {declared.group(1).decode()} — only UTF-8 is packed")
    return data


def push(roots: Roots, name: str) -> None:
    tree, sh3d = roots.tree(name), roots.sh3d(name)
    meta_path = tree / META
    if not meta_path.is_file():
        raise Sh3dError(f"{meta_path} not found — pull first")
    if not sh3d.is_file():
        raise Sh3dError(f"{sh3d} not found")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if sha256_file(sh3d) != meta["source_sha256"]:
        raise Sh3dError(f"{sh3d} edited in GUI since pull — run pull first")
    if meta["sh3d_version_pinned"] != SH3D_VERSION_PINNED:
        print(
            f"sh3d: warning: pulled under SH3D {meta['sh3d_version_pinned']}, pinned {SH3D_VERSION_PINNED}",
            file=sys.stderr,
        )
    errors = validate_errors(tree, load_dtd(roots.dtd))
    if errors:
        raise Sh3dError(f"push refused — validate: {len(errors)} error(s) in {name}\n" + "\n".join(errors))
    entries: list[dict[str, str | int]] = meta["entries"]
    kept = [(str(e["name"]), int(e["compress_type"])) for e in entries if e["name"] not in DROPPED]
    known = {n for n, _ in kept} | {META}
    unknown = sorted(
        rel for p in tree.rglob("*") if p.is_file() and (rel := p.relative_to(tree).as_posix()) not in known
    )
    if unknown:
        raise Sh3dError(f"unknown file(s) in {tree} — new resources are unsupported: {', '.join(unknown)}")
    payload: list[tuple[str, int, bytes]] = []
    for entry, compress in kept:
        check_entry_name(entry)
        if entry.endswith("/"):
            payload.append((entry, compress, b""))
        elif not (tree / entry).is_file():
            raise Sh3dError(f"{tree / entry} missing — entry listed in {META}")
        else:
            data = home_xml_bytes(tree / entry) if entry == "Home.xml" else (tree / entry).read_bytes()
            payload.append((entry, compress, data))
    backup = roots.backups / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.sh3d"
    if backup.exists():
        raise Sh3dError(f"backup {backup} exists — retry in a second")
    roots.backups.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sh3d, backup)
    tmp = roots.homes / f".{name}.sh3d.tmp"
    try:
        with zipfile.ZipFile(tmp, "w") as zf:
            for entry, compress, data in payload:
                zf.writestr(zipfile.ZipInfo(entry), data, compress_type=compress)
        os.replace(tmp, sh3d)
    finally:
        tmp.unlink(missing_ok=True)
    meta["source_sha256"] = sha256_file(sh3d)
    meta["files"] = tree_shas(tree)
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"push: ok {name} — commit sh3d_src/{name}/{META}; reopen in SH3D")


PIECE_TAGS = frozenset({"pieceOfFurniture", "light", "doorOrWindow"})
INSPECTED = PIECE_TAGS | {"home", "level", "wall", "room", "point", "furnitureGroup"}


def read_home(tree: Path) -> etree._Element:
    xml = tree / "Home.xml"
    if not xml.is_file():
        raise Sh3dError(f"{xml} not found — pull first")
    try:
        return etree.parse(str(xml), etree.XMLParser(resolve_entities=False, no_network=True)).getroot()
    except etree.XMLSyntaxError as e:
        raise Sh3dError(f"Home.xml:{e.lineno}: malformed XML: {e.msg}") from e


def num(value: str | None) -> str:
    return "-" if value is None else f"{float(value):g}"


def colour(value: str | None) -> str:
    return "-" if value is None else "#" + value[-6:].upper()  # %08X ARGB, NOTES.md (d)


def room_points(room: etree._Element) -> list[tuple[float, float]]:
    return [(float(p.get("x", "0")), float(p.get("y", "0"))) for p in room.iter("point")]


def shoelace_m2(points: list[tuple[float, float]]) -> float:
    pairs = zip(points, points[1:] + points[:1], strict=True)
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in pairs)) / 2 / 10_000


def flatten_pieces(parent: etree._Element, prefix: str = "") -> Iterable[tuple[str, etree._Element]]:
    for el in parent.iterchildren(etree.Element):
        if el.tag == "furnitureGroup":
            yield from flatten_pieces(el, f"{prefix}group/{el.get('id', '-')}/")
        elif el.tag in PIECE_TAGS:
            yield prefix + el.get("id", "-"), el


def table(title: str, header: list[str], rows: list[list[str]]) -> list[str]:
    widths = [max(len(r[i]) for r in [header, *rows]) for i in range(len(header))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip() for r in [header, *rows]]
    return [f"{title} ({len(rows)})", *lines, ""]


def inspect_lines(root: etree._Element) -> list[str]:
    home_wall_height = root.get("wallHeight")

    def wall_height(wall: etree._Element) -> str:
        if wall.get("height") is not None:
            return num(wall.get("height"))
        return "-" if home_wall_height is None else num(home_wall_height) + "*"

    levels = [
        [e.get("id", "-"), e.get("name", "-"), num(e.get("elevation")), num(e.get("height"))]
        for e in root.iter("level")
    ]
    walls = [
        [e.get("id", "-"), *(num(e.get(a)) for a in ("xStart", "yStart", "xEnd", "yEnd", "thickness")), wall_height(e)]
        + [f"{colour(e.get('leftSideColor'))}/{colour(e.get('rightSideColor'))}", e.get("level", "-")]
        for e in root.iter("wall")
    ]
    rooms = [
        [e.get("id", "-"), e.get("name", "-"), f"{shoelace_m2(room_points(e)):.2f}", e.get("level", "-")]
        for e in root.iter("room")
    ]
    pieces: dict[bool, list[list[str]]] = {False: [], True: []}
    for pid, e in flatten_pieces(root):
        angle = e.get("angle")
        pieces[e.tag == "doorOrWindow"].append(
            [pid, e.get("name", "-"), num(e.get("x")), num(e.get("y"))]
            + ["-" if angle is None else f"{math.degrees(float(angle)):.1f}"]
            + [num(e.get(a)) for a in ("width", "depth", "height")]
            + [e.get("level", "-")]
        )
    piece_header = ["id", "name", "x", "y", "angle°", "width", "depth", "height", "level"]
    unhandled = Counter(str(e.tag) for e in root.iter(etree.Element) if e.tag not in INSPECTED)
    return [
        *table("levels", ["id", "name", "elevation", "height"], levels),
        *table(
            "walls", ["id", "xStart", "yStart", "xEnd", "yEnd", "thickness", "height", "L/R colour", "level"], walls
        ),
        *table("rooms", ["id", "name", "area m²", "level"], rooms),
        *table("furniture", piece_header, pieces[False]),
        *table("doors/windows", piece_header, pieces[True]),
        "unhandled: " + (" ".join(f"{t}×{n}" for t, n in sorted(unhandled.items())) or "none"),
    ]


def inspect(roots: Roots, name: str) -> None:
    print("\n".join(inspect_lines(read_home(roots.tree(name)))))


def wall_corners(wall: etree._Element) -> list[tuple[float, float]]:
    x0, y0, x1, y1 = (float(wall.get(a, "0")) for a in ("xStart", "yStart", "xEnd", "yEnd"))
    length = math.hypot(x1 - x0, y1 - y0) or 1.0
    half = float(wall.get("thickness", "0")) / 2
    nx, ny = -(y1 - y0) / length * half, (x1 - x0) / length * half
    return [(x0 + nx, y0 + ny), (x1 + nx, y1 + ny), (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)]


def piece_corners(piece: etree._Element) -> list[tuple[float, float]]:
    x, y = float(piece.get("x", "0")), float(piece.get("y", "0"))
    hw, hd = float(piece.get("width", "0")) / 2, float(piece.get("depth", "0")) / 2
    c, s = math.cos(float(piece.get("angle", "0"))), math.sin(float(piece.get("angle", "0")))
    return [(x + dx * c - dy * s, y + dx * s + dy * c) for dx, dy in ((-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd))]


def preview(roots: Roots, name: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    from matplotlib.patches import Polygon

    root = read_home(roots.tree(name))
    level_names = {e.get("id"): e.get("name", "-") for e in root.iter("level")}
    level_ids: list[str | None] = list(level_names) or [None]
    fig, axes = plt.subplots(1, len(level_ids), figsize=(8 * len(level_ids), 8), squeeze=False)
    curved: list[str] = []
    for ax, level in zip(axes[0], level_ids, strict=True):

        def on_level(el: etree._Element, level: str | None = level) -> bool:
            return level is None or el.get("level") == level

        for room in filter(on_level, root.iter("room")):
            points = room_points(room)
            if points:
                ax.add_patch(Polygon(points, closed=True, facecolor="#cfe3f5", edgecolor="#7aa6cc", lw=0.5))
                cx, cy = sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)
                ax.text(cx, cy, room.get("name", ""), ha="center", va="center", fontsize=8)
        for wall in filter(on_level, root.iter("wall")):
            if wall.get("arcExtent") is not None:
                curved.append(wall.get("id", "-"))
            ax.add_patch(Polygon(wall_corners(wall), closed=True, facecolor="#444444", edgecolor="none"))
        for _, piece in flatten_pieces(root):
            if on_level(piece):
                door = piece.tag == "doorOrWindow"
                style = {"facecolor": "#f2b880" if door else "#c9c9c9", "edgecolor": "#8a5a2b" if door else "#666666"}
                ax.add_patch(Polygon(piece_corners(piece), closed=True, lw=0.5, **style))
                ax.text(
                    float(piece.get("x", "0")),
                    float(piece.get("y", "0")),
                    piece.get("name", ""),
                    ha="center",
                    va="center",
                    fontsize=6,
                )
        ax.set_title(level_names[level] if level else name)
        ax.set_xlabel("cm")
        ax.set_ylabel("cm")
        ax.set_aspect("equal")
        ax.autoscale_view()
        ax.invert_yaxis()  # SH3D y grows down
    out = roots.work / "sh3d" / name / "preview.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=120, bbox_inches="tight")
    plt.close(fig)
    print(f"preview: {out}")
    print("unhandled: " + (f"curved walls drawn straight: {' '.join(curved)}" if curved else "none"))


COMMANDS: dict[str, Callable[[Roots, str], None]] = {
    "pull": pull,
    "validate": validate,
    "push": push,
    "inspect": inspect,
    "preview": preview,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sh3d", description=__doc__)
    parser.add_argument("--homes", type=Path, default=ROOT / "homes")
    parser.add_argument("--src", type=Path, default=ROOT / "sh3d_src")
    parser.add_argument("--backups", type=Path, default=ROOT / "backups")
    parser.add_argument("--work", type=Path, default=ROOT / "work")
    parser.add_argument("--dtd", type=Path, default=ROOT / "work" / "sh3d" / "SweetHome3D.dtd")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for cmd in COMMANDS:
        sub.add_parser(cmd).add_argument("name")
    sub.choices["pull"].add_argument("--force", action="store_true", help="discard edits not yet pushed")
    dtd = sub.add_parser("dtd")
    dtd.add_argument("--src-zip", type=Path, default=SRC_ZIP)
    dtd.add_argument("--src-zip-sha256", default=SRC_ZIP_SHA256)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    roots = Roots(homes=args.homes, src=args.src, backups=args.backups, work=args.work, dtd=args.dtd)
    try:
        if args.cmd == "dtd":
            build_dtd(args.src_zip, roots.dtd, args.src_zip_sha256)
        else:
            check_name(args.name)
            if args.cmd == "pull":
                pull(roots, args.name, force=args.force)
            else:
                COMMANDS[args.cmd](roots, args.name)
    except Sh3dError as e:
        print(f"sh3d: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
