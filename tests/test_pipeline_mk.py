import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

import pipeline_mk
import plans


def make_q(mk: Path, target: Path) -> int:
    """0 = up to date, 1 = would rebuild, 2 = the makefile itself is broken."""
    cmd = ["make", "-q", "-f", str(mk), pipeline_mk.rel(target)]
    return subprocess.run(cmd, cwd=plans.ROOT, check=False).returncode


CAL = plans.Calibration((0.0, 0.0), (100.0, 0.0), 1.0)


def render(tmp_path: Path, floor: plans.Floor) -> Path:
    mk = tmp_path / "pipeline.mk"
    mk.write_text(pipeline_mk.render([floor], tmp_path / "plans.yaml", tmp_path / "stamps", tmp_path / "status"))
    return mk


def built_floor(tmp_path: Path) -> plans.Floor:
    pdf = tmp_path / "f.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    floor = plans.Floor(name="f", pdf=pdf, calibration=CAL, work=tmp_path / "work", output=tmp_path / "out")
    mk = render(tmp_path, floor)
    # A fully built floor: stamps, then each step's output newer than its inputs (scripts/ included), all in the past.
    t = max(p.stat().st_mtime_ns for p in (plans.ROOT / "scripts").glob("*.py"))
    chain = [*(tmp_path / "stamps").iterdir(), floor.meta, floor.raw_blend, pipeline_mk.report(floor)]
    for i, p in enumerate(chain, 1):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
        os.utime(p, ns=(t + i * 1000, t + i * 1000))
    assert make_q(mk, pipeline_mk.report(floor)) == 0
    return floor


@pytest.mark.parametrize("edit", [{"metres": 1.2}, {"basis": 0.5}], ids=["metres", "basis"])
def test_calibration_edit_reruns_only_export(tmp_path, edit):
    floor = built_floor(tmp_path)
    floor = replace(floor, calibration=replace(CAL, **edit))  # same report path: staleness is the stamp's doing
    mk = render(tmp_path, floor)
    assert make_q(mk, floor.raw_blend) == 0  # no preprocess, no Blender convert
    assert make_q(mk, pipeline_mk.report(floor)) == 1


@pytest.mark.parametrize("edit", [{"wall_min_px": 4}, {"wall_min_area": 100}], ids=["wall_min_px", "wall_min_area"])
def test_wall_mask_edit_reruns_preprocess(tmp_path, edit):
    floor = replace(built_floor(tmp_path), **edit)
    assert make_q(render(tmp_path, floor), floor.meta) == 1
