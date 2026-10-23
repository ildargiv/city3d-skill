---
name: city-3d
description: Build a 3D model of a real city district from OpenStreetMap plus the user's own photos and video, one model per building on one shared landscape, in a realistic and a cartoon style, exported to Blender and Unreal/Godot/Unity. Use when someone wants to recreate a real street, district or town in 3D or in a game.
---

# City 3D

Turns a place and a radius into a game-ready district: every building is a
separate model standing on its OSM footprint, the landscape is one piece, and
the user's own photos and video raise the quality building by building.

The work runs as a loop. Build everything possible from what exists, then
tell the user exactly what to photograph, then build again with the new
material. Repeat until the user is happy.

```
place + radius → OSM → block city (every house) → shot list
      ↑                                              ↓
  rebuild with scans  ←  photogrammetry  ←  user shoots what is listed
```

Scripts live in `scripts/` next to this file. Python 3.9+, no packages except
where noted. Talk to the user in their language; the examples below are
Russian because the first project was in Yoshkar-Ola.

## 0. Ground rules (read once)

- Data sources: only what `references/legal.md` lists as safe. Google,
  Yandex and 2GIS maps, panoramas and 3D tiles are for the user's eyes only:
  never download, screenshot, trace or feed them to a tool, even "just for
  personal use". Users may add their own sources at their own responsibility;
  ask once where a non-own file came from and note it in `sources.md`.
- Credit "© OpenStreetMap contributors (ODbL)" in every viewer, export and
  release.
- Never modify or delete the user's original photos and videos. Write
  derived files into new folders.

## 1. Area

Ask for a place (an address or landmark) and a radius. 200–300 m is a good
first district; more than 500 m makes the shot list very long.

```bash
python3 scripts/fetch_osm.py --place "Республиканский театр кукол, Йошкар-Ола" --radius 300 --out map.json
```

If the network blocks Overpass/Nominatim, ask the user to export the area at
https://www.openstreetmap.org/export (select a box around the place, press
Export) and send the `.osm` file. Find the landmark's coordinates in the file
and use them as the centre.

## 2. Block city

```bash
python3 scripts/osm2city.py map.json --lat 56.63696 --lon 47.90838 --radius 300 \
    --name "Йошкар-Ола, у театра кукол" --out city/
```

Produces `city/buildings.csv` (the work list), `city/city.json` (geometry in
metres around the centre) and `city/city.obj`. Buildings with `building:part`
pieces are drawn by their parts. Missing levels default to 3 (house: 2) and
are flagged `levels_known=False`: ask the user to confirm those from their
photos.

For an area between landmarks (for example "from one tower to the other"),
export a box that covers both with a margin, pass `--radius 0` to keep
everything in it, and put the centre between the landmarks. A big area that
openstreetmap.org refuses to export in one go can be exported in pieces and
passed together: `osm2city.py part1.osm part2.osm ...` merges them.

Show it: `city/index.html` (written next to `city.json`, with the data also in
`city.js`) opens with a double click, no server needed. It has a Realistic /
Cartoon switch, windows drawn per storey, pitched roofs, and a card per
building. After `house_folders.py init` (step 5) the card also links to the
building's folder ("Открыть папку дома").

## 3. Blender scene and export

Run with Blender 4.2+ (or `pip install bpy` for headless use):

```bash
blender -b -P scripts/blender_city.py -- city/city.json --out build/ --style both
```

Writes `city.blend`, `landscape_<style>.glb`, `buildings_<style>.glb`,
one `buildings/B_<id>_<style>.glb` per building and `placement.json`.
Collections: `Landscape` (one ground, roads, areas, trees) and `Buildings`
(one object per building, origin on its footprint). If the user has
BlenderMCP connected, open `city.blend` there and work interactively.

Engine import notes: `references/engines.md`.

## 4. Inventory what the user already has

For each folder of photos or video the user points to:
- List files, resolution, duration, GPS (EXIF, DJI `.SRT`). No GPS is normal.
- Cut a preview frame every few seconds (`ffmpeg -vf fps=0.25`) and look at
  them. For each clip/photo set record: which building (match against
  `buildings.csv` names, addresses, colours and shapes), which sides, and
  quality (blur, sun glare, people, distance).
