"""Per floor: Blender rescales work/<name>/<name>_raw.blend -> output/<name>/ (.blend/.glb/.obj/previews/report)."""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np

import plans

BLENDER_SCRIPT = plans.ROOT / "scripts" / "export_blend.py"
TOLERANCE = 0.03


def stem(floor: plans.Floor) -> str:
    return floor.name if floor.calibration else f"{floor.name}_UNSCALED"


def drawn_extent_px(floor: plans.Floor) -> tuple[int, int]:
    """Width, height of the black-pixel bbox in the cleaned PNG."""
    img = cv2.imread(str(floor.png), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError(floor.png)
    ys, xs = np.nonzero(img == 0)
    return int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)


def wall_check(floor: plans.Floor, report: dict) -> dict:
    """Model XY extent vs the drawn extent (PNG bbox x metres_per_px)."""
    assert floor.calibration is not None
    mpp = floor.calibration.metres_per_px
    drawn = [px * mpp for px in drawn_extent_px(floor)]
    model = report["extent_m"][:2]
    diff = [abs(m - d) / d for m, d in zip(model, drawn, strict=True)]
    return {
        "status": "OK" if max(diff) <= TOLERANCE else "MISMATCH",
        "drawn_m": [round(v, 3) for v in drawn],
        "model_m": model,
        "diff_pct": [round(100 * v, 1) for v in diff],
    }


def export(floor: plans.Floor, blender: str) -> dict:
    if not floor.raw_blend.is_file():
        raise FileNotFoundError(f"{floor.raw_blend} missing — run convert first")
    name = stem(floor)
    args = ["--out-dir", str(floor.outdir), "--name", name, "--wall-height", str(floor.wall_height_m)]
    if floor.calibration:
        args += ["--metres-per-px", repr(floor.calibration.metres_per_px)]
    # The raw .blend is sandbox-written: no startup file, no auto-run scripts.
    cmd = [blender, "-noaudio", "--factory-startup", "--disable-autoexec", "--background", str(floor.raw_blend)]
    cmd += ["--python-exit-code", "1"]
    subprocess.run([*cmd, "--python", str(BLENDER_SCRIPT), "--", *args], check=True, capture_output=True, text=True)
    path = floor.outdir / f"{name}_report.json"
    report = json.loads(path.read_text())
    report["label"] = "SCALED" if floor.calibration else "UNSCALED"
    if floor.calibration:
        report["wall_check"] = wall_check(floor, report)
    path.write_text(json.dumps(report, indent=2) + "\n")
    return report


def summary(floor: plans.Floor, report: dict) -> str:
    out = f"{floor.name}: {floor.outdir / stem(floor)}.{{blend,glb,obj}} objects={report['objects']}"
    if not floor.calibration:
        return f"{out}\n  WARNING: no calibration in plans.yaml — UNSCALED (1 unit = 100 px), wall check skipped"
    c, cal = report["wall_check"], floor.calibration
    lo, hi = cal.metres * (1 - TOLERANCE), cal.metres * (1 + TOLERANCE)
    return (
        f"{out} extent_m={report['extent_m']}\n"
        f"  wall check {c['status']}: model {c['model_m']} m vs drawn {c['drawn_m']} m, diff {c['diff_pct']} %\n"
        f"  calibration: in {stem(floor)}.blend measure the wall at PNG {cal.p1}->{cal.p2}; "
        f"expect {cal.metres} m ({lo:.2f}..{hi:.2f})"
    )


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plans", type=Path, default=plans.ROOT / "plans.yaml")
    p.add_argument("--only", help="export only this floor name")
    p.add_argument("--blender", default=shutil.which("blender") or "blender")
    a = p.parse_args()
    blender = shutil.which(a.blender)
    if blender is None:
        raise SystemExit(f"blender not found: {a.blender}")
    for floor in plans.load(a.plans):
        if a.only and floor.name != a.only:
            continue
        try:
            report = export(floor, os.path.realpath(blender))
        except subprocess.CalledProcessError as e:
            raise SystemExit(f"Blender export failed for {floor.name}:\n{e.stdout[-2000:]}\n{e.stderr[-2000:]}") from e
        print(summary(floor, report))


if __name__ == "__main__":
    main()
