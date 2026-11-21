"""Sweet Home 3D `.sh3d` (a ZIP) <-> an unpacked, git-tracked source tree. Format facts: NOTES.md § SweetHome3D format."""

import argparse
import codecs
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from collections.abc import Callable
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
# NOTES.md (f); pinned from the 7.6.1 source — test_numeric_attrs_match_source re-derives it.
NUMERIC_ATTRS = frozenset(
    [
        "angle",
        "arcExtent",
        "areaAngle",
        "areaXOffset",
        "areaYOffset",
        "ceilingShininess",
        "creationDate",
        "dashOffset",
        "depth",
        "depthInPlan",
        "diameter",
        "dropOnTopElevation",
        "elevation",
        "elevationEnd",
        "elevationIndex",
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
        "modelFlags",
        "modelSize",
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
        "photoHeight",
        "photoQuality",
        "photoWidth",
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
        "videoFrameRate",
        "videoQuality",
        "videoSpeed",
        "videoWidth",
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
NON_FINITE = frozenset({"NaN", "-NaN", "Infinity", "-Infinity"})
NUMBER_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
SRC_ZIP = ROOT / "work" / "sh3d-src" / f"SweetHome3D-{SH3D_VERSION_PINNED}-src.zip"
SRC_ZIP_URL = (
    "https://sourceforge.net/projects/sweethome3d/files/SweetHome3D-source/"
    f"SweetHome3D-{SH3D_VERSION_PINNED}-src/SweetHome3D-{SH3D_VERSION_PINNED}-src.zip/download"
)
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
    path = PurePosixPath(entry)
    if path.is_absolute() or "\\" in entry or ".." in path.parts or entry.rstrip("/") == META:
        raise Sh3dError(f"refusing unsafe zip entry name {entry!r}")


def require_clean(tree: Path, src: Path) -> None:
    target = (tree if tree.exists() else src).resolve()
    if not target.is_dir():
        raise Sh3dError(f"source root not found: {target}")
    inside = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True, check=False
    )
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise Sh3dError(f"{target} is not inside a git work tree — sh3d_src must be git-tracked")
    status = subprocess.run(
        ["git", "-C", str(target), "status", "--porcelain", "--", "."], capture_output=True, text=True, check=True
    )
    if status.stdout.strip():
        raise Sh3dError(f"{target} has uncommitted changes — commit or discard them first")


def pull(roots: Roots, name: str) -> None:
    sh3d = roots.sh3d(name)
    if not sh3d.is_file():
        raise Sh3dError(f"{sh3d} not found")
    tree = roots.tree(name)
    require_clean(tree, roots.src)
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
    return text


def build_dtd(src_zip: Path, out: Path) -> None:
    if not src_zip.is_file():
        raise Sh3dError(f"source zip not found: {src_zip} — fetch it: curl -sSL -o {src_zip} {SRC_ZIP_URL}")
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
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"push: ok {name} — commit sh3d_src/{name}/{META}; reopen in SH3D")


COMMANDS: dict[str, Callable[[Roots, str], None]] = {"pull": pull, "validate": validate, "push": push}


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
    sub.add_parser("dtd").add_argument("--src-zip", type=Path, default=SRC_ZIP)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    roots = Roots(homes=args.homes, src=args.src, backups=args.backups, work=args.work, dtd=args.dtd)
    try:
        if args.cmd == "dtd":
            build_dtd(args.src_zip, roots.dtd)
        else:
            check_name(args.name)
            COMMANDS[args.cmd](roots, args.name)
    except Sh3dError as e:
        print(f"sh3d: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
