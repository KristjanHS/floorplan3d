import json
import os
import re
import subprocess
import zipfile
from pathlib import Path

import pytest
from lxml import etree

import sh3d
import sh3d_window

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


def commit(cwd: Path) -> None:
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "--allow-empty", "-m", "c")


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


def test_pull_ignores_dirt_outside_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    r = sh3d.Roots(
        homes=tmp_path / "homes", src=repo / "src", backups=tmp_path / "b", work=tmp_path / "w", dtd=tmp_path
    )
    (r.src / "y").mkdir(parents=True)
    git(repo, "init", "-q")
    commit(repo)
    (r.src / "y" / "Home.xml").write_bytes(b"dirty sibling")
    (repo / "outside.txt").write_text("untracked outside src")
    make_sh3d(r.sh3d("newname"))
    assert run(r, "pull", "newname") == 0
    assert run(r, "pull", "newname") == 1  # dirt inside <name> (the untracked pull) still refuses
    assert "newname has uncommitted" in capsys.readouterr().err


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


@pytest.mark.parametrize("entry", ["../evil", ".git/config", "x/.GIT/HEAD"])
def test_pull_refuses_unsafe_entry(roots: sh3d.Roots, capsys: pytest.CaptureFixture[str], entry: str) -> None:
    make_sh3d(roots.sh3d("x"), [("Home.xml", HOME_XML, 8), (entry, b"x", 0)])
    assert run(roots, "pull", "x") == 1
    assert "unsafe zip entry" in capsys.readouterr().err
    assert not roots.tree("x").exists()


