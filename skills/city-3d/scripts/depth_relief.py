#!/usr/bin/env python3
"""Depth from single photos with a metric depth network: a draft, not a measurement.

  python3 depth_relief.py photo1.jpg [photo2.jpg ...] --out depth/ [--model small|base|large] [--max-depth 80]

For each photo it writes into --out:
  <name>_depth.png   depth in millimetres, 16-bit grey (0 = sky / too far)
  <name>_depth.npy   the same in metres, float32 (needs numpy)
  <name>_relief.obj  a textured relief mesh in the camera frame (metres,
                     Y up, camera at the origin looking down -Z), cut where
                     the depth jumps so foreground and walls do not fuse
  <name>.json        source, model, field of view and "invented": true

Uses Depth Anything V2 Metric (outdoor) through Hugging Face transformers on
the CPU. Install once: pip install torch transformers pillow numpy (CPU
torch is enough); the model downloads on first use (small ~100 MB,
base ~400 MB, large ~1.3 GB).

What it is for, honestly: the network guesses depth from one picture. Big
shapes come out plausible and walls fairly flat, scale is roughly right, but
nothing is measured. Two uses:
- add fine relief (window recesses, balconies, cornices) to a measured
  photogrammetry facade: fit each depth map to the scan's depth with a
  scale and offset, keep only the small-scale difference;
- a draft wall from one photo where nothing else exists.
Either way the result is invented by the rule in SKILL.md: only with the
user's consent, and marked in coverage.json ("invented") and in the model.
"""
import argparse
import json
import math
import os
import sys

MODELS = {
    "small": "depth-anything/Depth-Anything-V2-Metric-Outdoor-Small-hf",
    "base": "depth-anything/Depth-Anything-V2-Metric-Outdoor-Base-hf",
    "large": "depth-anything/Depth-Anything-V2-Metric-Outdoor-Large-hf",
}


def fov_of(img, default=60.0):
    """Horizontal field of view from EXIF 35 mm focal length, else default."""
    try:
        f35 = img.getexif().get_ifd(0x8769).get(0xA405)
        if f35:
            return math.degrees(2 * math.atan(36 / (2 * float(f35))))
    except (AttributeError, KeyError, ValueError, ZeroDivisionError):
        pass
    return default


def write_mesh(path, depth, fov, texture, step, max_jump):
    """Grid mesh of the depth map; a quad is dropped where depth jumps."""
    h, w = len(depth), len(depth[0])
    f = (w / 2) / math.tan(math.radians(fov) / 2)
    idx, lines = {}, ["mtllib " + os.path.basename(path)[:-4] + ".mtl", "usemtl photo"]
    rows, cols = range(0, h, step), range(0, w, step)
    for r in rows:
        for c in cols:
            d = depth[r][c]
            if d <= 0:
                continue
            idx[(r, c)] = len(idx) + 1
            lines.append(f"v {(c - w / 2) / f * d:.3f} {-(r - h / 2) / f * d:.3f} {-d:.3f}")
            lines.append(f"vt {c / (w - 1):.5f} {1 - r / (h - 1):.5f}")
    for r in rows[:-1]:
        for c in cols[:-1]:
            q = [(r, c), (r + step, c), (r + step, c + step), (r, c + step)]
            if not all(k in idx for k in q):
                continue
            ds = [depth[k[0]][k[1]] for k in q]
            if max(ds) - min(ds) > max_jump * min(ds):
                continue
            a, b, cc, d = (idx[k] for k in q)
            lines.append(f"f {a}/{a} {b}/{b} {cc}/{cc}")
            lines.append(f"f {a}/{a} {cc}/{cc} {d}/{d}")
    with open(path, "w", encoding="utf-8") as fo:
        fo.write("\n".join(lines) + "\n")
    with open(path[:-4] + ".mtl", "w", encoding="utf-8") as fo:
        fo.write(f"newmtl photo\nKd 1 1 1\nmap_Kd {os.path.basename(texture)}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("photos", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", choices=sorted(MODELS), default="small")
    ap.add_argument("--max-depth", type=float, default=80.0, help="metres; farther is cut (sky)")
    ap.add_argument("--fov", type=float, help="horizontal field of view, degrees (else EXIF or 60)")
    ap.add_argument("--step", type=int, default=2, help="mesh grid step in depth pixels")
    ap.add_argument("--max-jump", type=float, default=0.08,
                    help="relative depth jump that cuts the mesh")
    a = ap.parse_args()
    try:
        import numpy as np
        import torch
        from PIL import Image
        from transformers import pipeline
    except ImportError as e:
        sys.exit(f"{e.name} is missing: pip install torch transformers pillow numpy")

    torch.set_num_threads(os.cpu_count() or 4)
    estimator = pipeline("depth-estimation", model=MODELS[a.model], device="cpu")
    os.makedirs(a.out, exist_ok=True)
    for photo in a.photos:
        name = os.path.splitext(os.path.basename(photo))[0]
        img = Image.open(photo).convert("RGB")
        fov = a.fov or fov_of(Image.open(photo))
        pred = estimator(img)["predicted_depth"]
        depth = torch.nn.functional.interpolate(
            pred[None, None] if pred.dim() == 2 else pred[None],
            size=img.size[::-1], mode="bilinear", align_corners=False)[0, 0].numpy()
        depth = np.where(depth > a.max_depth, 0, depth).astype("float32")
        np.save(os.path.join(a.out, f"{name}_depth.npy"), depth)
        Image.fromarray(np.clip(depth * 1000, 0, 65535).astype("uint16")).save(
            os.path.join(a.out, f"{name}_depth.png"))
        tex = os.path.join(a.out, f"{name}.jpg")
        img.save(tex, quality=92)
        write_mesh(os.path.join(a.out, f"{name}_relief.obj"), depth.tolist(), fov, tex,
                   a.step, a.max_jump)
        valid = depth[depth > 0]
        meta = {"source": os.path.abspath(photo), "model": MODELS[a.model], "fov_deg": round(fov, 1),
                "depth_m": [round(float(valid.min()), 2), round(float(np.median(valid)), 2),
                            round(float(valid.max()), 2)] if valid.size else None,
                "invented": True,
                "note": "neural depth estimate from one photo, not a measurement"}
        with open(os.path.join(a.out, f"{name}.json"), "w", encoding="utf-8") as fo:
            json.dump(meta, fo, ensure_ascii=False, indent=1)
        print(f"{photo}: depth {meta['depth_m']} m, fov {fov:.0f}° -> {a.out}")


if __name__ == "__main__":
    main()
