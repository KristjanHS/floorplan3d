import json
import os
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

import convert
import plans
import sandbox

PROBE = plans.ROOT / "scripts" / "ftb_probe.py"
T = 10  # wall thickness px


def ring(img: np.ndarray, x0: int, y0: int, x1: int, y1: int, gap: tuple[int, int] | None = None) -> None:
    """Closed room of T-px walls; gap = (xa, xb) cuts a door into the bottom wall."""
    img[y0:y1, x0:x1] = 0
    img[y0 + T : y1 - T, x0 + T : x1 - T] = 255
    if gap:
        img[y1 - T : y1, gap[0] : gap[1]] = 255


PYTHON = str(plans.ROOT / ".venv" / "bin" / "python")


def write_inputs(tmp_path: Path, img: np.ndarray) -> Path:
    image = tmp_path / "plan.png"
    cv2.imwrite(str(image), img)
    ini = tmp_path / "floor.ini"
    convert.write_config(ini, image)
    return ini


def sandboxed(tmp_path: Path, args: list[str]) -> None:
    res = subprocess.run(
        sandbox.bwrap_cmd(args, [tmp_path], tmp_path, env={"MPLBACKEND": "Agg"}),
        check=False,
        text=True,
        capture_output=True,
    )
    assert res.returncode == 0, res.stderr[-2000:]


def run_probe(tmp_path: Path, img: np.ndarray, targets: list[str], extra_args: tuple[str, ...] = ()) -> dict:
    ini = write_inputs(tmp_path, img)
    out = tmp_path / "probe.json"
    args = [PYTHON, "-I", str(PROBE), str(ini), "--out", str(out), *extra_args]
    sandboxed(tmp_path, args + [a for t in targets for a in ("--target", t)])
    return json.loads(out.read_text())


ROOM = "room=109.5,230:489.5,230"  # inner faces of ring(100, 80, 500, 380)


def frame(img: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> None:
    """2-px outline: wall_filter's opening erases it, but it is upstream's outer contour (gray < 230)."""
    img[y0:y1, x0:x1] = 0
    img[y0 + 2 : y1 - 2, x0 + 2 : x1 - 2] = 255


def framed_room() -> np.ndarray:
    img = np.full((460, 600), 255, np.uint8)
    frame(img, 92, 72, 508, 388)
    ring(img, 100, 80, 500, 380, gap=(280, 330))
    return img


def test_room_inside_outline_keeps_every_wall_and_face(tmp_path: Path):
    res = run_probe(tmp_path, framed_room(), [ROOM])
    assert res["verdict"] is None, res["reason"]
    assert all(f["present"] for fs in res["targets"][0]["faces"].values() for f in fs)


def test_walls_on_the_outline_are_dropped_by_the_contour_filter(tmp_path: Path):
    img = np.full((460, 600), 255, np.uint8)
    ring(img, 100, 80, 500, 380, gap=(280, 330))  # the previous test's room without the frame
    res = run_probe(tmp_path, img, [ROOM])
    assert min(res["stages"]["S3"]["ratio"]) >= 0.97
    assert (res["verdict"], res["reason"]) == ("S4", "no walls left")


def test_closed_room_loses_its_faces_at_precise_boxes(tmp_path: Path):
    img = np.full((460, 600), 255, np.uint8)
    frame(img, 92, 72, 508, 388)
    ring(img, 100, 80, 500, 380)
    res = run_probe(tmp_path, img, [ROOM])
    faces = res["targets"][0]["faces"]
    assert all(f["present"] for f in faces["S1"])
    assert not any(f["present"] for f in faces["S2"])
    assert res["verdict"] == "S2"


def test_second_block_outside_the_largest_contour_is_dropped(tmp_path: Path):
    img = np.full((460, 620), 255, np.uint8)
    frame(img, 52, 52, 368, 408)
    ring(img, 60, 60, 360, 400, gap=(180, 230))
    frame(img, 412, 52, 568, 208)
    ring(img, 420, 60, 560, 200, gap=(470, 510))
    res = run_probe(tmp_path, img, [])
    assert min(res["stages"]["S2"]["ratio"]) >= 0.97
    assert res["stages"]["S3"]["ratio"][0] < 0.90
    assert res["verdict"] == "S4"
    assert res["stages"]["S4"]["ratio"][0] < 0.90


def test_thin_partition_erased_by_wall_filter_loses_its_face_at_s1(tmp_path: Path):
    img = np.full((460, 800), 255, np.uint8)
    frame(img, 92, 72, 708, 388)
    ring(img, 100, 80, 700, 380, gap=(480, 530))
    img[80 + T : 380 - T, 397:400] = 0  # 3-px partition: below the 3×3 opening ×2, so wall_filter erases it
    res = run_probe(tmp_path, img, ["thin=109.5,230:396.5,230"])  # left inner face → partition face
    near, far = res["targets"][0]["faces"]["S1"]
    assert near["present"]
    assert not far["present"]
    assert res["verdict"] == "S1"


def test_wall_verts_match_kept_boxes_and_a_stale_report_is_skipped(tmp_path: Path):
    img = framed_room()
    ini = write_inputs(tmp_path, img)
    (tmp_path / "Images").symlink_to(convert.UPSTREAM / "Images")  # as convert(): CWD-relative templates
    sandboxed(tmp_path, [PYTHON, "-I", str(plans.ROOT / "scripts" / "ftb_generate.py"), str(ini)])
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"extent_raw_units": [4.0, 3.0, 1.0]}))

    res = run_probe(tmp_path, img, [], ("--report", str(report)))
    stages = res["stages"]
    assert stages["S4"]["bbox"] is not None
    assert stages["S5"]["bbox"] == pytest.approx(stages["S4"]["bbox"])
    assert stages["S6"]["extent"] == pytest.approx([400.0, 300.0])

    verts = tmp_path / "Data" / "0" / "wall_vertical_verts.txt"
    stale = verts.stat().st_mtime - 60
    os.utime(report, (stale, stale))
    res = run_probe(tmp_path, img, [], ("--report", str(report)))
    assert "S6" not in res["stages"]
    assert any(n.startswith("S6 skipped") and "older" in n for n in res["notes"])
