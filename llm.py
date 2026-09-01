"""A local model, used only for the one thing it is reliably good at.

Reading text and saying what is in it. Not judging, not inferring, not filling
gaps. Every rule below came out of a measurement rather than a preference:

* **One narrow question per call.** Asked for medium, materials and a summary
  in a single schema, the model invented "Canvas" for a show that never
  mentions it and returned a curator's name as a material. Asked only for
  materials, with an explicit instruction to return nothing when nothing is
  named, it correctly returned an empty array on every text tried.

* **Every answer is checked against the source.** A material either appears in
  the text or it does not; that is a substring test, and it catches the case
  above without needing the model to be honest.

* **Never ask what something means for you.** Given an open call restricted to
  six Californian counties and asked whether it blocked a Germany-based
  applicant, the model said no. Three times, with no reason. Facts come from
  the model; comparisons are done in code.

* **Cached on model, task and input.** The same text answered differently
  across runs. Without a cache key that includes the model name, changing
  models silently rewrites everything the project believes.

Nothing here is required. With no Ollama running, every call returns None and
the pipeline behaves exactly as it did before.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import unicodedata

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(HERE, "llm_cache.json")

ENDPOINT = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
# Overridable, because the right model is a property of the machine. Measured
# on an RTX 2070 SUPER: this one answers in about three seconds, while a larger
# 8B that also fits in VRAM took forty-five to a hundred for the same prompt.
MODEL = os.environ.get("LLM_MODEL", "qwen2.5-coder:7b")
TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "120"))
MAX_CHARS = 1400              # more context than this rarely improves the answer

_available = None


def available():
    """Whether a local model is reachable. Cached for the run."""
    global _available
    if _available is None:
        try:
            r = requests.get(ENDPOINT + "/api/tags", timeout=4)
            names = [m["name"] for m in r.json().get("models", [])]
            _available = MODEL in names or any(n.startswith(MODEL.split(":")[0])
                                               for n in names)
        except Exception:                                      # noqa: BLE001
            _available = False
    return _available


# --------------------------------------------------------------------------
# cache
# --------------------------------------------------------------------------

def load_cache(path=CACHE_PATH):
    """Answers already paid for, keyed by model, task and input."""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_cache(cache, path=CACHE_PATH):
    """Write the cache back, so a rerun costs nothing."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=1, sort_keys=True)
        fh.write("\n")


def cache_key(task, text, model=None):
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    return "%s|%s|%s" % (model or MODEL, task, digest)


# --------------------------------------------------------------------------
# the call
# --------------------------------------------------------------------------

def ask(prompt, schema, model=None, num_predict=300):
    """One request. Returns a parsed object, or None if anything goes wrong."""
    if not available():
        return None
    try:
        response = requests.post(
            ENDPOINT + "/api/generate", timeout=TIMEOUT,
            json={"model": model or MODEL, "prompt": prompt, "stream": False,
                  "format": schema,
                  "options": {"temperature": 0, "num_predict": num_predict}})
        response.raise_for_status()
        return json.loads(response.json()["response"])
    except Exception as exc:                                   # noqa: BLE001
        print("    ! model call failed: %s" % str(exc)[:80])
        return None


# --------------------------------------------------------------------------
# grounding
# --------------------------------------------------------------------------

# German for the English words the model tends to answer in.
_EQUIVALENTS = {
    "oil": ["ol", "oel"], "canvas": ["leinwand"], "linen": ["leinen"],
    "wood": ["holz"], "plaster": ["gips"], "steel": ["stahl"],
    "iron": ["eisen"], "clay": ["ton"], "ceramic": ["keramik"],
    "porcelain": ["porzellan"], "paper": ["papier"], "glass": ["glas"],
    "concrete": ["beton"], "stone": ["stein"], "marble": ["marmor"],
    "bronze": ["bronze"], "wax": ["wachs"], "resin": ["harz"],
    "textile": ["textil", "stoff"], "photography": ["foto", "photo"],
    "film": ["film"], "video": ["video"], "paint": ["farbe"],
    "drawing": ["zeichnung"], "print": ["druck"], "copper": ["kupfer"],
}


def _fold(text):
    text = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(c for c in text if not unicodedata.combining(c))


