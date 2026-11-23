"""Coupled double-sash wooden window -> SweetHome3D furniture library (.sh3f).

Nordic coupled window: a wooden frame holding two sash layers that open together, inward, side-hung
(turn only). The inner sash carries a double-glazed unit, the outer sash a single pane, and metal
coupling bars on the top and bottom edges join the two layers. One catalogue entry per --size, each
built at its exact size so frame and sash profiles keep their real widths (SH3D would otherwise
stretch one model's profiles with the piece).

A size can split into columns (leaves) separated by fixed centre posts: `200x150=36Dv,164R` puts the
post centre at 36 cm (weights scale to the width) and hinges the 164 leaf right; `D` splits a column
into two half-width doors hinged left and right that meet in the middle without a post (cabinet style).
`v` makes a vent column: glass-less solid wooden doors, a fixed insect mesh in the middle layer and
fixed exterior louvres in place of the outer sash. A single vent leaf (`L`/`R`) carries the glazed
leaves' lever handle; a `D` vent pair carries knobs. Glazed leaves carry an open venetian blind between
their layers; `u` raises it (`130Ru`): all its slats stack in a pack at the top of the opening.

SH3D naming: leaf N's sash, handle and panes rotate around `sweethome3d_hinge_N`
(ModelManager.HINGE_PREFIX / OPENING_ON_HINGE_PREFIX / WINDOW_PANE_ON_HINGE_PREFIX); the axis is the
hinge parts' longest bbox dimension through their centre. Model axes: x = width, y = up, z = depth,
back (exterior) at z=0, front (room side, where the sashes swing) at z=depth.
"""

import argparse
import io
import math
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple, cast

from matplotlib.figure import Figure
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from mpl_toolkits.mplot3d.axes3d import Axes3D

Vec = tuple[float, float, float]
Box = tuple[float, float, float, float, float, float]  # x0, x1, y0, y1, z0, z1

# Profile dimensions, cm.
DEPTH = 13.0  # both sash layers together = frame depth, exterior to room face
FRAME = 6.0  # frame face width, also the centre post width
CLEAR = 0.3  # sash-to-frame gap
INNER_FACE, INNER_T = 5.5, 5.5  # inner sash face width, thickness
OUTER_FACE, OUTER_T = 5.0, 4.5
INNER_GLASS, OUTER_GLASS = 2.4, 0.4
GLASS_BITE = 1.0  # glass edge hidden inside the sash profile
OUTER_Z0 = 0.0  # outer sash flush with the frame's exterior face
INNER_Z0 = DEPTH - INNER_T  # inner sash flush with the frame's room face
HINGE = 1.0  # hinge knuckle cross-section
HINGE_LEN = 8.0
HINGE_INSET = 15.0  # hinge distance from the sash top/bottom
HANDLE_PROJ = 4.5  # handle reach past the room face; part of the model's bbox depth
MODEL_DEPTH = DEPTH + HANDLE_PROJ
BAR_W, BAR_INSET = 2.5, 8.0  # coupling bar width, distance from the sash side
PANEL_T = 2.0  # vent door: solid wooden slab flush with the room face
KNOB = 2.4  # vent door knob, square cross-section
VENT_MIN = 8.0  # narrowest vent door that still fits its knob clear of the hinge
MESH_T = 0.1
LOUVRE_T, LOUVRE_PITCH = 1.0, 4.0  # each slat steps down by LOUVRE_T toward the exterior
BLIND_T, BLIND_PITCH = 0.2, 2.2  # venetian blind slats between the two sash layers, open (horizontal)
BLIND_STACK_PITCH = 0.41  # raised blind: slat spacing in the top pack (pack depth = slat count x this)
ELEVATION = 90.0  # default sill height

