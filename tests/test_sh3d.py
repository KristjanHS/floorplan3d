import json
import re
import subprocess
import zipfile
from pathlib import Path

import pytest

import sh3d

HOME_XML = b"""<?xml version='1.0' encoding='UTF-8'?>
<home version='7600' wallHeight='250'>
  <level id='level0' name='L0' elevation='0' floorThickness='12' height='250'/>
  <pieceOfFurniture id='p1' level='level0' name='Sofa' model='0' icon='1' x='100' y='200' angle='0'
    width='200' depth='90' height='80'/>
  <doorOrWindow id='d1' level='level0' name='Door' model='0' icon='1' x='0' y='50' width='90' depth='10'
    height='210'/>
  <wall id='w1' level='level0' xStart='0' yStart='0' xEnd='500' yEnd='0' thickness='10' height='250'/>
  <room id='r1' level='level0' name='Living'>
    <point x='0' y='0'/><point x='500' y='0'/><point x='500' y='400'/><point x='0' y='400'/>
  </room>
</home>
"""
MODEL = bytes(range(256)) * 4
ICON = b"\x89PNG fake icon" * 50
ENTRIES = [
    ("Home", b"\xac\xed java-serialized", zipfile.ZIP_STORED),
    ("0", MODEL, zipfile.ZIP_STORED),
    ("1", ICON, zipfile.ZIP_DEFLATED),
    ("Home.xml", HOME_XML, zipfile.ZIP_DEFLATED),
    ("ContentDigests", b"ContentDigests-Version: 1.0\n", zipfile.ZIP_DEFLATED),
]


def make_sh3d(path: Path, entries: list[tuple[str, bytes, int]] = ENTRIES) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, data, compress in entries:
            zf.writestr(zipfile.ZipInfo(name), data, compress_type=compress)


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd), *args], check=True)


@pytest.fixture
def roots(tmp_path: Path) -> sh3d.Roots:
    r = sh3d.Roots(
        homes=tmp_path / "homes",
        src=tmp_path / "src",
        backups=tmp_path / "backups",
        work=tmp_path / "work",
        dtd=tmp_path / "work" / "SweetHome3D.dtd",
    )
    r.src.mkdir()
    git(r.src, "init", "-q")
    git(r.src, "commit", "-q", "--allow-empty", "-m", "init")
    make_sh3d(r.sh3d("x"))
    return r


def run(roots: sh3d.Roots, *argv: str) -> int:
    flags = {"--homes": roots.homes, "--src": roots.src, "--backups": roots.backups, "--work": roots.work}
    flags["--dtd"] = roots.dtd
    return sh3d.main([s for flag, path in flags.items() for s in (flag, str(path))] + list(argv))


def test_pull_records_meta_and_writes_resources(roots: sh3d.Roots) -> None:
    assert run(roots, "pull", "x") == 0
    tree = roots.tree("x")
    meta = json.loads((tree / sh3d.META).read_text())
    assert meta["source_sha256"] == sh3d.sha256_file(roots.sh3d("x"))
    assert meta["sh3d_version_pinned"] == sh3d.SH3D_VERSION_PINNED
    assert meta["entries"] == [{"name": n, "compress_type": c} for n, _, c in ENTRIES]
    assert (tree / "0").read_bytes() == MODEL
    assert (tree / "1").read_bytes() == ICON
    assert (tree / "Home.xml").read_bytes() == HOME_XML
    assert not (tree / "ContentDigests").exists()
    assert (tree / "Home").exists() is not sh3d.DROP_HOME_ENTRY


def test_pull_preserves_sub_paths(roots: sh3d.Roots) -> None:
    make_sh3d(roots.sh3d("x"), [("Home.xml", HOME_XML, zipfile.ZIP_DEFLATED), ("3/model.obj", b"v 0 0 0\n", 8)])
    assert run(roots, "pull", "x") == 0
    assert (roots.tree("x") / "3" / "model.obj").read_bytes() == b"v 0 0 0\n"


