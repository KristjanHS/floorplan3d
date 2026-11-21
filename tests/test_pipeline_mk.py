import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pipeline_mk
import plans


def up_to_date(mk: Path, target: Path) -> bool:
    cmd = ["make", "-q", "-f", str(mk), pipeline_mk.rel(target)]
    return subprocess.run(cmd, cwd=plans.ROOT, check=False).returncode == 0


def test_calibration_edit_reruns_only_export(tmp_path):
    pdf = tmp_path / "f.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    cal = plans.Calibration((0.0, 0.0), (100.0, 0.0), 1.0)
    floor = plans.Floor(name="f", pdf=pdf, calibration=cal, work=tmp_path / "work", output=tmp_path / "out")
    stamps, mk = tmp_path / "stamps", tmp_path / "pipeline.mk"
    mk.write_text(pipeline_mk.render([floor], tmp_path / "plans.yaml", stamps, tmp_path / "status"))
    # A fully built floor: stamps, then each step's output newer than its inputs (scripts/ included), all in the past.
    t = max(p.stat().st_mtime_ns for p in (plans.ROOT / "scripts").glob("*.py"))
    chain = [*stamps.iterdir(), floor.meta, floor.raw_blend, pipeline_mk.report(floor)]
    for i, p in enumerate(chain, 1):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
        os.utime(p, ns=(t + i * 1000, t + i * 1000))
    assert up_to_date(mk, pipeline_mk.report(floor))

    floor = replace(floor, calibration=replace(cal, metres=1.2))  # same report path: staleness is the stamp's doing
    mk.write_text(pipeline_mk.render([floor], tmp_path / "plans.yaml", stamps, tmp_path / "status"))
    assert up_to_date(mk, floor.raw_blend)  # no preprocess, no Blender convert
    assert not up_to_date(mk, pipeline_mk.report(floor))
