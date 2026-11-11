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


MARY = re.compile(r"\bmaria|onze[- ]lieve[- ]vrouw|o\.\s?-?l\.\s?-?v|madonna|\bmadonne|lourdes|mariagrot", re.I)


def fetch_csv(query):
    try:
        text = get(f"{BASE}/erfgoedobjecten.csv?{query}", accept="text/csv")
    except Exception as e:
        print(f"csv failed {query}: {e}", file=sys.stderr)
        return []
    rows = [r for r in csv.DictReader(io.StringIO(text)) if (r.get("gemeente") or "").strip() == "Gent"]
    print(f"{query}: {len(rows)} rows in Gent", file=sys.stderr)
    return rows


def build(rows, keep=None, label=""):
    out, seen = [], set()
    for r in rows:
        oid = r["id"]
        if oid in seen:
            continue
        seen.add(oid)
        item = {
            "id": oid,
            "name": r["naam"].strip(),
            "address": re.sub(r"\s*\(Gent\)\s*$", "", r["locatie"].strip()),
            "deelgemeente": r["deelgemeente"].strip(),
            "period": r["datering"].strip(),
            "designer": re.sub(r"\s*\((Ontwerper|Beeldhouwer|Kunstenaar)\)", "", r["personen"]).strip(),
            "style": r["stijl"].strip(),
            "typology": r["typologie"].strip(),
            "preserved": r["bewaringstoestand"].strip() == "bewaard",
            "established": r["is_vastgesteld"] == "True",
            "protected": r["is_beschermd"] == "True",
            "url": f"{BASE}/erfgoedobjecten/{oid}",
            "description": "",
            "text": "",
            "photo": None,
            "lat": None, "lng": None, "geo": None,
        }
        d = None
        try:
            d = json.loads(get(f"{BASE}/erfgoedobjecten/{oid}"))
            for key in ("beschrijving", "korte_beschrijving", "description"):
                if d.get(key):
                    item["description"] = strip_html(d[key])
                    break
            for t in d.get("teksten") or []:
                if (t.get("teksttype") or {}).get("naam") == "Basistekst" and t.get("tekst"):
                    item["text"] = strip_html(t["tekst"])
                    break
            foto = d.get("primaire_foto")
            m = re.search(r"(\d+)$", foto if isinstance(foto, str) else str((foto or {}).get("uri") or (foto or {}).get("id") or ""))
            if m:
                item["photo"] = f"https://beeldbank.onroerenderfgoed.be/images/{m.group(1)}/content/medium"
            ll, how = find_geometry(d)
            if ll:
                item["lat"], item["lng"], item["geo"] = round(ll[0], 6), round(ll[1], 6), "inventaris"
        except Exception as e:
            print("  json failed", oid, e, file=sys.stderr)
        if keep and not keep(item):
            continue
        if item["lat"] is None:
            ll = nominatim(r["locatie"], item["deelgemeente"])
            if ll:
                item["lat"], item["lng"], item["geo"] = round(ll[0], 6), round(ll[1], 6), "nominatim"
        print(f"  {label} {oid} {item['name']} -> {item['geo']}", file=sys.stderr)
        out.append(item)
        time.sleep(0.25)
    return out


def write(path, source, items):
    with open(path, "w") as f:
        json.dump({"source": source, "generated": time.strftime("%Y-%m-%d"), "items": items}, f, ensure_ascii=False, indent=1)
    print(f"wrote {path}: {len(items)} items, {sum(1 for i in items if i['lat'])} with location", file=sys.stderr)


def mary_snippet(item):
    """Return the sentence(s) about the Mary statue, for objects where it is one detail of a bigger building."""
    text = item["text"] or item["description"]
    sents = re.split(r"(?<=[.!?])\s+", text)
    hits = [s for s in sents if MARY.search(s)]
    return " ".join(hits[:3])


CHAINS = re.compile(r"carrefour|delhaize|okay|spar\b|proxy|alvo|intermarch|albert heijn|aldi|lidl|colruyt|bio-planet|crf", re.I)


