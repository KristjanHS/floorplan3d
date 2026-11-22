from pathlib import Path

import cv2
import numpy as np
import pytest
from lxml import etree

import sh3d_walls

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
    assert sh3d_walls.main(["--src", str(src), "--work", str(work), "overlay", "x"]) == 0
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
    assert sh3d_walls.main(["--src", str(src), "--work", str(work), "rooms", "x"]) == 0
    assert capsys.readouterr().out.startswith("L0: 3 rooms, 1 walls 15.0-15.0 cm, 1 holes (64 cm) -> ")
    assert (src / "x" / "Home.xml").read_bytes() == before
    out = cv2.imread(str(work / "walls" / "x-L0-rooms.png"))
    assert out is not None and (out == sh3d_walls.ORANGE).all(axis=2).any()