SYNTH_DTD = """<!ELEMENT home (level*, (pieceOfFurniture | doorOrWindow)*, wall*, room*)>
<!ATTLIST home version CDATA #IMPLIED wallHeight CDATA #IMPLIED selectedLevel CDATA #IMPLIED>
<!ELEMENT level EMPTY>
<!ATTLIST level id ID #REQUIRED name CDATA #REQUIRED elevation CDATA #REQUIRED floorThickness CDATA #REQUIRED
  height CDATA #REQUIRED elevationIndex CDATA #IMPLIED>
<!ENTITY % piece "id ID #REQUIRED level IDREF #IMPLIED name CDATA #REQUIRED model CDATA #IMPLIED
  icon CDATA #IMPLIED x CDATA #REQUIRED y CDATA #REQUIRED angle CDATA #IMPLIED width CDATA #REQUIRED
  depth CDATA #REQUIRED height CDATA #REQUIRED">
<!ELEMENT pieceOfFurniture EMPTY>
<!ATTLIST pieceOfFurniture %piece;>
<!ELEMENT doorOrWindow EMPTY>
<!ATTLIST doorOrWindow %piece;>
<!ELEMENT wall EMPTY>
<!ATTLIST wall id ID #REQUIRED level IDREF #IMPLIED xStart CDATA #REQUIRED yStart CDATA #REQUIRED
  xEnd CDATA #REQUIRED yEnd CDATA #REQUIRED thickness CDATA #REQUIRED height CDATA #IMPLIED
  leftSideColor CDATA #IMPLIED rightSideColor CDATA #IMPLIED>
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
    edit_xml(pulled, b"width='200'", b"width='1,5'")
    edit_xml(pulled, b"name='Living'", b"name='-Infinity'")  # ruled: any attribute, not only NUMERIC_ATTRS
    edit_xml(pulled, b"<level id='level0'", b"<level elevationIndex='1.5' id='level0'")
    edit_xml(pulled, b"thickness='10'", b"thickness='10' leftSideColor='#FF0000' rightSideColor='FF808080'")
    assert run(roots, "validate", "x") == 1
    err = capsys.readouterr().err
    assert "6 error(s)" in err
    assert "x='NaN'> non-finite" in err
    assert "width=''> not a number" in err
    assert "width='1,5'> not a number" in err
    assert "name='-Infinity'> non-finite" in err
    assert "elevationIndex='1.5'> not an integer" in err
    assert "leftSideColor='#FF0000'> not a hex colour" in err
    assert "rightSideColor" not in err


def test_validate_dangling_selected_level(roots: sh3d.Roots, pulled: Path, capsys: pytest.CaptureFixture[str]) -> None:
    edit_xml(pulled, b"<home version='7600'", b"<home selectedLevel='gone' version='7600'")
    assert run(roots, "validate", "x") == 1
    err = capsys.readouterr().err
    assert "1 error(s)" in err and "selectedLevel='gone'> no such <level id>" in err
    edit_xml(pulled, b"selectedLevel='gone'", b"selectedLevel='level0'")
    assert run(roots, "validate", "x") == 0


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
 * &lt;!ELEMENT lightSourceMaterial (texture?, texture?, texture?)>
 * &lt;!ATTLIST lightSourceMaterial
 *       name #REQUIRED>
 *
 * &lt;!ELEMENT texture EMPTY>
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
    assert run(roots, "dtd", "--src-zip", str(src_zip), "--src-zip-sha256", sh3d.sha256_file(src_zip)) == 0
    dtd = roots.dtd.read_text()
    assert dtd.startswith("<!ELEMENT home (lightSourceMaterial*)>\n<!ATTLIST home\n      renderer CDATA")
    assert "name CDATA #REQUIRED>" in dtd and '"10";' not in dtd
    loaded = etree.DTD(str(roots.dtd))
    for n, ok in ((0, True), (1, True), (3, True), (4, False)):  # `texture?` ×3 rewritten deterministic
        doc = f"<home><lightSourceMaterial name='a'>{'<texture/>' * n}</lightSourceMaterial></home>"
        assert loaded.validate(etree.fromstring(doc)) is ok, (n, loaded.error_log)


def test_dtd_missing_zip_names_curl(roots: sh3d.Roots, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(roots, "dtd", "--src-zip", str(tmp_path / "nope.zip")) == 1
    assert "curl -sSL -o" in capsys.readouterr().err
    assert not roots.dtd.exists()


def test_dtd_refuses_unloadable(roots: sh3d.Roots, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src_zip = tmp_path / "src.zip"
    write_src_zip(src_zip, FAKE_JAVA.replace("EMPTY>", "BROKEN>"))
    assert run(roots, "dtd", "--src-zip", str(src_zip), "--src-zip-sha256", sh3d.sha256_file(src_zip)) == 1
    assert "does not load" in capsys.readouterr().err
    assert not roots.dtd.exists()


def test_numeric_attrs_match_source() -> None:
    if not sh3d.SRC_ZIP.is_file():
        pytest.skip(f"{sh3d.SRC_ZIP} absent")
    with zipfile.ZipFile(sh3d.SRC_ZIP) as zf:
        java = zf.read(sh3d.DTD_JAVA).decode("utf-8")
    for kinds, attrs in (
        ("Float|Decimal", sh3d.NUMERIC_ATTRS),
        ("Integer|Long", sh3d.INT_ATTRS),
        ("Color", sh3d.COLOR_ATTRS),
    ):
        assert set(re.findall(rf'parse(?:Optional)?(?:{kinds})\(\w+, *"(\w+)"', java)) == attrs, kinds


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


def test_pull_refuses_unpushed_edits(roots: sh3d.Roots, pulled: Path, capsys: pytest.CaptureFixture[str]) -> None:
    commit(roots.src)
    edit_xml(pulled, b"x='100'", b"x='101'")
    commit(roots.src)
    assert run(roots, "pull", "x") == 1
    assert "x has edits not yet pushed" in capsys.readouterr().err
    assert b"x='101'" in (pulled / "Home.xml").read_bytes()
    meta = json.loads((pulled / sh3d.META).read_text())
    del meta["files"]  # older meta: no record, no refusal
    (pulled / sh3d.META).write_text(json.dumps(meta))
    commit(roots.src)
    assert run(roots, "pull", "x") == 0
    commit(roots.src)
    edit_xml(pulled, b"x='100'", b"x='101'")
    commit(roots.src)
    assert run(roots, "pull", "x", "--force") == 0
    assert (pulled / "Home.xml").read_bytes() == HOME_XML


def test_pull_after_push_keeps_edits(roots: sh3d.Roots, pulled: Path) -> None:
    commit(roots.src)
    edit_xml(pulled, b"x='100'", b"x='101'")
    assert run(roots, "push", "x") == 0
    commit(roots.src)
    assert run(roots, "pull", "x") == 0
    assert b"x='101'" in (pulled / "Home.xml").read_bytes()


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


def test_inspect_tables(roots: sh3d.Roots, pulled: Path, capsys: pytest.CaptureFixture[str]) -> None:
    edit_xml(pulled, b"<wall ", b"<wall id='w2' xStart='0' yStart='0' xEnd='0' yEnd='400' thickness='10'/><wall ")
    edit_xml(pulled, b"thickness='10' height='250'", b"thickness='10' height='240' leftSideColor='FF00FF80'")
    edit_xml(
        pulled,
        b"<doorOrWindow",
        b"<furnitureGroup id='g1' name='G'><pieceOfFurniture id='c1' name='Chair'/>"
        b"</furnitureGroup><label text='hi'/><label text='yo'/><doorOrWindow",
    )
    capsys.readouterr()
    assert run(roots, "inspect", "x") == 0
    lines = capsys.readouterr().out.splitlines()
    rows = {line.split()[0]: line.split() for line in lines if line and not line.startswith(("unhandled", "id "))}
    assert rows["w1"][6:8] == ["240", "#00FF80/-"]
    assert rows["w2"][6] == "250*"  # no height → home wallHeight
    assert rows["r1"][2] == "20.00"  # 500 × 400 cm
    assert rows["p1"][1] == "Sofa" and rows["p1"][4] == "0.0"
    assert "group/g1/c1" in rows
    assert "d1" in rows and "furniture (2)" in lines and "doors/windows (1)" in lines
    assert lines[-1] == "unhandled: label×2"


def test_preview_writes_png_under_work(roots: sh3d.Roots, pulled: Path, capsys: pytest.CaptureFixture[str]) -> None:
    edit_xml(pulled, b"thickness='10' height='250'", b"thickness='10' height='250' arcExtent='1.5'")
    before = {p: p.read_bytes() for p in roots.src.rglob("*") if p.is_file() and ".git" not in p.parts}
    assert run(roots, "preview", "x") == 0
    png = roots.work / "sh3d" / "x" / "preview.png"
    assert png.read_bytes().startswith(b"\x89PNG") and png.stat().st_size > 1024
    assert {p: p.read_bytes() for p in roots.src.rglob("*") if p.is_file() and ".git" not in p.parts} == before
    assert "unhandled: curved walls drawn straight: w1" in capsys.readouterr().out


REPO = Path(__file__).resolve().parents[1]


def make_n(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ | {"NAME": "wsl-hostname"}
    return subprocess.run(["make", "-n", "-C", str(REPO), *args], capture_output=True, text=True, env=env, check=False)


def test_make_sh3d_target_requires_name_on_command_line() -> None:
    r = make_n("inspect")
    assert r.returncode != 0 and "NAME" in r.stderr


def test_make_sh3d_target_refuses_shell_metachars_in_name() -> None:
    r = make_n("inspect", "NAME=x;id")
    assert r.returncode != 0 and "NAME='x;id'" in r.stderr


def test_make_sh3d_target_passes_name() -> None:
    r = make_n("inspect", "NAME=x")
    assert r.returncode == 0 and "scripts/sh3d.py --dtd work/sh3d/SweetHome3D.dtd inspect x" in r.stdout


def test_dtd_refuses_unpinned_zip(roots: sh3d.Roots, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src_zip = tmp_path / "src.zip"
    write_src_zip(src_zip, FAKE_JAVA)
    assert run(roots, "dtd", "--src-zip", str(src_zip)) == 1
    assert "!= pinned" in capsys.readouterr().err
    assert not roots.dtd.exists()


def test_gui_save_auto_pulls_and_commits(roots: sh3d.Roots, pulled: Path, capsys: pytest.CaptureFixture[str]) -> None:
    commit(roots.src)
    saved = [(n, HOME_XML.replace(b"x='100'", b"x='102'") if n == "Home.xml" else d, c) for n, d, c in ENTRIES]
    make_sh3d(roots.sh3d("x"), saved)
    assert run(roots, "validate", "x") == 0
    assert "auto-pull: x saved in SH3D" in capsys.readouterr().out
    assert b"x='102'" in (pulled / "Home.xml").read_bytes()
    status = subprocess.run(
        ["git", "-C", str(roots.src), "status", "--porcelain"], check=True, capture_output=True, text=True
    )
    assert status.stdout == ""
    assert run(roots, "validate", "x") == 0
    assert "auto-pull" not in capsys.readouterr().out  # in sync now: no second pull


def test_auto_pull_refuses_unpushed_edits(roots: sh3d.Roots, pulled: Path, capsys: pytest.CaptureFixture[str]) -> None:
    commit(roots.src)
    edit_xml(pulled, b"x='100'", b"x='101'")
    commit(roots.src)
    make_sh3d(roots.sh3d("x"), ENTRIES[1:])
    assert run(roots, "validate", "x") == 1
    assert "x has edits not yet pushed" in capsys.readouterr().err
    assert b"x='101'" in (pulled / "Home.xml").read_bytes()


def test_auto_pull_commit_rejected_is_sh3d_error(
    roots: sh3d.Roots, pulled: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit(roots.src)
    hooks = roots.src / ".git" / "hooks"
    (hooks / "pre-commit").write_text("#!/bin/sh\necho 'hook says no' >&2\nexit 1\n")
    (hooks / "pre-commit").chmod(0o755)
    git(roots.src, "config", "core.hooksPath", str(hooks))  # a global hooksPath would skip .git/hooks
    make_sh3d(roots.sh3d("x"), ENTRIES[1:])
    assert run(roots, "validate", "x") == 1
    err = capsys.readouterr().err
    assert err.startswith("sh3d: auto-pull: x pulled but git commit failed:") and "hook says no" in err
    assert f"git -C {roots.src.resolve()} commit -- x" in err and "Traceback" not in err


def test_auto_pull_refuses_older_meta(roots: sh3d.Roots, pulled: Path, capsys: pytest.CaptureFixture[str]) -> None:
    meta = json.loads((pulled / sh3d.META).read_text())
    del meta["files"]
    (pulled / sh3d.META).write_text(json.dumps(meta))
    edit_xml(pulled, b"x='100'", b"x='101'")  # unpushed edit pull could not see without "files"
    commit(roots.src)
    make_sh3d(roots.sh3d("x"), ENTRIES[1:])
    assert run(roots, "validate", "x") == 1
    assert "older meta — run make pull NAME=x explicitly" in capsys.readouterr().err
    assert b"x='101'" in (pulled / "Home.xml").read_bytes()


def corrupt_member(path: Path, member: str) -> None:
    """Garble a deflated member's data, central directory intact: ZipFile opens, the read fails mid-extraction."""
    with zipfile.ZipFile(path) as zf:
        info = zf.getinfo(member)
    data = bytearray(path.read_bytes())
    start = info.header_offset + 30 + len(info.filename) + len(info.extra)  # local header: 30 fixed bytes
    data[start + 2 : start + 12] = b"\xff" * 10
    path.write_bytes(bytes(data))


