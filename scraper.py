"""Fetch + parse the three sources into raw event dicts.

Sources:
  rundgang-kunst.de   static WordPress HTML, one page per region (Leipzig cluster)
  artatberlin.com     EventON calendar; the grid is AJAX-only, so we use the public
                      WP REST API plus the schema.org JSON-LD on each event page
  berlinartlink.com   static weekly post, used only for the corroboration bonus
"""

import hashlib
import html as _html
import json
import re
import time
import unicodedata
from datetime import date, datetime, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (compatible; ExhibitionDigest/1.0; personal weekly digest)"
)
REQUEST_TIMEOUT = 30
REQUEST_PAUSE = 0.7          # be polite: gap between requests
MAX_RETRIES = 3

RUNDGANG_REGIONS = {
    "leipzig": "Leipzig",
    "halle": "Halle",
    "dresden": "Dresden",
    "chemnitz": "Chemnitz",
}
RUNDGANG_BASE = "https://www.rundgang-kunst.de/regions/{slug}/"

# Subevent labels that mean "this is the opening", not a finissage or a talk.
OPENING_WORDS = ("vernissage", "eroffnung", "eroeffnung", "opening", "preview")

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "de,en;q=0.8"})


class FetchError(RuntimeError):
    pass


def fetch(url, params=None, as_json=False):
    """GET with retries. Raises FetchError after MAX_RETRIES failures."""
    last = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = _session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            time.sleep(REQUEST_PAUSE)
            return r.json() if as_json else r.text
        except Exception as exc:          # noqa: BLE001 - retry on anything
            last = exc
            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt)
    raise FetchError("%s: %s" % (url, last))


def soup(html):
    """Parse HTML with lxml."""
    return BeautifulSoup(html, "lxml")


