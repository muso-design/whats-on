"""Render the inventory as a single self-contained HTML page.

The email answers "what is new". This answers "what are my options" - every
show being tracked, filterable by status, city and medium, sorted by relevance
or by how soon it closes. No server, no build step: one file, opened from disk
or from a phone.

It carries two inventories, because there are two questions. Where to go is a
show with a run and a distance. Where to send work is a call with a deadline
and an entry fee, and distance means nothing - you can apply to Reykjavik from
Leipzig. They share the page, the filters and the keyboard, and nothing else.
"""

import argparse
import html
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from urllib.parse import quote_plus, urlencode

import state as state_mod

HERE = os.path.dirname(os.path.abspath(__file__))
OUTPUT = os.path.join(HERE, "index.html")
BLURB_CHARS = 260
OPENING_HOURS = 2             # how long to block out for a vernissage
TIMEZONE = "Europe/Berlin"

STATUS_ORDER = ["closing_soon", "opening_soon", "running", "upcoming",
                "undated", "closed"]
STATUS_TEXT = {
    "closing_soon": "closing soon",
    "opening_soon": "opening soon",
    "running": "on now",
    "upcoming": "upcoming",
    "undated": "no dates",
    "closed": "closed",
}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _d(stamp):
    try:
        return date.fromisoformat((stamp or "")[:10])
    except ValueError:
        return None


def _short(day):
    return "%d %s" % (day.day, MONTHS[day.month - 1]) if day else ""


def describe_dates(record):
    """One human line: when it opens, how long it runs, when it goes."""
    vern = record.get("vernissage_datetime")
    start = _d(vern) or _d(record.get("exhibition_start"))
    end = _d(record.get("exhibition_end"))

    if vern and len(vern) > 10:
        opening = "%s %s, %s" % (WEEKDAYS[start.weekday()], _short(start), vern[11:16])
    elif start:
        opening = "%s %s" % (WEEKDAYS[start.weekday()], _short(start))
    else:
        opening = ""

    if opening and end:
        return "%s – %s" % (opening, _short(end))
    if end:
        return "until %s" % _short(end)
    return opening or "dates not announced"


def days_left(record, today=None):
    """Days until the show closes, or None if the end date is unknown."""
    end = _d(record.get("exhibition_end"))
    return (end - (today or date.today())).days if end else None


def days_until(record, today=None):
    """Days until the show opens, or None if the start is unknown."""
    start = _d(record.get("vernissage_datetime")) or _d(
        record.get("exhibition_start"))
    return (start - (today or date.today())).days if start else None


def medium_label(record):
    """The medium filter bucket, keeping 'unknown' distinct from 'not sculpture'."""
    if record.get("medium_tier") == 3:
        return "sculpture"
    if record.get("medium_tier") == 2:
        return "painting/drawing"
    if record.get("medium_confidence") == "unknown":
        return "medium unknown"
    return "other medium"


# Sentences that only restate the header ("Galerie X zeigt ab Freitag ... die
# Ausstellung Y des Kuenstlers Z") and the metadata block that follows the
# prose. Neither belongs in a one-line summary.
_ANNOUNCEMENT = re.compile(
    r"(?i)(zeigt|praesentiert|präsentiert|zeigen|eröffnet|shows"
    r"|presents|opens|invites)")
_META_LABELS = (r"Ausstellungsdaten|Vernissage|Er[oö]ffnung|Finissage"
                r"|K[uü]nstlergespr[aä]ch|[Oö]ffnungszeiten"
                r"|Exhibition dates")
_METADATA = re.compile(r"(?i)^\s*(" + _META_LABELS + r")\s*:")
_METADATA_INLINE = re.compile(r"(?i)(?<![A-Za-z])(" + _META_LABELS + r")\s*:")


def _is_announcement(sentence):
    """A sentence that only restates the card: a verb of showing plus a date."""
    return bool(_ANNOUNCEMENT.search(sentence)
                and re.search(r"\d{4}|\d{1,2}\.\s*[A-Za-z]", sentence))


def summarise(record, limit=BLURB_CHARS):
    """A readable one-liner: the description minus the parts that repeat the card.

    Listings open by restating the gallery, the date and the title, then end
    with a block of dates and opening times. Both are already on the card.
    """
    text = (record.get("description_en")
            or record.get("raw_description") or "").strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return ""

    # Not after a digit: "10. September" is an ordinal, not a sentence end.
    # Some listings write it "29 . August", so allow the stray space too.
    sentences = re.split(
        r"(?<=[.!?])(?<![0-9]\.)(?<![0-9] \.)\s+(?=[\"(A-ZÄÖÜ])",
        text)
    sentences = [x for x in sentences if not _METADATA.match(x)]
    # Drop announcement sentences outright. Some listings are nothing but an
    # announcement, and half of one is worse than none - but only when the
    # sentence also carries a date, so "zeigt Arbeiten aus Bronze" survives.
    while sentences and _is_announcement(sentences[0]):
        sentences.pop(0)
    text = " ".join(sentences).strip()

    cut = _METADATA_INLINE.search(text)
    if cut and cut.start() > 0:
        text = text[:cut.start()].strip()
    text = text.strip(" (,;:-")
    if len(text) < 12:
        return ""
    if len(text) <= limit:
        return text
    clipped = text[:limit]
    stop = max(clipped.rfind(". "), clipped.rfind("! "), clipped.rfind("? "))
    return clipped[:stop + 1] if stop > 80 else clipped.rstrip() + "…"


def calendar_event(record):
    """What a calendar entry for this show should say, and when.

    Returns (title, start, end, all_day, label) or None. A vernissage is an
    appointment, so it becomes a timed entry. A show already running has no
    hour to attend, so the useful entry is a reminder on its last day.
    """
    title = record.get("title") or "Exhibition"
    venue = record.get("venue") or ""
    vern = record.get("vernissage_datetime")
    start = _d(vern) or _d(record.get("exhibition_start"))
    end = _d(record.get("exhibition_end"))
    status = record.get("status")

    if vern and len(vern) > 10 and status not in ("running", "closing_soon"):
        opening = datetime.fromisoformat(vern)
        return ("%s - %s" % (title, venue) if venue else title,
                opening, opening + timedelta(hours=OPENING_HOURS), False,
                "Add opening")

    if status in ("running", "closing_soon") and end:
        return ("Last day: %s - %s" % (title, venue) if venue
                else "Last day: %s" % title,
                end, end + timedelta(days=1), True, "Add last day")

    if start:
        return ("%s - %s" % (title, venue) if venue else title,
                start, start + timedelta(days=1), True, "Add opening")
    return None


