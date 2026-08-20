"""Checks for the scoring rules that live data does not currently exercise.

Run: python test_scoring.py
"""

import scoring


def ev(**kw):
    base = {
        "title": "", "artists": None, "venue": "", "venue_slug": None,
        "city": "Berlin", "address": None, "vernissage_datetime": None,
        "exhibition_start": None, "exhibition_end": None,
        "source": "art-at-berlin", "source_url": "", "raw_description": "",
    }
    base.update(kw)
    return base


FAILURES = []


def check(name, got, want):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s: got %r, want %r" % (name, got, want))
        FAILURES.append(name)


print("medium tier")
check("sculpture in title", scoring.medium_tier(ev(title="Neue Skulpturen")), 3)
check("Bildhauerei in description",
      scoring.medium_tier(ev(raw_description="Arbeiten der Bildhauerei")), 3)
check("English sculpture",
      scoring.medium_tier(ev(raw_description="a sculptural installation")), 3)
check("painting only", scoring.medium_tier(ev(raw_description="neue Malerei")), 2)
check("no medium words", scoring.medium_tier(ev(title="Sommerfest")), 0)
check("sculpture beats painting",
      scoring.medium_tier(ev(raw_description="Malerei und Skulptur")), 3)
check("artist field counts",
      scoring.medium_tier(ev(artists="Uta Schlenzig, Skulpturen open air")), 3)
check("no substring false positive",
      scoring.medium_tier(ev(raw_description="das Objektiv der Kamera")), 0)

print("\nKoenitz override")
check("by venue slug",
      scoring.is_koenitz(ev(venue="Galerie Koenitz", venue_slug="galerie-koenitz")),
      True)
check("by venue name", scoring.is_koenitz(ev(venue="Galerie Koenitz")), True)
check("other venue", scoring.is_koenitz(ev(venue="Galerie Kleindienst")), False)

koenitz = scoring.score_event(ev(venue="Galerie Koenitz", city="Leipzig",
                                 title="Fotografien", raw_description="Fotoarbeiten"))
check("tier-0 show at Koenitz survives", scoring.passes_city_bar(koenitz), True)
check("tier-0 show at Koenitz lifted to 2", koenitz["medium_tier"], 2)

print("\ncorroboration (now a consequence of merging)")
pair = scoring.score_all([
    ev(venue="Galerie Judin", title="Pull the Rug",
       vernissage_datetime="2026-09-10T18:00", source="art-at-berlin"),
    ev(venue="Galerie Judin", title="Michael Sailstorfer: Pull the Rug",
       vernissage_datetime="2026-09-10T19:00", source="berlin-art-link"),
])
check("same show in two sources merges", len(pair), 1)
check("and is marked corroborated", pair[0]["corroborated"], True)

same_source = scoring.score_all([
    ev(venue="BBA Gallery", title="Mood Lighting",
       vernissage_datetime="2026-07-08T18:00", source="art-at-berlin"),
    ev(venue="BBA Gallery", title="Mood Lighting",
       vernissage_datetime="2026-07-08T18:00", source="art-at-berlin"),
])
check("a source listing one show twice is not corroboration",
      [e["corroborated"] for e in same_source], [False])

different_show = scoring.score_all([
    ev(venue="Semjon Contemporary", title="Spiegel.Bilder",
       vernissage_datetime="2026-08-28T19:00", source="art-at-berlin"),
    ev(venue="Semjon Contemporary", title="Verzaubert",
       vernissage_datetime="2026-08-28T19:00", source="berlin-art-link"),
])
check("same venue, same night, different show stays separate",
      len(different_show), 2)

print("\ncity bar")
# Leipzig: low bar
check("Leipzig tier 2 kept", scoring.passes_city_bar(
    scoring.score_event(ev(city="Leipzig", raw_description="Malerei"))), True)
check("Leipzig tier 0 dropped", scoring.passes_city_bar(
    scoring.score_event(ev(city="Leipzig", raw_description="Sommerfest"))), False)

# Halle/Dresden/Chemnitz: tier 3 always, tier 2 only on a weekend
check("Dresden tier 3 kept", scoring.passes_city_bar(
    scoring.score_event(ev(city="Dresden", raw_description="Skulptur",
                           vernissage_datetime="2026-08-25T18:00"))), True)
check("Dresden tier 2 on a Tuesday dropped", scoring.passes_city_bar(
    scoring.score_event(ev(city="Dresden", raw_description="Malerei",
                           vernissage_datetime="2026-08-25T18:00"))), False)
check("Dresden tier 2 on a Saturday kept", scoring.passes_city_bar(
    scoring.score_event(ev(city="Dresden", raw_description="Malerei",
                           vernissage_datetime="2026-08-29T18:00"))), True)

# Berlin: high bar
check("Berlin tier 3 kept", scoring.passes_city_bar(
    scoring.score_event(ev(city="Berlin", raw_description="Skulptur"))), True)
check("Berlin tier 2 uncorroborated dropped", scoring.passes_city_bar(
    scoring.score_event(ev(city="Berlin", raw_description="Malerei"))), False)
berlin_pair = ev(city="Berlin", raw_description="Malerei", corroborated=True)
check("Berlin tier 2 corroborated kept",
      scoring.passes_city_bar(scoring.score_event(berlin_pair)), True)

