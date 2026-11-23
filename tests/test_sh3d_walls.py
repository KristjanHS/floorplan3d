import subprocess
import zipfile
from pathlib import Path

import cv2
import numpy as np
import pytest
from lxml import etree
from test_sh3d import make_sh3d

import sh3d
import sh3d_walls
import sh3d_xmltext

BG = "<backgroundImage image='{img}' scaleDistance='100' scaleDistanceXStart='10' scaleDistanceYStart='20' scaleDistanceXEnd='10' scaleDistanceYEnd='70'{origin}/>"
HOME = """<home version='7400' name='t' wallHeight='250'>{home_bg}
  <level id='L0' name='A' elevation='0' floorThickness='12' height='260'>{l0_bg}</level>
  <level id='L1' name='B' elevation='272' floorThickness='12' height='260'/>
</home>"""


def root(home_bg: str = "", l0_bg: str = "") -> etree._Element:
    return etree.fromstring(HOME.format(home_bg=home_bg, l0_bg=l0_bg))


def test_px_to_cm_matches_plancomponent(tmp_path: Path) -> None:
    (bg,) = sh3d_walls.level_backgrounds(root(l0_bg=BG.format(img="0", origin=" xOrigin='30' yOrigin='-40'")), tmp_path)
    assert (bg.level_id, bg.image, bg.scale) == ("L0", tmp_path / "0", 2.0)
    # g2D.translate(-xOrigin, -yOrigin); g2D.scale(s, s): image px -> plan cm = T @ S @ p
    t = np.array([[1, 0, -30], [0, 1, 40], [0, 0, 1]])
    s = np.diag([2.0, 2.0, 1.0])
    for u, v in [(0, 0), (123.5, 7), (-3, 900)]:
        assert bg.px_to_cm(u, v) == pytest.approx(tuple((t @ s @ [u, v, 1])[:2]))
        assert bg.cm_to_px(*bg.px_to_cm(u, v)) == pytest.approx((u, v))


def test_origin_defaults_to_zero(tmp_path: Path) -> None:
    (bg,) = sh3d_walls.level_backgrounds(root(l0_bg=BG.format(img="0", origin="")), tmp_path)
    assert bg.px_to_cm(5, 5) == (10.0, 10.0)


def test_level_without_bg_skipped_or_falls_back_to_home(tmp_path: Path) -> None:
    assert [b.level_id for b in sh3d_walls.level_backgrounds(root(l0_bg=BG.format(img="0", origin="")), tmp_path)] == [
        "L0"
    ]
    both = sh3d_walls.level_backgrounds(
        root(home_bg=BG.format(img="h", origin=""), l0_bg=BG.format(img="0", origin="")), tmp_path
    )
    assert [(b.level_id, b.image.name) for b in both] == [("L0", "0"), ("L1", "h")]
    assert sh3d_walls.level_backgrounds(root(), tmp_path) == []


def test_home_without_levels_uses_home_bg(tmp_path: Path) -> None:
    home = etree.fromstring(f"<home version='7400' name='t'>{BG.format(img='h', origin='')}</home>")
    assert [(b.level_id, b.image.name) for b in sh3d_walls.level_backgrounds(home, tmp_path)] == [(None, "h")]


WALL = "<wall id='{id}' level='L0' xStart='40' yStart='{y}' xEnd='400' yEnd='{y}' thickness='20'>{prop}</wall>"


def drawn_plan() -> np.ndarray:
    """White 300x300 px at 2 cm/px with both faces of a 20 cm wall at y=100 cm drawn in black."""
    img = np.full((300, 300), 255, np.uint8)
    for v in (45, 55):
        cv2.line(img, (20, v), (200, v), 0, 1)
    return img


def wall(y: float) -> sh3d_walls.TracedWall:
    return sh3d_walls.TracedWall("w", "L0", (40, y), (400, y), 20)


def test_ink_rate_separates_fit_from_shift(tmp_path: Path) -> None:
    (bg,) = sh3d_walls.level_backgrounds(root(l0_bg=BG.format(img="0", origin="")), tmp_path)
    img = drawn_plan()
    assert sh3d_walls.ink_rate(img, sh3d_walls.faces_px(wall(100), bg)) >= 0.8
    assert sh3d_walls.ink_rate(img, sh3d_walls.faces_px(wall(150), bg)) < 0.3


