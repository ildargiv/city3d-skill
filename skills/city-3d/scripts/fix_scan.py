#!/usr/bin/env python3
"""Measure a photogrammetry scan and repair what can be repaired.

  python3 fix_scan.py scan.glb                        # report only
  python3 fix_scan.py scan.glb --out fixed.glb        # repair, write .glb
  python3 fix_scan.py scan.glb --height 54 --collision boxes.glb --out fixed.glb
  python3 fix_scan.py scan.glb --json

Reads and writes .glb directly, no packages needed. The point is to answer
before the model reaches the map: is this scan a building, or a facade sheet
that should go back to the shot list?

The report separates two kinds of damage:

* capture damage -- the mesh is flat, covers one side, the scale is off.
  No script fixes this; the building goes back into the shot list.
* mesh damage -- inverted winding, open borders, floating debris, too many
  triangles, no normals. `--out` repairs these.

Repair, in order: Y-up to Z-up, weld, drop debris, make the winding agree
and face outwards, decimate by clustering, recompute normals, write .glb.
The texture is left as it is (crop and repack it in Blender); the report
says how much of it is actually used.
"""
import argparse
import json
import math
import struct
import sys

# glTF component types and accessor types we can read
CT = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}
CT_SIZE = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def read_glb(path):
    """(json, binary) of a .glb file."""
    with open(path, "rb") as f:
        data = f.read()
    magic, version, length = struct.unpack("<III", data[:12])
    if magic != 0x46546C67:
        sys.exit(f"{path}: not a .glb file")
    off, gl, binary = 12, None, b""
    while off + 8 <= min(length, len(data)):
        clen, ctype = struct.unpack("<II", data[off:off + 8])
        chunk = data[off + 8:off + 8 + clen]
        if ctype == 0x4E4F534A:
            gl = json.loads(chunk.decode("utf-8"))
        elif ctype == 0x004E4942:
            binary = chunk
        off += 8 + clen
    return gl, binary


def accessor(gl, binary, index):
    """Flat tuple of numbers of one accessor (byteStride aware)."""
    a = gl["accessors"][index]
    bv = gl["bufferViews"][a["bufferView"]]
    n = NCOMP[a["type"]]
    code = CT[a["componentType"]]
    size = CT_SIZE[a["componentType"]]
    start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    stride = bv.get("byteStride") or size * n
    out = []
    for i in range(a["count"]):
        out.extend(struct.unpack_from("<" + code * n, binary, start + i * stride))
    return out


