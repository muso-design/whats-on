"""Render the inventory as a single self-contained HTML page.

The email answers "what is new". This answers "what are my options" - every
show being tracked, filterable by status, city and medium, sorted by relevance
or by how soon it closes. No server, no build step: one file, opened from disk
or from a phone.
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
  --ink:#1A1D1B; --muted:#6B716C; --line:#DBDCD8;
  --accent:#2E6A5C; --accent-soft:#E2EDE9;
  --urgent:#A8482A; --urgent-soft:#F6E7E1;
  --new:#8A6D1F; --new-soft:#F5EEDB;
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
  }
}
:root[data-theme="dark"]{
  --ground:#121512; --surface:#1A1E1B; --sunk:#161A17; --raise:#222724;
  --ink:#E6E9E6; --muted:#98A099; --line:#2C312D;
  --accent:#74B9A4; --accent-soft:#1D2B27;
  --urgent:#D5764F; --urgent-soft:#2E211B;
  --new:#D6B45C; --new-soft:#2A2418;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--ground);color:var(--ink);
  font-family:var(--sans);font-size:15px;line-height:1.5;
  -webkit-font-smoothing:antialiased;overscroll-behavior-y:none}
button{font:inherit;color:inherit}
a{color:var(--accent)}

.wrap{max-width:960px;margin:0 auto;
  padding:0 14px calc(84px + var(--safe-b))}

/* ---------- header ---------- */
.top{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;
  padding:14px 2px 8px}
h1{font-size:19px;font-weight:600;letter-spacing:-.02em;margin:0}
.stamp{font-family:var(--mono);font-size:11.5px;color:var(--muted)}

/* ---------- controls ---------- */
.controls{position:sticky;top:0;z-index:20;background:var(--ground);
  padding:8px 0 9px;border-bottom:1px solid var(--line)}
.searchrow{display:flex;gap:7px}
input[type=search]{flex:1 1 auto;min-width:0;font:inherit;color:var(--ink);
  background:var(--surface);border:1px solid var(--line);border-radius:9px;
  padding:0 12px;height:var(--tap);-webkit-appearance:none}
input[type=search]:focus-visible,select:focus-visible,button:focus-visible{
  outline:2px solid var(--accent);outline-offset:1px}
select{font:inherit;color:var(--ink);background:var(--surface);
  border:1px solid var(--line);border-radius:9px;padding:0 8px;height:var(--tap);
  max-width:44%}

.filters{display:flex;flex-direction:column;gap:4px;margin-top:7px}
.row{display:flex;gap:6px;overflow-x:auto;scrollbar-width:none;
  padding-bottom:2px;-webkit-overflow-scrolling:touch}
.row::-webkit-scrollbar{display:none}
.chip{font-family:var(--mono);font-size:11.5px;background:var(--surface);
  color:var(--muted);border:1px solid var(--line);border-radius:999px;
  padding:0 12px;height:32px;display:inline-flex;align-items:center;gap:5px;
  cursor:pointer;white-space:nowrap;flex:0 0 auto}
.chip .c{opacity:.6;font-size:10.5px}
.chip[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);
  color:#fff}
.chip.urgent[aria-pressed="true"]{background:var(--urgent);border-color:var(--urgent)}
.chip.new[aria-pressed="true"]{background:var(--new);border-color:var(--new)}
.chip.reset{border-style:dashed}

/* ---------- results ---------- */
.count{font-family:var(--mono);font-size:11.5px;color:var(--muted);
  padding:9px 2px 7px;display:flex;justify-content:space-between;gap:10px;
  align-items:center}
.card{background:var(--surface);border:1px solid var(--line);
  border-left:3px solid var(--line);border-radius:10px;
  padding:13px 14px;margin-bottom:9px}
.card.t3{border-left-color:var(--accent)}
.card.urgent{border-left-color:var(--urgent)}
.card.isnew{border-left-color:var(--new)}
.card.done{opacity:.55}
.card h2{font-size:16px;font-weight:600;margin:0 0 2px;letter-spacing:-.01em;
  line-height:1.3}
.card h2 a{color:inherit;text-decoration:none}
.who{color:var(--muted);font-size:14px;margin:0 0 7px}
.where{display:flex;flex-wrap:wrap;gap:3px 10px;align-items:baseline;
  font-size:13.5px;margin-bottom:8px}
.venue{font-weight:500}
.when{font-family:var(--mono);font-size:12px;color:var(--muted)}
.tags{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:8px}
.tag{font-family:var(--mono);font-size:10px;letter-spacing:.03em;
  text-transform:uppercase;padding:3px 7px;border-radius:4px;
  background:var(--sunk);color:var(--muted)}
.tag.live{background:var(--accent-soft);color:var(--accent)}
.tag.urgent{background:var(--urgent-soft);color:var(--urgent)}
.tag.med{background:var(--accent-soft);color:var(--accent)}
.tag.new{background:var(--new-soft);color:var(--new)}
.blurb{font-size:13.5px;color:var(--muted);margin:0 0 6px}
.why{font-family:var(--mono);font-size:10.5px;color:var(--muted);opacity:.75;
  margin:0 0 9px}
.shot{float:right;width:96px;height:96px;margin:0 0 8px 12px;border-radius:8px;
  overflow:hidden;background:var(--sunk)}
.shot img{width:100%;height:100%;object-fit:cover;display:block}
.card::after{content:"";display:block;clear:both}
@media (max-width:520px){.shot{width:74px;height:74px;margin-left:10px}}
.hours{font-family:var(--mono);font-size:11px;color:var(--muted);
  margin:0 0 9px;padding-top:7px;border-top:1px dashed var(--line)}

/* actions: big enough for a thumb */
.acts{display:flex;flex-wrap:wrap;gap:6px}
.act{background:var(--surface);border:1px solid var(--line);border-radius:8px;
  height:40px;padding:0 12px;display:inline-flex;align-items:center;gap:6px;
  font-size:12.5px;cursor:pointer;text-decoration:none;color:var(--ink)}
@media (pointer:coarse){.act{height:var(--tap)}}
.act:hover{border-color:var(--muted)}
.act[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);
  color:#fff}
.act.skip[aria-pressed="true"]{background:var(--muted);border-color:var(--muted)}
.act .i{font-size:13px;line-height:1}

.empty{text-align:center;color:var(--muted);padding:44px 18px;
  border:1px dashed var(--line);border-radius:10px}
.empty b{color:var(--ink);font-weight:600}

/* ---------- map ---------- */
#map{height:calc(100vh - 320px);min-height:320px;border:1px solid var(--line);
  border-radius:10px;margin-bottom:8px;background:var(--sunk)}
.mapnote{font-family:var(--mono);font-size:11px;color:var(--muted);margin:0 0 12px}
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
  gap:3px;color:var(--muted);font-size:11px;font-family:var(--mono)}
.nav button[aria-selected="true"]{color:var(--accent)}
.nav .g{font-size:17px;line-height:1}
.nav .badge{position:absolute;transform:translate(15px,-13px);
  background:var(--new);color:#fff;border-radius:999px;font-size:9.5px;
  min-width:16px;height:16px;display:flex;align-items:center;
  justify-content:center;padding:0 4px}

/* ---------- desktop ---------- */
@media (min-width:760px){
  .wrap{padding-bottom:40px}
  .nav{position:static;border:1px solid var(--line);border-radius:10px;
    margin:14px 0 0;padding:0;max-width:420px}
  .nav button{height:44px;flex-direction:row;gap:7px;font-size:12.5px}
  #map{height:min(64vh,540px)}
  .filters{flex-direction:row;flex-wrap:wrap;gap:6px}
  .row{overflow:visible;flex-wrap:wrap}
}
</style>
</head>
<body>
<div class="wrap">

  <header class="top">
    <h1>What&rsquo;s on</h1>
    <span class="stamp" id="stamp"></span>
  </header>

  <nav class="nav" id="nav" role="tablist">
    <button role="tab" aria-selected="true" data-tab="browse">
      <span class="g">◍</span><span>Browse</span></button>
    <button role="tab" aria-selected="false" data-tab="saved">
      <span class="g">★</span><span>Saved</span>
      <span class="badge" id="savedcount" hidden></span></button>
    <button role="tab" aria-selected="false" data-tab="map">
      <span class="g">⌖</span><span>Map</span></button>
  </nav>

  <div class="controls">
    <div class="searchrow">
      <input type="search" id="q" placeholder="Artist, title or venue…"
             aria-label="Search" autocomplete="off">
      <select id="sort" aria-label="Sort by">
        <option value="rank">Most relevant</option>
        <option value="soon">Opening soonest</option>
        <option value="closing">Closing soonest</option>
      </select>
    </div>
    <div class="filters">
      <div class="row" id="f-status"></div>
      <div class="row" id="f-city"></div>
      <div class="row" id="f-medium"></div>
    </div>
  </div>

  <div class="count">
    <span id="count"></span>
    <button class="chip reset" id="reset" hidden>Clear filters</button>
  </div>

  <div id="map" hidden></div>
  <p class="mapnote" id="mapnote" hidden></p>
  <div id="results"></div>

</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script id="data" type="application/json">__DATA__</script>
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
    onlyNew: false,
    status: new Set(["closing_soon", "opening_soon"]),
    city: new Set(),
    medium: new Set()
  };

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
  function chip(label, value, count, group, extra) {
    var b = document.createElement("button");
    b.className = "chip " + (extra || "");
    b.type = "button";
    b.dataset.group = group;
    b.dataset.value = value;
    b.setAttribute("aria-pressed",
      group === "new" ? String(state.onlyNew) :
        String(state[group].has(value)));
    b.innerHTML = esc(label) +
      (count != null ? '<span class="c">' + count + "</span>" : "");
    b.addEventListener("click", function () {
      if (group === "new") { state.onlyNew = !state.onlyNew; }
      else if (state[group].has(value)) { state[group].delete(value); }
      else { state[group].add(value); }
      b.setAttribute("aria-pressed",
        group === "new" ? String(state.onlyNew) :
          String(state[group].has(value)));
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

  function filtersActive() {
    return state.status.size || state.city.size || state.medium.size ||
      state.onlyNew;
  }

  function clearFilters() {
    state.status.clear(); state.city.clear(); state.medium.clear();
    state.onlyNew = false;
    document.querySelectorAll(".chip[aria-pressed]").forEach(function (b) {
      b.setAttribute("aria-pressed", "false");
    });
    render();
  }

  // ---- marking --------------------------------------------------------
  function setMark(id, value) {
    if (marks[id] === value) { delete marks[id]; }
    else { marks[id] = value; }
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
    out.push("<h2>" + (r.url
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

    if (r.blurb) { out.push('<p class="blurb">' + esc(r.blurb) + "</p>"); }
    if (r.why) {
      out.push('<p class="why" title="How the medium was decided">' +
        esc(r.why) + "</p>");
    }
    if (r.hours) { out.push('<div class="hours">' + esc(r.hours) + "</div>"); }
    out.push(actions(r));
    out.push("</article>");
    return out.join("");
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

  function render() {
    var list = sorted(rows.filter(matches));
    var isMap = state.tab === "map";

    document.getElementById("count").textContent =
      list.length + (list.length === 1 ? " exhibition" : " exhibitions") +
      (state.tab === "saved" ? " saved" : " of " + rows.length);
    document.getElementById("reset").hidden = !filtersActive() || state.tab === "saved";
    document.getElementById("map").hidden = !isMap;
    document.getElementById("mapnote").hidden = !isMap;
    document.getElementById("results").hidden = isMap;

    if (isMap) { drawMap(list); return; }

    var results = document.getElementById("results");
    if (list.length) {
      results.innerHTML = list.map(card).join("");
    } else {
      results.innerHTML = '<p class="empty">' + emptyMessage(list) + "</p>";
      var c2 = document.getElementById("clear2");
      if (c2) { c2.addEventListener("click", clearFilters); }
    }
  }

  // ---- wiring ---------------------------------------------------------
  document.getElementById("nav").addEventListener("click", function (e) {
    var b = e.target.closest("[data-tab]");
    if (!b) { return; }
    state.tab = b.dataset.tab;
    document.querySelectorAll("#nav [data-tab]").forEach(function (x) {
      x.setAttribute("aria-selected", String(x.dataset.tab === state.tab));
    });
    document.querySelector(".controls").style.display =
      state.tab === "saved" ? "none" : "";
    window.scrollTo(0, 0);
    render();
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

  var newCount = rows.filter(function (r) { return r._new; }).length;
  document.getElementById("stamp").textContent =
    "updated " + BUILT + (newCount ? " \\u00b7 " + newCount + " new" : "");

  buildFilters();
  updateSavedCount();
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


def write_pwa(directory=None):
    """Write the manifest, service worker and icons beside the page."""
    directory = directory or HERE
    with open(os.path.join(directory, "manifest.webmanifest"), "w",
              encoding="utf-8") as fh:
        json.dump(MANIFEST, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    with open(os.path.join(directory, "sw.js"), "w", encoding="utf-8") as fh:
        fh.write(SERVICE_WORKER.lstrip())
    for size in (192, 512):
        with open(os.path.join(directory, "icon-%d.png" % size), "wb") as fh:
            fh.write(_png(size, (46, 106, 92), (243, 243, 241)))
    return 4


def render(state, today=None):
    """The complete self-contained board page."""
    rows = build_rows(state, today)
    updated = (today or date.today())
    stamp = "%d %s %d" % (updated.day, MONTHS[updated.month - 1], updated.year)
    return (PAGE
            .replace("__DATA__", json.dumps(rows, ensure_ascii=False)
                     .replace("</", "<\\/"))
            .replace("__BUILT__", stamp))


def main(argv=None):
    """Write board.html from the inventory."""
    parser = argparse.ArgumentParser(description="Render the inventory board")
    parser.add_argument("--state", default=state_mod.STATE_PATH)
    parser.add_argument("--out", default=OUTPUT)
    args = parser.parse_args(argv)

    st = state_mod.load(args.state)
    write_pwa(os.path.dirname(os.path.abspath(args.out)) or HERE)
    page = render(st)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(page)
    print("wrote %s (%d shows, %d KB)"
          % (args.out, len(st["events"]), len(page) // 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
