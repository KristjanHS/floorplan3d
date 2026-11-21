import json
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
