"""Coupled double-sash wooden window -> SweetHome3D furniture library (.sh3f).

Nordic coupled window: a wooden frame holding two sash layers that open together, inward, side-hung
(turn only). The inner sash carries a double-glazed unit, the outer sash a single pane, and metal
coupling bars on the top and bottom edges join the two layers. One catalogue entry per --size, each
built at its exact size so frame and sash profiles keep their real widths (SH3D would otherwise
stretch one model's profiles with the piece).

SH3D naming: the coupled sash, its handle and both panes rotate around `sweethome3d_hinge_1`
(ModelManager.HINGE_PREFIX / OPENING_ON_HINGE_PREFIX / WINDOW_PANE_ON_HINGE_PREFIX); the axis is the
hinge parts' longest bbox dimension through their centre. Model axes: x = width (hinge at x=0 side),
y = up, z = depth, back (exterior) at z=0, front (room side, where the sash swings) at z=depth.
"""

import argparse
import io
import math
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from matplotlib.figure import Figure
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from mpl_toolkits.mplot3d.axes3d import Axes3D

Vec = tuple[float, float, float]
Box = tuple[float, float, float, float, float, float]  # x0, x1, y0, y1, z0, z1

# Profile dimensions, cm.
DEPTH = 14.0  # frame depth, exterior to room face
FRAME = 6.0  # frame face width
STOP = 1.5  # exterior stop lip, reaching into the opening
STOP_DEPTH = 1.0
CLEAR = 0.3  # sash-to-frame gap
INNER_FACE, INNER_T = 5.5, 5.5  # inner sash face width, thickness
OUTER_FACE, OUTER_T = 5.0, 4.5
INNER_GLASS, OUTER_GLASS = 2.4, 0.4
GLASS_BITE = 1.0  # glass edge hidden inside the sash profile
OUTER_Z0 = STOP_DEPTH + 0.2  # outer sash back face
INNER_Z0 = DEPTH - INNER_T  # inner sash flush with the frame's room face
HINGE = 1.0  # hinge knuckle cross-section
HINGE_LEN = 8.0
HINGE_INSET = 15.0  # hinge distance from the sash top/bottom
HANDLE_PROJ = 5.0  # handle reach past the room face; part of the model's bbox depth
MODEL_DEPTH = DEPTH + HANDLE_PROJ
ELEVATION = 90.0  # default sill height

MATERIALS = {
    "wood": ("0.78 0.55 0.32", "0.15 0.15 0.15", 1.0),
    "hardware": ("0.88 0.88 0.86", "0.40 0.40 0.40", 1.0),
    "handle": ("0.80 0.80 0.80", "0.60 0.60 0.60", 1.0),
    "glass": ("0.80 0.88 0.90", "0.80 0.80 0.80", 0.3),
}
NORMALS: list[Vec] = [(-1, 0, 0), (1, 0, 0), (0, -1, 0), (0, 1, 0), (0, 0, -1), (0, 0, 1)]


@dataclass
class Group:
    name: str
    material: str
    boxes: list[Box] = field(default_factory=list)


def ring(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float, w: float) -> list[Box]:
    """Rectangular frame of four members, face width w, outer edge at the given bounds."""
    return [
        (x0, x0 + w, y0, y1, z0, z1),
        (x1 - w, x1, y0, y1, z0, z1),
        (x0 + w, x1 - w, y0, y0 + w, z0, z1),
        (x0 + w, x1 - w, y1 - w, y1, z0, z1),
    ]


