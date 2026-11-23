import shutil
import subprocess
from pathlib import Path

import pytest

import sh3d
import sh3d_level
from sh3d_level import Line, raise_text, split_text
from sh3d_xmltext import element_re, open_attrs

REAL_DTD = sh3d.ROOT / "work" / "sh3d" / "SweetHome3D.dtd"
LINE = Line.parse("x=100", "+", 5)  # made-up fixture: split at x = 100, tolerance 5 cm


def wall(wid: str, x1: float, y1: float, x2: float, y2: float, extra: str = "", level: str = "up") -> str:
    return (
        f"  <wall id='{wid}' level='{level}'{extra} xStart='{x1:.1f}' yStart='{y1:.1f}' xEnd='{x2:.1f}' yEnd='{y2:.1f}'"
        " height='250.0' thickness='20.0' pattern='hatchUp'/>\n"
    )


W4 = wall("w4", 0, 200, 0, 0, " wallAtStart='w3' wallAtEnd='w1'")
HOME = (
    "<?xml version='1.0'?>\n<home version='7400' name='t' wallHeight='250'>\n"
    "  <level id='gr' name='Ground' elevation='0.0' floorThickness='12.0' height='250.0' elevationIndex='0'/>\n"
    "  <level id='up' name='Upper' elevation='262.0' floorThickness='12.0' height='250.0' elevationIndex='0'/>\n"
    "  <doorOrWindow id='d1' level='up' name='Window' x='200.0' y='0.0' width='80.0' depth='20.0' height='120.0'/>\n"
    "  <pieceOfFurniture id='p1' level='up' name='Chair' x='50.0' y='100.0' width='40.0' depth='40.0' height='90.0'/>\n"
    + wall("g1", 0, 0, 300, 0, level="gr")
    + wall("w1", 0, 0, 300, 0, " wallAtStart='w4' wallAtEnd='w2'")
    + wall("w2", 300, 0, 300, 200, " wallAtStart='w1' wallAtEnd='w3'")
    + wall("w3", 300, 200, 0, 200, " wallAtStart='w2' wallAtEnd='w4'").replace(
        "height='250.0'", "height='200.0' heightAtEnd='260.0'"
    )
    + W4
    + wall("w5", 97, 50, 250, 50, " wallAtStart='w4'")  # 97: within 5 cm of the line, so on it
    + "  <room id='r1' level='up' name='Lobby' areaVisible='true'>\n"
    "    <point x='0.0' y='0.0'/>\n    <point x='300.0' y='0.0'/>\n"
    "    <point x='300.0' y='200.0'/>\n    <point x='0.0' y='200.0'/>\n  </room>\n"
    "  <dimensionLine id='dl1' level='up' xStart='0.0' yStart='-30.0' xEnd='300.0' yEnd='-30.0' offset='5.0'/>\n"
    "  <dimensionLine id='dl2' level='up' xStart='150.0' yStart='-30.0' xEnd='250.0' yEnd='-30.0' offset='5.0'/>\n"
    "</home>\n"
)


def elements(text: str, tag: str) -> dict[str, dict[str, str]]:
    return {a["id"]: a for a in (open_attrs(m.group(0)) for m in element_re(tag).finditer(text))}


@pytest.fixture
def split() -> tuple[str, str]:
    text, _, nid = split_text(HOME, "Upper", "Upper b", LINE, 336.0, 30.0, 260.0)
    return text, nid


def test_split_moves_side_walls_cuts_crossing_ones(split: tuple[str, str]) -> None:
    text, nid = split
    walls = elements(text, "wall")
    on = {lv: sorted(w for w, a in walls.items() if a["level"] == lv) for lv in ("up", nid)}
    assert on["up"] == ["w1", "w3", "w4"]  # w1/w3 keep their id on the stay half
    assert len(on[nid]) == 4 and {"w2", "w5"} <= set(on[nid])
    assert (walls["w1"]["xStart"], walls["w1"]["xEnd"]) == ("0.0", "100")
    w1b = next(a for a in walls.values() if a["level"] == nid and a["yStart"] == "0.0" and a["xStart"] == "100")
    assert (w1b["xEnd"], w1b.get("wallAtEnd"), w1b.get("wallAtStart")) == ("300.0", "w2", None)
    assert walls["w1"].get("wallAtStart") == "w4" and "wallAtEnd" not in walls["w1"]


