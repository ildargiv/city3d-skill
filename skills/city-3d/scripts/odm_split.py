#!/usr/bin/env python3
"""Cut an OpenDroneMap / WebODM textured model into one model per building.

  python3 odm_split.py <odm project> city/city.json --root buildings/ [--buffer 1.5]

<odm project> is the folder ODM or WebODM produced (WebODM: Download
Assets -> All Assets, unzipped). It needs
  odm_texturing/odm_textured_model_geo.obj (+ .mtl and textures)
  odm_georeferencing/coords.txt            ("WGS84 UTM 39N" and the offset)

The drone model is georeferenced, so every face is moved into the city frame
(metres from the city origin in city.json) and given to the building whose
footprint it stands on (plus --buffer metres for eaves and cornices). Each
building folder under --root that gets enough faces receives
model/drone.obj (+ drone.mtl and the textures it uses), in the same frame
as block.obj: metres, origin on the footprint centre, OBJ Y-up, ground at 0.
blender_city.py --scans then puts them on the map. A walk-round model in the
same folder (any other file in model/) wins over drone.obj.

Roads, trees and ground are left out; the landscape stays from OSM.
Data you fly yourself is yours; mind the local drone rules.
"""
import argparse
import json
import math
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from house_folders import existing, point_in  # noqa: E402

A, F = 6378137.0, 1 / 298.257223563  # WGS84
K0 = 0.9996


def utm_to_latlon(e, n, zone, north=True):
    """Inverse transverse Mercator (Snyder), good to well under a metre."""
    e2 = F * (2 - F)
    ep2 = e2 / (1 - e2)
    x, y = e - 500000.0, n if north else n - 10000000.0
    m = y / K0
    mu = m / (A * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    p = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
         + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
         + (151 * e1 ** 3 / 96) * math.sin(6 * mu))
    c1 = ep2 * math.cos(p) ** 2
    t1 = math.tan(p) ** 2
    n1 = A / math.sqrt(1 - e2 * math.sin(p) ** 2)
    r1 = A * (1 - e2) / (1 - e2 * math.sin(p) ** 2) ** 1.5
    d = x / (n1 * K0)
    lat = p - (n1 * math.tan(p) / r1) * (
        d ** 2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2) * d ** 6 / 720)
    lon = (d - (1 + 2 * t1 + c1) * d ** 3 / 6
           + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2) * d ** 5 / 120
           ) / math.cos(p)
    return math.degrees(lat), (zone - 1) * 6 - 180 + 3 + math.degrees(lon)


