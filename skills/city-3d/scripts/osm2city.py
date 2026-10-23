#!/usr/bin/env python3
"""Turn an OpenStreetMap export (.osm XML or Overpass JSON) into a city kit:

  buildings.csv  - one row per building: the work list for per-house modelling
  city.json      - footprints/roads/areas in local metres, used by preview.html
  city.obj       - extruded block model (Blender, Godot, Unity, Unreal import it)

Usage:
  python3 osm2city.py map.osm --lat 56.63 --lon 47.89 --radius 300 --out out/

Without --lat/--lon the centre of the data is used. Data (c) OpenStreetMap
contributors, ODbL.
"""
import argparse
import csv
import json
import math
import os
import re
import shutil
import xml.etree.ElementTree as ET

LEVEL_HEIGHT = 3.0
DEFAULT_LEVELS = {
    "house": 2, "detached": 2, "garage": 1, "garages": 1, "shed": 1,
    "kiosk": 1, "service": 1, "roof": 1, "church": 3, "cathedral": 4,
    "apartments": 5, "commercial": 3, "retail": 2, "school": 3,
}
ROAD_WIDTH = {
    "motorway": 14, "trunk": 12, "primary": 12, "secondary": 10,
    "tertiary": 8, "residential": 6, "unclassified": 6, "living_street": 5,
    "service": 4, "pedestrian": 6, "footway": 2, "path": 1.5, "cycleway": 2,
}
AREA_KINDS = {
    ("leisure", "park"): "park", ("landuse", "grass"): "park",
    ("leisure", "garden"): "park", ("landuse", "forest"): "park",
    ("natural", "wood"): "park", ("natural", "water"): "water",
    ("waterway", "riverbank"): "water", ("leisure", "pitch"): "pitch",
    ("place", "square"): "square", ("highway", "pedestrian"): "square",
}


def load(path):
    """Return nodes {id: (lat, lon)}, ways [(id, [node ids], tags)] and
    point features [(lat, lon, tags)] such as trees.

    Multipolygon relations are flattened into extra closed ways (outer rings
    only) carrying the relation's tags."""
    with open(path, "rb") as f:
        head = f.read(64).lstrip()
    if head.startswith(b"{"):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        nodes, ways, rels, points = {}, [], [], []
        for el in data.get("elements", []):
            if el["type"] == "node":
                nodes[el["id"]] = (el["lat"], el["lon"])
                if el.get("tags"):
                    points.append((el["lat"], el["lon"], el["tags"]))
            elif el["type"] == "way":
                ways.append((el["id"], el.get("nodes", []), el.get("tags", {})))
            elif el["type"] == "relation":
                rels.append((el["id"], [(m["type"], m["ref"], m.get("role", ""))
                                        for m in el.get("members", [])],
                             el.get("tags", {})))
        return nodes, ways + relation_rings(ways, rels), points
    root = ET.parse(path).getroot()
    nodes, points = {}, []
    for n in root.iter("node"):
        lat, lon = float(n.get("lat")), float(n.get("lon"))
        nodes[int(n.get("id"))] = (lat, lon)
        tags = {t.get("k"): t.get("v") for t in n.iter("tag")}
        if tags:
            points.append((lat, lon, tags))
    ways = []
    for w in root.iter("way"):
        refs = [int(nd.get("ref")) for nd in w.iter("nd")]
        tags = {t.get("k"): t.get("v") for t in w.iter("tag")}
        ways.append((int(w.get("id")), refs, tags))
    rels = []
    for r in root.iter("relation"):
        members = [(m.get("type"), int(m.get("ref")), m.get("role", ""))
                   for m in r.iter("member")]
        tags = {t.get("k"): t.get("v") for t in r.iter("tag")}
        rels.append((int(r.get("id")), members, tags))
    return nodes, ways + relation_rings(ways, rels), points


def relation_rings(ways, rels):
    """Join the outer member ways of multipolygons into closed rings."""
    by_id = {wid: refs for wid, refs, _ in ways}
    out = []
    for rid, members, tags in rels:
        if tags.get("type") != "multipolygon":
            continue
        segs = [list(by_id[ref]) for typ, ref, role in members
                if typ == "way" and role in ("outer", "") and ref in by_id]
        n = 0
        while segs:
            ring = segs.pop(0)
            joined = True
            while ring[0] != ring[-1] and joined:
                joined = False
                for i, sg in enumerate(segs):
                    if sg[0] == ring[-1]:
                        ring += sg[1:]
                    elif sg[-1] == ring[-1]:
                        ring += sg[-2::-1]
                    else:
                        continue
                    segs.pop(i)
                    joined = True
                    break
            # A box export cuts big water bodies (a river) into open pieces;
            # close them across the cut so the water still shows.
            if ring[0] != ring[-1] and tags.get("natural") == "water" and len(ring) > 3:
                ring.append(ring[0])
            if ring[0] == ring[-1]:
                out.append((-(rid * 100 + n), ring, tags))  # negative id = relation
                n += 1
    return out


