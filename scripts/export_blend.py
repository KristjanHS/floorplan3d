"""Inside Blender: bake + scale the raw upstream scene, save .blend, export .glb/.obj, render previews, write report."""

import argparse
import json
import math
import sys
from pathlib import Path

import bpy  # pyright: ignore[reportMissingImports]
from mathutils import Matrix, Vector  # pyright: ignore[reportMissingImports]

UPSTREAM_PX_PER_UNIT = 100  # upstream const.PIXEL_TO_3D_SCALE
GROUPS = ("Walls", "Rooms", "Floor")
SAMPLES = 16
PREVIEW_PX = 1200


def parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="export_blend.py")
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--metres-per-px", type=float)
    p.add_argument("--wall-height", type=float, required=True)
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return p.parse_args(argv)


def group_of(ob) -> str | None:
    """First GROUPS name on the object's own name or its parent chain."""
    while ob is not None:
        for g in GROUPS:
            if ob.name.startswith(g):
                return g
        ob = ob.parent
    return None


def bake(meshes: list) -> None:
    """World transforms -> mesh data; objects end unparented at identity."""
    worlds = {ob: ob.matrix_world.copy() for ob in meshes}
    for ob in meshes:
        if ob.data.users > 1:
            ob.data = ob.data.copy()
        ob.parent = None
        ob.matrix_parent_inverse = Matrix.Identity(4)
        ob.matrix_basis = Matrix.Identity(4)
        ob.data.transform(worlds[ob])
        if worlds[ob].determinant() < 0:
            ob.data.flip_normals()


def bounds(meshes: list) -> tuple[Vector, Vector]:
    pts = [v.co for ob in meshes for v in ob.data.vertices]
    lo = Vector(tuple(min(p[i] for p in pts) for i in range(3)))
    hi = Vector(tuple(max(p[i] for p in pts) for i in range(3)))
    return lo, hi


def look_at(ob, target: Vector) -> None:
    ob.rotation_euler = (target - ob.location).to_track_quat("-Z", "Y").to_euler()


def render(scene, cam, path: Path) -> None:
    scene.camera = cam
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def paint(meshes: list, groups: dict) -> None:
    """Preview-only flat materials: walls dark, everything else light (runs after save/export)."""
    for name, rgb in (("PreviewWall", (0.15, 0.2, 0.3)), ("PreviewFloor", (0.85, 0.85, 0.8))):
        bpy.data.materials.new(name).diffuse_color = (*rgb, 1.0)
    for ob in meshes:
        mat = bpy.data.materials["PreviewWall" if groups[ob.name] == "Walls" else "PreviewFloor"]
        mat.use_nodes = True
        mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = mat.diffuse_color
        ob.data.materials.clear()
        ob.data.materials.append(mat)


def previews(scene, size: Vector, base: Path) -> None:
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = SAMPLES
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.world = scene.world or bpy.data.worlds.new("World")
    scene.world.color = (0.8, 0.8, 0.8)
    sun = bpy.data.objects.new("PreviewSun", bpy.data.lights.new("PreviewSun", "SUN"))
    sun.data.energy = 3.0
    sun.rotation_euler = (math.radians(35), math.radians(15), math.radians(30))
    scene.collection.objects.link(sun)
    centre = size / 2
    span = max(size.x, size.y)

    top = bpy.data.objects.new("PreviewTop", bpy.data.cameras.new("PreviewTop"))
    top.data.type = "ORTHO"
    top.data.ortho_scale = span * 1.05
    top.location = (centre.x, centre.y, size.z + span)
    top.data.clip_end = size.z + span * 2
    scene.collection.objects.link(top)
    aspect = size.x / size.y
    scene.render.resolution_x = PREVIEW_PX if aspect >= 1 else max(1, round(PREVIEW_PX * aspect))
    scene.render.resolution_y = PREVIEW_PX if aspect < 1 else max(1, round(PREVIEW_PX / aspect))
    render(scene, top, base.with_name(base.name + "_top.png"))

    persp = bpy.data.objects.new("PreviewPersp", bpy.data.cameras.new("PreviewPersp"))
    persp.location = centre + Vector((-1.0, -1.0, 1.1)).normalized() * size.length * 1.4
    persp.data.clip_end = size.length * 10
    look_at(persp, centre)
    scene.collection.objects.link(persp)
    scene.render.resolution_x, scene.render.resolution_y = PREVIEW_PX, PREVIEW_PX * 3 // 4
    render(scene, persp, base.with_name(base.name + "_persp.png"))


def main() -> None:
    a = parse()
    scene = bpy.context.scene
    for ob in list(bpy.data.objects):
        if ob.type != "MESH":
            bpy.data.objects.remove(ob)
    meshes = list(bpy.data.objects)
    groups = {ob.name: group_of(ob) for ob in meshes}
    counts = {g.lower(): sum(groups[ob.name] == g and len(ob.data.vertices) > 0 for ob in meshes) for g in GROUPS}
    bake(meshes)
    for ob in [ob for ob in meshes if len(ob.data.vertices) == 0]:
        meshes.remove(ob)
        bpy.data.objects.remove(ob)
    if not meshes:
        raise SystemExit("export_blend: no mesh geometry in the input scene")

    lo, hi = bounds(meshes)
    raw = hi - lo
    scaled = a.metres_per_px is not None
    xy = a.metres_per_px * UPSTREAM_PX_PER_UNIT if scaled else 1.0
    z = a.wall_height / raw.z if scaled else 1.0
    fit = Matrix.Diagonal((xy, xy, z, 1.0)) @ Matrix.Translation(-lo)
    for ob in meshes:
        ob.data.transform(fit)
    lo, hi = bounds(meshes)
    size = hi - lo

    for ob in meshes:
        if groups[ob.name]:
            ob["group"] = groups[ob.name]  # bake dropped the parent chain group_of reads; measure.py needs it

    a.out_dir.mkdir(parents=True, exist_ok=True)
    base = a.out_dir / a.name
    bpy.ops.wm.save_as_mainfile(filepath=str(base.with_name(a.name + ".blend")), check_existing=False)
    bpy.ops.export_scene.gltf(filepath=str(base.with_name(a.name + ".glb")), export_format="GLB")
    bpy.ops.wm.obj_export(filepath=str(base.with_name(a.name + ".obj")))
    paint(meshes, groups)
    previews(scene, size, base)
    report = {
        "scaled": scaled,
        "metres_per_px": a.metres_per_px,
        "wall_height_m": a.wall_height if scaled else None,
        "extent_m": [round(v, 4) for v in size] if scaled else None,
        "extent_raw_units": [round(v, 4) for v in raw],
        "objects": counts,
    }
    base.with_name(a.name + "_report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