def test_pull_refuses_dirty_src(roots: sh3d.Roots, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(roots, "pull", "x") == 0
    assert run(roots, "pull", "x") == 1  # pulled tree is untracked
    assert "uncommitted" in capsys.readouterr().err
    git(roots.src, "add", "-A")
    git(roots.src, "commit", "-q", "-m", "pull")
    assert run(roots, "pull", "x") == 0


def test_pull_refuses_non_git_src(roots: sh3d.Roots, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    r = sh3d.Roots(homes=roots.homes, src=plain, backups=roots.backups, work=roots.work, dtd=roots.dtd)
    assert run(r, "pull", "x") == 1
    assert "not inside a git work tree" in capsys.readouterr().err
    assert not (plain / "x").exists()


def test_pull_refuses_missing_sh3d(roots: sh3d.Roots, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(roots, "pull", "nope") == 1
    assert "not found" in capsys.readouterr().err


def test_pull_refuses_bad_name(roots: sh3d.Roots, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(roots, "pull", "../x") == 1
    assert "bad name" in capsys.readouterr().err


def test_pull_refuses_unsafe_entry(roots: sh3d.Roots, capsys: pytest.CaptureFixture[str]) -> None:
    make_sh3d(roots.sh3d("x"), [("Home.xml", HOME_XML, 8), ("../evil", b"x", 0)])
    assert run(roots, "pull", "x") == 1
    assert "unsafe zip entry" in capsys.readouterr().err
    assert not roots.tree("x").exists()


SYNTH_DTD = """<!ELEMENT home (level*, (pieceOfFurniture | doorOrWindow)*, wall*, room*)>
<!ATTLIST home version CDATA #IMPLIED wallHeight CDATA #IMPLIED>
<!ELEMENT level EMPTY>
<!ATTLIST level id ID #REQUIRED name CDATA #REQUIRED elevation CDATA #REQUIRED floorThickness CDATA #REQUIRED
  height CDATA #REQUIRED>
<!ENTITY % piece "id ID #REQUIRED level IDREF #IMPLIED name CDATA #REQUIRED model CDATA #IMPLIED
  icon CDATA #IMPLIED x CDATA #REQUIRED y CDATA #REQUIRED angle CDATA #IMPLIED width CDATA #REQUIRED
  depth CDATA #REQUIRED height CDATA #REQUIRED">
<!ELEMENT pieceOfFurniture EMPTY>
<!ATTLIST pieceOfFurniture %piece;>
<!ELEMENT doorOrWindow EMPTY>
<!ATTLIST doorOrWindow %piece;>
<!ELEMENT wall EMPTY>
<!ATTLIST wall id ID #REQUIRED level IDREF #IMPLIED xStart CDATA #REQUIRED yStart CDATA #REQUIRED
  xEnd CDATA #REQUIRED yEnd CDATA #REQUIRED thickness CDATA #REQUIRED height CDATA #IMPLIED>
<!ELEMENT room (point+)>
<!ATTLIST room id ID #REQUIRED level IDREF #IMPLIED name CDATA #IMPLIED>
<!ELEMENT point EMPTY>
<!ATTLIST point x CDATA #REQUIRED y CDATA #REQUIRED>
"""


@pytest.fixture
def pulled(roots: sh3d.Roots) -> Path:
    roots.dtd.parent.mkdir(parents=True, exist_ok=True)
    roots.dtd.write_text(SYNTH_DTD)
    assert run(roots, "pull", "x") == 0
    return roots.tree("x")


def edit_xml(tree: Path, old: bytes, new: bytes) -> None:
    xml = tree / "Home.xml"
    data = xml.read_bytes()
    assert old in data
    xml.write_bytes(data.replace(old, new, 1))


def test_validate_ok(roots: sh3d.Roots, pulled: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(roots, "validate", "x") == 0
    assert "validate: ok x" in capsys.readouterr().out


def test_validate_reports_dtd_violation(roots: sh3d.Roots, pulled: Path, capsys: pytest.CaptureFixture[str]) -> None:
    edit_xml(pulled, b"<wall ", b"<bogus/><wall ")
    assert run(roots, "validate", "x") == 1
    assert "DTD:" in capsys.readouterr().err


def test_validate_missing_resource(roots: sh3d.Roots, pulled: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (pulled / "1").unlink()
    assert run(roots, "validate", "x") == 1
    err = capsys.readouterr().err
    assert "icon='1'" in err and "missing resource" in err


def test_validate_collects_number_errors(roots: sh3d.Roots, pulled: Path, capsys: pytest.CaptureFixture[str]) -> None:
    edit_xml(pulled, b"x='100'", b"x='NaN'")
    edit_xml(pulled, b"width='90'", b"width=''")
    edit_xml(pulled, b"name='Living'", b"name='-Infinity'")  # ruled: any attribute, not only NUMERIC_ATTRS
    assert run(roots, "validate", "x") == 1
    err = capsys.readouterr().err
    assert "3 error(s)" in err
    assert "x='NaN'> non-finite" in err
    assert "width=''> not a number" in err
    assert "name='-Infinity'> non-finite" in err


def test_validate_malformed_xml(roots: sh3d.Roots, pulled: Path, capsys: pytest.CaptureFixture[str]) -> None:
    edit_xml(pulled, b"</room>", b"</rom>")
    assert run(roots, "validate", "x") == 1
    assert "malformed XML" in capsys.readouterr().err


def test_validate_missing_dtd(roots: sh3d.Roots, pulled: Path, capsys: pytest.CaptureFixture[str]) -> None:
    roots.dtd.unlink()
    assert run(roots, "validate", "x") == 1
    assert "DTD not found" in capsys.readouterr().err


FAKE_JAVA = """package com.eteks.sweethome3d.io;
/**
 * SAX handler for Sweet Home 3D XML stream. Read home should respect the following DTD:<pre>
 * &lt;!ELEMENT home (lightSourceMaterial*)>
 * &lt;!ATTLIST home
 *       renderer CADATA #IMPLIED
 *       endMarkSize CDATA "10";>
 *
 * &lt;!ELEMENT lightSourceMaterial EMPTY>
 * &lt;!ATTLIST lightSourceMaterial
 *       name #REQUIRED>
 * </pre>
 * with <code>home</code> as root element.
 */
"""


def write_src_zip(path: Path, java: str) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(sh3d.DTD_JAVA, java)


def test_dtd_extracts_and_patches(roots: sh3d.Roots, tmp_path: Path) -> None:
    src_zip = tmp_path / "src.zip"
    write_src_zip(src_zip, FAKE_JAVA)
    assert run(roots, "dtd", "--src-zip", str(src_zip)) == 0
    dtd = roots.dtd.read_text()
    assert dtd.startswith("<!ELEMENT home (lightSourceMaterial*)>\n<!ATTLIST home\n      renderer CDATA")
    assert "name CDATA #REQUIRED>" in dtd and '"10";' not in dtd


def test_dtd_missing_zip_names_curl(roots: sh3d.Roots, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(roots, "dtd", "--src-zip", str(tmp_path / "nope.zip")) == 1
    assert "curl -sSL -o" in capsys.readouterr().err
    assert not roots.dtd.exists()


def test_dtd_refuses_unloadable(roots: sh3d.Roots, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src_zip = tmp_path / "src.zip"
    write_src_zip(src_zip, FAKE_JAVA.replace("EMPTY>", "BROKEN>"))
    assert run(roots, "dtd", "--src-zip", str(src_zip)) == 1
    assert "does not load" in capsys.readouterr().err
    assert not roots.dtd.exists()


def test_numeric_attrs_match_source() -> None:
    if not sh3d.SRC_ZIP.is_file():
        pytest.skip(f"{sh3d.SRC_ZIP} absent")
    with zipfile.ZipFile(sh3d.SRC_ZIP) as zf:
        java = zf.read(sh3d.DTD_JAVA).decode("utf-8")
    pattern = r'parse(?:Optional)?(?:Float|Integer|Long|Decimal)\(\w+, *"(\w+)"'
    assert set(re.findall(pattern, java)) == sh3d.NUMERIC_ATTRS


def kept_entries() -> list[tuple[str, bytes, int]]:
    return [e for e in ENTRIES if e[0] not in sh3d.DROPPED]


def test_push_round_trip(roots: sh3d.Roots, pulled: Path, capsys: pytest.CaptureFixture[str]) -> None:
    original = roots.sh3d("x").read_bytes()
    assert run(roots, "push", "x") == 0
    assert "push: ok x" in capsys.readouterr().out
    with zipfile.ZipFile(roots.sh3d("x")) as zf:
        infos = zf.infolist()
        assert [(i.filename, i.compress_type) for i in infos] == [(n, c) for n, _, c in kept_entries()]
        assert [zf.read(i) for i in infos] == [d for _, d, _ in kept_entries()]
    assert ("Home" in [i.filename for i in infos]) is not sh3d.DROP_HOME_ENTRY
    (backup,) = roots.backups.iterdir()
    assert backup.read_bytes() == original
    meta = json.loads((pulled / sh3d.META).read_text())
    assert meta["source_sha256"] == sh3d.sha256_file(roots.sh3d("x"))
    backup.unlink()  # same-second second push would collide on the backup name
    assert run(roots, "push", "x") == 0  # no pull in between: meta sha tracks the pushed file


def test_push_refuses_gui_edit_since_pull(roots: sh3d.Roots, pulled: Path, capsys: pytest.CaptureFixture[str]) -> None:
    make_sh3d(roots.sh3d("x"), ENTRIES[1:])
    edited = roots.sh3d("x").read_bytes()
    assert run(roots, "push", "x") == 1
    assert "edited in GUI since pull" in capsys.readouterr().err
    assert roots.sh3d("x").read_bytes() == edited
    assert not roots.backups.exists()


def test_push_strips_bom(roots: sh3d.Roots, pulled: Path) -> None:
    (pulled / "Home.xml").write_bytes(b"\xef\xbb\xbf" + HOME_XML)
    assert run(roots, "push", "x") == 0
    with zipfile.ZipFile(roots.sh3d("x")) as zf:
        assert zf.read("Home.xml") == HOME_XML


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (b"</room>", b"</rom>", "malformed XML"),
        (b"encoding='UTF-8'", b"encoding='ISO-8859-1'", "only UTF-8"),
    ],
)
def test_push_refuses_bad_home_xml(
    roots: sh3d.Roots, pulled: Path, capsys: pytest.CaptureFixture[str], old: bytes, new: bytes, message: str
) -> None:
    original = roots.sh3d("x").read_bytes()
    edit_xml(pulled, old, new)
    assert run(roots, "push", "x") == 1
    assert message in capsys.readouterr().err
    assert roots.sh3d("x").read_bytes() == original
    assert not roots.backups.exists()


def test_push_refuses_unknown_src_file(roots: sh3d.Roots, pulled: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (pulled / "2").write_bytes(b"new texture")
    assert run(roots, "push", "x") == 1
    assert "unknown file(s)" in capsys.readouterr().err
