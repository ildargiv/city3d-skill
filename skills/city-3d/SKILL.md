---
name: city-3d
description: Build a universal, realistic 3D map of a real city from OpenStreetMap plus the user's own photos and video, one model per building on one shared, growable landscape, to serve as the base for games, films, animation, guided tours and other projects; restyle later, export to Blender, USD and Unreal/Godot/Unity. Use when someone wants to recreate a real street, district or town in 3D.
---

# City 3D

Builds a real city in 3D as a universal base map: every building is a
separate model standing on its OSM footprint, the landscape is shared and
grows tile by tile, and the user's own photos and video raise the quality
building by building. The map is not tied to one use: games, films,
cartoons, guided walks, VR and anything else are separate projects built on
top of it (see `references/engines.md`). Keep it that way: accurate, real,
one master scene, with names and addresses on every building, and no
project-specific changes in the master.

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

- On a new machine, or when a step says "not found", run
  `python3 scripts/doctor.py` first: one call lists Python, Blender, ffmpeg,
  COLMAP, OpenMVS and the optional packages, with download links. Install
  nothing yourself; give the user the links.

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

Fix the city origin on the first build and never change it: one landmark
for the whole city, passed as `--origin LAT,LON` (ask the user which; the
first project uses its main tower). Every later build reuses the origin
stored in `city/city.json`. The district circle (`--lat/--lon/--radius`) is
separate from it, so moving the origin does not change which buildings are
kept.

### Growing the map

To add a neighbouring area later: export its box from openstreetmap.org,
keep every earlier export (an `osm/` folder is handy), and rebuild with all
of them and a circle or `--radius 0` box that covers the new extent:

```bash
python3 scripts/osm2city.py osm/*.osm --radius 0 --out city/
python3 scripts/house_folders.py init city/city.json --root "<project>/buildings"
blender -b -P scripts/blender_city.py -- city/city.json --scans "<project>/buildings" --out build/
```

Buildings keep their OSM ids and positions, existing folders keep their
names and numbers (new ones are numbered after them), finished models are
placed again from their folders, and the landscape is cut into tiles of
`--tile` metres (default 250) on a grid fixed to the origin, so old tiles
stay identical and new ones are added. In Unreal, import the tiles with
World Partition.

Show it: `city/index.html` (written next to `city.json`, with the data also in
`city.js`) opens with a double click, no server needed. It has a Realistic /
Cartoon switch, windows drawn per storey, pitched roofs, and a card per
building. After `house_folders.py init` (step 5) the card also links to the
building's folder ("Open building folder").

## 3. Blender scene and export

Run with Blender 4.2+ (or `pip install bpy` for headless use):

```bash
blender -b -P scripts/blender_city.py -- city/city.json --out build/
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
  moving* (facade relief and texture), *corridor* (driving or walking along
  a street 40–70 m from the houses: texture only), *panorama from one spot*
  or *far view* (reference only, no 3D).
- A corridor clip gives one long strip of facades, not separate buildings.
  Run it through photogrammetry at most once, as a test of the tools; do
  not try to cut building models out of it. Building models come from a
  walk round each building (step 5).

Record the result in `coverage.json`:

```json
{"1052243741": {"status": "partial", "sides": ["S"],
                "note": "южный фасад в тени, переснять в пасмурную погоду"}}
```

`status` is `none | partial | done`; `sides` lists compass sides already
covered well (N, NE, E, SE, S, SW, W, NW).

Real or invented. A side is either shot (it is in `sides`) or still to
shoot, unless the user says they do not want to shoot it (a courtyard behind
a fence, a wall nobody will see, a roof without a drone). Only then may it be
filled in by guess, for example with the texture of a shot side of the same
building, and it goes into `invented`:

```json
{"1052243741": {"status": "partial", "sides": ["S"], "invented": ["N", "roof"]}}
```

Never decide this yourself and never fill a side in silently. Invented sides
leave the shot list, show as "Invented, not shot" in the building's
TO_SHOOT.md, and `blender_city.py --coverage coverage.json` writes
`shot_sides` and `invented_sides` onto the building, so every export (glTF
extras, USD) says which parts of the city are real. Each build also writes
`build/sources.json`, the map's source manifest: how many buildings are OSM
blocks, scans or drone cuts, and which ones have invented parts. Hand it
over with every release of the map.

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
python3 scripts/house_folders.py init city/city.json --root "<project>/buildings" --coverage coverage.json
```

