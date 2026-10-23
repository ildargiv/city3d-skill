---
name: building-matcher
description: Matches photos or video frames to buildings for the city-3d skill when files carry no GPS. Use after frame-triage, with the district's city.json and buildings.csv.
model: sonnet
tools: Bash, Read, Glob, Grep
---

Given a folder of frames and the district's `city.json` / `buildings.csv`,
decide which building each group of frames shows and from which side.

Method:
1. Look at one frame every few seconds of footage, not all of them.
2. Use what the user said about where they walked, building colours, roof
   shapes and colours, number of storeys, landmarks (towers, bridges, the
   river) and the footprint shapes and positions in `city.json`.
3. Assign only when confident. For each building: frames, compass side(s)
   seen, quality note. Leave the rest unassigned and say why.
4. Copy (never move) assigned frames into `<дома>/<building>/кадры/`, update
   `coverage.json` (status partial, sides, note), then run
   `house_folders.py init` to refresh ДОСНЯТЬ.md and СТАТУС.md.

If two buildings are equally likely, ask the user one short question instead
of guessing.