def read_coords(path):
    with open(path, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    m = re.search(r"UTM\s*(\d+)\s*([NS])", lines[0], re.I)
    if not m:
        sys.exit(f"{path}: expected a line like 'WGS84 UTM 39N', got {lines[0]!r}")
    de, dn = map(float, lines[1].split()[:2])
    return int(m.group(1)), m.group(2).upper() == "N", de, dn


def near(pt, ring, buf):
    """Inside the footprint or within buf metres of its outline."""
    if point_in(pt, ring):
        return True
    x, y = pt
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        dx, dy = x2 - x1, y2 - y1
        t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy or 1)))
        if math.hypot(x - x1 - t * dx, y - y1 - t * dy) <= buf:
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("odm")
    ap.add_argument("city_json")
    ap.add_argument("--root", required=True, help="the building folders")
    ap.add_argument("--buffer", type=float, default=1.5, help="metres around the footprint")
    ap.add_argument("--min-faces", type=int, default=300,
                    help="skip buildings the drone barely saw")
    a = ap.parse_args()

    obj = os.path.join(a.odm, "odm_texturing", "odm_textured_model_geo.obj")
    coords = os.path.join(a.odm, "odm_georeferencing", "coords.txt")
    for p in (obj, coords):
        if not os.path.exists(p):
            sys.exit(f"not found: {p}")
    zone, north, de, dn = read_coords(coords)
    with open(a.city_json, encoding="utf-8") as f:
        city = json.load(f)
    lat0, lon0 = city.get("origin") or city["center"]
    k_lat, k_lon = 111_320.0, 111_320.0 * math.cos(math.radians(lat0))

    # the model spans a few hundred metres: a local linear fit of UTM -> city
    # frame is exact to centimetres and far faster than per-vertex projection
    def to_city(e, n):
        lat, lon = utm_to_latlon(e + de, n + dn, zone, north)
        return (lon - lon0) * k_lon, (lat - lat0) * k_lat
    o = to_city(0, 0)
    ex, ny = to_city(100, 0), to_city(0, 100)
    ax, ay = [(ex[i] - o[i]) / 100 for i in (0, 1)], [(ny[i] - o[i]) / 100 for i in (0, 1)]

    v, vt, faces, mtllib, mat = [], [], [], None, None
    with open(obj, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith("v "):
                x, y, z = map(float, line.split()[1:4])
                v.append((o[0] + ax[0] * x + ay[0] * y, o[1] + ax[1] * x + ay[1] * y, z))
            elif line.startswith("vt "):
                vt.append(line.split()[1:3])
            elif line.startswith("f "):
                faces.append((mat, [tuple(int(i) if i else 0 for i in (c.split("/") + ["", ""])[:2])
                                    for c in line.split()[1:]]))
            elif line.startswith("usemtl"):
                mat = line.split(None, 1)[1].strip()
            elif line.startswith("mtllib"):
                mtllib = line.split(None, 1)[1].strip()
    print(f"drone model: {len(v)} vertices, {len(faces)} faces, UTM {zone}{'N' if north else 'S'}")

    # face centroids on a 10 m grid, so each building only looks at its block
    grid = {}
    for k, (_, fv) in enumerate(faces):
        cx = sum(v[i - 1][0] for i, _ in fv) / len(fv)
        cy = sum(v[i - 1][1] for i, _ in fv) / len(fv)
        grid.setdefault((int(cx // 10), int(cy // 10)), []).append((k, cx, cy))

    mtl = {}
    if mtllib:
        cur = None
        with open(os.path.join(os.path.dirname(obj), mtllib), encoding="utf-8") as f:
            for line in f:
                if line.startswith("newmtl"):
                    cur = line.split(None, 1)[1].strip()
                    mtl[cur] = [line]
                elif cur:
                    mtl[cur].append(line)

    folders = existing(a.root)
    done = 0
    for b in city["buildings"]:
        if b.get("part") or b["id"] not in folders:
            continue
        ring = b["footprint"]
        xs, ys = [p[0] for p in ring], [p[1] for p in ring]
        mine = []
        for gx in range(int((min(xs) - a.buffer) // 10), int((max(xs) + a.buffer) // 10) + 1):
            for gy in range(int((min(ys) - a.buffer) // 10), int((max(ys) + a.buffer) // 10) + 1):
                mine += [k for k, cx, cy in grid.get((gx, gy), [])
                         if near((cx, cy), ring, a.buffer)
                         and all(near(v[i - 1][:2], ring, 2 * a.buffer) for i, _ in faces[k][1])]
        if len(mine) < a.min_faces:
            continue
        used_v = sorted({i for k in mine for i, _ in faces[k][1]})
        zs = sorted(v[i - 1][2] for i in used_v)
        ground = zs[len(zs) // 50]  # 2nd percentile: the pavement at the walls
        # drop the pavement itself, keep everything standing on it
        mine = [k for k in mine if max(v[i - 1][2] for i, _ in faces[k][1]) > ground + 0.5]
        used_v = sorted({i for k in mine for i, _ in faces[k][1]})
        vmap = {i: n for n, i in enumerate(used_v, 1)}
        used_t = sorted({t for k in mine for _, t in faces[k][1] if t})
        tmap = {t: n for n, t in enumerate(used_t, 1)}
        out = os.path.join(a.root, folders[b["id"]], "model")
        os.makedirs(out, exist_ok=True)
        mats = sorted({faces[k][0] for k in mine if faces[k][0]})
        lines = ["# OpenDroneMap model cut to this building; frame as block.obj",
                 "mtllib drone.mtl" if mats else ""]
        lines += [f"v {v[i-1][0]-b['x']:.3f} {v[i-1][2]-ground:.3f} {-(v[i-1][1]-b['y']):.3f}"
                  for i in used_v]
        lines += [f"vt {vt[t-1][0]} {vt[t-1][1]}" for t in used_t]
        cur = object()
        for k in sorted(mine, key=lambda k: faces[k][0] or ""):
            m, fv = faces[k]
            if m != cur:
                cur = m
                if m:
                    lines.append(f"usemtl {m}")
            lines.append("f " + " ".join(f"{vmap[i]}/{tmap[t]}" if t else str(vmap[i])
                                         for i, t in fv))
        with open(os.path.join(out, "drone.obj"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        if mats:
            body = []
            for m in mats:
                for line in mtl.get(m, []):
                    if line.split(None, 1)[0] in ("map_Kd", "map_Ka", "map_d", "map_Bump", "bump"):
                        tex = line.split(None, 1)[1].strip()
                        src = os.path.join(os.path.dirname(obj), tex)
                        dst = os.path.join(out, os.path.basename(tex))
                        if os.path.exists(src) and not os.path.exists(dst):
                            try:
                                os.link(src, dst)
                            except OSError:
                                shutil.copy2(src, dst)
                        line = f"{line.split(None, 1)[0]} {os.path.basename(tex)}\n"
                    body.append(line)
            with open(os.path.join(out, "drone.mtl"), "w", encoding="utf-8") as f:
                f.writelines(body)
        done += 1
        print(f"{folders[b['id']]}: {len(mine)} faces")
    print(f"drone models written for {done} buildings")


if __name__ == "__main__":
    main()