MATERIALS = {
    "wood": ("0.78 0.55 0.32", "0.15 0.15 0.15", 1.0),
    "hardware": ("0.88 0.88 0.86", "0.40 0.40 0.40", 1.0),
    "handle": ("0.80 0.80 0.80", "0.60 0.60 0.60", 1.0),
    "glass": ("0.80 0.88 0.90", "0.80 0.80 0.80", 0.3),
    "mesh": ("0.20 0.20 0.20", "0.05 0.05 0.05", 0.5),
    "blind": ("0.85 0.75 0.58", "0.10 0.10 0.10", 1.0),
}
NORMALS: list[Vec] = [(-1, 0, 0), (1, 0, 0), (0, -1, 0), (0, 1, 0), (0, 0, -1), (0, 0, 1)]
SWINGING = re.compile(r"sweethome3d_(?:opening|window_pane)_on_hinge_(\d+)")


class Leaf(NamedTuple):
    """One column between frame/posts. hinge L/R: one leaf hinged on that side; D: two half-width
    doors hinged left and right, meeting in the middle without a post."""

    weight: float
    hinge: str = "L"
    vent: bool = False
    raised: bool = False  # glazed leaf: blind stacked at the top


SINGLE = (Leaf(1.0),)  # one leaf hinged left: a plain --size


class Spec(NamedTuple):
    width: float
    height: float
    leaves: tuple[Leaf, ...] = SINGLE


class Sash(NamedTuple):
    """One swinging part: its x span, hinge side and kind. Numbered 1.. in model order (hinge N)."""

    x0: float
    x1: float
    right: bool
    vent: bool
    lever: bool = True  # lever handle; False = vent-pair knob
    raised: bool = False


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


def post_centres(width: float, leaves: tuple[Leaf, ...]) -> list[float]:
    total = sum(leaf.weight for leaf in leaves)
    acc, out = 0.0, []
    for leaf in leaves[:-1]:
        acc += leaf.weight
        out.append(width * acc / total)
    return out


def column_spans(width: float, leaves: tuple[Leaf, ...]) -> list[tuple[float, float]]:
    """(x0, x1) of each column's opening between the frame and the posts."""
    edges = [FRAME] + [e for p in post_centres(width, leaves) for e in (p - FRAME / 2, p + FRAME / 2)] + [width - FRAME]
    return [(edges[2 * i], edges[2 * i + 1]) for i in range(len(leaves))]


def sashes(width: float, leaves: tuple[Leaf, ...]) -> list[Sash]:
    out = []
    for leaf, (cx0, cx1) in zip(leaves, column_spans(width, leaves), strict=True):
        sx0, sx1 = cx0 + CLEAR, cx1 - CLEAR
        if leaf.hinge == "D":
            mid = (sx0 + sx1) / 2
            lever = not leaf.vent
            out += [
                Sash(sx0, mid - CLEAR / 2, False, leaf.vent, lever, leaf.raised),
                Sash(mid + CLEAR / 2, sx1, True, leaf.vent, lever, leaf.raised),
            ]
        else:
            out.append(Sash(sx0, sx1, leaf.hinge == "R", leaf.vent, True, leaf.raised))
    return out


def check_size(width: float, height: float, leaves: tuple[Leaf, ...] = SINGLE) -> None:
    """Smallest size whose coupling bars (vent doors: knob) and hinge knuckles don't run into each other."""
    if len(sashes(width, leaves)) > 9:
        # SH3D matches sash parts by name prefix, so sweethome3d_hinge_1 would also claim hinge_10.
        raise ValueError(f"window {spec_label(Spec(width, height, leaves))} has more than 9 sashes")
    too_narrow = any(s.x1 - s.x0 < (VENT_MIN if s.vent else 2 * (BAR_INSET + BAR_W) + 5) for s in sashes(width, leaves))
    if too_narrow or height < 2 * (FRAME + CLEAR + HINGE_INSET) + HINGE_LEN:
        raise ValueError(f"window {spec_label(Spec(width, height, leaves))} cm is too small for its profiles")


def mirror(boxes: list[Box], x0: float, x1: float) -> list[Box]:
    return [(x0 + x1 - b[1], x0 + x1 - b[0], *b[2:]) for b in boxes]


