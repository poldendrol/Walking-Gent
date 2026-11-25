"""Build data/streets.json: every street inside Ghent's small ring (R40), grouped into neighbourhoods.

Streets come from OpenStreetMap (Overpass). Neighbourhoods are tried in this order:
  1. the city's statistical sectors from data.stad.gent (official, cover everything, no gaps),
  2. neighbourhood/quarter polygons from OpenStreetMap,
  3. Voronoi cells around OpenStreetMap neighbourhood points (or a built-in list of quarters).

Each street is cut where it crosses a neighbourhood border, so a neighbourhood is complete
when every street piece inside it is walked. The app matches GPS fixes against the sample
points ("pts", every ~15 m) of each piece.

Run:  python scripts/fetch_streets.py            (needs shapely and pyproj)
"""
import json, math, os, re, sys, time, urllib.parse, urllib.request
from collections import defaultdict

from pyproj import Transformer
from shapely.geometry import LineString, MultiLineString, MultiPoint, Point, Polygon, shape, mapping
from shapely.ops import linemerge, unary_union, voronoi_diagram
from shapely.ops import transform  # noqa: E402  (deprecated in shapely 2.1 but still fine)
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

UA = "walking-gent-builder/1.0 (+https://github.com/poldendrol/Walking-Gent)"
OUT = "data/streets.json"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
STEP = 15            # metres between sample points
MIN_PIECE = 12       # drop street bits shorter than this after clipping
SMALL_PIECE = 45     # a piece this short spilling into another neighbourhood joins the street's main neighbourhood

to_m = Transformer.from_crs("EPSG:4326", "EPSG:31370", always_xy=True).transform
to_ll = Transformer.from_crs("EPSG:31370", "EPSG:4326", always_xy=True).transform

WALKABLE = ("residential|living_street|pedestrian|primary|secondary|tertiary|unclassified|service|footway|path|steps|"
            "cycleway|track|primary_link|secondary_link|tertiary_link|road")

# Fallback seeds if no neighbourhood data can be found (approximate centres of well-known quarters)
SEEDS = [
    ("Patershol", 51.0581, 3.7238), ("Prinsenhof", 51.0592, 3.7163), ("Gravensteen & Veerleplein", 51.0565, 3.7200),
    ("Korenmarkt & Graslei", 51.0545, 3.7205), ("Sint-Baafs & Kouter", 51.0518, 3.7255), ("Sint-Jacobs", 51.0565, 3.7275),
    ("Oude Beestenmarkt", 51.0585, 3.7330), ("Portus Ganda", 51.0520, 3.7345), ("Sint-Anna", 51.0480, 3.7380),
    ("Zuid", 51.0465, 3.7300), ("Ketelvest & Lieven Bauwens", 51.0490, 3.7215), ("Sint-Michiels", 51.0525, 3.7145),
    ("Bijloke", 51.0475, 3.7110), ("Sint-Pieters & Blandijn", 51.0430, 3.7240), ("Overpoort & Universiteit", 51.0420, 3.7280),
    ("Muinkpark & Visserij", 51.0420, 3.7355), ("Citadelpark", 51.0380, 3.7200), ("Rabot side", 51.0610, 3.7110),
    ("Dampoort side", 51.0570, 3.7400), ("Kunstenwijk", 51.0450, 3.7170),
]


def post(url, data, timeout=200):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode()


