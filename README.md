# Walking Gent

A mobile web game for exploring Ghent on foot, in the spirit of an "explorer" achievement: walk every street inside the small ring (R40).

- **Streets** get coloured in once you've walked most of them (grey = not yet, amber = started, green = explored).
- **Neighbourhoods** (the city's statistical sectors) get filled in once every street in them is walked.
- **Achievements** for 1, 10, 25, 50, 100 and 250 streets, your first and fifth neighbourhood, 25/50/75%, and *Explorer of Ghent* for every street.
- Your GPS trail is recorded while the page is open with location on, and saved on your phone. Walks from other apps can be imported as GPX. Progress can be backed up and restored as a file.

The heritage layers (sights, beluiken, Mary statues, hidden gems) and shops are still there under Layers, and Search finds streets and neighbourhoods too.

Open `index.html` over HTTPS (for example via GitHub Pages) so the phone can share its location.

## Data

- `data/streets.json`: every named walkable street inside the ring from OpenStreetMap, cut into pieces per neighbourhood, with sample points every 15 m for GPS matching. Built by `scripts/fetch_streets.py` (GitHub Action *Update streets data*, runs on changes to the script or by hand). Neighbourhoods come from Stad Gent's open data (*statistische-sectoren-gent*).
- `data/ring.json`: the R40 ring, the edge of the map.
- `data/beluiken.json`, `maria.json`, `gems.json`, `nightshops.json`, `geekstores.json`: built by `scripts/fetch_beluiken.py`.
