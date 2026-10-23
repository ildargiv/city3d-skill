"""Build a Blender scene from city.json (made by osm2city.py) and export it.

Run inside Blender:
  blender -b -P blender_city.py -- city.json --out out/ --style both

Scene layout (Z up, X east, Y north, metres, origin = district centre):
  Landscape  - one ground, roads, parks/water/squares, trees
  Buildings  - one object per building (or building:part), origin on the
               footprint centre at ground level, named "B_<osm id>"

Outputs in --out:
  city.blend                      editable master scene
  landscape_<style>.glb           the single landscape
  buildings_<style>.glb           every building, placed in world position
  buildings/<name>.glb            each building alone, centred on its origin
  placement.json                  world position of every building origin

Swap a block model for a photogrammetry or hand-made one by replacing the
mesh of its "B_<id>" object in city.blend and exporting again with
--from-blend city.blend.

Or let the script do it for a whole team's work:
  blender -b -P blender_city.py -- city.json --scans buildings/ --out out/
  blender -b -P blender_city.py -- city.json --from-blend out/city.blend --scans buildings/ --out out/
Every building folder "<NN> <name> [<osm id>]" under --scans whose model/
(or модель/) holds a .glb/.gltf/.obj/.fbx replaces that building's block
(and its building:part blocks). The model is expected in metres, Z up,
north = +Y, origin at the footprint centre on the ground: the same frame as
block.obj that house_folders.py writes into each folder. The script still
puts it on the ground, centres it on the footprint and fixes the scale when
it is more than 25 % off (photogrammetry units are arbitrary). A
model/place.json {"rotate_deg": 0, "scale": 1, "offset": [0, 0, 0]}
overrides that.
"""
import argparse
import json
import math
import os
import re
import sys

import bpy  # first: the pip build of bpy provides bmesh/mathutils on import
import bmesh
from mathutils import Vector

PITCHED = {"pyramidal", "hipped", "gabled", "gable", "dome", "onion",
           "skillion", "mansard", "half-hipped", "round", "cone"}

PALETTES = {
    "real": {
        "ground": (0.42, 0.44, 0.41), "road": (0.16, 0.17, 0.18),
        "park": (0.26, 0.38, 0.18), "water": (0.17, 0.28, 0.36),
        "square": (0.58, 0.55, 0.50), "pitch": (0.25, 0.42, 0.22),
        "roof": (0.35, 0.33, 0.32), "trunk": (0.25, 0.18, 0.12),
        "crown": (0.20, 0.33, 0.14),
        "walls": [(0.80, 0.76, 0.69), (0.72, 0.66, 0.58), (0.62, 0.58, 0.55)],
        "roughness": 0.85,
    },
    "toon": {
        "ground": (0.49, 0.79, 0.35), "road": (0.49, 0.50, 0.60),
        "park": (0.42, 0.79, 0.33), "water": (0.31, 0.70, 1.00),
        "square": (1.00, 0.89, 0.66), "pitch": (0.35, 0.76, 0.42),
        "roof": (0.85, 0.35, 0.35), "trunk": (0.55, 0.36, 0.20),
        "crown": (0.30, 0.75, 0.30),
        "walls": [(1.00, 0.56, 0.64), (1.00, 0.77, 0.42), (0.56, 0.83, 1.00),
                  (0.71, 0.61, 1.00), (0.50, 0.88, 0.72), (1.00, 0.63, 0.48)],
        "roughness": 1.0,
    },
}


def args_after_dashes():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument("city_json", nargs="?")
    ap.add_argument("--out", default="out")
    ap.add_argument("--style", choices=["real", "toon", "both"], default="real",
                    help="real first; other styles are made from the finished real map")
    ap.add_argument("--from-blend", help="re-export an edited city.blend")
    ap.add_argument("--scans", help="folder of building folders with finished models")
    ap.add_argument("--no-split", action="store_true",
                    help="skip the per-building .glb files")
    return ap.parse_args(argv)


