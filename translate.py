"""English descriptions: take the published one where it exists, translate the rest.

Three steps, cheapest first:

1. Some listings publish both languages in one field, separated by a rule
   ("(English below) ... --- ..."). That English is the gallery's own wording,
   so it beats any machine translation and costs nothing.
2. Text that already reads as English is left alone.
3. Whatever is left is translated once and cached in translations.json, keyed
   by a hash of the source text. A description is never translated twice, so
   the weekly cost is only the handful of shows that are new.

Translation is optional. With no provider configured the pipeline runs exactly
as before and descriptions stay in their original language.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time

import scraper

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "translations.json")

# A rule of three or more dashes is how these sites separate the two languages.
_LANG_SPLIT = re.compile(r"\s[-–—]{3,}\s")
_ENGLISH_MARKER = re.compile(r"\(?\s*English (?:below|version)\s*\)?|\(EN\)",
                             re.IGNORECASE)

# Function words are a better language signal than umlauts: plenty of German
# prose has none, and English quotations inside German text have none either.
_GERMAN_WORDS = {
    "der", "die", "das", "und", "ist", "mit", "von", "für", "fur", "ein",
    "eine", "einer", "eines", "im", "in", "den", "dem", "des", "sich", "auch",
    "werden", "wird", "sind", "nicht", "aus", "auf", "als", "sie", "bei",
    "zum", "zur", "durch", "über", "uber", "ihre", "seine", "dieser", "diese",
}
_ENGLISH_WORDS = {
    "the", "and", "is", "of", "with", "for", "a", "an", "in", "on", "to",
    "his", "her", "their", "this", "that", "are", "was", "were", "by", "from",
    "as", "at", "which", "these", "those", "been", "has", "have",
}

MYMEMORY_CHUNK = 450          # the anonymous endpoint rejects longer strings
REQUEST_PAUSE = 1.0


def _words(text):
    return re.findall(r"[a-zäöüßA-ZÄÖÜ]+", (text or "").lower())


def detect_language(text):
    """'en', 'de' or 'unknown'.

    Only a positive English reading counts as English. Short German fragments
    like "Hochdrucke" contain no function words at all, and treating "not
    detectably German" as English would publish German text under an English
    label - the one outcome worth avoiding. Anything uncertain is left for the
    translator, which is cheap and cannot mislead.
    """
    words = _words(text)
    if not words:
        return "unknown"
    de = sum(1 for w in words if w in _GERMAN_WORDS)
    en = sum(1 for w in words if w in _ENGLISH_WORDS)
    if en > de and en >= 2:
        return "en"
    if de > en or re.search(r"[äöüßÄÖÜ]", text or ""):
        return "de"
    return "unknown"


def looks_german(text):
    """Whether a description reads as German. Uncertain text counts as German."""
    return detect_language(text) != "en"


def published_english(text):
    """The English half of a bilingual description, if the source published one.

    Returns None when the text is not bilingual, so the caller can fall back to
    translating.
    """
    if not text:
        return None
    marked = bool(_ENGLISH_MARKER.search(text))
    parts = [p.strip() for p in _LANG_SPLIT.split(text) if p.strip()]
    if len(parts) < 2:
        return None

    # The English half is the one that reads as English. With the marker
    # present the second part is English by convention, but check anyway - the
    # order is not guaranteed.
    candidates = [p for p in parts if not looks_german(p)]
    if not candidates:
        return None
    best = max(candidates, key=len)
    if not marked and looks_german(best):
        return None
    return _ENGLISH_MARKER.sub("", best).strip(" -–—") or None


# --------------------------------------------------------------------------
# cache
# --------------------------------------------------------------------------

def _key(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def load_cache(path=CACHE_PATH):
    """Previously translated descriptions, keyed by a hash of the source."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_cache(cache, path=CACHE_PATH):
    """Write the translation cache back, so nothing is paid for twice."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------

def provider_name():
    """Which translator is configured, from the environment."""
    if os.environ.get("DEEPL_API_KEY"):
        return "deepl"
    if os.environ.get("TRANSLATE_PROVIDER") == "mymemory":
        return "mymemory"
    return "none"


def _deepl(text):
    key = os.environ["DEEPL_API_KEY"]
    host = ("api-free.deepl.com" if key.endswith(":fx") else "api.deepl.com")
    response = scraper._session.post(
        "https://%s/v2/translate" % host,
        data={"text": text, "source_lang": "DE", "target_lang": "EN"},
        headers={"Authorization": "DeepL-Auth-Key %s" % key},
        timeout=scraper.REQUEST_TIMEOUT)
    response.raise_for_status()
    return " ".join(t["text"] for t in response.json().get("translations", []))


def _chunks(text, size=MYMEMORY_CHUNK):
    """Split on sentence boundaries into pieces the endpoint will accept."""
    pieces, current = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        while len(sentence) > size:          # a single very long sentence
            pieces.append(sentence[:size])
            sentence = sentence[size:]
        if len(current) + len(sentence) + 1 > size:
            if current:
                pieces.append(current.strip())
            current = sentence
        else:
            current += " " + sentence
    if current.strip():
        pieces.append(current.strip())
    return pieces


def _mymemory(text):
    out = []
    for chunk in _chunks(text):
        params = {"q": chunk, "langpair": "de|en"}
        # Optional, and only ever the address the operator chooses to set:
        # it raises the anonymous daily character limit.
        if os.environ.get("MYMEMORY_EMAIL"):
            params["de"] = os.environ["MYMEMORY_EMAIL"]
        data = scraper.fetch("https://api.mymemory.translated.net/get",
                             params=params, as_json=True)
        translated = (data.get("responseData") or {}).get("translatedText")
        if not translated:
            raise RuntimeError(data.get("responseDetails") or "no translation")
        if "MYMEMORY WARNING" in translated.upper():
            raise RuntimeError("quota reached")
        out.append(translated)
        time.sleep(REQUEST_PAUSE)
    return " ".join(out)


def translate_text(text, provider=None):
    """German to English via the configured provider. None when unavailable."""
    provider = provider or provider_name()
    if provider == "none" or not text:
        return None
    try:
        return _deepl(text) if provider == "deepl" else _mymemory(text)
    except Exception as exc:                                   # noqa: BLE001
        print("    ! translation failed (%s): %s" % (provider, str(exc)[:90]))
        return None


# --------------------------------------------------------------------------

def english_for(event, cache, provider=None, allow_network=True):
    """The English description for one event, and where it came from."""
    text = (event.get("raw_description") or "").strip()
    if not text:
        return None, "no description"

    published = published_english(text)
    if published:
        return published, "published"

    # Each source declares the language it publishes in, which beats guessing:
    # a Berlin Art Link listing is English however many German gallery names it
    # contains. Detection is only the fallback.
    language = event.get("language") or detect_language(text)
    if language == "en":
        return text, "already english"

    hit = cache.get(_key(text))
    if hit:
        return hit, "cached"

    if not allow_network:
        return None, "not translated"

    translated = translate_text(text, provider)
    if translated:
        cache[_key(text)] = translated
        return translated, "translated"
    return None, "unavailable"


FLUSH_EVERY = 5               # translations between cache writes


def enrich(events, cache=None, provider=None, allow_network=True, verbose=True,
           cache_path=CACHE_PATH, budget=0):
    """Add `description_en` to every event that can have one.

    The original text is never replaced - scoring reads both, so the German
    keyword list and the English one can each do their work.

    The cache is flushed to disk every few translations. Translating a backlog
    takes minutes and free providers have daily limits, so a run that is cut
    short - by a timeout, a quota, or a lost connection - has to keep the work
    it already paid for. `budget` caps translations per run; 0 means no cap.
    """
    cache = load_cache(cache_path) if cache is None else cache
    provider = provider or provider_name()
    tally = {}
    done = 0

    for event in events:
        may_call = allow_network and (not budget or done < budget)
        english, how = english_for(event, cache, provider, may_call)
        if english:
            event["description_en"] = english
        tally[how] = tally.get(how, 0) + 1
        if how == "translated":
            done += 1
            if cache_path and done % FLUSH_EVERY == 0:
                save_cache(cache, cache_path)

    if cache_path and done:
        save_cache(cache, cache_path)
    if verbose:
        print("  english: %s (provider: %s)"
              % (", ".join("%s=%d" % kv for kv in sorted(tally.items())),
                 provider))
    return cache, tally


def main(argv=None):
    """Backfill English descriptions for everything in the inventory."""
    parser = argparse.ArgumentParser(
        description="Translate stored descriptions into English")
    parser.add_argument("--state", default=None,
                        help="inventory to backfill (default: state.json)")
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after this many translations")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be translated, call nothing")
    args = parser.parse_args(argv)

    import state as state_mod
    path = args.state or state_mod.STATE_PATH
    st = state_mod.load(path)
    cache = load_cache()
    provider = provider_name()
    print("provider: %s, %d shows in the inventory, %d cached translations"
          % (provider, len(st["events"]), len(cache)))

    done = 0
    tally = {}
    for record in st["events"].values():
        if record.get("description_en"):
            tally["already"] = tally.get("already", 0) + 1
            continue
        english, how = english_for(
            record, cache, provider,
            allow_network=not args.dry_run and (not args.limit or done < args.limit))
        tally[how] = tally.get(how, 0) + 1
        if english:
            record["description_en"] = english
            if how == "translated":
                done += 1
                if done % FLUSH_EVERY == 0:
                    save_cache(cache)
                    state_mod.save(st, path)

    print("  " + ", ".join("%s=%d" % kv for kv in sorted(tally.items())))
    if args.dry_run:
        print("dry run: nothing written")
        return 0

    save_cache(cache)
    state_mod.save(st, path)
    print("wrote %d translations to the cache, updated the inventory"
          % len(cache))
    return 0


if __name__ == "__main__":
    sys.exit(main())
