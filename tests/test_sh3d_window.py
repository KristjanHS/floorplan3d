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
    hinge = next(g for g in sw.window_groups(100, 140) if g.name == "sweethome3d_hinge_1")
    xs = [b[1] - b[0] for b in hinge.boxes] + [b[5] - b[4] for b in hinge.boxes]
    ys = [b[3] - b[2] for b in hinge.boxes]
    assert min(ys) > max(xs)  # SH3D takes the hinge's longest bbox dimension as the swing axis


def test_inner_pane_thicker_than_outer() -> None:
    panes = next(g for g in sw.window_groups(100, 140) if g.name == "sweethome3d_window_pane_on_hinge_1").boxes
    inner, outer = sorted(panes, key=lambda b: b[4], reverse=True)
    assert inner[5] - inner[4] == pytest.approx(sw.INNER_GLASS)
    assert outer[5] - outer[4] == pytest.approx(sw.OUTER_GLASS)


def test_too_small_window_rejected() -> None:
    with pytest.raises(ValueError, match="too small"):
        sw.window_groups(30, 140)


def test_parse_size_rejects_garbage() -> None:
    assert sw.parse_size("90.5x140") == (90.5, 140)
    with pytest.raises(Exception, match="<width>x<height>"):
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
