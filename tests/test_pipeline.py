"""Offline tests for the city-3d scripts: no network, no Blender, no COLMAP.

  python3 -m unittest discover -s tests

Blender parts run only when the `bpy` module is importable (pip install bpy).
"""
import importlib.util
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "city-3d", "scripts")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "mini.osm")
ORIGIN = "56.6322,47.9082"
sys.path.insert(0, SCRIPTS)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def run(*args):
    out = subprocess.run([sys.executable, *args], capture_output=True, text=True, cwd=SCRIPTS)
    if out.returncode:
        raise AssertionError(out.stdout + out.stderr)
    return out.stdout


class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.city_dir = os.path.join(cls.tmp, "city")
        run("osm2city.py", FIXTURE, "--radius", "0", "--origin", ORIGIN,
            "--name", "Test", "--out", cls.city_dir)
        cls.city_json = os.path.join(cls.city_dir, "city.json")
        with open(cls.city_json, encoding="utf-8") as f:
            cls.city = json.load(f)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def building(self, i):
        return next(b for b in self.city["buildings"] if b["id"] == i)

    def test_city_kit(self):
        for name in ("buildings.csv", "city.json", "city.js", "city.obj", "index.html"):
            self.assertTrue(os.path.exists(os.path.join(self.city_dir, name)), name)
        self.assertEqual(self.city["attribution"], "© OpenStreetMap contributors (ODbL)")
        self.assertEqual(len(self.city["roads"]), 1)
        self.assertEqual(sorted(a["kind"] for a in self.city["areas"]), ["park", "water"])
        self.assertEqual(len(self.city["trees"]), 1)

    def test_heights_and_parts(self):
        self.assertEqual(self.building(100)["height"], 16.0)  # 5 levels
        self.assertTrue(self.building(200)["has_parts"])
        self.assertEqual(self.building(202)["height"], 30.0)
        self.assertEqual(self.building(201)["name"], "Test tower")  # inherited

    def test_origin_is_fixed_and_reused(self):
        self.assertEqual(self.city["origin"], [56.6322, 47.9082])
        # node 1 is the origin itself: building 100 starts at (0, 0)
        self.assertIn([0.0, 0.0], self.building(100)["footprint"])
        out = os.path.join(self.tmp, "again")
        os.makedirs(out)
        shutil.copy(self.city_json, out)
        run("osm2city.py", FIXTURE, "--radius", "0", "--out", out)
        with open(os.path.join(out, "city.json"), encoding="utf-8") as f:
            again = json.load(f)
        self.assertEqual(again["origin"], self.city["origin"])
        self.assertEqual(again["buildings"][0]["footprint"], self.city["buildings"][0]["footprint"])

    def test_shot_list_and_invented_sides(self):
        cov = os.path.join(self.tmp, "cov.json")
        with open(cov, "w", encoding="utf-8") as f:
            json.dump({"100": {"status": "partial", "sides": ["S"], "invented": ["N", "roof"]}}, f)
        out = os.path.join(self.tmp, "sl")
        run("shotlist.py", self.city_json, "--coverage", cov, "--lang", "en", "--out", out)
        md = read(os.path.join(out, "shotlist.md"))
        self.assertIn("Invented, not shot: north, roof", md)
        block = md[md.index("Test street 1"):].split("\n## ")[0]
        self.assertNotIn("north (", block)  # invented sides are not asked for
        self.assertIn("1 of them partly shot", md)

    def test_building_folders(self):
        root = os.path.join(self.tmp, "b")
        run("house_folders.py", "init", self.city_json, "--root", root)
        folders = os.listdir(root)
        self.assertIn("STATUS.md", folders)
        tower = next(d for d in folders if d.endswith("[200]"))
        for name in ("photos", "frames", "model", "TO_SHOOT.md", "block.obj"):
            self.assertTrue(os.path.exists(os.path.join(root, tower, name)), name)
        # block.obj of a building with parts holds both parts
        obj = read(os.path.join(root, tower, "block.obj"))
        self.assertEqual(obj.count("\no block_"), 2)
        # a folder added later is numbered after the existing ones
        os.rename(os.path.join(root, tower), os.path.join(root, "07 keep me [200]"))
        shutil.rmtree(os.path.join(root, next(d for d in os.listdir(root) if d.endswith("[100]"))))
        run("house_folders.py", "init", self.city_json, "--root", root)
        self.assertTrue(any(d.startswith("08 ") and d.endswith("[100]") for d in os.listdir(root)))

    def test_russian_project_keeps_its_names(self):
        root = os.path.join(self.tmp, "ru")
        run("house_folders.py", "init", self.city_json, "--root", root, "--lang", "ru")
        run("house_folders.py", "init", self.city_json, "--root", root)  # no --lang
        tower = next(d for d in os.listdir(root) if d.endswith("[200]"))
        self.assertTrue(os.path.isdir(os.path.join(root, tower, "фото")))
        self.assertFalse(os.path.exists(os.path.join(root, tower, "photos")))

    def test_qa_flags_wrong_scale(self):
        root = os.path.join(self.tmp, "qa")
        run("house_folders.py", "init", self.city_json, "--root", root)
        folder = next(d for d in os.listdir(root) if d.endswith("[100]"))
        lines = []
        for line in read(os.path.join(root, folder, "block.obj")).splitlines(True):
            if line.startswith("v "):
                x, y, z = map(float, line.split()[1:])
                line = f"v {x * 100} {y * 100} {z * 100}\n"
            lines.append(line)
        with open(os.path.join(root, folder, "model", "scan.obj"), "w", encoding="utf-8") as f:
            f.writelines(lines)
        report = json.loads(run("qa.py", self.city_json, "--root", root, "--json"))
        self.assertEqual(len(report), 1)
        self.assertTrue(any("footprint" in i for i in report[0]["issues"]))


