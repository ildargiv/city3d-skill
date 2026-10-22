# What data may go into the model

Checked October 2026. Not legal advice: terms change, so re-check before a
commercial release.

## Safe to download and build from (the automated pipeline uses only these)

| Source | What | Terms |
|---|---|---|
| OpenStreetMap | outlines, levels, roofs, roads, water, trees | ODbL. Credit "© OpenStreetMap contributors". A changed *database* you publish stays ODbL; a model or render made from it can use any licence. https://www.openstreetmap.org/copyright |
| Copernicus DEM GLO-30, SRTM | terrain heights, 30 m | Free with credit (Copernicus: © DLR/Airbus, ESA). SRTM is public domain. |
| Mapillary, Panoramax | street-level photos by volunteers | CC BY-SA 4.0: credit the authors, share derived works alike. Use only as extra angles. |
| The user's own photos and video | facades, textures, photogrammetry | The user owns them. Shoot from public places; blur faces and plates; swap logos and signs for invented ones. Architecture in public places may be depicted (RU: ГК РФ ст. 1276). |
| Photos with explicit permission or a free licence (Wikimedia Commons) | textures, reference | Follow the stated licence and credit the author. |

## Look only: allowed in personal use, never copied into the model

The user may open these in a browser to check how a roof or facade looks, and
describe it in words. The agent must not download, screenshot, trace or feed
them to a 3D or image tool.

**Yandex Maps, Yandex Panoramas** (terms dated 23.09.2026, item 4.6:
https://yandex.ru/legal/maps_termsofuse/). All data is "for personal
non-commercial use only". Copying, reproducing or reworking (переработка) the
data, or publishing it, needs written permission from the rights holder.
Viewing in the service and embedding the official map widget are fine.

**Google Maps, Google Earth, Street View, Photorealistic 3D Tiles**
(https://www.google.com/help/terms_maps, last modified 27.01.2026; geo
guidelines https://about.google/brand-resource-center/products-and-services/geo-guidelines/;
tiles policy https://developers.google.com/maps/documentation/tile/policies).
- Personal use: viewing, printing maps for non-commercial or personal use,
  annotating maps, a handful of static Earth images for non-commercial
  posts, always with Google attribution left in place.
- Not allowed even for personal projects: copying content beyond those
  permissions, mass download, building another mapping dataset;
  screenshotting, tracing, downloading or stitching Street View imagery;
  using Earth output "to reconstruct 3D models"; extracting, tracing or
  deriving 3D objects from Photorealistic 3D Tiles, or caching them offline.
- So importing Google 3D tiles into Blender (e.g. with the Blosm add-on) is a
  terms violation for a deliverable. Do not do it in this skill.

**2GIS**: same rule. Viewing in the app, nothing copied into the model.

## What the skill does with user uploads

Users may bring any source they like, at their own responsibility. When the
user adds files that did not come from their own camera, ask once where they
are from and record the answer in `sources.md` next to the project. Refuse to
automate scraping of the services in the "look only" list.

Every published model or scene carries: "© OpenStreetMap contributors (ODbL)"
plus credits for any CC BY-SA photos used.