def clean(text):
    """Collapse whitespace and normalise the typographic junk in listing text."""
    if not text:
        return ""
    for _ in range(3):                      # some fields are double-encoded
        unescaped = _html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("–", "-").replace("—", "-")
    text = text.replace("‘", "'").replace("’", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("„", '"').replace("‚", "'")
    return re.sub(r"\s+", " ", text).strip()


def fold(text):
    """Lowercase, strip accents and punctuation - for fuzzy cross-source matching."""
    text = unicodedata.normalize("NFKD", clean(text).lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def event_id(venue, title, vernissage_date):
    """Stable across runs: venue + title + vernissage date."""
    key = "|".join([
        fold(venue),
        fold(title),
        (vernissage_date or "")[:10],
    ])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def https_url(url):
    """Force an image URL to https.

    Some listings hand out http:// image links even though the same host
    serves them over https. Left alone they are blocked as mixed content the
    moment the page is served over https, which is exactly where the phone
    reads it.
    """
    if url and url.startswith("http://"):
        return "https://" + url[len("http://"):]
    return url or None


def _empty_event(source, source_url):
    return {
        "id": None,
        "title": "",
        "artists": None,
        "venue": "",
        "venue_slug": None,
        "city": None,
        "address": None,
        "lat": None,
        "lng": None,
        "opening_hours": None,
        "image": None,
        "event_type": "exhibition",   # exhibition | talk | performance | fair
        "language": None,             # language of raw_description, per source
        "category": None,             # source's own grouping, where it has one
        "vernissage_datetime": None,
        "exhibition_start": None,
        "exhibition_end": None,
        "source": source,
        "source_url": source_url,
        "raw_description": "",
    }


# --------------------------------------------------------------------------
# rundgang-kunst.de
# --------------------------------------------------------------------------

_DE_DATE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_DE_TIME = re.compile(r"(\d{1,2})[:.](\d{2})")
# Postal code + city, e.g. "04129 Leipzig" or "01067 Dresden"
_PLZ_CITY = re.compile(r"\b\d{5}\s+([A-Za-zÀ-ɏ][\wÀ-ɏ.-]+)")


def _de_date_iso(text):
    m = _DE_DATE.search(text or "")
    if not m:
        return None
    d, mo, y = m.groups()
    try:
        return date(int(y), int(mo), int(d)).isoformat()
    except ValueError:
        return None


RUNDGANG_TABS = (
    "events_with_subevents",   # dated events, next ~3 days only
    "future_events",           # everything that has not opened yet
    "running_events",          # exhibitions currently on
)


def rundgang_event_urls(html, tabs=RUNDGANG_TABS):
    """Event permalinks from the region page's tabs.

    The dated tab only covers about three days, so the upcoming tab is what
    gives the digest its horizon and the running tab is what makes the board a
    picture of everything currently on rather than only what is about to open.
    """
    s = soup(html)
    urls = []
    for section_id in tabs:
        section = s.find("section", id=section_id)
        if not section:
            continue
        for a in section.select("div.event_title a[href]"):
            href = a["href"]
            if "/events/" in href and href not in urls:
                urls.append(href)
    return urls


def parse_rundgang_event(html, url, region_city):
    """Parse a rundgang-kunst.de event detail page."""
    s = soup(html)
    ev = _empty_event("rundgang-kunst", url)
    ev["language"] = "de"

    h1 = s.select_one("header.header_box h1")
    ev["title"] = clean(h1.get_text(" ") if h1 else "")

    artists = s.select_one("div.artists_box div.right")
    if artists:
        ev["artists"] = clean(artists.get_text(" ")) or None

    text_box = s.select_one("div.text_box")
    if text_box:
        for hr in text_box.find_all("hr"):
            hr.decompose()
        ev["raw_description"] = clean(text_box.get_text(" "))

    loc = s.select_one("p.location_adress")
    if loc:
        link = loc.find("a")
        if link:
            ev["venue"] = clean(link.get_text(" "))
            ev["venue_slug"] = link.get("href", "").rstrip("/").rsplit("/", 1)[-1]
            link.extract()
        strong = loc.find("strong")
        if strong:
            if not ev["venue"]:
                ev["venue"] = clean(strong.get_text(" "))
            strong.extract()
        ev["address"] = clean(loc.get_text(" ")) or None

    shot = s.select_one("div.image_box img")
    if shot and shot.get("src"):
        ev["image"] = https_url(urljoin(url, shot["src"]))

    # Opening hours matter as soon as running exhibitions are listed: a
    # vernissage carries its own time, "on now" is useless without them.
    hours = s.select_one("p.openingtimes")
    if hours:
        ev["opening_hours"] = clean(hours.get_text(" ")) or None

    # Exhibition run: the tab strip carries the start and end date.
    tab = s.select_one("ul.tab_navi")
    if tab:
        found = [_de_date_iso(m.group(0)) for m in _DE_DATE.finditer(tab.get_text(" "))]
        dates = sorted({d for d in found if d})
        if dates:
            ev["exhibition_start"] = dates[0]
            ev["exhibition_end"] = dates[-1] if len(dates) > 1 else None

    # Opening: first subevent whose label reads as a vernissage.
    for li in s.select("div.subevents_box li"):
        # The time sits inside the label, so read it before stripping it out.
        tnode = li.select_one("time.time")
        time_text = tnode.get_text() if tnode else ""
        h4 = li.select_one("h4")
        if h4:
            for t in h4.select("time"):
                t.extract()
            label = clean(h4.get_text(" ")).strip(" ,")
        else:
            label = ""
        if not any(w in fold(label) for w in OPENING_WORDS):
            continue
        dnode = li.select_one("time.date")
        day = _de_date_iso(dnode.get_text() if dnode else "")
        if not day:
            continue
        tm = _DE_TIME.search(time_text)
        if tm:
            ev["vernissage_datetime"] = "%sT%02d:%s" % (day, int(tm.group(1)), tm.group(2))
        else:
            ev["vernissage_datetime"] = day
        ev["subevent_label"] = label
        break

    # City: prefer the postal address, fall back to the region the page came from.
    city = None
    if ev["address"]:
        m = _PLZ_CITY.search(ev["address"])
        if m:
            city = m.group(1).strip(".,")
    ev["city"] = city if city in RUNDGANG_REGIONS.values() else region_city

    ev["id"] = event_id(ev["venue"], ev["title"],
                        ev["vernissage_datetime"] or ev["exhibition_start"])
    return ev


def scrape_rundgang(regions=None, verbose=True):
    """All events currently listed for the given rundgang regions."""
    regions = regions or RUNDGANG_REGIONS
    events, seen = [], set()
    for slug, city in regions.items():
        try:
            listing = fetch(RUNDGANG_BASE.format(slug=slug))
        except FetchError as exc:
            print("  ! rundgang/%s listing failed: %s" % (slug, exc))
            continue
        urls = rundgang_event_urls(listing)
        if verbose:
            print("  rundgang/%s: %d event pages" % (slug, len(urls)))
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            try:
                events.append(parse_rundgang_event(fetch(url), url, city))
            except FetchError as exc:
                print("    ! skipped %s: %s" % (url, exc))
    return events


# --------------------------------------------------------------------------
# artatberlin.com
#
# The vernissage calendar is drawn by EventON over AJAX, so there is nothing to
# scrape on the calendar page itself. Two public, unauthenticated routes carry
# the same information without a headless browser:
#
#   /wp-json/wp/v2/ajde_events - the calendar entries. Precise, but the blurbs
#       are boilerplate ("Gallery X opens on Thursday the exhibition Y") and
#       almost never name a medium, which makes them useless for scoring.
#   /wp-json/wp/v2/posts - the long-form exhibition descriptions behind the
#       calendar's "To the exhibition description" button. These carry the real
#       editorial text, the run dates in the post title, and the vernissage
#       time in the body.
#
# We use the posts feed: it is one request for a hundred shows, and it is the
# only one of the two that gives the medium keywords any material to work with.
# The cost is that Berlin records have no street address.
# --------------------------------------------------------------------------

AAB_API = "https://www.artatberlin.com/wp-json/wp/v2/posts"
AAB_LOOKBACK_DAYS = 45       # how far back to look for newly published shows

# "Artist | Title | Gallery | 29.08.-03.10.2026"
_AAB_RUN = re.compile(
    r"(\d{1,2})\.(\d{1,2})\.(\d{4})?\s*-\s*(\d{1,2})\.(\d{1,2})\.(\d{4})")
# "Vernissage: Freitag, 28. August 2026, 19:00-21:30 Uhr"
# "Eroeffnung: Donnerstag, 13. August, 18:00 - 21:00 Uhr"   (no year)
# "Vernissage: Freitag, 14. August 2026, ab 19 Uhr"         (no minutes)
# "Vernissage: Donnerstag, 10. September 2026 von 18:00 bis 20:00 Uhr"
# The time is matched separately: written as one pattern, an optional year lets
# the hour group backtrack into "2026" and read the opening as 20:00.
_AAB_OPENING_LINE = re.compile(
    r"(?:Vernissage|Er[oö]ffnung|Opening)\s*:\s*"
    r"(?:[A-Za-zÀ-ɏ]+,?\s*)?"
    r"(\d{1,2})\.\s*([A-Za-zÀ-ɏ]+)\.?\s*(\d{4})?", re.I)
# The start time, i.e. the first clock reading after the date.
_AAB_TIME = re.compile(r"(?<![\d:])(\d{1,2})(?::(\d{2}))?\s*(?:Uhr|bis|-|h(?![a-z]))", re.I)
# Everything from here on is captions and previews of other shows at the venue.
_AAB_BOILERPLATE = ("zur galerie", "bildunterschrift", "image caption",
                    "to the gallery")
# Editorial news items, not gallery shows.
_AAB_SKIP_TITLE = ("news ++", "news++", "art at berlin |", "artletter")

_DE_MONTHS = ["januar", "februar", "marz", "april", "mai", "juni", "juli",
              "august", "september", "oktober", "november", "dezember"]


def _de_month_num(name):
    """Month number from a German month name, accent- and case-insensitive."""
    name = fold(name)
    if not name:
        return None
    for i, full in enumerate(_DE_MONTHS, 1):
        if full.startswith(name[:3]):
            return i
    return None


def _strip_boilerplate(text):
    """Cut the post body where the editorial description ends."""
    lowered = text.lower()          # same length as text, unlike fold()
    cut = len(text)
    for marker in _AAB_BOILERPLATE:
        pos = lowered.find(marker)
        if 0 <= pos < cut:
            cut = pos
    text = text[:cut]
    # Drop the "bis 03.10. | #5120ARTatBerlin | " lead-in.
    return clean(re.sub(r"^[^|]{0,30}\|\s*#\d+[A-Za-z]*\s*\|\s*", "", text))


def aab_split_title(name):
    """'Artist | Title | Gallery | dates' -> (artists, title, venue, run)."""
    parts = [clean(p) for p in (name or "").split("|") if clean(p)]
    run = None
    # The last segment is the run. It is sometimes a placeholder such as
    # "10.09.2026-(folgt)", which still has to come off the venue name.
    if len(parts) > 2 and re.match(r"^\(?\d{1,2}[./]", parts[-1]):
        run = _aab_run_dates(parts.pop())
    if len(parts) >= 3:
        return parts[0], " | ".join(parts[1:-1]), parts[-1], run
    if len(parts) == 2:
        return None, parts[0], parts[1], run
    return None, (parts[0] if parts else ""), "", run


def _aab_run_dates(text):
    """'29.08.-03.10.2026' -> ('2026-08-29', '2026-10-03')."""
    m = _AAB_RUN.search(text or "")
    if not m:
        return None
    d1, m1, y1, d2, m2, y2 = m.groups()
    inferred = y1 is None                 # "09.10.-17.01.2027" omits the first year
    y1 = y1 or y2
    try:
        start = date(int(y1), int(m1), int(d1))
        end = date(int(y2), int(m2), int(d2))
    except ValueError:
        return None
    if end < start:                       # the run crosses the new year
        if inferred:
            start = date(start.year - 1, start.month, start.day)
        else:
            end = date(end.year + 1, end.month, end.day)
    return start.isoformat(), end.isoformat()


def aab_opening_datetime(text, fallback_year, run=None):
    """Vernissage date and time from the post body.

    The body ends with a preview block for the venue's *other* upcoming shows,
    each with its own "Eroeffnung:" line, so a bare first-match is not safe.
    When the run is known, prefer an opening that sits within a week of it -
    a vernissage happens on or just before the day the show opens.
    """
    run_start = None
    if run:
        try:
            run_start = date.fromisoformat(run[0])
        except (ValueError, TypeError):
            run_start = None

    text = text or ""
    fallback = None
    for m in _AAB_OPENING_LINE.finditer(text):
        day, month, year = m.groups()
        mo = _de_month_num(month)
        if not mo:
            continue                      # a stray "Opening: 28.08." reference
        try:
            when = date(int(year or fallback_year), mo, int(day))
        except ValueError:
            continue

        stamp = when.isoformat()
        tm = _AAB_TIME.search(text[m.end():m.end() + 60])
        if tm and int(tm.group(1)) < 24:
            stamp += "T%02d:%s" % (int(tm.group(1)), tm.group(2) or "00")

        if run_start is None:
            return stamp
        if -7 <= (when - run_start).days <= 7:
            return stamp
        if fallback is None:
            fallback = stamp
    return fallback if run_start is None else None


def parse_aab_post(post):
    """One artatberlin exhibition-description post -> an event dict."""
    title_raw = clean((post.get("title") or {}).get("rendered", ""))
    # Checked on the raw title: fold() strips the "++" that marks a news item.
    lowered = title_raw.lower()
    if not title_raw or any(s in lowered for s in _AAB_SKIP_TITLE):
        return None

    artists, title, venue, run = aab_split_title(title_raw)
    if not title or not venue:
        return None
    # Group shows are written "Show name | Gruppenausstellung | Gallery", the
    # reverse of the usual "Artist | Show name | Gallery".
    if artists and fold(title) in ("gruppenausstellung", "group exhibition",
                                   "group show"):
        artists, title = title, artists

    rendered = (post.get("content") or {}).get("rendered", "")
    shot = re.search(r'<img[^>]+src="([^"]+)"', rendered)
    body = clean(re.sub(r"(?s)<[^>]+>", " ", rendered))
    description = _strip_boilerplate(body)

    ev = _empty_event("art-at-berlin", post.get("link") or "")
    ev["language"] = "de"
    if shot:
        ev["image"] = https_url(shot.group(1))
    ev["title"] = title
    ev["artists"] = artists
    ev["venue"] = venue
    ev["city"] = "Berlin"
    ev["raw_description"] = description
    if run:
        ev["exhibition_start"], ev["exhibition_end"] = run

    year = int((run[0][:4] if run else post.get("date", "")[:4]) or date.today().year)
    ev["vernissage_datetime"] = aab_opening_datetime(body, year, run)
    if not ev["vernissage_datetime"] and run:
        ev["vernissage_datetime"] = run[0]

    ev["id"] = event_id(ev["venue"], ev["title"],
                        ev["vernissage_datetime"] or ev["exhibition_start"])
    return ev


def scrape_art_at_berlin(lookback_days=AAB_LOOKBACK_DAYS, max_pages=3, verbose=True):
    """Exhibitions published on artatberlin within the lookback window."""
    after = (datetime.now() - timedelta(days=lookback_days)).replace(
        microsecond=0).isoformat()
    posts = []
    for page in range(1, max_pages + 1):
        try:
            batch = fetch(AAB_API, params={
                "per_page": 100, "page": page, "orderby": "date", "order": "desc",
                "after": after,
                "_fields": "id,link,date,title,content",
            }, as_json=True)
        except FetchError as exc:
            print("  ! art-at-berlin page %d failed: %s" % (page, exc))
            break
        if not isinstance(batch, list) or not batch:
            break
        posts.extend(batch)
        if len(batch) < 100:
            break

    events = []
    for post in posts:
        ev = parse_aab_post(post)
        if ev:
            events.append(ev)
    if verbose:
        print("  art-at-berlin: %d posts since %s, %d exhibitions parsed"
              % (len(posts), after[:10], len(events)))
    return events


# --------------------------------------------------------------------------
# berlinartlink.com  (secondary Berlin source: corroboration only)
# --------------------------------------------------------------------------

BAL_URL = "https://www.berlinartlink.com/this-weeks-events/"

_BAL_DAY = re.compile(
    r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
    r"([A-Z][a-z]+)\.?\s+(\d{1,2}),?\s*(\d{4})?", re.I)
_MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july",
                "august", "september", "october", "november", "december"]
# Times appear as "6pm", "6:30pm" and as ranges "7-10pm" where only the end
# token carries the meridiem.
_BAL_TIME = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.I)


