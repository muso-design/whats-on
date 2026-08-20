"""Medium tier, Koenitz override, corroboration, city bar.

Scoring annotates; it never removes. The goal is to know as much as possible
about what is on, so every event any source mentions is kept and ranked. The
city bar and the medium tiers decide the *default view* - what you see before
touching a filter - not what exists.

Deliberately dumb: per-event keyword classification does the real work. There
is no learned or statistical layer and no per-venue prior. At a handful of
shows per venue per year that would measure how often a venue shows sculpture,
not whether it has good taste, and would need years of data to mean anything.
"""

import hashlib
import re
from datetime import date

from scraper import clean, fold

# --------------------------------------------------------------------------
# 1. Medium tier
# --------------------------------------------------------------------------

TIER3_KEYWORDS = [
    # German
    "skulptur", "skulpturen", "bildhauer", "bildhauerei", "bildhauerin",
    "plastik", "plastiken", "relief", "installation", "objekt", "objekte",
    "figurativ", "bronze", "assemblage", "raumobjekt",
    # English
    "sculpture", "sculptural", "sculptor", "relief", "figurative",
    "three dimensional", "object based", "installation",
]

# Galleries describe sculpture by what it is made of far more often than by the
# word "Skulptur" - a show listed only as "Epoxidharz, Farbe" is still sculpture.
MATERIAL_KEYWORDS = [
    "gips", "ton", "keramik", "terrakotta", "porzellan", "marmor", "stein",
    "sandstein", "alabaster", "holz", "beton", "stahl", "eisen", "kupfer",
    "messing", "wachs", "epoxidharz", "harz", "kunstharz", "guss", "gegossen",
    "modelliert", "geschnitzt", "gebrannt",
    "plaster", "clay", "ceramic", "terracotta", "porcelain", "marble", "stone",
    "wood", "concrete", "steel", "iron", "copper", "brass", "wax", "resin",
    "cast", "carved", "modelled", "fired",
]

TIER2_KEYWORDS = [
    # German
    "malerei", "zeichnung", "zeichnungen", "grafik", "graphik", "druckgrafik",
    "gemalde", "aquarell",
    # English
    "painting", "paintings", "drawing", "drawings", "works on paper",
    "print", "prints", "graphic",
]

# Only real inflectional endings are allowed after a keyword, so "Objekt" does
# not fire on "Objektiv" (camera lens) while "Objekte" and "Skulpturen" do.
_ENDINGS = r"(?:e|en|es|er|n|s|in|innen)?"


def _kw_re(words):
    return re.compile(r"\b(" + "|".join(words) + r")" + _ENDINGS + r"\b")


_TIER3_RE = _kw_re(TIER3_KEYWORDS)
_MATERIAL_RE = _kw_re(MATERIAL_KEYWORDS)
_TIER2_RE = _kw_re(TIER2_KEYWORDS)

# Words that describe a *manner* rather than a medium. "figurative Gemaelde"
# is painting; only the noun that follows decides.
AMBIGUOUS = {"figurativ", "figurative"}
_FOLLOWED_BY_MEDIUM = re.compile(
    r"^\s*\w{0,4}\s+(" + "|".join(TIER2_KEYWORDS) + r")" + _ENDINGS + r"\b")

KOENITZ_SLUG = "galerie-koenitz"
KOENITZ_NAMES = ("koenitz", "konitz")


def searchable_text(event):
    """Title + description, plus the artist field.

    The brief specifies title + description. The artist field is included as
    well because on rundgang-kunst it routinely carries the medium outright
    ("... Skulpturen open air"), and dropping it would lose exactly the
    sculpture signal this digest exists to catch.
    """
    return fold(" ".join(filter(None, [
        event.get("title"),
        event.get("artists"),
        event.get("raw_description"),
        event.get("description_en"),     # so both keyword lists can fire
    ])))


def _sculpture_hits(text):
    """Tier 3 keyword matches, minus the ones that turn out to describe painting."""
    hits = []
    for m in list(_TIER3_RE.finditer(text)) + list(_MATERIAL_RE.finditer(text)):
        word = m.group(1)
        if word in AMBIGUOUS and _FOLLOWED_BY_MEDIUM.match(text[m.end():]):
            continue          # "figurativer Gemaelde" - the noun wins
        hits.append(word)
    return hits


