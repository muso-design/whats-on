# What's on

A hub for two questions: **where to go**, and **where to send the work**.

Exhibitions in Leipzig, Halle, Dresden, Chemnitz and Berlin — openings and
shows already running — ranked so sculpture comes first, with everything else
one filter away. And open calls: residencies, grants, prizes and Kunst am Bau
commissions, filtered down to the ones you are actually allowed to enter and
still have time to prepare for. Works on a phone and on a desktop, installs to
a home screen, and remembers what you have saved.

There is no email. The page answers "what is new" against what you have already
seen, which is the same job done without a mailbox.

```
scraper.py      fetch + parse the four sources into raw event dicts
scoring.py      merging, medium tier, Koenitz override, the default view
translate.py    English descriptions: published where possible, translated otherwise
geocode.py      coordinates for venues that arrive with only an address
wikidata.py     medium from the artist, for listings with no text to read
venues.py       the venue registry: who exists, independent of who lists them
direct.py       read a gallery's own site, for the ones no aggregator carries
calls.py        open calls: deadlines, runway, entry fees and eligibility
llm.py          the local model, allowed only to report what a text says
venues.yaml     the venues you care about, edited by hand
state.py        the inventory: every show, its status and dates
calls.json      the calls inventory, on its own clock
board.py        render the hub page, the manifest, the service worker and icons
update.py       the whole pipeline: fetch, enrich, score, store, rebuild
launch.bat      double-click: refresh if stale, then open the hub
index.html      the hub; committed, rebuilt on every run
```

## Using it

Double-click **`launch.bat`**. It installs dependencies the first time,
refreshes the listings if they are more than three days old, and opens the hub
at `http://localhost:8000/`.

```
launch.bat            open, refreshing only if the data is stale
launch.bat refresh    always fetch fresh listings first
launch.bat open       never refresh, just open
```

It serves over localhost rather than opening the file directly, because saved
shows and offline support both need a real origin — `file://` gives neither.

## The hub

Four tabs, at the bottom on a phone and inline on a desktop.

Cards carry the exhibition image where the source publishes one (176 of 338),
lazily loaded, and a line saying how the medium was decided.

**Browse** — everything being tracked. Opens on what is actionable: shows
closing soon or opening soon. Filter chips for status, city and medium each
carry their own count, so it is always visible how much sits behind a filter
rather than missing. Sort by relevance, by what opens first, or by what
disappears first. Press `/` to jump to the search box.

**Calls** — where to send the work. Opens on sculpture, with the ones whose
terms rule you out already filtered away. Chips for relevance, for how much
time is left, for whether it is free to enter, and for what kind of thing it is
— residency, grant, award, commission. Sort by relevance, by deadline, or by
**most time to prepare**, which is the one that matters when a call wants a
proposal and a portfolio.

**Saved** — everything you pressed Save on, and every call you are tracking.
The list you actually open on a Friday.

**Map** — the same filtered set as pins, sized and coloured by what matters:
sculpture larger, closing-soon in the urgent colour, saved shows in gold. Each
popup carries the details, a calendar link and directions. Filter by city
first; Leipzig and Berlin are 200km apart, so the whole set zooms out to cover
both.

### Marks

Each show has **Save**, **Seen** and **Hide**. They live in your browser's local
storage — nothing is uploaded and there is no account. Hidden shows drop out of
Browse; seen ones dim. The limitation is honest: marks are per device, and
clearing site data clears them.

### New since last visit

Anything that appeared since you last opened the hub is badged **new**, with a
filter chip to see only those. On a first visit nothing is marked — flagging all
338 would say nothing. This replaces the weekly email, and it is better: it is
there when you look rather than buried in a mailbox.

### Adding to your calendar

Every show has a Google Calendar link that opens prefilled — title, venue,
address, the English description, opening hours, source link — for you to
confirm and save. A plain link on purpose: no API key, no OAuth, no access to
your calendar. Nothing is written until you press save.

The entry matches the kind of show. A vernissage is an appointment, so it
becomes a two-hour timed entry in Europe/Berlin. A show already running has no
hour to attend, so the useful entry is an all-day reminder on its last day,
titled "Last day: …".

### On your phone

Serve `index.html` over HTTPS — GitHub Pages is the easy route, and the
workflow already commits a fresh page — then open it and choose "Add to home
screen". It installs as a standalone app with its own icon, and the service
worker caches the shell so the list still opens with no signal. The map needs a
connection for its tiles and says so when it has none.

## Reading galleries directly

The four listing sources only know about galleries that submit to them. REITER
shows in Leipzig and Berlin, publishes both plainly on its own homepage, and
appeared in none of them — rundgang does not even carry the venue. That is a
shape, not a bug, and no amount of parser tuning reaches outside it.

