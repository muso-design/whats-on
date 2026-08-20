"""Checks for classifying a show by who is exhibiting.

No network: what matters here is the decision logic, above all that a namesake
never gets to label a show. Wikidata's answers are fixtures, taken from real
lookups.

Run: python test_wikidata.py
"""

import os
import tempfile

import scoring
import wikidata

FAILURES = []


def check(name, got, want):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s: got %r, want %r" % (name, got, want))
        FAILURES.append(name)


print("splitting artist fields")
cases = [
    ("Rosa Barba, Mehtap Baydu & Joseph Beuys",
     ["Rosa Barba", "Mehtap Baydu", "Joseph Beuys"]),
    ("Adrian Kay Wong, Juan de La Rica und Nicolas Bono Kennedy",
     ["Adrian Kay Wong", "Juan de La Rica", "Nicolas Bono Kennedy"]),
    ("Gruppenausstellung", []),
    ("Ghone", []),                       # a single word is not a person
    ("", []),
    (None, []),
]
for text, want in cases:
    check(repr(text)[:38], wikidata.split_artists(text), want)


print("\na namesake never gets to label a show")
# All real: searching these names turns up the wrong person first.
politician = {"Q94785660": {"politician", "chancellor"}}
check("a politician is not an artist", wikidata.verdict_from(politician), None)
check("a researcher is not an artist",
      wikidata.verdict_from({"Q1": {"researcher"}}), None)
check("a diplomat is not an artist",
      wikidata.verdict_from({"Q2": {"diplomat"}}), None)

# The real Kaspar Mueller sits behind the politician in the results.
mixed = {
    "Q94785660": {"politician", "chancellor"},
    "Q94785661": {"lyricist"},
    "Q55236046": {"artist", "installation artist", "painter"},
}
answer = wikidata.verdict_from(mixed)
check("the artist among the namesakes is found", answer[0], "sculpture")
check("and the evidence points at the right entity", answer[1], "Q55236046")


print("\nreading the medium off the occupations")
for occupations, want in [
    ({"sculptor", "visual artist"}, "sculpture"),
    ({"installation artist"}, "sculpture"),
    ({"ceramicist", "painter"}, "sculpture"),      # sculpture wins ties
    ({"painter", "draftsperson"}, "painting"),
    ({"printmaker"}, "painting"),
    ({"photographer", "video artist"}, "other art"),
    ({"filmmaker"}, "other art"),
    ({"artist"}, "other art"),                     # an artist, medium unstated
]:
    got = wikidata.verdict_from({"Q9": occupations})
    check(", ".join(sorted(occupations))[:40], got[0] if got else None, want)


print("\na group show counts if any one of them is a sculptor")
cache = {
    "Anna Painter": {"medium": "painting", "qid": "Q10", "occupations": ["painter"]},
    "Bea Sculptor": {"medium": "sculpture", "qid": "Q11",
                     "occupations": ["sculptor"]},
    "Cee Unknown": None,
}
kind, evidence = wikidata.medium_for(
    {"artists": "Anna Painter, Cee Unknown, Bea Sculptor"}, cache)
check("sculpture wins the group", kind, "sculpture")
check("and names who", evidence["artist"], "Bea Sculptor")

kind, _ = wikidata.medium_for({"artists": "Anna Painter"}, cache)
check("a painter alone stays painting", kind, "painting")
kind, _ = wikidata.medium_for({"artists": "Cee Unknown"}, cache)
check("an unresolved artist gives nothing", kind, None)
kind, _ = wikidata.medium_for({"artists": None}, cache)
check("no artist field gives nothing", kind, None)


print("\nthe cache remembers misses too, so nothing is asked twice")
path = os.path.join(tempfile.mkdtemp(), "artists.json")
wikidata.save_cache(cache, path)
reloaded = wikidata.load_cache(path)
check("round trips", reloaded["Bea Sculptor"]["qid"], "Q11")
check("a miss is stored as a miss", "Cee Unknown" in reloaded, True)
check("and reads back as no answer", reloaded["Cee Unknown"], None)

calls = []
wikidata._query = lambda names: calls.append(list(names)) or []
wikidata.resolve(["Bea Sculptor", "Cee Unknown"], cache=dict(cache),
                 cache_path=None, verbose=False)
check("nothing already known is looked up again", calls, [])


print("\nscoring uses the artist only where the text says nothing")
def ev(**kw):
    base = {"title": "", "artists": None, "venue": "", "raw_description": ""}
    base.update(kw)
    return base


check("artist sculpture lifts a textless show to tier 3",
      scoring.score_event(ev(title="Crosstown", artist_medium="sculpture"))
      ["medium_tier"], 3)
check("artist painting lifts it to tier 2",
      scoring.score_event(ev(title="Crosstown", artist_medium="painting"))
      ["medium_tier"], 2)
check("the description still wins when there is one",
      scoring.score_event(ev(raw_description="Neue Skulpturen in Bronze",
                             artist_medium="painting"))["medium_tier"], 3)
check("and says where the answer came from",
      scoring.score_event(ev(raw_description="Neue Skulpturen in Bronze",
                             artist_medium="painting"))["medium_source"],
      "keywords")
check("a photographer is not sculpture, but is no longer unknown",
      scoring.score_event(ev(title="X", artist_medium="other art"))
      ["medium_confidence"], "described")
check("nothing known at all stays unknown",
      scoring.score_event(ev(title="X"))["medium_confidence"], "unknown")


print("\n%d failure(s)" % len(FAILURES))
raise SystemExit(1 if FAILURES else 0)
