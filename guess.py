"""A best guess at the medium of shows nothing else could classify: from the picture.

Keywords read the description and Wikidata knows the artist. That still left
157 shows unclassified, and they turned out to have almost nothing to read:
only 6 had any description, because index-berlin - where 129 of them come
from - publishes none, not even on a show's own page. A text model asked
about them would be guessing from a name, which for an emerging artist means
inventing. What most of them do have is the exhibition's picture, and the
picture usually shows the work.

So a vision model looks at it. gemma3:4b was measured before it was trusted:
about four seconds an image on the graphics card, "sculpture" for a ceramics
show, "installation" for Cevdet Erek, "painting" for a still life - and,
where the picture was a portrait photo standing in for a painting show,
"unclear" rather than a confident wrong answer. It may say "text or poster"
or "unclear", and then there is no guess.

A guess never moves a show out of "unclassified". It is shown as a guess -
"looks like installation", with what the model saw - and the unclassified
shows can be filtered by it. Answers are cached by image in guesses.json,
which is committed, so the nightly job applies them without a model.

Run: python guess.py   (update.py does this for you)
"""

import base64
import io
import json
import os
import sys

import llm
import scraper

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, "guesses.json")
VISION_MODEL = os.environ.get("VISION_MODEL", "gemma3:4b")
GUESS_BUDGET = 80             # new images per run; about five minutes
MAX_SIDE = 896                # what gemma3 looks at anyway

KINDS = ["sculpture", "installation", "ceramics", "painting", "drawing",
         "print", "photography", "video", "textile", "mixed media",
         "text or poster", "unclear"]

# The kinds, grouped into what the page filters by. "text or poster" and
# "unclear" are the model declining to guess, and are kept as no guess.
FAMILY = {
    "sculpture": "sculpture", "installation": "sculpture",
    "ceramics": "sculpture",
    "painting": "painting", "drawing": "painting", "print": "painting",
    "photography": "photo/video", "video": "photo/video",
    "textile": "other", "mixed media": "other",
}

SCHEMA = {
    "type": "object",
    "properties": {"kind": {"type": "string", "enum": KINDS},
                   "seen": {"type": "string"}},
    "required": ["kind", "seen"],
}
PROMPT = (
    "This is the announcement image of an art exhibition. Look at the artwork "
    "in it and say what kind of work it is.\n"
    "kind: one of " + ", ".join(KINDS) + ".\n"
    "Use \"text or poster\" when the image is mostly lettering, a logo or a "
    "graphic poster, and \"unclear\" when you cannot tell. Do not guess.\n"
    "seen: at most 12 words describing what is actually visible.\n")


def unclassified(event):
    """Nothing - keywords, the artist, the venue - decided this show's medium."""
    return (event.get("medium_confidence") == "unknown"
            and not event.get("medium_tier"))


def load_cache(path=CACHE_PATH):
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_cache(cache, path=CACHE_PATH):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")


def cache_key(url):
    return "%s|%s" % (VISION_MODEL, url)


def vision_available():
    """Is the vision model installed on a reachable Ollama?"""
    try:
        tags = scraper._session.get(llm.ENDPOINT + "/api/tags", timeout=4).json()
    except Exception:                                          # noqa: BLE001
        return False
    names = [m.get("name", "") for m in tags.get("models", [])]
    return VISION_MODEL in names


def image_payload(url):
    """The image, reduced and re-encoded for the model; None if it is not one.

    index-berlin's image links pointed at a domain it had left, and every one
    returned its homepage as HTML with a 200. Content is checked, not status.
    """
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        response = scraper._session.get(url, timeout=scraper.REQUEST_TIMEOUT)
        response.raise_for_status()
        if not response.headers.get("Content-Type", "").startswith("image/"):
            return None
        image = Image.open(io.BytesIO(response.content)).convert("RGB")
    except Exception:                                          # noqa: BLE001
        return None
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def look(url):
    """What the vision model makes of one image: {kind, seen}, or None."""
    payload = image_payload(url)
    if not payload:
        return None
    try:
        response = scraper._session.post(
            llm.ENDPOINT + "/api/generate", timeout=llm.TIMEOUT,
            json={"model": VISION_MODEL, "prompt": PROMPT, "images": [payload],
                  # 80 tokens cut one answer off mid-sentence, leaving JSON
                  # that would not parse; a dozen words need more headroom.
                  "stream": False, "format": SCHEMA,
                  "options": {"temperature": 0, "num_predict": 160}})
        response.raise_for_status()
        answer = json.loads(response.json()["response"])
    except Exception as exc:                                   # noqa: BLE001
        print("    ! vision model failed: %s" % str(exc)[:80])
        return None
    kind = answer.get("kind")
    if kind not in KINDS:
        return None
    return {"kind": kind, "seen": " ".join((answer.get("seen") or "").split())[:100]}


def apply(event, answer):
    """Write a guess onto a show, or clear one that no longer applies.

    The stored rank is never touched: it belongs to the classifier. The small
    lift a guess earns is added by the page when it sorts (board.to_row).
    Adjusting the stored rank here broke the moment a show was re-scored
    while an old adjustment was still recorded against it.
    """
    family = FAMILY.get((answer or {}).get("kind"))
    if not family or not unclassified(event):
        for field in ("medium_guess", "guess_family", "guess_seen"):
            event[field] = None
        return False
    event["medium_guess"] = answer["kind"]
    event["guess_family"] = family
    event["guess_seen"] = answer["seen"]
    return True


def enrich(events, cache=None, budget=GUESS_BUDGET, allow_network=True,
           verbose=True, cache_path=CACHE_PATH):
    """Guess the unclassified shows that have a picture. Returns the cache."""
    cache = load_cache(cache_path) if cache is None else cache
    looking = allow_network and vision_available()
    # Distinct pictures: several shows can share one, and each is looked at once.
    todo = {cache_key(e["image"]) for e in events if unclassified(e) and e.get("image")
            and cache_key(e["image"]) not in cache}
    total = min(len(todo), budget) if looking else 0
    looked = guessed = 0
    for event in events:
        if not (unclassified(event) and event.get("image")):
            apply(event, None)
            continue
        key = cache_key(event["image"])
        if key not in cache and looking and looked < total:
            looked += 1
            answer = look(event["image"])
            if answer is not None:
                cache[key] = answer
            if verbose:
                print("  looking at pictures %d of %d" % (looked, total), flush=True)
        guessed += apply(event, cache.get(key))
    if looked and cache_path:
        save_cache(cache, cache_path)
    if verbose:
        waiting = sum(1 for e in events if unclassified(e) and e.get("image")
                      and cache_key(e["image"]) not in cache)
        blind = sum(1 for e in events if unclassified(e) and not e.get("image"))
        print("  guesses: %d shows guessed from their picture, %d pictures looked "
              "at, %d waiting%s, %d with no picture to go on"
              % (guessed, looked, waiting,
                 "" if looking else " (no vision model here)", blind))
    return cache


def main(argv=None):
    """Guess for every unclassified show in the inventory."""
    import state as state_mod
    st = state_mod.load()
    events = list(st["events"].values())
    enrich(events, budget=int((argv or sys.argv[1:] or [GUESS_BUDGET])[0]))
    state_mod.save(st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