So `venues.py` collects **venues** rather than events, from indexes that have no
opinion about art listings: OpenStreetMap (97 art venues in Leipzig, every one
with coordinates), rundgang's own location pages, the venue id index-berlin puts
on every card, and `venues.yaml`. That produced a registry of **222 venues, 155
with a website**.

`direct.py` then reads those websites with a local model. One prompt covers
every site, whatever it is built with, which is what makes this scale where a
parser per gallery did not. REITER took eight seconds and gave up both shows
with correct cities and ISO dates.

The model is allowed to report only what the page says:

- artists and titles are checked back against the page text, so an invented
  name is dropped
- dates must parse as dates, or they become null
- venue, coordinates, opening hours and the URL come from the registry, never
  from the model
- answers are cached on the page text, so an unchanged site costs nothing

Run it with `python direct.py --curated` for the venues you marked, or let
`update.py` read a few each run.

## Open calls

A call is not an exhibition with a different date. A show has a run and a
distance — closing soon means go now, and Leipzig is walkable while Berlin is a
train. A call has a deadline and an eligibility rule: distance is irrelevant
because you can apply to Reykjavík from Leipzig, closing soon may mean it is
already too late to assemble a portfolio, and a third of them charge you to
enter. So calls live in their own inventory, `calls.json`, with their own
sources and their own clock.

**bbk-bundesverband.de** — a plain table kept by the German artists'
association. Small, curated, almost no noise, and where the regional money is:
a one-month sculpture stipend at Künstlergut Prösitz an hour from Leipzig, a
Kunst am Bau commission in Dresden. Two tables, one dated and one for the ones
that come round every year.

**artconnect.com** — several hundred international opportunities in a
structured blob, with fees, deadlines, requirements and restrictions already
typed. Its artistic-field tags are self-declared, though: many listings tick
every category, so a naive filter on "sculpture" returns mostly calls open to
anybody. Tag breadth is treated as a confidence signal rather than a fact.

### Deadlines

ArtConnect's listing is sorted **deadline soonest**, so reading the first few
pages returns only the calls closing this week — the ones there is no longer
time to enter. It reads thirty pages instead, which is the useful horizon.

The deadline field itself is real: the days spread over three months and pile
up on the 15th, the 30th and the 1st the way application deadlines do. That it
also equals the post's expiry is the platform retiring the listing when the
call shuts, not a bug. What is *not* knowable is which midnight the stored
instant meant — `21:45Z`, `22:00Z`, `12:00Z` and `16:00Z` all appear, which is
what a field entered in the organiser's own timezone looks like after
conversion. Rendering that in Berlin time would move some deadlines across
midnight, and a deadline shown a day late costs a submission. **The day is
kept and the hour is thrown away.** The card says "by 30 Sep".

### Runway, not urgency

A single "urgent" flag turned out to be useless: because the source is sorted
deadline-first, 156 of 330 calls landed inside any threshold worth setting, and
a board where half the cards shout has no signal in it. Two bands instead —
**closing** (seven days, decide today or let it go) and **this month** (three
weeks, enterable if you start now) — and a call you can prepare for is ranked
*above* one closing tomorrow, because a week is not long enough to build
anything.

### Eligibility

The one thing here that removes a call from view, so it is the most carefully
built. An award for Argentine nationals is not a near miss; it is not a call at
all, and finding that out costs three paragraphs of terms every time.

Nationality requirements are matched in plain code, from a list of about two
hundred demonyms — the model would not take "invites Canadian artists" as a
restriction no matter how the prompt was worded, and this is a pattern rather
than a judgement. Two rules make it safe:

- **the whole coordinated list is read.** In "German and Austrian artists" only
  *Austrian* touches the noun, and reading that alone turns a call you may
  enter into one you may not.
- **it must be a requirement, not a mention.** A nationality in front of
  "artists" counts only when the surrounding clause is setting a condition.
  Without this, Ming Fay's biography — "co-founded an Asian American artist
  collective" — shut you out of his research fellowship, and a festival
  advertising "the presentation of Italian artists" shut you out of its call.
  Naming a citizenship or a nationality needs no such evidence; naming an
  artist does.

Whatever the pattern does not catch goes to the local model, one narrow
question at a time, with every country it returns checked back against the
text. Where the two could conflict the door stays open: an explicit "artists
worldwide may apply" beats a named nationality, because leaving a call in costs
you a few seconds of reading and taking one out loses it for good.

Of 330 calls, 42 are shut — US state fellowships, an Arts Council award for
England, Scotland and Wales, a Berlin working grant that wants Berlin
residency. They are not hidden but sunk, so a misreading can still be caught.

### Tracking an application