def test_ink_rate_counts_off_image_as_miss() -> None:
    assert sh3d_walls.ink_rate(drawn_plan(), [((-1000, 45), (180, 45))]) < 0.3


def test_overlay_skips_auto_walls_and_names_trace(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src, work = tmp_path / "src", tmp_path / "work"
    (src / "x").mkdir(parents=True)
    work.mkdir()
    (src / "x" / "0").write_bytes(cv2.imencode(".png", drawn_plan())[1].tobytes())
    (work / "a_trace.png").write_bytes((src / "x" / "0").read_bytes())
    auto = f"<property name='{sh3d_walls.AUTO_PROP}' value='s'/>"
    walls = WALL.format(id="w1", y=100, prop="") + WALL.format(id="auto-1", y=150, prop=auto)
    home = HOME.format(home_bg="", l0_bg=BG.format(img="0", origin="")).replace("</home>", walls + "</home>")
    (src / "x" / "Home.xml").write_text(home)
    assert (
        sh3d_walls.main(["--homes", str(tmp_path / "homes"), "--src", str(src), "--work", str(work), "overlay", "x"])
        == 0
    )
    assert capsys.readouterr().out.startswith("L0: 1 walls, ink 1.00, image = a_trace.png")
    out = cv2.imread(str(work / "walls" / "x-L0.png"))
    assert out is not None and out.shape == (300, 300, 3)


ROOM = "<room level='L0'>{pts}</room>"


def room(x0: float, y0: float, x1: float, y1: float) -> str:
    pts = "".join(f"<point x='{x}' y='{y}'/>" for x, y in [(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
    return ROOM.format(pts=pts)


def test_rooms_dry_run_draws_walls_and_holes_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    src, work = tmp_path / "src", tmp_path / "work"
    (src / "x").mkdir(parents=True)
    (src / "x" / "0").write_bytes(cv2.imencode(".png", np.full((300, 300), 255, np.uint8))[1].tobytes())
    # 15 cm strip (wall), then a 64 cm strip (hole); level L1 has no background and is skipped
    rooms = room(0, 0, 100, 200) + room(115, 0, 215, 200) + room(279, 0, 379, 200)
    home = HOME.format(home_bg="", l0_bg=BG.format(img="0", origin="")).replace("</home>", rooms + "</home>")
    (src / "x" / "Home.xml").write_text(home)
    before = (src / "x" / "Home.xml").read_bytes()
    assert (
        sh3d_walls.main(["--homes", str(tmp_path / "homes"), "--src", str(src), "--work", str(work), "rooms", "x"]) == 0
    )
    assert capsys.readouterr().out.startswith("L0: 3 rooms, 1 walls 15.0-15.0 cm, 1 holes (64 cm) -> ")
    assert (src / "x" / "Home.xml").read_bytes() == before
    out = cv2.imread(str(work / "walls" / "x-L0-rooms.png"))
    assert out is not None and (out == sh3d_walls.ORANGE).all(axis=2).any()


WRITE_DTD = """<!ELEMENT home (property*, level*, wall*, room*)>
<!ATTLIST home version CDATA #IMPLIED name CDATA #IMPLIED wallHeight CDATA #IMPLIED>
<!ELEMENT level EMPTY>
<!ATTLIST level id ID #REQUIRED height CDATA #IMPLIED>
<!ELEMENT wall (property*)>
<!ATTLIST wall id ID #REQUIRED level IDREF #IMPLIED wallAtStart IDREF #IMPLIED wallAtEnd IDREF #IMPLIED
  xStart CDATA #REQUIRED yStart CDATA #REQUIRED xEnd CDATA #REQUIRED yEnd CDATA #REQUIRED height CDATA #IMPLIED
  thickness CDATA #REQUIRED pattern CDATA #IMPLIED>
<!ELEMENT property EMPTY>
<!ATTLIST property name CDATA #REQUIRED value CDATA #REQUIRED>
<!ELEMENT room (point+)>
<!ATTLIST room level IDREF #IMPLIED>
<!ELEMENT point EMPTY>
<!ATTLIST point x CDATA #REQUIRED y CDATA #REQUIRED>
"""
HAND = "  <wall id='hand' level='L0'{ref} xStart='-100' yStart='-22.5' xEnd='400' yEnd='-22.5' thickness='45'/>\n"


def tagged(y: float, x0: str = "0", sig_x0: float = 0, extra: str = "", value: str | None = None) -> str:
    sig = sh3d_walls._sig(sh3d_walls.wl.Wall((sig_x0, y), (100, y), 10), "260")
    wid = sh3d_walls._wall_id("L0", sig)
    return (
        f"  <wall id='{wid}' level='L0' xStart='{x0}' yStart='{y}' xEnd='100' yEnd='{y}' height='260.0' thickness='10.0'"
        f"{extra}>\n    <property name='floorplan3d.auto' value='{sig if value is None else value}'/>\n  </wall>\n"
    )


OLD = tagged(300)
ROOMS = room(0, 0, 100, 200) + room(115, 0, 215, 200) + "\n"
WRITE_HOME = (
    "<?xml version='1.0'?>\n<home version='7400' name='t' wallHeight='250'>\n  <level id='L0' height='260'/>\n"
    + HAND.format(ref=f" wallAtStart='{sh3d_xmltext.open_attrs(OLD)['id']}'")
    + OLD
    + tagged(320, x0="0.004")  # SH3D float reprint: still unedited
    + tagged(340, x0="-20")  # moved 20 cm in the GUI
    + tagged(360, extra=" pattern='hatchUp'")  # restyled
    + tagged(380, value="x")  # unreadable tag
    + ROOMS
    + "</home>\n"
)


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(cwd), *args], check=True)


def write_cli(tmp_path: Path) -> list[str]:
    return [
        "--homes",
        str(tmp_path / "homes"),
        "--src",
        str(tmp_path / "src"),
        "--work",
        str(tmp_path / "work"),
        "write",
        "x",
    ]


@pytest.fixture
def home_src(tmp_path: Path) -> Path:
    (tmp_path / "work" / "sh3d").mkdir(parents=True)
    (tmp_path / "work" / "sh3d" / "SweetHome3D.dtd").write_text(WRITE_DTD)
    (tmp_path / "src" / "x").mkdir(parents=True)
    (tmp_path / "src" / "x" / "Home.xml").write_text(WRITE_HOME)
    git(tmp_path / "src", "init", "-q")
    git(tmp_path / "src", "add", "-A")
    git(tmp_path / "src", "commit", "-q", "-m", "c")
    return tmp_path / "src" / "x" / "Home.xml"


def auto_walls(text: str) -> list[str]:
    return [w for w in sh3d_walls.WALL_RE.findall(text) if "<wall id='auto-" in w]


def test_write_replaces_tagged_keeps_edited_and_hand_walls(home_src: Path, tmp_path: Path) -> None:
    assert sh3d_walls.main(write_cli(tmp_path)) == 0
    out = home_src.read_text()
    assert HAND.format(ref="") in out  # dangling ref to the replaced wall cleared, rest byte-identical
    walls = auto_walls(out)
    assert not any("yStart='300'" in w or "yStart='320'" in w for w in walls)
    kept = [w for w in walls if sh3d_walls.AUTO_PROP not in w]
    assert sorted(w.split("yStart='")[1][:3] for w in kept) == ["340", "360", "380"]
    (new,) = [w for w in walls if sh3d_walls.AUTO_PROP in w]
    assert "level='L0'" in new and "height='260'" in new and "thickness='15'" in new
    assert out.endswith(ROOMS + "</home>\n")
    wid = sh3d_xmltext.open_attrs(new)["id"]
    home_src.write_text(out.replace(HAND.format(ref=""), HAND.format(ref=f" wallAtEnd='{wid}'")))
    joined = home_src.read_text()
    git(tmp_path / "src", "commit", "-q", "-am", "w")
    assert sh3d_walls.main(write_cli(tmp_path)) == 0
    assert home_src.read_text() == joined  # idempotent; the hand wall's join to a re-inserted id survives


def wall_by_id(text: str, wid: str) -> list[str]:
    return [w for w in sh3d_walls.WALL_RE.findall(text) if sh3d_xmltext.open_attrs(w)["id"] == wid]


def test_write_edited_wall_keeps_its_id_no_duplicate(
    home_src: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert sh3d_walls.main(write_cli(tmp_path)) == 0
    out = home_src.read_text()
    (new,) = [w for w in auto_walls(out) if sh3d_walls.AUTO_PROP in w]
    wid, x1, x2 = (sh3d_xmltext.open_attrs(new)[k] for k in ("id", "xStart", "xEnd"))
    moved_x = (str(float(x1) + 20), str(float(x2) + 20))
    moved = new.replace(f"xStart='{x1}' ", f"xStart='{moved_x[0]}' ").replace(f"xEnd='{x2}' ", f"xEnd='{moved_x[1]}' ")
    home_src.write_text(out.replace(new, moved))
    git(tmp_path / "src", "commit", "-q", "-am", "moved")
    capsys.readouterr()
    assert sh3d_walls.main(write_cli(tmp_path)) == 0  # was: DTD duplicate ID, Home.xml restored
    (kept,) = wall_by_id(home_src.read_text(), wid)
    attrs = sh3d_xmltext.open_attrs(kept)
    assert (attrs["xStart"], attrs["xEnd"]) == moved_x and sh3d_walls.AUTO_PROP not in kept  # adopted, untagged
    assert "L0: 0 walls written" in capsys.readouterr().out  # its id is taken: nothing was inserted


def test_write_gui_deleted_auto_wall_stays_deleted(
    home_src: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert sh3d_walls.main(write_cli(tmp_path)) == 0
    out = home_src.read_text()
    (new,) = [w for w in auto_walls(out) if sh3d_walls.AUTO_PROP in w]
    wid = sh3d_xmltext.open_attrs(new)["id"]
    assert wid in sh3d_walls._home_ids(out, sh3d_walls.WRITTEN_PROP)
    home_src.write_text(out.replace(new, ""))  # deleted in the GUI
    git(tmp_path / "src", "commit", "-q", "-am", "deleted")
    capsys.readouterr()
    assert sh3d_walls.main(write_cli(tmp_path)) == 0
    again = home_src.read_text()
    assert wall_by_id(again, wid) == []
    assert sh3d_walls._home_ids(again, sh3d_walls.DELETED_PROP) == {wid}
    assert wid not in sh3d_walls._home_ids(again, sh3d_walls.WRITTEN_PROP)

    def walls_and_rest(text: str) -> str:  # everything but the two home-level tombstone properties
        for name in (sh3d_walls.WRITTEN_PROP, sh3d_walls.DELETED_PROP):
            text = sh3d_walls._home_prop_re(name).sub("", text)
        return text

    assert walls_and_rest(again) == walls_and_rest(out.replace(new, ""))  # every other wall untouched
    assert capsys.readouterr().out.splitlines() == [
        "L0: 0 walls written, 0 skipped (match a traced wall)",  # was: 1 written, counted before the tombstone
        "1 deleted auto walls stay deleted",
        "0 edited auto walls kept as hand-traced",
    ]
    git(tmp_path / "src", "commit", "-q", "-am", "w")
    assert sh3d_walls.main(write_cli(tmp_path)) == 0
    assert home_src.read_text() == again  # idempotent: the tombstone holds on every later run


def test_write_refuses_dirty_tree(home_src: Path, tmp_path: Path) -> None:
    home_src.write_text(WRITE_HOME + "\n")
    assert sh3d_walls.main(write_cli(tmp_path)) == 1
    assert home_src.read_text() == WRITE_HOME + "\n"


def test_write_auto_pulls_gui_save_first(tmp_path: Path) -> None:
    work, src = tmp_path / "work", tmp_path / "src"
    (work / "sh3d").mkdir(parents=True)
    (work / "sh3d" / "SweetHome3D.dtd").write_text(WRITE_DTD)
    src.mkdir()
    git(src, "init", "-q")
    roots = sh3d.Roots(homes=tmp_path / "homes", src=src, backups=tmp_path / "b", work=work, dtd=work / "d")
    make_sh3d(roots.sh3d("x"), [("Home.xml", WRITE_HOME.encode(), zipfile.ZIP_DEFLATED)])
    sh3d.pull(roots, "x")
    git(src, "add", "-A")
    git(src, "commit", "-q", "-m", "pull")
    saved = WRITE_HOME.replace("name='t'", "name='gui'")
    make_sh3d(roots.sh3d("x"), [("Home.xml", saved.encode(), zipfile.ZIP_DEFLATED)])
    assert sh3d_walls.main(write_cli(tmp_path)) == 0
    assert "name='gui'" in (src / "x" / "Home.xml").read_text()  # write ran on the GUI save, not the stale tree
