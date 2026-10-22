#!/usr/bin/env python3
"""Download OpenStreetMap data for a circle around a point (Overpass API).

  python3 fetch_osm.py --place "Республиканский театр кукол, Йошкар-Ола" --radius 300
  python3 fetch_osm.py --lat 56.63696 --lon 47.90838 --radius 300 --out map.json

Prints the centre it used, so the same --lat/--lon can go to osm2city.py.
If these hosts are blocked, export the area by hand at
https://www.openstreetmap.org/export (button "Export") and use that .osm file.
Data (c) OpenStreetMap contributors, ODbL.
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

UA = "city3d-skill/0.1 (+https://github.com/ildargiv/city3d-skill)"
OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter"]


def get(url, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=180) as r:
        return r.read()


def geocode(place):
    q = urllib.parse.urlencode({"q": place, "format": "json", "limit": 1})
    hits = json.loads(get(f"https://nominatim.openstreetmap.org/search?{q}"))
    if not hits:
        sys.exit(f"Nothing found for: {place}")
    return float(hits[0]["lat"]), float(hits[0]["lon"]), hits[0]["display_name"]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--place")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--radius", type=float, default=300)
    ap.add_argument("--out", default="map.json")
    a = ap.parse_args()
    if a.place:
        a.lat, a.lon, name = geocode(a.place)
        print(f"place: {name}")
    if a.lat is None or a.lon is None:
        sys.exit("Give --place or --lat and --lon")

    # A little extra radius so roads and parks crossing the edge stay whole.
    around = f"around:{a.radius * 1.2:.0f},{a.lat},{a.lon}"
    query = f"""[out:json][timeout:180];
(
  nwr({around})[building];
  nwr({around})["building:part"];
  way({around})[highway];
  nwr({around})[landuse];
  nwr({around})[leisure];
  nwr({around})[natural];
  nwr({around})[waterway];
  nwr({around})[amenity];
  nwr({around})[place=square];
);
(._;>;);
out body;"""
    body = urllib.parse.urlencode({"data": query}).encode()
    for i, url in enumerate(OVERPASS * 2):
        try:
            raw = get(url, body)
            break
        except Exception as e:  # busy servers answer 429/504; try the next one
            print(f"{url}: {e}", file=sys.stderr)
            time.sleep(5 * (i + 1))
    else:
        sys.exit("Overpass is unreachable. Export the area by hand at "
                 "https://www.openstreetmap.org/export instead.")
    with open(a.out, "wb") as f:
        f.write(raw)
    n = len(json.loads(raw).get("elements", []))
    print(f"centre: --lat {a.lat:.6f} --lon {a.lon:.6f}")
    print(f"saved {n} elements -> {a.out}")


if __name__ == "__main__":
    main()
