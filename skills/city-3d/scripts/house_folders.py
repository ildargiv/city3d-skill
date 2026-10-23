#!/usr/bin/env python3
"""One folder per building: the user drops photos and video there.

  python3 house_folders.py init   city.json --root buildings/ [--coverage coverage.json] [--lang en|ru]
  python3 house_folders.py status city.json --root buildings/ [--coverage coverage.json]

Layout under --root (Russian names with --lang ru: СТАТУС.md, фото/, кадры/,
модель/, ДОСНЯТЬ.md):
  STATUS.md                         one table: building / status / sides left
  <NN> <name or address> [<osm id>]/
      photos/      the user drops photos and video here
      frames/      frames the agent cut from videos showing this building
      model/       the agent's result (mesh + textures)
      TO_SHOOT.md  facades still needed, how to shoot, map link

The language is detected from an existing root (СТАТУС.md means ru), so a
project keeps its folder names; for a new root it is --lang (default en).

init    creates the folders for every building that needs shooting, numbered
        in the shot-list walking order. Existing folders and files are never
        touched; the two .md files are rewritten from the coverage.
        Also writes folders.js next to city.json, so the 3D preview
        (index.html) shows "Open building folder" on each building card.
status  lists which buildings got photos or video in photos/ and which files
        are new since the last run (tracked in <root>/.city3d_seen.json), so
        the agent processes only fresh material. With --coverage, buildings
        that received files and were "none" become "partial".
"""
import argparse
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shotlist import SIDES, TEXT, facades, walking_order  # noqa: E402

MEDIA = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".dng",
         ".raw", ".arw", ".cr2", ".cr3", ".nef", ".mp4", ".mov", ".mkv", ".avi")
ID_RE = re.compile(r"\[(-?\d+)\]\s*$")
NAMES = {
    "en": {"subdirs": ("photos", "frames", "model"), "todo": "TO_SHOOT.md",
           "status_md": "STATUS.md", "building": "Building",
           "done": "**Done, nothing to shoot.**", "need": "**Facades to shoot:**",
           "note": "**Note:**",
           "how": "How to shoot: walk round 10–20 m away, camera on the facade, a frame "
                  "every 1–2 steps (or slow 4K video), second lap with the camera higher. "
                  "Overcast or shade, no panoramas from one spot.",
           "drop": "Put the original files, no filters, into `photos` and tell the agent "
                   "\"added\".",
           "map": "On the map", "status_title": "# Shooting status",
           "status_head": "| Folder | Status | Facades missing |"},
    "ru": {"subdirs": ("фото", "кадры", "модель"), "todo": "ДОСНЯТЬ.md",
           "status_md": "СТАТУС.md", "building": "Здание",
           "done": "**Готово, снимать не нужно.**", "need": "**Снять фасады:**",
           "note": "**Замечание:**",
           "how": "Как снимать: обойти дом на 10–20 м, камера на фасаде, кадр каждые "
                  "1–2 шага (или видео 4K, медленно), второй круг с камерой выше. "
                  "Пасмурно или в тени, без панорам с одной точки.",
           "drop": "Кладите оригиналы без фильтров в папку `фото` и напишите агенту «добавил».",
           "map": "На карте", "status_title": "# Статус съёмки",
           "status_head": "| Папка | Статус | Не хватает фасадов |"},
}


def lang_of(root, wanted):
    """An existing project keeps its language; a new one uses --lang."""
    for lang, n in NAMES.items():
        if os.path.exists(os.path.join(root, n["status_md"])):
            return lang
    return wanted or "en"


