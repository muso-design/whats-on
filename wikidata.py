"""Medium from the artist, for listings that give us no text to read.

index-berlin supplies most of the Berlin coverage and publishes no exhibition
description anywhere, so keyword scoring has nothing to work on - but it does
name the artists. Who made the work tells you what medium it is, and Wikidata
records that as structured data.

Two things make this safe rather than a guess:

* **It abstains.** An artist with no entry gets no verdict, and the show stays
  honestly unclassified rather than being labelled on a hunch.
* **It refuses the wrong person.** Searching "Kaspar Mueller" turns up a
  politician, a lyricist and an artist; "Alan Charlton" a diplomat and a
  painter. A match only counts when the occupations say visual artist, which
  is why the SPARQL endpoint is used instead of the search API - it returns
  every same-name candidate at once so the artist can be picked from among
  them.

Every answer carries the Wikidata id it came from, so a strange ranking can
always be traced back to a claim someone can check.
"""

import argparse
import json
import os
import re
import sys
import time

import requests

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "artists.json")
SPARQL = "https://query.wikidata.org/sparql"
USER_AGENT = ("ExhibitionDigest/1.0 (personal exhibition listing tool) "
              "python-requests")
BATCH = 40                    # names per query; larger ones start timing out
PAUSE = 2.0                   # the endpoint throttles hard when pushed
MAX_RETRIES = 4

# Occupations that decide the medium.
SCULPTURE = {
    "sculptor", "sculptress", "installation artist", "ceramicist", "ceramist",
    "potter", "woodcarver", "glass artist", "medalist", "stonemason",
}
PAINTING = {
    "painter", "draftsperson", "draughtsman", "printmaker", "graphic artist",
    "illustrator", "lithographer", "engraver", "watercolorist", "etcher",
}
# Everything else that still means "this is the visual artist, not a namesake".
OTHER_ART = {
    "artist", "visual artist", "contemporary artist", "photographer",
    "filmmaker", "film director", "video artist", "performance artist",
    "conceptual artist", "collagist", "textile artist", "multimedia artist",
    "art theorist", "curator", "designer", "architect", "graphic designer",
    "calligrapher", "animator", "cartoonist", "comics artist",
}
ARTIST_OCCUPATIONS = SCULPTURE | PAINTING | OTHER_ART

# "Rosa Barba, Mehtap Baydu & Joseph Beuys" -> three names
_SPLIT = re.compile(r",|;| & | und | and |/|\bwith\b|\bmit\b", re.IGNORECASE)
_NOT_A_NAME = re.compile(r"\d|gruppenausstellung|group show|group exhibition"
                         r"|diverse|u\.a\.|others", re.IGNORECASE)

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT,
                         "Accept": "application/sparql-results+json"})


def split_artists(text):
    """Individual artist names from a listing's artist field."""
    out = []
    for part in _SPLIT.split(text or ""):
        name = " ".join(part.split()).strip(" .-")
        if not name or _NOT_A_NAME.search(name):
            continue
        if not 1 < len(name.split()) <= 4:     # "Ghone" or a whole sentence
            continue
        if name not in out:
            out.append(name)
    return out


def verdict_from(candidates):
    """('sculpture'|'painting'|'other art', qid, occupations) or None.

    `candidates` maps a Wikidata id to that entity's occupations. The first
    candidate that reads as a visual artist wins; namesakes are skipped.
    """
    for qid, occupations in candidates.items():
        occupations = {o.lower() for o in occupations}
        if not occupations & ARTIST_OCCUPATIONS:
            continue                            # a politician of the same name
        if occupations & SCULPTURE:
            kind = "sculpture"
        elif occupations & PAINTING:
            kind = "painting"
        else:
            kind = "other art"
        return kind, qid, sorted(occupations & ARTIST_OCCUPATIONS)
    return None


# --------------------------------------------------------------------------
# cache
# --------------------------------------------------------------------------

def load_cache(path=CACHE_PATH):
    """Everything we have ever resolved, keyed by artist name."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_cache(cache, path=CACHE_PATH):
    """Write the artist cache back. An artist is looked up once, ever."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")


# --------------------------------------------------------------------------
# lookup
# --------------------------------------------------------------------------