def _month_num(name):
    """Month number from a full or abbreviated name ('Sept.', 'Aug', 'June')."""
    name = (name or "").lower().rstrip(".")
    if not name:
        return None
    for i, full in enumerate(_MONTH_NAMES, 1):
        if full.startswith(name) or name.startswith(full[:3]):
            return i
    return None


def _bal_day_iso(text, fallback_year):
    m = _BAL_DAY.match(clean(text))
    if not m:
        return None
    _, month, day, year = m.groups()
    mo = _month_num(month)
    if not mo:
        return None
    try:
        return date(int(year or fallback_year), mo, int(day)).isoformat()
    except ValueError:
        return None


def _bal_start_time(line):
    """(hour, minute) of the *start* of an opening. '7-10pm' -> (19, '00').

    The time always follows the date after a semicolon
    ("Opening Reception: Tuesday, July 28; 7-10pm"), so cut there first -
    otherwise the day number gets read as an hour.
    """
    line = line.rsplit(";", 1)[-1] if ";" in line else line
    tokens = [t for t in _BAL_TIME.finditer(line) if t.group(1)]
    if not tokens:
        return None
    first = tokens[0]
    meridiem = first.group(3) or next(
        (t.group(3) for t in tokens[1:] if t.group(3)), None)
    hour = int(first.group(1))
    if meridiem:
        hour = hour % 12 + (12 if meridiem.lower() == "pm" else 0)
    elif hour < 9:                 # gallery openings are evening events
        hour += 12
    return hour, (first.group(2) or "00")


