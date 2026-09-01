"""Open calls: where to put the work, rather than where to go and look at it.

A call is not an exhibition with a different date, so it does not live in the
same inventory. An exhibition has a run and a distance: closing soon means go
now, and Leipzig is walkable while Berlin is a train. A call has a deadline and
an eligibility rule: distance is irrelevant because you can apply to Iceland
from Leipzig, closing soon may mean it is already too late to assemble a
portfolio, and a third of them charge you to enter.

Two sources, chosen because they fail in opposite directions:

  bbk-bundesverband.de   a plain table kept by the German artists' association.
                         Small, curated, almost no noise, and where the
                         regional money is - a one-month sculpture stipend an
                         hour from Leipzig, Kunst am Bau commissions.
  artconnect.com         several hundred international opportunities in a
                         structured blob, with fees, deadlines, required
                         materials and restrictions already typed.

The catch with the second is that its artistic-field tags are self-declared:
nineteen listings in eighty tick all twenty-five categories, so a naive filter
on "sculpture" returns mostly calls open to anybody. Tag breadth is treated as
a confidence signal, the same way an exhibition with no description is not the
same as one that turned out not to be sculpture.
"""

import argparse
import json
import os
import re
import sys
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

SCULPTURE_FIELDS = {"SCULPTURE", "INSTALLATION", "PUBLIC_ART", "APPLIED_ARTS"}

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


def _artconnect_payload(html):
    match = _NEXT_DATA.search(html or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
        return data["props"]["pageProps"]["opportunities"]
    except (ValueError, KeyError):
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
# scoring
# --------------------------------------------------------------------------

def specificity(call):
    """How narrowly a call describes who it wants.

    A listing that ticks every artistic field is telling you nothing. One that
    ticks two is telling you a lot.
    """
    count = len(call.get("fields") or [])
    if not count:
        return "untagged"
    return "open to all" if count >= SHOTGUN_FIELDS else "specific"


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


def score(call, today=None):
    """Annotate a call in place. Nothing is discarded."""
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
        rank -= 40                      # pay to play
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
    scan = eligibility_scan(call)
    named = _demonyms(scan)
    if named:
        if _home(named) or _says_open(scan):
            return "eligible", named
        return "closed", named

    text = eligibility_text(call)
    if not text:
        return "open", []

    import llm
    if not llm.available():
        return "unknown", []
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
    if not llm.available():
        for call in calls:
            call.setdefault("eligibility", "unknown" if call.get("restrictions")
                            else "open")
        if verbose:
            print("  eligibility: no model reachable, terms left unread")
        return 0

    cache = llm.load_cache()
    spent = 0
    for call in sorted(calls, key=lambda c: -(c.get("rank") or 0)):
        named = _demonyms(eligibility_scan(call))
        text = eligibility_text(call)
        if not named and not text:
            call["eligibility"] = "open"
            call["open_to"] = []
            continue
        if not named and spent >= budget and llm.cache_key(
                "eligibility.2", text.strip()[:llm.MAX_CHARS]) not in cache:
            call.setdefault("eligibility", "unknown")
            continue
        before = len(cache)
        verdict, countries = eligibility_of(call, cache)
        call["eligibility"] = verdict
        call["open_to"] = countries
        if len(cache) != before:
            spent += 1
    llm.save_cache(cache)
    if verbose:
        shut = sum(1 for c in calls if c.get("eligibility") == "closed")
        print("  eligibility: %d terms read, %d calls are shut to you"
              % (spent, shut))
    return spent


# --------------------------------------------------------------------------
# inventory
# --------------------------------------------------------------------------

KEEP = ("title", "organisation", "type", "deadline", "deadline_pattern",
        "recurrence", "city", "country", "place", "eligibility", "open_to",
        "url", "source", "source_url",
        "description", "description_en", "language", "fee", "fee_note",
        "requires", "rewards", "fields", "restrictions", "online", "promoted",
        "sculpture", "sculpture_why", "specificity", "status", "days_left",
        "rank")


def load(path=CALLS_PATH):
    """The calls inventory, or an empty one."""
    if not os.path.exists(path):
        return {"calls": {}, "last_run": None}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("calls", {})
    data.setdefault("last_run", None)
    return data


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

    for call in calls:
        key = call["id"]
        if key in seen:
            continue
        seen.add(key)
        record = {field: call.get(field) for field in KEEP}
        record["last_seen"] = now
        existing = known.get(key)
        if existing:
            record["first_seen"] = existing.get("first_seen", now)
            sources = set(existing.get("sources") or [])
            sources.add(call.get("source"))
            record["sources"] = sorted(s for s in sources if s)
            # A verdict already reached is not unreached by a run that could
            # not reach it. The nightly job has no model, so without this it
            # would quietly replace every "closed" with "unknown" and put the
            # calls you cannot enter back on the board.
            if (record.get("eligibility") in (None, "unknown")
                    and existing.get("eligibility") not in (None, "unknown")):
                record["eligibility"] = existing["eligibility"]
                record["open_to"] = existing.get("open_to") or []
        else:
            record["first_seen"] = now
            record["sources"] = [call["source"]] if call.get("source") else []
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
    """Fetch both sources, score, and fold into the inventory."""
    inventory = load() if inventory is None else inventory
    found = scrape_bbk(verbose) + scrape_artconnect(
        pages=pages or ARTCONNECT_PAGES, verbose=verbose)

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

    scored = score_all(found)
    if eligibility:
        # Ranked first so the budget lands on the calls you might actually
        # enter, then ranked again because being shut out changes the order.
        resolve_eligibility(scored, verbose=verbose)
        scored = score_all(scored)
    fresh = merge(inventory, scored)
    if verbose:
        relevant = sum(1 for c in scored if c["sculpture"] == "yes")
        print("  calls: %d found, %d sculpture-relevant, %d new"
              % (len(scored), relevant, len(fresh)))
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
