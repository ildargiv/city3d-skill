# Capture guide: photos and video for a building

Photogrammetry needs the camera to *move* between shots. A panorama turned
from one spot gives a nice picture and no 3D at all.

## One building, one walk

1. Stand 10–20 m from the wall, camera aimed at the facade.
2. Walk all the way round at an even pace. Photos: one every 1–2 steps
   (60–80 % overlap). Video: 4K, slow walk, no fast pans.
3. Second lap with the camera raised (arms up or a pole) to see cornices and
   the lower roof edge.
4. Corners: take 3–4 extra shots while turning round each corner.
5. 1–2 minutes of video or 60–150 photos per ordinary house.

## Conditions

- Overcast or full shade. Hard sun makes shadows that get baked into the
  texture; never shoot into the sun.
- Few people and cars. Wait for the street to clear, or shoot early morning.
- Fixed exposure and focus if the camera allows (AE/AF lock); no filters,
  no beauty modes, no cropping.
- Gimbal cameras (DJI Osmo Pocket): normal follow mode, not FPV. Turn on
  location recording via the phone app if available.
- Phones: keep the original files (HEIC/JPG with EXIF GPS).

## What the agent checks after each round

- Registered images out of total (COLMAP "sparse" stage). Below about 70 %
  usually means too little overlap or a turn-on-the-spot panorama.
- Which facades have no cameras looking at them: those go back into the
  shot list (`coverage.json`, then `shotlist.py`).
- Holes, blur, people and glare in the textured mesh, named by side
  ("north facade, ground floor, left half").

## Towers and landmarks

A tower is the hardest thing in the city to scan, because it is tall, it has
one narrow side facing each street, and the top is never visible from the
ground. Walking round it once is not enough:

1. Four separate passes, one per facade, 10–15 m away, camera at eye level.
2. A second lap with the camera raised (pole, monopod, arms up) for the
   cornices and the tiers above.
3. A drone orbit at 2–3 heights for the roof, the tent roof and the top
   tiers. Without it the top is a smeared guess.
4. A scale reference in one frame per facade: a person or an A4 sheet against
   the wall. Drone photos with GPS give scale for free; street photos do not.
5. Expect 300–400 frames for a tower. 15 frames cut from one video taken
   from a bridge give a flat postcard, not a model -- see below.

## Check the scan before it reaches the map

```bash
python3 scripts/fix_scan.py "<building>/model/scan.glb" --height 50
```

It prints the numbers and one of three verdicts:

- **reshoot** -- the capture is wrong and no script fixes it. The mesh is
  flat (one side seen), covers a fraction of the walls, or the scale is off
  by more than a quarter. Put the building back in the shot list with the
  missing sides named.
- **repair** -- the shape is fine, the mesh is not: inverted winding, no
  normals, floating debris, too many triangles, atlas half empty.
  `--out fixed.glb` fixes that part, `--collision coll.glb` adds stand-in
  boxes for the game.
- **ok** -- nothing to do.

A real example, a tower in Yoshkar-Ola shot from the Theatre bridge, 15
frames out of one video (3840×2160), and what the report said:

```
159028 triangles, size 8.6 x 8.7 x 15.2 m
area 140 m2 = 22 % of the walls a closed shell would have
flatness 0.055 (1.0 = a cube, 0.05 = a sheet), 90 % of points within 0.4 m
830 border edges, 98 % of faces point inwards, normals no
texture 8192x8192 png 25.0 MB, atlas used 25 %, v [0.0, 0.25]
scale to OSM height: 3.3x
Verdict: reshoot
```

Read it as: the camera moved sideways along the bridge, the tower stayed
far away, so the reconstruction had no parallax to measure depth with and
collapsed into a relief on a plane. The triangle count was high and the
model was still wrong: **more triangles is not more building**. That is why
the capture rules above matter more than any processing setting.

`--out` on that file still gives a valid, upright, 20k-triangle model with
normals and a collision box -- but it stays flat, because the data is not
there. Repair makes a good scan shippable; it cannot make a bad one good.

## Roofs

Roofs of tall buildings are not visible from the ground. Use the OSM
roof shape and colour, or fly a drone.

## Drone flights (for OpenDroneMap / WebODM)

Only where flying is allowed: check the local rules first. In Russia a
flight over a city needs registration and permission, and city centres are
often closed zones; elsewhere, check the national aviation authority.

- Grid flight for the block: 70–80 % front overlap, 60–70 % side overlap,
  height 60–100 m, camera tilted 70–80° (not straight down) so facades show.
- Add a second grid crossing the first, or an orbit round each landmark at
  2–3 heights with the camera on the building, for walls and towers.
- Overcast light, fixed exposure, GPS on (drones store it in every photo).
- Flight apps that plan grids: DJI Pilot 2 / GS Pro, Litchi, Dronelink;
  WebODM reads their photos directly.
