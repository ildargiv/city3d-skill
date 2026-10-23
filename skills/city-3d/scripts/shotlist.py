#!/usr/bin/env python3
"""Make a shot list: which buildings and facades still need photos or video.

  python3 shotlist.py city.json --coverage coverage.json --out shotlist/

coverage.json is kept by the agent between rounds, keyed by OSM id:
  {"1052243741": {"status": "partial", "sides": ["S", "E"],
                  "note": "south facade in shade, reshoot overcast"}}
status: none | partial | done. "sides" lists facades already covered well.
Buildings missing from the file count as "none".

Writes shotlist.md (to read or print) and shotlist.html (a map to carry on a
phone: buildings coloured by status, numbered in walking order).
"""
import argparse
import html
import json
import math
import os

SIDES = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
SIDE_RU = {"N": "северный", "NE": "северо-восточный", "E": "восточный",
           "SE": "юго-восточный", "S": "южный", "SW": "юго-западный",
           "W": "западный", "NW": "северо-западный"}
SIDE_EN = {"N": "north", "NE": "north-east", "E": "east", "SE": "south-east",
           "S": "south", "SW": "south-west", "W": "west", "NW": "north-west"}
STATUS_RU = {"none": "не снят", "partial": "снят частично", "done": "готов"}
TEXT = {
    "en": {
        "sides": SIDE_EN, "status": {"none": "not shot", "partial": "partly shot", "done": "done"},
        "title": "Shot list", "district": "district", "building": "Building",
        "summary": "To shoot: {n} buildings, {p} of them partly shot. Done: {d}. Small structures without their own shoot: {t}.",
        "how": "How to shoot: walk round the building 10–20 m away, camera on the facade, "
               "a frame every 1–2 steps (or slow 4K video), then a second lap with the camera "
               "higher. Overcast or shade, no panoramas from one spot.",
        "levels": "levels", "m": "m", "need": "Facades to shoot", "note": "Note",
        "map": "On the map", "facades": "Facades",
        "legend": ("not shot", "partly", "done", "north is up"),
    },
    "ru": {
        "sides": SIDE_RU, "status": STATUS_RU,
        "title": "Список съёмки", "district": "район", "building": "Здание",
        "summary": "Нужно снять: {n} зданий, из них частично снято {p}. Готово: {d}. Мелких построек без отдельной съёмки: {t}.",
        "how": "Как снимать: обойти дом на расстоянии 10–20 м, камера всё время на фасаде, "
               "шаг 1–2 м между кадрами (или видео 4K, медленно). Затем второй круг с камерой "
               "выше. Пасмурная погода или тень, без панорам с одной точки.",
        "levels": "эт.", "m": "м", "need": "Снять фасады", "note": "Замечание",
        "map": "На карте", "facades": "Фасады",
        "legend": ("не снят", "частично", "готов", "север сверху"),
    },
}
MIN_FACADE = 4.0  # metres; shorter walls are covered by neighbouring shots


