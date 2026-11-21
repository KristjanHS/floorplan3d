"""Run upstream FloorplanToBlender3d non-interactively per floor, sandboxed; unscaled result -> work/<name>/<name>_raw.blend."""

import argparse
import configparser
import json
import os
import shutil
import subprocess
from pathlib import Path

import plans
import sandbox

UPSTREAM = plans.ROOT / "vendor" / "FloorplanToBlender3d"
BLENDER_SCRIPT = UPSTREAM / "Blender" / "floorplan_to_3dObject_in_blender.py"
CALIBRATION_IMAGE = UPSTREAM / "Images" / "Calibrations" / "wallcalibration.png"
TARGET = "/floorplan.blend"


def write_config(path: Path, image: Path) -> None:
    """Upstream ini (keys from FloorplanToBlenderLib/config.py:generate_file); values are JSON."""
    conf = configparser.ConfigParser()
    conf["IMAGE"] = {"image_path": json.dumps(str(image)), "color": "[0, 0, 0]"}
    conf["TRANSFORM"] = {"position": "[0, 0, 0]", "rotation": "[0, 0, 0]", "scale": "[1, 1, 1]", "margin": "[0, 0, 0]"}
    conf["FEATURES"] = {k: "true" for k in ("floors", "rooms", "walls", "doors", "windows")}
    # rescale_image off: upstream's auto-rescale would change pixel size and break our calibration.
    conf["EXTRA_SETTINGS"] = {"remove_noise": "true", "rescale_image": "false"}
    conf["WALL_CALIBRATION"] = {
        "calibration_image_path": json.dumps(str(CALIBRATION_IMAGE)),
        "wall_size_calibration": "0",
    }
    with path.open("w") as f:
        conf.write(f)


def blender_argv(blender: str, scratch: Path, data_path: str) -> list[str]:
    # Upstream's script reads sys.argv positionally: [5] program_path, [6] target, [7:] data dirs.
    return [
        blender,
        "--factory-startup",
        "--background",
        "--python",
        str(BLENDER_SCRIPT),
        str(scratch),
        TARGET,
        data_path,
    ]


def convert(floor: plans.Floor, blender: str) -> Path:
    if not floor.png.is_file():
        raise FileNotFoundError(f"{floor.png} missing — run preprocess first")
    floor.raw_blend.unlink(missing_ok=True)  # a failed run must not leave old geometry for export
    scratch = floor.work / floor.name / "ftb"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    # Door/window templates load from CWD-relative Images/ (const.DOOR_MODEL); link it read-only.
    (scratch / "Images").symlink_to(UPSTREAM / "Images")
    ini = scratch / "floor.ini"
    write_config(ini, floor.png)

    env = {"MPLBACKEND": "Agg"}
    python = str(plans.ROOT / ".venv" / "bin" / "python")
    gen = sandbox.bwrap_cmd(
        [python, "-I", str(plans.ROOT / "scripts" / "ftb_generate.py"), str(ini)], [scratch], scratch, env=env
    )
    out = subprocess.run(gen, check=False, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"upstream detection failed for {floor.name}:\n{out.stderr[-3000:]}")
    data_path = out.stdout.strip().splitlines()[-1]

    build = sandbox.bwrap_cmd(blender_argv(blender, scratch, data_path), [scratch], scratch, ro=[Path(blender).parent])
    res = subprocess.run(build, check=False, capture_output=True, text=True)
    blend = scratch / TARGET.lstrip("/")
    # Upstream's script always exit(0)s, so failure shows only as a traceback / missing file.
    if res.returncode != 0 or "Traceback" in res.stderr + res.stdout or not blend.is_file():
        raise RuntimeError(
            f"upstream Blender build failed for {floor.name}:\n{res.stdout[-2000:]}\n{res.stderr[-2000:]}"
        )

    shutil.move(blend, floor.raw_blend)
    return floor.raw_blend


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plans", type=Path, default=plans.ROOT / "plans.yaml")
    p.add_argument("--only", help="convert only this floor name")
    p.add_argument("--blender", default=shutil.which("blender") or "blender")
    a = p.parse_args()
    blender = shutil.which(a.blender)
    if blender is None:
        raise SystemExit(f"blender not found: {a.blender}")
    for floor in plans.load(a.plans):
        if not a.only or floor.name == a.only:
            print(f"{floor.name}: {convert(floor, os.path.realpath(blender))}")


if __name__ == "__main__":
    main()