def image_header_size(data):
    """(width, height, format) from PNG, JPEG or WebP bytes."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", data[16:24]) + ("png",)
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h, "jpeg"
            size = struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + size
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        kind = data[12:16]
        if kind == b"VP8X":
            w = 1 + int.from_bytes(data[24:27], "little")
            h = 1 + int.from_bytes(data[27:30], "little")
            return w, h, "webp"
        if kind == b"VP8 ":
            w, h = struct.unpack("<HH", data[26:30])
            return w & 0x3FFF, h & 0x3FFF, "webp"
    return 0, 0, "?"


def load(path):
    """(positions, triangles, uvs, texture) of the first primitive of a .glb."""
    gl, binary = read_glb(path)
    mesh = gl["meshes"][0]
    prim = mesh["primitives"][0]
    pos = accessor(gl, binary, prim["attributes"]["POSITION"])
    points = [(pos[i], pos[i + 1], pos[i + 2]) for i in range(0, len(pos), 3)]
    if "indices" in prim:
        idx = accessor(gl, binary, prim["indices"])
    else:
        idx = list(range(len(points)))
    tris = [(idx[i], idx[i + 1], idx[i + 2]) for i in range(0, len(idx), 3)]
    uv = None
    if "TEXCOORD_0" in prim["attributes"]:
        raw = accessor(gl, binary, prim["attributes"]["TEXCOORD_0"])
        uv = [(raw[i], raw[i + 1]) for i in range(0, len(raw), 2)]
    tex = (0, 0, "?", 0)
    for img in gl.get("images", []):
        if "bufferView" in img:
            bv = gl["bufferViews"][img["bufferView"]]
            start = bv.get("byteOffset", 0)
            w, h, fmt = image_header_size(binary[start:start + bv["byteLength"]])
            tex = (w, h, fmt, bv["byteLength"])
            break
    has_normals = "NORMAL" in prim["attributes"]
    return points, tris, uv, tex, has_normals


def weld(points, tris, tol=1e-3):
    """(unique points, triangles, old index -> new index) with tol-metre merge."""
    key = {}
    index_of = [0] * len(points)
    unique = []
    step = 1.0 / tol
    for i, p in enumerate(points):
        k = (round(p[0] * step), round(p[1] * step), round(p[2] * step))
        j = key.get(k)
        if j is None:
            j = key[k] = len(unique)
            unique.append(p)
        index_of[i] = j
    out = []
    for t in tris:
        a, b, c = index_of[t[0]], index_of[t[1]], index_of[t[2]]
        if a != b and b != c and a != c:
            out.append((a, b, c))
    return unique, out, index_of


def edges_of(tris):
    """{(a, b) with a < b: number of faces using the edge}."""
    e = {}
    for a, b, c in tris:
        for x, y in ((a, b), (b, c), (c, a)):
            k = (x, y) if x < y else (y, x)
            e[k] = e.get(k, 0) + 1
    return e


def components(tris, nvert):
    """Connected component id for every welded vertex."""
    parent = list(range(nvert))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, c in tris:
        ra, rb, rc = find(a), find(b), find(c)
        parent[rb] = ra
        parent[find(rc)] = ra
    return [find(i) for i in range(nvert)]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def tri_area(p0, p1, p2):
    n = cross(sub(p1, p0), sub(p2, p0))
    return 0.5 * math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2)


def eigen_small_to_large(points):
    """Standard deviations along the principal axes (len 3, ascending)."""
    n = len(points)
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n
    cz = sum(p[2] for p in points) / n
    xx = xy = xz = yy = yz = zz = 0.0
    for p in points:
        x, y, z = p[0] - cx, p[1] - cy, p[2] - cz
        xx += x * x; yy += y * y; zz += z * z
        xy += x * y; xz += x * z; yz += y * z
    a = [[xx / n, xy / n, xz / n], [xy / n, yy / n, yz / n], [xz / n, yz / n, zz / n]]
    v = [[1.0, 0, 0], [0, 1.0, 0], [0, 0, 1.0]]
    for _ in range(24):  # Jacobi rotations
        off = abs(a[0][1]) + abs(a[0][2]) + abs(a[1][2])
        if off < 1e-12:
            break
        for p, q in ((0, 1), (0, 2), (1, 2)):
            if abs(a[p][q]) < 1e-15:
                continue
            theta = (a[q][q] - a[p][p]) / (2 * a[p][q])
            t = (1 if theta >= 0 else -1) / (abs(theta) + math.sqrt(theta * theta + 1))
            c = 1 / math.sqrt(t * t + 1)
            s = t * c
            for k in range(3):
                akp, akq = a[k][p], a[k][q]
                a[k][p] = c * akp - s * akq
                a[k][q] = s * akp + c * akq
            for k in range(3):
                apk, aqk = a[p][k], a[q][k]
                a[p][k] = c * apk - s * aqk
                a[q][k] = s * apk + c * aqk
            for k in range(3):
                vkp, vkq = v[k][p], v[k][q]
                v[k][p] = c * vkp - s * vkq
                v[k][q] = s * vkp + c * vkq
    return sorted(math.sqrt(max(a[i][i], 0.0)) for i in range(3))


def hull_area(points2d):
    """Area of the convex hull of 2D points (monotone chain)."""
    pts = sorted(set(points2d))
    if len(pts) < 3:
        return 0.0

    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2:
                (x1, y1), (x2, y2) = out[-2], out[-1]
                if (x2 - x1) * (p[1] - y1) - (y2 - y1) * (p[0] - x1) <= 0:
                    out.pop()
                else:
                    break
            out.append(p)
        return out

    lower = half(pts)
    upper = half(reversed(pts))
    poly = lower[:-1] + upper[:-1]
    s = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


def uv_usage(uv, tris):
    """(u range, v range, share of a 64x64 atlas covered by triangles)."""
    us = [uv[i][0] for t in tris for i in t]
    vs = [uv[i][1] for t in tris for i in t]
    grid = [[False] * 64 for _ in range(64)]
    for t in tris:
        (u0, v0), (u1, v1), (u2, v2) = uv[t[0]], uv[t[1]], uv[t[2]]
        x0 = max(0, min(63, int(min(u0, u1, u2) * 64)))
        x1 = max(0, min(63, int(max(u0, u1, u2) * 64)))
        y0 = max(0, min(63, int(min(v0, v1, v2) * 64)))
        y1 = max(0, min(63, int(max(v0, v1, v2) * 64)))
        for y in range(y0, y1 + 1):
            row = grid[y]
            for x in range(x0, x1 + 1):
                row[x] = True
    used = sum(sum(1 for c in row if c) for row in grid)
    return (min(us), max(us)), (min(vs), max(vs)), used / (64 * 64)


def measure(points, tris, uv, tex, has_normals, height=None):
    """All the numbers the report is built from."""
    nvert = len(points)
    uniq, wtris, _ = weld(points, tris)
    e = edges_of(wtris)
    border = sum(1 for c in e.values() if c == 1)
    nonman = sum(1 for c in e.values() if c > 2)

    comp = components(wtris, len(uniq))
    per_comp_v = {}
    per_comp_area = {}
    for i, c in enumerate(comp):
        per_comp_v[c] = per_comp_v.get(c, 0) + 1
    for t in wtris:
        c = comp[t[0]]
        per_comp_area[c] = per_comp_area.get(c, 0.0)
    for t in wtris:
        per_comp_area[comp[t[0]]] += tri_area(uniq[t[0]], uniq[t[1]], uniq[t[2]])
    area = sum(per_comp_area.values())
    biggest = max(per_comp_v.values()) if per_comp_v else 0
    junk = {c for c, v in per_comp_v.items()
            if v < 200 and v < 0.02 * biggest}
    debris_v = sum(per_comp_v[c] for c in junk)
    debris_area = sum(per_comp_area.get(c, 0.0) for c in junk)

    sd = eigen_small_to_large(uniq)
    flatness = sd[0] / sd[2] if sd[2] > 0 else 0.0

    xs = [p[0] for p in uniq]; ys = [p[1] for p in uniq]; zs = [p[2] for p in uniq]
    size = (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))

    foot = hull_area([(p[0], p[1]) for p in uniq])
    expected = 2 * foot + 2 * (size[0] + size[1]) * size[2] if size[2] > 0 else 0
    coverage = area / expected if expected > 0 else 0.0

    centre = (sum(xs) / nvert, sum(ys) / nvert, sum(zs) / nvert)
    inward = 0
    for a, b, c in wtris:
        p0, p1, p2 = uniq[a], uniq[b], uniq[c]
        n = cross(sub(p1, p0), sub(p2, p0))
        mid = ((p0[0] + p1[0] + p2[0]) / 3 - centre[0],
               (p0[1] + p1[1] + p2[1]) / 3 - centre[1],
               (p0[2] + p1[2] + p2[2]) / 3 - centre[2])
        if n[0] * mid[0] + n[1] * mid[1] + n[2] * mid[2] < 0:
            inward += 1
    inward /= max(len(wtris), 1)

    texture = {"px": tex[0], "height_px": tex[1], "format": tex[2],
               "bytes": tex[3]}
    if uv and tex[0] > 0:
        (u0, u1), (v0, v1), used = uv_usage(uv, tris)
        texture.update({"u": [round(u0, 3), round(u1, 3)],
                        "v": [round(v0, 3), round(v1, 3)],
                        "atlas_used": round(used, 3)})

    report = {
        "triangles": len(tris),
        "vertices": nvert,
        "welded_vertices": len(uniq),
        "border_edges": border,
        "non_manifold_edges": nonman,
        "components": len(per_comp_v),
        "debris_vertices": debris_v,
        "debris_area_m2": round(debris_area, 1),
        "area_m2": round(area, 1),
        "footprint_m2": round(foot, 1),
        "size_m": [round(v, 2) for v in size],
        "coverage": round(coverage, 2),
        "flatness": round(flatness, 3),
        "thickness_90_m": None,
        "inward_share": round(inward, 3),
        "normals": has_normals,
        "texture": texture,
    }
    # thickness holding 90 % of the points along the flattest axis
    if sd[2] > 0:
        # cheap: use the bbox extent along the axis of least spread is not
        # available, so report 2 sigma of the smallest deviation instead
        report["thickness_90_m"] = round(2 * sd[0], 2)
    if height:
        report["scale_needed"] = round(height / max(size[2], 0.01), 3)
    return report


def verdict(r, height=None):
    """(reshoot?, [reasons]) in plain words."""
    bad, fix = [], []
    if r["flatness"] < 0.15:
        bad.append(f"mesh is {r['thickness_90_m']} m thick over a "
                   f"{max(r['size_m'])} m bounding box: it is a facade sheet, "
                   "not a volume")
    if r["coverage"] < 0.45:
        bad.append(f"only {int(r['coverage'] * 100)} % of the walls were "
                   "reconstructed, the rest was never seen")
    if r["border_edges"] > 200:
        fix.append(f"{r['border_edges']} border edges (open holes)")
    if r["inward_share"] > 0.5:
        fix.append(f"{int(r['inward_share'] * 100)} % of faces point inwards")
    if not r["normals"]:
        fix.append("no NORMAL attribute, flat shading in every viewer")
    if r["debris_vertices"]:
        fix.append(f"{r['debris_vertices']} vertices of floating debris")
    tex = r["texture"]
    if tex.get("px", 0) > 4096:
        fix.append(f"texture {tex['px']} px is over the 4096 budget")
    if tex.get("atlas_used", 1) < 0.5:
        fix.append(f"atlas uses {int(tex['atlas_used'] * 100)} % of the image")
    if height and r.get("scale_needed") and abs(r["scale_needed"] - 1) > 0.25:
        fix.append(f"height {r['size_m'][2]} m vs {height} m in OSM, "
                   f"scale {r['scale_needed']}x")
    return bad, fix


def fix_winding(uniq, tris):
    """Make neighbouring faces agree, then turn the whole shell outwards."""
    edge_faces = {}
    for i, (a, b, c) in enumerate(tris):
        for x, y in ((a, b), (b, c), (c, a)):
            key = (x, y) if x < y else (y, x)
            edge_faces.setdefault(key, []).append((i, x < y))

    flip = [None] * len(tris)
    for start in range(len(tris)):
        if flip[start] is not None:
            continue
        flip[start] = False
        stack = [start]
        while stack:
            i = stack.pop()
            a, b, c = tris[i]
            for x, y in ((a, b), (b, c), (c, a)):
                key = (x, y) if x < y else (y, x)
                same = x < y
                for j, other in edge_faces[key]:
                    if j == i or flip[j] is not None:
                        continue
                    # two faces agree when they walk the shared edge in
                    # opposite directions; parity follows from the neighbour
                    flip[j] = (same == other) != flip[i]
                    stack.append(j)

    out = [list(t[::-1]) if flip[i] else list(t) for i, t in enumerate(tris)]

    volume = 0.0
    for a, b, c in out:
        p0, p1, p2 = uniq[a], uniq[b], uniq[c]
        volume += (p0[0] * (p1[1] * p2[2] - p1[2] * p2[1])
                   - p0[1] * (p1[0] * p2[2] - p1[2] * p2[0])
                   + p0[2] * (p1[0] * p2[1] - p1[1] * p2[0])) / 6.0
    if volume < 0:
        out = [[t[0], t[2], t[1]] for t in out]
    return out


def count_at(uniq, tris, cell):
    """How many triangles survive a clustering at this cell size."""
    step = 1.0 / cell
    key = {}
    remap = [0] * len(uniq)
    for i, p in enumerate(uniq):
        k = (round(p[0] * step), round(p[1] * step), round(p[2] * step))
        remap[i] = key.setdefault(k, len(key))
    kept = 0
    for a, b, c in tris:
        ra, rb, rc = remap[a], remap[b], remap[c]
        if ra != rb and rb != rc and ra != rc:
            kept += 1
    return kept


def decimate(uniq, tris, target):
    """Cell size for clustering: the coarsest cell that still keeps `target` faces.

    Returns 0 when the mesh already fits the budget and must be left alone --
    clustering a 12-triangle box would only destroy it.
    """
    if target <= 0 or len(tris) <= target:
        return 0.0
    area = sum(tri_area(uniq[a], uniq[b], uniq[c]) for a, b, c in tris)
    edge = math.sqrt(2 * area / max(len(tris), 1))
    cell = edge * 4.0
    for _ in range(20):
        if count_at(uniq, tris, cell) >= target:
            return cell
        cell *= 0.75
        if cell < edge * 0.05:
            break
    return max(cell, edge * 0.05)


def rebuild(uniq, tris, uv, cell):
    """Decimated mesh: (positions, normals, uvs, triangles) with split corners."""
    if cell <= 0:  # under budget already: keep the geometry, just split corners
        cluster = list(range(len(uniq)))
        pos_of = dict(enumerate(uniq))
        kept = list(tris)
    else:
        step = 1.0 / cell
        cluster = []
        centres, counts = {}, {}
        for p in uniq:
            k = (round(p[0] * step), round(p[1] * step), round(p[2] * step))
            cluster.append(k)
            c = centres.get(k, (0.0, 0.0, 0.0))
            centres[k] = (c[0] + p[0], c[1] + p[1], c[2] + p[2])
            counts[k] = counts.get(k, 0) + 1
        pos_of = {k: (v[0] / counts[k], v[1] / counts[k], v[2] / counts[k])
                  for k, v in centres.items()}
        kept = []
        seen_tri = set()
        for a, b, c in tris:
            ka, kb, kc = cluster[a], cluster[b], cluster[c]
            if ka != kb and kb != kc and ka != kc:
                # two faces merge only if they also share the same texture
                # corners; separate UV islands often meet on one grid vertex
                key = tuple(sorted(
                    (k, round(uv[i][0], 4), round(uv[i][1], 4)) if uv else (k, 0.0, 0.0)
                    for k, i in ((ka, a), (kb, b), (kc, c))))
                if key in seen_tri:
                    continue
                seen_tri.add(key)
                kept.append((a, b, c))

    # area-weighted normals per cluster, for smooth shading across UV seams
    normals = {}
    for a, b, c in kept:
        p0, p1, p2 = pos_of[cluster[a]], pos_of[cluster[b]], pos_of[cluster[c]]
        n = cross(sub(p1, p0), sub(p2, p0))
        for k in (cluster[a], cluster[b], cluster[c]):
            acc = normals.get(k, (0.0, 0.0, 0.0))
            normals[k] = (acc[0] + n[0], acc[1] + n[1], acc[2] + n[2])

    positions, norms, uvs, indices = [], [], [], []
    seen = {}
    for a, b, c in kept:
        tri = []
        for corner in (a, b, c):
            k = cluster[corner]
            u, v = uv[corner] if uv else (0.0, 0.0)
            key = (k, round(u, 4), round(v, 4))
            i = seen.get(key)
            if i is None:
                i = seen[key] = len(positions)
                positions.append(pos_of[k])
                n = normals[k]
                ln = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2) or 1.0
                norms.append((n[0] / ln, n[1] / ln, n[2] / ln))
                uvs.append((u, v))
            tri.append(i)
        indices.extend(tri)
    return positions, norms, uvs, indices


def write_glb(path, positions, normals, uvs, indices, texture_bytes, mime):
    """One mesh, one primitive, one texture -- a file any engine opens."""
    with_uv = bool(texture_bytes and uvs)
    chunks = [b"".join(struct.pack("<3f", *p) for p in positions),
              b"".join(struct.pack("<3f", *n) for n in normals)]
    if with_uv:
        chunks.append(b"".join(struct.pack("<2f", *t) for t in uvs))
    chunks.append(b"".join(struct.pack("<I", i) for i in indices))
    if texture_bytes:
        chunks.append(texture_bytes)
    views, offset = [], 0
    for c in chunks:
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(c)})
        offset += len(c) + (-len(c) % 4)
    binary = b"".join(c + b"\0" * (-len(c) % 4) for c in chunks)
    mn = [min(p[i] for p in positions) for i in range(3)]
    mx = [max(p[i] for p in positions) for i in range(3)]

    attributes = {"POSITION": 0, "NORMAL": 1}
    accessors = [
        {"bufferView": 0, "componentType": 5126, "count": len(positions),
         "type": "VEC3", "min": mn, "max": mx},
        {"bufferView": 1, "componentType": 5126, "count": len(normals),
         "type": "VEC3"},
    ]
    view = 2
    if with_uv:
        attributes["TEXCOORD_0"] = len(accessors)
        accessors.append({"bufferView": view, "componentType": 5126,
                          "count": len(uvs), "type": "VEC2"})
        view += 1
    index_accessor = len(accessors)
    accessors.append({"bufferView": view, "componentType": 5125,
                      "count": len(indices), "type": "SCALAR"})

    gl = {
        "asset": {"version": "2.0", "generator": "fix_scan"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": attributes,
                                    "indices": index_accessor, "material": 0}]}],
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(binary)}],
    }
    if texture_bytes:
        gl["images"] = [{"bufferView": view + 1, "mimeType": mime}]
        gl["samplers"] = [{"magFilter": 9729, "minFilter": 9987,
                           "wrapS": 33071, "wrapT": 33071}]
        gl["textures"] = [{"sampler": 0, "source": 0}]
        gl["materials"] = [{"pbrMetallicRoughness": {
            "baseColorTexture": {"index": 0},
            "metallicFactor": 0.0, "roughnessFactor": 0.9}}]
    else:
        gl["materials"] = [{"pbrMetallicRoughness": {
            "baseColorFactor": [0.6, 0.6, 0.6, 1.0],
            "metallicFactor": 0.0, "roughnessFactor": 0.9}}]

    js = json.dumps(gl, separators=(",", ":")).encode("utf-8")
    js += b" " * (-len(js) % 4)
    total = 12 + 8 + len(js) + 8 + len(binary)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
        f.write(struct.pack("<II", len(binary), 0x004E4942) + binary)


def normals_from_faces(positions, indices):
    """Per-vertex normals averaged over the faces that share the vertex."""
    acc = [[0.0, 0.0, 0.0] for _ in positions]
    for i in range(0, len(indices), 3):
        a, b, c = indices[i], indices[i + 1], indices[i + 2]
        n = cross(sub(positions[b], positions[a]), sub(positions[c], positions[a]))
        for k in (a, b, c):
            v = acc[k]
            v[0] += n[0]; v[1] += n[1]; v[2] += n[2]
    out = []
    for v in acc:
        ln = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2) or 1.0
        out.append((v[0] / ln, v[1] / ln, v[2] / ln))
    return out


def collision_hull(uniq, levels=1):
    """Boxes standing in for the walls: the footprint extruded, split by level.

    Assumes the walls are vertical, which is what a photogrammetry tower is.
    A footprint that is much smaller than its bounding box (a sheet, a broken
    scan) is replaced by that bounding box: a thin wedge is worse than a
    slightly too big box for walking into.
    """
    pts = sorted({(round(p[0], 2), round(p[1], 2)) for p in uniq})
    if len(pts) < 3:
        sys.exit("fix_scan: nothing to build a collision from")
    chains = []
    for seq in (pts, list(reversed(pts))):
        part = []
        for p in seq:
            while len(part) >= 2:
                (x1, y1), (x2, y2) = part[-2], part[-1]
                if (x2 - x1) * (p[1] - y1) - (y2 - y1) * (p[0] - x1) <= 0:
                    part.pop()
                else:
                    break
            part.append(p)
        chains.append(part)
    poly = chains[0][:-1] + chains[1][:-1]
    signed = sum(poly[i][0] * poly[(i + 1) % len(poly)][1]
                 - poly[(i + 1) % len(poly)][0] * poly[i][1]
                 for i in range(len(poly)))
    hull = abs(signed) / 2
    if signed < 0:
        poly.reverse()
    x0 = min(p[0] for p in pts); x1 = max(p[0] for p in pts)
    y0 = min(p[1] for p in pts); y1 = max(p[1] for p in pts)
    box = (x1 - x0) * (y1 - y0)
    if box > 0 and hull < 0.75 * box:
        poly = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

    z0 = min(p[2] for p in uniq)
    z1 = max(p[2] for p in uniq)
    positions, indices = [], []
    for level in range(levels):
        a = z0 + (z1 - z0) * level / levels
        b = z0 + (z1 - z0) * (level + 1) / levels
        base = len(positions)
        for x, y in poly:
            positions.append((x, y, a))
            positions.append((x, y, b))
        n = len(poly)
        for i in range(n):
            j = (i + 1) % n
            p, q = base + 2 * i, base + 2 * j
            indices.extend([p, q, q + 1, p, q + 1, p + 1])   # walls
        for i in range(1, n - 1):                          # floor and ceiling
            indices.extend([base, base + 2 * (i + 1), base + 2 * i])
            indices.extend([base + 1, base + 2 * i + 1, base + 2 * (i + 1) + 1])
    normals = normals_from_faces(positions, indices)
    return positions, normals, [(0.0, 0.0)] * len(positions), indices


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("glb")
    ap.add_argument("--out", help="write the repaired model here")
    ap.add_argument("--height", type=float,
                    help="OSM height in metres, for the scale check")
    ap.add_argument("--triangles", type=int, default=0,
                    help="decimate to about this many triangles (0 = keep as is)")
    ap.add_argument("--collision",
                    help="write a coarse collision .glb here as well")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    points, tris, uv, tex, has_normals = load(a.glb)
    # glTF is Y-up; the city frame is Z-up: (x, y, z) -> (x, -z, y)
    points = [(p[0], -p[2], p[1]) for p in points]
    report = measure(points, tris, uv, tex, has_normals, a.height)
    bad, fixable = verdict(report, a.height)
    report["verdict"] = "reshoot" if bad else ("repair" if fixable else "ok")

    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        t = report["texture"]
        print(f"{a.glb}")
        print(f"  {report['triangles']} triangles, {report['vertices']} vertices, "
              f"{report['components']} pieces")
        print(f"  size {report['size_m'][0]} x {report['size_m'][1]} x "
              f"{report['size_m'][2]} m, footprint {report['footprint_m2']} m2")
        print(f"  area {report['area_m2']} m2 = {int(report['coverage'] * 100)} % "
              "of the walls a closed shell would have")
        print(f"  flatness {report['flatness']} (1.0 = a cube, 0.05 = a sheet), "
              f"90 % of points within {report['thickness_90_m']} m")
        print(f"  {report['border_edges']} border edges, "
              f"{report['non_manifold_edges']} non-manifold, "
              f"{report['debris_vertices']} debris vertices")
        print(f"  {int(report['inward_share'] * 100)} % of faces point inwards, "
              f"normals {'yes' if has_normals else 'no'}")
        if t["px"]:
            print(f"  texture {t['px']}x{t['height_px']} {t['format']} "
                  f"{t['bytes'] / 1e6:.1f} MB, atlas used "
                  f"{int(t.get('atlas_used', 0) * 100)} %, "
                  f"u {t.get('u')} v {t.get('v')}")
        if report.get("scale_needed"):
            print(f"  scale to OSM height: {report['scale_needed']}x")
        print()
        for r in bad:
            print(f"  CAPTURE: {r}")
        for r in fixable:
            print(f"  MESH: {r}")
        if bad:
            print("\n  Verdict: reshoot. No script turns a facade sheet into a "
                  "building; put it back in the shot list.")
        elif not fixable:
            print("\n  Verdict: ok.")
        else:
            print("\n  Verdict: repair (--out) fixes the mesh part.")

    if not a.out:
        return

    uniq, wtris, _ = weld(points, tris)
    # drop debris before anything else, it only confuses the winding pass
    comp = components(wtris, len(uniq))
    counts = {}
    for c in comp:
        counts[c] = counts.get(c, 0) + 1
    biggest = max(counts.values()) if counts else 0
    keep = {c for c, n in counts.items() if not (n < 200 and n < 0.02 * biggest)}
    if keep:
        wtris = [t for t in wtris if comp[t[0]] in keep]
    wtris = fix_winding(uniq, wtris)
    cell = decimate(uniq, wtris, a.triangles)
    positions, normals, uvs, indices = rebuild(uniq, wtris, uv, cell)
    # the city frame is Z-up, glTF is Y-up: (X, Y, Z) -> (X, Z, -Y)
    positions = [(p[0], p[2], -p[1]) for p in positions]
    normals = [(n[0], n[2], -n[1]) for n in normals]

    gl, binary = read_glb(a.glb)
    texture, mime = b"", "image/png"
    if gl.get("images") and "bufferView" in gl["images"][0]:
        bv = gl["bufferViews"][gl["images"][0]["bufferView"]]
        start = bv.get("byteOffset", 0)
        texture = binary[start:start + bv["byteLength"]]
        mime = gl["images"][0].get("mimeType") or "image/png"
    if not indices:
        sys.exit("fix_scan: nothing left after cleaning, raise --triangles")
    write_glb(a.out, positions, normals, uvs, indices, texture, mime)
    out_len = len(indices) // 3
    print(f"\nwrote {a.out}: {out_len} triangles "
          f"({report['triangles'] / max(out_len, 1):.1f}x fewer), "
          f"{len(positions)} vertices, normals yes"
          + (f", cell {cell * 100:.1f} cm" if cell else ""))

    if a.collision:
        p, n, t, i = collision_hull(uniq)
        p = [(q[0], q[2], -q[1]) for q in p]
        n = [(q[0], q[2], -q[1]) for q in n]
        write_glb(a.collision, p, n, t, i, b"", "")
        print(f"wrote {a.collision}: {len(i) // 3} collision triangles")


if __name__ == "__main__":
    main()