def sash_groups(n: int, s: Sash, height: float) -> list[Group]:
    """Sash N's swinging groups, built hinged left on its span and mirrored for a right hinge."""
    sx0, sx1 = s.x0, s.x1
    sy0, sy1 = FRAME + CLEAR, height - FRAME - CLEAR
    inner_z1 = INNER_Z0 + INNER_T
    outer_z1 = OUTER_Z0 + OUTER_T
    hcy = (sy0 + sy1) / 2
    prefix = f"sweethome3d_opening_on_hinge_{n}"
    # Lever handle on the free stile, pointing down (closed).
    hcx = sx1 - INNER_FACE / 2
    lever = [
        (hcx - 1.5, hcx + 1.5, hcy - 3.5, hcy + 3.5, inner_z1, inner_z1 + 0.8),  # rosette
        (hcx - 0.6, hcx + 0.6, hcy - 0.6, hcy + 0.6, inner_z1 + 0.8, inner_z1 + 3.5),  # neck
        (hcx - 0.8, hcx + 0.8, hcy - 12.0, hcy + 0.8, inner_z1 + 3.5, inner_z1 + HANDLE_PROJ),  # lever
    ]

    # Hinge knuckles sit in the sash-to-frame gap at the room face; their centre is the swing axis.
    hx0 = sx0 - CLEAR - 0.7
    hz0, hz1 = inner_z1 - 0.1 - HINGE, inner_z1 - 0.1
    hinges = Group(
        f"sweethome3d_hinge_{n}",
        "hardware",
        [
            (hx0, hx0 + HINGE, y, y + HINGE_LEN, hz0, hz1)
            for y in (sy0 + HINGE_INSET - HINGE_LEN / 2, sy1 - HINGE_INSET - HINGE_LEN / 2)
        ],
    )

    if s.vent:
        # A solid wooden door with a lever or knob on its free edge; mesh and louvres stay in the frame.
        kx = sx1 - 1.5 - KNOB / 2
        knob = [
            (kx - 1.5, kx + 1.5, hcy - 1.5, hcy + 1.5, inner_z1, inner_z1 + 0.5),  # rosette
            (kx - KNOB / 2, kx + KNOB / 2, hcy - KNOB / 2, hcy + KNOB / 2, inner_z1 + 0.5, inner_z1 + HANDLE_PROJ),
        ]
        groups = [
            hinges,
            Group(prefix, "wood", [(sx0, sx1, sy0, sy1, inner_z1 - PANEL_T, inner_z1)]),
            Group(f"{prefix}_handle", "handle", lever if s.lever else knob),
        ]
    else:

        def pane(face: float, z0: float, t: float, thick: float) -> Box:
            zc = z0 + t / 2
            e = face - GLASS_BITE
            return (sx0 + e, sx1 - e, sy0 + e, sy1 - e, zc - thick / 2, zc + thick / 2)

        # Coupling bars: flat straps on the top and bottom sash edges bridging the gap between layers,
        # one near each side.
        bar_t = 0.3
        bars = [
            (x, x + BAR_W, y0, y1, OUTER_Z0 + 1.0, inner_z1 - 1.0)
            for x in (sx0 + BAR_INSET, sx1 - BAR_INSET - BAR_W)
            for y0, y1 in ((sy0 - bar_t, sy0), (sy1, sy1 + bar_t))
        ]
        # Venetian blind in the air gap between the layers, hung inside the outer sash's opening.
        bx0, bx1, by0, by1 = sx0 + OUTER_FACE, sx1 - OUTER_FACE, sy0 + OUTER_FACE, sy1 - OUTER_FACE
        bz0, bz1 = outer_z1 + 0.25, INNER_Z0 - 0.25
        # Raised: the same slats stacked down from the top of the opening.
        slats = range(int((by1 - by0) // BLIND_PITCH))
        ys = (
            [by1 - (k + 1) * BLIND_STACK_PITCH for k in slats]
            if s.raised
            else [by0 + BLIND_PITCH / 2 + k * BLIND_PITCH for k in slats]
        )
        blinds = [(bx0, bx1, y, y + BLIND_T, bz0, bz1) for y in ys]
        groups = [
            hinges,
            Group(
                prefix,
                "wood",
                ring(sx0, sx1, sy0, sy1, INNER_Z0, inner_z1, INNER_FACE)
                + ring(sx0, sx1, sy0, sy1, OUTER_Z0, outer_z1, OUTER_FACE),
            ),
            Group(f"{prefix}_coupling", "hardware", bars),
            Group(f"{prefix}_handle", "handle", lever),
            Group(f"{prefix}_blinds", "blind", blinds),
            Group(
                f"sweethome3d_window_pane_on_hinge_{n}",
                "glass",
                [pane(INNER_FACE, INNER_Z0, INNER_T, INNER_GLASS), pane(OUTER_FACE, OUTER_Z0, OUTER_T, OUTER_GLASS)],
            ),
        ]
    if s.right:
        for g in groups:
            g.boxes = mirror(g.boxes, sx0, sx1)
    return groups


def vent_fixed(i: int, cx0: float, cx1: float, height: float) -> list[Group]:
    """Column i's fixed vent parts: insect mesh between the layers, rain/sun louvres in the outer layer."""
    cy0, cy1 = FRAME, height - FRAME
    outer_z1 = OUTER_Z0 + OUTER_T
    zm = (outer_z1 + INNER_Z0) / 2
    # Each slat is two steps, higher at the room side, so rain runs off to the exterior.
    zh = (OUTER_Z0 + outer_z1) / 2
    louvres = [
        box
        for k in range(int((cy1 - cy0 - LOUVRE_T) // LOUVRE_PITCH))
        for y in (cy0 + LOUVRE_PITCH / 2 + k * LOUVRE_PITCH,)
        for box in (
            (cx0, cx1, y, y + LOUVRE_T, OUTER_Z0, zh),
            (cx0, cx1, y + LOUVRE_T, y + 2 * LOUVRE_T, zh, outer_z1),
        )
    ]
    return [
        Group(f"vent_mesh_{i}", "mesh", [(cx0, cx1, cy0, cy1, zm - MESH_T / 2, zm + MESH_T / 2)]),
        Group(f"vent_louvres_{i}", "wood", louvres),
    ]


def window_groups(width: float, height: float, leaves: tuple[Leaf, ...] = SINGLE) -> list[Group]:
    check_size(width, height, leaves)
    posts = [(p - FRAME / 2, p + FRAME / 2, FRAME, height - FRAME, 0.0, DEPTH) for p in post_centres(width, leaves)]
    groups = [Group("frame", "wood", ring(0, width, 0, height, 0, DEPTH, FRAME) + posts)]
    for i, (leaf, (cx0, cx1)) in enumerate(zip(leaves, column_spans(width, leaves), strict=True), start=1):
        if leaf.vent:
            groups += vent_fixed(i, cx0, cx1, height)
    for n, s in enumerate(sashes(width, leaves), start=1):
        groups += sash_groups(n, s, height)
    return groups


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
    """Swing a point around a vertical hinge axis; positive degrees move +x of the axis toward the room (+z)."""
    a = math.radians(deg)
    dx, dz = p[0] - axis_x, p[2] - axis_z
    return (axis_x + dx * math.cos(a) - dz * math.sin(a), p[1], axis_z + dx * math.sin(a) + dz * math.cos(a))


def leaf_swings(groups: list[Group], open_deg: float) -> dict[int, tuple[float, float, float]]:
    """Per leaf N: (axis x, axis z, degrees) that open its sash toward the room by open_deg."""
    swings = {}
    for g in groups:
        m = re.fullmatch(r"sweethome3d_opening_on_hinge_(\d+)", g.name)
        if m:
            n = int(m.group(1))
            ax_x, ax_z = hinge_axis(groups, n)
            centre = (min(b[0] for b in g.boxes) + max(b[1] for b in g.boxes)) / 2
            swings[n] = (ax_x, ax_z, open_deg if centre > ax_x else -open_deg)
    return swings


def render_png(groups: list[Group], width: float, height: float, size_px: int, open_deg: float = 0.0) -> bytes:
    """Shaded view from the room side; open_deg swings every sash for a preview."""
    swings = leaf_swings(groups, open_deg)
    light = (0.35, 0.5, 0.8)
    polys, colours = [], []
    for g in groups:
        m = SWINGING.match(g.name)
        swing = swings[int(m.group(1))] if m and open_deg else None
        r, gr, bl = (float(c) for c in MATERIALS[g.material][0].split())
        alpha = MATERIALS[g.material][2]
        for b in g.boxes:
            for ni, quad in box_faces(b):
                nrm = NORMALS[ni]
                if swing:
                    ax_x, ax_z, deg = swing
                    quad = [rotate_y(p, ax_x, ax_z, deg) for p in quad]
                    nrm = rotate_y(nrm, 0.0, 0.0, deg)
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


NUM = r"\d+(?:\.\d+)?"


def parse_size(text: str) -> Spec:
    m = re.fullmatch(rf"({NUM})x({NUM})(?:=((?:{NUM}[LRD][vu]?)(?:,{NUM}[LRD][vu]?)*))?", text)
    if not m:
        raise argparse.ArgumentTypeError(
            f"size '{text}' must be <width>x<height>[=<leaf>,...] in cm, leaf = <weight><L|R|D>[v|u], e.g. 100x140 or 200x150=36Dv,164Ru"
        )
    if not m.group(3):
        return Spec(float(m.group(1)), float(m.group(2)))
    leaves = tuple(
        Leaf(float(lm.group(1)), lm.group(2), lm.group(3) == "v", lm.group(3) == "u")
        for lm in (re.fullmatch(rf"({NUM})([LRD])([vu]?)", c) for c in m.group(3).split(","))
        if lm
    )
    if any(leaf.weight <= 0 for leaf in leaves):
        raise argparse.ArgumentTypeError(f"size '{text}': leaf weights must be > 0")
    if len(leaves) == 1:
        leaves = (leaves[0]._replace(weight=1.0),)  # a lone leaf's weight means nothing
    return Spec(float(m.group(1)), float(m.group(2)), leaves)


def leaf_label(leaf: Leaf) -> str:
    return f"{leaf.weight:g}{leaf.hinge}{'v' if leaf.vent else ''}{'u' if leaf.raised else ''}"


def spec_label(spec: Spec) -> str:
    base = f"{spec.width:g}x{spec.height:g}"
    return base if spec.leaves == SINGLE else f"{base} {'|'.join(map(leaf_label, spec.leaves))}"


def size_tag(width: float, height: float, leaves: tuple[Leaf, ...] = SINGLE) -> str:
    tag = f"{width:g}x{height:g}"
    if leaves != SINGLE:
        tag += "-" + "-".join(map(leaf_label, leaves))
    return tag.replace(".", "_")


def hinge_axis(groups: list[Group], n: int = 1) -> tuple[float, float]:
    """(x, z) of leaf N's swing axis: SH3D's pivot is the centre of all its hinge parts' combined bbox."""
    hinge = next(g for g in groups if g.name == f"sweethome3d_hinge_{n}").boxes
    return (
        (min(b[0] for b in hinge) + max(b[1] for b in hinge)) / 2,
        (min(b[4] for b in hinge) + max(b[5] for b in hinge)) / 2,
    )


def catalog_entry(i: int, width: float, height: float, leaves: tuple[Leaf, ...] = SINGLE) -> list[str]:
    tag = size_tag(width, height, leaves)
    groups = window_groups(width, height, leaves)
    # Plan sash arcs from the 3D hinges: x and y in cm from the model's back-left, radius to the free edge.
    # SH3D reads one space-separated value per sash. A left hinge sweeps 0 -> -90, a right one 180 -> 270.
    xs, ys, radii, starts, ends = [], [], [], [], []
    for n, s in enumerate(sashes(width, leaves), start=1):
        ax_x, ax_z = hinge_axis(groups, n)
        xs.append(ax_x)
        ys.append(ax_z)
        radii.append(ax_x - s.x0 if s.right else s.x1 - ax_x)
        starts.append(180 if s.right else 0)
        ends.append(270 if s.right else -90)

    def join(values: list[float] | list[int]) -> str:
        return " ".join(f"{v:g}" for v in values)

    keys = {
        "id": f"floorplan3d#coupledWindow{tag}",
        "name": f"Coupled wooden window {spec_label(Spec(width, height, leaves))}",
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
        # Not width/depth deformable, so the frame keeps its own depth in any wall and the cut-out
        # still runs through the whole wall. SH3D then drops the frame flush with the wall face the
        # window was dropped on (PlanController); move it in plan for a mid-wall or exterior seat.
        # A width or height resize still scales depth and profiles proportionally: add a --size instead.
        "doorOrWindowCutOutShape": "M0,0 v1 h1 v-1 z",
        "doorOrWindowWallThickness": f"{DEPTH:g}",
        "doorOrWindowWallDistance": "0",
        "doorOrWindowWidthDepthDeformable": "false",
        "doorOrWindowWallCutOutOnBothSides": "true",
        "doorOrWindowSashXAxis": join(xs),
        "doorOrWindowSashYAxis": join(ys),
        "doorOrWindowSashWidth": join(radii),
        "doorOrWindowSashStartAngle": join(starts),
        "doorOrWindowSashEndAngle": join(ends),
        "creator": "floorplan3d",
    }
    return [f"{k}#{i}={v}" for k, v in keys.items()]


def build_library(specs: list[Spec], out: Path) -> None:
    props = [
        "id=floorplan3d-coupled-windows",
        "name=Coupled wooden windows",
        "description=Nordic coupled double-sash wooden windows, inward side-hung",
        "version=1.0",
        "provider=floorplan3d",
        "",
    ]
    for s in specs:
        check_size(*s)  # before the zip exists, so a bad size leaves no partial file
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, s in enumerate(specs, start=1):
            groups = window_groups(*s)
            d = f"coupledWindow{size_tag(*s)}"
            zf.writestr(f"{d}/window.obj", obj_text(groups))
            zf.writestr(f"{d}/window.mtl", mtl_text())
            zf.writestr(f"{d}/icon.png", render_png(groups, s.width, s.height, 128))
            props += [*catalog_entry(i, *s), ""]
        zf.writestr("PluginFurnitureCatalog.properties", "\n".join(props))
    tmp.replace(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sh3d_window", description=(__doc__ or "").split("\n", 1)[0])
    parser.add_argument(
        "--size",
        type=parse_size,
        action="append",
        help="<width>x<height>[=<weight><L|R|D>[v],...] cm, e.g. 200x150=36Dv,164R; repeatable (default 100x140)",
    )
    parser.add_argument("--out", type=Path, default=Path("output/sh3d/coupled-windows.sh3f"))
    parser.add_argument("--preview", type=Path, help="also render the first size, sashes half open, to this PNG")
    args = parser.parse_args(argv)
    specs: list[Spec] = list(dict.fromkeys(args.size or [Spec(100.0, 140.0)]))
    try:
        build_library(specs, args.out)
    except ValueError as e:
        parser.error(str(e))
    print(f"wrote {args.out} ({len(specs)} window{'s' * (len(specs) > 1)})")
    if args.preview:
        s = specs[0]
        args.preview.parent.mkdir(parents=True, exist_ok=True)
        args.preview.write_bytes(render_png(window_groups(*s), s.width, s.height, 600, open_deg=60))
        print(f"wrote {args.preview}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