def test_split_repoints_refs_to_the_half_on_the_same_level_drops_cross_level(split: tuple[str, str]) -> None:
    text, nid = split
    walls = elements(text, "wall")
    half = {a["xStart"] + "," + a["yStart"]: w for w, a in walls.items() if a["level"] == nid}
    assert walls["w2"]["wallAtStart"] == half["100,0.0"]  # was w1: its side half
    assert walls["w2"]["wallAtEnd"] == half["300.0,200.0"]  # was w3: w3 starts on the side
    assert "wallAtStart" not in walls["w5"]  # w4 stayed on Upper
    assert W4 in text  # west wall byte for byte


def test_split_interpolates_a_sloped_wall(split: tuple[str, str]) -> None:
    walls = elements(split[0], "wall")
    side = next(a for a in walls.values() if a["xStart"] == "300.0" and a["yStart"] == "200.0")
    assert (side["height"], side["heightAtEnd"], side["xEnd"]) == ("200.0", "240", "100")
    assert (walls["w3"]["height"], walls["w3"]["heightAtEnd"], walls["w3"]["xStart"]) == ("240", "260.0", "100")


def test_split_clips_rooms_moves_lines_and_pieces(split: tuple[str, str]) -> None:
    text, nid = split
    rooms = [m.group(0) for m in element_re("room").finditer(text)]
    assert len(rooms) == 2 and all("name='Lobby' areaVisible='true'" in r for r in rooms)
    west, east = rooms
    assert "level='up'" in west and "<point x='100' y='0.0'/>" in west and "300.0" not in west
    assert f"level='{nid}'" in east and "id='r1'" not in east and "<point x='0.0'" not in east
    dims = elements(text, "dimensionLine")
    assert (dims["dl1"]["level"], dims["dl2"]["level"]) == ("up", nid)
    assert elements(text, "doorOrWindow")["d1"]["level"] == nid
    assert elements(text, "pieceOfFurniture")["p1"]["level"] == "up"
    level = elements(text, "level")[nid]
    assert (level["name"], level["elevation"], level["floorThickness"], level["height"]) == (
        "Upper b",
        "336.0",
        "30.0",
        "260.0",
    )
    assert elements(text, "wall")["g1"]["level"] == "gr"


def test_split_keeps_unchanged_coordinate_strings() -> None:
    w1 = "xStart='0.0' yStart='0.0' xEnd='300.0' yEnd='0.0'"
    home = HOME.replace(w1, "xStart='-0.12345' yStart='0.54321' xEnd='300.0' yEnd='0.54321'")
    home = home.replace("<point x='0.0' y='0.0'/>", "<point x='0.0' y='0.54321'/>")
    home = home.replace("<point x='300.0' y='0.0'/>", "<point x='300.0' y='0.54321'/>")
    text, _, nid = split_text(home, "Upper", "Upper b", LINE, 336.0, 30.0, 260.0)
    walls = elements(text, "wall")
    assert (walls["w1"]["xStart"], walls["w1"]["yStart"], walls["w1"]["yEnd"]) == ("-0.12345", "0.54321", "0.54321")
    assert any(a["level"] == nid and a["yStart"] == "0.54321" and a["xStart"] == "100" for a in walls.values())
    rooms = [m.group(0) for m in element_re("room").finditer(text)]
    assert all("<point x='100' y='0.54321'/>" in r for r in rooms)


def test_split_refuses_a_room_crossing_the_line_twice() -> None:
    u = "".join(
        f"    <point x='{x}' y='{y}'/>\n"
        for x, y in ((0, 0), (300, 0), (300, 200), (200, 200), (200, 100), (150, 100), (150, 200), (0, 200))
    )
    home = HOME.replace(
        "    <point x='0.0' y='0.0'/>\n    <point x='300.0' y='0.0'/>\n"
        "    <point x='300.0' y='200.0'/>\n    <point x='0.0' y='200.0'/>\n",
        u,
    )
    assert home != HOME
    with pytest.raises(sh3d.Sh3dError, match="more than once"):
        split_text(home, "Upper", "Upper b", Line.parse("y=150", "+", 5), 336.0, 30.0, 260.0)