def hex_rgb(value):
    v = (value or "").strip().lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    if len(v) != 6:
        return None
    try:
        return tuple(int(v[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return None


_materials = {}


def material(name, rgb, roughness):
    key = (name, rgb, roughness)
    if key in _materials:
        return _materials[key]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
    bsdf.inputs["Roughness"].default_value = roughness
    m.diffuse_color = (*rgb, 1.0)
    _materials[key] = m
    return m


def collection(name):
    c = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    if c.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(c)
    return c


def new_object(name, bm, coll):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    ob = bpy.data.objects.new(name, me)
    coll.objects.link(ob)
    return ob


def roof_height(b):
    if b["roof"] not in PITCHED:
        return 0.0
    span = math.sqrt(max(b["area_m2"], 1.0))
    return min(6.0, max(1.5, span * 0.25))


def building_mesh(b, wall_mat, roof_mat):
    """Extrude the footprint; pitched roofs become a raised centre point."""
    cx, cy = b["x"], b["y"]
    base, top = b.get("base", 0.0), b["height"]
    rh = roof_height(b)
    wall_top = max(base + 0.5, top - rh)
    bm = bmesh.new()
    verts = [bm.verts.new((x - cx, y - cy, base)) for x, y in b["footprint"]]
    try:
        face = bm.faces.new(verts)
    except ValueError:
        bm.free()
        return None
    bm.normal_update()
    if face.normal.z > 0:
        face.normal_flip()  # bottom faces down so the extrusion faces out
    res = bmesh.ops.extrude_face_region(bm, geom=[face])
    top_verts = [e for e in res["geom"] if isinstance(e, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, verts=top_verts, vec=(0, 0, wall_top - base))
    top_faces = [e for e in res["geom"] if isinstance(e, bmesh.types.BMFace)]
    bm.normal_update()
    for f in bm.faces:
        f.material_index = 0
    if rh > 0 and top_faces:
        poked = bmesh.ops.poke(bm, faces=top_faces)
        for v in poked["verts"]:
            v.co.z += rh
        for f in poked["faces"]:
            f.material_index = 1
    elif top_faces:
        for f in top_faces:
            f.material_index = 1
    ob = new_object(f"B_{abs(b['id'])}" + ("r" if b["id"] < 0 else ""), bm,
                    collection("Buildings"))
    ob.location = (cx, cy, 0)
    ob.data.materials.append(wall_mat)
    ob.data.materials.append(roof_mat)
    for k in ("name", "address", "type", "levels", "height", "roof"):
        ob[k] = b.get(k) or ""
    ob["osm_id"] = b["id"]
    return ob


def flat_polygon(name, pts, z, mat, coll):
    bm = bmesh.new()
    verts = [bm.verts.new((x, y, z)) for x, y in pts]
    try:
        f = bm.faces.new(verts)
    except ValueError:
        bm.free()
        return None
    bm.normal_update()
    if f.normal.z < 0:
        f.normal_flip()
    ob = new_object(name, bm, coll)
    ob.data.materials.append(mat)
    return ob


def road_mesh(roads, mat, coll):
    bm = bmesh.new()
    for r in roads:
        line, w = r["line"], r["width"] / 2
        for (x1, y1), (x2, y2) in zip(line, line[1:]):
            dx, dy = x2 - x1, y2 - y1
            ln = math.hypot(dx, dy) or 1
            nx, ny = -dy / ln * w, dx / ln * w
            z = 0.06 if r["kind"] in ("footway", "path", "cycleway") else 0.05
            vs = [bm.verts.new(p) for p in ((x1 - nx, y1 - ny, z), (x2 - nx, y2 - ny, z),
                                            (x2 + nx, y2 + ny, z), (x1 + nx, y1 + ny, z))]
            bm.faces.new(vs)
    ob = new_object("Roads", bm, coll)
    ob.data.materials.append(mat)
    return ob


def trees(points, pal, coll):
    if not points:
        return
    trunk = material("trunk", pal["trunk"], pal["roughness"])
    crown = material("crown", pal["crown"], pal["roughness"])
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=6, radius1=0.25,
                          radius2=0.2, depth=2.5)
    bmesh.ops.translate(bm, verts=bm.verts, vec=(0, 0, 1.25))
    ico = bmesh.ops.create_icosphere(bm, subdivisions=1, radius=2.2)
    for v in ico["verts"]:
        v.co.z += 4.2
    for f in bm.faces:
        f.material_index = 1 if f.calc_center_median().z > 2.6 else 0
    me = bpy.data.meshes.new("Tree")
    bm.to_mesh(me)
    bm.free()
    me.materials.append(trunk)
    me.materials.append(crown)
    for i, t in enumerate(points):
        ob = bpy.data.objects.new(f"Tree_{i}", me)
        ob.location = (t["x"], t["y"], 0)
        s = t["height"] / 8.0
        ob.scale = (s, s, s)
        coll.objects.link(ob)


def build(data, pal):
    land = collection("Landscape")
    r = data["radius"] * 1.15
    bm = bmesh.new()
    vs = [bm.verts.new((r * math.cos(a), r * math.sin(a), 0))
          for a in (i * math.tau / 64 for i in range(64))]
    bm.faces.new(vs)
    g = new_object("Ground", bm, land)
    g.data.materials.append(material("ground", pal["ground"], pal["roughness"]))
    for i, a in enumerate(data["areas"]):
        if len(a["polygon"]) >= 3:
            z = {"water": 0.02, "park": 0.03}.get(a["kind"], 0.04)
            flat_polygon(f"Area_{a['kind']}_{i}", a["polygon"], z,
                         material(a["kind"], pal.get(a["kind"], pal["park"]),
                                  pal["roughness"]), land)
    if data["roads"]:
        road_mesh(data["roads"], material("road", pal["road"], pal["roughness"]), land)
    trees(data.get("trees", []), pal, land)

    roof = material("roof", pal["roof"], pal["roughness"])
    for b in data["buildings"]:
        if b.get("has_parts"):
            continue
        rgb = hex_rgb(b.get("colour"))
        if pal is PALETTES["toon"] or rgb is None:
            rgb = pal["walls"][abs(b["id"]) % len(pal["walls"])]
        wall = material("wall", tuple(round(c, 3) for c in rgb), pal["roughness"])
        rrgb = hex_rgb(b.get("roof_colour")) if pal is PALETTES["real"] else None
        building_mesh(b, wall, material("roof", rrgb, pal["roughness"]) if rrgb else roof)


def select_only(objs):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    if objs:
        bpy.context.view_layer.objects.active = objs[0]


def export_glb(path, objs):
    select_only(objs)
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB",
                              use_selection=True, export_apply=True,
                              export_yup=True)


def recolour(pal):
    """Repaint an existing scene with another palette, keeping geometry."""
    for ob in bpy.data.objects:
        if ob.type != "MESH" or "scan" in ob:
            continue  # scanned models keep their own textures
        for slot in ob.material_slots:
            m = slot.material
            if not m:
                continue
            kind = m.name.split(".")[0]
            if kind == "wall":
                rgb = pal["walls"][abs(int(ob.get("osm_id", 0))) % len(pal["walls"])]
            else:
                rgb = pal.get(kind)
            if rgb:
                slot.material = material(kind, tuple(rgb), pal["roughness"])


MODEL_DIRS = ("model", "модель")
MODEL_EXT = (".glb", ".gltf", ".obj", ".fbx")


def point_in(pt, ring):
    x, y = pt
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def obj_name(i):
    return f"B_{abs(i)}" + ("r" if i < 0 else "")


def find_models(root):
    """osm id -> newest model file in the folder's model/ (or модель/)."""
    out = {}
    for d in sorted(os.listdir(root)):
        m = re.search(r"\[(-?\d+)\]\s*$", d)
        if not m:
            continue
        # no glob here: "[123]" in the folder name is a glob pattern
        dirs = [os.path.join(root, d, sub) for sub in MODEL_DIRS]
        files = [os.path.join(p, f) for p in dirs if os.path.isdir(p)
                 for f in os.listdir(p) if f.lower().endswith(MODEL_EXT)]
        if files:
            out[int(m.group(1))] = max(files, key=os.path.getmtime)
    return out


def import_model(path):
    before = set(bpy.data.objects)
    ext = os.path.splitext(path)[1].lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    else:
        bpy.ops.import_scene.fbx(filepath=path)
    meshes = [o for o in set(bpy.data.objects) - before if o.type == "MESH"]
    for o in set(bpy.data.objects) - before:
        if o.type != "MESH":
            bpy.data.objects.remove(o)
    if not meshes:
        return None
    select_only(meshes)
    for o in meshes:  # bake parents and transforms before joining
        o.matrix_world = o.matrix_world.copy()
        o.parent = None
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    if len(meshes) > 1:
        bpy.ops.object.join()
    return bpy.context.view_layer.objects.active


def diameter(points):
    """Largest distance between two points: does not depend on rotation."""
    pts = points if len(points) <= 400 else points[::len(points) // 400]
    return max((math.dist(p, q) for p in pts for q in pts), default=0.0)


def place_scans(data, root):
    """Replace blocks with the finished models found under root."""
    blds = collection("Buildings")
    by_id = {b["id"]: b for b in data["buildings"]}
    placed = []
    for bid, path in find_models(root).items():
        b = by_id.get(bid)
        if not b:
            print(f"city3d: {path}: building {bid} is not in city.json, skipped")
            continue
        ob = import_model(path)
        if not ob:
            print(f"city3d: {path}: no mesh inside, skipped")
            continue
        cfg_path = os.path.join(os.path.dirname(path), "place.json")
        cfg = {}
        if os.path.exists(cfg_path):
            with open(cfg_path, encoding="utf-8") as f:
                cfg = json.load(f)
        me = ob.data
        if "rotate_deg" in cfg:
            a = math.radians(cfg["rotate_deg"])
            for v in me.vertices:
                x, y = v.co.x, v.co.y
                v.co.x, v.co.y = x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)
        foot = [(x - b["x"], y - b["y"]) for x, y in b["footprint"]]
        if "scale" in cfg:
            k = cfg["scale"]
        else:
            want, have = diameter(foot), diameter([(v.co.x, v.co.y) for v in me.vertices])
            k = want / have if have and not 0.8 < want / have < 1.25 else 1.0
        xs = [v.co.x for v in me.vertices]
        ys = [v.co.y for v in me.vertices]
        zmin = min(v.co.z for v in me.vertices)
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        # land the model's box centre on the footprint's box centre
        fx = (min(p[0] for p in foot) + max(p[0] for p in foot)) / 2
        fy = (min(p[1] for p in foot) + max(p[1] for p in foot)) / 2
        off = cfg.get("offset", [0, 0, 0])
        for v in me.vertices:
            v.co = Vector(((v.co.x - cx) * k + fx + off[0], (v.co.y - cy) * k + fy + off[1],
                           (v.co.z - zmin) * k + off[2]))
        # the old block and any building:part blocks standing inside it
        ring = b["footprint"]
        for p in data["buildings"]:
            if p["id"] == bid or (p.get("part") and point_in((p["x"], p["y"]), ring)):
                old = bpy.data.objects.get(obj_name(p["id"]))
                if old:
                    bpy.data.objects.remove(old)
        for c in list(ob.users_collection):
            c.objects.unlink(ob)
        blds.objects.link(ob)
        ob.name = me.name = obj_name(bid)
        ob.location = (b["x"], b["y"], 0)
        for key in ("name", "address", "type", "levels", "height", "roof"):
            ob[key] = b.get(key) or ""
        ob["osm_id"] = bid
        ob["scan"] = os.path.relpath(path, root)
        placed.append(f"{ob.name} <- {ob['scan']} (scale {k:.3g})")
    for line in placed:
        print("city3d: placed", line)
    return placed


def main():
    a = args_after_dashes()
    os.makedirs(os.path.join(a.out, "buildings"), exist_ok=True)
    styles = ["real", "toon"] if a.style == "both" else [a.style]
    if a.from_blend:
        bpy.ops.wm.open_mainfile(filepath=a.from_blend)
    else:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        with open(a.city_json, encoding="utf-8") as f:
            data = json.load(f)
        build(data, PALETTES[styles[0]])
        bpy.context.scene["city3d_attribution"] = data.get("attribution", "")
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(
            os.path.join(a.out, "city.blend")))
    if a.scans:
        if not a.city_json:
            sys.exit("--scans needs city.json too (footprints and ids)")
        with open(a.city_json, encoding="utf-8") as f:
            data = json.load(f)
        if place_scans(data, a.scans):
            bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(
                os.path.join(a.out, "city.blend")))

    blds = [o for o in bpy.data.collections["Buildings"].all_objects]
    land = [o for o in bpy.data.collections["Landscape"].all_objects]
    placement = [{"object": o.name, "osm_id": o.get("osm_id"),
                  "name": o.get("name", ""), "x": round(o.location.x, 2),
                  "y": round(o.location.y, 2), "z": round(o.location.z, 2)}
                 for o in blds]
    with open(os.path.join(a.out, "placement.json"), "w", encoding="utf-8") as f:
        json.dump(placement, f, ensure_ascii=False, indent=1)

    for i, style in enumerate(styles):
        if i or a.from_blend:
            recolour(PALETTES[style])
        export_glb(os.path.join(a.out, f"landscape_{style}.glb"), land)
        export_glb(os.path.join(a.out, f"buildings_{style}.glb"), blds)
        if not a.no_split:
            for o in blds:
                loc = o.location.copy()
                o.location = Vector((0, 0, 0))
                export_glb(os.path.join(a.out, "buildings", f"{o.name}_{style}.glb"), [o])
                o.location = loc
    print(f"city3d: {len(blds)} buildings, {len(land)} landscape objects -> {a.out}")


if __name__ == "__main__":
    main()
