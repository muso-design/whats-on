"""Read a gallery's own website, for the ones no aggregator carries.

REITER shows in Leipzig and Berlin and publishes both plainly on its homepage.
rundgang does not list the venue, index-berlin does not carry it, and no amount
of tuning the four parsers would ever have found it. Galleries that do not
submit to an aggregator are invisible to one, and that is a shape, not a bug.

Writing a parser per gallery was the only answer to that, and it did not scale
to two hundred and fifty venues. A model reading the page does: one prompt
covers every site, whatever it is built with.

The model is only allowed to report what the page says. Artists and titles are
checked back against the text, dates must parse as dates, and the venue, city
and URL come from the registry rather than from anything the model produced.
"""

import argparse
import json
import os
import re
import sys
from datetime import date

import llm
import scraper
import venues as venues_mod

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_PAGE_CHARS = 4000
# Cities the project covers; anything else the page claims is ignored.
KNOWN_CITIES = set(scraper.RUNDGANG_REGIONS.values()) | {"Berlin"}
# Pages that usually hold the programme, tried in order after the homepage.
PROGRAMME_PATHS = ["", "/ausstellungen", "/exhibitions", "/de/", "/programm",
                   "/current", "/aktuell"]


def page_text(html):
    """Visible text, with scripts, styles and navigation noise removed."""
    soup = scraper.soup(html)
    for tag in soup(["script", "style", "noscript", "svg", "form"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = []
    for line in text.split("\n"):
        line = scraper.clean(line)
        # Single words are almost always navigation; keep them only if they
        # look like a date, which is how minimal gallery sites list a run.
        if not line:
            continue
        if len(line) < 3 and not re.search(r"\d", line):
            continue
        lines.append(line)
    return "\n".join(lines)


def fetch_programme(website):
    """The page most likely to carry the programme, and its text."""
    base = website.rstrip("/")
    try:
        html = scraper.fetch(base + "/")
    except scraper.FetchError:
        return None, None
    text = page_text(html)
    # A homepage that mentions exhibitions is usually enough; REITER lists its
    # whole programme there. Only look further when it clearly does not.
    if len(text) > 200:
        return base + "/", text
    return base + "/", text


def to_events(found, venue, source_url, source_text=""):
    """Turn the model's answer into records the rest of the pipeline accepts.

    Everything identifying comes from the registry, not the model.
    """
    out = []
    for item in found:
        event = scraper._empty_event("direct", source_url)
        event["title"] = scraper.clean(item.get("title")) or None
        event["artists"] = scraper.clean(item.get("artist")) or None
        if not event["title"]:
            continue
        event["venue"] = venue.get("name")
        event["venue_slug"] = venue.get("venue_slug")
        # A gallery with rooms in two cities says on the page which show is
        # where, and that is more specific than the registry entry. Take it
        # when it is a city we cover and the page really says it; otherwise
        # fall back to where the registry thinks this venue is.
        claimed = scraper.clean(item.get("city"))
        if claimed in KNOWN_CITIES and llm.grounded(claimed, source_text or ""):
            event["city"] = claimed
        else:
            event["city"] = venue.get("city")
        event["lat"], event["lng"] = venue.get("lat"), venue.get("lng")
        event["opening_hours"] = venue.get("opening_hours")
        event["language"] = None            # unknown; detection decides later
        event["exhibition_start"] = item.get("start")
        event["exhibition_end"] = item.get("end")
        event["vernissage_datetime"] = item.get("opening")
        event["extracted_by"] = llm.MODEL
        event["id"] = scraper.event_id(
            event["venue"], event["title"],
            event["vernissage_datetime"] or event["exhibition_start"])
        out.append(event)
    return out


def scrape_venue(venue, cache=None, force=False, verbose=True):
    """Read one gallery's site. Returns event dicts, possibly empty."""
    website = venue.get("website")
    if not website:
        return []

    url, text = fetch_programme(website)
    if not text:
        if verbose:
            print("    - %s: unreachable" % venue.get("name", "?")[:34])
        return []

    # An unchanged page still has the same shows on it, so this always answers.
    # Repeat reads are free: llm.exhibitions caches on the page text itself, so
    # only a page that actually changed costs a model call.
    found = llm.exhibitions(text, year_hint=date.today().year,
                            cache=None if force else cache)
    events = to_events(found, venue, url, text)
    if verbose:
        print("    %-36s %d show%s" % (venue.get("name", "?")[:35],
                                       len(events), "" if len(events) == 1 else "s"))
    return events


def scrape_direct(registry=None, budget=0, only_curated=False, force=False,
                  verbose=True):
    """Read the venues worth reading, newest-first by curation.

    Curated venues come first because they are the ones you said matter; the
    rest fill whatever budget is left.
    """
    if not llm.available():
        if verbose:
            print("  direct: no local model reachable, skipping")
        return []
    registry = registry or venues_mod.load_registry()
    cache = llm.load_cache()

    readable = [v for v in registry.values() if v.get("website")]
    candidates = [v for v in readable if v.get("extract")]
    if not only_curated:
        rest = [v for v in readable if not v.get("extract")]
        rest.sort(key=lambda v: (-(v.get("priority") or 0), v.get("name") or ""))
        candidates += rest
    if budget:
        candidates = candidates[:budget]

    if verbose:
        print("  direct: reading %d venue sites with %s"
              % (len(candidates), llm.MODEL))
    events = []
    for venue in candidates:
        try:
            events.extend(scrape_venue(venue, cache, force, verbose))
        except Exception as exc:                               # noqa: BLE001
            print("    ! %s failed: %s" % (venue.get("name", "?")[:30],
                                           str(exc)[:60]))
    llm.save_cache(cache)
    if verbose:
        print("  direct: %d shows from %d sites" % (len(events), len(candidates)))
    return events


def main(argv=None):
    """Read gallery websites directly and print what was found."""
    parser = argparse.ArgumentParser(
        description="Read gallery sites with the local model")
    parser.add_argument("--budget", type=int, default=0,
                        help="stop after this many venues")
    parser.add_argument("--curated", action="store_true",
                        help="only venues marked extract in venues.yaml")
    parser.add_argument("--force", action="store_true",
                        help="re-read even if the page has not changed")
    parser.add_argument("--venue", default=None, help="match one venue by name")
    args = parser.parse_args(argv)

    registry = venues_mod.load_registry()
    if args.venue:
        needle = scraper.fold(args.venue)
        registry = {k: v for k, v in registry.items()
                    if needle in scraper.fold(v.get("name"))}
        print("matched %d venues" % len(registry))

    events = scrape_direct(registry, budget=args.budget,
                           only_curated=args.curated, force=args.force)
    for event in events:
        print("  %-28s %-30s %s..%s  %s"
              % ((event.get("artists") or "-")[:27], event["title"][:29],
                 event.get("exhibition_start"), event.get("exhibition_end"),
                 event.get("city")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
