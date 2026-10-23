#!/usr/bin/env python3
"""Cut videos into sharp still frames for photogrammetry (needs ffmpeg).

  python3 extract_frames.py clip1.mp4 clip2.mp4 --fps 3 --out frames/

For each output slot it decodes two candidates and keeps the sharper one,
so motion blur from walking is filtered out. Frames go to
<out>/<clip name>/<clip>_00001.jpg. Originals are never modified.
Sharpness needs Pillow (pip install pillow); without it every frame is kept.

Camera positions: if the clip has GPS (a DJI .SRT file next to it, or GPS
samples inside the video that exiftool can read), <out>/<clip>_gps.txt
gets one line per kept frame: "<frame> <lat> <lon> <alt>". Pass it to
photogrammetry.py --gps to get the model in metres, oriented and standing
on its place in the city. Most clips have no GPS; that is fine.
"""
import argparse
import bisect
import json
import os
import re
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


def srt_track(video):
    """DJI .SRT next to the clip -> [(seconds, lat, lon, alt)]."""
    base = os.path.splitext(video)[0]
    path = next((base + e for e in (".SRT", ".srt") if os.path.exists(base + e)), None)
    if not path:
        return []
    track = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        blocks = f.read().split("\n\n")
    for blk in blocks:
        t = re.search(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->", blk)
        lat = re.search(r"latitude\s*[:=]\s*(-?[\d.]+)", blk)
        lon = re.search(r"longi?tude\s*[:=]\s*(-?[\d.]+)", blk)
        if not (lat and lon):  # older firmware: GPS(lon, lat, alt)
            g = re.search(r"GPS\s*\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)", blk)
            if g:
                lon, lat, alt = g.group(1), g.group(2), g.group(3)
            else:
                continue
        else:
            lat, lon = lat.group(1), lon.group(1)
            a = re.search(r"(?:abs_alt|altitude)\s*[:=]\s*(-?[\d.]+)", blk)
            alt = a.group(1) if a else "nan"
        if t and float(lat) and float(lon):
            h, m, sec, ms = map(int, t.groups())
            track.append((h * 3600 + m * 60 + sec + ms / 1000, float(lat), float(lon), float(alt)))
    return track


def exif_track(video):
    """GPS samples embedded in the video, read by exiftool -ee if it is installed."""
    if not shutil.which("exiftool"):
        return []
    out = subprocess.run(["exiftool", "-ee", "-n", "-j", "-G3", "-SampleTime",
                          "-GPSLatitude", "-GPSLongitude", "-GPSAltitude", video],
                         capture_output=True, text=True)
    try:
        tags = json.loads(out.stdout)[0]
    except (ValueError, IndexError):
        return []
    docs = {}
    for k, v in tags.items():
        doc, _, name = k.rpartition(":")
        docs.setdefault(doc, {})[name] = v
    track = []
    for d in docs.values():
        if "GPSLatitude" in d and "GPSLongitude" in d and "SampleTime" in d:
            track.append((float(d["SampleTime"]), float(d["GPSLatitude"]),
                          float(d["GPSLongitude"]), float(d.get("GPSAltitude", "nan"))))
    return sorted(track)


def position_at(track, t):
    times = [p[0] for p in track]
    i = bisect.bisect_left(times, t)
    if i <= 0:
        return track[0][1:]
    if i >= len(track):
        return track[-1][1:]
    (t0, *a), (t1, *b) = track[i - 1], track[i]
    k = (t - t0) / (t1 - t0) if t1 > t0 else 0
    return tuple(x + (y - x) * k for x, y in zip(a, b))


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
            kept = []
            for n, g in enumerate(groups, 1):
                best = max(g, key=lambda f: sharpness(os.path.join(tmp, f))) if Image else g[0]
                out = f"{name}_{n:05d}.jpg"
                shutil.move(os.path.join(tmp, best), os.path.join(dest, out))
                # frame i of the fps filter sits at (i - 1) / rate seconds
                i = int(re.search(r"(\d+)", best).group(1))
                kept.append((out, (i - 1) / (a.fps * (2 if Image else 1))))
        track = srt_track(video) or exif_track(video)
        if track:
            gps = os.path.join(a.out, f"{name}_gps.txt")
            with open(gps, "w", encoding="utf-8") as f:
                for out, t in kept:
                    lat, lon, alt = position_at(track, t)
                    f.write(f"{out} {lat:.7f} {lon:.7f} {alt:.2f}\n")
            print(f"{video}: GPS for {len(kept)} frames -> {gps}")
        print(f"{video}: {len(groups)} frames -> {dest}" + ("" if track else " (no GPS)"))


if __name__ == "__main__":
    main()