def num(value):
    if not value:
        return None
    m = re.match(r"\s*([0-9]+(?:[.,][0-9]+)?)", value)
    return float(m.group(1).replace(",", ".")) if m else None


def building_height(tags):
    """Top and base height in metres plus levels, from tags or defaults."""
    levels = num(tags.get("building:levels"))
    height = num(tags.get("height"))
    if levels is None:
        kind = tags.get("building") or tags.get("building:part")
        levels = DEFAULT_LEVELS.get(kind, 3)
        if height is not None:
            levels = max(1, round(height / LEVEL_HEIGHT))
    if height is None:
        min_level = num(tags.get("building:min_level")) or 0
        height = (levels + min_level) * LEVEL_HEIGHT + (1.0 if levels > 1 else 0.5)
    base = num(tags.get("min_height"))
    if base is None:
        base = (num(tags.get("building:min_level")) or 0) * LEVEL_HEIGHT
    return round(height, 1), round(base, 1), int(levels)


def point_in(pt, ring):
    x, y = pt
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def polygon_area(pts):
    return 0.5 * sum(x1 * y2 - x2 * y1
                     for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))


def triangulate(pts):
    """Ear clipping for a simple polygon (CCW). Returns index triples."""
    idx = list(range(len(pts)))
    tris = []

    def inside(p, a, b, c):
        def s(p1, p2, p3):
            return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])
        d1, d2, d3 = s(p, a, b), s(p, b, c), s(p, c, a)
        return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))

    guard = 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        for k in range(len(idx)):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % len(idx)]
            a, b, c = pts[i0], pts[i1], pts[i2]
            if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) <= 0:
                continue
            if any(inside(pts[j], a, b, c) for j in idx if j not in (i0, i1, i2)):
                continue
            tris.append((i0, i1, i2))
            idx.pop(k)
            break
        else:
            break  # degenerate polygon: fan the rest
    for k in range(1, len(idx) - 1):
        tris.append((idx[0], idx[k], idx[k + 1]))
    return tris


