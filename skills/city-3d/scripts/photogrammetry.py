#!/usr/bin/env python3
"""Photos or video frames of one building -> textured mesh (COLMAP + OpenMVS).

  python3 photogrammetry.py frames/0033 --work work/0033 --stage sparse
  python3 photogrammetry.py frames/0033 --work work/0033 --stage mesh

Stages:
  sparse  camera poses + sparse points (COLMAP). Runs on CPU. Use it to judge
          a capture: registered images and points are printed at the end.
  mesh    dense cloud, mesh and texture (OpenMVS, CPU is fine but slow).
          Result: <work>/mvs/scene_textured.obj

Tools are found on PATH or via --colmap / --openmvs. Windows: the COLMAP
"nocuda" zip and the OpenMVS release zip both run without installing.
Without an NVIDIA card COLMAP's own dense step is unavailable; OpenMVS
does that part instead. RealityScan (Epic) gives better results on NVIDIA
machines and can replace this script entirely.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys


def tool(name, folder):
    for cand in ([os.path.join(folder, name), os.path.join(folder, name + ".exe"),
                  os.path.join(folder, "bin", name + ".exe"),
                  os.path.join(folder, "bin", name)] if folder else []) + [name]:
        path = shutil.which(cand) or (cand if os.path.isfile(cand) else None)
        if path:
            return path
    return None


def run(cmd):
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def pick_option(colmap, command, *names):
    """COLMAP renamed options between versions; use the one this build knows."""
    out = subprocess.run([colmap, command, "-h"], capture_output=True, text=True)
    text = out.stdout + out.stderr
    for n in names:
        if n in text:
            return n
    return None


def sparse(colmap, images, work, sequential):
    db = os.path.join(work, "database.db")
    model = os.path.join(work, "sparse")
    os.makedirs(model, exist_ok=True)
    gpu = pick_option(colmap, "feature_extractor",
                      "FeatureExtraction.use_gpu", "SiftExtraction.use_gpu")
    cmd = [colmap, "feature_extractor", "--database_path", db,
           "--image_path", images, "--ImageReader.single_camera", "1",
           "--ImageReader.camera_model", "OPENCV"]
    if gpu:
        cmd += [f"--{gpu}", "0"]
    run(cmd)
    matcher = "sequential_matcher" if sequential else "exhaustive_matcher"
    gpu = pick_option(colmap, matcher, "FeatureMatching.use_gpu", "SiftMatching.use_gpu")
    cmd = [colmap, matcher, "--database_path", db]
    if gpu:
        cmd += [f"--{gpu}", "0"]
    run(cmd)
    run([colmap, "mapper", "--database_path", db, "--image_path", images,
         "--output_path", model])
    models = sorted(d for d in os.listdir(model) if d.isdigit())
    if not models:
        sys.exit("No model: the images did not connect. Usually the camera "
                 "only turned on one spot, or frames overlap too little.")
    best, best_n = None, -1
    for m in models:  # several disconnected pieces: keep the largest
        res = subprocess.run([colmap, "model_analyzer", "--path",
                              os.path.join(model, m)], capture_output=True, text=True)
        text = res.stdout + res.stderr
        n = int((re.search(r"Registered images:\s*(\d+)", text) or [0, 0])[1])
        print(f"model {m}:\n{text.strip()}")
        if n > best_n:
            best, best_n = m, n
    total = len([f for f in os.listdir(images)
                 if f.lower().endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff"))])
    print(f"\nBEST MODEL {best}: {best_n} of {total} images registered")
    return os.path.join(model, best)


def mesh(colmap, openmvs, images, work, model):
    dense = os.path.join(work, "dense")
    run([colmap, "image_undistorter", "--image_path", images, "--input_path", model,
         "--output_path", dense, "--output_type", "COLMAP"])
    mvs = os.path.join(work, "mvs")
    os.makedirs(mvs, exist_ok=True)
    t = {n: tool(n, openmvs) for n in ("InterfaceCOLMAP", "DensifyPointCloud",
                                       "ReconstructMesh", "RefineMesh", "TextureMesh")}
    missing = [n for n, p in t.items() if not p and n != "RefineMesh"]
    if missing:
        sys.exit(f"OpenMVS tools not found: {', '.join(missing)}. Pass --openmvs <folder>.")
    scene = os.path.join(mvs, "scene.mvs")
    run([t["InterfaceCOLMAP"], "-i", dense, "-o", scene, "--image-folder",
         os.path.join(dense, "images")])
    run([t["DensifyPointCloud"], scene, "--resolution-level", "1", "-w", mvs])
    run([t["ReconstructMesh"], os.path.join(mvs, "scene_dense.mvs"), "-w", mvs])
    meshed = os.path.join(mvs, "scene_dense_mesh.mvs")
    if t["RefineMesh"]:
        run([t["RefineMesh"], meshed, "--resolution-level", "2", "-w", mvs])
        meshed = os.path.join(mvs, "scene_dense_mesh_refine.mvs")
    run([t["TextureMesh"], meshed, "--export-type", "obj", "-o",
         os.path.join(mvs, "scene_textured.mvs"), "-w", mvs])
    print(f"\nMESH: {os.path.join(mvs, 'scene_textured.obj')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("images", help="folder with the photos or frames")
    ap.add_argument("--work", required=True)
    ap.add_argument("--stage", choices=["sparse", "mesh"], default="sparse")
    ap.add_argument("--photos", action="store_true",
                    help="unordered photos (exhaustive matching) instead of video frames")
    ap.add_argument("--colmap", help="folder with colmap(.exe or .bat)")
    ap.add_argument("--openmvs", help="folder with the OpenMVS tools")
    a = ap.parse_args()
    colmap = tool("colmap", a.colmap) or tool("COLMAP.bat", a.colmap)
    if not colmap:
        sys.exit("COLMAP not found. Download it from "
                 "https://github.com/colmap/colmap/releases and pass --colmap <folder>.")
    os.makedirs(a.work, exist_ok=True)
    model = os.path.join(a.work, "sparse", "0")
    if a.stage == "sparse" or not os.path.isdir(model):
        model = sparse(colmap, a.images, a.work, sequential=not a.photos)
    if a.stage == "mesh":
        mesh(colmap, a.openmvs, a.images, a.work, model)


if __name__ == "__main__":
    main()
