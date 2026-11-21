"""plans.yaml loading shared by the pipeline scripts."""

import math
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
OUTPUT = ROOT / "output"


@dataclass(frozen=True)
class Calibration:
    p1: tuple[float, float]
    p2: tuple[float, float]
    metres: float

    @property
    def pixels(self) -> float:
        return math.dist(self.p1, self.p2)

    @property
    def metres_per_px(self) -> float:
        return self.metres / self.pixels


@dataclass(frozen=True)
class Floor:
    name: str
    pdf: Path
    page: int = 1
    calibration: Calibration | None = None
    wall_height_m: float = 2.6
    dpi: int = 150
    max_side: int = 1000
    min_speck_px: int = 30
    binarize: str = "otsu"
    work: Path = WORK
    output: Path = OUTPUT

    @property
    def png(self) -> Path:
        return self.work / f"{self.name}.png"

    @property
    def meta(self) -> Path:
        return self.work / f"{self.name}.json"

    @property
    def grid(self) -> Path:
        return self.work / f"{self.name}_grid.png"

    @property
    def raw_blend(self) -> Path:
        return self.work / self.name / f"{self.name}_raw.blend"

    @property
    def outdir(self) -> Path:
        return self.output / self.name


def _calibration(raw: dict | None) -> Calibration | None:
    if not raw:
        return None
    cal = Calibration(tuple(raw["p1"]), tuple(raw["p2"]), float(raw["metres"]))
    if cal.pixels == 0 or cal.metres <= 0:
        raise ValueError(f"calibration needs two distinct points and metres > 0: {raw}")
    return cal


def load(path: Path) -> list[Floor]:
    data = yaml.safe_load(path.read_text())
    floors = []
    for raw in data["floors"]:
        raw = dict(raw)
        raw["pdf"] = (path.parent / raw["pdf"]).resolve()
        raw["calibration"] = _calibration(raw.get("calibration"))
        floors.append(Floor(**raw))
    names = [f.name for f in floors]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate floor names in {path}: {names}")
    return floors