def window_groups(width: float, height: float) -> list[Group]:
    if width < 2 * (FRAME + INNER_FACE) + 10 or height < 2 * (FRAME + INNER_FACE) + 10:
        raise ValueError(f"window {width:g}x{height:g} cm is too small for its profiles")
    sx0, sx1 = FRAME + CLEAR, width - FRAME - CLEAR  # sash outer bounds
    sy0, sy1 = FRAME + CLEAR, height - FRAME - CLEAR
    inner_z1 = INNER_Z0 + INNER_T
    outer_z1 = OUTER_Z0 + OUTER_T

    def pane(face: float, z0: float, t: float, glass: float) -> Box:
        zc = z0 + t / 2
        e = face - GLASS_BITE
        return (sx0 + e, sx1 - e, sy0 + e, sy1 - e, zc - glass / 2, zc + glass / 2)

    # Hinge knuckles sit in the sash-to-frame gap at the room face; their centre is the swing axis.
    hx0, hx1 = FRAME - 0.7, FRAME - 0.7 + HINGE
    hz0, hz1 = inner_z1 - 0.1 - HINGE, inner_z1 - 0.1
    hinges = [
        (hx0, hx1, y, y + HINGE_LEN, hz0, hz1)
        for y in (sy0 + HINGE_INSET - HINGE_LEN / 2, sy1 - HINGE_INSET - HINGE_LEN / 2)
    ]

    # Coupling bars: flat straps on the top and bottom sash edges bridging the gap between layers,
    # one near each side.
    bar_w, bar_t = 2.5, 0.3
    bars = [
        (x, x + bar_w, y0, y1, OUTER_Z0 + 1.0, inner_z1 - 1.0)
        for x in (sx0 + 8.0, sx1 - 8.0 - bar_w)
        for y0, y1 in ((sy0 - bar_t, sy0), (sy1, sy1 + bar_t))
    ]

    # Handle on the inner sash's free stile, lever pointing down (closed).
    hcx = sx1 - INNER_FACE / 2
    hcy = (sy0 + sy1) / 2
    handle = [
        (hcx - 1.5, hcx + 1.5, hcy - 3.5, hcy + 3.5, inner_z1, inner_z1 + 0.8),  # rosette
        (hcx - 0.6, hcx + 0.6, hcy - 0.6, hcy + 0.6, inner_z1 + 0.8, inner_z1 + 3.5),  # neck
        (hcx - 0.8, hcx + 0.8, hcy - 12.0, hcy + 0.8, inner_z1 + 3.5, inner_z1 + HANDLE_PROJ),  # lever
    ]

    return [
        Group(
            "frame",
            "wood",
            ring(0, width, 0, height, 0, DEPTH, FRAME)
            + ring(FRAME - 0.5, width - FRAME + 0.5, FRAME - 0.5, height - FRAME + 0.5, 0, STOP_DEPTH, STOP + 0.5),
        ),
        Group("sweethome3d_hinge_1", "hardware", hinges),
        Group(
            "sweethome3d_opening_on_hinge_1",
            "wood",
            ring(sx0, sx1, sy0, sy1, INNER_Z0, inner_z1, INNER_FACE)
            + ring(sx0, sx1, sy0, sy1, OUTER_Z0, outer_z1, OUTER_FACE),
        ),
        Group("sweethome3d_opening_on_hinge_1_coupling", "hardware", bars),
        Group("sweethome3d_opening_on_hinge_1_handle", "handle", handle),
        Group(
            "sweethome3d_window_pane_on_hinge_1",
            "glass",
            [pane(INNER_FACE, INNER_Z0, INNER_T, INNER_GLASS), pane(OUTER_FACE, OUTER_Z0, OUTER_T, OUTER_GLASS)],
        ),
    ]


def box_faces(b: Box) -> list[tuple[int, list[Vec]]]:
    """Six quads of a box as (normal index, corners), wound counter-clockwise seen from outside."""
    x0, x1, y0, y1, z0, z1 = b
    quads = [
        [(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)],
        [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)],
        [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)],
        [(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)],
        [(x0, y0, z0), (x0, y1, z0), (x1, y1, z0), (x1, y0, z0)],
        [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)],
    ]
    return list(enumerate(quads))


