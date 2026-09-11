"""Open calls: where to put the work, rather than where to go and look at it.

A call is not an exhibition with a different date, so it does not live in the
same inventory. An exhibition has a run and a distance: closing soon means go
now, and Leipzig is walkable while Berlin is a train. A call has a deadline and
an eligibility rule: distance is irrelevant because you can apply to Iceland
from Leipzig, closing soon may mean it is already too late to assemble a
portfolio, and a third of them charge you to enter.

Three sources, which fail in different directions:

  bbk-bundesverband.de   a plain table kept by the German artists' association.
                         Small, curated, almost no noise, and where the
                         regional money is - a one-month sculpture stipend an
                         hour from Leipzig, Kunst am Bau commissions.
  artconnect.com         several hundred international opportunities in a
                         structured blob, with fees, deadlines, required
                         materials and restrictions already typed.
  opencallforartists     the site behind a 219k-follower Instagram feed. Most
                         of it is new to the other two, and most of what it
                         carries charges an entry fee, which is why paying to
                         enter and paying to be shown are told apart below.

The same call often arrives from two of them under different titles, so they
are merged, and the merged record keeps one id for good.

The catch with the second is that its artistic-field tags are self-declared:
nineteen listings in eighty tick all twenty-five categories, so a naive filter
on "sculpture" returns mostly calls open to anybody. Tag breadth is treated as
a confidence signal, the same way an exhibition with no description is not the
same as one that turned out not to be sculpture.
"""

import argparse
import functools
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta

import scoring
import scraper

HERE = os.path.dirname(os.path.abspath(__file__))
CALLS_PATH = os.path.join(HERE, "calls.json")

BBK_URL = ("https://www.bbk-bundesverband.de/ausschreibungen/"
           "aktuelle-ausschreibungen")
ARTCONNECT_URL = "https://www.artconnect.com/opportunities"
# ArtConnect sorts by deadline, soonest first, so the early pages are calls
# closing this week - the ones there is no longer time to enter. The useful
# horizon is further in, which is why this reads deep rather than wide.
ARTCONNECT_PAGES = 30

RETENTION_DAYS = 120          # keep closed calls this long, then forget them

# A call wants more warning than a show. Five days to see an exhibition is
# plenty; five days to assemble a portfolio, a statement and a project
# description is not. But a single "urgent" flag turned out to be useless:
# ArtConnect is sorted deadline-first, so half of everything read lands inside
# any threshold worth setting, and a board where 156 of 330 cards shout is a
# board with no signal in it. Two bands instead, answering different questions.
CLOSING_DAYS = 7              # decide today or let it go
SOON_DAYS = 21                # enterable, if you start this week

# ArtConnect types, mapped to something readable.
CALL_TYPES = {
    "ART_RESIDENCY": "residency",
    "OPEN_CALL": "open call",
    "AWARD_OR_PRICE": "award",
    "GRANT_OR_STIPEND": "grant",
    "COMMISSION": "commission",
    "CALL_FOR_CURATORS": "curators",
    "COLLABORATION": "collaboration",
    "JOB": "job",
    "EDUCATION": "course",
    "EXHIBITION": "exhibition",
    "FESTIVAL": "festival",
    "WORKSHOP": "workshop",
    "PUBLICATION": "publication",
    "MARKET": "market",
    "COMPETITION": "competition",
}

# A listing claiming this many fields is saying "anyone may apply", which is
# not the same as wanting sculpture.
SHOTGUN_FIELDS = 12

SCULPTURE_FIELDS = {"SCULPTURE", "INSTALLATION", "PUBLIC_ART", "APPLIED_ARTS",
                    "CERAMICS"}

# Words that only ever mean sculpture. One of these is enough.
_STRONG_WORDS = [
    "skulptur", "bildhauer", "bildhauerei", "bildhauerin", "bildhauersymposium",
    "plastik", "plastisches", "relief", "installation", "assemblage",
    "raumobjekt", "raumbezogen", "skulpturenpark", "skulpturenweg",
    "kunst am bau", "kunst-am-bau", "percent for art",
    "sculpture", "sculptor", "sculptural", "bronze", "terrakotta", "terracotta",
    "keramik", "ceramic", "gips", "plaster", "epoxidharz", "giesserei",
    "gießerei", "foundry", "brennofen", "kiln", "marmor", "alabaster",
]
# Words that mean sculpture in the right company and nothing on their own. A
# call that mentions "stone" once is usually saying "stepping stone".
_WEAK_WORDS = [
    "stein", "stone", "holz", "wood", "stahl", "steel", "eisen", "iron",
    "kupfer", "copper", "messing", "brass", "beton", "concrete", "wachs",
    "wax", "harz", "resin", "ton", "clay", "porzellan", "porcelain",
    "guss", "gegossen", "cast", "carved", "geschnitzt", "modelliert",
]

_ENDINGS = r"(?:e|en|es|er|n|s|in|innen)?"


def _word_re(words):
    # Both boundaries matter. Without the trailing one "stone" matched inside
    # "cornerstone" and "cast" inside "castle".
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")"
                      + _ENDINGS + r"\b", re.IGNORECASE)


_STRONG_RE = _word_re(_STRONG_WORDS)
_WEAK_RE = _word_re(_WEAK_WORDS)

# Kunst am Bau: a paid public commission, and the one category here that is
# reliably about making something large out of material.
_KAB_RE = re.compile(r"\bkab\b|kunst am bau|kunst-am-bau", re.IGNORECASE)


def _empty_call(source, source_url):
    return {
        "id": None,
        "title": "",
        "organisation": None,
        "type": None,
        "deadline": None,          # ISO date, or datetime when the hour matters
        "recurrence": None,        # for the ones that come round every year
        "city": None,
        "country": None,
        "place": None,             # as a person would say it, when given
        "url": None,               # where to actually apply
        "source": source,
        "source_url": source_url,
        "description": "",
        "language": None,
        "fee": None,               # None unknown, False free, True charges
        "fee_note": None,
        "requires": [],
        "rewards": [],
        "fields": [],
        "restrictions": None,
        "online": False,
    }


def call_id(organisation, title, deadline):
    """Stable across runs and across sources listing the same call."""
    return scraper.event_id(organisation or "", title or "", deadline)


# --------------------------------------------------------------------------
# bbk-bundesverband.de
# --------------------------------------------------------------------------

_DE_DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")