@pytest.mark.parametrize("damage", ["truncated", "garbled member"])
def test_auto_pull_unreadable_save_is_sh3d_error_tree_intact(
    roots: sh3d.Roots, pulled: Path, capsys: pytest.CaptureFixture[str], damage: str
) -> None:
    commit(roots.src)
    before = {p.relative_to(pulled): p.read_bytes() for p in sorted(pulled.rglob("*")) if p.is_file()}
    path = roots.sh3d("x")
    if damage == "truncated":  # SH3D still writing: no central directory yet
        path.write_bytes(path.read_bytes()[: path.stat().st_size // 2])
    else:  # fails after other entries are written: an in-place extraction has wiped the old tree by then
        corrupt_member(path, "Home.xml")
    assert run(roots, "validate", "x") == 1
    assert "is not a readable zip" in capsys.readouterr().err
    assert {p.relative_to(pulled): p.read_bytes() for p in sorted(pulled.rglob("*")) if p.is_file()} == before
    assert sorted(p.name for p in roots.src.iterdir()) == [".git", "x"]  # no staging dir left behind


@pytest.fixture
def window_lib(tmp_path: Path) -> Path:
    lib = tmp_path / "lib.sh3f"
    sh3d_window.build_library([sh3d_window.Spec(100, 140), sh3d_window.parse_size("200x150=36Dv,164R")], lib)
    return lib


def test_add_model_registers_entries_push_packs_them(
    roots: sh3d.Roots, pulled: Path, window_lib: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    entry = "floorplan3d#coupledWindow200x150-36Dv-164R"
    assert run(roots, "add-model", "x", "--lib", str(window_lib), "--entry", entry) == 0
    out = capsys.readouterr().out
    # The fixture home holds entries 0 and 1: the model dir is next, its icon after it.
    assert "model='2/window.obj' icon='3'" in out and f"commit {pulled}" in out
    # Home.xml takes sashes as <sash> children: axes/width as fractions of the size, angles in radians.
    sashes = [dict(re.findall(r"(\w+)='([^']*)'", line)) for line in out.splitlines() if "<sash " in line]
    assert len(sashes) == 3  # the D column's pair, then the R leaf
    assert sashes[1]["startAngle"] == "3.14159" and sashes[0]["endAngle"] == "-1.5708"
    assert all(0 < float(s[k]) < 1 for s in sashes for k in ("xAxis", "width"))
    assert "wallThickness='" in out and "doorOrWindowSash" not in out
    assert run(roots, "push", "x") == 0
    with zipfile.ZipFile(window_lib) as lib, zipfile.ZipFile(roots.sh3d("x")) as home:
        for theirs, ours in (("window.obj", "2/window.obj"), ("window.mtl", "2/window.mtl"), ("icon.png", "3")):
            assert home.read(ours) == lib.read(f"coupledWindow200x150-36Dv-164R/{theirs}")
        assert "2/" in home.namelist()


def test_add_model_refuses_unknown_entry_and_a_second_copy(
    roots: sh3d.Roots, pulled: Path, window_lib: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(roots, "add-model", "x", "--lib", str(window_lib), "--entry", "floorplan3d#nope") == 1
    assert "floorplan3d#coupledWindow100x140" in capsys.readouterr().err  # lists what the library has
    entry = "floorplan3d#coupledWindow100x140"
    assert run(roots, "add-model", "x", "--lib", str(window_lib), "--entry", entry) == 0
    capsys.readouterr()
    assert run(roots, "add-model", "x", "--lib", str(window_lib), "--entry", entry) == 1
    assert "already in" in capsys.readouterr().err
    assert sorted(p.name for p in pulled.iterdir() if p.name[0].isdigit()) == ["0", "1", "2", "3"]


def test_add_model_refuses_a_library_missing_its_model(
    roots: sh3d.Roots, pulled: Path, window_lib: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = window_lib.with_name("broken.sh3f")
    with zipfile.ZipFile(window_lib) as src, zipfile.ZipFile(broken, "w") as dst:
        for info in src.infolist():
            if info.filename != "coupledWindow100x140/window.obj":
                dst.writestr(info, src.read(info))
    entry = "floorplan3d#coupledWindow100x140"
    assert run(roots, "add-model", "x", "--lib", str(broken), "--entry", entry) == 1
    assert "is not in the library" in capsys.readouterr().err
    assert sorted(p.name for p in pulled.iterdir() if p.name[0].isdigit()) == ["0", "1"]