def test_split_refuses_crossing_arc_and_unknown_level() -> None:
    arc = HOME.replace("wallAtEnd='w2' xStart", "wallAtEnd='w2' arcExtent='0.5' xStart")
    assert arc != HOME
    with pytest.raises(sh3d.Sh3dError, match="arc"):
        split_text(arc, "Upper", "Upper b", LINE, 336.0, 30.0, 260.0)
    with pytest.raises(sh3d.Sh3dError, match="no level named 'Attic'"):
        split_text(HOME, "Attic", "Upper b", LINE, 336.0, 30.0, 260.0)


def test_raise_walls_sets_height_on_the_side_and_joins_the_halves() -> None:
    text, _ = raise_text(HOME, "Upper", LINE, 512.0)
    walls = elements(text, "wall")
    assert walls["w2"]["height"] == "250" and walls["w5"]["height"] == "250"
    side = next(a for w, a in walls.items() if a["xStart"] == "300.0" and a["yStart"] == "200.0")
    assert side["height"] == "250" and "heightAtEnd" not in side
    assert (walls["w3"]["height"], walls["w3"]["heightAtEnd"]) == ("240", "260.0")  # stay half: not raised
    assert side["wallAtEnd"] == "w3" and walls["w3"]["wallAtStart"] == side["id"]
    assert walls["w2"]["wallAtEnd"] == side["id"]
    assert walls["w5"]["wallAtStart"] == "w4"  # same level: kept
    assert W4 in text and walls["g1"]["height"] == "250.0"


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd), *args], check=True)


def cli(tmp_path: Path, home: str, *args: str) -> tuple[int, Path]:
    if not REAL_DTD.is_file():
        pytest.skip("SweetHome3D.dtd not built (make sh3d-dtd)")
    (tmp_path / "work" / "sh3d").mkdir(parents=True)
    shutil.copy(REAL_DTD, tmp_path / "work" / "sh3d" / "SweetHome3D.dtd")
    xml = tmp_path / "src" / "x" / "Home.xml"
    xml.parent.mkdir(parents=True)
    xml.write_text(home)
    git(tmp_path / "src", "init", "-q")
    git(tmp_path / "src", "add", "-A")
    git(tmp_path / "src", "commit", "-q", "-m", "c")
    return sh3d_level.main([*roots(tmp_path), *args]), xml


def roots(tmp_path: Path) -> list[str]:
    return ["--homes", str(tmp_path / "h"), "--src", str(tmp_path / "src"), "--work", str(tmp_path / "work")]


SPLIT_ARGS = ("split", "x", "--line", "x=100", "--side", "+", "--from", "Upper", "--new", "Upper b")
SIZE_ARGS = ("--elevation", "336", "--floor-thickness", "30", "--height", "260")


def test_cli_split_output_is_dtd_valid(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, xml = cli(tmp_path, HOME, *SPLIT_ARGS, *SIZE_ARGS)
    assert code == 0, capsys.readouterr().err
    assert "Upper b: 4 wall, 1 room, 1 dimensionLine, 1 doorOrWindow" in capsys.readouterr().out
    assert xml.read_text() != HOME


def test_cli_dry_run_and_invalid_output_leave_home_xml_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = HOME.replace("name='Chair'", "name='Chair' model='1/missing.obj'")  # resource missing: invalid
    code, xml = cli(tmp_path, broken, *SPLIT_ARGS, *SIZE_ARGS)
    assert code == 1 and "Home.xml restored" in capsys.readouterr().err
    assert xml.read_text() == broken
    assert sh3d_level.main([*roots(tmp_path), *SPLIT_ARGS, *SIZE_ARGS, "--dry-run"]) == 0
    assert xml.read_text() == broken