def medium_tier(event):
    """3 = sculpture/installation/material, 2 = painting/drawing, 0 = no match.

    Keywords first. Where a listing carries no text at all - most of Berlin -
    the artists decide instead, via `artist_medium` from wikidata.py.
    """
    text = searchable_text(event)
    if _sculpture_hits(text):
        return 3
    if _TIER2_RE.search(text):
        return 2
    by_artist = event.get("artist_medium")
    if by_artist == "sculpture":
        return 3
    if by_artist == "painting":
        return 2
    return 0


def medium_source(event):
    """Where the medium came from: the text, the artist, or nowhere."""
    text = searchable_text(event)
    if _sculpture_hits(text) or _TIER2_RE.search(text):
        return "keywords"
    if event.get("artist_medium"):
        return "artist"
    return "none"


# How much text there was to judge on. index-berlin lists 287 Berlin shows with
# no description anywhere on the site, so "we read it and it is not sculpture"
# and "we had two words to go on" both land at tier 0 - and they are not the
# same claim. Only the first is evidence.
DESCRIBED_CHARS = 120


def medium_confidence(event):
    """'described' when something actually told us the medium, else 'unknown'.

    Knowing a show is photography is as useful as knowing it is sculpture: it
    stops being noise. Only a show nothing could speak for stays unknown.
    """
    if event.get("artist_medium"):
        return "described"        # an artist with a recorded occupation
    text = clean(event.get("raw_description")
                 or event.get("description_en") or "")
    if len(text) >= DESCRIBED_CHARS:
        return "described"
    if event.get("medium_tier"):
        return "described"        # the title or artist field named a medium
    return "unknown"


def matched_keywords(event):
    """Which keywords fired - shown in the digest so the ranking is auditable."""
    text = searchable_text(event)
    hits = _sculpture_hits(text) or [m.group(1) for m in _TIER2_RE.finditer(text)]
    seen, out = set(), []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


# --------------------------------------------------------------------------
# 2. Koenitz override
# --------------------------------------------------------------------------

def is_koenitz(event):
    """Galerie Koenitz is always included, whatever the medium.

    The one hardcoded rule in the system. It is kept because it encodes a
    relationship, not a taste signal an algorithm could infer.
    """
    if (event.get("venue_slug") or "") == KOENITZ_SLUG:
        return True
    venue = fold(event.get("venue"))
    return any(name in venue for name in KOENITZ_NAMES)


# --------------------------------------------------------------------------
# 3. Corroboration
# --------------------------------------------------------------------------

def _overlap(a, b):
    """Word overlap between two strings, ignoring short words."""
    wa = {w for w in fold(a).split() if len(w) > 3}
    wb = {w for w in fold(b).split() if len(w) > 3}
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


_title_overlap = _overlap          # kept under its old name


# Words that carry no identity: every second Berlin gallery is a "Galerie" and
# half of them append the city.
_VENUE_NOISE = {"galerie", "gallery", "galerien", "berlin", "leipzig",
                "contemporary", "projektraum", "the"}


def _venue_tokens(name):
    return {w for w in fold(name).split() if w not in _VENUE_NOISE and len(w) > 1}


def _venue_match(a, b):
    """'BQ' and 'BQ Berlin' are one gallery; 'Galerie K' and 'Galerie KUB' are not."""
    va, vb = fold(a.get("venue")), fold(b.get("venue"))
    if not va or not vb:
        return False
    if va == vb:
        return True

    short, long_ = sorted([va, vb], key=len)
    if len(short) >= 2 and long_.startswith(short):
        # Guard against "galerie k" matching "galerie kub": the longer name has
        # to continue on a word boundary.
        return long_[len(short):].startswith(" ")

    # "Galerie Crone" and "Crone Berlin" are the same place; compare only the
    # parts of the name that identify anything.
    ta, tb = _venue_tokens(va), _venue_tokens(vb)
    if not ta or not tb:
        return False
    return len(ta & tb) / min(len(ta), len(tb)) >= 0.6


def _anchors(event):
    out = []
    for key in ("vernissage_datetime", "exhibition_start", "exhibition_end"):
        stamp = event.get(key)
        if stamp:
            try:
                out.append((key, date.fromisoformat(stamp[:10])))
            except ValueError:
                pass
    return out