Each folder `NN <name> [<osm id>]` has `photos/` (the user drops photos and
video here), `frames/` (frames you cut from earlier videos that show this
building), `model/` (your result) and `TO_SHOOT.md` (what is still
missing). `<root>/STATUS.md` sums it up. Re-running `init` keeps existing
folders and files and refreshes the two .md files.

Labels and names follow `--lang` (`en` default, `ru` gives `фото/`,
`кадры/`, `модель/`, `ДОСНЯТЬ.md`, `СТАТУС.md`). Use the user's language in
`osm2city.py`, `shotlist.py` and `house_folders.py`; an existing root keeps
the language it was created in.

When the user says they added photos:

```bash
python3 scripts/house_folders.py status city/city.json --root "<project>/buildings" --coverage coverage.json
```

It lists buildings with new files since the last check; process only those
(step 6), update `coverage.json`, and run `init` again to refresh
`TO_SHOOT.md` and `STATUS.md`.

## 6. Photogrammetry per building

Video → sharp frames:

```bash
python3 scripts/extract_frames.py clip.mp4 --fps 3 --out frames/
```

If the clip has GPS (a DJI `.SRT` next to it, or samples exiftool can read),
this also writes `frames/<clip>_gps.txt`. Many gimbal cameras record none;
phone photos usually carry GPS in EXIF.

Frames or photos → model, one building per work folder (`work/<osm id>`).
Use the building's own `photos/` and `frames/`; frames shared with a
neighbour are fine, but drop frames where the building is not the main
subject. Check the capture first with the sparse stage; it runs on CPU in
minutes:

```bash
python3 scripts/photogrammetry.py <building>/frames --work work/<osm id> --stage sparse --colmap <folder> --city city/city.json [--gps frames/<clip>_gps.txt]
python3 scripts/photogrammetry.py <building>/frames --work work/<osm id> --stage mesh --colmap <folder> --openmvs <folder>
```

- Loose points are filtered after mapping and dense points need 3 views, so
  floating junk does not reach the mesh.
- With `--city` and GPS for at least 3 images, the model is put into the
  city frame (metres, oriented, in place); without GPS it is aligned to
  `block.obj` in step 7.
- RefineMesh is off by default: on a 16 GB laptop it can run out of memory.
  Add `--refine` only on a big machine. Textures are capped at 4096 px.
- Raw photos (DNG etc.) are converted to JPG in the work folder (needs
  `pip install rawpy imageio`); originals are untouched.
- An interrupted mesh stage continues where it stopped. Always run the dense
  steps through the script, not by hand.

Tools on the user's machine. Claude Code's safety checks usually block an
agent from downloading and running third-party programs, even after the user
says yes, so give the user the direct links and the target folder and let
them unpack it themselves:

| Tool | Download | Unpack to (Windows example) |
|---|---|---|
| COLMAP, no CUDA | https://github.com/colmap/colmap/releases (`colmap-x64-windows-nocuda.zip`, ~120 MB) | `Desktop\Программы\colmap` |
| OpenMVS | https://github.com/cdcseacave/openMVS/releases (`OpenMVS_Windows_x64.zip`, ~22 MB) | `Desktop\Программы\openmvs` |

Then pass `--colmap <folder> --openmvs <folder>` to `photogrammetry.py`. If
the user prefers the agent to run them, they add an allow rule in Claude
Code (`/permissions`) for that folder. With an NVIDIA card, RealityScan gives better meshes
and can replace this step; the RealityScan phone app computes in Epic's
cloud and needs no GPU.

Judge the result: registered images vs total, holes, smeared textures. Turn
what is missing into `coverage.json` entries and go back to step 5.

### From a drone (OpenDroneMap / WebODM)

A drone flight gives what the street cannot: roofs, upper floors, courtyards
and whole blocks at once, already georeferenced. Process the photos in
[WebODM](https://github.com/OpenDroneMap/WebODM) (free, runs in Docker; or
its paid one-click installers) or ODM on the command line, with
`--auto-boundary` and a textured mesh. Then cut the result into buildings:

```bash
python3 scripts/odm_split.py <odm project> city/city.json --root "<project>/buildings"
```

It moves the georeferenced mesh into the city frame and writes
`model/drone.obj` (+ textures) into every building folder the flight covered
well, in the `block.obj` frame, without the pavement. Step 7 places them;
a walk-round scan in the same folder wins over `drone.obj`, so the best
result is both: the drone for roofs and blocks, the ground for facades.
Flight rules: `references/capture.md`.

## 7. Put the scan on the map

