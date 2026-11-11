"""Build data/beluiken.json from the Flemish heritage inventory (Inventaris Onroerend Erfgoed).

1. Download the CSV of all heritage objects with typology "beluiken".
2. Keep the ones in the municipality of Gent.
3. For each, fetch the object's JSON for its description and location,
   falling back to OpenStreetMap Nominatim to geocode the first address.
"""
import csv, io, json, os, re, sys, time, urllib.request, urllib.parse

BASE = "https://inventaris.onroerenderfgoed.be"
UA = "walking-gent-builder/1.0 (+https://github.com/poldendrol/Walking-Gent)"
OUT = "data/beluiken.json"


def get(url, accept="application/json"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8-sig")


def strip_html(s):
    s = re.sub(r"<br\s*/?>|</p>", "\n", s or "")
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"[ \t]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()


try:
    from pyproj import Transformer
    L72 = Transformer.from_crs("EPSG:31370", "EPSG:4326", always_xy=True)
except Exception:  # pragma: no cover
    L72 = None


def all_coords(obj):
    """Yield every [x, y] pair inside a GeoJSON-like structure."""
    if isinstance(obj, list):
        if len(obj) >= 2 and all(isinstance(v, (int, float)) for v in obj[:2]):
            yield obj[:2]
        else:
            for v in obj:
                yield from all_coords(v)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from all_coords(v)


def to_latlng(x, y):
    if 2 < x < 7 and 49 < y < 52:  # already WGS84
        return y, x
    if L72 and 10000 < x < 300000 and 10000 < y < 300000:
        lng, lat = L72.transform(x, y)
        return lat, lng
    return None


def find_geometry(d):
    loc = d.get("locatie") or {}
    for key in ("centroid", "punt", "contour", "geometrie"):
        g = loc.get(key) if isinstance(loc, dict) else None
        if g:
            pts = list(all_coords(g.get("coordinates", g) if isinstance(g, dict) else g))
            if pts:
                xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
                ll = to_latlng(sum(xs) / len(xs), sum(ys) / len(ys))
                if ll:
                    return ll, key
    return None, None


def nominatim(address, deelgemeente):
    first = address.split(",")[0]
    first = re.sub(r"\s*\(Gent\)\s*$", "", first)
    first = re.sub(r"(\d+)[A-Z]?-.*$", r"\1", first)  # "Abrikoosstraat 43-75" -> "Abrikoosstraat 43"
    q = f"{first}, {deelgemeente or 'Gent'}, Belgium"
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({"q": q, "format": "json", "limit": 1})
    time.sleep(1.1)  # Nominatim usage policy: max 1 request per second
    try:
        res = json.loads(get(url))
    except Exception as e:
        print("  nominatim failed", q, e, file=sys.stderr)
        return None
    if res:
        return float(res[0]["lat"]), float(res[0]["lon"])
    if q != f"{first.split(' ')[0]}, Gent, Belgium":  # retry street only
        street = re.sub(r"\s+\d.*$", "", first)
        return nominatim(street + " 1", deelgemeente) if street != first else None
    return None


def main():
    os.makedirs("data", exist_ok=True)
    csv_text = get(f"{BASE}/erfgoedobjecten.csv?typologie=beluiken&type=dibe_relict", accept="text/csv")
    rows = [r for r in csv.DictReader(io.StringIO(csv_text)) if r["gemeente"].strip() == "Gent"]
    print(f"{len(rows)} beluiken in Gent", file=sys.stderr)
    out, sample_dumped = [], False
    for r in rows:
        oid = r["id"]
        item = {
            "id": oid,
            "name": r["naam"].strip(),
            "address": re.sub(r"\s*\(Gent\)\s*$", "", r["locatie"].strip()),
            "deelgemeente": r["deelgemeente"].strip(),
            "period": r["datering"].strip(),
            "designer": re.sub(r"\s*\(Ontwerper\)", "", r["personen"]).strip(),
            "style": r["stijl"].strip(),
            "typology": r["typologie"].strip(),
            "preserved": r["bewaringstoestand"].strip() == "bewaard",
            "established": r["is_vastgesteld"] == "True",
            "protected": r["is_beschermd"] == "True",
            "url": f"{BASE}/erfgoedobjecten/{oid}",
            "description": "",
            "lat": None, "lng": None, "geo": None,
        }
        try:
            d = json.loads(get(f"{BASE}/erfgoedobjecten/{oid}"))
            if not sample_dumped:
                with open("data/_sample_object.json", "w") as f:
                    json.dump(d, f, ensure_ascii=False, indent=1)
                sample_dumped = True
            for key in ("beschrijving", "korte_beschrijving", "description"):
                if d.get(key):
                    item["description"] = strip_html(d[key])
                    break
            ll, how = find_geometry(d)
            if ll:
                item["lat"], item["lng"], item["geo"] = round(ll[0], 6), round(ll[1], 6), "inventaris"
        except Exception as e:
            print("  json failed", oid, e, file=sys.stderr)
        if item["lat"] is None:
            ll = nominatim(r["locatie"], item["deelgemeente"])
            if ll:
                item["lat"], item["lng"], item["geo"] = round(ll[0], 6), round(ll[1], 6), "nominatim"
        print(f"  {oid} {item['name']} -> {item['geo']}", file=sys.stderr)
        out.append(item)
        time.sleep(0.3)
    with open(OUT, "w") as f:
        json.dump({"source": f"{BASE}/erfgoedobjecten?typologie=beluiken",
                   "generated": time.strftime("%Y-%m-%d"),
                   "items": out}, f, ensure_ascii=False, indent=1)
    print(f"wrote {len(out)} items, {sum(1 for i in out if i['lat'])} with location", file=sys.stderr)


if __name__ == "__main__":
    main()
