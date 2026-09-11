"""Checks for the English-description step, with a stub provider.

No network: translation providers are rate-limited and their output changes,
neither of which belongs in a test. What matters here is the decision logic -
what gets translated, what does not, and what survives an interrupted run.

Run: python test_translate.py
"""

import os
import tempfile

import llm
import translate

# No model, ever, unless a check supplies it: a test that asks the local one
# passes here and fails on the GitHub runner, which is how the hub froze for
# ten days in September.
llm.available = lambda: False

FAILURES = []
CALLS = []


def check(name, got, want):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s: got %r, want %r" % (name, got, want))
        FAILURES.append(name)


def stub(text, provider=None):
    CALLS.append(text)
    return "EN[" + text[:24] + "]"


translate.translate_text = stub


def ev(**kw):
    base = {"raw_description": "", "language": None}
    base.update(kw)
    return base


GERMAN = ("Die Ausstellung zeigt neue Arbeiten der Künstlerin. "
          "Sie arbeitet mit Bronze und Gips.")
ENGLISH = ("The gallery shows new works by the artist, who has been working "
           "with bronze and plaster for many years.")

print("language detection")
check("German prose", translate.detect_language(GERMAN), "de")
check("English prose", translate.detect_language(ENGLISH), "en")
check("short German fragment is not called English",
      translate.detect_language("Hochdrucke") == "en", False)
check("empty", translate.detect_language(""), "unknown")

print("\nbilingual descriptions publish their own English")
bilingual = ("(English below) Die Galerie zeigt neue Arbeiten. "
             "--- The gallery shows new works by the artist.")
check("English half extracted", translate.published_english(bilingual),
      "The gallery shows new works by the artist.")
check("monolingual text has no English half",
      translate.published_english(GERMAN), None)
check("a rule that is not a language split is ignored",
      translate.published_english("Teil eins --- Teil zwei auf Deutsch"), None)

print("\nwhat gets translated")
CALLS.clear()
cases = [
    ("German description", ev(raw_description=GERMAN, language="de"), "translated"),
    ("source declares English", ev(raw_description=ENGLISH, language="en"),
     "already english"),
    ("bilingual source", ev(raw_description=bilingual, language="de"), "published"),
    ("no description", ev(raw_description="", language="de"), "no description"),
]
for name, event, want in cases:
    _, how = translate.english_for(event, {}, provider="stub")
    check(name, how, want)

print("\na declared language beats detection")
# Berlin Art Link listings are English but full of German gallery names.
listing = ("Clara Brörmann: 'Aureolen' / Opening Reception: Thursday, Aug. 6 / "
           "Exhibition: Aug. 7-Sept. 5, 2026 / Marburger Straße 3, Berlin")
check("detection alone would call it German",
      translate.detect_language(listing), "de")
_, how = translate.english_for(ev(raw_description=listing, language="en"), {},
                               provider="stub")
check("but the source says English", how, "already english")

print("\nthe cache")
CALLS.clear()
cache = {}
first, _ = translate.english_for(ev(raw_description=GERMAN, language="de"),
                                 cache, provider="stub")
second, how = translate.english_for(ev(raw_description=GERMAN, language="de"),
                                    cache, provider="stub")
check("translated once", len(CALLS), 1)
check("served from cache the second time", how, "cached")
check("same text both times", first, second)

print("\nan interrupted run keeps what it paid for")
CALLS.clear()
path = os.path.join(tempfile.mkdtemp(), "translations.json")
events = [ev(raw_description=GERMAN + " Nummer %d." % i, language="de")
          for i in range(12)]
cache, tally = translate.enrich(events, cache={}, provider="stub",
                                cache_path=path, budget=7, verbose=False)
check("budget respected", tally.get("translated"), 7)
check("the rest are left for next time", tally.get("not translated"), 5)
check("cache written to disk", len(translate.load_cache(path)), 7)
check("only budgeted calls made", len(CALLS), 7)

