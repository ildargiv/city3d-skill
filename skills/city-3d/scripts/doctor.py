#!/usr/bin/env python3
"""Check the machine for everything the city-3d skill uses, in one call.

  python3 doctor.py [--blender <exe or folder>] [--colmap <folder>] [--openmvs <folder>] [--json]

Prints one line per tool: found or missing, what it is for and where to get
it. Nothing is installed or downloaded. Run it first on a new machine and
whenever a step fails with "not found".
"""
import argparse
import glob
import importlib.util
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from photogrammetry import tool  # noqa: E402


def find_blender(hint):
    if hint and os.path.isfile(hint):
        return hint
    cands = [os.path.join(hint, "blender.exe"), os.path.join(hint, "blender")] if hint else []
    cands += glob.glob(r"C:\Program Files\Blender Foundation\Blender*\blender.exe")
    cands += ["/Applications/Blender.app/Contents/MacOS/Blender"]
    for c in cands:
        if os.path.isfile(c):
            return c
    return shutil.which("blender")


def version(cmd):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    text = (out.stdout or out.stderr).strip().splitlines()
    return text[0][:60] if text else ""


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--blender")
    ap.add_argument("--colmap")
    ap.add_argument("--openmvs")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    blender = find_blender(a.blender)
    colmap = tool("colmap", a.colmap) or tool("COLMAP.bat", a.colmap)
    mvs = tool("DensifyPointCloud", a.openmvs)
    module = lambda m: importlib.util.find_spec(m) is not None
    rows = [
        ("python", sys.version_info >= (3, 9), sys.version.split()[0],
         "all scripts", "https://www.python.org/downloads/ (3.9+)"),
        ("blender", bool(blender), blender and version([blender, "--version"]),
         "scene, .glb/.usd export, placing scans", "https://www.blender.org/download/ (4.2+)"),
        ("ffmpeg", bool(shutil.which("ffmpeg")), version(["ffmpeg", "-version"]),
         "cutting video into frames", "https://ffmpeg.org/download.html"),
        ("colmap", bool(colmap), colmap or "",
         "camera poses from frames", "https://github.com/colmap/colmap/releases (nocuda zip)"),
        ("openmvs", bool(mvs), mvs or "",
         "dense mesh and texture", "https://github.com/cdcseacave/openMVS/releases"),
        ("pillow", module("PIL"), "", "sharp-frame picking, photo GPS",
         "pip install pillow"),
        ("rawpy+imageio", module("rawpy") and module("imageio"), "",
         "raw photos (DNG) to JPG", "pip install rawpy imageio"),
        ("exiftool", bool(shutil.which("exiftool")), "",
         "GPS inside videos (optional)", "https://exiftool.org"),
    ]
    if a.json:
        print(json.dumps([{"tool": r[0], "ok": bool(r[1]), "info": r[2] or "", "for": r[3],
                           "get": r[4]} for r in rows], ensure_ascii=False, indent=1))
        return
    for name, ok, info, use, get in rows:
        mark = "OK  " if ok else "--  "
        print(f"{mark}{name:14} {info or '':40.40} {use}" + ("" if ok else f"  ->  {get}"))
    ok = {r[0]: r[1] for r in rows}
    print("\nblock city and preview: " + ("ready" if ok["python"] else "needs Python 3.9+"))
    print("Blender scene and export: " + ("ready" if ok["blender"] else "needs Blender"))
    miss = [n for n in ("ffmpeg", "colmap", "openmvs") if not ok[n]]
    print("models from photos/video: " + ("ready" if not miss else "needs " + ", ".join(miss)))


if __name__ == "__main__":
    main()
