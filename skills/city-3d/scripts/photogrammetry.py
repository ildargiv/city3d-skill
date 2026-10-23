#!/usr/bin/env python3
"""Photos or video frames of one building -> textured mesh (COLMAP + OpenMVS).

  python3 photogrammetry.py frames/0033 --work work/0033 --stage sparse
  python3 photogrammetry.py frames/0033 --work work/0033 --stage mesh

Stages:
  sparse  camera poses + sparse points (COLMAP). Runs on CPU. Use it to judge
          a capture: registered images and points are printed at the end.
  mesh    dense cloud, mesh and texture (OpenMVS, CPU is fine but slow).
          Result: <work>/mvs/scene_textured.obj. Finished steps are skipped,
          so an interrupted run continues where it stopped.

Quality and memory:
  After mapping, loose points are dropped (short tracks, high reprojection
  error) before the dense step, and dense points need 3 views to survive.
  RefineMesh is off by default: it needs far more RAM than the rest
  (a 4K scene can exhaust 16 GB). --refine turns it on for big machines.
  Textures are capped at --texture-size (default 4096).

Georeference (--city city.json plus camera positions):
  With positions for at least 3 frames, the sparse model is moved, turned
  and scaled into the city frame (metres, X east, Y north, Z up), so the
  mesh comes out in place and to scale. Positions come from --gps
  (extract_frames.py writes <clip>_gps.txt when the clip has GPS) or, for
  photos, from their EXIF GPS (needs Pillow). Without them the model keeps
  COLMAP's arbitrary frame and is aligned to block.obj by hand.

Raw photos (.dng and other raw files) are converted to JPG in <work>/images
first (needs rawpy and imageio); the originals are not touched.

Tools are found on PATH or via --colmap / --openmvs. Windows: the COLMAP
"nocuda" zip and the OpenMVS release zip both run without installing.
Without an NVIDIA card COLMAP's own dense step is unavailable; OpenMVS
does that part instead. RealityScan (Epic) gives better results on NVIDIA
machines and can replace this script entirely.
"""
import argparse
import json
import math
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


IMAGES = (".jpg", ".jpeg", ".png", ".tif", ".tiff")
RAW = (".dng", ".arw", ".cr2", ".cr3", ".nef", ".raf", ".rw2", ".orf")


def prepare_images(images, work):
    """Raw files cannot be read by COLMAP: make <work>/images with JPGs.
    Ordinary images are hard-linked (no extra disk space), raws converted."""
    names = sorted(os.listdir(images))
    raws = [n for n in names if n.lower().endswith(RAW)]
    if not raws:
        return images
    try:
        import imageio.v2 as imageio
        import rawpy
    except ImportError:
        sys.exit(f"{len(raws)} raw files ({raws[0]}, ...) need converting to JPG: "
                 "pip install rawpy imageio, or export them as JPG yourself.")
    out = os.path.join(work, "images")
    os.makedirs(out, exist_ok=True)
    for n in names:
        src, stem = os.path.join(images, n), os.path.splitext(n)[0]
        if n.lower().endswith(IMAGES):
            dst = os.path.join(out, n)
            if not os.path.exists(dst):
                try:
                    os.link(src, dst)
                except OSError:
                    shutil.copy2(src, dst)
        elif n.lower().endswith(RAW) and not os.path.exists(os.path.join(out, stem + ".jpg")):
            with rawpy.imread(src) as r:
                imageio.imwrite(os.path.join(out, stem + ".jpg"),
                                r.postprocess(use_camera_wb=True), quality=95)
            print(f"converted {n}")
    return out


def has_option(exe, option, *args):
    out = subprocess.run([exe, *args], capture_output=True, text=True)
    return option in out.stdout + out.stderr