def parse_berlin_art_link(html, fallback_year=None):
    """One record per venue block. Openings only; talks/performances skipped."""
    fallback_year = fallback_year or date.today().year
    s = soup(html)
    body = s.select_one("section.entry-content") or s
    events, current_day = [], None

    for node in body.find_all(["h2", "h3", "p"]):
        if node.name == "h3":
            current_day = _bal_day_iso(node.get_text(" "), fallback_year) or current_day
            continue
        if node.name != "h2":
            continue

        venue = clean(node.get_text(" "))
        para = node.find_next_sibling("p")
        if not venue or not para:
            continue

        link = para.find("a", href=True)
        lines = [clean(x) for x in para.get_text("\n").split("\n") if clean(x)]
        if not lines:
            continue

        ev = _empty_event("berlin-art-link", link["href"] if link else BAL_URL)
        ev["language"] = "en"
        ev["venue"] = venue
        ev["city"] = "Berlin"
        ev["raw_description"] = " / ".join(lines)

        # "Artist, Artist: 'Title'"
        head = lines[0]
        m = re.match(r"^(.*?):\s*['\"](.+?)['\"]\s*$", head)
        if m:
            ev["artists"], ev["title"] = clean(m.group(1)), clean(m.group(2))
        else:
            ev["title"] = head

        for line in lines[1:]:
            low = fold(line)
            if low.startswith("exhibition"):
                run = re.sub(r"(?i)^exhibition:?\s*", "", line)
                ev["exhibition_start"], ev["exhibition_end"] = _bal_run(run, fallback_year)
            elif any(w in low for w in ("opening", "vernissage", "eroffnung")):
                day = _bal_day_iso(re.sub(r"(?i)^[^:]*:\s*", "", line), fallback_year) \
                    or current_day
                if not day:
                    continue
                tm = _bal_start_time(re.sub(r"(?i)^[^:]*:\s*", "", line))
                ev["vernissage_datetime"] = (
                    "%sT%02d:%s" % (day, tm[0], tm[1]) if tm else day)
            elif re.search(r"\b\d{5}\b", line) and not ev["address"]:
                ev["address"] = clean(
                    re.sub(r"(?i),?\s*click here for map\s*$", "", line)).rstrip(",")

        if not ev["vernissage_datetime"]:
            continue          # a talk or performance, not an opening
        ev["id"] = event_id(ev["venue"], ev["title"], ev["vernissage_datetime"])
        events.append(ev)
    return events


