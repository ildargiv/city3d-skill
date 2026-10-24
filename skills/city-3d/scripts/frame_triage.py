#!/usr/bin/env python3
"""Sort frames or photos before photogrammetry, optionally with a local vision model.

  python3 frame_triage.py frames/0043 --out triage.json [--every 5]
          [--llm http://localhost:1234/v1 --model <name>] [--buildings city/buildings.csv]

Without --llm it measures sharpness only (Pillow). With --llm it also asks a
vision model behind an OpenAI-compatible server (LM Studio, Ollama,
llama.cpp, vLLM) about every --every-th frame: usable for 3D or not, blur,
glare, people/cars, and which building it shows, chosen from the names and
addresses in buildings.csv. Nothing leaves the machine when the server is
local, and it costs no tokens.

The model's building guess is a hint, not a decision: the agent (or the
building-matcher agent) confirms it before copying frames into a folder.
"""
import argparse
import base64
import csv
import io
import json
import os
import sys
import urllib.request

try:
    from PIL import Image, ImageFilter, ImageStat
except ImportError:
    Image = None

EXT = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp")
PROMPT = """You check photos for building photogrammetry. Answer with JSON only:
{{"usable_3d": true/false, "blur": "none|some|bad", "glare": true/false,
"people_or_cars": "none|few|many", "building": "<one name from the list or unknown>",
"side": "<which side of that building faces the camera, or unknown>", "note": "<short>"}}
Usable for 3D means: a building facade fills a good part of the frame, sharp, not a
far panorama. Buildings in this district:
{buildings}"""


def sharpness(path):
    with Image.open(path) as im:
        g = im.convert("L")
        g.thumbnail((960, 960))
        return round(ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).var[0], 1)


def ask(llm, model, path, buildings, timeout):
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((1024, 1024))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
    body = {
        "model": model, "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT.format(buildings=buildings)},
            {"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()}}]}],
    }
    req = urllib.request.Request(llm.rstrip("/") + "/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = json.load(r)["choices"][0]["message"]["content"]
    start, end = text.find("{"), text.rfind("}")
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return {"note": "unreadable answer: " + text[:200]}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder")
    ap.add_argument("--out", default="triage.json")
    ap.add_argument("--every", type=int, default=5, help="ask the model about every Nth frame")
    ap.add_argument("--llm", help="OpenAI-compatible base URL, e.g. http://localhost:1234/v1")
    ap.add_argument("--model", default="local", help="model name as the server lists it")
    ap.add_argument("--buildings", help="buildings.csv from osm2city.py")
    ap.add_argument("--timeout", type=int, default=300)
    a = ap.parse_args()
    if not Image:
        sys.exit("pip install pillow")
    names = "unknown"
    if a.buildings:
        with open(a.buildings, encoding="utf-8") as f:
            rows = [r for r in csv.DictReader(f) if r.get("part") != "True"]
        names = "\n".join(f"- {r['name'] or r['address']} [{r['id']}]"
                          for r in rows if r["name"] or r["address"])
    files = sorted(f for f in os.listdir(a.folder) if f.lower().endswith(EXT))
    report = []
    for i, f in enumerate(files):
        path = os.path.join(a.folder, f)
        item = {"file": f, "sharpness": sharpness(path)}
        if a.llm and i % a.every == 0:
            try:
                item["llm"] = ask(a.llm, a.model, path, names, a.timeout)
            except OSError as e:
                sys.exit(f"cannot reach {a.llm}: {e}")
        report.append(item)
        print(f, item["sharpness"], json.dumps(item.get("llm", {}), ensure_ascii=False))
    # frames much softer than the clip's median are blur candidates
    s = sorted(r["sharpness"] for r in report)
    median = s[len(s) // 2] if s else 0
    for r in report:
        r["soft"] = r["sharpness"] < 0.5 * median
    with open(a.out, "w", encoding="utf-8") as fo:
        json.dump(report, fo, ensure_ascii=False, indent=1)
    print(f"{len(report)} frames, {sum(r['soft'] for r in report)} soft -> {a.out}")


if __name__ == "__main__":
    main()