def _bbk_deadline(text):
    match = _DE_DATE.search(text or "")
    if not match:
        return None
    day, month, year = (int(x) for x in match.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def parse_bbk(html):
    """Both BBK tables: the dated calls, and the ones that come round yearly."""
    page = scraper.soup(html)
    out = []
    for table in page.select("table"):
        headers = [scraper.clean(th.get_text(" ")).lower()
                   for th in table.select("thead th")]
        if not headers or "titel" not in headers:
            continue
        index = {name: i for i, name in enumerate(headers)}
        recurring = "turnus" in index

        for row in table.select("tbody tr"):
            cells = row.select("td")
            if len(cells) < len(headers) - 1:
                continue

            def cell(name):
                position = index.get(name)
                if position is None or position >= len(cells):
                    return ""
                return scraper.clean(cells[position].get_text(" "))

            title = cell("titel")
            if not title:
                continue

            call = _empty_call("bbk", BBK_URL)
            call["title"] = title
            call["organisation"] = cell("organisation") or None
            call["language"] = "de"
            call["deadline"] = _bbk_deadline(cell("endet am"))
            if recurring:
                call["recurrence"] = cell("turnus") or None
                # "31.01./15.08." is a pattern, not a date this year.
                call["deadline"] = call["deadline"] or None
                call["deadline_pattern"] = cell("endet am") or None

            for link in row.select("a[href]"):
                href = link["href"]
                if "bbk-bundesverband.de/fileadmin" in href:
                    continue                     # the PDF copy, not the call
                if href.startswith("http"):
                    call["url"] = scraper.https_url(href)
                    break
            call["description"] = title

            call["id"] = call_id(call["organisation"], title,
                                 call["deadline"] or call.get("deadline_pattern"))
            out.append(call)
    return out


def scrape_bbk(verbose=True):
    """Current Ausschreibungen from the German artists' association."""
    try:
        html = scraper.fetch(BBK_URL)
    except scraper.FetchError as exc:
        print("  ! bbk failed: %s" % exc)
        return []
    calls = parse_bbk(html)
    if verbose:
        dated = sum(1 for c in calls if c["deadline"])
        print("  bbk: %d calls (%d with a deadline, %d recurring)"
              % (len(calls), dated, len(calls) - dated))
    return calls


# --------------------------------------------------------------------------
# artconnect.com
# --------------------------------------------------------------------------

_NEXT_DATA = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)


# Next.js app-router pages stream their data as JavaScript string literals
# inside self.__next_f.push([1, "..."]) calls rather than one JSON block.
_RSC_CHUNK = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', re.S)
_RSC_LISTING = re.compile(r'\{\s*"data"\s*:\s*\[')


def _artconnect_payload(html):
    """The listing object: {data, entries, pages, total}.

    ArtConnect moved from one __NEXT_DATA__ block to the streamed app-router
    format some time after 1 September, and the parser read nothing until it
    was noticed by hand. The records inside are unchanged, so both layouts
    are read and whichever is present wins.
    """
    match = _NEXT_DATA.search(html or "")
    if match:
        try:
            data = json.loads(match.group(1))
            return data["props"]["pageProps"]["opportunities"]
        except (ValueError, KeyError):
            pass
    try:
        text = "".join(json.loads(chunk) for chunk in _RSC_CHUNK.findall(html or ""))
    except ValueError:
        return None
    decoder = json.JSONDecoder()
    for start in _RSC_LISTING.finditer(text):
        try:
            obj, _ = decoder.raw_decode(text, start.start())
        except ValueError:
            continue
        records = obj.get("data")
        if (isinstance(records, list) and records and isinstance(records[0], dict)
                and "postLifetime" in records[0]):
            return obj
    return None


