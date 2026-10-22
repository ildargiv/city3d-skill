#!/usr/bin/env python3
"""Cut videos into sharp still frames for photogrammetry (needs ffmpeg).

  python3 extract_frames.py clip1.mp4 clip2.mp4 --fps 3 --out frames/

For each output slot it decodes two candidates and keeps the sharper one,
so motion blur from walking is filtered out. Frames go to
<out>/<clip name>/<clip>_00001.jpg. Originals are never modified.
Sharpness needs Pillow (pip install pillow); without it every frame is kept.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

try:
    from PIL import Image, ImageFilter, ImageStat
except ImportError:
    Image = None


def sharpness(path):
    with Image.open(path) as im:
        g = im.convert("L")
        g.thumbnail((960, 960))
        edges = g.filter(ImageFilter.FIND_EDGES)
        return ImageStat.Stat(edges).var[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("videos", nargs="+")
    ap.add_argument("--fps", type=float, default=3.0, help="frames kept per second")
    ap.add_argument("--out", default="frames")
    ap.add_argument("--max-width", type=int, default=0,
                    help="downscale (0 = keep full resolution)")
    a = ap.parse_args()
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found: install it and add it to PATH")

    for video in a.videos:
        name = os.path.splitext(os.path.basename(video))[0]
        dest = os.path.join(a.out, name)
        os.makedirs(dest, exist_ok=True)
        vf = [f"fps={a.fps * (2 if Image else 1)}"]
        if a.max_width:
            vf.append(f"scale='min({a.max_width},iw)':-2")
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["ffmpeg", "-v", "error", "-i", video, "-vf", ",".join(vf),
                            "-q:v", "2", os.path.join(tmp, "f_%06d.jpg")], check=True)
            frames = sorted(os.listdir(tmp))
            groups = ([frames[i:i + 2] for i in range(0, len(frames), 2)]
                      if Image else [[f] for f in frames])
            for n, g in enumerate(groups, 1):
                best = max(g, key=lambda f: sharpness(os.path.join(tmp, f))) if Image else g[0]
                shutil.move(os.path.join(tmp, best),
                            os.path.join(dest, f"{name}_{n:05d}.jpg"))
        print(f"{video}: {len(groups)} frames -> {dest}")


if __name__ == "__main__":
    main()