1. Clean the mesh (in Blender, or ask the user): delete ground, cars, people
   and trees around the building; fill holes.
2. Align it to the folder's `block.obj` (the OSM block in the same frame:
   metres, origin on the footprint centre, north up the map): walls along
   the block's walls, ground at 0.
3. Decimate to a game budget (houses 20–80k triangles; landmarks more) and
   bake the texture to one 4K map. Save it as `.glb` in the folder's
   `model/`.
4. Put every finished model on the map at once:
   `blender -b -P scripts/blender_city.py -- city/city.json --scans "<project>/buildings" --out build/`
   (add `--from-blend build/city.blend` to keep edits made in the scene).
   Each `model/*.glb|obj|fbx` replaces that building's block and its
   `building:part` blocks. The script lands it on the ground, centres it on
   the footprint and fixes the scale when it is more than 25 % off; the
   rotation must come from step 2, or from `model/place.json`
   (`{"rotate_deg": 90, "scale": 1, "offset": [0, 0, 0]}`).
5. Check it against the budgets in `targets.json` (triangles, texture size,
   size and height against OSM):
   `python3 scripts/qa.py city/city.json --root "<project>/buildings"`.
   Fix or explain every miss before calling a building done.
6. Mark the building `done` in `coverage.json`.

Roofs not seen from the ground keep the OSM roof shape and colour.

## 8. Styles, after the real map

Build the realistic map first and finish it; only then talk about other
looks. A style is a layer on top of the finished real map: the geometry,
placement and folders stay, only materials (and maybe simplified meshes)
change, so any number of styles can be made from one `city.blend`.

- `real` (default): OSM colours, scanned textures, rough PBR.
- `toon`, built in: a bright palette per building, flat roofs in one
  colour, cel-shading in the engine (`--style toon` or `--style both`).
- Others on request (low-poly, winter, night, a historical period, ...):
  add a palette to `PALETTES` in `blender_city.py` or a material pass in
  Blender, and export it next to `real`.

Do not spend time on styles while the user is still shooting and building
the real map, unless they ask.

## Working on one building

The user can point at a single building folder ("improve the building in
`.../buildings/116 ... [898107550]`"). Then touch only that building: the
OSM id in brackets names it in `city.json`, the city folder is two levels
up (or ask). Run steps 6–7 for its `photos/` and `frames/` only, write the
result to its `model/`, update its `coverage.json` entry and re-run `init`.
Do not rebuild the district, re-read other folders or re-export other
buildings.

## A team of helpers

Every building folder is self-contained, so the user can hand folders to
other people. `TO_SHOOT.md` ends with a "For a helper" section: shoot the
missing facades, build the model in any tool, align it to `block.obj`,
export `.glb` into `model/`, send the folder back without renaming it (the
`[osm id]` places it). When folders come back, the user drops them into the
same root and step 7.4 puts them all on the map. Check each returned model
before placing it: a mesh, sensible size, textures inside, nothing that
came from Google, Yandex or 2GIS.

## Keeping it cheap: which model does what

Heavy lifting (OSM, Blender, ffmpeg, COLMAP, OpenMVS) is done by programs on
the CPU and costs nothing in tokens. Spend model time only on judgement, and
route it:

| Work | Model | How |
|---|---|---|
| Developing the skill, unclear cases, quality calls, talking the user through decisions | Opus (main session) | only when needed |
| The routine loop: run scripts, update folders and statuses, explain results | Sonnet | `/model sonnet` in the session doing the loop |
| Bulk checks: new files, cutting frames, blur/glare, file lists | Haiku | the `frame-triage` agent |
| Looking at many frames (usable? blur? which building?) | the user's local vision model, free | `frame_triage.py --llm http://localhost:1234/v1`; a guess to confirm, not a decision |
| Texts: building descriptions for a guide, notes, translations | the user's local model, free | ask the user for its address; Claude reviews |
| Matching frames to buildings without GPS | Sonnet | the `building-matcher` agent |
| Photogrammetry per building and its verdict | Sonnet | the `model-builder` agent |

Habits that save more than model choice:
- Look at one frame every few seconds, never at every frame.
- Start each new round (new photos, new district) in a fresh session: the
  state lives in the folders, `coverage.json` and `STATUS.md`, not in chat.
- Do not keep a session open while COLMAP runs; check back when it is done.

## Reporting to the user

After each round say: what was built (counts), what the material allowed and
what it did not (and why, in plain words), and the shot list link. Lead with
the result. Keep jargon out: say "собрать модель из кадров" rather than
"dense reconstruction".