- Classify honestly: *walk-around* (usable for 3D), *one facade while
  moving* (facade relief and texture), *panorama from one spot* or *far
  view* (reference only, no 3D).

Record the result in `coverage.json`:

```json
{"1052243741": {"status": "partial", "sides": ["S"],
                "note": "южный фасад в тени, переснять в пасмурную погоду"}}
```

`status` is `none | partial | done`; `sides` lists compass sides already
covered well (N, NE, E, SE, S, SW, W, NW).

## 5. Shot list

```bash
python3 scripts/shotlist.py city/city.json --coverage coverage.json --out shotlist/
```

Gives `shotlist.md` and `shotlist.html`, a phone-friendly map with numbered
buildings in walking order, colour by status, and the facades still needed
with their lengths. Hand it over together with the short capture rules from
`references/capture.md` (walk around at 10–20 m, camera on the facade,
overcast, second lap higher, no panoramas).

Then give every building its own folder on the user's machine, so new
photos land in the right place:

```bash
python3 scripts/house_folders.py init city/city.json --root "<project>/дома" --coverage coverage.json
```

Each folder `NN <name> [<osm id>]` has `фото/` (the user drops photos and
video here), `кадры/` (frames you cut from earlier videos that show this
building), `модель/` (your result) and `ДОСНЯТЬ.md` (what is still
missing). `<project>/дома/СТАТУС.md` sums it up. Re-running `init` keeps
existing folders and files and refreshes the two .md files.

When the user says they added photos:

```bash
python3 scripts/house_folders.py status city/city.json --root "<project>/дома" --coverage coverage.json
```

It lists buildings with new files since the last check; process only those
(step 6), update `coverage.json`, and run `init` again to refresh
`ДОСНЯТЬ.md` and `СТАТУС.md`.

## 6. Photogrammetry per building

Video → sharp frames:

```bash
python3 scripts/extract_frames.py clip.mp4 --fps 3 --out frames/
```

Frames or photos → model. Check the capture first with the sparse stage; it
runs on CPU in minutes:

```bash
python3 scripts/photogrammetry.py frames/clip --work work/house35 --stage sparse --colmap <folder>
python3 scripts/photogrammetry.py frames/clip --work work/house35 --stage mesh --colmap <folder> --openmvs <folder>
```

Ask before downloading COLMAP or OpenMVS onto the user's machine (name the
file, size and folder). With an NVIDIA card, RealityScan gives better meshes
and can replace this step; the RealityScan phone app computes in Epic's
cloud and needs no GPU.

Judge the result: registered images vs total, holes, smeared textures. Turn
what is missing into `coverage.json` entries and go back to step 5.

## 7. Put the scan on the map

In Blender:
1. Import `scene_textured.obj` and clean it: delete ground, cars, people and
   trees around the building; fill holes.
2. Align it to the building's `B_<id>` block: scale so the footprint matches
   (OSM outline length is the ruler), rotate so walls follow the outline,
   drop it to Z = 0.
3. Decimate to a game budget (houses 20–80k triangles; landmarks more) and
   bake the texture to one 4K map.
4. Replace the block's mesh with the scan, keep the object name and origin,
   then re-export: `blender -b -P scripts/blender_city.py -- --from-blend build/city.blend --out build/`.
5. Mark the building `done` in `coverage.json`.

Roofs not seen from the ground keep the OSM roof shape and colour.

## 8. Two styles

Geometry is shared; only materials change. `real`: OSM colours, scanned
textures, rough PBR. `toon`: a bright palette per building, flat roofs in
one colour, cel-shading in the engine. Keep both exports in sync from the
same `city.blend`.

## Reporting to the user

After each round say: what was built (counts), what the material allowed and
what it did not (and why, in plain words), and the shot list link. Lead with
the result. Keep jargon out: say "собрать модель из кадров" rather than
"dense reconstruction".