def _date_match(a, b, tolerance=4):
    """Same show if any pair of comparable dates is within a few days.

    Sources disagree about whether a show 'starts' on the vernissage or the
    day after, so exact equality is too strict.
    """
    aa, bb = _anchors(a), _anchors(b)
    if not aa or not bb:
        return True                    # nothing to contradict
    for _, da in aa:
        for _, db in bb:
            if abs((da - db).days) <= tolerance:
                return True
    return False


# "Ausstellung" in two different titles is not evidence that they are the same
# show - most titles at a gallery contain it.
_TITLE_NOISE = {"ausstellung", "ausstellungen", "gruppenausstellung",
                "einzelausstellung", "exhibition", "group", "solo", "show",
                "werke", "works", "neue", "new"}


def _name_overlap(a, b):
    """Overlap between two titles or artist lists, ignoring generic words."""
    wa = {w for w in fold(a).split() if len(w) > 3 and w not in _TITLE_NOISE}
    wb = {w for w in fold(b).split() if len(w) > 3 and w not in _TITLE_NOISE}
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / min(len(wa), len(wb))


def same_show(a, b):
    """Whether two records from different sources describe one exhibition."""
    if a.get("city") != b.get("city"):
        return False
    if not _venue_match(a, b):
        return False
    if not _date_match(a, b):
        return False
    # A gallery can open two shows on one night, so the name has to agree too.
    return (_name_overlap(a.get("title"), b.get("title")) >= 0.5
            or _name_overlap(a.get("artists"), b.get("artists")) >= 0.5
            or _name_overlap(a.get("title"), b.get("artists")) >= 0.5
            or _name_overlap(a.get("artists"), b.get("title")) >= 0.5)


def stable_id(event):
    """An id that does not depend on which source reported the show.

    The scraper's id is computed per source, so a merged record would inherit
    the id of whichever source happened to have the longest description - and
    change it the week that source stopped listing the show, which would send
    the whole thing out again as new. This key uses only what every source
    agrees on: the identifying words of the venue and title, and the month the
    show starts.
    """
    venue = " ".join(sorted(_venue_tokens(event.get("venue"))))
    title = " ".join(sorted(
        w for w in fold(event.get("title")).split() if w not in _TITLE_NOISE))
    anchor = ""
    dates = sorted(d.isoformat() for _, d in _anchors(event))
    if dates:
        anchor = dates[0][:7]           # year-month tolerates a day's disagreement
    key = "|".join([venue, title or fold(event.get("artists")), anchor])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


# Preferred when two sources both have a value: whoever knows more wins.
_RICHEST = ("raw_description",)


def _combine(group):
    """Fold several records of one show into the most complete single record."""
    base = max(group, key=lambda e: len(e.get("raw_description") or ""))
    merged = dict(base)
    for ev in group:
        for key, value in ev.items():
            if value in (None, "", [], {}):
                continue
            if merged.get(key) in (None, "", [], {}):
                merged[key] = value
            elif key in _RICHEST and len(str(value)) > len(str(merged[key])):
                merged[key] = value

    # Recomputed from the merged record, so the id does not depend on which
    # source won the base slot or on which sources listed it this week.
    merged["id"] = stable_id(merged)
    merged["sources"] = sorted({e["source"] for e in group if e.get("source")})
    merged["source_urls"] = sorted({e["source_url"] for e in group
                                    if e.get("source_url")})
    merged["corroborated"] = len(merged["sources"]) > 1
    return merged


def merge_duplicates(events):
    """Collapse the same exhibition reported by several sources into one record.

    This is what turns overlapping sources into an advantage: index-berlin
    knows where a gallery is, art-at-berlin knows what the show is about, and
    only together do they make a record worth ranking.
    """
    by_city = {}
    for ev in events:
        by_city.setdefault(ev.get("city"), []).append(ev)

    merged = []
    for group in by_city.values():
        clusters = []
        for ev in group:
            for cluster in clusters:
                if any(same_show(ev, other) for other in cluster):
                    cluster.append(ev)
                    break
            else:
                clusters.append([ev])
        merged.extend(_combine(c) for c in clusters)
    return merged


