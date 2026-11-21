from pathlib import Path

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
