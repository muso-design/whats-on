"""The venue registry: who exists, independent of who happens to list them.

The four listing sources only know about galleries that submit to them. REITER
Galerie shows in Leipzig and Berlin, publishes its programme plainly on its own
site, and appears in none of them - rundgang does not even carry the venue.
Aggregator coverage has a shape, and things outside that shape are invisible no
matter how the parsers are tuned.

So this collects venues rather than events, from indexes that have no opinion
about art listings:

  OpenStreetMap   101 art venues in Leipzig alone, every one with coordinates
  rundgang        its own location pages, which outlive its event listings
  index-berlin    a venue id on every card, which we were discarding
  venues.yaml     the ones you care about, edited by hand

Venues barely change, so this is cheap to keep current. What it produces is a
list of websites worth reading - which is what turns "who lists this show" into
"who has a website", a much larger set.
"""

import argparse
import json
import os
import re
import sys
import time

import scoring
import scraper

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY_PATH = os.path.join(HERE, "venue_registry.json")
CURATED_PATH = os.path.join(HERE, "venues.yaml")

# The main instance is often busy. These are the public mirrors, tried in turn.
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
OVERPASS_PAUSE = 3.0          # a shared free service; one query per city, rarely

# Overpass answers 406 to the scraper's browser-ish headers. It wants a plain
# identifying User-Agent and nothing else, so it gets its own session.
_overpass = None


def _overpass_session():
    global _overpass
    if _overpass is None:
        import requests
        _overpass = requests.Session()
        _overpass.headers.update({
            "User-Agent": "ExhibitionDigest/1.0 (personal art listing tool)"})
    return _overpass

# OSM tags that mean "art is shown here".
OSM_TAGS = [
    ('node["tourism"="gallery"]', 'way["tourism"="gallery"]'),
    ('node["shop"="art"]', 'way["shop"="art"]'),
    ('node["amenity"="arts_centre"]', 'way["amenity"="arts_centre"]'),
]

# Leipzig is a district, Berlin a city-state; the admin level differs.
CITY_AREAS = {
    "Leipzig": ("Leipzig", 6),
    "Halle": ("Halle (Saale)", 6),
    "Dresden": ("Dresden", 6),
    "Chemnitz": ("Chemnitz", 6),
    "Berlin": ("Berlin", 4),
}


def venue_key(name, city):
    """A stable key that survives 'BQ' vs 'BQ Berlin'."""
    tokens = scoring._venue_tokens(name)
    if not tokens:
        tokens = set(scraper.fold(name).split())
    return "%s|%s" % (" ".join(sorted(tokens)), scraper.fold(city))


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

def discover_osm(city, verbose=True):
    """Art venues OpenStreetMap knows about, with coordinates and websites."""
    area, level = CITY_AREAS.get(city, (city, 6))
    parts = "\n  ".join("%s(area.a);\n  %s(area.a);" % pair for pair in OSM_TAGS)
    query = ('[out:json][timeout:90];\n'
             'area["name"="%s"]["admin_level"="%d"]->.a;\n(\n  %s\n);\nout center tags;'
             % (area, level, parts))
    elements = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            response = _overpass_session().post(endpoint, data={"data": query},
                                                timeout=180)
            response.raise_for_status()
            elements = response.json().get("elements", [])
            break
        except Exception as exc:                               # noqa: BLE001
            print("  . %s busy for %s (%s)"
                  % (endpoint.split("/")[2], city, str(exc)[:40]))
            time.sleep(OVERPASS_PAUSE)
    if elements is None:
        print("  ! every overpass mirror refused %s; keeping what we have" % city)
        return []
    time.sleep(OVERPASS_PAUSE)

    out = []
    for element in elements:
        tags = element.get("tags") or {}
        name = scraper.clean(tags.get("name"))
        if not name:
            continue                      # an unnamed point is no use to us
        centre = element.get("center") or {}
        out.append({
            "name": name,
            "city": city,
            "website": scraper.https_url(
                tags.get("website") or tags.get("contact:website")),
            "lat": element.get("lat") or centre.get("lat"),
            "lng": element.get("lon") or centre.get("lon"),
            "source": "osm",
            "osm_id": "%s/%s" % (element.get("type"), element.get("id")),
        })
    if verbose:
        print("  osm/%s: %d venues, %d with a website"
              % (city, len(out), sum(1 for v in out if v["website"])))
    return out


def discover_rundgang(region_slug, city, verbose=True):
    """Venues from rundgang's location pages, which outlast its event listings."""
    try:
        html = scraper.fetch(scraper.RUNDGANG_BASE.format(slug=region_slug))
    except scraper.FetchError as exc:
        print("  ! rundgang/%s failed: %s" % (region_slug, exc))
        return []
    slugs = sorted(set(re.findall(
        r'https://www\.rundgang-kunst\.de/locations/([a-z0-9-]+)/', html)))
    out = []
    for slug in slugs:
        out.append({
            "name": slug.replace("-", " ").title(),   # refined by the detail pass
            "city": city,
            "website": None,
            "source": "rundgang",
            "venue_slug": slug,
        })
    if verbose:
        print("  rundgang/%s: %d venues" % (region_slug, len(out)))
    return out


def rundgang_details(venue, verbose=False):
    """Fill a rundgang venue in from its location page: real name, site, hours."""
    url = "https://www.rundgang-kunst.de/locations/%s/" % venue["venue_slug"]
    try:
        page = scraper.soup(scraper.fetch(url))
    except scraper.FetchError:
        return venue
    name = page.select_one("p.location_adress strong")
    if name:
        venue["name"] = scraper.clean(name.get_text(" "))
    contact = page.select_one("p.location_contact")
    if contact:
        for link in contact.select("a[href]"):
            href = link["href"]
            if href.startswith("http") and not venue.get("website"):
                venue["website"] = scraper.https_url(href)
            elif href.startswith("mailto:") and not venue.get("email"):
                venue["email"] = href[7:]
    hours = page.select_one("p.openingtimes")
    if hours:
        venue["opening_hours"] = scraper.clean(hours.get_text(" ")) or None
    venue["listing_url"] = url
    return venue


