"""plans.yaml loading shared by the pipeline scripts."""

import json
import math
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

Point = tuple[float, float]

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
OUTPUT = ROOT / "output"


@dataclass(frozen=True)
class Calibration:
    p1: tuple[float, float]
    p2: tuple[float, float]
    metres: float
    basis: float | None = None  # preprocess downscale p1/p2 were picked at; None = the current work PNG

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
    max_side: int = 1800
    min_speck_px: int = 30
    wall_min_px: int = 6  # wall mask: keep strokes at least this wide (render px); 0 = no mask, no outline frame
    wall_min_area: int = 300  # wall mask: drop kept components smaller than this (render px²)
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

    def downscale(self) -> float:
        """The current work PNG's downscale, from preprocess's meta."""
        if not self.meta.is_file():
            raise FileNotFoundError(f"{self.meta} missing — run preprocess (px basis needs the work PNG's downscale)")
        return float(json.loads(self.meta.read_text())["downscale"])

    def work_px(self, pt: Point, basis: float | None) -> Point:
        """`pt` picked at downscale `basis` (None = the current work PNG), in current work-PNG px."""
        return pt if basis is None else at_downscale(pt, basis, self.downscale())

    def calibration_px(self) -> Calibration | None:
        """The calibration with p1/p2 in current work-PNG px (the only form metres_per_px is valid in)."""
        cal = self.calibration
        if cal is None or cal.basis is None:
            return cal
        return replace(cal, p1=self.work_px(cal.p1, cal.basis), p2=self.work_px(cal.p2, cal.basis), basis=None)


def at_downscale(pt: Point, basis: float, downscale: float) -> Point:
    """A point picked on a work PNG made at downscale `basis`, in px of one made at `downscale`."""
    k = downscale / basis
    return pt[0] * k, pt[1] * k


def _calibration(raw: dict | None) -> Calibration | None:
    if not raw:
        return None
    basis = None if raw.get("basis") is None else float(raw["basis"])
    cal = Calibration(tuple(raw["p1"]), tuple(raw["p2"]), float(raw["metres"]), basis)
    if cal.pixels == 0 or cal.metres <= 0:
        raise ValueError(f"calibration needs two distinct points and metres > 0: {raw}")
    if basis is not None and not 0 < basis <= 1:
        raise ValueError(f"calibration basis is a preprocess downscale in (0, 1]: {raw}")
    return cal


def load(path: Path) -> list[Floor]:
    data = yaml.safe_load(path.read_text())
    floors = []
    for raw in data["floors"]:
        raw = dict(raw)
        raw["pdf"] = (path.parent / raw["pdf"]).resolve()
        raw["calibration"] = _calibration(raw.get("calibration"))
        floor = Floor(**raw)
        if floor.wall_min_px < 0 or floor.wall_min_area < 0:
            raise ValueError(f"{floor.name}: wall_min_px and wall_min_area must be >= 0")
        floors.append(floor)
    names = [f.name for f in floors]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate floor names in {path}: {names}")
    return floors