print("\na provider that fails does not stop the run")
translate.translate_text = lambda text, provider=None: None
events = [ev(raw_description=GERMAN, language="de")]
cache, tally = translate.enrich(events, cache={}, provider="stub",
                                cache_path=None, verbose=False)
check("failure is reported, not raised", tally.get("unavailable"), 1)
check("no English claimed", events[0].get("description_en"), None)

print("\nwith no provider configured nothing breaks")
translate.translate_text = lambda text, provider=None: None
saved = dict(os.environ)
os.environ.pop("DEEPL_API_KEY", None)
os.environ.pop("TRANSLATE_PROVIDER", None)
check("provider is 'none'", translate.provider_name(), "none")
events = [ev(raw_description=GERMAN, language="de"),
          ev(raw_description=ENGLISH, language="en")]
cache, tally = translate.enrich(events, cache={}, cache_path=None, verbose=False)
check("English source still passes through", tally.get("already english"), 1)

print("\nwhich translator, when")
llm.available = lambda: True
check("with no key, the local model is used when it is reachable",
      translate.provider_name(), "local")
os.environ["TRANSLATE_PROVIDER"] = "none"
check("unless translation is switched off by name", translate.provider_name(), "none")
os.environ.pop("TRANSLATE_PROVIDER")
os.environ["DEEPL_API_KEY"] = "test:fx"
check("a DeepL key wins over the local model", translate.provider_name(), "deepl")
os.environ.pop("DEEPL_API_KEY")
llm.available = lambda: False
check("and with no model and no key there is none", translate.provider_name(), "none")
os.environ.clear()
os.environ.update(saved)

print("\nthe local model's translations are checked before they are kept")
_real_llm_translate = llm.translate
llm.translate = lambda piece: "The gallery shows new works by the artist in the city."
check("an English answer is accepted",
      translate._local("Die Galerie zeigt neue Arbeiten des Künstlers in der Stadt."),
      "The gallery shows new works by the artist in the city.")
llm.translate = lambda piece: "Die Galerie zeigt neue Arbeiten und der Künstler ist auch da."
try:
    translate._local("Die Galerie zeigt neue Arbeiten.")
    handed_back = False
except RuntimeError:
    handed_back = True
check("German handed back as English is refused", handed_back, True)
llm.translate = lambda piece: None
check("a refusal leaves the description untranslated, not half done",
      translate.translate_text("Die Galerie zeigt neue Arbeiten.", provider="local"),
      None)
llm.translate = lambda piece: "The artist shows sculpture in the gallery space."
long_german = "Die Künstlerin zeigt Skulpturen im Raum der Galerie. " * 60
out = translate._local(long_german)
check("a long description is translated from its opening, and says it was cut",
      out.endswith("[…]"), True)
check("in pieces the model handles well",
      out.count("The artist shows") >= 2, True)
llm.translate = _real_llm_translate

print("\nthe local model refuses to call a summary a translation")
_real_ask = llm.ask
llm.ask = lambda prompt, schema, model=None, num_predict=300: {"english": "Art."}
check("an answer a fraction of the source's length is not kept",
      llm.translate("Die Galerie zeigt ab Freitag neue Arbeiten des Künstlers."), None)
llm.ask = lambda prompt, schema, model=None, num_predict=300: {
    "english": "The gallery shows new works by the artist from Friday."}
check("a translation of about the source's length is",
      llm.translate("Die Galerie zeigt ab Freitag neue Arbeiten des Künstlers."),
      "The gallery shows new works by the artist from Friday.")
llm.ask = _real_ask

print("\nchunking for length-limited providers")
long_text = "Ein Satz über Skulptur. " * 60
chunks = translate._chunks(long_text)
check("all chunks within the limit",
      all(len(c) <= translate.MYMEMORY_CHUNK for c in chunks), True)
check("nothing dropped",
      "".join(chunks).replace(" ", "") == long_text.replace(" ", ""), True)

print("\n%d failure(s)" % len(FAILURES))
raise SystemExit(1 if FAILURES else 0)
