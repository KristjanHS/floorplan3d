import argparse
import re
import zipfile
from pathlib import Path

import pytest

import sh3d_window as sw


def parse_obj(text: str) -> tuple[list[tuple[float, ...]], dict[str, list[list[int]]]]:
    verts: list[tuple[float, ...]] = []
    faces: dict[str, list[list[int]]] = {}
    group = ""
    for line in text.split("\r\n"):
        if line.startswith("v "):
            verts.append(tuple(float(t) for t in line.split()[1:]))
        elif line.startswith("g "):
            group = line[2:]
        elif line.startswith("f "):
            faces.setdefault(group, []).append([int(t.split("/")[0]) for t in line.split()[1:]])
    return verts, faces


def test_obj_is_crlf_terminated_and_indices_valid() -> None:
    text = sw.obj_text(sw.window_groups(100, 140))
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")
    verts, faces = parse_obj(text)
    assert all(1 <= i <= len(verts) for fs in faces.values() for f in fs for i in f)


def test_bbox_matches_catalogue_size() -> None:
    # SH3D scales the model bbox to the catalogue width/height/depth: any mismatch squashes the profiles.
    verts, _ = parse_obj(sw.obj_text(sw.window_groups(80, 120)))
    lo = [min(v[i] for v in verts) for i in range(3)]
    hi = [max(v[i] for v in verts) for i in range(3)]
    entry = dict(line.split("=", 1) for line in sw.catalog_entry(1, 80, 120))
    expected = [float(entry[f"{k}#1"]) for k in ("width", "height", "depth")]
    assert [h - lo_ for lo_, h in zip(lo, hi, strict=True)] == pytest.approx(expected)


def test_one_coupled_opening_on_one_hinge() -> None:
    _, faces = parse_obj(sw.obj_text(sw.window_groups(100, 140)))
    special = sorted(g for g in faces if g.startswith("sweethome3d_"))
    # Both sash layers, bars, handle and panes swing on hinge 1; no second hinge exists.
    assert special == [
        "sweethome3d_hinge_1",
        "sweethome3d_opening_on_hinge_1",
        "sweethome3d_opening_on_hinge_1_coupling",
        "sweethome3d_opening_on_hinge_1_handle",
        "sweethome3d_window_pane_on_hinge_1",
    ]
    # SH3D swings around the longest dimension of all hinge parts' combined bbox: must be vertical.
    hinge = next(g for g in sw.window_groups(100, 140) if g.name == "sweethome3d_hinge_1").boxes
    size = [max(b[2 * k + 1] for b in hinge) - min(b[2 * k] for b in hinge) for k in range(3)]
    assert size[1] > max(size[0], size[2])


def test_plan_arc_matches_hinge_side_and_reach() -> None:
    # EndAngle -90 draws the arc from the left (x=0) side: the hinge must be there, the handle opposite,
    # and the arc radius must reach the sash's free edge.
    groups = sw.window_groups(100, 140)
    entry = dict(line.split("=", 1) for line in sw.catalog_entry(1, 100, 140))
    handle = next(g for g in groups if g.name.endswith("_handle")).boxes
    sash = next(g for g in groups if g.name == "sweethome3d_opening_on_hinge_1").boxes
    ax_x = float(entry["doorOrWindowSashXAxis#1"])
    assert entry["doorOrWindowSashEndAngle#1"] == "-90"
    assert ax_x < 50 < min(b[0] for b in handle)
    assert ax_x + float(entry["doorOrWindowSashWidth#1"]) == pytest.approx(max(b[1] for b in sash))


def test_double_glass_inside_inner_layer_single_inside_outer() -> None:
    groups = sw.window_groups(100, 140)
    panes = next(g for g in groups if g.name == "sweethome3d_window_pane_on_hinge_1").boxes
    sash = next(g for g in groups if g.name == "sweethome3d_opening_on_hinge_1").boxes
    layers = sorted({(b[4], b[5]) for b in sash})  # (z0, z1) of the outer, then the inner layer
    assert len(layers) == 2
    for (z0, z1), pane, glass in zip(layers, sorted(panes, key=lambda b: b[4]), (0.4, 2.4), strict=True):
        assert z0 < pane[4] and pane[5] < z1
        assert pane[5] - pane[4] == pytest.approx(glass)


def test_faces_wound_counter_clockwise_around_their_normal() -> None:
    for g in sw.window_groups(100, 140):
        for b in g.boxes:
            for ni, (p0, p1, p2, _) in sw.box_faces(b):
                u = [p1[k] - p0[k] for k in range(3)]
                v = [p2[k] - p0[k] for k in range(3)]
                cross = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
                assert sum(c * n for c, n in zip(cross, sw.NORMALS[ni], strict=True)) > 0


def test_bad_size_rejected_without_partial_file(tmp_path: Path) -> None:
    out = tmp_path / "lib.sh3f"
    with pytest.raises(SystemExit):
        sw.main(["--size", "100x140", "--size", "30x140", "--out", str(out)])
    assert list(tmp_path.iterdir()) == []


def test_parse_size_rejects_garbage() -> None:
    assert sw.parse_size("90.5x140") == (90.5, 140)
    with pytest.raises(argparse.ArgumentTypeError, match="<width>x<height>"):
        sw.parse_size("90*140")


def test_library_zip_entries_and_catalogue(tmp_path: Path) -> None:
    out = tmp_path / "lib.sh3f"
    assert sw.main(["--size", "100x140", "--size", "60.5x90", "--out", str(out)]) == 0
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        props = zf.read("PluginFurnitureCatalog.properties").decode("ascii")
        for i in (1, 2):
            for key in ("model", "icon"):
                path = re.search(rf"^{key}#{i}=/(.+)$", props, re.MULTILINE)
                assert path and path.group(1) in names
            assert zf.read(re.search(rf"^icon#{i}=/(.+)$", props, re.MULTILINE).group(1)).startswith(b"\x89PNG")  # type: ignore[union-attr]
    assert "width#2=60.5" in props and "height#2=90" in props
    assert "doorOrWindowWidthDepthDeformable#1=false" in props
    assert not out.with_suffix(".tmp").exists()