def filter_points(colmap, model, out):
    """Drop loose points before the dense step: they turn into floating junk."""
    if not has_option(colmap, "min_track_len", "point_filtering", "-h"):
        return model
    os.makedirs(out, exist_ok=True)
    run([colmap, "point_filtering", "--input_path", model, "--output_path", out,
         "--min_track_len", "3", "--max_reproj_error", "1.5", "--min_tri_angle", "1.5"])
    return out


def exif_positions(images):
    """{image name: (lat, lon, alt)} from photo EXIF GPS."""
    try:
        from PIL import Image
    except ImportError:
        return {}
    pos = {}
    for n in sorted(os.listdir(images)):
        if not n.lower().endswith((".jpg", ".jpeg", ".tif", ".tiff")):
            continue
        try:
            with Image.open(os.path.join(images, n)) as im:
                gps = im.getexif().get_ifd(0x8825)
        except OSError:
            continue
        if 2 not in gps or 4 not in gps:
            continue
        deg = lambda v: float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
        lat = deg(gps[2]) * (-1 if gps.get(1) == "S" else 1)
        lon = deg(gps[4]) * (-1 if gps.get(3) == "W" else 1)
        pos[n] = (lat, lon, float(gps[6]) if 6 in gps else float("nan"))
    return pos


def georeference(colmap, model, out, city_json, positions):
    """Move, turn and scale the sparse model into the city frame."""
    with open(city_json, encoding="utf-8") as f:
        lat0, lon0 = json.load(f)["center"]
    k_lat, k_lon = 111_320.0, 111_320.0 * math.cos(math.radians(lat0))
    alts = [p[2] for p in positions.values() if not math.isnan(p[2])]
    # phone and gimbal GPS height is poor; trust it only when it clearly varies
    use_alt = len(alts) == len(positions) and max(alts) - min(alts) > 3
    ref = os.path.join(out + "_ref.txt")
    with open(ref, "w", encoding="utf-8") as f:
        for name, (lat, lon, alt) in positions.items():
            z = alt - min(alts) + 1.5 if use_alt else 1.5
            f.write(f"{name} {(lon - lon0) * k_lon:.3f} {(lat - lat0) * k_lat:.3f} {z:.3f}\n")
    os.makedirs(out, exist_ok=True)
    cmd = [colmap, "model_aligner", "--input_path", model, "--output_path", out,
           "--ref_images_path", ref, "--ref_is_gps", "0", "--alignment_max_error", "5"]
    if has_option(colmap, "alignment_type", "model_aligner", "-h"):
        cmd += ["--alignment_type", "custom"]
    try:
        run(cmd)
    except subprocess.CalledProcessError:
        print("georeference failed (too few or too noisy positions); keeping COLMAP's frame")
        return model
    print(f"model is now in the city frame (metres, X east, Y north): {out}")
    return out


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
    total = len([f for f in os.listdir(images) if f.lower().endswith(IMAGES)])
    print(f"\nBEST MODEL {best}: {best_n} of {total} images registered")
    return os.path.join(model, best)