class ScanRepair(unittest.TestCase):
    """fix_scan.py: judge and repair a .glb before it reaches the map."""

    @staticmethod
    def make_glb(path, sheet=False):
        """A 2 m cube (closed) or a 2 m sheet, Y-up, no normals, no texture."""
        if sheet:
            v = [(-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]
            f = [(0, 1, 2), (0, 2, 3)]
        else:
            v = [(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
            f = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5),
                 (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6),
                 (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
        pos = b"".join(struct.pack("<3f", *p) for p in v)
        idx = b"".join(struct.pack("<I", i) for t in f for i in t)
        pos += b"\0" * (-len(pos) % 4)
        binary = pos + idx
        gl = {"asset": {"version": "2.0"},
              "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
              "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
              "accessors": [
                  {"bufferView": 0, "componentType": 5126, "count": len(v),
                   "type": "VEC3", "min": [min(p[k] for p in v) for k in range(3)],
                   "max": [max(p[k] for p in v) for k in range(3)]},
                  {"bufferView": 1, "componentType": 5125, "count": len(idx) // 4,
                   "type": "SCALAR"}],
              "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(pos)},
                              {"buffer": 0, "byteOffset": len(pos), "byteLength": len(idx)}],
              "buffers": [{"byteLength": len(binary)}]}
        js = json.dumps(gl, separators=(",", ":")).encode() + b" " * 0
        js += b" " * (-len(js) % 4)
        with open(path, "wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(binary)))
            f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
            f.write(struct.pack("<II", len(binary), 0x004E4942) + binary)

    def test_closed_box_is_ok_and_repairs(self):
        tmp = tempfile.mkdtemp()
        try:
            box = os.path.join(tmp, "box.glb")
            self.make_glb(box)
            rep = json.loads(run("fix_scan.py", box, "--json"))
            # closed and solid, but the fixture has no normals: repair, not reshoot
            self.assertEqual(rep["verdict"], "repair")
            self.assertGreater(rep["flatness"], 0.8)
            self.assertEqual(rep["border_edges"], 0)

            fixed = os.path.join(tmp, "fixed.glb")
            coll = os.path.join(tmp, "coll.glb")
            run("fix_scan.py", box, "--out", fixed, "--collision", coll,
                "--triangles", str(rep["triangles"]))
            again = json.loads(run("fix_scan.py", fixed, "--json"))
            self.assertEqual(again["verdict"], "ok")
            self.assertTrue(again["normals"])
            self.assertEqual(again["border_edges"], 0)
            box_rep = json.loads(run("fix_scan.py", coll, "--json"))
            self.assertEqual(box_rep["border_edges"], 0)
            self.assertEqual(box_rep["inward_share"], 0.0)
        finally:
            shutil.rmtree(tmp)

    def test_sheet_is_sent_back_to_the_shot_list(self):
        tmp = tempfile.mkdtemp()
        try:
            sheet = os.path.join(tmp, "sheet.glb")
            self.make_glb(sheet, sheet=True)
            rep = json.loads(run("fix_scan.py", sheet, "--json"))
            self.assertEqual(rep["verdict"], "reshoot")
            self.assertLess(rep["flatness"], 0.1)
            self.assertLessEqual(rep["coverage"], 0.55)
        finally:
            shutil.rmtree(tmp)


class Helpers(unittest.TestCase):
    def test_utm_inverse(self):
        from odm_split import utm_to_latlon
        # reference values from PROJ (EPSG:32638 / 32637)
        for (e, n, zone), (lat, lon) in [((678425.816, 6280260.943, 38), (56.632482, 47.909036)),
                                         ((531183.759, 6206192.391, 38), (56.0, 45.5)),
                                         ((413380.720, 6179118.146, 37), (55.75, 37.62))]:
            la, lo = utm_to_latlon(e, n, zone)
            self.assertLess(abs(la - lat) * 111320, 0.05)
            self.assertLess(abs(lo - lon) * 111320 * math.cos(math.radians(lat)), 0.05)

    def test_dji_srt_track(self):
        from extract_frames import position_at, srt_track
        tmp = tempfile.mkdtemp()
        try:
            with open(os.path.join(tmp, "c.SRT"), "w", encoding="utf-8") as f:
                f.write("1\n00:00:00,000 --> 00:00:00,033\n[latitude: 56.6331] [longitude: 47.906] "
                        "[rel_alt: 1.2 abs_alt: 101.5]\n\n2\n00:00:02,000 --> 00:00:02,033\n"
                        "[latitude: 56.6332] [longitude: 47.9062] [rel_alt: 1.2 abs_alt: 101.9]\n\n")
            track = srt_track(os.path.join(tmp, "c.mp4"))
            self.assertEqual(len(track), 2)
            lat, lon, alt = position_at(track, 1.0)
            self.assertAlmostEqual(lat, 56.63315, places=6)
            self.assertAlmostEqual(alt, 101.7, places=3)
        finally:
            shutil.rmtree(tmp)

    def test_colmap_exe_preferred_over_bat(self):
        from photogrammetry import tool
        tmp = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(tmp, "bin"))
            for name in ("COLMAP.bat", os.path.join("bin", "colmap.exe")):
                open(os.path.join(tmp, name), "w").close()
            self.assertEqual(tool("colmap", tmp), os.path.join(tmp, "bin", "colmap.exe"))
        finally:
            shutil.rmtree(tmp)

    def test_doctor_runs(self):
        report = json.loads(run("doctor.py", "--json"))
        self.assertTrue(next(r for r in report if r["tool"] == "python")["ok"])


@unittest.skipUnless(importlib.util.find_spec("bpy"), "pip install bpy to test the Blender scene")
class BlenderScene(unittest.TestCase):
    def test_tiles_and_scans(self):
        tmp = tempfile.mkdtemp()
        try:
            city = os.path.join(tmp, "city")
            run("osm2city.py", FIXTURE, "--radius", "0", "--origin", ORIGIN, "--out", city)
            root = os.path.join(tmp, "b")
            run("house_folders.py", "init", os.path.join(city, "city.json"), "--root", root)
            folder = next(d for d in os.listdir(root) if d.endswith("[200]"))
            shutil.copy(os.path.join(root, folder, "block.obj"),
                        os.path.join(root, folder, "model", "scan.obj"))
            out = run("blender_city.py", "--", os.path.join(city, "city.json"), "--scans", root,
                      "--out", os.path.join(tmp, "build"), "--no-split")
            self.assertIn("placed B_200", out)
            self.assertTrue(os.listdir(os.path.join(tmp, "build", "tiles")))
            with open(os.path.join(tmp, "build", "sources.json"), encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertEqual(manifest["geometry"]["scan"], 1)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
