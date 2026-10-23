# Using the map: engines, film, tours

`blender_city.py` writes, per style (`real` by default; `toon` or others once
the real map is done):

- `landscape_<style>.glb` — one landscape: ground, roads, parks, water, trees
- `buildings_<style>.glb` — all buildings, each a separate mesh in its world
  position
- `buildings/B_<id>_<style>.glb` — each building alone, origin at the centre
  of its footprint at ground level
- `placement.json` — world position of every building origin (metres, X east,
  Y north, Z up, origin = district centre)

Import landscape and buildings files together: they share one origin, so
everything lines up without manual placement. Keep `city.blend` as the
master; replace a block with a scanned or hand-made building there and
re-export with `--from-blend city.blend`.

## Unreal Engine 5

1. Content Browser → Import → `landscape_real.glb` and `buildings_real.glb`
   (Interchange glTF importer). Keep "Import into level" so each building
   becomes its own actor in the right place. glTF metres become Unreal
   centimetres automatically.
2. For Nanite, enable it on the high-poly scanned meshes; block models do
   not need it.
3. For a toon look, use `*_toon.glb` plus a cel-shading post-process
   material; for realism, swap the flat colours for Quixel/Fab materials.
4. Big areas: a Landscape actor sculpted from the DEM can replace the flat
   ground; keep the roads mesh above it.

## Film and animation

`blender_city.py --usd` also writes `city.usdc`, the whole scene as USD with
the building properties. Open it in Blender, Houdini, NVIDIA Omniverse or
Unreal (USD Stage). Keep one master map and make a separate shot scene per
film that references it, so fixes to the map reach every film.

## Guided walks and virtual tours

The `.glb` files carry each building's OSM id, name, address and height as
glTF extras, so a tour app can label buildings and link them to text or
audio. In the browser, three.js or Babylon.js load the per-tile and
per-building files; `city.json` already has names, addresses and positions
for a route. For VR, use Unreal or Godot with the same files.

## Godot 4

Drag both `.glb` files into the project and instance them into one scene.
Each building is a separate MeshInstance3D node named `B_<id>`.

## Unity

Install the glTFast package, then drop the `.glb` files into Assets and
place both prefabs at the origin.
