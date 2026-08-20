"""The inventory: every show we have ever seen, in state.json.

This used to be a list of ids to suppress. It is now a record of what is on,
because the question changed from "what is new this week" to "what are my
options". Nothing is deleted while it is still running; status is derived from
the run dates on every pass rather than remembered.

The file is committed to the repo after each run, so there is no database.
"""

import json
import os
from datetime import date, datetime, timedelta

STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
RETENTION_DAYS = 365          # keep closed shows this long, then forget them
CLOSING_SOON_DAYS = 14        # "catch it before it goes"
OPENING_SOON_DAYS = 21

# Fields worth carrying in the inventory. Descriptions are kept because the
# board shows them and re-scraping to rebuild the page would be wasteful.
KEEP_FIELDS = (
    "title", "artists", "venue", "venue_slug", "city", "address",
    "lat", "lng", "opening_hours", "image", "event_type", "category",
    "language",
    "description_en",
    "vernissage_datetime", "exhibition_start", "exhibition_end",
    "source", "source_url", "raw_description",
    "medium_tier", "medium_confidence", "medium_source", "artist_medium",
    "artist_evidence", "koenitz_override", "corroborated",
    "matched_keywords",
    "rank", "in_default_view",
)


def load(path=STATE_PATH):
    """Read the inventory, or an empty one if this is the first run."""
    if not os.path.exists(path):
        return {"events": {}, "last_run": None}
    with open(path, encoding="utf-8") as fh:
        state = json.load(fh)
    state.setdefault("events", {})
    state.setdefault("last_run", None)
    return state


def save(state, path=STATE_PATH):
    """Write the inventory back, stamping the run time."""
    state["last_run"] = datetime.now().isoformat(timespec="seconds")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")


def _as_date(stamp):
    try:
        return date.fromisoformat((stamp or "")[:10])
    except ValueError:
        return None


def status_of(event, today=None):
    """upcoming | opening_soon | running | closing_soon | closed | undated."""
    today = today or date.today()
    start = _as_date(event.get("vernissage_datetime")) or _as_date(
        event.get("exhibition_start"))
    end = _as_date(event.get("exhibition_end"))

    if end and end < today:
        return "closed"
    if start and start > today:
        days = (start - today).days
        return "opening_soon" if days <= OPENING_SOON_DAYS else "upcoming"
    if end:
        return "closing_soon" if (end - today).days <= CLOSING_SOON_DAYS else "running"
    if start:
        return "running"
    return "undated"


def merge(state, events, today=None):
    """Fold this run's scrape into the inventory.

    Returns the events that were not in the inventory before. Everything else
    is updated in place - dates move, descriptions improve, a second source
    turns up - but nothing is dropped for being old news.
    """
    known = state["events"]
    now = datetime.now().isoformat(timespec="seconds")
    fresh, seen_this_run = [], set()

    for ev in events:
        eid = ev["id"]
        if eid in seen_this_run:
            continue                      # a source listed the same show twice
        seen_this_run.add(eid)

        record = {k: ev.get(k) for k in KEEP_FIELDS}
        record["status"] = status_of(ev, today)
        record["last_seen"] = now

        existing = known.get(eid)
        if existing:
            record["first_seen"] = existing.get("first_seen", now)
            record["notified"] = existing.get("notified", False)
            # Keep every source that has mentioned this show.
            sources = set(existing.get("sources") or [existing.get("source")])
            sources.add(ev.get("source"))
            record["sources"] = sorted(s for s in sources if s)
            known[eid] = record
        else:
            record["first_seen"] = now
            record["notified"] = False
            record["sources"] = [ev.get("source")] if ev.get("source") else []
            known[eid] = record
            ev["first_seen"] = now
            ev["notified"] = False
            fresh.append(ev)

    # Shows that no source listed this run keep their record, but their status
    # is recomputed so a run that quietly ended is still marked closed.
    for eid, record in known.items():
        if eid not in seen_this_run:
            record["status"] = status_of(record, today)

    return fresh


def inventory(state, status=None, city=None):
    """Records from the inventory, optionally filtered."""
    out = []
    for eid, record in state["events"].items():
        if status and record.get("status") not in (
                status if isinstance(status, (list, tuple, set)) else [status]):
            continue
        if city and record.get("city") != city:
            continue
        item = dict(record)
        item["id"] = eid
        out.append(item)
    return out


def counts(state):
    """How many shows sit in each status - the header line of the digest."""
    tally = {}
    for record in state["events"].values():
        tally[record.get("status", "undated")] = tally.get(
            record.get("status", "undated"), 0) + 1
    return tally


def mark_notified(state, events):
    """Record that these shows have gone out in an email."""
    for ev in events:
        record = state["events"].get(ev["id"])
        if record:
            record["notified"] = True
        ev["notified"] = True


def prune(state, retention_days=RETENTION_DAYS, today=None):
    """Forget shows that closed long ago. Running shows are never pruned."""
    today = today or date.today()
    cutoff = (today - timedelta(days=retention_days)).isoformat()
    stale = [eid for eid, record in state["events"].items()
             if record.get("status") == "closed"
             and (record.get("exhibition_end") or record.get("last_seen") or "")[:10] < cutoff]
    for eid in stale:
        del state["events"][eid]
    return len(stale)