def obj_text(groups: list[Group]) -> str:
    """OBJ with CRLF line ends and a final CRLF (SH3D's OBJLoader face loop needs the last EOL)."""
    lines = [
        "# Coupled double-sash wooden window, generated by floorplan3d scripts/sh3d_window.py",
        "mtllib window.mtl",
    ]
    lines += [f"vn {x} {y} {z}" for x, y, z in NORMALS]
    n = 0
    for g in groups:
        lines += [f"g {g.name}", f"usemtl {g.material}"]
        for b in g.boxes:
            for ni, quad in box_faces(b):
                lines += [f"v {x:.3f} {y:.3f} {z:.3f}" for x, y, z in quad]
                lines.append("f " + " ".join(f"{n + i + 1}//{ni + 1}" for i in range(4)))
                n += 4
    return "\r\n".join(lines) + "\r\n"


def mtl_text() -> str:
    lines = []
    for name, (kd, ks, d) in MATERIALS.items():
        lines += [f"newmtl {name}", "Ka 0.2 0.2 0.2", f"Kd {kd}", f"Ks {ks}", "Ns 30", f"d {d}", "illum 2", ""]
    return "\r\n".join(lines) + "\r\n"


def rotate_y(p: Vec, axis_x: float, axis_z: float, deg: float) -> Vec:
    """Swing a point around the vertical hinge axis; positive degrees open toward the room (+z)."""
    a = math.radians(deg)
    dx, dz = p[0] - axis_x, p[2] - axis_z
    return (axis_x + dx * math.cos(a) - dz * math.sin(a), p[1], axis_z + dx * math.sin(a) + dz * math.cos(a))


def render_png(groups: list[Group], width: float, height: float, size_px: int, open_deg: float = 0.0) -> bytes:
    """Shaded view from the room side; open_deg swings the sash for a preview."""
    hinge = next(g for g in groups if g.name == "sweethome3d_hinge_1").boxes
    ax_x = (min(b[0] for b in hinge) + max(b[1] for b in hinge)) / 2
    ax_z = (min(b[4] for b in hinge) + max(b[5] for b in hinge)) / 2
    light = (0.35, 0.5, 0.8)
    polys, colours = [], []
    for g in groups:
        moving = g.name.startswith(("sweethome3d_opening_on_hinge_1", "sweethome3d_window_pane_on_hinge_1"))
        r, gr, bl = (float(c) for c in MATERIALS[g.material][0].split())
        alpha = MATERIALS[g.material][2]
        for b in g.boxes:
            for ni, quad in box_faces(b):
                nrm = NORMALS[ni]
                if moving and open_deg:
                    quad = [rotate_y(p, ax_x, ax_z, open_deg) for p in quad]
                    a = math.radians(open_deg)
                    nrm = (
                        nrm[0] * math.cos(a) - nrm[2] * math.sin(a),
                        nrm[1],
                        nrm[0] * math.sin(a) + nrm[2] * math.cos(a),
                    )
                shade = 0.45 + 0.55 * max(0.0, sum(n * li for n, li in zip(nrm, light, strict=True)))
                # Plot axes: x = width, y = -depth (room side toward the viewer), z = up.
                polys.append([(x, -z, y) for x, y, z in quad])
                colours.append((r * shade, gr * shade, bl * shade, alpha))
    span = max(width, height) * 1.05
    fig = Figure(figsize=(size_px / 100, size_px / 100), dpi=100)
    ax = cast(Axes3D, fig.add_axes((0, 0, 1, 1), projection="3d"))
    ax.add_collection3d(Poly3DCollection(polys, facecolors=colours, edgecolors=(0, 0, 0, 0.15), linewidths=0.2))
    cx, cy = width / 2, height / 2
    ax.set_xlim(cx - span / 2, cx + span / 2)
    ax.set_ylim(-span / 2 - DEPTH / 2, span / 2 - DEPTH / 2)
    ax.set_zlim(cy - span / 2, cy + span / 2)
    ax.set_box_aspect((1, 1, 1))
    ax.set_proj_type("ortho")
    ax.view_init(elev=12, azim=-70)
    ax.set_axis_off()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", transparent=True)
    return buf.getvalue()


