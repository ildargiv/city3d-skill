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

## Roofs

Roofs of tall buildings are not visible from the ground. Use the OSM
roof shape and colour. Drones need flight permission in Russia (and are
restricted in city centres); do not plan around them without that.
