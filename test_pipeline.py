"""End-to-end check of the refresh loop against a scratch inventory.

Scoring, merging, the inventory diff and the page build all run for real.
Network-bound enrichment (translation, geocoding) is switched off so the test
is deterministic.

Run: python test_pipeline.py
"""

import json
import os
import tempfile

import board
import state as state_mod
import update

FAILURES = []


def check(name, got, want):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s: got %r, want %r" % (name, got, want))
        FAILURES.append(name)


sample = "sample_events.json"
if not os.path.exists(sample):
    print("sample_events.json missing. Regenerate it with:\n"
          "  python -c \"import json,scraper;"
          "json.dump(scraper.scrape_all(),open('sample_events.json','w',"
          "encoding='utf-8'),ensure_ascii=False,indent=1)\"")
    raise SystemExit(2)

with open(sample, encoding="utf-8") as fh:
    events = json.load(fh)

workdir = tempfile.mkdtemp()
state_path = os.path.join(workdir, "state.json")

print("run 1 - empty inventory")
st = state_mod.load(state_path)
fresh = update.refresh([dict(e) for e in events], st,
                       translate=False, geocode=False, verbose=False)
state_mod.save(st, state_path)
first_count = len(st["events"])
check("inventory populated", first_count > 0, True)
check("everything is new the first time", len(fresh), first_count)
check("duplicates were merged", first_count < len(events), True)
check("every record has a status",
      all(r.get("status") for r in st["events"].values()), True)

print("\nrun 2 - same listings, nothing new")
st = state_mod.load(state_path)
fresh = update.refresh([dict(e) for e in events], st,
                       translate=False, geocode=False, verbose=False)
state_mod.save(st, state_path)
check("no new shows", len(fresh), 0)
check("inventory unchanged in size", len(st["events"]), first_count)

print("\nrun 3 - one new show appears")
extra = dict(events[0])
extra.update({
    "id": "zzzzzzzzzzzzzzzz",
    "title": "Neue Plastiken",
    "artists": "Testkuenstlerin",
    "venue": "Galerie Koenitz",
    "venue_slug": "galerie-koenitz",
    "city": "Leipzig",
    "vernissage_datetime": "2099-01-15T18:00",
    "exhibition_start": "2099-01-15",
    "exhibition_end": "2099-03-01",
    "raw_description": "Neue Arbeiten in Bronze und Gips.",
    "language": "de",
    "source": "rundgang-kunst",
    "source_url": "https://example.invalid/",
})
st = state_mod.load(state_path)
fresh = update.refresh([dict(e) for e in events] + [extra], st,
                       translate=False, geocode=False, verbose=False)
state_mod.save(st, state_path)
check("exactly one new show", len(fresh), 1)
check("it is the one we added", fresh[0]["title"], "Neue Plastiken")
check("inventory grew by one", len(st["events"]), first_count + 1)

added = [r for r in st["events"].values() if r["title"] == "Neue Plastiken"][0]
check("scored as sculpture", added["medium_tier"], 3)
check("Koenitz override recorded", added["koenitz_override"], True)
check("status derived from its dates", added["status"], "upcoming")

print("\nthe page builds from the inventory")
st = state_mod.load(state_path)
page = board.render(st)
check("every show reaches the page",
      len(board.build_rows(st)), len(st["events"]))
check("the new show is in it", "Neue Plastiken" in page, True)
check("data block is present", '<script id="data"' in page, True)
check("build stamp filled in", "__BUILT__" not in page, True)
check("no unreplaced placeholders", "__DATA__" not in page, True)

print("\nthe page is self-contained apart from fonts, tiles and Leaflet")
external = [line for line in page.split("\n")
            if "http" in line and "src=" in line or "href=\"http" in line]
allowed = ("fonts.googleapis.com", "fonts.gstatic.com", "unpkg.com/leaflet")
offenders = [line.strip()[:70] for line in external
             if not any(a in line for a in allowed)
             and "calendar.google" not in line and "google.com/maps" not in line
             and "openstreetmap" not in line and "example.invalid" not in line]
check("no unexpected external assets", offenders, [])

print("\nan empty inventory still renders")
check("empty page builds",
      "__DATA__" not in board.render({"events": {}, "last_run": None}), True)

print("\nthe PWA files are written")
board.write_pwa(workdir)
for name in ("manifest.webmanifest", "sw.js", "icon-192.png", "icon-512.png"):
    check(name, os.path.exists(os.path.join(workdir, name)), True)
with open(os.path.join(workdir, "manifest.webmanifest"), encoding="utf-8") as fh:
    manifest = json.load(fh)
check("manifest names two icons", len(manifest["icons"]), 2)
check("manifest is installable",
      all(k in manifest for k in ("name", "start_url", "display", "icons")), True)
with open(os.path.join(workdir, "icon-192.png"), "rb") as fh:
    check("icon is a real PNG", fh.read(8), b"\x89PNG\r\n\x1a\n")

print("\n%d failure(s)" % len(FAILURES))
raise SystemExit(1 if FAILURES else 0)