def get(url, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8-sig")


def overpass(q):
    data = urllib.parse.urlencode({"data": q}).encode()
    for attempt in range(6):
        url = OVERPASS[attempt % len(OVERPASS)]
        try:
            return json.loads(post(url, data))["elements"]
        except Exception as e:
            print(f"overpass attempt {attempt + 1} ({url}) failed: {e}", file=sys.stderr)
            time.sleep(15 * (attempt + 1))
    raise RuntimeError("overpass unavailable")


def ll2m(geom):
    return transform(to_m, geom)


def m2ll(geom):
    return transform(to_ll, geom)


def pretty(name):
    """Statistical sector names are often upper case: 'SINT-JACOBS' -> 'Sint-Jacobs'."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    if name and name.upper() == name:
        name = "-".join(" ".join(w.capitalize() for w in part.split(" ")) for part in name.lower().split("-"))
        name = re.sub(r"\b(En|Van|De|Der|Den|Het|Ten|Ter)\b", lambda m: m.group(1).lower(), name)
        name = name[0].upper() + name[1:]
    return name


# ---------------------------------------------------------------- ring
def load_ring():
    with open("data/ring.json") as f:
        ring = json.load(f)["ring"]          # [[lat, lng], ...]
    poly = Polygon([(lng, lat) for lat, lng in ring])
    if not poly.is_valid:
        poly = poly.buffer(0)
    return ring, poly


# ---------------------------------------------------------------- neighbourhoods
def hoods_from_stad_gent(ring_m):
    """Statistical sectors from the city's open data portal (Opendatasoft)."""
    base = "https://data.stad.gent/api/explore/v2.1/catalog/datasets"
    ids = []
    for term in ("statistische sectoren", "statistische sector", "sectoren"):
        try:
            j = json.loads(get(f"{base}?where=" + urllib.parse.quote(f'search("{term}")') + "&limit=30&select=dataset_id"))
            for r in j.get("results", []):
                if r["dataset_id"] not in ids:
                    ids.append(r["dataset_id"])
        except Exception as e:
            print("stad.gent catalog search failed:", e, file=sys.stderr)
    print("stad.gent datasets:", ids, file=sys.stderr)
    ids.sort(key=lambda d: (0 if re.search(r"statistische-sector", d) else 1, len(d)))
    for ds in ids:
        if not re.search(r"sector", ds):
            continue
        try:
            gj = json.loads(get(f"{base}/{ds}/exports/geojson", timeout=200))
        except Exception as e:
            print(f"stad.gent {ds} export failed: {e}", file=sys.stderr)
            continue
        feats = gj.get("features", [])
        print(f"stad.gent {ds}: {len(feats)} features; props: {list((feats[0].get('properties') or {}).keys()) if feats else []}", file=sys.stderr)
        out = []
        for ft in feats:
            p = ft.get("properties") or {}
            name = None
            for k in ("sector_naam", "naam_sector", "sectornaam", "tx_sector_descr_nl", "naam", "name", "sector", "omschrijving"):
                v = p.get(k)
                if isinstance(v, str) and v.strip() and not re.fullmatch(r"[0-9A-Z]{5,}", v.strip()):
                    name = v
                    break
            if not name:
                name = next((v for k, v in p.items() if isinstance(v, str) and re.search(r"naam|name|descr", k, re.I) and v.strip()), None)
            if not name or not ft.get("geometry"):
                continue
            try:
                g = ll2m(shape(ft["geometry"])).buffer(0)
            except Exception:
                continue
            inter = g.intersection(ring_m)
            if inter.area > 0.35 * g.area or inter.area > 60000:
                out.append((pretty(name), inter))
        if len(out) >= 6:
            return out, f"Stad Gent open data ({ds})"
    return None, None


def hoods_from_osm(ring_ll, ring_m):
    minx, miny, maxx, maxy = ring_ll.bounds
    bbox = f"{miny},{minx},{maxy},{maxx}"
    els = overpass(f'[out:json][timeout:120];(way["place"~"^(neighbourhood|quarter|suburb)$"]({bbox});'
                   f'rel["place"~"^(neighbourhood|quarter|suburb)$"]({bbox});'
                   f'node["place"~"^(neighbourhood|quarter|suburb)$"]({bbox}););out geom tags;')
    polys, points = [], []
    for e in els:
        name = (e.get("tags") or {}).get("name")
        if not name:
            continue
        if e["type"] == "node":
            pt = Point(e["lon"], e["lat"])
            if ring_ll.contains(pt):
                points.append((name, ll2m(pt)))
            continue
        try:
            if e["type"] == "way":
                g = Polygon([(c["lon"], c["lat"]) for c in e["geometry"]])
            else:
                lines = [LineString([(c["lon"], c["lat"]) for c in m["geometry"]]) for m in e.get("members", [])
                         if m.get("role") in ("outer", "") and m.get("geometry")]
                from shapely.ops import polygonize
                g = unary_union(list(polygonize(linemerge(lines))))
            g = ll2m(g).buffer(0)
        except Exception:
            continue
        inter = g.intersection(ring_m)
        if not inter.is_empty and inter.area > 0.35 * g.area and inter.area < 0.6 * ring_m.area:
            polys.append((name, inter))
    print(f"osm places: {len(polys)} polygons, {len(points)} points inside the ring: "
          f"{[n for n, _ in polys]} / {[n for n, _ in points]}", file=sys.stderr)
    return polys, points


def voronoi(seeds, ring_m):
    pts = MultiPoint([p for _, p in seeds])
    cells = voronoi_diagram(pts, envelope=ring_m.buffer(2000))
    out = []
    for name, p in seeds:
        cell = next((c for c in cells.geoms if c.contains(p)), None)
        if cell is not None:
            out.append((name, cell.intersection(ring_m)))
    return out


def tidy_hoods(hoods, ring_m):
    """Merge duplicate names, make polygons disjoint, and fill gaps by giving them to the nearest neighbourhood."""
    by = defaultdict(list)
    for n, g in hoods:
        by[n].append(g)
    merged = [(n, unary_union(gs).buffer(0)) for n, gs in by.items()]
    merged.sort(key=lambda x: x[1].area)            # small ones first win overlaps
    taken, out = None, []
    for n, g in merged:
        if taken is not None:
            g = g.difference(taken)
        if g.area < 2000:
            continue
        out.append([n, g])
        taken = g if taken is None else unary_union([taken, g])
    gap = ring_m.difference(taken) if taken is not None else ring_m
    for piece in getattr(gap, "geoms", [gap]):
        if piece.area < 1:
            continue
        best = max(out, key=lambda h: h[1].buffer(1).intersection(piece.buffer(1)).area or -h[1].distance(piece))
        best[1] = unary_union([best[1], piece]).buffer(0)
    return [(n, g) for n, g in out]


# ---------------------------------------------------------------- streets
def fetch_streets(ring_ll):
    coords = " ".join(f"{lat:.6f} {lng:.6f}" for lng, lat in list(ring_ll.buffer(0.0008).exterior.coords)[::2])
    q = (f'[out:json][timeout:180];way["highway"~"^({WALKABLE})$"]["name"](poly:"{coords}");out geom tags;')
    els = overpass(q)
    print(f"overpass: {len(els)} named ways", file=sys.stderr)
    return els


def walkable(t):
    if t.get("access") in ("private", "no") and t.get("foot") not in ("yes", "designated", "permissive"):
        return False
    if t.get("foot") == "no" or t.get("area:highway"):
        return False
    if t.get("highway") == "service" and t.get("service") in ("parking_aisle", "drive-through"):
        return False
    if t.get("tunnel") in ("yes", "building_passage") and t.get("highway") in ("primary", "secondary", "trunk"):
        return False
    return True


def sample(line, step=STEP):
    n = max(1, round(line.length / step))
    return [line.interpolate(i / n, normalized=True) for i in range(n + 1)] if line.length >= step else [line.interpolate(0.5, normalized=True)]


def r5(x):
    return round(x, 5)


def build_pieces(by_name, hoods, clip_m):
    pieces = []
    for name, lines in by_name.items():
        u = unary_union(lines)
        geom = (linemerge(u) if u.geom_type == "MultiLineString" else u).intersection(clip_m)
        if geom.is_empty or geom.length < MIN_PIECE:
            continue
        per_hood = []
        for hn, hg in hoods:
            part = geom.intersection(hg)
            parts = [p for p in getattr(part, "geoms", [part]) if isinstance(p, LineString) and p.length > 0.5]
            if parts:
                per_hood.append([hn, parts, sum(p.length for p in parts)])
        if not per_hood:
            continue
        per_hood.sort(key=lambda x: -x[2])
        main_h = per_hood[0]
        for ph in per_hood[1:]:
            if ph[2] < SMALL_PIECE:                    # tiny spill-over: belongs to the main neighbourhood
                main_h[1] += ph[1]
                main_h[2] += ph[2]
                ph[2] = 0
        for hn, parts, length in per_hood:
            if length < MIN_PIECE:
                continue
            merged = linemerge(MultiLineString(parts)) if len(parts) > 1 else parts[0]
            lines = [l for l in getattr(merged, "geoms", [merged]) if l.length > 0.5]
            pts = []
            for l in lines:
                for p in sample(l):
                    q = to_ll(p.x, p.y)
                    pts.append([r5(q[1]), r5(q[0])])
            # drop duplicate sample points where lines meet
            seen, uniq = set(), []
            for p in pts:
                if (p[0], p[1]) not in seen:
                    seen.add((p[0], p[1]))
                    uniq.append(p)
            lls = [[[r5(c[1]), r5(c[0])] for c in m2ll(l.simplify(1.5)).coords] for l in lines]
            pieces.append({"name": name, "hood": hn, "len": round(length), "lines": lls, "pts": uniq})

    return pieces


def main():
    ring, ring_ll = load_ring()
    ring_m = ll2m(ring_ll)
    clip_m = ring_m.buffer(8)

    # neighbourhoods
    hoods, source = None, None
    try:
        hoods, source = hoods_from_stad_gent(ring_m)
    except Exception as e:
        print("stad.gent failed:", e, file=sys.stderr)
    osm_polys, osm_points = [], []
    if not hoods:
        try:
            osm_polys, osm_points = hoods_from_osm(ring_ll, ring_m)
        except Exception as e:
            print("osm places failed:", e, file=sys.stderr)
        covered = sum(g.area for _, g in osm_polys) / ring_m.area
        if len(osm_polys) >= 6 and covered > 0.6:
            hoods, source = osm_polys, "OpenStreetMap neighbourhood areas"
        elif len(osm_points) + len(osm_polys) >= 8:
            seeds = osm_points + [(n, g.representative_point()) for n, g in osm_polys]
            hoods, source = voronoi(seeds, ring_m), "OpenStreetMap neighbourhood names (nearest centre)"
        else:
            seeds = [(n, ll2m(Point(lng, lat))) for n, lat, lng in SEEDS if ring_ll.contains(Point(lng, lat))]
            hoods, source = voronoi(seeds, ring_m), "Built-in list of quarters (nearest centre)"
    hoods = tidy_hoods(hoods, ring_m)
    print(f"neighbourhoods ({source}): {len(hoods)}", file=sys.stderr)

    # streets: merge all named ways into one geometry per name
    by_name = defaultdict(list)
    areas = defaultdict(list)
    for e in fetch_streets(ring_ll):
        t = e.get("tags", {})
        if not walkable(t) or not e.get("geometry"):
            continue
        name = re.sub(r"\s+", " ", t["name"]).strip()
        cs = [(c["lon"], c["lat"]) for c in e["geometry"]]
        if len(cs) < 2:
            continue
        g = ll2m(LineString(cs))
        if t.get("area") == "yes" and cs[0] == cs[-1]:
            areas[name].append(Polygon(g.coords))
        else:
            by_name[name].append(g)
    for name, polys in areas.items():           # squares mapped only as areas: walk their outline
        if name not in by_name:
            by_name[name] = [LineString(p.exterior.coords) for p in polys]

    MIN_HOOD = 1500      # metres of street: smaller neighbourhood slivers join a neighbour
    while True:
        pieces = build_pieces(by_name, hoods, clip_m)
        lens = defaultdict(float)
        for pc in pieces:
            lens[pc["hood"]] += pc["len"]
        small = min(hoods, key=lambda h: lens.get(h[0], 0))
        if lens.get(small[0], 0) >= MIN_HOOD or len(hoods) < 3:
            break
        others = [h for h in hoods if h is not small]
        best = max(others, key=lambda h: h[1].buffer(3).intersection(small[1].buffer(3)).area)
        print(f"merging {small[0]} ({lens.get(small[0], 0):.0f} m of street) into {best[0]}", file=sys.stderr)
        hoods = [(n, unary_union([g, small[1]]).buffer(0.5).buffer(-0.5) if n == best[0] else g) for n, g in others]

    # stable, readable ids
    hood_names = sorted({p["hood"] for p in pieces})
    slug = lambda s: re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    hid = {n: slug(n) or f"h{i}" for i, n in enumerate(hood_names)}
    out_hoods = []
    for n, g in hoods:
        if n not in hid:
            continue
        gl = m2ll(g.simplify(4))
        polys = [gl] if gl.geom_type == "Polygon" else [p for p in gl.geoms if p.geom_type == "Polygon"]
        c = m2ll(g.representative_point())
        out_hoods.append({"id": hid[n], "name": n, "area": round(g.area),
                          "center": [r5(c.y), r5(c.x)],
                          "poly": [[[r5(y), r5(x)] for x, y in p.exterior.coords] for p in polys if p.area > 0]})
    out_hoods.sort(key=lambda h: h["name"])
    pieces.sort(key=lambda p: (p["name"], p["hood"]))
    seen_ids = set()
    for p in pieces:
        p["hood"] = hid[p["hood"]]
        i = slug(p["name"]) + "@" + p["hood"]
        while i in seen_ids:
            i += "+"
        seen_ids.add(i)
        p["id"] = i
    streets = [{"id": p["id"], "name": p["name"], "hood": p["hood"], "len": p["len"], "lines": p["lines"], "pts": p["pts"]} for p in pieces]

    out = {"source": "Streets: OpenStreetMap contributors (ODbL). Neighbourhoods: " + source,
           "generated": time.strftime("%Y-%m-%d"), "step": STEP,
           "hoods": out_hoods, "streets": streets}
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"), ensure_ascii=False)
    names = {s["name"] for s in streets}
    print(f"wrote {OUT}: {len(names)} streets in {len(streets)} pieces, {len(out_hoods)} neighbourhoods, "
          f"{sum(s['len'] for s in streets) / 1000:.1f} km, {sum(len(s['pts']) for s in streets)} sample points, "
          f"{os.path.getsize(OUT) / 1024:.0f} KB", file=sys.stderr)
    for h in out_hoods:
        ss = [s for s in streets if s["hood"] == h["id"]]
        print(f"  {h['name']}: {len(ss)} pieces, {sum(s['len'] for s in ss) / 1000:.1f} km", file=sys.stderr)


if __name__ == "__main__":
    main()