def mark_corroboration(events):
    """Set 'corroborated' on every event seen in more than one source."""
    for ev in events:
        if "sources" in ev:
            ev["corroborated"] = len(ev["sources"]) > 1
        else:
            ev.setdefault("corroborated", False)
    return events


# --------------------------------------------------------------------------
# 4. The default view
# --------------------------------------------------------------------------

DAY_TRIP_CITIES = {"Halle", "Dresden", "Chemnitz"}
# Friday counts: an evening vernissage in Halle or Dresden is still a day trip
# from Leipzig, and Friday is when most German galleries open.
WEEKEND_DAYS = {4, 5, 6}


def is_weekend(event):
    """Whether the opening falls Friday to Sunday - a day trip is realistic."""
    stamp = event.get("vernissage_datetime") or event.get("exhibition_start")
    if not stamp:
        return False
    try:
        return date.fromisoformat(stamp[:10]).weekday() in WEEKEND_DAYS
    except ValueError:
        return False


def in_default_view(event):
    """Whether this shows up before any filter is touched.

    Nothing is discarded on the strength of this - it decides the opening
    screen, and every filter can be widened to reach the rest.
    """
    tier = event["medium_tier"]
    city = event.get("city")

    if event.get("koenitz_override"):
        return True
    if tier == 0:
        return False
    if tier == 3:
        return True                      # sculpture clears the bar everywhere

    # Tier 2 from here down: how much painting survives depends on the trip.
    if city == "Leipzig":
        return True                      # lives there, low cost to attend
    if city in DAY_TRIP_CITIES:
        return is_weekend(event)         # day-trip realism
    if city == "Berlin":
        return bool(event.get("corroborated"))
    return False


# Kept under the old name: the city bar is now one input to the default view.
passes_city_bar = in_default_view


# --------------------------------------------------------------------------
# Putting it together
# --------------------------------------------------------------------------

def score_event(event):
    """Annotate one event in place. Never decides whether it is kept."""
    text = searchable_text(event)
    event["medium_tier"] = medium_tier(event)
    event["koenitz_override"] = is_koenitz(event)
    event.setdefault("corroborated", False)
    event["matched_keywords"] = matched_keywords(event)

    event["medium_confidence"] = medium_confidence(event)
    event["medium_source"] = medium_source(event)

    if event["koenitz_override"] and event["medium_tier"] == 0:
        event["medium_tier"] = 2         # include it, but below real sculpture

    # Rank inside a section: tier dominates, then weight of evidence.
    headline = fold(" ".join(filter(None, [event.get("title"),
                                           event.get("artists")])))
    rank = event["medium_tier"] * 100
    rank += min(len(event["matched_keywords"]), 5) * 4
    if event["medium_tier"] == 3 and _sculpture_hits(headline):
        rank += 15                       # medium named in the title, not buried
    if event["corroborated"]:
        rank += 10
    if event["koenitz_override"]:
        rank += 5
    event["rank"] = rank

    event["in_default_view"] = in_default_view(event)
    return event


def score_all(events, merge=True):
    """Merge duplicates, annotate and rank. Returns every show, nothing dropped."""
    if merge:
        events = merge_duplicates(events)
    mark_corroboration(events)
    for ev in events:
        score_event(ev)
    return sorted(events, key=sort_key)


def default_view(events):
    """The subset shown before any filter is applied."""
    return [ev for ev in events if ev.get("in_default_view")]


def sort_key(event):
    """Highest rank first, then soonest date."""
    return (-event.get("rank", 0),
            event.get("vernissage_datetime")
            or event.get("exhibition_start") or "9999")


if __name__ == "__main__":
    import json
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "sample_events.json"
    with open(path, encoding="utf-8") as fh:
        events = json.load(fh)

    scored = score_all(events)
    shown = default_view(scored)
    print("scored %d events; %d in the default view, %d behind a filter\n"
          % (len(scored), len(shown), len(scored) - len(shown)))
    for ev in shown[:40]:
        flags = []
        if ev["koenitz_override"]:
            flags.append("KOENITZ")
        if ev["corroborated"]:
            flags.append("corroborated")
        print("  T%d %-4d %-9s %-32s @ %-24s %s" % (
            ev["medium_tier"], ev["rank"], ev["city"],
            clean(ev["title"])[:32], clean(ev["venue"])[:24],
            ",".join(flags + ev["matched_keywords"][:3])))