def _query(names):
    """One SPARQL call: every same-name candidate and their occupations."""
    values = " ".join('"%s"@de "%s"@en' % (n.replace('"', ""), n.replace('"', ""))
                      for n in names)
    query = ("SELECT ?label ?item ?occLabel WHERE { VALUES ?label { %s } "
             "?item rdfs:label ?label . ?item wdt:P106 ?occ . "
             'SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } }'
             % values)
    for attempt in range(MAX_RETRIES):
        try:
            response = _session.get(SPARQL, params={"query": query,
                                                    "format": "json"},
                                    timeout=90)
            if response.status_code == 429:
                time.sleep(PAUSE * (attempt + 2))
                continue
            response.raise_for_status()
            return response.json()["results"]["bindings"]
        except Exception as exc:                              # noqa: BLE001
            if attempt == MAX_RETRIES - 1:
                print("    ! wikidata batch failed: %s" % str(exc)[:80])
                return None
            time.sleep(PAUSE * (attempt + 2))
    return None


def resolve(names, cache=None, allow_network=True, budget=0, verbose=True,
            cache_path=CACHE_PATH):
    """Look up any names not already cached. Returns the cache."""
    cache = load_cache(cache_path) if cache is None else cache
    pending = [n for n in dict.fromkeys(names) if n not in cache]
    if not pending or not allow_network:
        return cache
    if budget:
        pending = pending[:budget]

    for start in range(0, len(pending), BATCH):
        batch = pending[start:start + BATCH]
        rows = _query(batch)
        if rows is None:
            break                                # throttled; try again next run

        found = {}
        for row in rows:
            name = row["label"]["value"]
            qid = row["item"]["value"].rsplit("/", 1)[-1]
            found.setdefault(name, {}).setdefault(qid, set()).add(
                row["occLabel"]["value"].lower())

        for name in batch:
            candidates = found.get(name)
            if not candidates:
                cache[name] = None                # no entry: remember the miss
                continue
            answer = verdict_from(candidates)
            if answer:
                kind, qid, occupations = answer
                cache[name] = {"medium": kind, "qid": qid,
                               "occupations": occupations}
            else:
                cache[name] = None                # only namesakes
        if cache_path:
            save_cache(cache, cache_path)
        if verbose:
            print("    resolved %d/%d names" % (min(start + BATCH, len(pending)),
                                                len(pending)))
        time.sleep(PAUSE)
    return cache


def medium_for(record, cache):
    """The medium implied by a show's artists, with the evidence behind it.

    A group show counts as sculpture if any one of its artists is a sculptor -
    the point is whether there is sculpture in the room.
    """
    verdicts = []
    for name in split_artists(record.get("artists")):
        hit = cache.get(name)
        if hit:
            verdicts.append((hit["medium"], name, hit))
    if not verdicts:
        return None, None

    for wanted in ("sculpture", "painting", "other art"):
        for kind, name, hit in verdicts:
            if kind == wanted:
                return kind, {"artist": name, "qid": hit["qid"],
                              "occupations": hit["occupations"]}
    return None, None


def enrich(records, cache=None, allow_network=True, budget=0, verbose=True,
           cache_path=CACHE_PATH):
    """Attach `artist_medium` to records that keyword scoring cannot reach."""
    cache = load_cache(cache_path) if cache is None else cache

    names = []
    for record in records:
        names.extend(split_artists(record.get("artists")))
    cache = resolve(names, cache, allow_network=allow_network, budget=budget,
                    verbose=False, cache_path=cache_path)

    tally = {}
    for record in records:
        kind, evidence = medium_for(record, cache)
        if kind:
            record["artist_medium"] = kind
            record["artist_evidence"] = evidence
        tally[kind or "no artist match"] = tally.get(kind or "no artist match", 0) + 1

    if verbose:
        print("  artists: %s (%d known)"
              % (", ".join("%s=%d" % kv for kv in sorted(tally.items())),
                 sum(1 for v in cache.values() if v)))
    return cache, tally


def main(argv=None):
    """Resolve every artist in the inventory, then re-score."""
    parser = argparse.ArgumentParser(
        description="Classify shows by who is exhibiting")
    parser.add_argument("--state", default=None)
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after this many new names")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    import state as state_mod
    path = args.state or state_mod.STATE_PATH
    st = state_mod.load(path)
    records = list(st["events"].values())
    cache = load_cache()

    names = []
    for record in records:
        names.extend(split_artists(record.get("artists")))
    unique = list(dict.fromkeys(names))
    unknown = [n for n in unique if n not in cache]
    print("%d shows, %d artist names, %d already resolved, %d to look up"
          % (len(records), len(unique), len(unique) - len(unknown), len(unknown)))
    if args.dry_run:
        return 0

    cache, tally = enrich(records, cache, budget=args.limit)
    save_cache(cache)
    state_mod.save(st, path)
    print("  " + ", ".join("%s=%d" % kv for kv in sorted(tally.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