Each call has a stage: interested, preparing, submitted, heard back. It lives
in your browser's local storage like the show marks, and tracked calls appear
in **Saved** beside your saved exhibitions. Every dated call also offers a
calendar reminder placed **a week before** the deadline, not on it, carrying
what the call wants — a deadline you find out about on the day is a deadline
you miss.

### Which model

Measured on an RTX 2070 SUPER with 8 GB:

| model | size | seconds per record |
|-------|------|--------------------|
| `qwen2.5-coder:7b` | 4.7 GB | **2.8** |
| `gemma3:4b` | 3.3 GB | 60–80 |
| `qwen3:8b` | 5.2 GB | 45–101 |

All three sit entirely in VRAM, so this is not spilling — the two newer models
simply spend their time in prompt processing on this card. `gemma3:4b` gave
visibly better German (it found materials the coder model missed, all of them
real), so it is worth using for an occasional quality pass via
`LLM_MODEL=gemma3:4b`, but the default is the one that is twenty times faster.

Set `LLM_MODEL` to change it and `LLM_TIMEOUT` if your machine is slower.
Nothing here is required: with no Ollama running, every model call returns
nothing and the rest of the pipeline is unaffected.

## Sources

All four were inspected before any parser was written. None needs a headless
browser.

**rundgang-kunst.de** — static WordPress, one page per region. Slugs confirmed,
not guessed: `/regions/leipzig/`, `/halle/`, `/dresden/`, `/chemnitz/`. Three
tabs: the dated one covers about three days, `future_events` gives the horizon,
`running_events` gives everything currently on. Event pages carry the venue,
address, opening hours, the run and the vernissage time.

**artatberlin.com** — the calendar grid is EventON over AJAX, so the page is
empty HTML. `/wp-json/wp/v2/posts` carries the long-form descriptions, the run
in the post title and the vernissage time in the body. The events endpoint
exists too but its blurbs never name a medium, which makes them useless for
scoring.

**indexberlin.de** — the whole Berlin listing, running and upcoming, on one
static page: 287 exhibitions in a single request. No descriptions anywhere on
the site, so these cannot be medium-scored alone — but every card carries the
venue's coordinates, which nothing else provides.

**berlinartlink.com** — static weekly post at a stable permalink.

### Merging

The same show from several sources collapses into one record, matched on venue,
date proximity and title/artist overlap. This is what makes overlapping sources
an advantage: index-berlin knows where a gallery is, art-at-berlin knows what
the show is about, and only together do they make a record worth ranking.

Venue names are compared loosely — "BQ" and "BQ Berlin" are one gallery,
"Galerie Crone" and "Crone Berlin" are one gallery, "Galerie K" and "Galerie
KUB" are not — and generic words ("Ausstellung", "Galerie", the city name) are
ignored so two different shows opening at one venue on one night stay separate.

Record ids are computed from what every source agrees on — the identifying
words of the venue and title, plus the start month — so a show does not turn up
as new again the week one source stops listing it.

## Scoring

Scoring annotates; it never deletes. Every show any source mentions is kept and
ranked. The tiers and the city bar decide the *default view*, not what exists.

1. **Medium tier** from keywords in title, artist and description. Tier 3
   sculpture / installation / material, Tier 2 painting / drawing, Tier 0 no
   match. Materials count as sculpture: galleries write "Epoxidharz" or "Gips
   und Bronze" far more often than "Skulptur". A manner word beside a medium
   noun loses to the noun, so "figurative Gemälde" is painting.
2. **Koenitz override** — anything at Galerie Koenitz is always included. The
   one hardcoded rule; it encodes a relationship, not a taste signal.
3. **Corroboration** — a rank boost when two sources report the same show.
4. **City bar** — Leipzig shows everything Tier 2+; Halle/Dresden/Chemnitz show
   Tier 3 always and Tier 2 only for a Fri–Sun vernissage; Berlin shows Tier 3
   always and Tier 2 only when corroborated.

`medium_confidence` separates "something told us the medium" from "nothing
could speak for this show". Knowing a show is photography is as useful as
knowing it is sculpture: it stops being noise. `medium_source` records which
of the two below answered, so any ranking can be questioned.

### Medium from the artist

Most Berlin listings carry no description at all, so keywords have nothing to
read — but they do name the artists, and who made the work tells you the
medium. Wikidata records that as structured occupation data.

This took the unclassified count from **281 to 131**, and sculpture from
**12 to 83**.

Two things make it safe rather than a guess:

- **It abstains.** An artist with no Wikidata entry gets no verdict and the
  show stays honestly unclassified.
- **It refuses the wrong person.** "Kaspar Müller" turns up a politician, a
  lyricist and an artist; "Alan Charlton" a diplomat and a painter; "Thomas
  Feuerstein" a researcher and an artist. A match only counts when the
  occupations say visual artist — which is why the SPARQL endpoint is used
  rather than the search API: it returns every same-name candidate at once, so
  the artist can be picked from among them. Naive top-hit matching mislabelled
  about a quarter of shows by a stranger's occupation.