print("\nranking")
ranked = sorted([
    scoring.score_event(ev(title="painting show", raw_description="Malerei",
                           city="Leipzig")),
    scoring.score_event(ev(title="sculpture show", raw_description="Skulptur",
                           city="Leipzig")),
    scoring.score_event(ev(title="corroborated painting", raw_description="Malerei",
                           city="Leipzig", corroborated=True)),
], key=scoring.sort_key)
check("tier 3 first, corroboration breaks ties",
      [e["title"] for e in ranked],
      ["sculpture show", "corroborated painting", "painting show"])

print("\nnothing is discarded")
mixed = [
    ev(title="Skulpturen", city="Leipzig"),
    ev(title="Sommerfest", city="Berlin"),              # tier 0
    ev(title="Malerei", city="Berlin"),                 # tier 2, uncorroborated
]
scored = scoring.score_all(mixed)
check("score_all returns every event", len(scored), 3)
check("the default view is narrower", len(scoring.default_view(scored)), 1)
check("filtered events are still annotated",
      all("rank" in e and "medium_tier" in e for e in scored), True)

print("\nmerging the same show across sources")
idx = ev(title="Hauntings", artists="Carina Brandes", venue="BQ",
         exhibition_start="2026-09-11", source="index-berlin", lat=52.5, lng=13.4)
aab = ev(title="Hauntings", artists="Carina Brandes", venue="BQ Berlin",
         vernissage_datetime="2026-09-10T18:00", exhibition_start="2026-09-11",
         exhibition_end="2026-11-07", source="art-at-berlin",
         raw_description="Carina Brandes arbeitet mit Gips und Bronze." * 4)
check("'BQ' and 'BQ Berlin' are one gallery", scoring.same_show(idx, aab), True)

crone_a = ev(title="Sediments", venue="Galerie Crone",
             exhibition_start="2026-09-10", source="art-at-berlin")
crone_b = ev(title="Sediments", venue="Crone Berlin",
             exhibition_start="2026-09-10", source="index-berlin")
check("'Galerie Crone' and 'Crone Berlin' are one gallery",
      scoring.same_show(crone_a, crone_b), True)

kub = ev(title="Etwas", venue="Galerie K", exhibition_start="2026-09-10")
kub2 = ev(title="Anderes", venue="Galerie KUB", exhibition_start="2026-09-10")
check("'Galerie K' is not 'Galerie KUB'", scoring.same_show(kub, kub2), False)

two_rooms = [
    ev(title="Erste Ausstellung", venue="Kunstverein X",
       exhibition_start="2026-09-10", source="index-berlin"),
    ev(title="Zweite Ausstellung", venue="Kunstverein X",
       exhibition_start="2026-09-10", source="art-at-berlin"),
]
check("two shows opening the same night stay separate",
      len(scoring.merge_duplicates(two_rooms)), 2)

merged = scoring.merge_duplicates([idx, aab])
check("duplicates collapse to one record", len(merged), 1)
one = merged[0]
check("both sources recorded", one["sources"], ["art-at-berlin", "index-berlin"])
check("coordinates survive from the listing source", one["lat"], 52.5)
check("the vernissage time survives from the described source",
      one["vernissage_datetime"], "2026-09-10T18:00")
check("the description survives", "Gips" in one["raw_description"], True)
check("merged record is corroborated",
      scoring.score_event(one)["corroborated"], True)
check("and the merge makes it scoreable", one["medium_tier"], 3)

print("\nsculpture materials")
for text, want in [
    ("Gil Shachar, O.T., 2026, Epoxidharz, Farbe", 3),
    ("Arbeiten in Gips und Bronze", 3),
    ("cast and carved works", 3),
    ("Keramik und Porzellan", 3),
]:
    check(text[:38], scoring.medium_tier(ev(raw_description=text)), want)

print("\nfigurative painting is not sculpture")
check("figurativer Gemaelde -> tier 2",
      scoring.medium_tier(ev(raw_description="eine Serie figurativer Gemälde von X")), 2)
check("figurative painting -> tier 2",
      scoring.medium_tier(ev(raw_description="a series of figurative paintings")), 2)
check("figurativ alone stays tier 3",
      scoring.medium_tier(ev(raw_description="figurative Arbeiten aus Ton")), 3)

print("\nhow much text there was to judge on")
described = scoring.score_event(ev(raw_description="x" * 200))
thin = scoring.score_event(ev(title="Crosstown", artists="Robert Rauschenberg"))
named = scoring.score_event(ev(title="Neue Skulpturen"))
check("long description counts as described", described["medium_confidence"], "described")
check("a bare listing is unknown, not 'not sculpture'",
      thin["medium_confidence"], "unknown")
check("a medium in the title counts as described",
      named["medium_confidence"], "described")

print("\nstatus is derived from the run, not remembered")
import state as state_mod
from datetime import date as _date
today = _date(2026, 8, 20)
for stamps, want in [
    ({"exhibition_start": "2026-08-01", "exhibition_end": "2026-12-01"}, "running"),
    ({"exhibition_start": "2026-08-01", "exhibition_end": "2026-08-25"}, "closing_soon"),
    ({"exhibition_start": "2026-08-01", "exhibition_end": "2026-08-10"}, "closed"),
    ({"vernissage_datetime": "2026-08-29T18:00"}, "opening_soon"),
    ({"exhibition_start": "2026-11-01"}, "upcoming"),
    ({}, "undated"),
]:
    check(want, state_mod.status_of(stamps, today), want)

print("\n%d failure(s)" % len(FAILURES))
raise SystemExit(1 if FAILURES else 0)