def calendar_url(record):
    """A Google Calendar link that opens prefilled for you to confirm.

    Deliberately a link, not an API call: it needs no key, no OAuth and no
    access to the calendar itself. Nothing is written until you press save.
    """
    event = calendar_event(record)
    if not event:
        return "", ""
    title, start, end, all_day, label = event

    if all_day:
        stamps = "%s/%s" % (start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
    else:
        stamps = "%s/%s" % (start.strftime("%Y%m%dT%H%M%S"),
                            end.strftime("%Y%m%dT%H%M%S"))

    details = []
    if record.get("artists"):
        details.append(record["artists"])
    blurb = (record.get("description_en") or record.get("raw_description") or "")
    if blurb:
        details.append(blurb[:400])
    if record.get("opening_hours"):
        details.append("Opening hours: %s" % record["opening_hours"])
    if record.get("source_url"):
        details.append(record["source_url"])

    location = ", ".join(x for x in [record.get("venue"),
                                     record.get("address")] if x)
    params = {
        "action": "TEMPLATE",
        "text": title,
        "dates": stamps,
        "details": "\n\n".join(details),
        "location": location or record.get("city") or "",
    }
    if not all_day:
        params["ctz"] = TIMEZONE
    return ("https://calendar.google.com/calendar/render?" + urlencode(params),
            label)


def directions_url(record):
    """A Google Maps link, by coordinates when known and by name otherwise."""
    lat, lng = record.get("lat"), record.get("lng")
    if lat and lng:
        return "https://www.google.com/maps/search/?api=1&query=%s,%s" % (lat, lng)
    where = ", ".join(x for x in [record.get("venue"), record.get("address"),
                                  record.get("city")] if x)
    if not where:
        return ""
    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(where)


def _why(record):
    """How the medium was decided, so a ranking can always be questioned."""
    source = record.get("medium_source")
    if source == "artist":
        evidence = record.get("artist_evidence") or {}
        occupations = ", ".join(evidence.get("occupations") or [])
        who = evidence.get("artist") or "the artist"
        return "%s: %s" % (who, occupations) if occupations else who
    if source == "keywords":
        hits = record.get("matched_keywords") or []
        return ", ".join(hits[:4])
    return ""


def to_row(record, today=None):
    """Trim an inventory record down to what the page needs."""
    blurb = summarise(record)

    lat, lng = record.get("lat"), record.get("lng")
    cal_url, cal_label = calendar_url(record)
    return {
        "id": record.get("id"),
        "title": record.get("title") or "(untitled)",
        "artists": record.get("artists") or "",
        "venue": record.get("venue") or "",
        "city": record.get("city") or "",
        "status": record.get("status") or "undated",
        "when": describe_dates(record),
        "left": days_left(record, today),
        "until": days_until(record, today),
        "medium": medium_label(record),
        "tier": record.get("medium_tier") or 0,
        "rank": record.get("rank") or 0,
        "sort_date": (record.get("vernissage_datetime")
                      or record.get("exhibition_start") or "9999"),
        "end_date": record.get("exhibition_end") or "9999",
        "hours": record.get("opening_hours") or "",
        "blurb": blurb,
        "first_seen": record.get("first_seen") or "",
        "lang": record.get("language") or "",
        "blurb_lang": ("en" if record.get("description_en")
                       else (record.get("language") or "")),
        "image": record.get("image") or "",
        "why": _why(record),
        "translated": bool(record.get("description_en")
                           and record.get("language") == "de"),
        "url": record.get("source_url") or "",
        "sources": record.get("sources") or [],
        "keywords": record.get("matched_keywords") or [],
        "koenitz": bool(record.get("koenitz_override")),
        "default": bool(record.get("in_default_view")),
        "map": directions_url(record),
        "lat": lat, "lng": lng,
        "cal": cal_url,
        "cal_label": cal_label,
    }


def build_rows(state, today=None):
    """Every inventory record, trimmed for the page and ranked."""
    today = today or date.today()
    rows = [to_row(r, today) for r in state_mod.inventory(state)]
    rows.sort(key=lambda r: (-r["rank"], r["sort_date"]))
    return rows


# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# open calls
# --------------------------------------------------------------------------

CALL_TYPE_TEXT = {"residency": "residency", "grant": "grant", "award": "award",
                  "commission": "commission", "open call": "open call",
                  "curators": "for curators", "collaboration": "collaboration",
                  "job": "job"}

# What a call asks you to produce, in the order it costs you time.
REQUIRE_TEXT = {
    "PORTFOLIO": "portfolio", "CV": "CV", "STATEMENT": "statement",
    "PROJECT_PROPOSAL": "proposal", "MOTIVATION_LETTER": "letter",
    "REFERENCES": "references", "BIOGRAPHY": "bio", "WEBSITE": "website",
    "VIDEO": "video", "BUDGET": "budget",
}


# BBK types nothing, but its titles say what they are in plain German.
BBK_TYPES = [
    ("kunst am bau", "commission"), ("kunst-am-bau", "commission"),
    ("wettbewerb", "competition"), ("stipendium", "grant"),
    ("förderung", "grant"), ("residen", "residency"),
    ("preis", "award"), ("symposium", "symposium"),
    ("ausstellung", "exhibition"), ("atelier", "studio"),
]


def call_type(record):
    """What kind of opportunity it is, in one lowercase word."""
    given = record.get("type")
    if given:
        # An inventory written before a source type was mapped still holds the
        # raw enum, so both spellings are accepted rather than one rendering
        # as "art residency" on the card.
        try:
            import calls as calls_mod
            if given in calls_mod.CALL_TYPES:
                given = calls_mod.CALL_TYPES[given]
        except ImportError:
            pass
        return CALL_TYPE_TEXT.get(given, str(given).replace("_", " ").lower())
    title = (record.get("title") or "").lower()
    for needle, label in BBK_TYPES:
        if needle in title:
            return label
    return ""


# ArtConnect stores descriptions as rich text and hands them over with the
# markup still in them, so "**Artist Opportunity**" arrives verbatim.
_MD_LINK = re.compile(r"\[([^\]]{1,120})\]\([^)]{1,300}\)")
_MD_MARKS = re.compile(r"\*{1,3}|_{2,3}|`+|^#{1,6}\s*|^>\s*", re.M)


def strip_markdown(text):
    """Plain prose out of the rich-text markup the sources leave behind."""
    text = _MD_LINK.sub(r"\1", text or "")
    text = _MD_MARKS.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def call_blurb(record, limit=BLURB_CHARS):
    """The first few sentences of a call, cut on a boundary.

    Calls are written as prose and the opening lines carry the terms, so this
    keeps the beginning rather than hunting for a description the way a show
    listing needs.
    """
    text = strip_markdown(record.get("description_en")
                          or record.get("description") or "")
    if len(text) <= limit:
        return text
    cut = text[:limit]
    stop = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    if stop > limit * 0.5:
        return cut[:stop + 1]
    return cut[:cut.rfind(" ")].rstrip(",;:") + "…"


def deadline_text(record):
    """'by 30 Sep', or the pattern for the ones that come round every year."""
    day = _d(record.get("deadline"))
    if day:
        return "by %d %s" % (day.day, MONTHS[day.month - 1])
    pattern = record.get("deadline_pattern")
    if pattern:
        return "every %s" % pattern if not pattern[0].isdigit() else pattern
    return "no deadline given"


def call_place(record):
    """Where the opportunity is, which is not where you apply from."""
    if record.get("place"):
        return record["place"]
    if record.get("online"):
        return "online"
    bits = [record.get("city"), record.get("country")]
    return ", ".join(b for b in bits if b) or ""


def call_calendar_url(record):
    """An all-day reminder on the deadline, and one the week before.

    A deadline you find out about on the day is a deadline you miss, so the
    entry is placed a week early and named for what it is. Still a link:
    nothing is written to the calendar until you press save.
    """
    day = _d(record.get("deadline"))
    if not day:
        return "", ""
    warn = day - timedelta(days=7)
    if warn <= date.today():
        warn, label = day, "Add deadline"
    else:
        label = "Remind me"
    details = [record.get("organisation") or "",
               "Deadline: %d %s %d" % (day.day, MONTHS[day.month - 1], day.year)]
    if record.get("requires"):
        details.append("Wants: " + ", ".join(record["requires"][:6]))
    if record.get("fee_note"):
        details.append("Fee: %s" % record["fee_note"])
    if record.get("url"):
        details.append(record["url"])
    params = {
        "action": "TEMPLATE",
        "text": "Apply: %s" % (record.get("title") or "open call"),
        "dates": "%s/%s" % (warn.strftime("%Y%m%d"),
                            (warn + timedelta(days=1)).strftime("%Y%m%d")),
        "details": "\n\n".join(x for x in details if x),
    }
    return ("https://calendar.google.com/calendar/render?" + urlencode(params),
            label)


def to_call_row(record, today=None, key=None):
    """One call, flattened for the page.

    The id is the inventory key, which the stored record does not repeat. It
    has to be threaded through: without it every card shares an empty id, and
    tracking one application marks all of them.
    """
    left = record.get("days_left")
    requires = [REQUIRE_TEXT.get(r, str(r).replace("_", " ").lower())
                for r in (record.get("requires") or [])]
    cal, cal_label = call_calendar_url(record)
    return {
        "cal": cal,
        "cal_label": cal_label,
        "elig": record.get("eligibility") or "open",
        "open_to": record.get("open_to") or [],
        "id": key or record.get("id") or "",
        "kind": "call",
        "title": record.get("title") or "",
        "org": record.get("organisation") or "",
        "type": call_type(record),
        "place": call_place(record),
        "deadline": (record.get("deadline") or "")[:10],
        "when": deadline_text(record),
        "left": left,
        "status": record.get("status") or "rolling",
        "fit": record.get("sculpture") or "no",
        "why": record.get("sculpture_why") or "",
        "spec": record.get("specificity") or "untagged",
        "fee": record.get("fee"),
        "fee_note": record.get("fee_note") or "",
        "requires": requires[:5],
        "restrictions": record.get("restrictions") or "",
        "url": record.get("url") or record.get("source_url") or "",
        "source": record.get("source") or "",
        "blurb": call_blurb(record),
        "lang": record.get("language") or "en",
        "rank": record.get("rank") or 0,
        "first_seen": record.get("first_seen") or "",
    }


def build_call_rows(inventory, today=None):
    """Every call worth showing, best first. Closed ones are left out."""
    rows = [to_call_row(record, today, key)
            for key, record in (inventory or {}).get("calls", {}).items()
            if record.get("status") != "closed"]
    rows.sort(key=lambda r: (-r["rank"], r["deadline"] or "9999"))
    return rows


def load_calls():
    """The calls inventory, if the calls run has ever happened."""
    try:
        import calls as calls_mod
    except ImportError:
        return {}
    try:
        return calls_mod.load()
    except Exception:                                          # noqa: BLE001
        return {}


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#2E6A5C">
<meta name="description" content="Exhibitions on now in Leipzig and Berlin.">
<title>What's on</title>
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="icon-192.png">
<link rel="apple-touch-icon" href="icon-192.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
:root{
  --ground:#F3F3F1; --surface:#FFFFFF; --sunk:#EAEAE6; --raise:#FFFFFF;
  --ink:#1A1D1B; --muted:#606661; --line:#D2D4D0;
  --accent:#2E6A5C; --accent-soft:#E2EDE9;
  --urgent:#A8482A; --urgent-soft:#F6E7E1;
  --new:#7E631C; --new-soft:#F5EEDB;
  /* Text on a filled accent. The accent is dark in light mode and light in
     dark mode, so the foreground has to flip with it. */
  --on-fill:#FFFFFF;
  --sans:"IBM Plex Sans","Segoe UI",system-ui,-apple-system,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,Consolas,monospace;
  --tap:44px;
  --safe-b:env(safe-area-inset-bottom,0px);
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --ground:#121512; --surface:#1A1E1B; --sunk:#161A17; --raise:#222724;
    --ink:#E6E9E6; --muted:#98A099; --line:#2C312D;
    --accent:#74B9A4; --accent-soft:#1D2B27;
    --urgent:#D5764F; --urgent-soft:#2E211B;
    --new:#D6B45C; --new-soft:#2A2418;
    --on-fill:#0E1A16;
  }
}
:root[data-theme="dark"]{
  --ground:#121512; --surface:#1A1E1B; --sunk:#161A17; --raise:#222724;
  --ink:#E6E9E6; --muted:#98A099; --line:#2C312D;
  --accent:#74B9A4; --accent-soft:#1D2B27;
  --urgent:#D5764F; --urgent-soft:#2E211B;
  --new:#D6B45C; --new-soft:#2A2418;
  --on-fill:#0E1A16;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
/* Sizes are in rem so that a larger default text size in the browser or the
   operating system actually enlarges this page. Nothing here is below
   0.75rem, which is the point where small print stops being readable. */
body{margin:0;background:var(--ground);color:var(--ink);
  font-family:var(--sans);font-size:1rem;line-height:1.55;
  -webkit-font-smoothing:antialiased;overscroll-behavior-y:none}
button{font:inherit;color:inherit}
a{color:var(--accent)}

.wrap{max-width:960px;margin:0 auto;
  padding:0 14px calc(84px + var(--safe-b))}

/* ---------- header ---------- */
.top{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;
  padding:14px 2px 8px}
h1{font-size:1.3rem;font-weight:600;letter-spacing:-.02em;margin:0}
.stamp{font-family:var(--mono);font-size:.8rem;color:var(--muted);margin:0}
.summary{font-size:1rem;color:var(--ink);margin:0 0 2px;padding:0 2px 8px;
  max-width:60ch}
.summary b{font-weight:600}
.summary .none{color:var(--muted);font-weight:400}

/* ---------- controls ---------- */
.controls{position:sticky;top:0;z-index:20;background:var(--ground);
  padding:8px 0 9px;border-bottom:1px solid var(--line)}
.searchrow{display:flex;gap:7px}
input[type=search]{flex:1 1 auto;min-width:0;font:inherit;color:var(--ink);
  background:var(--surface);border:1px solid var(--line);border-radius:9px;
  padding:0 12px;height:var(--tap);-webkit-appearance:none}
/* One obvious focus style for everything that can take focus, drawn outside
   the element so it is never clipped by a rounded corner. */
:focus-visible{outline:3px solid var(--accent);outline-offset:2px;
  border-radius:4px}
a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible{
  outline:3px solid var(--accent);outline-offset:2px}

@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.01ms !important;
    animation-iteration-count:1 !important;transition-duration:.01ms !important;
    scroll-behavior:auto !important}
}
@media (prefers-contrast:more){
  :root{--muted:var(--ink);--line:var(--ink)}
  .card,.chip,.act,input[type=search],select{border-width:2px}
  .why,.chip .c{opacity:1}
}

