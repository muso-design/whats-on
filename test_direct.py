"""Checks for the venue registry and for reading gallery sites directly.

No network and no model: the model is stubbed, because what matters here is
what the code does with an answer, not whether a particular model gives a good
one. The grounding rules are the point.

Run: python test_direct.py
"""

import os
import tempfile

import direct
import llm
import venues

FAILURES = []


def check(name, got, want):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s: got %r, want %r" % (name, got, want))
        FAILURES.append(name)


print("venue identity")
check("'BQ' and 'BQ Berlin' are one venue",
      venues.venue_key("BQ", "Berlin") == venues.venue_key("BQ Berlin", "Berlin"),
      True)
check("'Galerie Crone' and 'Crone Berlin' are one venue",
      venues.venue_key("Galerie Crone", "Berlin")
      == venues.venue_key("Crone Berlin", "Berlin"), True)
check("'Galerie K' is not 'Galerie KUB'",
      venues.venue_key("Galerie K", "Leipzig")
      == venues.venue_key("Galerie KUB", "Leipzig"), False)
check("the same gallery in two cities stays two entries",
      venues.venue_key("REITER", "Leipzig") == venues.venue_key("REITER", "Berlin"),
      False)

print("\nmerging what discovery finds")
registry = {}
venues.merge_venues(registry, [
    {"name": "REITER Galerie", "city": "Leipzig", "source": "osm",
     "lat": 51.3, "lng": 12.3},
    {"name": "REITER", "city": "Leipzig", "source": "curated",
     "website": "https://example.invalid/", "always_include": True},
    {"name": "REITER", "city": "Leipzig", "source": "rundgang"},
])
check("one venue, not three", len(registry), 1)
entry = list(registry.values())[0]
check("every index that saw it is recorded", entry["sources"],
      ["curated", "osm", "rundgang"])
check("coordinates kept from OSM", entry["lat"], 51.3)
check("curation kept from the yaml", entry["always_include"], True)

registry2 = {}
venues.merge_venues(registry2, [{"name": "A", "city": "Leipzig",
                                 "website": "https://a.invalid", "source": "osm"},
                                {"name": "A", "city": "Leipzig",
                                 "website": "https://curated.invalid",
                                 "source": "curated"}])
check("a curated value overrides a discovered one",
      list(registry2.values())[0]["website"], "https://curated.invalid")

print("\nthe silence detector")
state = {"events": {"1": {"venue": "REITER", "city": "Leipzig"}}}
reg = {}
venues.merge_venues(reg, [
    {"name": "REITER", "city": "Leipzig", "source": "osm"},
    {"name": "Quiet Gallery", "city": "Leipzig", "source": "osm",
     "website": "https://quiet.invalid"},
])
quiet = venues.silent(reg, state, verbose=False)
check("only the venue we have heard nothing from", len(quiet), 1)
check("and it is the right one", quiet[0]["name"], "Quiet Gallery")

print("\npage text is stripped of machinery")
html = """<html><head><style>.x{color:red}</style><script>var a=1;</script></head>
<body><nav>Home</nav><h2>Carsten Goering</h2><p>Andoya</p>
<p>05.09.-17.10.2026</p></body></html>"""
text = direct.page_text(html)
check("script contents removed", "var a" in text, False)
check("style contents removed", "color:red" in text, False)
check("the actual content survives", "Carsten Goering" in text, True)
check("dates survive", "05.09.-17.10.2026" in text, True)


print("\nwhat the model is allowed to claim")
PAGE = ("REITER Galerie\nAusstellungen\nCarsten Goering\nAndoya\nLeipzig\n"
        "05.09.-17.10.2026\nWanda Stolle\nloose ends\nBerlin\n10.09.-07.11.2026")


def stub(answer):
    llm.ask = lambda prompt, schema, model=None, num_predict=300: answer


stub({"exhibitions": [
    {"artist": "Carsten Goering", "title": "Andoya", "city": "Leipzig",
     "start": "2026-09-05", "end": "2026-10-17", "opening": "2026-09-05 11:00"},
    # An artist who is nowhere on the page: the classic invention.
    {"artist": "Gerhard Richter", "title": "Invented Show", "city": "Leipzig",
     "start": "2026-09-05", "end": None, "opening": None},
    # A real show with a nonsense date.
    {"artist": "Wanda Stolle", "title": "loose ends", "city": "Berlin",
     "start": "not-a-date", "end": "2026-13-45", "opening": None},
]})
found = llm.exhibitions(PAGE, year_hint=2026)
titles = [f["title"] for f in found]
check("a show named on the page is kept", "Andoya" in titles, True)
check("an artist who is not on the page is dropped",
      "Invented Show" in titles, False)
check("a real show survives its bad dates", "loose ends" in titles, True)
bad = [f for f in found if f["title"] == "loose ends"][0]
check("an unparseable start becomes null", bad["start"], None)
check("an impossible date becomes null", bad["end"], None)
good = [f for f in found if f["title"] == "Andoya"][0]
check("a valid opening keeps its time", good["opening"], "2026-09-05T11:00")

print("\nturning answers into records")
venue = {"name": "REITER", "city": "Leipzig", "lat": 51.3, "lng": 12.3,
         "opening_hours": "Di-Sa 12-18"}
events = direct.to_events(found, venue, "https://example.invalid/", PAGE)
check("one record per surviving show", len(events), 2)
by_title = {e["title"]: e for e in events}
check("venue comes from the registry, not the model",
      by_title["Andoya"]["venue"], "REITER")
check("coordinates come from the registry", by_title["Andoya"]["lat"], 51.3)
check("opening hours come from the registry",
      by_title["Andoya"]["opening_hours"], "Di-Sa 12-18")
check("the page decides the city for a two-city gallery",
      by_title["loose ends"]["city"], "Berlin")
check("and the registry city is used otherwise",
      by_title["Andoya"]["city"], "Leipzig")
check("the source is recorded", by_title["Andoya"]["source"], "direct")
check("the model that read it is recorded",
      bool(by_title["Andoya"]["extracted_by"]), True)

print("\na city the page does not mention is not believed")
stub({"exhibitions": [{"artist": "Carsten Goering", "title": "Andoya",
                       "city": "Hamburg", "start": None, "end": None,
                       "opening": None}]})
events = direct.to_events(llm.exhibitions(PAGE, cache=None), venue,
                          "https://example.invalid/", PAGE)
check("falls back to the registry city", events[0]["city"], "Leipzig")

print("\nwith no model reachable nothing breaks")
llm._available = False
check("direct returns nothing rather than failing",
      direct.scrape_direct(registry={}, verbose=False), [])
llm._available = None

print("\n%d failure(s)" % len(FAILURES))
raise SystemExit(1 if FAILURES else 0)