def grounded(claim, source):
    """Whether a claimed term actually occurs in the text it came from.

    Generous about language and inflection, strict about invention: at least
    one meaningful word of the claim, or its German equivalent, has to be
    present.
    """
    haystack = _fold(source)
    # Two letters is enough: "Oel" folds to "ol", and dropping it would
    # discard a real material as though it were invented.
    for word in re.findall(r"[a-z]{2,}", _fold(claim)):
        if word in haystack:
            return True
        for other in _EQUIVALENTS.get(word, []):
            if other in haystack:
                return True
    return False


def keep_grounded(claims, source):
    """Drop anything the source does not actually say."""
    return [c for c in (claims or []) if grounded(c, source)]


# --------------------------------------------------------------------------
# tasks - one narrow question each
# --------------------------------------------------------------------------

MATERIALS_SCHEMA = {
    "type": "object",
    "properties": {"materials": {"type": "array", "items": {"type": "string"}}},
    "required": ["materials"],
}
MATERIALS_PROMPT = (
    "List only the physical materials or media explicitly named in this "
    "exhibition text. Do not guess. If none are named, return an empty array.\n\n")


def materials(text, cache=None):
    """Materials the text actually names, verified against it."""
    text = (text or "").strip()[:MAX_CHARS]
    if len(text) < 40:
        return []
    key = cache_key("materials", text)
    if cache is not None and key in cache:
        return cache[key]
    answer = ask(MATERIALS_PROMPT + text, MATERIALS_SCHEMA, num_predict=200)
    result = keep_grounded((answer or {}).get("materials"), text)
    if cache is not None and answer is not None:
        cache[key] = result
    return result


ELIGIBILITY_SCHEMA = {
    "type": "object",
    "properties": {
        "restricted": {"type": "boolean"},
        "countries": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["restricted", "countries"],
}
ELIGIBILITY_PROMPT = (
    "This is the text of an art open call. Decide whether it limits WHO MAY "
    "APPLY by nationality or by country of residence.\n"
    "Set restricted true only if applicants must be from, live in, or hold "
    "the nationality of specific named places. An invitation addressed to one "
    "nationality counts, even without the word must: \"invites Canadian "
    "artists\" and \"open to artists based in Norway\" are both restrictions.\n"
    "These are NOT restrictions, and for them restricted is false:\n"
    "- where the residency, exhibition or prize takes place\n"
    "- where the organisation is based, or where the work will be shown\n"
    "- travel, visas, accommodation or shipping\n"
    "- age, career stage, student status, medium or theme\n"
    "List in countries only the place words that applicants must belong to, "
    "spelled as they appear in the text. If none, return an empty list.\n\n")


def eligibility(text, cache=None):
    """Which countries a call is closed to outsiders from.

    The single question that decides whether an opportunity is real for you.
    An award for Argentine nationals is not a near miss, it is not a call at
    all, and reading three paragraphs of terms to find that out is the kind of
    work worth handing to a model.

    Returns (restricted, countries). Every country returned must appear in the
    text; the rest are dropped, and a claim of restriction that survives with
    nothing named is downgraded, because "restricted, but I cannot say to
    where" is a guess wearing a boolean.
    """
    text = (text or "").strip()[:MAX_CHARS]
    if len(text) < 30:
        return False, []
    # Versioned: the cache is keyed on the text and the model, not the prompt,
    # so sharpening the wording has to invalidate the old answers by hand.
    key = cache_key("eligibility.2", text)
    if cache is not None and key in cache:
        cached = cache[key]
        return bool(cached[0]), list(cached[1])
    answer = ask(ELIGIBILITY_PROMPT + text, ELIGIBILITY_SCHEMA, num_predict=120)
    if answer is None:
        return False, []
    countries = keep_grounded(answer.get("countries"), text)
    restricted = bool(answer.get("restricted")) and bool(countries)
    if cache is not None:
        cache[key] = [restricted, countries]
    return restricted, countries


SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": ["string", "null"]}},
    "required": ["summary"],
}
SUMMARY_PROMPT = (
    "Summarise what this exhibition shows, in one plain English sentence of at "
    "most 22 words. Do not mention the gallery name, the dates or the opening. "
    "If the text says nothing about the work itself, return null.\n\n")