def osm_url(i):
    if i < 0:
        return f"https://www.openstreetmap.org/relation/{-i // 100}"
    return f"https://www.openstreetmap.org/way/{i}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", nargs="+",
                    help="one or more .osm/.json files; pieces of a big area are merged")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--radius", type=float, default=300,
                    help="metres; 0 keeps everything in the files (a box export)")
    ap.add_argument("--out", default="out")
    ap.add_argument("--name", default="")
    args = ap.parse_args()

    nodes, ways, points = {}, {}, []
    for path in args.input:
        n, w, p = load(path)
        nodes.update(n)
        for way in w:  # the same way can sit in two overlapping exports
            ways[way[0]] = way
        points += p
    ways = list(ways.values())
    points = list({(la, lo): (la, lo, t) for la, lo, t in points}.values())
    if args.lat is None or args.lon is None:
        lats = [p[0] for p in nodes.values()]
        lons = [p[1] for p in nodes.values()]
        args.lat = (min(lats) + max(lats)) / 2
        args.lon = (min(lons) + max(lons)) / 2
    k_lat = 111_320.0
    k_lon = 111_320.0 * math.cos(math.radians(args.lat))

    def xy(nid):
        lat, lon = nodes[nid]
        return ((lon - args.lon) * k_lon, (lat - args.lat) * k_lat)

    buildings, roads, areas, trees = [], [], [], []
    for lat, lon, tags in points:
        if tags.get("natural") != "tree":
            continue
        x, y = (lon - args.lon) * k_lon, (lat - args.lat) * k_lat
        if not args.radius or math.hypot(x, y) <= args.radius:
            trees.append({"x": round(x, 2), "y": round(y, 2),
                          "height": num(tags.get("height")) or 8.0})
    for wid, refs, tags in ways:
        if any(r not in nodes for r in refs) or len(refs) < 2:
            continue
        pts = [xy(r) for r in refs]
        closed = refs[0] == refs[-1] and len(refs) >= 4
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        if args.radius and math.hypot(cx, cy) > args.radius:
            continue
        rounded = [[round(x, 2), round(y, 2)] for x, y in pts]
        if closed and ("building" in tags or "building:part" in tags):
            ring = pts[:-1]
            if polygon_area(ring) < 0:
                ring.reverse()
            height, base, levels = building_height(tags)
            addr = " ".join(filter(None, [tags.get("addr:street"),
                                          tags.get("addr:housenumber")]))
            buildings.append({
                "id": wid, "name": tags.get("name", ""), "address": addr,
                "type": tags.get("building") or tags.get("building:part"),
                "part": "building:part" in tags and "building" not in tags,
                "levels": levels, "height": height, "base": base,
                "levels_known": "building:levels" in tags or "height" in tags,
                "roof": tags.get("roof:shape", "flat"),
                "colour": tags.get("building:colour") or tags.get("colour", ""),
                "roof_colour": tags.get("roof:colour", ""),
                "material": tags.get("building:material", ""),
                "area_m2": round(abs(polygon_area(ring)), 1),
                "x": round(cx, 1), "y": round(cy, 1),
                "dist_m": round(math.hypot(cx, cy)),
                "footprint": [[round(x, 2), round(y, 2)] for x, y in ring],
            })
            continue
        kind = next((v for (k, val), v in AREA_KINDS.items()
                     if tags.get(k) == val), None)
        if closed and kind and not (kind == "square" and tags.get("area") != "yes"
                                    and "highway" in tags):
            areas.append({"kind": kind, "name": tags.get("name", ""),
                          "polygon": rounded[:-1]})
        elif tags.get("highway") in ROAD_WIDTH:
            roads.append({"kind": tags["highway"], "name": tags.get("name", ""),
                          "width": ROAD_WIDTH[tags["highway"]], "line": rounded})

    # A building outline that has building:part pieces inside is drawn by
    # those parts; keep it in the list but drop it from the 3D scene.
    parts = [b for b in buildings if b["part"]]
    for b in buildings:
        b["has_parts"] = False
        if b["part"]:
            continue
        inner = [p for p in parts if point_in((p["x"], p["y"]), b["footprint"])]
        b["has_parts"] = bool(inner)
        for p in inner:  # parts inherit name, address and colours of their building
            for k in ("name", "address", "colour", "material"):
                p[k] = p[k] or b[k]
    buildings.sort(key=lambda b: b["dist_m"])
    os.makedirs(args.out, exist_ok=True)

    fields = ["id", "name", "address", "type", "part", "has_parts", "levels",
              "height", "base", "levels_known", "roof", "colour", "material",
              "area_m2",
              "dist_m", "x", "y"]
    with open(os.path.join(args.out, "buildings.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields + ["osm_url"],
                           extrasaction="ignore")
        w.writeheader()
        for b in buildings:
            w.writerow({**b, "osm_url": osm_url(b["id"])})

    city = {"name": args.name, "center": [args.lat, args.lon],
            # box exports: the extent of what was kept
            "radius": args.radius or max([b["dist_m"] for b in buildings] + [50]) + 30,
            "attribution": "© OpenStreetMap contributors (ODbL)",
            "buildings": buildings, "roads": roads, "areas": areas,
            "trees": trees}
    with open(os.path.join(args.out, "city.json"), "w", encoding="utf-8") as f:
        json.dump(city, f, ensure_ascii=False)
    # Same data as a script, so preview.html also works when opened from disk.
    with open(os.path.join(args.out, "city.js"), "w", encoding="utf-8") as f:
        f.write("window.CITY_DATA = ")
        json.dump(city, f, ensure_ascii=False)
        f.write(";\n")
    preview = os.path.join(os.path.dirname(os.path.abspath(__file__)), "preview.html")
    if os.path.exists(preview):
        shutil.copy(preview, os.path.join(args.out, "index.html"))

    # OBJ: Y-up, X east, Z south (so it looks right in Blender/Godot).
    with open(os.path.join(args.out, "city.obj"), "w", encoding="utf-8") as f:
        f.write("# (c) OpenStreetMap contributors, ODbL\n")
        base = 1
        for b in buildings:
            if b["has_parts"]:
                continue
            ring, h, h0 = b["footprint"], b["height"], b["base"]
            n = len(ring)
            f.write(f"o building_{abs(b['id'])}\n")
            for x, y in ring:
                f.write(f"v {x} {h0} {-y}\n")
            for x, y in ring:
                f.write(f"v {x} {h} {-y}\n")
            for i in range(n):
                j = (i + 1) % n
                f.write(f"f {base+i} {base+j} {base+n+j} {base+n+i}\n")
            for a, b2, c in triangulate([tuple(p) for p in ring]):
                f.write(f"f {base+n+a} {base+n+b2} {base+n+c}\n")
            base += 2 * n

    known = sum(b["levels_known"] for b in buildings if not b["part"])
    whole = sum(not b["part"] for b in buildings)
    print(f"buildings: {whole} + parts: {len(buildings) - whole} "
          f"(levels from OSM: {known}), "
          f"roads: {len(roads)}, areas: {len(areas)}, trees: {len(trees)} "
          f"-> {args.out}/")


if __name__ == "__main__":
    main()