def _bal_run(text, fallback_year):
    """'July 28-Aug. 2, 2026' -> ('2026-07-28', '2026-08-02').

    Also handles same-month ranges written as 'Aug. 7-23, 2026'.
    """
    year = re.search(r"(\d{4})", text)
    year = int(year.group(1)) if year else fallback_year
    text = re.sub(r",?\s*\d{4}\s*$", "", clean(text))     # drop the trailing year
    parts = re.findall(r"(?:([A-Z][a-z]+)\.?\s+)?(\d{1,2})\b", text)
    out, last_month = [], None
    for month, day in parts[:2]:
        mo = _month_num(month) if month else last_month
        if not mo:
            continue
        last_month = mo
        try:
            out.append(date(year, mo, int(day)).isoformat())
        except ValueError:
            pass
    if len(out) == 2 and out[1] < out[0]:      # run crosses new year
        out[1] = out[1].replace(str(year), str(year + 1), 1)
    return (out[0] if out else None, out[1] if len(out) > 1 else None)


def scrape_berlin_art_link(verbose=True):
    """This week's Berlin openings, for the corroboration bonus."""
    try:
        html = fetch(BAL_URL)
    except FetchError as exc:
        print("  ! berlin-art-link failed: %s" % exc)
        return []
    events = parse_berlin_art_link(html)
    if verbose:
        print("  berlin-art-link: %d openings" % len(events))
    return events