def safe(name):
    """Folder-safe on Windows, macOS and Linux."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name)
    return re.sub(r"\s+", " ", name).strip(" .")[:60] or "Building"


def osm_link(i):
    return (f"https://www.openstreetmap.org/relation/{-i // 100}" if i < 0
            else f"https://www.openstreetmap.org/way/{i}")


def load(city_json, coverage):
    with open(city_json, encoding="utf-8") as f:
        city = json.load(f)
    cov = {}
    if coverage and os.path.exists(coverage):
        with open(coverage, encoding="utf-8") as f:
            cov = json.load(f)
    return city, cov


def existing(root):
    """Map osm id -> folder name for folders already created."""
    out = {}
    if os.path.isdir(root):
        for d in os.listdir(root):
            m = ID_RE.search(d)
            if m and os.path.isdir(os.path.join(root, d)):
                out[int(m.group(1))] = d
    return out


def init(a):
    city, cov = load(a.city_json, a.coverage)
    lang = lang_of(a.root, a.lang)
    L, T = NAMES[lang], TEXT[lang]
    os.makedirs(a.root, exist_ok=True)
    have = existing(a.root)
    items = []
    for b in city["buildings"]:
        if b.get("part"):
            continue
        sides = facades(b["footprint"])
        if not sides:
            continue  # sheds and kiosks
        c = cov.get(str(b["id"]), {})
        missing = [s for s in SIDES if s in sides and s not in c.get("sides", [])]
        items.append({**b, "missing": missing, "sides": sides,
                      "status": c.get("status", "none"), "note": c.get("note", "")})
    made = 0
    for n, b in enumerate(walking_order(items), 1):
        folder = have.get(b["id"])
        if not folder:
            title = b["name"] or b["address"] or L["building"]
            folder = f"{n:02d} {safe(title)} [{b['id']}]"
            made += 1
        for sub in L["subdirs"]:
            os.makedirs(os.path.join(a.root, folder, sub), exist_ok=True)
        b["folder"] = folder
        done = b["status"] == "done" or not b["missing"]
        lines = [f"# {b['name'] or L['building']}", b["address"] or "", "",
                 L["done"] if done else L["need"] + " " + ", ".join(
                     f"{T['sides'][s]} ({b['sides'][s]:.0f} {T['m']})" for s in b["missing"]),
                 f"{L['note']} {b['note']}" if b["note"] else "", "",
                 L["how"], "", L["drop"], "", f"{L['map']}: {osm_link(b['id'])}"]
        # BOM so Windows Notepad shows Cyrillic correctly
        with open(os.path.join(a.root, folder, L["todo"]), "w",
                  encoding="utf-8-sig") as f:
            f.write("\n".join(lines) + "\n")
    write_status(a.root, items, lang)
    write_links(a.city_json, a.root)
    print(f"folders: {made} new, {len(items) - made} existing -> {a.root} ({lang})")


def write_links(city_json, root):
    """folders.js next to city.json: the 3D preview links each building to its folder."""
    links = {bid: pathlib.Path(root, d).resolve().as_uri()
             for bid, d in existing(root).items()}
    with open(os.path.join(os.path.dirname(os.path.abspath(city_json)), "folders.js"),
              "w", encoding="utf-8") as f:
        f.write("window.CITY_FOLDERS = ")
        json.dump(links, f, ensure_ascii=False)
        f.write(";\n")


def write_status(root, items, lang):
    L, T = NAMES[lang], TEXT[lang]
    rows = [L["status_title"], "", L["status_head"], "|---|---|---|"]
    for b in sorted(items, key=lambda b: b.get("folder", "")):
        left = ("—" if b["status"] == "done" or not b["missing"]
                else ", ".join(T["sides"][s] for s in b["missing"]))
        st = "done" if not b["missing"] else b["status"]
        rows.append(f"| {b.get('folder', b['id'])} | {T['status'].get(st, st)} | {left} |")
    with open(os.path.join(root, L["status_md"]), "w", encoding="utf-8-sig") as f:
        f.write("\n".join(rows) + "\n")


def status(a):
    city, cov = load(a.city_json, a.coverage)
    names = {b["id"]: b["name"] or b["address"] or str(b["id"]) for b in city["buildings"]}
    seen_path = os.path.join(a.root, ".city3d_seen.json")
    seen = {}
    if os.path.exists(seen_path):
        with open(seen_path, encoding="utf-8") as f:
            seen = json.load(f)
    report, changed = [], False
    photos = NAMES[lang_of(a.root, a.lang)]["subdirs"][0]
    for bid, folder in sorted(existing(a.root).items(), key=lambda kv: kv[1]):
        files = []  # only what the user brought, not the agent's frames/model
        for dp, _, fs in os.walk(os.path.join(a.root, folder, photos)):
            files += [os.path.relpath(os.path.join(dp, f), a.root)
                      for f in fs if f.lower().endswith(MEDIA)]
        if not files:
            continue
        new = sorted(set(files) - set(seen.get(str(bid), [])))
        report.append({"id": bid, "name": names.get(bid, ""), "folder": folder,
                       "files": len(files), "new": new})
        seen[str(bid)] = sorted(files)
        c = cov.setdefault(str(bid), {"status": "none", "sides": []})
        if c["status"] == "none":
            c["status"] = "partial"
            changed = True
    for r in report:
        flag = f"NEW {len(r['new'])}" if r["new"] else "no new files"
        print(f"{r['folder']}: {r['files']} files, {flag}")
    if not report:
        print("no photos or video in any building folder yet")
    with open(seen_path, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=1)
    if a.coverage and changed:
        with open(a.coverage, "w", encoding="utf-8") as f:
            json.dump(cov, f, ensure_ascii=False, indent=1)
    if a.json:
        print(json.dumps(report, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("command", choices=["init", "status"])
    ap.add_argument("city_json")
    ap.add_argument("--root", required=True, help="folder that holds the building folders")
    ap.add_argument("--coverage")
    ap.add_argument("--lang", choices=sorted(NAMES),
                    help="folder and file names for a new root (default en)")
    ap.add_argument("--json", action="store_true", help="status: also print JSON")
    a = ap.parse_args()
    (init if a.command == "init" else status)(a)


if __name__ == "__main__":
    main()
