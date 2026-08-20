"""Feed every public function the inputs real scrapes eventually produce.

Sources go down, fields go missing, dates come back malformed, a gallery puts
an emoji in a title. None of that should raise - a run that dies takes the
whole week's digest with it.

Run: python test_robustness.py
"""

import inspect
import json
import os
import tempfile

import board
import geocode
import translate
import update
import scoring
import scraper
import state as state_mod

FAILURES = []

# The shapes that actually turn up: nothing, half a record, wrong types.
DEGENERATE = [
    {},
    {"title": None, "venue": None, "artists": None, "raw_description": None},
    {"title": "", "venue": "", "city": None},
    {"title": "x", "venue": "y", "vernissage_datetime": "not-a-date"},
    {"title": "x", "venue": "y", "exhibition_start": "2026-13-45"},
    {"title": "x", "venue": "y", "exhibition_end": ""},
    {"title": "🗿 Skulptur", "venue": "Galerie & Co <script>", "artists": "Ö Ä Ü",
     "raw_description": "Bronze " * 200},
    {"title": "x", "venue": "y", "vernissage_datetime": "2026-08-21T18:00",
     "exhibition_start": "2026-08-21", "exhibition_end": "2026-07-01"},  # end < start
]


def check(name, fn):
    try:
        fn()
        print("  ok   %s" % name)
    except Exception as exc:                                  # noqa: BLE001
        print("  FAIL %s -> %s: %s" % (name, type(exc).__name__, exc))
        FAILURES.append(name)


print("scoring on degenerate records")
for i, rec in enumerate(DEGENERATE):
    check("record %d" % i, lambda r=rec: (
        scoring.medium_tier(dict(r)),
        scoring.matched_keywords(dict(r)),
        scoring.is_koenitz(dict(r)),
        scoring.medium_confidence(dict(r)),
        scoring.stable_id(dict(r)),
        scoring.score_event(dict(r)),
    ))

check("score_all on the whole degenerate set",
      lambda: scoring.score_all([dict(r) for r in DEGENERATE]))
check("score_all on an empty list", lambda: scoring.score_all([]))
check("merge_duplicates on an empty list", lambda: scoring.merge_duplicates([]))
check("same_show with two empty records", lambda: scoring.same_show({}, {}))

print("\nstate on degenerate records")
for i, rec in enumerate(DEGENERATE):
    check("status_of %d" % i, lambda r=rec: state_mod.status_of(dict(r)))

check("merge into a fresh state", lambda: state_mod.merge(
    {"events": {}, "last_run": None},
    [dict(r, id="id%d" % i) for i, r in enumerate(DEGENERATE)]))
check("counts on an empty state",
      lambda: state_mod.counts({"events": {}, "last_run": None}))
check("prune on an empty state",
      lambda: state_mod.prune({"events": {}, "last_run": None}))
check("inventory on an empty state",
      lambda: state_mod.inventory({"events": {}, "last_run": None}))

print("\npage helpers on degenerate records")
scored = scoring.score_all([dict(r) for r in DEGENERATE])
check("summarise", lambda: [board.summarise(dict(r)) for r in DEGENERATE])
check("calendar_url",
      lambda: [board.calendar_url(dict(r)) for r in DEGENERATE])
check("directions_url",
      lambda: [board.directions_url(dict(r)) for r in DEGENERATE])
check("medium_label",
      lambda: [board.medium_label(dict(r)) for r in DEGENERATE])