# --------------------------------------------------------------------------
# indexberlin.de
#
# The whole Berlin listing - currently running and upcoming - is one static
# page, roughly 290 exhibitions in a single request. No vernissage times, but
# every card carries the venue's coordinates, which nothing else gives us.
# --------------------------------------------------------------------------

INDEX_BERLIN_URL = "https://www.indexberlin.de/"

# "starts on September 10, 2026" / "until August 23, 2026"
_IDX_DATE = re.compile(
    r"(starts on|until)\s+([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", re.I)

_EN_MONTHS = ["january", "february", "march", "april", "may", "june", "july",
              "august", "september", "october", "november", "december"]

# data-section values that are not Berlin galleries/institutions
_IDX_CITY = {"brandenburg": "Brandenburg"}


def _en_month_num(name):
    name = fold(name)
    for i, full in enumerate(_EN_MONTHS, 1):
        if full.startswith(name[:3]):
            return i
    return None


def _idx_date(text):
    """('starts'|'until', iso date) from the card's date line."""
    m = _IDX_DATE.search(text or "")
    if not m:
        return None, None
    kind, month, day, year = m.groups()
    mo = _en_month_num(month)
    if not mo:
        return None, None
    try:
        return kind.lower(), date(int(year), mo, int(day)).isoformat()
    except ValueError:
        return None, None