/* Visible only to screen readers, for labels a sighted user gets from layout. */
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;
  overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
.skip{position:absolute;left:8px;top:-60px;z-index:60;background:var(--accent);
  color:var(--on-fill);padding:10px 16px;border-radius:0 0 8px 8px;text-decoration:none;
  font-size:.9rem}
.skip:focus{top:0}
select{font:inherit;color:var(--ink);background:var(--surface);
  border:1px solid var(--line);border-radius:9px;padding:0 8px;height:var(--tap);
  max-width:44%}

/* An explicit display beats the browser's own [hidden] rule, so every
   .filters row and .badge stayed visible once it was told to hide. */
[hidden]{display:none !important}
.filters{display:flex;flex-direction:column;gap:4px;margin-top:7px}
.row{display:flex;gap:6px;overflow-x:auto;scrollbar-width:none;
  padding-bottom:2px;-webkit-overflow-scrolling:touch}
.row::-webkit-scrollbar{display:none}
.chip{font-family:var(--mono);font-size:.8rem;background:var(--surface);
  color:var(--muted);border:1px solid var(--line);border-radius:999px;
  padding:0 12px;height:32px;display:inline-flex;align-items:center;gap:5px;
  cursor:pointer;white-space:nowrap;flex:0 0 auto}
.chip .c{opacity:.75;font-size:.75rem}
.chip[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);
  color:var(--on-fill)}
.chip.urgent[aria-pressed="true"]{background:var(--urgent);border-color:var(--urgent)}
.chip.new[aria-pressed="true"]{background:var(--new);border-color:var(--new)}
.chip.reset{border-style:dashed}

/* ---------- results ---------- */
.count{font-family:var(--mono);font-size:.82rem;color:var(--muted);
  padding:9px 2px 7px;display:flex;justify-content:space-between;gap:10px;
  align-items:center}
.card{background:var(--surface);border:1px solid var(--line);
  border-left:3px solid var(--line);border-radius:10px;
  padding:13px 14px;margin-bottom:9px}
.card.t3{border-left-color:var(--accent)}
.card.urgent{border-left-color:var(--urgent)}
.card.isnew{border-left-color:var(--new)}
.card.done{opacity:.55}
.card h2{font-size:1.06rem;font-weight:600;margin:0 0 2px;letter-spacing:-.01em;
  line-height:1.3}
.card h2 a{color:inherit;text-decoration:none}
.who{color:var(--muted);font-size:.94rem;margin:0 0 7px}
.where{display:flex;flex-wrap:wrap;gap:3px 10px;align-items:baseline;
  font-size:.9rem;margin-bottom:8px}
.venue{font-weight:500}
.when{font-family:var(--mono);font-size:.82rem;color:var(--muted)}
.tags{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:8px}
.tag{font-family:var(--mono);font-size:.75rem;letter-spacing:.03em;
  text-transform:uppercase;padding:3px 7px;border-radius:4px;
  background:var(--sunk);color:var(--muted)}
.tag.live{background:var(--accent-soft);color:var(--accent)}
.tag.urgent{background:var(--urgent-soft);color:var(--urgent)}
.grouphead{font-size:.95rem;font-weight:650;letter-spacing:.02em;
  text-transform:lowercase;color:var(--muted);margin:22px 0 10px}
.grouphead:first-child{margin-top:4px}
.card.call .where{color:var(--muted)}
select.stage{font:inherit;font-size:.85rem;padding:7px 10px;border-radius:8px;
  border:1px solid var(--line);background:var(--card);color:var(--ink);
  min-height:38px;cursor:pointer}
select.stage:focus-visible{outline:3px solid var(--focus);outline-offset:2px}
.tag.med{background:var(--accent-soft);color:var(--accent)}
.tag.new{background:var(--new-soft);color:var(--new)}
.blurb{font-size:.92rem;color:var(--muted);margin:0 0 6px}
.why{font-family:var(--mono);font-size:.78rem;color:var(--muted);opacity:.75;
  margin:0 0 9px}
.shot{float:right;width:96px;height:96px;margin:0 0 8px 12px;border-radius:8px;
  overflow:hidden;background:var(--sunk)}
.shot img{width:100%;height:100%;object-fit:cover;display:block}
.card::after{content:"";display:block;clear:both}
@media (max-width:520px){.shot{width:74px;height:74px;margin-left:10px}}
.hours{font-family:var(--mono);font-size:.78rem;color:var(--muted);
  margin:0 0 9px;padding-top:7px;border-top:1px dashed var(--line)}

/* actions: big enough for a thumb */
.acts{display:flex;flex-wrap:wrap;gap:6px}
.act{background:var(--surface);border:1px solid var(--line);border-radius:8px;
  height:40px;padding:0 12px;display:inline-flex;align-items:center;gap:6px;
  font-size:.85rem;cursor:pointer;text-decoration:none;color:var(--ink)}
