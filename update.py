"""Refresh both inventories, then rebuild the hub page.

Scrape every source, merge the duplicates, fill in coordinates and English
descriptions, score, and fold the result into state.json. Then do the same for
open calls, which have their own sources, their own clock and their own file.
There is no email: the hub is the product, and "what is new" is answered on the
page against what you have already seen.

One command, because two commands is one you will forget to run.
"""

import argparse
import json
import sys

import scoring
import state as state_mod

# Nominatim asks for one call a second, so cap what a single run will do.
# New venues are rare; the rest wait for the next run.
GEOCODE_BUDGET = 25


# Wikidata throttles hard, so cap new artist lookups per run. They are cached
# for good, so after the first backfill only new names cost anything.
ARTIST_BUDGET = 120

# Gallery sites read directly per run. Curated venues always go first, and an
# unchanged page costs nothing, so this caps only genuinely new reading.
DIRECT_BUDGET = 12


def refresh(raw_events, st, translate=True, geocode=True, artists=True,
            network=True, verbose=True):
    """Fold a scrape into the inventory. Returns the shows that are new."""
    merged = scoring.merge_duplicates(raw_events)

    if geocode:
        import geocode as geocode_mod
        cache, _ = geocode_mod.locate(merged, allow_network=network,
                                      budget=GEOCODE_BUDGET, verbose=verbose)
        if network:
            geocode_mod.save_cache(cache)

    if translate:
        import translate as translate_mod
        cache, _ = translate_mod.enrich(merged, allow_network=network,
                                        verbose=verbose)
        if network:
            translate_mod.save_cache(cache)

    if artists:
        import wikidata
        cache, _ = wikidata.enrich(merged, allow_network=network,
                                   budget=ARTIST_BUDGET, verbose=verbose)
        if network:
            wikidata.save_cache(cache)

    scored = scoring.score_all(merged, merge=False)
    if verbose:
        shown = len(scoring.default_view(scored))
        print("  scored %d shows, %d in the default view" % (len(scored), shown))

    return state_mod.merge(st, scored)


def main(argv=None):
    """Scrape, enrich, score and store. Then rebuild the page."""
    parser = argparse.ArgumentParser(
        description="Refresh what's on and rebuild the hub")
    parser.add_argument("--from-file", metavar="PATH",
                        help="use a saved scrape instead of hitting the network")
    parser.add_argument("--no-translate", action="store_true")
    parser.add_argument("--no-geocode", action="store_true")
    parser.add_argument("--no-artists", action="store_true",
                        help="skip resolving artists against Wikidata")
    parser.add_argument("--no-direct", action="store_true",
                        help="skip reading gallery sites with the local model")
    parser.add_argument("--no-calls", action="store_true",
                        help="skip refreshing open calls")
    parser.add_argument("--no-build", action="store_true",
                        help="update the inventory but do not rebuild the page")
    parser.add_argument("--state", default=state_mod.STATE_PATH)
    args = parser.parse_args(argv)

    if args.from_file:
        with open(args.from_file, encoding="utf-8") as fh:
            raw_events = json.load(fh)
        print("loaded %d events from %s" % (len(raw_events), args.from_file))
    else:
        import scraper
        print("fetching...")
        raw_events = scraper.scrape_all()
        print("  %d listings" % len(raw_events))
        if not args.no_direct:
            import direct
            raw_events.extend(direct.scrape_direct(budget=DIRECT_BUDGET))

    st = state_mod.load(args.state)
    fresh = refresh(raw_events, st,
                    translate=not args.no_translate,
                    geocode=not args.no_geocode,
                    artists=not args.no_artists)

    removed = state_mod.prune(st)
    state_mod.save(st, args.state)
    tally = state_mod.counts(st)
    print("  %d new since the last run" % len(fresh))
    print("inventory: %d shows (%s)%s"
          % (len(st["events"]),
             ", ".join("%s %d" % (k.replace("_", " "), v)
                       for k, v in sorted(tally.items())),
             ", %d pruned" % removed if removed else ""))

    if not args.no_calls and not args.from_file:
        print("open calls...")
        import calls as calls_mod
        inventory, new_calls = calls_mod.refresh(
            translate=not args.no_translate)
        dropped = calls_mod.prune(inventory)
        calls_mod.save(inventory)
        tally = calls_mod.counts(inventory)
        print("  %d new since the last run" % len(new_calls))
        print("calls: %d (%s)%s"
              % (len(inventory["calls"]),
                 ", ".join("%s %d" % kv for kv in sorted(tally.items())),
                 ", %d pruned" % dropped if dropped else ""))

    if not args.no_build:
        import board
        board.main(["--state", args.state])
    return 0


if __name__ == "__main__":
    sys.exit(main())