def parse_index_berlin(html):
    """Every exhibition card on the index Berlin listing page."""
    events = []
    s = soup(html)
    for group in s.select("div.events"):
        section = group.get("data-section") or ""
        city = _IDX_CITY.get(section, "Berlin")
        for card in group.select("article.event"):
            title_node = card.select_one(".event__title")
            author = card.select_one(".event__authors")
            venue_node = card.select_one(".event__location span")
            # An untitled solo show carries only the artist name.
            if not venue_node or not (title_node or author):
                continue

            href = (card.get("data-href")
                    or (title_node or author).get("href") or "")
            ev = _empty_event("index-berlin",
                              urljoin(INDEX_BERLIN_URL, href) if href else INDEX_BERLIN_URL)
            ev["artists"] = clean(author.get_text(" ")) if author else None
            ev["title"] = (clean(title_node.get_text(" ")) if title_node
                           else ev["artists"])
            ev["venue"] = clean(venue_node.get_text(" "))
            ev["city"] = city
            ev["category"] = section or None

            venue_href = card.get("data-venue") or ""
            if venue_href:
                ev["venue_slug"] = venue_href.rstrip("/").rsplit("/", 1)[-1]

            thumb = card.select_one(".list-thumb img")
            if thumb and thumb.get("src"):
                ev["image"] = https_url(
                    urljoin(INDEX_BERLIN_URL, thumb["src"]))

            date_node = card.select_one(".event__date span")
            kind, when = _idx_date(date_node.get_text(" ") if date_node else "")
            if kind == "starts on":
                ev["exhibition_start"] = when
            elif kind == "until":
                ev["exhibition_end"] = when

            for attr, key in (("data-latitude", "lat"), ("data-longitude", "lng")):
                raw = card.get(attr)
                if raw:
                    try:
                        ev[key] = float(raw)
                    except ValueError:
                        pass

            ev["id"] = event_id(ev["venue"], ev["title"],
                                ev["exhibition_start"] or ev["exhibition_end"])
            events.append(ev)
    return events


def scrape_index_berlin(verbose=True):
    """The Berlin listing: running and upcoming shows, with coordinates."""
    try:
        html = fetch(INDEX_BERLIN_URL)
    except FetchError as exc:
        print("  ! index-berlin failed: %s" % exc)
        return []
    events = parse_index_berlin(html)
    if verbose:
        running = sum(1 for e in events if e["exhibition_end"])
        print("  index-berlin: %d exhibitions (%d already running)"
              % (len(events), running))
    return events


# --------------------------------------------------------------------------

SOURCES = {
    "rundgang": scrape_rundgang,
    "art-at-berlin": scrape_art_at_berlin,
    "index-berlin": scrape_index_berlin,
    "berlin-art-link": scrape_berlin_art_link,
}


def scrape_all(verbose=True):
    """Every event from every source, as raw dicts.

    A source that fails is reported and skipped; one dead site must not take
    the whole run with it.
    """
    events = []
    for name, fn in SOURCES.items():
        try:
            events.extend(fn(verbose=verbose))
        except Exception as exc:                      # noqa: BLE001
            print("  ! source %s failed entirely: %s" % (name, exc))
    return events


if __name__ == "__main__":
    import sys
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    fn = {
        "rundgang": scrape_rundgang,
        "berlin": scrape_art_at_berlin,
        "bal": scrape_berlin_art_link,
        "all": scrape_all,
    }[which]
    result = fn()
    print(json.dumps(result, ensure_ascii=False, indent=2))
