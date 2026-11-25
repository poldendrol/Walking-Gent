# Ghent Explorer (Walking Gent)

A mobile web game for exploring Ghent on foot, in the spirit of an "explorer" achievement: walk every street inside the small ring (R40), and collect the city's sights, beluiken, Mary statues and hidden gems along the way.

Three tabs: **Explore** (your progress, neighbourhoods, achievements), **Collection** (what you've discovered, and what the map shows) and **Walk** (suggested walks past unexplored streets, or plan your own with guidance).

- **Streets** get coloured in once you've walked most of them (grey = not yet, amber = started, green = explored).
- **Neighbourhoods** (the city's statistical sectors) get filled in once every street in them is walked.
- **Discoveries**: walk within 30 m of a sight, beluik, Mary statue or hidden gem to add it to your collection. Undiscovered places show as grey ? pins (from zoom 16).
- **Achievements** for streets, neighbourhoods, percentages and discoveries, ending with *Explorer of Ghent* for every street.
- Your GPS trail is recorded while the page is open with location on, and saved on your phone. Past walks can be imported: GPX files, or Google location history (the Timeline export from Google Maps on your phone, or Takeout's Records.json / Semantic Location History). Only walking counts. Progress can be backed up and restored as a file.

Night shops and geek stores are optional map layers in the Collection tab. Search finds streets, neighbourhoods and places.

Open `index.html` over HTTPS (for example via GitHub Pages) so the phone can share its location.

## Data

- `data/streets.json`: every named walkable street inside the ring from OpenStreetMap, cut into pieces per neighbourhood, with sample points every 15 m for GPS matching. Built by `scripts/fetch_streets.py` (GitHub Action *Update streets data*, runs on changes to the script or by hand). Neighbourhoods come from Stad Gent's open data (*statistische-sectoren-gent*).
- `data/ring.json`: the R40 ring, the edge of the map.
- `data/beluiken.json`, `maria.json`, `gems.json`, `nightshops.json`, `geekstores.json`: built by `scripts/fetch_beluiken.py`.