A group show counts as sculpture if any one of its artists is a sculptor — the
question is whether there is sculpture in the room. "Installation artist"
counts as sculpture, matching the agreed keyword list, which does mean some
video and performance installations land in the sculpture bucket.

Every verdict carries the Wikidata id it came from, and the card shows which
artist and which occupations decided it.

Artist to medium is a durable fact, so `artists.json` only grows: resolve
someone once and every future show of theirs is classified. Wikidata throttles
hard, so a run looks up at most 120 new names.

```bash
python wikidata.py --dry-run   # how many artists are still unresolved
python wikidata.py --limit 200 # resolve a batch
```

There is deliberately no per-venue statistical prior and no learned layer. At a
handful of shows per venue per year it would measure how often a venue shows
sculpture, not whether it has good taste, and would need years of data to mean
anything.

To tune what surfaces, edit `TIER3_KEYWORDS` / `MATERIAL_KEYWORDS` /
`TIER2_KEYWORDS` in `scoring.py`.

### Status

Derived from the run dates on every pass, never remembered: `opening_soon`
(within three weeks), `upcoming`, `running`, `closing_soon` (within a
fortnight), `closed`, `undated`. A show is never deleted while it is running;
`prune()` forgets shows that closed over a year ago.

## English descriptions

Three steps, cheapest first: take the **published English** when a listing
carries both languages; leave alone anything a source **declares** English;
**translate** the rest once and cache it in `translations.json`. The original is
never replaced — scoring reads both, so the German and English keyword lists
each do their work.

| Variable | Effect |
|----------|--------|
| `DEEPL_API_KEY` | DeepL. Recommended; the free tier's 500k characters/month is far more than this needs. |
| `TRANSLATE_PROVIDER=mymemory` | No signup, but 5k characters/day anonymously. `MYMEMORY_EMAIL` raises it to 50k. |
| neither | Nothing is translated; everything else runs unchanged. |

```bash
python translate.py --dry-run     # what would be translated
python translate.py --limit 40    # backfill, capped
python geocode.py                 # look up any missing venue coordinates
```

## Tests

```bash
python test_scraper.py      # parser edge cases per source
python test_scoring.py      # tiers, merging, override, the default view
python test_pipeline.py     # the refresh loop and the page build
python test_robustness.py   # every function against missing and hostile input
python test_translate.py    # what gets translated, and what must not
python test_calendar.py     # calendar entries, directions, geocoding
python test_wikidata.py     # medium from the artist, and namesake rejection
python test_direct.py       # the venue registry, and what the model may claim
python test_calls.py        # deadlines, runway, and who may actually apply
```

`test_pipeline.py` needs `sample_events.json`:

```bash
python -c "import json,scraper;json.dump(scraper.scrape_all(),open('sample_events.json','w',encoding='utf-8'),ensure_ascii=False,indent=1)"
```

## Scheduling

`.github/workflows/refresh.yml` runs daily at 06:00 UTC, rebuilds the page and
commits it. No secrets are required unless you want translation.

## Known limitations

- **131 shows are still unclassified** — artists with no Wikidata entry, mostly
  emerging ones, plus 51 shows that name no artist at all. They are listed,
  searchable and mappable, just not ranked by medium.
- **Occupation is not the same as this show.** A painter who also casts bronze
  is filed as sculpture whatever is actually on the walls this month. Josef
  Albers surfaces as sculpture because Wikidata lists "glass artist". The bias
  is deliberately towards over-inclusion.
- **Images are hotlinked** from the source sites, not copied. They load lazily,
  are forced to https so they are not blocked as mixed content, and a moved
  image removes its own box rather than leaving a gap.
- **Marks are per device.** Local storage, no account, no sync. Clearing site
  data clears them.
- **Translation quality is not reviewed.** Machine output is labelled
  "translated" so it is never mistaken for the gallery's own wording, but nobody
  checks it. Abbreviations suffer most — one translator turned "KK5" into "CC5".
- **12 shows have no coordinates**, mostly venues with no usable address, and
  they are excluded from the map with a note saying how many.
- **The map needs a connection** for its tiles. The list does not.
- **Source data is occasionally self-contradictory** — Berlin Art Link has
  listed a September opening for an August run. Nothing is validated against
  reality.
- **No Instagram**, by design. One Leipzig gallery lists an Instagram profile as
  its entire web presence; treat that as a manual channel.
- **No taste-learning.** The marks are the honest path to it: rate shows after
  attending and there is a real dataset in a year. Scraped listing text cannot
  produce one.