def summary(text, cache=None):
    """One English sentence about the work, or None when the text says nothing."""
    text = (text or "").strip()[:MAX_CHARS]
    if len(text) < 80:
        return None
    key = cache_key("summary", text)
    if cache is not None and key in cache:
        return cache[key]
    answer = ask(SUMMARY_PROMPT + text, SUMMARY_SCHEMA, num_predict=120)
    result = (answer or {}).get("summary")
    if isinstance(result, str):
        result = " ".join(result.split())
        if len(result) < 15:
            result = None
    if cache is not None and answer is not None:
        cache[key] = result
    return result


EXHIBITIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "exhibitions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "artist": {"type": ["string", "null"]},
                    "title": {"type": ["string", "null"]},
                    "city": {"type": ["string", "null"]},
                    "start": {"type": ["string", "null"]},
                    "end": {"type": ["string", "null"]},
                    "opening": {"type": ["string", "null"]},
                },
                "required": ["artist", "title"],
            },
        }
    },
    "required": ["exhibitions"],
}
EXHIBITIONS_PROMPT = (
    "This is the visible text of an art gallery's website. List the current and "
    "upcoming exhibitions it announces. Use ISO dates (YYYY-MM-DD) and 24-hour "
    "times; write null for anything not stated. Do not invent artists, titles "
    "or dates. If no exhibition is announced, return an empty array.\n\n"
    "WEBSITE TEXT:\n")


def exhibitions(page_text, year_hint=None, cache=None):
    """Exhibitions announced on a gallery's own page.

    This is what replaces a parser per venue. The names and titles are checked
    against the page, because an invented artist is worse than a missing show.
    """
    text = (page_text or "").strip()[:4000]
    if len(text) < 60:
        return []
    key = cache_key("exhibitions", text)
    if cache is not None and key in cache:
        return cache[key]

    prompt = EXHIBITIONS_PROMPT + text
    if year_hint:
        prompt = prompt.replace("Use ISO dates",
                                "Assume the year is %s where none is given. "
                                "Use ISO dates" % year_hint)
    answer = ask(prompt, EXHIBITIONS_SCHEMA, num_predict=700)

    out = []
    for item in (answer or {}).get("exhibitions") or []:
        if not isinstance(item, dict):
            continue
        artist, title = item.get("artist"), item.get("title")
        # A show has to be named in the page to exist.
        if not (artist and grounded(artist, text)) and \
           not (title and grounded(title, text)):
            continue
        out.append({
            "artist": artist or None,
            "title": title or artist,
            "city": item.get("city") or None,
            "start": _iso(item.get("start")),
            "end": _iso(item.get("end")),
            "opening": _stamp(item.get("opening")),
        })
    if cache is not None and answer is not None:
        cache[key] = out
    return out


def _iso(value):
    """Keep only something that really is a date."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    if not m:
        return None
    try:
        from datetime import date
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def _stamp(value):
    """A date, optionally with a time, and nothing else."""
    text = str(value or "").strip().replace("T", " ")
    m = re.match(r"^(\d{4}-\d{2}-\d{2})(?:[ ](\d{1,2}):(\d{2}))?", text)
    if not m or not _iso(m.group(1)):
        return None
    if m.group(2) is None:
        return m.group(1)
    return "%sT%02d:%s" % (m.group(1), int(m.group(2)), m.group(3))


# --------------------------------------------------------------------------

def main(argv=None):
    """Check the model is reachable and time it on the inventory."""
    parser = argparse.ArgumentParser(description="Local model helper")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--bench", type=int, default=0,
                        help="time N records from the inventory")
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)

    global MODEL
    if args.model:
        MODEL = args.model
    print("endpoint %s, model %s, reachable: %s" % (ENDPOINT, MODEL, available()))
    if not available() or not args.bench:
        return 0

    import state as state_mod
    cache = load_cache()
    records = [r for r in state_mod.load()["events"].values()
               if (r.get("raw_description") or "").strip()][:args.bench]
    started = time.time()
    for record in records:
        text = record["raw_description"]
        mats = materials(text, cache)
        print("  %-38s %s" % (record["title"][:37], mats or "-"))
    save_cache(cache)
    print("%.1fs for %d records (%.1fs each)"
          % (time.time() - started, len(records),
             (time.time() - started) / max(1, len(records))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