@media (pointer:coarse){.act{height:var(--tap)}}
.act:hover{border-color:var(--muted)}
.act[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);
  color:var(--on-fill)}
.act.skip[aria-pressed="true"]{background:var(--muted);border-color:var(--muted);
  color:var(--ground)}
.act .i{font-size:13px;line-height:1}

.empty{text-align:center;color:var(--muted);padding:2.5rem 1.2rem;font-size:1rem;
  border:1px dashed var(--line);border-radius:10px}
.empty b{color:var(--ink);font-weight:600}

/* ---------- map ---------- */
#map{height:calc(100vh - 320px);min-height:320px;border:1px solid var(--line);
  border-radius:10px;margin-bottom:8px;background:var(--sunk)}
.mapnote{font-family:var(--mono);font-size:.78rem;color:var(--muted);margin:0 0 12px}
.pin{border-radius:50%;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.45)}
.pin.s-closing{background:#A8482A}
.pin.s-open{background:#2E6A5C}
.pin.s-saved{background:#8A6D1F}
.pin.s-other{background:#8A908A}
.leaflet-popup-content{font-family:var(--sans);font-size:13.5px;margin:11px 13px}
.leaflet-popup-content h3{font-size:14.5px;margin:0 0 3px;font-weight:600}
.leaflet-popup-content .pv{color:#555;margin:0 0 6px}
.leaflet-popup-content .pl{display:flex;gap:10px;flex-wrap:wrap;font-size:12.5px}

/* ---------- bottom navigation ---------- */
.nav{position:fixed;left:0;right:0;bottom:0;z-index:30;
  display:flex;background:var(--surface);border-top:1px solid var(--line);
  padding-bottom:var(--safe-b)}
.nav button{flex:1;border:0;background:none;height:56px;cursor:pointer;
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  gap:3px;color:var(--muted);font-size:.75rem;font-family:var(--mono)}
.nav button[aria-selected="true"]{color:var(--accent)}
.nav .g{font-size:1.15rem;line-height:1}
.nav .badge{position:absolute;transform:translate(15px,-13px);
  background:var(--new);color:var(--on-fill);border-radius:999px;font-size:9.5px;
  min-width:16px;height:16px;display:flex;align-items:center;
  justify-content:center;padding:0 4px}

/* ---------- desktop ---------- */
@media (min-width:760px){
  .wrap{padding-bottom:40px}
  .nav{position:static;border:1px solid var(--line);border-radius:10px;
    margin:14px 0 0;padding:0;max-width:420px}
  .nav button{height:44px;flex-direction:row;gap:7px;font-size:.85rem}
  #map{height:min(64vh,540px)}
  .filters{flex-direction:row;flex-wrap:wrap;gap:6px}
  .row{overflow:visible;flex-wrap:wrap}
}
</style>
</head>
<body>
<a class="skip" href="#results">Skip to the exhibitions</a>
<div class="wrap">

  <header class="top">
    <h1>What&rsquo;s on</h1>
    <p class="stamp" id="stamp"></p>
  </header>

  <p class="summary" id="summary"></p>

  <nav class="nav" id="nav" role="tablist" aria-label="Views">
    <button role="tab" id="tab-browse" aria-controls="results"
            aria-selected="true" tabindex="0" data-tab="browse">
      <span class="g" aria-hidden="true">◍</span><span>Browse</span></button>
    <button role="tab" id="tab-calls" aria-controls="results"
            aria-selected="false" tabindex="-1" data-tab="calls">
      <span class="g" aria-hidden="true">✉</span><span>Calls</span>
      <span class="badge" id="callcount" hidden></span></button>
    <button role="tab" id="tab-saved" aria-controls="results"
            aria-selected="false" tabindex="-1" data-tab="saved">
      <span class="g" aria-hidden="true">★</span><span>Saved</span>
      <span class="badge" id="savedcount" hidden></span></button>
    <button role="tab" id="tab-map" aria-controls="map"
            aria-selected="false" tabindex="-1" data-tab="map">
      <span class="g" aria-hidden="true">⌖</span><span>Map</span></button>
  </nav>

  <div class="controls" id="controls">
    <h2 class="sr-only">Search and filter</h2>
    <div class="searchrow">
      <input type="search" id="q" placeholder="Artist, title or venue…"
             aria-label="Search" autocomplete="off">
      <select id="sort" aria-label="Sort by">
        <option value="rank">Most relevant</option>
        <option value="soon">Opening soonest</option>
        <option value="closing">Closing soonest</option>
      </select>
      <select id="csort" aria-label="Sort by" hidden>
        <option value="rank">Most relevant</option>
        <option value="deadline">Deadline soonest</option>
        <option value="runway">Most time to prepare</option>
      </select>
    </div>
    <div class="filters" id="show-filters">
      <div class="row" id="f-status" role="group" aria-label="When"></div>
      <div class="row" id="f-city" role="group" aria-label="Where"></div>
      <div class="row" id="f-medium" role="group" aria-label="Medium"></div>
    </div>
    <div class="filters" id="call-filters" hidden>
      <div class="row" id="f-fit" role="group" aria-label="Relevance"></div>
      <div class="row" id="f-runway" role="group" aria-label="Time left"></div>
      <div class="row" id="f-terms" role="group" aria-label="Terms"></div>
      <div class="row" id="f-ctype" role="group" aria-label="Kind"></div>
    </div>
  </div>

  <div class="count">
    <span id="count"></span>
    <button class="chip reset" id="reset" hidden>Clear filters</button>
  </div>

  <!-- Filtering changes the page silently for anyone not watching it, so the
       result count and every mark are announced here. -->
  <p class="sr-only" role="status" aria-live="polite" id="announce"></p>

  <main id="content">
    <div id="map" role="tabpanel" aria-labelledby="tab-map"
         tabindex="-1" hidden></div>
    <p class="mapnote" id="mapnote" hidden></p>
    <div id="results" role="tabpanel" aria-labelledby="tab-browse"
         tabindex="-1"></div>
  </main>

</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script id="data" type="application/json">__DATA__</script>
<script id="calldata" type="application/json">__CALLS__</script>
<script>
(function () {
  "use strict";
  var rows = JSON.parse(document.getElementById("data").textContent);
  var BUILT = "__BUILT__";

  // ---- what you have decided about each show -------------------------
  // Kept in this browser only. Nothing is uploaded and no account exists.
  var STORE = "whatson.marks.v1";
  var VISIT = "whatson.lastvisit.v1";

  function loadMarks() {
    try { return JSON.parse(localStorage.getItem(STORE) || "{}"); }
    catch (e) { return {}; }
  }
  function saveMarks() {
    try { localStorage.setItem(STORE, JSON.stringify(marks)); } catch (e) {}
  }
  var marks = loadMarks();

  // "New" means: appeared since the last time you opened this. On a first
  // visit nothing is new - flagging all 338 would say nothing.
  var lastVisit = null;
  try { lastVisit = localStorage.getItem(VISIT); } catch (e) {}
  var firstEver = !lastVisit;
  try { localStorage.setItem(VISIT, new Date().toISOString()); } catch (e) {}

  function isNew(r) {
    return !firstEver && !!lastVisit && !!r.first_seen && r.first_seen > lastVisit;
  }
  rows.forEach(function (r) { r._new = isNew(r); });

  // ---- the other inventory -------------------------------------------
  var calls = [];
  try {
    calls = JSON.parse(document.getElementById("calldata").textContent) || [];
  } catch (e) { calls = []; }
  calls.forEach(function (c) { c._new = isNew(c); });

  // Where you are with each application. Four stages, because that is how
  // many states an application is actually in: one you noticed, one you are
  // writing, one you sent, and one that came back.
  var APPS = "whatson.apps.v1";
  var STAGES = [
    ["", "not tracking"],
    ["interested", "interested"],
    ["preparing", "preparing"],
    ["submitted", "submitted"],
    ["answered", "heard back"]
  ];
  var apps = {};
  try { apps = JSON.parse(localStorage.getItem(APPS) || "{}"); } catch (e) {}
  function saveApps() {
    try { localStorage.setItem(APPS, JSON.stringify(apps)); } catch (e) {}
  }

  var FIT = [["yes", "sculpture"], ["maybe", "maybe"], ["no", "everything else"]];
  var RUNWAY = [
    ["closing", "closing", "urgent"],
    ["soon", "this month", ""],
    ["open", "later", ""],
    ["rolling", "no deadline", ""]
  ];

  var STATUS = [
    ["closing_soon", "closing soon", "urgent"],
    ["opening_soon", "opening soon", ""],
    ["running", "on now", ""],
    ["upcoming", "later", ""],
    ["closed", "closed", ""]
  ];
  var MEDIUM = [
    ["sculpture", "sculpture"],
    ["painting/drawing", "painting"],
    ["medium unknown", "unclassified"]
  ];

  var state = {
    tab: "browse",
    q: "",
    sort: "rank",
    csort: "rank",
    onlyNew: false,
    status: new Set(["closing_soon", "opening_soon"]),
    city: new Set(),
    medium: new Set(),
    // Calls open on sculpture only. Nine in ten of the rest are painting
    // prizes and curator jobs, and the point of the tab is where the work
    // could go, not what exists.
    fit: new Set(["yes"]),
    runway: new Set(),
    ctype: new Set(),
    freeOnly: false,
    canEnter: true          // hide what you are not allowed to apply to
  };

  function mode() {
    return state.tab === "calls" ? "calls" : state.tab === "saved" ? "both" : "shows";
  }

  // An action's own message beats the running result count: pressing Save
  // should say "Saved", not read the list length back at you.
  var pendingMessage = null;
  var lastAnnouncedCount = null;

  function announce(message) {
    // Re-setting the same text does not re-announce, so clear it first.
    var region = document.getElementById("announce");
    region.textContent = "";
    setTimeout(function () { region.textContent = message; }, 60);
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  // ---- filtering ------------------------------------------------------
  function textMatch(r) {
    if (!state.q) { return true; }
    return (r.title + " " + r.artists + " " + r.venue + " " + r.blurb)
      .toLowerCase().indexOf(state.q) !== -1;
  }

  function matches(r) {
    if (state.tab === "saved") {
      var m = marks[r.id];
      if (m !== "want" && m !== "going") { return false; }
      return textMatch(r);
    }
    if (marks[r.id] === "skip") { return false; }
    if (state.onlyNew && !r._new) { return false; }
    if (state.status.size && !state.status.has(r.status)) { return false; }
    if (state.city.size && !state.city.has(r.city)) { return false; }
    if (state.medium.size && !state.medium.has(r.medium)) { return false; }
    return textMatch(r);
  }

  function callText(c) {
    if (!state.q) { return true; }
    return (c.title + " " + c.org + " " + c.place + " " + c.blurb + " " + c.type)
      .toLowerCase().indexOf(state.q) !== -1;
  }

  function matchesCall(c) {
    if (state.tab === "saved") { return !!apps[c.id] && callText(c); }
    if (state.canEnter && c.elig === "closed") { return false; }
    if (state.freeOnly && c.fee !== false) { return false; }
    if (state.fit.size && !state.fit.has(c.fit)) { return false; }
    if (state.runway.size && !state.runway.has(c.status)) { return false; }
    if (state.ctype.size && !state.ctype.has(c.type)) { return false; }
    return callText(c);
  }

  function sortedCalls(list) {
    var copy = list.slice();
    function byDeadline(a, b) {
      var ad = a.deadline || "9999", bd = b.deadline || "9999";
      return ad < bd ? -1 : ad > bd ? 1 : 0;
    }
    if (state.csort === "deadline") { copy.sort(byDeadline); }
    else if (state.csort === "runway") {
      // Most time to prepare first, and calls with no deadline at the end -
      // "rolling" is not the same as "loads of time".
      copy.sort(function (a, b) {
        var al = a.left == null ? -1 : a.left, bl = b.left == null ? -1 : b.left;
        return bl - al;
      });
    } else {
      copy.sort(function (a, b) { return b.rank - a.rank || byDeadline(a, b); });
    }
    return copy;
  }

  function sorted(list) {
    var copy = list.slice();
    function byDate(a, b) {
      return a.sort_date < b.sort_date ? -1 : a.sort_date > b.sort_date ? 1 : 0;
    }
    if (state.sort === "soon") { copy.sort(byDate); }
    else if (state.sort === "closing") {
      copy.sort(function (a, b) {
        var ac = a.status === "closed" ? 1 : 0, bc = b.status === "closed" ? 1 : 0;
        if (ac !== bc) { return ac - bc; }
        return a.end_date < b.end_date ? -1 : a.end_date > b.end_date ? 1 : 0;
      });
    } else {
      copy.sort(function (a, b) { return b.rank - a.rank || byDate(a, b); });
    }
    return copy;
  }

  // ---- chips ----------------------------------------------------------
  // Groups that are a single on/off switch rather than a set of values.
  var TOGGLES = { "new": "onlyNew", "free": "freeOnly", "enter": "canEnter" };

  function pressed(group, value) {
    var flag = TOGGLES[group];
    return flag ? state[flag] : state[group].has(value);
  }

  function chip(label, value, count, group, extra) {
    var b = document.createElement("button");
    b.className = "chip " + (extra || "");
    b.type = "button";
    b.dataset.group = group;
    b.dataset.value = value;
    b.setAttribute("aria-pressed", String(pressed(group, value)));
    b.innerHTML = esc(label) +
      (count != null ? '<span class="c">' + count + "</span>" : "");
    b.addEventListener("click", function () {
      var flag = TOGGLES[group];
      if (flag) { state[flag] = !state[flag]; }
      else if (state[group].has(value)) { state[group].delete(value); }
      else { state[group].add(value); }
      b.setAttribute("aria-pressed", String(pressed(group, value)));
      render();
    });
    return b;
  }

  function buildFilters() {
    var s = document.getElementById("f-status");
    var newCount = rows.filter(function (r) { return r._new; }).length;
    if (newCount) { s.appendChild(chip("new", "new", newCount, "new", "new")); }
    STATUS.forEach(function (row) {
      var n = rows.filter(function (r) { return r.status === row[0]; }).length;
      if (n) { s.appendChild(chip(row[1], row[0], n, "status", row[2])); }
    });

    var cities = {};
    rows.forEach(function (r) { cities[r.city] = (cities[r.city] || 0) + 1; });
    var c = document.getElementById("f-city");
    Object.keys(cities).sort(function (a, b) { return cities[b] - cities[a]; })
      .forEach(function (name) {
        if (name) { c.appendChild(chip(name, name, cities[name], "city")); }
      });

    var m = document.getElementById("f-medium");
    MEDIUM.forEach(function (row) {
      var n = rows.filter(function (r) { return r.medium === row[0]; }).length;
      if (n) { m.appendChild(chip(row[1], row[0], n, "medium")); }
    });
  }

  function buildCallFilters() {
    if (!calls.length) { return; }
    var f = document.getElementById("f-fit");
    FIT.forEach(function (row) {
      var n = calls.filter(function (c) { return c.fit === row[0]; }).length;
      if (n) { f.appendChild(chip(row[1], row[0], n, "fit")); }
    });

    var r = document.getElementById("f-runway");
    RUNWAY.forEach(function (row) {
      var n = calls.filter(function (c) { return c.status === row[0]; }).length;
      if (n) { r.appendChild(chip(row[1], row[0], n, "runway", row[2])); }
    });

    var t = document.getElementById("f-terms");
    var free = calls.filter(function (c) { return c.fee === false; }).length;
    if (free) { t.appendChild(chip("free to enter", "free", free, "free")); }
    var shut = calls.filter(function (c) { return c.elig === "closed"; }).length;
    if (shut) {
      t.appendChild(chip("open to me", "enter", calls.length - shut, "enter"));
    }

    var kinds = {};
    calls.forEach(function (c) { kinds[c.type] = (kinds[c.type] || 0) + 1; });
    var k = document.getElementById("f-ctype");
    Object.keys(kinds).sort(function (a, b) { return kinds[b] - kinds[a]; })
      .forEach(function (name) {
        if (name) { k.appendChild(chip(name, name, kinds[name], "ctype")); }
      });
  }

  function filtersActive() {
    if (mode() === "calls") {
      return state.fit.size !== 1 || !state.fit.has("yes") ||
        state.runway.size || state.ctype.size || state.freeOnly ||
        !state.canEnter;
    }
    return state.status.size || state.city.size || state.medium.size ||
      state.onlyNew;
  }

  function syncChips() {
    document.querySelectorAll(".chip[data-group]").forEach(function (b) {
      b.setAttribute("aria-pressed",
        String(pressed(b.dataset.group, b.dataset.value)));
    });
  }

  function clearFilters() {
    if (mode() === "calls") {
      // Back to the default, which is not "everything": the sculpture filter
      // is the tab, not a preference you happened to set.
      state.fit = new Set(["yes"]);
      state.runway.clear(); state.ctype.clear();
      state.freeOnly = false; state.canEnter = true;
    } else {
      state.status.clear(); state.city.clear(); state.medium.clear();
      state.onlyNew = false;
    }
    syncChips();
    render();
  }

  // ---- marking --------------------------------------------------------
  var MARK_WORDS = {want: "Saved", seen: "Marked as seen", skip: "Hidden"};

  function setMark(id, value) {
    var show = rows.filter(function (r) { return r.id === id; })[0];
    var name = show ? show.title : "This exhibition";
    if (marks[id] === value) {
      delete marks[id];
      pendingMessage = name + ": no longer " +
        (MARK_WORDS[value] || value).toLowerCase();
    } else {
      marks[id] = value;
      pendingMessage = name + ": " + (MARK_WORDS[value] || value).toLowerCase();
    }
    saveMarks();
    render();
    updateSavedCount();
  }

  function updateSavedCount() {
    var n = Object.keys(marks).filter(function (k) {
      return marks[k] === "want" || marks[k] === "going";
    }).length;
    var badge = document.getElementById("savedcount");
    badge.textContent = n;
    badge.hidden = !n;
  }

  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-mark]");
    if (btn) { setMark(btn.dataset.id, btn.dataset.mark); }
  });

  // ---- cards ----------------------------------------------------------
  function statusTag(r) {
    if (r.status === "closing_soon") {
      var d = r.left;
      return '<span class="tag urgent">' + esc(
        d == null ? "closing soon" : d <= 0 ? "last day"
          : d === 1 ? "1 day left" : d + " days left") + "</span>";
    }
    if (r.status === "opening_soon") {
      var u = r.until;
      return '<span class="tag live">' + esc(
        u == null ? "opening soon" : u <= 0 ? "opens today"
          : u === 1 ? "opens tomorrow" : "in " + u + " days") + "</span>";
    }
    if (r.status === "running") { return '<span class="tag live">on now</span>'; }
    if (r.status === "closed") { return '<span class="tag">closed</span>'; }
    if (r.status === "undated") { return '<span class="tag">no dates</span>'; }
    return '<span class="tag">upcoming</span>';
  }

  function actions(r) {
    var mark = marks[r.id] || "";
    var out = [];
    out.push('<button class="act" data-mark="want" data-id="' + esc(r.id) +
      '" aria-pressed="' + (mark === "want") + '">' +
      '<span class="i">★</span>' + (mark === "want" ? "Saved" : "Save") +
      "</button>");
    out.push('<button class="act" data-mark="seen" data-id="' + esc(r.id) +
      '" aria-pressed="' + (mark === "seen") + '">' +
      '<span class="i">✓</span>Seen</button>');
    out.push('<button class="act skip" data-mark="skip" data-id="' + esc(r.id) +
      '" aria-pressed="' + (mark === "skip") + '">' +
      '<span class="i">×</span>Hide</button>');
    if (r.cal) {
      out.push('<a class="act" href="' + esc(r.cal) +
        '" target="_blank" rel="noopener"><span class="i">📅</span>' +
        esc(r.cal_label) + "</a>");
    }
    if (r.map) {
      out.push('<a class="act" href="' + esc(r.map) +
        '" target="_blank" rel="noopener"><span class="i">⌖</span>Directions</a>');
    }
    if (r.url) {
      out.push('<a class="act" href="' + esc(r.url) +
        '" target="_blank" rel="noopener"><span class="i">↗</span>Details</a>');
    }
    return '<div class="acts">' + out.join("") + "</div>";
  }

  function card(r) {
    var mark = marks[r.id] || "";
    var cls = "card" + (r.tier === 3 ? " t3" : "") +
      (r.status === "closing_soon" ? " urgent" : "") +
      (r._new ? " isnew" : "") + (mark === "seen" ? " done" : "");
    var out = ['<article class="' + cls + '">'];

    if (r.image) {
      // A gallery image that has moved should leave no empty box behind.
      out.push('<div class="shot"><img src="' + esc(r.image) +
        '" alt="" loading="lazy" decoding="async" ' +
        'onerror="this.parentNode.remove()"></div>');
    }
    var de = r.lang === "de" ? ' lang="de"' : "";
    out.push("<h2" + de + ">" + (r.url
      ? '<a href="' + esc(r.url) + '" target="_blank" rel="noopener">' +
        esc(r.title) + "</a>" : esc(r.title)) + "</h2>");
    if (r.artists && r.artists !== r.title) {
      out.push('<p class="who">' + esc(r.artists) + "</p>");
    }
    out.push('<div class="where"><span class="venue">' + esc(r.venue) +
      "</span>" + (r.city ? '<span class="when">' + esc(r.city) + "</span>" : "") +
      '<span class="when">' + esc(r.when) + "</span></div>");

    var tags = [];
    if (r._new) { tags.push('<span class="tag new">new</span>'); }
    tags.push(statusTag(r));
    if (r.tier === 3) { tags.push('<span class="tag med">sculpture</span>'); }
    else if (r.tier === 2) { tags.push('<span class="tag">painting/drawing</span>'); }
    else if (r.medium === "medium unknown") {
      tags.push('<span class="tag">no description</span>');
    }
    if (r.koenitz) { tags.push('<span class="tag med">Koenitz</span>'); }
    if (r.translated) {
      tags.push('<span class="tag" title="Machine translated from German">' +
        "translated</span>");
    }
    if (mark === "going") { tags.push('<span class="tag med">going</span>'); }
    out.push('<div class="tags">' + tags.join("") + "</div>");

    if (r.blurb) {
      out.push('<p class="blurb"' + (r.blurb_lang === "de" ? ' lang="de"' : "") +
        ">" + esc(r.blurb) + "</p>");
    }
    if (r.why) {
      out.push('<p class="why"><span class="sr-only">Classified from: ' +
        "</span>" + esc(r.why) + "</p>");
    }
    if (r.hours) {
      out.push('<div class="hours" lang="de"><span class="sr-only">' +
        "Opening hours: </span>" + esc(r.hours) + "</div>");
    }
    out.push(actions(r));
    out.push("</article>");
    return out.join("");
  }

  // ---- calls ----------------------------------------------------------
  function runwayTag(c) {
    var d = c.left;
    if (c.status === "rolling") { return '<span class="tag">no deadline</span>'; }
    if (d == null) { return '<span class="tag">deadline unclear</span>'; }
    if (d < 0) { return '<span class="tag">closed</span>'; }
    var text = d === 0 ? "closes today" : d === 1 ? "1 day left" : d + " days left";
    return '<span class="tag ' + (c.status === "closing" ? "urgent" : "live") +
      '">' + esc(text) + "</span>";
  }

  function stagePicker(c) {
    var current = apps[c.id] || "";
    var id = "st-" + c.id;
    var out = ['<label class="sr-only" for="' + esc(id) + '">' +
      "Application stage for " + esc(c.title) + "</label>",
      '<select class="stage" id="' + esc(id) + '" data-call="' + esc(c.id) + '">'];
    STAGES.forEach(function (row) {
      out.push('<option value="' + esc(row[0]) + '"' +
        (row[0] === current ? " selected" : "") + ">" + esc(row[1]) + "</option>");
    });
    out.push("</select>");
    return out.join("");
  }

  function callCard(c) {
    var stage = apps[c.id] || "";
    var cls = "card call" + (c.fit === "yes" ? " t3" : "") +
      (c.status === "closing" ? " urgent" : "") +
      (c._new ? " isnew" : "") + (stage === "submitted" ? " done" : "");
    var out = ['<article class="' + cls + '">'];

    out.push("<h2>" + (c.url
      ? '<a href="' + esc(c.url) + '" target="_blank" rel="noopener">' +
        esc(c.title) + "</a>" : esc(c.title)) + "</h2>");
    if (c.org) { out.push('<p class="who">' + esc(c.org) + "</p>"); }

    var where = [];
    if (c.place) { where.push('<span class="venue">' + esc(c.place) + "</span>"); }
    where.push('<span class="when">' + esc(c.when) + "</span>");
    out.push('<div class="where">' + where.join("") + "</div>");

    var tags = [];
    if (c._new) { tags.push('<span class="tag new">new</span>'); }
    tags.push(runwayTag(c));
    if (c.type) { tags.push('<span class="tag">' + esc(c.type) + "</span>"); }
    if (c.fit === "yes") { tags.push('<span class="tag med">sculpture</span>'); }
    if (c.fee === false) { tags.push('<span class="tag live">free</span>'); }
    else if (c.fee === true) {
      tags.push('<span class="tag urgent">' +
        esc(c.fee_note ? "fee " + c.fee_note : "entry fee") + "</span>");
    }
    if (c.elig === "closed") {
      tags.push('<span class="tag urgent">only ' +
        esc(c.open_to.join(", ")) + "</span>");
    }
    if (c.spec === "open to all") {
      tags.push('<span class="tag" title="This listing ticks every artistic ' +
        'field, so the sculpture tag means little">open to all fields</span>');
    }
    if (stage) { tags.push('<span class="tag med">' + esc(stage) + "</span>"); }
    out.push('<div class="tags">' + tags.join("") + "</div>");

    if (c.blurb) {
      out.push('<p class="blurb"' + (c.lang === "de" ? ' lang="de"' : "") + ">" +
        esc(c.blurb) + "</p>");
    }
    var foot = [];
    if (c.why) { foot.push(esc(c.why)); }
    if (c.requires && c.requires.length) {
      foot.push("wants " + esc(c.requires.join(", ")));
    }
    if (foot.length) {
      out.push('<p class="why">' + foot.join(" &middot; ") + "</p>");
    }

    var acts = [stagePicker(c)];
    if (c.cal) {
      acts.push('<a class="act" href="' + esc(c.cal) +
        '" target="_blank" rel="noopener"><span class="i">&#128197;</span>' +
        esc(c.cal_label) + "</a>");
    }
    if (c.url) {
      acts.push('<a class="act" href="' + esc(c.url) +
        '" target="_blank" rel="noopener"><span class="i">&#8599;</span>' +
        "Read the call</a>");
    }
    out.push('<div class="acts">' + acts.join("") + "</div>");
    out.push("</article>");
    return out.join("");
  }

  document.addEventListener("change", function (e) {
    var sel = e.target.closest ? e.target.closest("select.stage") : null;
    if (!sel) { return; }
    var id = sel.dataset.call, value = sel.value;
    if (value) { apps[id] = value; } else { delete apps[id]; }
    saveApps();
    var name = STAGES.filter(function (r) { return r[0] === value; })[0];
    announce(value ? "Marked " + name[1] : "No longer tracking this call");
    updateCallCount();
  });

  function updateCallCount() {
    var n = Object.keys(apps).length;
    var badge = document.getElementById("callcount");
    badge.textContent = n ? String(n) : "";
    badge.hidden = !n;
    badge.setAttribute("aria-label", n + " calls tracked");
  }

  // ---- map ------------------------------------------------------------
  var map = null, layer = null;

  function pinClass(r) {
    if (marks[r.id] === "want" || marks[r.id] === "going") { return "s-saved"; }
    if (r.status === "closing_soon") { return "s-closing"; }
    if (r.status === "opening_soon" || r.tier === 3) { return "s-open"; }
    return "s-other";
  }

  function popup(r) {
    var links = [];
    if (r.url) {
      links.push('<a href="' + esc(r.url) +
        '" target="_blank" rel="noopener">Details</a>');
    }
    if (r.cal) {
      links.push('<a href="' + esc(r.cal) +
        '" target="_blank" rel="noopener">' + esc(r.cal_label) + "</a>");
    }
    if (r.map) {
      links.push('<a href="' + esc(r.map) +
        '" target="_blank" rel="noopener">Directions</a>');
    }
    return "<h3>" + esc(r.title) + "</h3>" +
      '<p class="pv">' + esc(r.venue) +
      (r.city ? " &middot; " + esc(r.city) : "") + "<br>" + esc(r.when) + "</p>" +
      '<div class="pl">' + links.join("") + "</div>";
  }

  function drawMap(list) {
    var note = document.getElementById("mapnote");
    if (typeof L === "undefined") {
      note.textContent = "The map needs a connection to load its tiles. " +
        "The list works offline.";
      note.hidden = false;
      return;
    }
    if (!map) {
      map = L.map("map", { scrollWheelZoom: false });
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19, attribution: "&copy; OpenStreetMap"
      }).addTo(map);
      map.setView([52.0, 12.9], 7);
    }
    if (layer) { map.removeLayer(layer); }

    var placed = list.filter(function (r) { return r.lat && r.lng; });
    layer = L.layerGroup(placed.map(function (r) {
      var size = r.tier === 3 ? 16 : 12;
      return L.marker([r.lat, r.lng], {
        title: r.title,
        icon: L.divIcon({
          className: "",
          html: '<div class="pin ' + pinClass(r) + '" style="width:' + size +
            "px;height:" + size + 'px"></div>',
          iconSize: [size, size], iconAnchor: [size / 2, size / 2]
        })
      }).bindPopup(popup(r));
    })).addTo(map);

    if (placed.length) {
      map.fitBounds(L.latLngBounds(placed.map(function (r) {
        return [r.lat, r.lng];
      })).pad(0.15));
    }
    var missing = list.length - placed.length;
    note.textContent = missing
      ? placed.length + " of " + list.length + " placed; " + missing +
        " have no usable address."
      : placed.length + " placed. Filter by city to zoom in.";
    note.hidden = false;
    setTimeout(function () { map.invalidateSize(); }, 0);
  }

  // ---- render ---------------------------------------------------------
  function emptyMessage(list) {
    if (state.tab === "saved") {
      return "<b>Nothing saved yet.</b><br>Press Save on anything you might " +
        "want to see and it collects here.";
    }
    var elsewhere = rows.filter(textMatch).length;
    var msg = state.q
      ? "<b>No match for &ldquo;" + esc(state.q) + "&rdquo; in this view.</b>"
      : "<b>Nothing matches those filters.</b>";
    if (elsewhere) {
      msg += "<br>" + elsewhere +
        (elsewhere === 1 ? " exhibition matches" : " exhibitions match") +
        " if you widen them.";
    }
    return msg + '<br><br><button class="chip" id="clear2">Clear filters</button>';
  }

  var first = true;

  function plural(n, one, many) { return n + " " + (n === 1 ? one : many); }

  function render() {
    var isMap = state.tab === "map";
    var here = mode();
    var list = here === "calls" ? [] : sorted(rows.filter(matches));
    var callList = here === "shows" || isMap
      ? [] : sortedCalls(calls.filter(matchesCall));

    var shown;
    if (here === "calls") {
      shown = plural(callList.length, "call", "calls") + " of " + calls.length;
    } else if (here === "both") {
      shown = plural(list.length, "show", "shows") + " saved, " +
        plural(callList.length, "call", "calls") + " tracked";
    } else {
      shown = plural(list.length, "exhibition", "exhibitions") +
        " of " + rows.length;
    }
    document.getElementById("count").textContent = shown;
    if (pendingMessage) {
      announce(pendingMessage +
        (list.length !== lastAnnouncedCount ? ". " + shown + " shown" : ""));
      pendingMessage = null;
    } else if (!first && list.length !== lastAnnouncedCount) {
      announce(shown + " shown");
    }
    lastAnnouncedCount = list.length;
    first = false;
    document.getElementById("reset").hidden = !filtersActive() || here === "both";
    document.getElementById("map").hidden = !isMap;
    document.getElementById("mapnote").hidden = !isMap;
    document.getElementById("results").hidden = isMap;

    if (isMap) { drawMap(list); return; }

    var results = document.getElementById("results");
    var html = "";
    if (here === "calls") {
      html = callList.length ? callList.map(callCard).join("")
        : '<p class="empty">' + callEmpty() + "</p>";
    } else if (here === "both") {
      if (!list.length && !callList.length) {
        html = '<p class="empty">' + emptyMessage(list) + "</p>";
      } else {
        if (list.length) {
          html += '<h2 class="grouphead">' +
            plural(list.length, "show", "shows") + " saved</h2>" +
            list.map(card).join("");
        }
        if (callList.length) {
          html += '<h2 class="grouphead">' +
            plural(callList.length, "call", "calls") + " tracked</h2>" +
            callList.map(callCard).join("");
        }
      }
    } else if (list.length) {
      html = list.map(card).join("");
    } else {
      html = '<p class="empty">' + emptyMessage(list) + "</p>";
    }
    results.innerHTML = html;
    var c2 = document.getElementById("clear2");
    if (c2) { c2.addEventListener("click", clearFilters); }
  }

  function callEmpty() {
    if (!calls.length) {
      return "No open calls have been collected yet. Run the calls refresh " +
        "and reload.";
    }
    if (state.q) { return "No call matches &ldquo;" + esc(state.q) + "&rdquo;."; }
    var shut = state.canEnter &&
      calls.filter(function (c) { return c.elig === "closed"; }).length;
    return "Nothing matches these filters." +
      (shut ? " " + shut + " calls are hidden because their terms rule you out."
        : "") +
      ' <button class="chip" id="clear2">Clear filters</button>';
  }

  // ---- wiring ---------------------------------------------------------
  var tabs = [].slice.call(document.querySelectorAll("#nav [data-tab]"));

  function selectTab(name, moveFocus) {
    state.tab = name;
    tabs.forEach(function (t) {
      var on = t.dataset.tab === name;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;          // one stop for the whole tab strip
      if (on && moveFocus) { t.focus(); }
    });
    var isCalls = name === "calls";
    document.getElementById("controls").hidden = false;
    document.getElementById("show-filters").hidden = isCalls || name === "saved";
    document.getElementById("call-filters").hidden = !isCalls;
    document.getElementById("sort").hidden = isCalls;
    document.getElementById("csort").hidden = !isCalls;
    var q = document.getElementById("q");
    q.placeholder = isCalls ? "Residency, prize or place…"
      : "Artist, title or venue…";
    var panel = document.getElementById(name === "map" ? "map" : "results");
    panel.setAttribute("aria-labelledby", "tab-" + name);
    window.scrollTo(0, 0);
    render();
  }

  document.getElementById("nav").addEventListener("click", function (e) {
    var b = e.target.closest("[data-tab]");
    if (b) { selectTab(b.dataset.tab, false); }
  });

  // Arrow keys move between tabs, which is how a tab strip is expected to work.
  document.getElementById("nav").addEventListener("keydown", function (e) {
    var i = tabs.indexOf(document.activeElement);
    if (i < 0) { return; }
    var next = null;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") { next = (i + 1) % tabs.length; }
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") { next = (i - 1 + tabs.length) % tabs.length; }
    else if (e.key === "Home") { next = 0; }
    else if (e.key === "End") { next = tabs.length - 1; }
    if (next === null) { return; }
    e.preventDefault();
    selectTab(tabs[next].dataset.tab, true);
  });

  var q = document.getElementById("q");
  q.addEventListener("input", function () {
    state.q = q.value.trim().toLowerCase();
    render();
  });
  document.getElementById("sort").addEventListener("change", function (e) {
    state.sort = e.target.value;
    render();
  });
  document.getElementById("reset").addEventListener("click", clearFilters);

  document.addEventListener("keydown", function (e) {
    if (e.key === "/" && document.activeElement !== q) {
      e.preventDefault(); q.focus();
    }
  });

  function summarise() {
    // What is worth doing, in a sentence. The counts are still below for
    // anyone who wants them, but they are a poor thing to read first.
    var newCount = rows.filter(function (r) { return r._new; }).length;
    var sculpture = rows.filter(function (r) {
      return r.tier === 3 && (r.status === "opening_soon" ||
        r.status === "running" || r.status === "closing_soon");
    }).length;
    var closing = rows.filter(function (r) {
      return r.status === "closing_soon" && marks[r.id] !== "seen";
    }).length;

    var parts = [];
    if (sculpture) {
      parts.push("<b>" + sculpture + "</b> sculpture show" +
        (sculpture === 1 ? "" : "s") + " you can still catch");
    }
    if (closing) {
      parts.push("<b>" + closing + "</b> closing within a fortnight");
    }
    if (newCount) {
      parts.push("<b>" + newCount + "</b> new since you last looked");
    }
    // A call you can enter and have time to prepare for. Anything closing
    // this week is not one of those, so it is left out of the headline.
    var worth = calls.filter(function (c) {
      return c.fit === "yes" && c.elig !== "closed" && c.status === "soon";
    }).length;
    if (worth) {
      parts.push("<b>" + worth + "</b> open call" + (worth === 1 ? "" : "s") +
        " you could still enter");
    }
    document.getElementById("summary").innerHTML = parts.length
      ? parts.join(" &middot; ")
      : '<span class="none">Nothing new. ' + rows.length +
        " exhibitions are being tracked.</span>";
    document.getElementById("stamp").textContent = "Updated " + BUILT;
  }

  var csort = document.getElementById("csort");
  csort.addEventListener("change", function (e) {
    state.csort = e.target.value;
    render();
  });

  summarise();
  buildFilters();
  buildCallFilters();
  updateSavedCount();
  updateCallCount();
  if (!calls.length) { document.getElementById("tab-calls").hidden = true; }
  render();

  if ("serviceWorker" in navigator && location.protocol.indexOf("http") === 0) {
    navigator.serviceWorker.register("sw.js").catch(function () {});
  }
})();
</script>
</body>
</html>
"""


MANIFEST = {
    "name": "What's on - Leipzig & Berlin",
    "short_name": "What's on",
    "description": "Exhibitions on now in Leipzig, Berlin and around.",
    "start_url": "./index.html",
    "scope": "./",
    "display": "standalone",
    "orientation": "portrait-primary",
    "background_color": "#F3F3F1",
    "theme_color": "#2E6A5C",
    "icons": [
        {"src": "icon-192.png", "sizes": "192x192", "type": "image/png",
         "purpose": "any maskable"},
        {"src": "icon-512.png", "sizes": "512x512", "type": "image/png",
         "purpose": "any maskable"},
    ],
}

# Cache the shell so the list opens instantly and still works underground.
# Network first, falling back to the cache, so a rebuilt page is picked up on
# the next load rather than being pinned forever.
SERVICE_WORKER = """
const CACHE = 'whatson-v1';
const SHELL = ['./', './index.html', './manifest.webmanifest',
               './icon-192.png', './icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL))
    .then(() => self.skipWaiting()).catch(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k !== CACHE)
      .map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') { return; }
  const url = new URL(req.url);
  if (url.origin !== location.origin) { return; }   // tiles and fonts: as-is
  e.respondWith(
    fetch(req).then(res => {
      const copy = res.clone();
      caches.open(CACHE).then(c => c.put(req, copy)).catch(() => {});
      return res;
    }).catch(() => caches.match(req).then(hit => hit ||
      caches.match('./index.html')))
  );
});
"""


def _png(size, background, mark):
    """A square PNG icon, written by hand to avoid an image dependency.

    The app should have a real icon on a home screen, and pulling in Pillow for
    two flat squares would be a poor trade.
    """
    import struct
    import zlib

    bg = background
    fg = mark
    rows = []
    inset = size // 4
    bar = max(2, size // 16)
    for y in range(size):
        row = bytearray([0])            # PNG filter byte: none
        for x in range(size):
            # A plinth: a solid block with a wider base, reading as a sculpture
            # stand at any size.
            on_base = y >= size - inset and inset // 2 <= x < size - inset // 2
            on_body = (inset <= x < size - inset
                       and inset // 2 <= y < size - inset - bar)
            row += bytes(fg if (on_base or on_body) else bg)
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def _ico(png_bytes, size):
    """Wrap a PNG in an ICO container, which Windows shortcuts need.

    Since Vista an .ico may hold PNG data directly, so this is a header and a
    directory entry rather than a re-encode.
    """
    import struct
    header = struct.pack("<HHH", 0, 1, 1)              # reserved, type=icon, count
    entry = struct.pack("<BBBBHHII",
                        size if size < 256 else 0,
                        size if size < 256 else 0,
                        0, 0, 1, 32, len(png_bytes), 6 + 16)
    return header + entry + png_bytes


def write_pwa(directory=None):
    """Write the manifest, service worker and icons beside the page."""
    directory = directory or HERE
    with open(os.path.join(directory, "manifest.webmanifest"), "w",
              encoding="utf-8") as fh:
        json.dump(MANIFEST, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    with open(os.path.join(directory, "sw.js"), "w", encoding="utf-8") as fh:
        fh.write(SERVICE_WORKER.lstrip())
    icons = {}
    for size in (192, 512):
        icons[size] = _png(size, (46, 106, 92), (243, 243, 241))
        with open(os.path.join(directory, "icon-%d.png" % size), "wb") as fh:
            fh.write(icons[size])
    # A desktop shortcut needs an .ico; browsers and phones do not.
    with open(os.path.join(directory, "icon.ico"), "wb") as fh:
        fh.write(_ico(icons[192], 192))
    return 5


def _island(rows):
    """JSON safe to sit inside a script tag."""
    return json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")


def render(state, today=None, calls_inventory=None):
    """The complete self-contained board page."""
    rows = build_rows(state, today)
    call_rows = build_call_rows(
        load_calls() if calls_inventory is None else calls_inventory, today)
    updated = (today or date.today())
    stamp = "%d %s %d" % (updated.day, MONTHS[updated.month - 1], updated.year)
    return (PAGE
            .replace("__DATA__", _island(rows))
            .replace("__CALLS__", _island(call_rows))
            .replace("__BUILT__", stamp))


def main(argv=None):
    """Write board.html from the inventory."""
    parser = argparse.ArgumentParser(description="Render the inventory board")
    parser.add_argument("--state", default=state_mod.STATE_PATH)
    parser.add_argument("--out", default=OUTPUT)
    args = parser.parse_args(argv)

    st = state_mod.load(args.state)
    inventory = load_calls()
    write_pwa(os.path.dirname(os.path.abspath(args.out)) or HERE)
    page = render(st, calls_inventory=inventory)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(page)
    print("wrote %s (%d shows, %d calls, %d KB)"
          % (args.out, len(st["events"]),
             len(build_call_rows(inventory)), len(page) // 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