def _plain(blocks):
    """ArtConnect stores descriptions as rich-text blocks."""
    if isinstance(blocks, str):
        return scraper.clean(blocks)
    out = []
    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("content"), str):
                out.append(node["content"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(blocks)
    return scraper.clean(" ".join(out))


def parse_artconnect(record):
    """One opportunity from the embedded JSON."""
    title = scraper.clean(record.get("title"))
    if not title:
        return None
    call = _empty_call("artconnect", ARTCONNECT_URL)
    call["title"] = title
    call["type"] = CALL_TYPES.get(record.get("type"), record.get("type"))
    call["language"] = "en"

    profile = record.get("profile") or {}
    call["organisation"] = scraper.clean(
        profile.get("organizationName")
        or " ".join(filter(None, [profile.get("firstName"),
                                  profile.get("lastName")]))) or None

    call["deadline"] = _iso_stamp(record.get("deadline")
                                  or record.get("postLifetime"))
    call["city"] = scraper.clean(record.get("city")) or None
    call["country"] = record.get("country") or None
    # locations[].description is already written out the way you would say it
    # ("Mexico City, CDMX, Mexico"), which beats reassembling an ISO code.
    for location in record.get("locations") or []:
        call["place"] = scraper.clean(location.get("description")) or None
        call["country"] = call["country"] or location.get("country")
        call["city"] = call["city"] or scraper.clean(location.get("city"))
        break
    call["online"] = bool(record.get("isOnline"))

    contact = record.get("contact") or record.get("apply") or {}
    call["url"] = scraper.https_url(contact.get("url")) if contact.get("url") else None
    call["description"] = _plain(record.get("description"))

    fee = record.get("fee")
    if fee == "FREE":
        call["fee"] = False
    elif fee == "FEES":
        call["fee"] = True
    participation = record.get("participationFee") or {}
    if participation.get("price"):
        call["fee"] = True
        call["fee_note"] = "%s %s" % (participation.get("price"),
                                      participation.get("currency") or "")
        note = scraper.clean(participation.get("description"))
        if note:
            call["fee_note"] += " - " + note[:120]

    call["requires"] = [_readable(x) for x in
                        ((record.get("required") or {}).get("items") or [])]
    call["rewards"] = [_readable(x) for x in
                       ((record.get("rewards") or {}).get("rewardTypes") or [])]
    call["fields"] = list(record.get("artisticFields") or [])

    restrictions = record.get("restrictions") or {}
    bits = []
    for key in ("age", "nationality", "location", "language", "other"):
        value = restrictions.get(key)
        if isinstance(value, str) and value.strip():
            bits.append("%s: %s" % (key, scraper.clean(value)))
        elif isinstance(value, list) and value:
            bits.append("%s: %s" % (key, ", ".join(str(v) for v in value)))
    call["restrictions"] = " | ".join(bits)[:600] or None

    # Paid placement, so it can never be mistaken for relevance.
    call["promoted"] = bool((record.get("boost") or {}).get("isHighlighted"))
    call["id"] = call_id(call["organisation"], title, call["deadline"])
    return call


def _readable(token):
    return str(token or "").replace("_", " ").lower()


def _iso_stamp(value):
    """Deadline day. The clock time is deliberately thrown away.

    ArtConnect publishes deadlines as UTC instants, and they are real: the
    listing is sorted "deadline soonest", the days spread over three months,
    and they pile up on the 15th, the 30th and the 1st the way application
    deadlines do. That the value also equals postLifetime is the platform
    retiring the post when the call shuts, not a bug.

    What is not knowable from the listing is which midnight the instant was
    meant to be. 21:45Z, 22:00Z, 12:00Z and 16:00Z all appear, which is what a
    field entered in the organiser's own timezone looks like after conversion.
    Rendering that back in Berlin time would move some deadlines across
    midnight, and a deadline shown a day late costs a submission. So the day
    is kept, the hour is dropped, and the card says "by 7 Sep" - which is what
    you would act on anyway.
    """
    text = str(value or "")
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?", text)
    if not match:
        return None
    year, month, day = (int(x) for x in match.groups()[:3])
    try:
        stamp = date(year, month, day).isoformat()
    except ValueError:
        return None
    return stamp


def scrape_artconnect(pages=ARTCONNECT_PAGES, verbose=True):
    """Opportunities from ArtConnect's embedded listing data."""
    out = []
    total = None
    for page in range(1, pages + 1):
        try:
            html = scraper.fetch(ARTCONNECT_URL, params={"page": page})
        except scraper.FetchError as exc:
            print("  ! artconnect page %d failed: %s" % (page, exc))
            break
        payload = _artconnect_payload(html)
        if not payload:
            print("  ! artconnect: the embedded listing data has moved")
            break
        total = payload.get("total")
        batch = payload.get("data") or []
        if not batch:
            break
        if total and len(out) >= total:
            break
        for record in batch:
            call = parse_artconnect(record)
            if call:
                out.append(call)
    if verbose:
        print("  artconnect: %d of %s opportunities read"
              % (len(out), total if total is not None else "?"))
    return out


# --------------------------------------------------------------------------
# opencallforartists.com
# --------------------------------------------------------------------------

# The Instagram account @opencallforartists_ is the shop window and this is
# the stock room. Every post there is a listing here, with the organiser, the
# fee and a plain calendar date as separate fields, so nothing has to be read
# out of a caption and Instagram is never touched. The site is a JavaScript
# front end over this backend, which is undocumented and lives on a "dev"
# subdomain: it can vanish, and the health record says so when it does.
OCFA_API = "https://dev.opencallforartists.com/product"
OCFA_LISTING = "https://opencallforartists.com/listing/%s"
OCFA_PAGE = 100
OCFA_PAUSE = 0.6
OCFA_CACHE_PATH = os.path.join(HERE, "ocfa_cache.json")
# Listings read in full per run. Only ones not already cached cost a request,
# so after the first run this is a handful a day.
OCFA_DETAIL_BUDGET = 150
OCFA_REFETCH_DAYS = 14

OCFA_TYPES = {
    "Call For Artists": "open call",
    "Call For Submissions": "open call",
    "Call For Photography": "open call",
    "Call For Entries": "competition",
    "Residency": "residency",
    "Workshop": "workshop",
}

# Who may apply, as the organiser declared it.
OCFA_SCOPES = {"National": "national", "Local": "local",
               "Regional": "regional", "International": "international"}

# Detail fields worth keeping. The record also carries the organiser's email
# and phone number - sometimes a named person's - and those never leave the
# request: calls.json and the cache are committed to a public repository.
OCFA_DETAIL_FIELDS = ("description", "apply_now_link", "web_link", "instagram",
                      "artistic_fields", "prize_summary", "listing_type")

# Free-text media, mapped onto the tags ArtConnect uses so one set of rules
# reads both. Separators are not consistent - semicolons, commas, or nothing.
_OCFA_FIELDS = [
    ("SCULPTURE", r"sculpt"), ("INSTALLATION", r"installation"),
    ("CERAMICS", r"ceramic|pottery|porcelain"),
    ("PUBLIC_ART", r"public art|mural"),
    ("PAINTING", r"paint"), ("DRAWING", r"drawing|illustrat"),
    ("PHOTOGRAPHY", r"photo|lens"), ("PRINTMAKING", r"printmak"),
    ("DIGITAL", r"digital|new media"), ("VIDEO", r"video|film|moving image"),
    ("PERFORMANCE", r"performance"), ("MIXED_MEDIA", r"mixed media"),
    ("TEXTILE", r"textile|fibre|fiber"), ("SOUND", r"sound"),
    ("DESIGN", r"design"), ("CRAFT", r"craft"),
]
_OCFA_FIELD_RES = [(tag, re.compile(pattern, re.IGNORECASE))
                   for tag, pattern in _OCFA_FIELDS]
_ALL_FIELDS_RE = re.compile(
    r"\ball (?:disciplines|media|mediums|fine arts|art ?forms|artistic fields)\b"
    r"|\bany (?:medium|media|discipline)\b|^\s*open\b", re.IGNORECASE)


def _ocfa_fields(text):
    """'All fine arts; painting; sculpture' -> ['ALL', 'PAINTING', 'SCULPTURE']."""
    text = text or ""
    tags = [tag for tag, pattern in _OCFA_FIELD_RES if pattern.search(text)]
    if _ALL_FIELDS_RE.search(text):
        tags.insert(0, "ALL")
    return tags


def _web(url):
    """A link that works from the page: 'foundwork.art' would resolve relative."""
    url = (url or "").strip()
    if not url:
        return None
    if url.startswith(("mailto:", "https://", "http://")):
        return scraper.https_url(url)
    if url.startswith("//"):
        return "https:" + url
    return "https://" + url


def _prose(text):
    """Plain text from a description that may or may not be HTML."""
    text = text or ""
    if "<" in text and ">" in text:
        text = scraper.soup(text).get_text(" ")
        # Tags become spaces, which leaves "sculpture ," behind a closing tag.
        text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return scraper.clean(text)


# Organisers pick a category from a short menu and often pick the first one:
# "Hayama Artist Residency in Japan" is filed as a call for artists. The title
# says what it is, and the type decides whether its fee buys a studio or a
# mention, so the title wins over a generic category.
_TITLE_TYPES = [
    ("residency", re.compile(r"\bresiden(?:cy|cies|ce|z)\b", re.IGNORECASE)),
    ("grant", re.compile(r"\b(?:grant|fellowship|stipend|bursary|stipendium)s?\b",
                         re.IGNORECASE)),
    ("award", re.compile(r"\b(?:prize|award|preis)s?\b", re.IGNORECASE)),
    ("commission", re.compile(r"\bcommission\b|kunst am bau|public art",
                              re.IGNORECASE)),
]


def _refine_type(title, given):
    if given not in (None, "open call", "competition"):
        return given
    for label, pattern in _TITLE_TYPES:
        if pattern.search(title or ""):
            return label
    return given


def _place(city, country):
    city = scraper.clean(city)
    if city and city.isupper():
        city = city.title()          # "NEWYORK" is how some organisers type it
    return ", ".join(x for x in (city, scraper.clean(country)) if x) or None


def ocfa_list():
    """Every listing the site has ever carried; most are long closed."""
    out, offset, total = [], 0, None
    while total is None or offset < total:
        try:
            payload = scraper.fetch(OCFA_API + "/explore-opencalls/",
                                    params={"limit": OCFA_PAGE, "offset": offset},
                                    as_json=True)
        except scraper.FetchError as exc:
            print("  ! opencallforartists list failed at %d: %s"
                  % (offset, str(exc)[:70]))
            break
        batch = (payload or {}).get("data") or []
        total = (payload or {}).get("total_count") or 0
        if not batch:
            break
        out.extend(batch)
        offset += len(batch)
        time.sleep(OCFA_PAUSE)
    return out


def ocfa_detail(listing_id):
    """The full listing, reduced to the fields worth keeping."""
    try:
        payload = scraper.fetch(OCFA_API + "/fetch-opencall-detail/%s" % listing_id,
                                as_json=True)
    except scraper.FetchError:
        return None
    record = (payload or {}).get("data") or {}
    if isinstance(record, list):
        record = record[0] if record else {}
    return {field: record.get(field) for field in OCFA_DETAIL_FIELDS}


def load_ocfa_cache(path=OCFA_CACHE_PATH):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_ocfa_cache(cache, path=OCFA_CACHE_PATH):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")


def parse_ocfa(row, detail=None):
    """One listing: the list row, and its detail when it has been read."""
    title = scraper.clean(row.get("title"))
    if not title:
        return None
    detail = detail or {}
    call = _empty_call("ocfa", OCFA_LISTING % row.get("id"))
    call["title"] = title
    call["listing_id"] = row.get("id")
    call["organisation"] = scraper.clean(row.get("organization_title")) or None
    call["type"] = _refine_type(title, OCFA_TYPES.get(row.get("category")))
    call["deadline"] = _iso_stamp(row.get("event_deadline"))
    call["city"] = scraper.clean(row.get("city")) or None
    call["country"] = scraper.clean(row.get("country")) or None
    call["place"] = _place(row.get("city"), row.get("country"))
    call["online"] = row.get("type") == "Online Only"
    call["language"] = "en"
    call["scope"] = OCFA_SCOPES.get(row.get("eligibility"))

    if row.get("fee_type") == "Free":
        call["fee"] = False
    elif row.get("fee_type") == "Paid":
        call["fee"] = True
        price = row.get("price")
        if isinstance(price, (int, float)) and price > 0:
            call["fee_note"] = "$%g" % price

    # The Instagram caption is the short version; the detail has the terms.
    call["description"] = _prose(detail.get("description")
                                 or row.get("instagram_caption"))
    call["url"] = (_web(detail.get("apply_now_link")) or _web(detail.get("web_link"))
                   or call["source_url"])
    call["org_url"] = _web(detail.get("web_link"))
    handle = (row.get("instagram_handle") or "").strip().lstrip("@")
    call["org_instagram"] = (_web(detail.get("instagram"))
                             or ("https://www.instagram.com/%s/" % handle
                                 if handle else None))
    call["fields"] = _ocfa_fields(detail.get("artistic_fields"))
    if detail.get("prize_summary"):
        call["rewards"] = [scraper.clean(detail["prize_summary"])]

    # "Standard post" is what every organiser pays for. Anything above it -
    # an email campaign, a deadline highlight - bought reach, and the site's
    # own listings are its own virtual exhibitions.
    listing_type = scraper.fold(detail.get("listing_type"))
    house = scraper.fold(call["organisation"]) == "open call for artists"
    call["promoted"] = house or bool(listing_type and listing_type != "standard post")
    call["id"] = call_id(call["organisation"], title, call["deadline"])
    return call


def scrape_ocfa(budget=OCFA_DETAIL_BUDGET, verbose=True, today=None):
    """Open listings from opencallforartists.com, each read in full once."""
    today = today or date.today()
    rows = ocfa_list()
    open_rows = [r for r in rows
                 if (r.get("event_deadline") or "") >= today.isoformat()]
    cache = load_ocfa_cache()
    cutoff = (today - timedelta(days=OCFA_REFETCH_DAYS)).isoformat()
    fetched, out = 0, []
    for row in open_rows:
        key = str(row.get("id"))
        entry = cache.get(key)
        if (not entry or (entry.get("fetched") or "") < cutoff) and fetched < budget:
            detail = ocfa_detail(row.get("id"))
            fetched += 1
            time.sleep(OCFA_PAUSE)
            if detail is not None:
                entry = dict(detail, fetched=today.isoformat())
                cache[key] = entry
        call = parse_ocfa(row, entry)
        if call:
            out.append(call)
    if rows:
        # A closed listing is never asked for again, so it need not be kept.
        # Skipped when the list itself failed, or a bad night would empty it.
        keep = {str(r.get("id")) for r in open_rows}
        save_ocfa_cache({k: v for k, v in cache.items() if k in keep})
    if verbose:
        print("  opencallforartists: %d open of %d listed, %d read in full"
              % (len(open_rows), len(rows), fetched))
    return out


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def specificity(call):
    """How narrowly a call describes who it wants.

    A listing that ticks every artistic field is telling you nothing. One that
    ticks two is telling you a lot.
    """
    fields = call.get("fields") or []
    if not fields:
        return "untagged"
    if "ALL" in fields:
        return "open to all"          # said in words: "all disciplines"
    return "open to all" if len(fields) >= SHOTGUN_FIELDS else "specific"


def sculpture_relevance(call):
    """('yes'|'maybe'|'no', why) - never a silent verdict."""
    if _KAB_RE.search(call.get("title") or ""):
        return "yes", "Kunst am Bau"

    text = " ".join(filter(None, [call.get("title"), call.get("description"),
                                  call.get("description_en")]))
    strong = _STRONG_RE.search(text)
    if strong:
        return "yes", "says %s" % strong.group(1).lower()

    # A single generic material is not evidence; two of them usually are.
    weak = sorted({m.group(1).lower() for m in _WEAK_RE.finditer(text)})
    if len(weak) >= 2:
        return "yes", "says %s" % ", ".join(weak[:3])
    if weak:
        return "maybe", "mentions %s once" % weak[0]

    fields = set(call.get("fields") or [])
    if fields & SCULPTURE_FIELDS:
        if specificity(call) == "specific":
            return "yes", "tagged %s" % ", ".join(
                sorted(f.lower().replace("_", " ") for f in fields & SCULPTURE_FIELDS))
        return "maybe", "open to all media"
    if fields:
        return "no", "tagged for other media"
    return "maybe", "nothing says either way"


def days_left(call, today=None):
    deadline = (call.get("deadline") or "")[:10]
    try:
        return (date.fromisoformat(deadline) - (today or date.today())).days
    except ValueError:
        return None


def status_of(call, today=None):
    """closing | soon | open | closed | rolling."""
    left = days_left(call, today)
    if left is None:
        return "rolling"           # recurring or undated: always worth a look
    if left < 0:
        return "closed"
    if left <= CLOSING_DAYS:
        return "closing"
    return "soon" if left <= SOON_DAYS else "open"


# Paying to enter a juried prize is one thing; paying to be shown is another.
# The second is a business model more than an opportunity - a page in a book
# of two hundred artists, a slot on a screen in a Paris window - and it is a
# good part of what an Instagram open-call feed exists to sell. It is told
# apart by what comes back: real money, or exposure you bought.
# Narrow on purpose, each phrase the shape of the scheme and not merely a word
# in it. "Book of" alone flagged the Tom Stoddart Award, whose prize is a book
# of the winner's work from a real publisher; a bare "membership" flagged the
# Martin Parr Foundation for describing its own supporters' scheme.
_EXPOSURE_RE = re.compile(
    r"\b(?:virtual|online) (?:exhibition|gallery|show|showcase)s?\b"
    r"|\bdigital (?:display|exhibition|screens?|billboard)s?\b"
    r"|\bmagazine\b|\bbook of (?:\w+ ){0,4}(?:artists|photographers|creatives|"
    r"sculptors|painters|makers|illustrators)\b"
    r"|\b(?:be |get )?published in\b|\bprinted in\b"
    r"|\bartist spotlight\b|\bartist interview\b|\b(?:be|get) featured\b"
    r"|\bfeatured in\b|\bmember artists?\b"
    r"|\b(?:paid|annual|monthly|yearly) membership\b", re.IGNORECASE)
# A number ends on a digit, so "$2,000." leaves its full stop to the sentence.
_MONEY_RE = re.compile(
    r"[$€£]\s?(\d(?:[\d.,]*\d)?)\s*(k\b)?"
    r"|(\d(?:[\d.,]*\d)?)\s*(k\b)?\s?(?:usd|eur|gbp|euros?|dollars?|pounds)\b",
    re.IGNORECASE)
# Not every amount is money you could win. "$20 per entry" is the fee, and
# "$10000+ Artist Package" is a valuation of the promotion being sold - the
# Arts to Hearts magazine call slipped through as a ten-thousand-dollar prize
# before these were read.
_FEE_NEAR = re.compile(
    r"\b(?:fees?|entry|entries|application|submission|registration|"
    r"per (?:image|work|piece|entry|artwork|submission))\b", re.IGNORECASE)
_VALUE_NEAR = re.compile(
    r"\b(?:package|packages|value|valued|worth|estimated|in services|in kind|"
    r"voucher|gift card|credit|discount|exposure|promotion|marketing)\b",
    re.IGNORECASE)
_SENTENCE = re.compile(r"[.!?;]\s|\n")
# Any real cash makes it a prize, however modest: KANE pays £200 and a London
# show, which is a small prize and not a purchase of exposure.
CASH_FLOOR = 100
# Where the fee buys the thing itself: a residency's fee is for a studio and
# time, not for a mention.
_NOT_EXPOSURE_TYPES = {"residency", "grant", "commission", "job", "course",
                       "workshop", "curators", "collaboration"}


def prize_money(text):
    """The biggest amount in a text that reads as money you could win."""
    text = text or ""
    best = 0.0
    for match in _MONEY_RE.finditer(text):
        # Wide enough for "$10,000+ Estimated Artist Package", and cut at the
        # sentence, so "Winner receives $2,000. Entry is $30." keeps its prize.
        before = _SENTENCE.split(text[max(0, match.start() - 22):match.start()])[-1]
        after = _SENTENCE.split(text[match.end():match.end() + 30])[0]
        near = before + " " + after
        if _FEE_NEAR.search(near) or _VALUE_NEAR.search(near):
            continue
        raw = match.group(1) or match.group(3) or ""
        thousands = match.group(2) or match.group(4)
        # 10,000 and 10.000 are both ten thousand; 1,5 is one and a half.
        raw = re.sub(r"[.,](?=\d{3}(?!\d))", "", raw).replace(",", ".").rstrip(".")
        try:
            value = float(raw)
        except ValueError:
            continue
        best = max(best, value * 1000 if thousands else value)
    return best


def pay_to_play(call):
    """(True, why) when the fee buys exposure rather than a chance at something."""
    if call.get("fee") is not True or call.get("type") in _NOT_EXPOSURE_TYPES:
        return False, None
    rewards = " ".join(call.get("rewards") or [])
    body = (call.get("description") or "")[:4000]
    if prize_money(rewards + " . " + body) >= CASH_FLOOR:
        return False, None             # a real prize: paying to enter, not to be shown
    match = _EXPOSURE_RE.search(" ".join([call.get("title") or "", rewards, body]))
    if match:
        return True, match.group(0).lower()
    if call.get("online"):
        return True, "online only"
    return False, None


def score(call, today=None):
    """Annotate a call in place. Nothing is discarded."""
    paid, paid_why = pay_to_play(call)
    call["pay_to_play"] = paid
    call["pay_why"] = paid_why
    relevance, why = sculpture_relevance(call)
    call["sculpture"] = relevance
    call["sculpture_why"] = why
    call["specificity"] = specificity(call)
    call["status"] = status_of(call, today)
    call["days_left"] = days_left(call, today)

    rank = {"yes": 300, "maybe": 120, "no": 0}[relevance]
    if call["specificity"] == "specific":
        rank += 40
    if call.get("fee") is False:
        rank += 30                      # free to enter
    elif call.get("fee") is True:
        rank -= 40                      # a fee to enter
    if paid:
        # Below every call that is a chance at something; still on the board,
        # because "as much as possible in one place" was the brief.
        rank -= 120
    if call.get("promoted"):
        rank -= 25                      # someone paid to be seen; that is not merit
    if call.get("source") == "bbk":
        rank += 25                      # curated, local, and rarely junk
    if call.get("eligibility") == "closed":
        # Not a near miss - you are not allowed to enter. It stays visible so
        # a misreading can be caught, but it stops competing for attention.
        rank -= 250
    if call["status"] == "closing":
        # Worth surfacing, but not worth promoting over a call you could
        # actually prepare for: a week is not long enough to build a piece.
        rank += 10
    elif call["status"] == "soon":
        rank += 20
    elif call["status"] == "closed":
        rank -= 500
    call["rank"] = rank
    return call


def score_all(calls, today=None):
    """Score everything and sort. Closed calls sink; nothing is dropped."""
    for call in calls:
        score(call, today)
    return sorted(calls, key=lambda c: (-c["rank"], c.get("deadline") or "9999"))


# --------------------------------------------------------------------------
# eligibility
# --------------------------------------------------------------------------

# Words that mean "you are included" wherever they appear in a country list.
HOME_TERMS = {"germany", "deutschland", "german", "deutsche", "deutschland",
              "eu", "europe", "european", "european union", "eea", "schengen",
              "international", "worldwide", "global", "any country",
              "all countries", "all nationalities", "saxony", "sachsen"}

# Only bother the model where there is something to read.
ELIGIBILITY_BUDGET = 60


def _home(countries):
    """Is a Leipzig-based artist inside this list of countries?"""
    return any(scraper.fold(c) in HOME_TERMS or scraper.fold(c) in _HOME_DEMONYMS
               for c in countries or [])


# Phrases that mean the door is open regardless of what else the text names.
_OPEN_RE = re.compile(
    r"\b(?:internationals?|worldwide|world[- ]wide|any nationality|"
    r"all nationalities|regardless of nationality|no nationality|"
    r"any country|all countries|from anywhere|globally)\b", re.IGNORECASE)


def _says_open(text):
    """Does the text explicitly welcome everyone?

    Shutting a call you could have entered removes it for good, while leaving
    one in costs a few seconds of reading. So when the text contradicts
    itself, the door stays open.
    """
    return bool(_OPEN_RE.search(text or ""))


# A nationality adjective sitting directly in front of the people being asked
# for. "invites Canadian artists" is a hard stop written as an invitation, and
# the model reads it as hospitality no matter how the prompt is worded - it is
# a pattern rather than a judgement, so it is matched rather than asked about.
_DEMONYMS = """
afghan albanian algerian american andorran angolan argentine argentinian
armenian australian austrian azerbaijani bahraini bangladeshi barbadian
belarusian belgian belizean beninese bhutanese bolivian bosnian botswanan
brazilian british bruneian bulgarian burkinabe burmese burundian cambodian
cameroonian canadian cape-verdean catalan chadian chilean chinese colombian
comoran congolese costa-rican croatian cuban cypriot czech danish djiboutian
dominican dutch ecuadorean ecuadorian egyptian emirati english eritrean
estonian ethiopian fijian filipino finnish flemish french gabonese gambian
georgian german ghanaian greek grenadian guatemalan guinean guyanese haitian
honduran hungarian icelandic indian indonesian iranian iraqi irish israeli
italian ivorian jamaican japanese jordanian kazakh kenyan korean kosovar
kuwaiti kyrgyz lao latvian lebanese liberian libyan liechtenstein lithuanian
luxembourgish macedonian malagasy malawian malaysian maldivian malian maltese
mauritanian mauritian mexican moldovan monegasque mongolian montenegrin
moroccan mozambican namibian nepalese nepali dutch new-zealand nicaraguan
nigerien nigerian norwegian omani pakistani palestinian panamanian paraguayan
peruvian philippine polish portuguese qatari romanian russian rwandan salvadoran
samoan saudi scottish senegalese serbian seychellois sierra-leonean singaporean
slovak slovakian slovene slovenian somali south-african spanish sri-lankan
sudanese surinamese swazi swedish swiss syrian taiwanese tajik tanzanian thai
togolese tongan trinidadian tunisian turkish turkmen ugandan ukrainian uruguayan
uzbek venezuelan vietnamese welsh yemeni zambian zimbabwean
nordic baltic balkan scandinavian iberian caribbean andean levantine
african asian european latin-american mena
""".split()

# The ones that include a sculptor living in Leipzig. Everything else in the
# list, named as a requirement, means the call is not open to you.
_HOME_DEMONYMS = {"german", "european", "eu"}

_DEMONYM_ALT = "|".join(sorted((d.replace("-", "[- ]") for d in _DEMONYMS),
                               key=len, reverse=True))
# The whole coordinated list, not just the adjective touching the noun: in
# "German and Austrian artists" only Austrian is adjacent, and reading that
# alone turns a call you may enter into one you may not.
_DEMONYM_RE = re.compile(
    r"\b((?:(?:" + _DEMONYM_ALT + r")(?:,\s*|\s+(?:and|or)\s+))*"
    r"(?:" + _DEMONYM_ALT + r"))[- ]"
    r"(?:based\s+|born\s+|resident\s+)?(artists?|applicants?|citizens?|"
    r"nationals?|nationality|citizenship|passports?|descent|residents?|"
    r"creatives?|practitioners?|sculptors?|makers?|"
    r"photographers?|painters?|writers?)\b", re.IGNORECASE)
_SPLIT_RE = re.compile(r",\s*|\s+(?:and|or)\s+")

# "Swedish citizen" is a statement about status and needs no further evidence.
# "American artist" does: Ming Fay's obituary calls him a founder of "an Asian
# American artist collective", which is biography, and IDRA advertises "the
# presentation of Italian artists", which is a programme. Neither is a rule
# about who may apply, and excluding on either loses a call for good.
_STATUS_NOUNS = {"citizen", "citizens", "national", "nationals",
                 "nationality", "citizenship", "passport", "passports",
                 "descent", "resident", "residents"}

_CUE_RE = re.compile(
    r"\b(?:open to|invite[sd]?|invitation|call for|call is for|eligib\w*|"
    r"must be|must live|must have|may apply|can apply|are welcome|"
    r"we welcome|applicants?|application is|applications? (?:are|from)|"
    r"restricted to|limited to|reserved for|only for|aimed at|addressed to|"
    r"submissions? from|accepting|apply|qualif\w*|"
    r"richtet sich an|bewerben|können sich|zugelassen|teilnahmeberechtigt)"
    r"\b", re.IGNORECASE)

_CUE_BEFORE = 110         # characters of run-up that count as the same clause
_CUE_AFTER = 60


def _demonyms(text):
    """Nationalities the text requires its applicants to be.

    Requires, not merely mentions: a nationality in front of "artists" only
    counts when the surrounding clause is setting a condition.
    """
    text = text or ""
    out = []
    for match in _DEMONYM_RE.finditer(text):
        if match.group(2).lower() not in _STATUS_NOUNS:
            window = text[max(0, match.start() - _CUE_BEFORE):
                          match.end() + _CUE_AFTER]
            if not _CUE_RE.search(window):
                continue
        for word in _SPLIT_RE.split(match.group(1)):
            word = word.strip()
            if word and word.title() not in out:
                out.append(word.title())
    return out


def eligibility_text(call):
    """The passage sent to the model: the terms, or the body if there are none.

    Kept narrow on purpose. The more prose the model reads the more places it
    can find a country that is only the address of the gallery, and it is
    charged per call.
    """
    if call.get("restrictions"):
        return call["restrictions"]
    body = call.get("description_en") or call.get("description") or ""
    return body[:1200]


def eligibility_scan(call):
    """Everything worth scanning for a nationality, since scanning is free.

    Toronto's terms describe a two-stage competition and never mention Canada;
    the sentence that rules you out is the first line of the description,
    "invites Canadian artists to apply". Both have to be read, and only the
    model has to be rationed.
    """
    return " ".join(filter(None, [
        call.get("title"),
        call.get("restrictions"),
        (call.get("description_en") or call.get("description") or "")[:2000],
    ]))


def eligibility_of(call, cache=None):
    """'open' | 'eligible' | 'closed' | 'unknown', and who it is open to.

    open      nothing in the terms limits applicants by country
    eligible  it does, and Germany or Europe is one of them
    closed    it does, and you are not in the list
    unknown   there were terms but no model to read them

    Only the last two change what you would do, which is the point: a board
    that quietly hides a call it misread is worse than one that shows it.
    """
    free = free_eligibility(call)
    if free:
        return free
    text = eligibility_text(call)
    if not text:
        return "open", []
    import llm
    if not llm.available():
        return "unknown", []
    return _model_eligibility(text, cache)


def scope_eligibility(call):
    """What the organiser declared about who may apply, where they did.

    opencallforartists asks every organiser: international, national,
    regional or local, and which country. That is a stated rule rather than
    a reading of prose, so it goes first.
    """
    scope, country = call.get("scope"), call.get("country")
    if scope not in ("national", "regional", "local") or not country:
        return None
    if not _home([country]):
        return "closed", [country]
    # National and German means you. Regional or local and German could mean
    # Hamburg - shown and marked, rather than guessed at either way.
    return ("eligible" if scope == "national" else "unknown"), [country]


def free_eligibility(call):
    """The verdicts that need no model, or None.

    These run everywhere, including the nightly job, which has no model: a
    call that says "Canadian artists" is shut whether or not Ollama is up.
    """
    declared = scope_eligibility(call)
    if declared:
        return declared
    scan = eligibility_scan(call)
    named = _demonyms(scan)
    if named:
        if _home(named) or _says_open(scan):
            return "eligible", named
        return "closed", named
    return None


def _model_eligibility(text, cache=None):
    import llm
    restricted, countries = llm.eligibility(text, cache)
    if not restricted:
        return "open", []
    if _home(countries) or _says_open(text):
        return "eligible", countries
    return "closed", countries


def resolve_eligibility(calls, budget=ELIGIBILITY_BUDGET, verbose=True):
    """Read the terms of the calls worth reading the terms of.

    Ordered by rank, so the budget is spent on the ones you might enter. The
    rest keep whatever they had, and say 'unknown' rather than 'open'.
    """
    import llm
    model = llm.available()
    cache = llm.load_cache() if model else None
    spent = 0
    for call in sorted(calls, key=lambda c: -(c.get("rank") or 0)):
        # The free rules first, everywhere. Until this was split out, a run
        # with no model skipped them too and left every new call "unknown".
        free = free_eligibility(call)
        if free:
            call["eligibility"], call["open_to"] = free[0], list(free[1])
            continue
        text = eligibility_text(call)
        if not text:
            call["eligibility"], call["open_to"] = "open", []
            continue
        if not model:
            call.setdefault("eligibility", "unknown")
            continue
        if spent >= budget and llm.cache_key(
                "eligibility.2", text.strip()[:llm.MAX_CHARS]) not in cache:
            call.setdefault("eligibility", "unknown")
            continue
        before = len(cache)
        call["eligibility"], call["open_to"] = _model_eligibility(text, cache)
        if len(cache) != before:
            spent += 1
    if model:
        llm.save_cache(cache)
    if verbose:
        shut = sum(1 for c in calls if c.get("eligibility") == "closed")
        print("  eligibility: %s, %d calls are shut to you"
              % ("%d terms read" % spent if model else "no model, free rules only",
                 shut))
    return spent


# --------------------------------------------------------------------------
# the same call, listed twice
# --------------------------------------------------------------------------

# Whose record leads when one call arrives from two sources and neither
# version is already known. BBK first because it is curated; ArtConnect
# before opencallforartists because its records carry more structure.
SOURCE_PRIORITY = {"bbk": 0, "artconnect": 1, "ocfa": 2}

# Words that do not tell one organisation from another.
_ORG_NOISE = {
    "the", "of", "and", "for", "art", "arts", "artist", "artists", "gallery",
    "galerie", "galleries", "foundation", "stiftung", "project", "projects",
    "studio", "studios", "center", "centre", "museum", "institute",
    "association", "society", "collective", "residency", "residencies",
    "program", "programme", "festival", "international", "inc", "ltd", "llc",
    "gmbh", "ev", "cic", "group", "network", "house", "space", "contemporary",
    "fine", "city", "council", "kunst", "verein", "kunstverein",
}
# Words that do not tell one call from another.
_TITLE_NOISE = {
    "open", "call", "calls", "opencall", "for", "the", "and", "of", "in", "on",
    "at", "to", "an", "with", "by", "from", "your", "our", "now", "new",
    "artists", "artist", "art", "arts", "international", "competition",
    "contest", "exhibition", "exhibitions", "show", "application",
    "applications", "submission", "submissions", "entry", "entries",
    "deadline", "edition", "annual", "opportunity", "opportunities",
}


@functools.lru_cache(maxsize=8192)
def _tokens(text):
    return frozenset(w for w in scraper.fold(text or "").split()
                     if len(w) >= 2 and not w.isdigit())


def _day(value):
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def same_call(a, b):
    """Is this one opportunity seen through two sources?

    The organiser alone is not enough: TERAVARNA runs a new themed competition
    every few weeks, and two of them close on the same day. The title alone is
    not enough either, because sources rewrite it - "2026 Foundwork Artist
    Prize" is "2026 Foundwork Artist Prize: 10,000 USD Grant with Studio
    Visits and Interview" elsewhere. So the deadline has to agree, and then
    the words left once the organiser's name and the boilerplate are gone.
    """
    if a.get("source") == b.get("source"):
        return False                   # a source does not list one call twice
    da, db = _day(a.get("deadline")), _day(b.get("deadline"))
    if bool(da) != bool(db) or (da and abs((da - db).days) > 3):
        return False
    oa = _tokens(a.get("organisation")) - _ORG_NOISE
    ob = _tokens(b.get("organisation")) - _ORG_NOISE
    same_org = bool(oa & ob)
    ta = _tokens(a.get("title")) - _TITLE_NOISE - oa - ob
    tb = _tokens(b.get("title")) - _TITLE_NOISE - oa - ob
    if not ta or not tb:
        # A title that is nothing but the organiser's name and a year: only
        # the same organiser on the same day will do.
        return same_org and da == db
    shared = ta & tb
    if same_org:
        return len(shared) / min(len(ta), len(tb)) >= 0.5
    return len(shared) >= 2 and len(shared) / min(len(ta), len(tb)) >= 0.8


def merge_duplicate_calls(calls, known=None):
    """Fold the same call from several sources into one record.

    The id is the one thing that must not move: the application stage you
    set, and what counts as new, both hang on it. So a merged record keeps
    whichever id the inventory already knows, and remembers the others as
    aliases, so that the day one source drops the call the other still lands
    on the same record rather than arriving as a stranger.
    """
    known = known or {}
    alias_of = {alias: key for key, record in known.items()
                for alias in record.get("aliases") or []}
    for call in calls:
        call["id"] = alias_of.get(call["id"], call["id"])

    groups = []
    for call in calls:
        for group in groups:
            if all(m.get("source") != call.get("source") for m in group) \
                    and any(same_call(call, m) for m in group):
                group.append(call)
                break
        else:
            groups.append([call])

    def lead(call):
        record = known.get(call["id"])
        return (record is None, (record or {}).get("first_seen") or "",
                SOURCE_PRIORITY.get(call.get("source"), 9),
                -len(call.get("description") or ""))

    out = []
    for group in groups:
        if len(group) == 1:
            out.append(group[0])
            continue
        group.sort(key=lead)
        merged = dict(group[0])
        aliases = set(known.get(merged["id"], {}).get("aliases") or [])
        for other in group[1:]:
            if not merged.get("description") and other.get("description"):
                for field in ("description", "description_en", "language"):
                    merged[field] = other.get(field)
            for field, value in other.items():
                if field in ("id", "source", "source_url", "description",
                             "description_en", "language"):
                    continue
                if merged.get(field) in (None, "", []) and value not in (None, "", []):
                    merged[field] = value
            if other["id"] != merged["id"]:
                aliases.add(other["id"])
        merged["aliases"] = sorted(aliases)
        merged["sources"] = sorted({c.get("source") for c in group if c.get("source")})
        out.append(merged)
    return out


# --------------------------------------------------------------------------
# inventory
# --------------------------------------------------------------------------

KEEP = ("title", "organisation", "type", "deadline", "deadline_pattern",
        "recurrence", "city", "country", "place", "eligibility", "open_to",
        "url", "source", "source_url",
        "description", "description_en", "language", "fee", "fee_note",
        "requires", "rewards", "fields", "restrictions", "online", "promoted",
        "sculpture", "sculpture_why", "specificity", "status", "days_left",
        "rank", "scope", "listing_id", "org_url", "org_instagram",
        "pay_to_play", "pay_why")


def load(path=CALLS_PATH):
    """The calls inventory, or an empty one."""
    if not os.path.exists(path):
        return {"calls": {}, "last_run": None}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("calls", {})
    data.setdefault("last_run", None)
    data.setdefault("health", {})
    return data


def record_health(inventory, source, count, today=None):
    """Remember when each source last answered, and since when it has not.

    ArtConnect changed its page after 1 September and read nothing for ten
    days before anyone looked. A source that returns nothing is now written
    down on the night it happens, and the page says so after two.
    """
    today = (today or date.today()).isoformat()
    entry = inventory.setdefault("health", {}).setdefault(source, {})
    if count:
        entry.update(last_ok=today, last_count=count, failing_since=None)
    elif not entry.get("failing_since"):
        entry["failing_since"] = today
    return entry


def save(inventory, path=CALLS_PATH):
    """Write the calls inventory back."""
    inventory["last_run"] = datetime.now().isoformat(timespec="seconds")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(inventory, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")


def merge(inventory, calls, today=None):
    """Fold a scrape into the inventory. Returns the calls that are new."""
    known = inventory["calls"]
    now = datetime.now().isoformat(timespec="seconds")
    fresh, seen = [], set()
    alias_of = {alias: key for key, record in known.items()
                for alias in record.get("aliases") or []}

    for call in calls:
        key = alias_of.get(call["id"], call["id"])
        if key in seen:
            continue
        seen.add(key)
        record = {field: call.get(field) for field in KEEP}
        record["last_seen"] = now
        arriving = set(call.get("sources") or []) | {call.get("source")}
        existing = known.get(key)
        if existing:
            record["first_seen"] = existing.get("first_seen", now)
            sources = set(existing.get("sources") or []) | arriving
            record["sources"] = sorted(s for s in sources if s)
            record["aliases"] = sorted(set(existing.get("aliases") or [])
                                       | set(call.get("aliases") or []))
            # A verdict already reached is not unreached by a run that could
            # not reach it. The nightly job has no model, so without this it
            # would quietly replace every "closed" with "unknown" and put the
            # calls you cannot enter back on the board.
            if (record.get("eligibility") in (None, "unknown")
                    and existing.get("eligibility") not in (None, "unknown")):
                record["eligibility"] = existing["eligibility"]
                record["open_to"] = existing.get("open_to") or []
                # The rank was worked out before the verdict came back, so a
                # restored "closed" was still ranked like an open call - two
                # shut calls sat in the top twenty until this.
                score(record, today)
        else:
            record["first_seen"] = now
            record["sources"] = sorted(s for s in arriving if s)
            record["aliases"] = sorted(call.get("aliases") or [])
            fresh.append(call)
        known[key] = record

    # A call nobody listed this run has not vanished; only its clock moved.
    for key, record in known.items():
        if key not in seen:
            record["status"] = status_of(record, today)
            record["days_left"] = days_left(record, today)
    return fresh


def prune(inventory, retention_days=RETENTION_DAYS, today=None):
    """Forget calls whose deadline passed a while ago."""
    today = today or date.today()
    cutoff = (today - timedelta(days=retention_days)).isoformat()
    stale = [k for k, c in inventory["calls"].items()
             if c.get("status") == "closed" and (c.get("deadline") or "")[:10] < cutoff]
    for key in stale:
        del inventory["calls"][key]
    return len(stale)


def counts(inventory):
    """How many calls sit in each status."""
    tally = {}
    for record in inventory["calls"].values():
        tally[record.get("status", "rolling")] = \
            tally.get(record.get("status", "rolling"), 0) + 1
    return tally


def refresh(inventory=None, translate=True, verbose=True, pages=None,
            eligibility=True):
    """Fetch every source, merge the duplicates, score, and fold in."""
    inventory = load() if inventory is None else inventory
    found = []
    for name, fetch in (
            ("bbk", lambda: scrape_bbk(verbose)),
            ("artconnect", lambda: scrape_artconnect(
                pages=pages or ARTCONNECT_PAGES, verbose=verbose)),
            ("ocfa", lambda: scrape_ocfa(verbose=verbose))):
        try:
            got = fetch()
        except Exception as exc:                               # noqa: BLE001
            # One source failing must not take the others down with it.
            print("  ! %s failed outright: %s" % (name, str(exc)[:70]))
            got = []
        record_health(inventory, name, len(got))
        found.extend(got)

    if translate:
        try:
            import translate as translate_mod
            cache, _ = translate_mod.enrich(
                [dict(c, raw_description=c.get("description")) for c in found],
                verbose=False)
            translate_mod.save_cache(cache)
            lookup = translate_mod.load_cache()
            for call in found:
                if call.get("language") == "de" and call.get("description"):
                    hit = lookup.get(translate_mod._key(call["description"]))
                    if hit:
                        call["description_en"] = hit
        except Exception as exc:                               # noqa: BLE001
            print("  ! call translation skipped: %s" % str(exc)[:60])

    before = len(found)
    found = merge_duplicate_calls(found, known=inventory["calls"])
    scored = score_all(found)
    if eligibility:
        # Ranked first so the budget lands on the calls you might actually
        # enter, then ranked again because being shut out changes the order.
        resolve_eligibility(scored, verbose=verbose)
        scored = score_all(scored)
    fresh = merge(inventory, scored)
    if verbose:
        relevant = sum(1 for c in scored if c["sculpture"] == "yes")
        print("  calls: %d found (%d listed twice), %d sculpture-relevant, %d new"
              % (len(scored), before - len(scored), relevant, len(fresh)))
    return inventory, fresh


def main(argv=None):
    """Refresh the calls inventory and show what is worth applying to."""
    parser = argparse.ArgumentParser(description="Open calls")
    parser.add_argument("--no-translate", action="store_true")
    parser.add_argument("--no-eligibility", action="store_true",
                        help="skip reading the terms with the local model")
    parser.add_argument("--pages", type=int, default=None,
                        help="how many ArtConnect pages to read")
    parser.add_argument("--show", type=int, default=25)
    args = parser.parse_args(argv)

    inventory, _ = refresh(translate=not args.no_translate,
                           pages=args.pages or ARTCONNECT_PAGES,
                           eligibility=not args.no_eligibility)
    removed = prune(inventory)
    save(inventory)
    tally = counts(inventory)
    print("inventory: %d calls (%s)%s"
          % (len(inventory["calls"]),
             ", ".join("%s %d" % kv for kv in sorted(tally.items())),
             ", %d pruned" % removed if removed else ""))

    rows = [dict(c, id=k) for k, c in inventory["calls"].items()
            if c.get("status") != "closed"]
    rows.sort(key=lambda c: (-(c.get("rank") or 0), c.get("deadline") or "9999"))
    print()
    for call in rows[:args.show]:
        left = call.get("days_left")
        when = ("%3d days" % left) if left is not None else " rolling"
        fee = "FEE" if call.get("fee") else ("free" if call.get("fee") is False else "?")
        shut = "shut" if call.get("eligibility") == "closed" else ""
        print("  %-8s %-4s %-5s %-4s %-42s %s"
              % (when, fee, call.get("sculpture"), shut,
                 (call.get("title") or "")[:41],
                 (call.get("organisation") or "")[:24]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
