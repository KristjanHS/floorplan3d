"""Inside Blender: nearest Walls-mesh hit per ray from one point; prints one MEASURE_HITS JSON line."""

import argparse
import json
import sys

import bpy  # pyright: ignore[reportMissingImports]
from mathutils import Vector  # pyright: ignore[reportMissingImports]

MARK = "MEASURE_HITS "


def parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="measure_blend.py")
    p.add_argument("--origin", type=float, nargs=3, required=True)
    p.add_argument("--dir", type=float, nargs=3, action="append", required=True)
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    return p.parse_args(argv)


def first_hit(walls: list, origin: Vector, direction: Vector) -> Vector | None:
    best = None
    for ob in walls:
        inv = ob.matrix_world.inverted()
        ok, loc, _normal, _face = ob.ray_cast(inv @ origin, (inv.to_3x3() @ direction).normalized())
        if ok:
            hit = ob.matrix_world @ loc
            if best is None or (hit - origin).length < (best - origin).length:
                best = hit
    return best


def main() -> None:
    a = parse()
    walls = [ob for ob in bpy.data.objects if ob.type == "MESH" and ob.get("group") == "Walls"]
    if not walls:
        raise SystemExit("measure_blend: no Walls-tagged meshes — re-export with the current export_blend.py")
    hits = [first_hit(walls, Vector(a.origin), Vector(d)) for d in a.dir]
    print(MARK + json.dumps([list(h) if h is not None else None for h in hits]))


if __name__ == "__main__":
    main()
