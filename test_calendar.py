"""Checks for the calendar links, the directions links and geocoding.

A calendar entry that is off by an hour, or lands on the wrong day, is worse
than none - you would turn up when the doors are shut. Geocoding is checked
without the network: what matters is the caching and the address it builds,
not what Nominatim happens to answer.

Run: python test_calendar.py
"""

import os
import tempfile
from urllib.parse import parse_qs, urlparse

import board
import geocode

FAILURES = []


def check(name, got, want):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s: got %r, want %r" % (name, got, want))
        FAILURES.append(name)


def params(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def rec(**kw):
    base = {"title": "Neue Plastiken", "venue": "Galerie Koenitz",
            "city": "Leipzig", "address": "Dittrichring 16, 04109 Leipzig",
            "status": "opening_soon"}
    base.update(kw)
    return base


print("a vernissage becomes a timed appointment")
url, label = board.calendar_url(rec(vernissage_datetime="2026-08-21T18:00"))
p = params(url)
check("label", label, "Add opening")
check("dates span the opening", p["dates"], "20260821T180000/20260821T200000")
check("local timezone, not UTC", p.get("ctz"), "Europe/Berlin")
check("title carries the venue", p["text"], "Neue Plastiken - Galerie Koenitz")
check("location is the street address",
      p["location"], "Galerie Koenitz, Dittrichring 16, 04109 Leipzig")

print("\na running show becomes a reminder on its last day")
url, label = board.calendar_url(rec(status="closing_soon",
                                    exhibition_start="2026-06-01",
                                    exhibition_end="2026-08-30"))
p = params(url)
check("label", label, "Add last day")
check("all-day, end exclusive", p["dates"], "20260830/20260831")
check("no timezone needed for an all-day entry", "ctz" in p, False)
check("title says what it is", p["text"].startswith("Last day:"), True)

print("\nan opening with no time is an all-day entry")
url, label = board.calendar_url(rec(exhibition_start="2026-09-04"))
p = params(url)
check("all-day on the start date", p["dates"], "20260904/20260905")
check("label", label, "Add opening")

print("\nnothing to schedule yields no link")
check("no dates at all", board.calendar_url(rec())[0], "")

print("\nthe entry carries what you need on the night")
url, _ = board.calendar_url(rec(
    vernissage_datetime="2026-08-21T18:00",
    artists="Uta Schlenzig",
    description_en="New works in bronze and plaster.",
    opening_hours="Mo. bis Fr.: 10 - 18 Uhr",
    source_url="https://example.invalid/show"))
details = params(url)["details"]
for part in ["Uta Schlenzig", "bronze and plaster",
             "Mo. bis Fr.", "https://example.invalid/show"]:
    check("details include %r" % part[:22], part in details, True)

print("\nEnglish is preferred over the original in the entry")
url, _ = board.calendar_url(rec(
    vernissage_datetime="2026-08-21T18:00",
    raw_description="Deutsche Beschreibung.",
    description_en="English description."))
details = params(url)["details"]
check("English used", "English description." in details, True)
check("German not duplicated", "Deutsche Beschreibung." in details, False)

print("\ndirections")
check("by coordinates when known",
      board.directions_url(rec(lat=51.34, lng=12.37)),
      "https://www.google.com/maps/search/?api=1&query=51.34,12.37")
check("by name when not",
      "Dittrichring" in board.directions_url(rec()), True)
check("nothing at all", board.directions_url(
    {"venue": "", "address": "", "city": ""}), "")

print("\ngeocoding builds a sensible query")
check("address already naming the city is left alone",
      geocode._address_for(rec()), "Dittrichring 16, 04109 Leipzig")
check("city appended when the address omits it",
      geocode._address_for(rec(address="Dittrichring 16")),
      "Dittrichring 16, Leipzig")
check("falls back to venue and city",
      geocode._address_for(rec(address=None)), "Galerie Koenitz, Leipzig")

print("\ngeocoding caches by venue and never asks twice")
calls = []


def fake_query(address, city=None):
    calls.append(address)
    return (51.34, 12.37)


geocode.query = fake_query
path = os.path.join(tempfile.mkdtemp(), "venues.json")
records = [rec(), rec(), rec(venue="Galerie KUB", address="Kantstr. 1, Leipzig")]
cache, tally = geocode.locate(records, cache={}, cache_path=path, verbose=False)
check("one lookup per venue, not per show", len(calls), 2)
check("both venues placed", tally.get("geocoded"), 2)
check("the repeat came from the cache", tally.get("from cache"), 1)
check("coordinates applied to the records",
      all(r.get("lat") for r in records), True)
check("cache written to disk", len(geocode.load_cache(path)), 2)

print("\na venue that cannot be found is remembered as such")
geocode.query = lambda address, city=None: None
records = [rec(venue="Nowhere", address="asdfgh")]
cache, tally = geocode.locate(records, cache={}, cache_path=path, verbose=False)
check("recorded as not found", tally.get("not found"), 1)
cache2, tally2 = geocode.locate([rec(venue="Nowhere", address="asdfgh")],
                                cache=cache, cache_path=path, verbose=False)
check("and not retried next run", tally2.get("known unplaceable"), 1)

print("\nthe budget caps a single run")
geocode.query = fake_query
calls.clear()
records = [rec(venue="V%d" % i, address="Str %d, Leipzig" % i) for i in range(9)]
cache, tally = geocode.locate(records, cache={}, cache_path=None,
                              budget=4, verbose=False)
check("stopped at the budget", len(calls), 4)
check("the rest wait for next time", tally.get("not looked up"), 5)

print("\n%d failure(s)" % len(FAILURES))
raise SystemExit(1 if FAILURES else 0)
