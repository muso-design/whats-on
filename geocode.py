"""Coordinates for venues that arrive without them.

index-berlin publishes latitude and longitude on every card, so most of Berlin
is already placed. Everything from rundgang-kunst arrives with a postal address
and nothing else, which is most of Leipzig - the one city where a map is worth
having.

Lookups go to Nominatim (OpenStreetMap), which is free and needs no key but
asks for one request per second and a User-Agent that identifies the caller.
Results are cached in venues.json by venue name, so each gallery is looked up
once and never again.
"""

import argparse
import json
import os
import sys
import time

import scraper

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "venues.json")
NOMINATIM = "https://nominatim.openstreetmap.org/search"
PAUSE = 1.1                   # Nominatim asks for no more than one call a second


def load_cache(path=CACHE_PATH):
    """Known venue coordinates, keyed by folded venue name."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_cache(cache, path=CACHE_PATH):
    """Write the venue cache back, so no gallery is looked up twice."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")


def _key(venue, city):
    return "%s|%s" % (scraper.fold(venue), scraper.fold(city))


def query(address, city=None):
    """One Nominatim lookup. Returns (lat, lng) or None."""
    params = {"q": address, "format": "json", "limit": 1,
              "countrycodes": "de", "addressdetails": 0}
    try:
        results = scraper.fetch(NOMINATIM, params=params, as_json=True)
    except Exception as exc:                                   # noqa: BLE001
        print("    ! geocode failed for %r: %s" % (address[:40], str(exc)[:70]))
        return None
    time.sleep(PAUSE)
    if not results:
        return None
    try:
        return float(results[0]["lat"]), float(results[0]["lon"])
    except (KeyError, ValueError, TypeError):
        return None


def _address_for(record):
    """The most specific string worth sending to a geocoder."""
    address = (record.get("address") or "").strip()
    city = (record.get("city") or "").strip()
    if address:
        # The address usually already carries the city; add it when it does not.
        if city and scraper.fold(city) not in scraper.fold(address):
            address = "%s, %s" % (address, city)
        return address
    venue = (record.get("venue") or "").strip()
    return ("%s, %s" % (venue, city)).strip(", ") if venue else ""


def locate(records, cache=None, allow_network=True, budget=0, verbose=True,
           cache_path=CACHE_PATH):
    """Fill in lat/lng on records that lack it, one lookup per venue.

    Cached after every hit: a run cut short by a timeout keeps its work, and
    Nominatim is slow enough by policy that this matters.
    """
    cache = load_cache(cache_path) if cache is None else cache
    tally = {}
    done = 0

    for record in records:
        if record.get("lat") and record.get("lng"):
            tally["already placed"] = tally.get("already placed", 0) + 1
            continue

        key = _key(record.get("venue"), record.get("city"))
        if key in cache:
            hit = cache[key]
            if hit:
                record["lat"], record["lng"] = hit[0], hit[1]
                tally["from cache"] = tally.get("from cache", 0) + 1
            else:
                tally["known unplaceable"] = tally.get("known unplaceable", 0) + 1
            continue

        address = _address_for(record)
        if not address:
            tally["no address"] = tally.get("no address", 0) + 1
            continue
        if not allow_network or (budget and done >= budget):
            tally["not looked up"] = tally.get("not looked up", 0) + 1
            continue

        found = query(address, record.get("city"))
        done += 1
        cache[key] = list(found) if found else None
        if cache_path:
            save_cache(cache, cache_path)
        if found:
            record["lat"], record["lng"] = found
            tally["geocoded"] = tally.get("geocoded", 0) + 1
        else:
            tally["not found"] = tally.get("not found", 0) + 1

    if verbose:
        print("  coordinates: %s"
              % ", ".join("%s=%d" % kv for kv in sorted(tally.items())))
    return cache, tally


def main(argv=None):
    """Backfill coordinates for every venue in the inventory."""
    parser = argparse.ArgumentParser(description="Geocode venues")
    parser.add_argument("--state", default=None)
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after this many lookups")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    import state as state_mod
    path = args.state or state_mod.STATE_PATH
    st = state_mod.load(path)
    records = list(st["events"].values())
    cache = load_cache()
    print("%d shows, %d venues already known" % (len(records), len(cache)))

    cache, _ = locate(records, cache, allow_network=not args.dry_run,
                      budget=args.limit)
    if args.dry_run:
        print("dry run: nothing written")
        return 0

    save_cache(cache)
    state_mod.save(st, path)
    placed = sum(1 for r in records if r.get("lat"))
    print("%d of %d shows now have coordinates" % (placed, len(records)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
