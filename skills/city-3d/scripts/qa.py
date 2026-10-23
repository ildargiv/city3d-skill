#!/usr/bin/env python3
"""Check finished building models against the budgets in targets.json.

  python3 qa.py city/city.json --root buildings/ [--targets ../targets.json] [--json]

For every building folder with a model (model/ or модель/; the same file
blender_city.py --scans would place) it measures triangles, texture size,
footprint size against OSM and height against OSM, and prints what is out
of budget. No packages needed: .glb and .obj are read directly.
"""
import argparse
import json
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from house_folders import existing  # noqa: E402

MODEL_DIRS = ("model", "модель")
MODEL_EXT = (".glb", ".obj")


def image_size(data):
    """(width, height) of PNG or JPEG bytes, else None."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker, size = data[i + 1], struct.unpack(">H", data[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            i += 2 + size
    return None


def read_glb(path):
    """Triangles, largest texture side and vertex positions of a .glb."""
    with open(path, "rb") as f:
        data = f.read()
    n = struct.unpack("<I", data[12:16])[0]
    gl = json.loads(data[20:20 + n])
    binary = data[20 + n + 8:]
    acc = gl.get("accessors", [])
    tris, pts = 0, []
    for mesh in gl.get("meshes", []):
        for p in mesh["primitives"]:
            if p.get("mode", 4) != 4:
                continue
            idx = p.get("indices")
            pos = acc[p["attributes"]["POSITION"]]
            tris += (acc[idx]["count"] if idx is not None else pos["count"]) // 3
            if "min" in pos and "max" in pos:
                pts += [pos["min"], pos["max"]]
    tex = 0
    for img in gl.get("images", []):
        if "bufferView" in img:
            bv = gl["bufferViews"][img["bufferView"]]
            off = bv.get("byteOffset", 0)
            size = image_size(binary[off:off + bv["byteLength"]])
            if size:
                tex = max(tex, *size)
    # glTF is Y-up: (x, y, z) -> map (x, -z, y)
    return tris, tex, [(p[0], -p[2], p[1]) for p in pts]


def read_obj(path):
    tris, pts, tex = 0, [], 0
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith("v "):
                x, y, z = map(float, line.split()[1:4])
                pts.append((x, -z, y))  # OBJ Y-up like block.obj
            elif line.startswith("f "):
                tris += len(line.split()) - 3
            elif line.startswith("mtllib"):
                mtl = os.path.join(os.path.dirname(path), line.split(None, 1)[1].strip())
                if os.path.exists(mtl):
                    for m in open(mtl, encoding="utf-8", errors="ignore"):
                        if m.startswith("map_Kd"):
                            img = os.path.join(os.path.dirname(path), m.split(None, 1)[1].strip())
                            if os.path.exists(img):
                                with open(img, "rb") as fi:
                                    size = image_size(fi.read(65536))
                                if size:
                                    tex = max(tex, *size)
    return tris, tex, pts


def pick_model(folder):
    files = [os.path.join(folder, d, f) for d in MODEL_DIRS if os.path.isdir(os.path.join(folder, d))
             for f in os.listdir(os.path.join(folder, d)) if f.lower().endswith(MODEL_EXT)]
    own = [f for f in files if not os.path.basename(f).lower().startswith("drone.")]
    return max(own or files, key=os.path.getmtime) if files else None


def span(points):
    """Largest horizontal extent (bounding-box diagonal), independent of placement."""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return math.hypot(max(xs) - min(xs), max(ys) - min(ys))


def check(b, path, targets):
    t = targets["building_model"]
    tris, tex, pts = read_glb(path) if path.lower().endswith(".glb") else read_obj(path)
    landmark = (b.get("type") in targets["landmarks"]
                or any(w in (b.get("name") or "").lower() for w in ("башн", "собор", "церк", "tower")))
    lo, hi = t["triangles"]["landmark" if landmark else "house"]
    issues = []
    if not lo <= tris <= hi:
        issues.append(f"triangles {tris} outside {lo}-{hi}")
    if tex and tex > t["texture_px_max"]:
        issues.append(f"texture {tex}px > {t['texture_px_max']}")
    if path.lower().endswith(".glb") and 0 < tex < t["texture_px_min"]:
        issues.append(f"texture {tex}px < {t['texture_px_min']}")
    if pts:
        r = span(pts) / max(span([tuple(p) for p in b["footprint"]]), 0.1)
        lo_s, hi_s = t["size_vs_footprint"]
        if not lo_s <= r <= hi_s:
            issues.append(f"size {r:.2f}x the OSM footprint (wrong units or a "
                          "neighbour inside; --scans rescales, check it)")
        h = max(p[2] for p in pts) - min(p[2] for p in pts)
        rh = h / max(b["height"], 1)
        lo_h, hi_h = t["height_vs_osm"]
        if not lo_h <= rh <= hi_h:
            issues.append(f"height {h:.1f} m vs OSM {b['height']:.0f} m")
    return {"id": b["id"], "model": path, "triangles": tris, "texture_px": tex,
            "landmark": landmark, "issues": issues}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("city_json")
    ap.add_argument("--root", required=True)
    ap.add_argument("--targets", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                      "..", "targets.json"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    with open(a.targets, encoding="utf-8") as f:
        targets = json.load(f)
    with open(a.city_json, encoding="utf-8") as f:
        by_id = {b["id"]: b for b in json.load(f)["buildings"]}
    report = []
    for bid, folder in sorted(existing(a.root).items(), key=lambda kv: kv[1]):
        path = pick_model(os.path.join(a.root, folder))
        if path and bid in by_id:
            report.append({**check(by_id[bid], path, targets), "folder": folder})
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return
    for r in report:
        state = "OK" if not r["issues"] else "; ".join(r["issues"])
        print(f"{r['folder']}: {os.path.basename(r['model'])}, {r['triangles']} tris, "
              f"tex {r['texture_px'] or '-'}: {state}")
    bad = sum(bool(r["issues"]) for r in report)
    print(f"\n{len(report)} models checked, {bad} out of budget")


if __name__ == "__main__":
    main()