def mesh(colmap, openmvs, images, work, model, refine=False, texture_size=4096):
    dense = os.path.join(work, "dense")
    if not os.path.isdir(os.path.join(dense, "images")):
        run([colmap, "image_undistorter", "--image_path", images, "--input_path", model,
             "--output_path", dense, "--output_type", "COLMAP"])
    mvs = os.path.join(work, "mvs")
    os.makedirs(mvs, exist_ok=True)
    t = {n: tool(n, openmvs) for n in ("InterfaceCOLMAP", "DensifyPointCloud",
                                       "ReconstructMesh", "RefineMesh", "TextureMesh")}
    missing = [n for n, p in t.items() if not p and n != "RefineMesh"]
    if missing:
        sys.exit(f"OpenMVS tools not found: {', '.join(missing)}. Pass --openmvs <folder>.")
    done = lambda name: os.path.exists(os.path.join(mvs, name))
    scene = os.path.join(mvs, "scene.mvs")
    if not done("scene.mvs"):
        run([t["InterfaceCOLMAP"], "-i", dense, "-o", scene, "--image-folder",
             os.path.join(dense, "images")])
    if not done("scene_dense.mvs"):
        cmd = [t["DensifyPointCloud"], scene, "--resolution-level", "1", "-w", mvs]
        if has_option(t["DensifyPointCloud"], "min-views-fuse", "--help"):
            cmd += ["--min-views-fuse", "3"]
        run(cmd)
    def mesh_input(stem):
        """OpenMVS 2.x writes only <stem>.ply; older versions also <stem>.mvs."""
        if done(stem + ".mvs"):
            return [os.path.join(mvs, stem + ".mvs")]
        return [os.path.join(mvs, "scene_dense.mvs"), "-m", os.path.join(mvs, stem + ".ply")]

    if not (done("scene_dense_mesh.mvs") or done("scene_dense_mesh.ply")):
        run([t["ReconstructMesh"], os.path.join(mvs, "scene_dense.mvs"), "-w", mvs])
    meshed = mesh_input("scene_dense_mesh")
    if refine and t["RefineMesh"]:
        if not (done("scene_dense_mesh_refine.mvs") or done("scene_dense_mesh_refine.ply")):
            run([t["RefineMesh"], *meshed, "--resolution-level", "2", "-o",
                 os.path.join(mvs, "scene_dense_mesh_refine.mvs"), "-w", mvs])
        meshed = mesh_input("scene_dense_mesh_refine")
    cmd = [t["TextureMesh"], *meshed, "--export-type", "obj", "-o",
           os.path.join(mvs, "scene_textured.mvs"), "-w", mvs]
    if has_option(t["TextureMesh"], "max-texture-size", "--help"):
        cmd += ["--max-texture-size", str(texture_size)]
    run(cmd)
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
    ap.add_argument("--city", help="city.json: put the model into the city frame")
    ap.add_argument("--gps", help="<clip>_gps.txt from extract_frames.py (else photo EXIF)")
    ap.add_argument("--refine", action="store_true",
                    help="run RefineMesh (needs a lot of RAM; off by default)")
    ap.add_argument("--texture-size", type=int, default=4096)
    a = ap.parse_args()
    colmap = tool("colmap", a.colmap) or tool("COLMAP.bat", a.colmap)
    if not colmap:
        sys.exit("COLMAP not found. Download it from "
                 "https://github.com/colmap/colmap/releases and pass --colmap <folder>.")
    # OpenMVS runs inside <work>/mvs, so relative paths would break
    a.work, a.images = os.path.abspath(a.work), os.path.abspath(a.images)
    os.makedirs(a.work, exist_ok=True)
    images = prepare_images(a.images, a.work)
    final = os.path.join(a.work, "sparse", "final")
    if a.stage == "sparse" or not os.path.isdir(final):
        model = os.path.join(a.work, "sparse", "0")
        if a.stage == "sparse" or not os.path.isdir(model):
            model = sparse(colmap, images, a.work, sequential=not a.photos)
        model = filter_points(colmap, model, os.path.join(a.work, "sparse", "filtered"))
        if a.city:
            if a.gps:
                with open(a.gps, encoding="utf-8") as f:
                    rows = [l.split() for l in f if l.strip()]
                positions = {r[0]: tuple(map(float, r[1:4])) for r in rows}
            else:
                positions = exif_positions(images)
            if len(positions) >= 3:
                model = georeference(colmap, model, os.path.join(a.work, "sparse", "geo"),
                                     a.city, positions)
            else:
                print("no camera positions (GPS): the model keeps COLMAP's frame; "
                      "align it to block.obj")
        if os.path.isdir(final):
            shutil.rmtree(final)
        shutil.copytree(model, final)
        print(f"sparse model for the next stage: {final}")
    if a.stage == "mesh":
        mesh(colmap, a.openmvs, images, a.work, final, a.refine, a.texture_size)


if __name__ == "__main__":
    main()
