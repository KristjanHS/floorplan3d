"""Auto walls <-> Home.xml: background px<->cm transform, traced walls, tagged write. Plan: docs/plans/2026-11-21-sh3d-auto-walls.md."""

import math
from dataclasses import dataclass
from pathlib import Path

from lxml import etree


@dataclass(frozen=True)
class LevelBackground:
    level_id: str | None  # None: a home without levels
    image: Path
    scale: float  # plan cm per image px
    x_origin: float  # cm
    y_origin: float  # cm

    def px_to_cm(self, u: float, v: float) -> tuple[float, float]:
        # PlanComponent.paintBackgroundImage: translate(-origin) then scale(s)
        return u * self.scale - self.x_origin, v * self.scale - self.y_origin

    def cm_to_px(self, x: float, y: float) -> tuple[float, float]:
        return (x + self.x_origin) / self.scale, (y + self.y_origin) / self.scale


def _background(level_id: str | None, bg: etree._Element, tree: Path) -> LevelBackground:
    def f(attr: str, default: str | None = None) -> float:
        return float(bg.get(attr, default))  # pyright: ignore[reportArgumentType]

    dist = math.dist(
        (f("scaleDistanceXStart"), f("scaleDistanceYStart")), (f("scaleDistanceXEnd"), f("scaleDistanceYEnd"))
    )
    return LevelBackground(
        level_id, tree / bg.get("image", ""), f("scaleDistance") / dist, f("xOrigin", "0"), f("yOrigin", "0")
    )


def level_backgrounds(root: etree._Element, tree: Path) -> list[LevelBackground]:
    """Per level: its own backgroundImage, else the home's; levels with neither are skipped."""
    home_bg = root.find("backgroundImage")
    levels = root.findall("level")
    if not levels:
        return [] if home_bg is None else [_background(None, home_bg, tree)]
    out: list[LevelBackground] = []
    for level in levels:
        bg = level.find("backgroundImage")
        bg = home_bg if bg is None else bg
        if bg is not None:
            out.append(_background(level.get("id"), bg, tree))
    return out