def facades(ring):
    """Group footprint edges by outward compass side; return {side: length}."""
    out = {}
    n = len(ring)
    for i in range(n):
        (x1, y1), (x2, y2) = ring[i], ring[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length < 0.5:
            continue
        # ring is counter-clockwise, so the outward normal is (dy, -dx)
        bearing = math.degrees(math.atan2(dy, -dx)) % 360  # 0 = north
        side = SIDES[int((bearing + 22.5) // 45) % 8]
        out[side] = out.get(side, 0) + length
    return {s: round(l, 1) for s, l in out.items() if l >= MIN_FACADE}


def walking_order(items, start=(0.0, 0.0)):
    """Greedy nearest-neighbour tour starting from the district centre."""
    left, path, pos = list(items), [], tuple(start)
    while left:
        nxt = min(left, key=lambda b: math.hypot(b["x"] - pos[0], b["y"] - pos[1]))
        left.remove(nxt)
        path.append(nxt)
        pos = (nxt["x"], nxt["y"])
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("city_json")
    ap.add_argument("--coverage", help="coverage.json from earlier rounds")
    ap.add_argument("--out", default="shotlist")
    ap.add_argument("--lang", choices=["en", "ru"], default=None,
                    help="text language (default: city.json lang, else en)")
    ap.add_argument("--include-parts", action="store_true",
                    help="list building:part pieces separately")
    args = ap.parse_args()

    with open(args.city_json, encoding="utf-8") as f:
        city = json.load(f)
    L = TEXT[args.lang or city.get("lang") or "en"]
    cov = {}
    if args.coverage and os.path.exists(args.coverage):
        with open(args.coverage, encoding="utf-8") as f:
            cov = json.load(f)

    todo, done, tiny = [], [], 0
    for b in city["buildings"]:
        if b.get("part") and not args.include_parts:
            continue
        c = cov.get(str(b["id"]), {})
        status = c.get("status", "none")
        sides = facades(b["footprint"])
        missing = {s: l for s, l in sides.items() if s not in c.get("sides", [])}
        item = {**b, "status": status, "missing": missing,
                "note": c.get("note", ""), "sides": sides}
        if not sides:
            tiny += 1  # sheds and kiosks: no facade worth a dedicated shot
            continue
        (done if status == "done" or not missing else todo).append(item)

    route = walking_order(todo, city.get("center_xy", (0, 0)))
    os.makedirs(args.out, exist_ok=True)

    lines = [f"# {L['title']}: {city.get('name') or L['district']}", "",
             L["summary"].format(n=len(route), p=sum(b["status"] == "partial" for b in route), d=len(done), t=tiny), "", L["how"], ""]
    for i, b in enumerate(route, 1):
        title = b["name"] or b["address"] or f"{L['building']} {b['id']}"
        lines.append(f"## {i}. {title}")
        meta = [f"{b['levels']} {L['levels']}", f"{b['area_m2']:.0f} {L['m']}²",
                L["status"].get(b["status"], b["status"])]
        if b["address"] and b["name"]:
            meta.insert(0, b["address"])
        lines.append(" · ".join(meta))
        need = ", ".join(f"{L['sides'][s]} ({l:.0f} {L['m']})" for s, l in
                         sorted(b["missing"].items(), key=lambda kv: SIDES.index(kv[0])))
        lines.append(f"- {L['need']}: {need}")
        if b["note"]:
            lines.append(f"- {L['note']}: {b['note']}")
        lines.append(f"- {L['map']}: https://www.openstreetmap.org/"
                     f"{'relation/' + str(-b['id'] // 100) if b['id'] < 0 else 'way/' + str(b['id'])}")
        lines.append("")
    with open(os.path.join(args.out, "shotlist.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # SVG map: north up, 1 unit = 1 m
    r = city["radius"]
    cx, cy = city.get("center_xy", (0, 0))
    colour = {"none": "#d9480f", "partial": "#f59f00", "done": "#2f9e44"}
    order = {b["id"]: i for i, b in enumerate(route, 1)}
    shapes = []
    for a in city.get("areas", []):
        pts = " ".join(f"{x:.1f},{-y:.1f}" for x, y in a["polygon"])
        fill = "#a5d8ff" if a["kind"] == "water" else "#d3f9d8"
        shapes.append(f'<polygon points="{pts}" fill="{fill}"/>')
    for rd in city.get("roads", []):
        pts = " ".join(f"{x:.1f},{-y:.1f}" for x, y in rd["line"])
        shapes.append(f'<polyline points="{pts}" fill="none" stroke="#ced4da" '
                      f'stroke-width="{rd["width"]}" stroke-linecap="round"/>')
    for b in todo + done:
        pts = " ".join(f"{x:.1f},{-y:.1f}" for x, y in b["footprint"])
        st = "done" if b in done else b["status"]
        shapes.append(f'<polygon points="{pts}" fill="{colour[st]}" fill-opacity="0.8" '
                      f'stroke="#212529" stroke-width="0.6"><title>'
                      f'{html.escape(b["name"] or b["address"] or str(b["id"]))}</title></polygon>')
    for b in route:
        shapes.append(f'<text x="{b["x"]:.1f}" y="{-b["y"]:.1f}" font-size="11" '
                      f'text-anchor="middle" dominant-baseline="central" '
                      f'font-weight="700" fill="#fff" stroke="#212529" stroke-width="2.5" '
                      f'paint-order="stroke">{order[b["id"]]}</text>')
    rows = "".join(
        f"<li><b>{order[b['id']]}. {html.escape(b['name'] or b['address'] or L['building'] + ' ' + str(b['id']))}</b>"
        f"<br>{L['facades']}: {html.escape(', '.join(L['sides'][s] for s in b['missing']))}"
        + (f"<br><i>{html.escape(b['note'])}</i>" if b["note"] else "") + "</li>"
        for b in route)
    page = f"""<!doctype html><html lang="{'ru' if L is TEXT['ru'] else 'en'}"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{L['title']}</title>
<style>
body{{font:15px/1.45 system-ui,sans-serif;margin:0;padding:16px;color:#212529;background:#f8f9fa}}
svg{{width:100%;height:auto;background:#fff;border:1px solid #dee2e6;border-radius:8px}}
.k span{{display:inline-block;width:12px;height:12px;border-radius:3px;margin:0 4px 0 12px;vertical-align:-1px}}
li{{margin:0 0 10px}} h1{{font-size:20px;margin:0 0 8px}}
</style>
<h1>{html.escape(city.get('name') or L['title'])}</h1>
<p class="k"><span style="background:{colour['none']}"></span>{L['legend'][0]}
<span style="background:{colour['partial']}"></span>{L['legend'][1]}
<span style="background:{colour['done']}"></span>{L['legend'][2]} · {L['legend'][3]}</p>
<svg viewBox="{cx - r:.0f} {-cy - r:.0f} {2 * r} {2 * r}">{''.join(shapes)}</svg>
<ol style="list-style:none;padding:0">{rows}</ol>
<p style="color:#868e96;font-size:12px">{html.escape(city.get('attribution', ''))}</p>
"""
    with open(os.path.join(args.out, "shotlist.html"), "w", encoding="utf-8") as f:
        f.write(page)
    print(f"shot list: {len(route)} to shoot, {len(done)} done -> {args.out}/")


if __name__ == "__main__":
    main()