def parse_size(text: str) -> tuple[float, float]:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)", text)
    if not m:
        raise argparse.ArgumentTypeError(f"size '{text}' must be <width>x<height> in cm, e.g. 100x140")
    return float(m.group(1)), float(m.group(2))


def size_tag(width: float, height: float) -> str:
    return f"{width:g}x{height:g}".replace(".", "_")


def catalog_entry(i: int, width: float, height: float) -> list[str]:
    tag = size_tag(width, height)
    opening = width - 2 * FRAME
    keys = {
        "id": f"floorplan3d#coupledWindow{tag}",
        "name": f"Coupled wooden window {width:g}x{height:g}",
        "category": "Doors and windows",
        "tags": "window, wood, coupled, double sash",
        "icon": f"/coupledWindow{tag}/icon.png",
        "model": f"/coupledWindow{tag}/window.obj",
        "multiPartModel": "true",
        "width": f"{width:g}",
        "depth": f"{MODEL_DEPTH:g}",  # SH3D scales the bbox to this: must include the handle
        "height": f"{height:g}",
        "elevation": f"{ELEVATION:g}",
        "movable": "false",
        "doorOrWindow": "true",
        # The frame keeps its own depth inside any wall; the cut-out still runs through the wall.
        "doorOrWindowWallThickness": f"{DEPTH:g}",
        "doorOrWindowWallDistance": "0",
        "doorOrWindowWidthDepthDeformable": "false",
        "doorOrWindowWallCutOutOnBothSides": "true",
        "doorOrWindowSashXAxis": f"{FRAME:g}",
        "doorOrWindowSashYAxis": f"{DEPTH:g}",
        "doorOrWindowSashWidth": f"{opening:g}",
        "doorOrWindowSashStartAngle": "0",
        "doorOrWindowSashEndAngle": "-90",
        "creator": "floorplan3d",
    }
    return [f"{k}#{i}={v}" for k, v in keys.items()]


def build_library(sizes: list[tuple[float, float]], out: Path) -> None:
    props = [
        "id=floorplan3d-coupled-windows",
        "name=Coupled wooden windows",
        "description=Nordic coupled double-sash wooden windows, inward side-hung",
        "version=1.0",
        "provider=floorplan3d",
        "",
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, (w, h) in enumerate(sizes, start=1):
            groups = window_groups(w, h)
            d = f"coupledWindow{size_tag(w, h)}"
            zf.writestr(f"{d}/window.obj", obj_text(groups))
            zf.writestr(f"{d}/window.mtl", mtl_text())
            zf.writestr(f"{d}/icon.png", render_png(groups, w, h, 128))
            props += [*catalog_entry(i, w, h), ""]
        zf.writestr("PluginFurnitureCatalog.properties", "\n".join(props))
    tmp.replace(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sh3d_window", description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument(
        "--size", type=parse_size, action="append", help="<width>x<height> cm; repeatable (default 100x140)"
    )
    parser.add_argument("--out", type=Path, default=Path("output/sh3d/coupled-windows.sh3f"))
    parser.add_argument("--preview", type=Path, help="also render the first size, sash half open, to this PNG")
    args = parser.parse_args(argv)
    sizes = list(dict.fromkeys(args.size or [(100.0, 140.0)]))
    try:
        build_library(sizes, args.out)
    except ValueError as e:
        parser.error(str(e))
    print(f"wrote {args.out} ({len(sizes)} window{'s' * (len(sizes) > 1)})")
    if args.preview:
        w, h = sizes[0]
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        args.preview.write_bytes(render_png(window_groups(w, h), w, h, 600, open_deg=60))
        print(f"wrote {args.preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