def late_closing(oh):
    """True if opening_hours mentions 24/7 or a closing time at/after 22:00 or past midnight."""
    if not oh:
        return None
    if "24/7" in oh:
        return True
    for a, b in re.findall(r"(\d{1,2}):\d{2}\s*-\s*(\d{1,2}):\d{2}", oh):
        a, b = int(a), int(b)
        if b >= 22 or b < a or b == 24:
            return True
    return False


def nightshops():
    q = """[out:json][timeout:120];
area["name"="Gent"]["admin_level"="8"]->.g;
(nwr["shop"="convenience"](area.g); nwr["shop"]["name"~"[Nn]ight|[Nn]acht"](area.g););
out center tags;"""
    data = urllib.parse.urlencode({"data": q}).encode()
    req = urllib.request.Request("https://overpass-api.de/api/interpreter", data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=180) as r:
        els = json.loads(r.read().decode())["elements"]
    out = []
    for e in els:
        t = e.get("tags", {})
        name = t.get("name", "").strip()
        if CHAINS.search(name + " " + t.get("brand", "")):
            continue
        lat = e.get("lat") or (e.get("center") or {}).get("lat")
        lng = e.get("lon") or (e.get("center") or {}).get("lon")
        if lat is None:
            continue
        addr = " ".join(x for x in [t.get("addr:street", ""), t.get("addr:housenumber", "")] if x)
        oh = t.get("opening_hours", "")
        out.append({"id": f"osm-{e['type']}-{e['id']}", "name": name or "Night shop", "address": addr,
                    "opening_hours": oh, "late": late_closing(oh), "phone": t.get("phone") or t.get("contact:phone") or "",
                    "lat": round(lat, 6), "lng": round(lng, 6),
                    "url": f"https://www.openstreetmap.org/{e['type']}/{e['id']}"})
    print(f"nightshops: {len(out)} (late: {sum(1 for i in out if i['late'])}, no hours: {sum(1 for i in out if i['late'] is None)})", file=sys.stderr)
    return out


def main():
    os.makedirs("data", exist_ok=True)
    only = sys.argv[1:] or ["beluiken", "maria", "nightshops"]

    if "nightshops" in only:
        try:
            items = [i for i in nightshops() if i["late"] is not False]
            with open("data/nightshops.json", "w") as f:
                json.dump({"source": "OpenStreetMap contributors (ODbL)", "generated": time.strftime("%Y-%m-%d"), "items": items},
                          f, ensure_ascii=False, indent=1)
            print(f"wrote data/nightshops.json: {len(items)}", file=sys.stderr)
        except Exception as e:
            print("nightshops failed", e, file=sys.stderr)

    if "beluiken" in only:
        rows = fetch_csv("typologie=beluiken&type=dibe_relict")
        write(OUT, f"{BASE}/erfgoedobjecten?typologie=beluiken", build(rows, label="beluik"))

    if "maria" in only:
        rows = []
        for q in ("typologie=niskapellen", "typologie=kastkapellen", "typologie=mariagrotten",
                  "typologie=Mariabeelden", "typologie=gevelniskapellen", "typologie=heiligenbeelden",
                  "tekst=Mariabeeld", "tekst=Onze-Lieve-Vrouwbeeld", "tekst=Onze-Lieve-Vrouwebeeld",
                  "tekst=Mariakapelletje", "tekst=muurkapelletje", "tekst=Madonna"):
            rows += fetch_csv(q)
        from collections import Counter
        print("typologies:", Counter(t.strip() for r in rows for t in r["typologie"].split(",")).most_common(40), file=sys.stderr)

        def is_mary(item):
            return bool(MARY.search(item["name"]) or MARY.search(item["text"]) or MARY.search(item["description"]))

        items = build(rows, keep=is_mary, label="maria")
        for it in items:
            it["mary"] = mary_snippet(it)
        write("data/maria.json", f"{BASE}/erfgoedobjecten?tekst=Mariabeeld", items)


if __name__ == "__main__":
    main()