def discover_index_berlin(verbose=True):
    """Berlin venues, from the venue id index-berlin puts on every card."""
    try:
        html = scraper.fetch(scraper.INDEX_BERLIN_URL)
    except scraper.FetchError as exc:
        print("  ! index-berlin failed: %s" % exc)
        return []
    page = scraper.soup(html)
    seen, out = set(), []
    for card in page.select("article.event"):
        node = card.select_one(".event__location span")
        href = card.get("data-venue") or ""
        if not node:
            continue
        name = scraper.clean(node.get_text(" "))
        if not name or name in seen:
            continue
        seen.add(name)
        entry = {"name": name, "city": "Berlin", "website": None,
                 "source": "index-berlin"}
        if href:
            entry["venue_slug"] = href.rstrip("/").rsplit("/", 1)[-1]
            entry["listing_url"] = "https://www.indexberlin.de" + href
        for attr, key in (("data-latitude", "lat"), ("data-longitude", "lng")):
            raw = card.get(attr)
            if raw:
                try:
                    entry[key] = float(raw)
                except ValueError:
                    pass
        out.append(entry)
    if verbose:
        print("  index-berlin: %d venues" % len(out))
    return out


# --------------------------------------------------------------------------
# curation
# --------------------------------------------------------------------------

def load_curated(path=CURATED_PATH):
    """The venues you care about, and how to treat them."""
    if not os.path.exists(path):
        return []
    import yaml
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    out = []
    for entry in data.get("venues") or []:
        entry = dict(entry)
        entry["source"] = "curated"
        out.append(entry)
    return out


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------

def load_registry(path=REGISTRY_PATH):
    """Every venue we know exists, however we came to know it."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_registry(registry, path=REGISTRY_PATH):
    """Write the registry back. It changes slowly and is worth committing."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(registry, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")


def merge_venues(registry, found):
    """Fold discovered venues in, keeping the best value for every field."""
    added = 0
    for venue in found:
        key = venue_key(venue.get("name"), venue.get("city"))
        if not key.strip("|"):
            continue
        entry = registry.get(key)
        if not entry:
            entry = {"key": key, "sources": []}
            registry[key] = entry
            added += 1
        for field, value in venue.items():
            if field == "source":
                continue
            # Curated values win; otherwise first non-empty wins.
            if value in (None, "", []):
                continue
            if venue.get("source") == "curated" or not entry.get(field):
                entry[field] = value
        source = venue.get("source")
        if source and source not in entry["sources"]:
            entry["sources"] = sorted(entry["sources"] + [source])
    return added


def build(cities=None, detail=True, verbose=True):
    """Discover venues across every index and write the registry."""
    cities = cities or list(scraper.RUNDGANG_REGIONS.values())
    registry = load_registry()
    before = len(registry)

    for city in cities:
        merge_venues(registry, discover_osm(city, verbose))
    for slug, city in scraper.RUNDGANG_REGIONS.items():
        if city in cities:
            merge_venues(registry, discover_rundgang(slug, city, verbose))
    if "Berlin" in cities:
        merge_venues(registry, discover_index_berlin(verbose))
    merge_venues(registry, load_curated())

    if detail:
        pending = [v for v in registry.values()
                   if v.get("venue_slug") and "rundgang" in v.get("sources", [])
                   and not v.get("listing_url")]
        if verbose and pending:
            print("  reading %d rundgang venue pages" % len(pending))
        for venue in pending:
            rundgang_details(venue)
            merge_venues(registry, [dict(venue, source="rundgang")])

    save_registry(registry)
    if verbose:
        withsite = sum(1 for v in registry.values() if v.get("website"))
        print("registry: %d venues (%d new), %d with a website"
              % (len(registry), len(registry) - before, withsite))
    return registry


def readable(registry):
    """Venues with a website we could actually read a programme from."""
    return [v for v in registry.values() if v.get("website")]


def silent(registry, state, verbose=True):
    """Venues we know exist but have never heard a show from.

    Coverage stops being a feeling and becomes a list. Some of these are
    genuinely quiet; the rest are the blind spots.
    """
    heard = set()
    for record in state.get("events", {}).values():
        if record.get("venue"):
            heard.add(venue_key(record["venue"], record.get("city")))
    quiet = [v for k, v in registry.items() if k not in heard]
    if verbose:
        print("silent: %d of %d venues have produced no show (%d have a website)"
              % (len(quiet), len(registry),
                 sum(1 for v in quiet if v.get("website"))))
    return quiet


def main(argv=None):
    """Rebuild the venue registry and report what it can see."""
    parser = argparse.ArgumentParser(description="Discover art venues")
    parser.add_argument("--cities", nargs="*", default=None)
    parser.add_argument("--no-detail", action="store_true",
                        help="skip reading rundgang venue pages")
    parser.add_argument("--silent", action="store_true",
                        help="list venues that have produced no shows")
    args = parser.parse_args(argv)

    registry = build(args.cities, detail=not args.no_detail)
    if args.silent:
        import state as state_mod
        quiet = silent(registry, state_mod.load())
        for venue in sorted(quiet, key=lambda v: (v.get("city") or "",
                                                  v.get("name") or ""))[:40]:
            if venue.get("website"):
                print("   %-34s %-9s %s" % (venue["name"][:33],
                                            venue.get("city", "")[:8],
                                            venue["website"][:44]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
