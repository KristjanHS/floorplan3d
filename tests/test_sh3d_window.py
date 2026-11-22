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
    # Both sash layers, blinds, bars, handle and panes swing on hinge 1; no second hinge exists.
    assert special == [
        "sweethome3d_hinge_1",
        "sweethome3d_opening_on_hinge_1",
        "sweethome3d_opening_on_hinge_1_blinds",
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
    assert sw.parse_size("90.5x140") == (90.5, 140, sw.SINGLE)
    vented = sw.parse_size("200x150=36Lv,164R")
    assert sw.parse_size("180x220=90D").leaves == (sw.Leaf(1, "D"),)
    assert vented.leaves == (sw.Leaf(36, "L", vent=True), sw.Leaf(164, "R"))
    for bad in ("200x150=36X", "200x150=0L,10R", "200x150="):
        with pytest.raises(argparse.ArgumentTypeError):
            sw.parse_size(bad)
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


VENTED = sw.Spec(200, 150, (sw.Leaf(36, "D", vent=True), sw.Leaf(164, "R")))
PAIR = sw.Spec(180, 220, (sw.Leaf(1, "D"),))
POSTED = sw.Spec(180, 220, (sw.Leaf(1), sw.Leaf(1, "R")))


def group(groups: list[sw.Group], name: str) -> list[sw.Box]:
    return next(g for g in groups if g.name == name).boxes


def handle_x(groups: list[sw.Group], n: int) -> float:
    boxes = group(groups, f"sweethome3d_opening_on_hinge_{n}_handle")
    return (min(b[0] for b in boxes) + max(b[1] for b in boxes)) / 2


def test_post_at_weight_vent_door_pair_and_glass_leaf() -> None:
    groups = sw.window_groups(*VENTED)
    _, faces = parse_obj(sw.obj_text(groups))
    # Weights summing to the width are the post centre in cm.
    posts = [b for b in group(groups, "frame") if b[2] > 0 and b[3] < VENTED.height and b[1] - b[0] == sw.FRAME]
    assert [(b[0] + b[1]) / 2 for b in posts] == pytest.approx([36])
    # Two vent doors (hinges 1, 2) and the glass leaf (3); only the glass leaf has panes and blinds.
    assert sorted(g for g in faces if g.startswith("sweethome3d_hinge_")) == [
        f"sweethome3d_hinge_{n}" for n in (1, 2, 3)
    ]
    assert [n for n in (1, 2, 3) if f"sweethome3d_window_pane_on_hinge_{n}" in faces] == [3]
    assert [n for n in (1, 2, 3) if f"sweethome3d_opening_on_hinge_{n}_blinds" in faces] == [3]
    # Knobs meet mid-column, and the right-hinged glass leaf's handle sits just past the post: all three together.
    mid = (sw.FRAME + 36 - sw.FRAME / 2) / 2
    assert mid - 5 < handle_x(groups, 1) < mid < handle_x(groups, 2) < mid + 5
    assert 36 < handle_x(groups, 3) < 36 + 10


def test_vent_louvres_fill_outer_layer_mesh_between_layers() -> None:
    groups = sw.window_groups(*VENTED)
    louvres, mesh = group(groups, "vent_louvres_1"), group(groups, "vent_mesh_1")
    assert min(b[4] for b in louvres) == sw.OUTER_Z0 and max(b[5] for b in louvres) == sw.OUTER_Z0 + sw.OUTER_T
    assert all(b[1] <= 36 - sw.FRAME / 2 for b in louvres + mesh)  # stays in the vent column
    assert sw.OUTER_Z0 + sw.OUTER_T < mesh[0][4] and mesh[0][5] < sw.INNER_Z0
    # Slats cover most of the opening height, not a token few.
    assert len(louvres) / 2 * sw.LOUVRE_PITCH >= VENTED.height - 2 * sw.FRAME - sw.LOUVRE_PITCH


def test_blinds_sit_in_the_gap_between_layers() -> None:
    groups = sw.window_groups(*PAIR)
    blinds = group(groups, "sweethome3d_opening_on_hinge_1_blinds")
    assert all(sw.OUTER_Z0 + sw.OUTER_T < b[4] and b[5] < sw.INNER_Z0 for b in blinds)
    assert len(blinds) * sw.BLIND_PITCH > PAIR.height - 2 * (sw.FRAME + sw.OUTER_FACE) - 2 * sw.BLIND_PITCH


@pytest.mark.parametrize("spec", [VENTED, PAIR, POSTED])
def test_multi_leaf_bbox_matches_catalogue_size(spec: sw.Spec) -> None:
    verts, _ = parse_obj(sw.obj_text(sw.window_groups(*spec)))
    entry = dict(line.split("=", 1) for line in sw.catalog_entry(1, *spec))
    size = [max(v[i] for v in verts) - min(v[i] for v in verts) for i in range(3)]
    assert size == pytest.approx([float(entry[f"{k}#1"]) for k in ("width", "height", "depth")])


@pytest.mark.parametrize("spec", [PAIR, POSTED])
def test_right_hinge_mirrors_leaf_and_plan_arc(spec: sw.Spec) -> None:
    groups = sw.window_groups(*spec)
    entry = dict(line.split("=", 1) for line in sw.catalog_entry(1, *spec))
    xs = [float(v) for v in entry["doorOrWindowSashXAxis#1"].split()]
    radii = [float(v) for v in entry["doorOrWindowSashWidth#1"].split()]
    assert entry["doorOrWindowSashStartAngle#1"] == "0 180" and entry["doorOrWindowSashEndAngle#1"] == "-90 270"
    left, right = sw.sashes(spec.width, spec.leaves)
    # Left leaf hinged at its left edge, right leaf at its right edge; each arc reaches the free edge.
    assert xs[0] < left.x0 and xs[1] > right.x1
    assert xs[0] + radii[0] == pytest.approx(left.x1) and xs[1] - radii[1] == pytest.approx(right.x0)
    assert handle_x(groups, 2) < (right.x0 + right.x1) / 2  # handle on the free (left) stile


def test_door_pair_meets_without_post() -> None:
    frame = group(sw.window_groups(*PAIR), "frame")
    assert len(frame) == 4  # outer ring only
    left, right = sw.sashes(PAIR.width, PAIR.leaves)
    assert right.x0 - left.x1 == pytest.approx(sw.CLEAR)


def test_preview_swings_every_sash_toward_the_room() -> None:
    groups = sw.window_groups(*VENTED)
    swings = sw.leaf_swings(groups, 60)
    assert sorted(swings) == [1, 2, 3]
    for n, (ax_x, ax_z, deg) in swings.items():
        sash = group(groups, f"sweethome3d_opening_on_hinge_{n}")
        free_x = max(b[1] for b in sash) if deg > 0 else min(b[0] for b in sash)
        reach = abs(free_x - ax_x)
        assert sw.rotate_y((free_x, 0, ax_z), ax_x, ax_z, deg)[2] > ax_z + reach * 0.8


def test_too_narrow_columns_rejected() -> None:
    with pytest.raises(ValueError, match="too small"):
        sw.check_size(200, 150, (sw.Leaf(20, "D", vent=True), sw.Leaf(180, "R")))
    with pytest.raises(ValueError, match="too small"):
        sw.check_size(200, 150, (sw.Leaf(30), sw.Leaf(170, "R")))  # 30 is too narrow for a glazed leaf
    sw.check_size(*VENTED)
