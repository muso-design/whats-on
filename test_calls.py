"""Checks for open calls: deadlines, runway, eligibility and the call cards.

No network and no model. The eligibility rules get the most attention here
because they are the only part of the project that removes something from
view, and a call wrongly marked shut is a call you never hear about again.

Run: python test_calls.py
"""

import json
from datetime import date

import board
import calls
import llm

# No model, ever, unless a check supplies the answer. The nightly job has no
# Ollama, and two checks here that quietly asked the local one passed on the
# desktop and failed on the runner - ten nights running, each time stopping
# the refresh that sits behind the checks, so the hub froze on 1 September
# without a word. What is tested is what the code does with an answer.
_REAL_ELIGIBILITY = llm.eligibility


def no_model():
    llm.available = lambda: False
    llm.eligibility = _REAL_ELIGIBILITY


def model_says(restricted, countries):
    llm.available = lambda: True
    llm.eligibility = lambda text, cache=None: (restricted, list(countries))


no_model()

FAILURES = []


def check(name, got, want):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s: got %r, want %r" % (name, got, want))
        FAILURES.append(name)


TODAY = date(2026, 9, 1)


print("deadlines keep the day and drop the clock")
check("a UTC instant becomes a day",
      calls._iso_stamp("2026-09-07T12:00:00Z"), "2026-09-07")
check("a different hour on the same day is the same day",
      calls._iso_stamp("2026-09-07T23:45:00Z"), "2026-09-07")
check("a plain date survives", calls._iso_stamp("2026-09-07"), "2026-09-07")
check("nonsense is not a deadline", calls._iso_stamp("soon"), None)
check("an impossible date is not a deadline",
      calls._iso_stamp("2026-02-31"), None)
check("nothing is not a deadline", calls._iso_stamp(None), None)


print("\nArtConnect, in both of its page layouts")
# It moved from one __NEXT_DATA__ block to the streamed app-router format
# after 1 September and the parser read nothing until it was noticed by hand.
_record = {"title": "Residency", "deadline": "2026-10-01T00:00:00Z",
           "postLifetime": "2026-10-01T00:00:00Z"}
_listing = {"data": [_record], "entries": 1, "pages": 1, "total": 1}
_old = ('<script id="__NEXT_DATA__" type="application/json">%s</script>'
        % json.dumps({"props": {"pageProps": {"opportunities": _listing}}}))
# Compact, the way the site writes it.
_stream = ('5:["$","div",null,{"opportunities":'
           + json.dumps(_listing, separators=(",", ":")) + "}]")
_half = len(_stream) // 2
_new = "".join("<script>self.__next_f.push([1,%s])</script>" % json.dumps(part)
               for part in (_stream[:_half], _stream[_half:]))
check("the old single block is read",
      (calls._artconnect_payload(_old) or {}).get("total"), 1)
check("the streamed layout is read, even split across chunks",
      ((calls._artconnect_payload(_new) or {}).get("data") or [{}])[0].get("title"),
      "Residency")
check("a page with neither is nothing, not a crash",
      calls._artconnect_payload("<html><body>moved</body></html>"), None)


print("\nrunway, because a week is not enough to build anything")
def band(deadline):
    return calls.status_of({"deadline": deadline}, TODAY)


check("today is closing", band("2026-09-01"), "closing")
check("a week out is closing", band("2026-09-08"), "closing")
check("nine days out is this month", band("2026-09-10"), "soon")
check("three weeks out is still this month", band("2026-09-22"), "soon")
check("a month out is later", band("2026-10-15"), "open")
check("yesterday is closed", band("2026-08-31"), "closed")
check("no deadline is rolling", band(None), "rolling")
check("days left counts the days",
      calls.days_left({"deadline": "2026-09-11"}, TODAY), 10)

print("\nand a call you can prepare for outranks one closing tomorrow")
soon = calls.score({"title": "Sculpture prize", "deadline": "2026-09-20",
                    "description": "bronze"}, TODAY)
closing = calls.score({"title": "Sculpture prize", "deadline": "2026-09-02",
                       "description": "bronze"}, TODAY)
check("three weeks beats three days", soon["rank"] > closing["rank"], True)


print("\nwhat counts as sculpture")
def fit(title, description="", fields=None):
    return calls.sculpture_relevance({"title": title, "description": description,
                                      "fields": fields or []})[0]


check("a strong word is enough", fit("Open call", "bronze casting"), "yes")
check("Kunst am Bau is always in", fit("Kunst am Bau Dresden"), "yes")
check("two weak words together count",
      fit("Open call", "work in stone and wood"), "yes")
check("one weak word is only a maybe",
      fit("Open call", "a stepping stone for your career"), "maybe")
check("a word inside another word is not a word",
      fit("Open call", "the cornerstone of the castle", ["PAINTING"]), "no")
check("a call tagged for other media is a no",
      fit("Poetry prize", "submit three poems", ["LITERATURE"]), "no")
# An untagged call that describes nothing is not evidence of irrelevance, the
# same way an exhibition with no description is not evidence it is not
# sculpture. It stays a maybe so the filter can show it on request.
check("saying nothing at all is a maybe, not a no",
      fit("Poetry prize", "submit three poems"), "maybe")
check("a sculpture tag on a listing that ticks everything is only a maybe",
      fit("Open call", "", ["SCULPTURE"] + ["X%d" % i for i in range(14)]),
      "maybe")
check("a sculpture tag on a narrow listing is a yes",
      fit("Open call", "", ["SCULPTURE", "INSTALLATION"]), "yes")


print("\nnationality: named as a requirement, not merely mentioned")
def named(text):
    return calls._demonyms(text)


check("an invitation to one nationality is a rule",
      named("The City of Toronto invites Canadian artists to apply."),
      ["Canadian"])
check("the whole coordinated list is read, not just the last one",
      named("Open to German and Austrian artists."), ["German", "Austrian"])
check("commas too",
      named("Open to French, German or Swiss applicants."),
      ["French", "German", "Swiss"])
check("citizenship needs no further evidence",
      named("must be an Australian citizen or permanent resident"),
      ["Australian"])
check("nor does nationality",
      named("submitted by an author of Belgian nationality"), ["Belgian"])
check("a biography is not a rule",
      named("He co-founded the Epoxy Art Group, an Asian American artist "
            "collective."), [])
check("a programme description is not a rule",
      named("Wonderland Festival, for the presentation of Italian artists."), [])
check("naming the jury is not a rule",
      named("The jury includes the Japanese sculptor Kohei Nawa."), [])
check("naming past winners is not a rule",
      named("Previous winners include the British artist Rachel Whiteread."), [])
check("an adjective that is not a nationality is ignored",
      named("Emerging artists and disabled artists may apply."), [])


print("\nand what that means for you, in Leipzig")
def verdict(text, field="restrictions"):
    return calls.eligibility_of({field: text})[0]


check("no terms at all is open", verdict(None), "open")
check("shut when the list does not include you",
      verdict("Open call for Nordic artists."), "closed")
check("open to you when it does",
      verdict("Open to German and Austrian artists."), "eligible")
check("Europe counts as you",
      verdict("Open to European artists under 35."), "eligible")
check("an explicit welcome to everyone wins over a named nationality",
      verdict("We invite Nigerian sculptors, though international artists "
              "may also apply."), "eligible")

print("\nwhat the code does with the model's answer")
model_says(True, ["Berlin"])
check("a nearby city is not you",
      verdict("Applicants must live and work in Berlin."), "closed")
model_says(False, [])
check("where it happens is not who may enter",
      verdict("The residency takes place in Finland. Ceramic artists "
              "worldwide may apply."), "open")
model_says(True, ["Germany", "Austria"])
check("a restriction that includes Germany is open to you",
      verdict("Open to artists based in Germany or Austria."), "eligible")
model_says(True, ["Norway"])
check("a named country loses to an explicit worldwide welcome",
      verdict("Priority to artists in Norway; artists worldwide may apply."),
      "eligible")
no_model()

print("\nwith no model, as on the nightly job")
check("a rule in plain words is still read",
      verdict("Open call for Nordic artists."), "closed")
check("terms that need reading say so rather than guess",
      verdict("Applicants must live and work in Berlin."), "unknown")
batch = [
    {"title": "a", "rank": 3,
     "restrictions": "The City of Toronto invites Canadian artists to apply."},
    {"title": "b", "rank": 2,
     "restrictions": "Applicants must live and work in Berlin."},
    {"title": "c", "rank": 1},
]
calls.resolve_eligibility(batch, verbose=False)
check("the nightly run still shuts a call by nationality",
      batch[0]["eligibility"], "closed")
check("and says who it is for", batch[0]["open_to"], ["Canadian"])
check("and leaves what needs a model as unknown",
      batch[1]["eligibility"], "unknown")
check("and a call with no terms is open", batch[2]["eligibility"], "open")

print("\nthe free scan reads more than the model is asked to")
toronto = {"title": "George Street Hoarding",
           "restrictions": "other: a two-stage competition for practicing "
                           "artists who work in two-dimensional media.",
           "description": "The City of Toronto invites Canadian artists to "
                          "apply for the opportunity."}
check("a rule in the body is caught even when the terms field is busy",
      calls.eligibility_of(toronto), ("closed", ["Canadian"]))
check("but only the terms field is what the model would be charged for",
      calls.eligibility_text(toronto), toronto["restrictions"])

print("\nbeing shut out sinks a call without hiding it")
open_call = calls.score({"title": "Sculpture prize", "deadline": "2026-09-20",
                         "description": "bronze"}, TODAY)
shut = calls.score({"title": "Sculpture prize", "deadline": "2026-09-20",
                    "description": "bronze", "eligibility": "closed"}, TODAY)
check("it ranks far below", shut["rank"] < open_call["rank"] - 200, True)
check("it is still scored, not dropped", shut["sculpture"], "yes")


print("\nthe inventory")
inventory = {"calls": {}, "last_run": None}
one = calls.score({"id": "a", "title": "Prize", "deadline": "2026-09-20",
                   "source": "bbk"}, TODAY)
fresh = calls.merge(inventory, [one], TODAY)
check("a call arrives once", len(fresh), 1)
check("and is remembered", len(inventory["calls"]), 1)
fresh = calls.merge(inventory, [one], TODAY)
check("and is not new the second time", len(fresh), 0)
check("first seen is kept",
      inventory["calls"]["a"]["first_seen"] ==
      inventory["calls"]["a"]["last_seen"], True)

# The nightly job runs where there is no model. It must not undo what the
# machine with a model worked out.
inventory["calls"]["a"]["eligibility"] = "closed"
inventory["calls"]["a"]["open_to"] = ["Canadian"]
calls.merge(inventory, [dict(one, eligibility="unknown", open_to=[])], TODAY)
check("a run with no model keeps the verdict",
      inventory["calls"]["a"]["eligibility"], "closed")
check("and keeps who it is open to",
      inventory["calls"]["a"]["open_to"], ["Canadian"])
calls.merge(inventory, [dict(one, eligibility="open", open_to=[])], TODAY)
check("but a run that did read the terms may change its mind",
      inventory["calls"]["a"]["eligibility"], "open")

# A restored verdict has to reach the rank too, not only the label.
inventory = {"calls": {}}
shut_call = calls.score({"id": "s", "title": "Sculpture prize", "source": "artconnect",
                         "deadline": "2026-09-20", "description": "bronze",
                         "eligibility": "closed", "open_to": ["Nebraska"]}, TODAY)
calls.merge(inventory, [shut_call], TODAY)
shut_rank = inventory["calls"]["s"]["rank"]
nightly = calls.score(dict(shut_call, eligibility="unknown", open_to=[]), TODAY)
calls.merge(inventory, [nightly], TODAY)
check("a verdict restored by a run with no model also keeps its low rank",
      inventory["calls"]["s"]["rank"], shut_rank)

inventory["calls"]["old"] = {"status": "closed", "deadline": "2026-01-01"}
inventory["calls"]["recent"] = {"status": "closed", "deadline": "2026-08-20"}
removed = calls.prune(inventory, today=TODAY)
check("a long-closed call is forgotten", removed, 1)
check("a recently closed one is kept", "recent" in inventory["calls"], True)


print("\nthe card")
record = {
    "id": "x", "title": "Bildhauersymposium 2027",
    "organisation": "Kunstverein", "type": "ART_RESIDENCY",
    "deadline": "2026-09-30", "days_left": 29, "status": "open",
    "place": "Scuol, Switzerland", "sculpture": "yes",
    "sculpture_why": "says bildhauersymposium", "specificity": "specific",
    "fee": False, "requires": ["PORTFOLIO", "CV"], "rank": 370,
    "description": "**A residency** for artists working in [stone](http://x.y).",
    "eligibility": "open", "language": "de",
}
row = board.to_call_row(record)
check("the deadline reads as a day", row["when"], "by 30 Sep")
check("the type is readable", row["type"], "residency")
check("the place is the readable one", row["place"], "Scuol, Switzerland")
check("markdown does not reach the card",
      "**" in row["blurb"] or "](" in row["blurb"], False)
check("the link text survives its markup", "stone" in row["blurb"], True)
check("requirements are readable", row["requires"], ["portfolio", "CV"])
check("a reminder is offered", row["cal_label"], "Remind me")
check("and it is set before the deadline, not on it",
      "dates=20260923" in row["cal"], True)

check("a call with no deadline gets no reminder",
      board.to_call_row({"id": "y", "title": "Rolling"})["cal"], "")
check("and says so", board.to_call_row({"id": "y", "title": "Rolling"})["when"],
      "no deadline given")
check("a BBK title says what kind of thing it is",
      board.call_type({"title": "Kunst am Bau, Neubau Grundschule"}),
      "commission")

print("\nclosed calls are not built into the page")
inventory = {"calls": {
    "a": {"title": "Live", "status": "open", "rank": 10},
    "b": {"title": "Gone", "status": "closed", "rank": 900},
    "c": {"title": "Also live", "status": "closing", "rank": 5},
}}
rows = board.build_call_rows(inventory)
check("only the live ones", [r["title"] for r in rows], ["Live", "Also live"])
# The id lives in the inventory key, not in the record. Losing it gave every
# card the same empty id, so tracking one application tracked all of them.
check("every card carries its own id", sorted(r["id"] for r in rows),
      ["a", "c"])

check("no calls file means no calls, not a crash",
      board.build_call_rows({}), [])


print("\nopencallforartists: a listing, as the backend hands it over")
ROW = {"id": 1250, "title": "Infinite Expressions | Open Art Competition",
       "organization_title": "TERAVARNA", "category": "Call For Submissions",
       "event_deadline": "2026-09-15", "city": "LOS ANGELES",
       "country": "United States", "type": "Online Only",
       "eligibility": "International", "fee_type": "Paid", "price": 20,
       "instagram_caption": "short caption", "instagram_handle": "@teravarna"}
DETAIL = {"description": "<p>Submit <b>sculpture</b>, painting and photography.</p>",
          "apply_now_link": "teravarna.com/apply", "web_link": "https://teravarna.com",
          "instagram": None, "artistic_fields": "Open, Painting, sculpture",
          "prize_summary": "cash prizes up to $5,000", "listing_type": "Standard post",
          "email": "someone@example.invalid", "phone": "0123456"}
detail = {k: DETAIL.get(k) for k in calls.OCFA_DETAIL_FIELDS}
call = calls.parse_ocfa(ROW, detail)
check("the deadline is the plain date", call["deadline"], "2026-09-15")
check("the fee is read, with its amount", (call["fee"], call["fee_note"]),
      (True, "$20"))
check("an all-caps city is written like a city", call["place"],
      "Los Angeles, United States")
check("online-only is recorded", call["online"], True)
check("the description loses its HTML", call["description"],
      "Submit sculpture, painting and photography.")
check("a link without a scheme still goes somewhere",
      call["url"], "https://teravarna.com/apply")
check("the organiser's Instagram comes from the handle when that is all there is",
      call["org_instagram"], "https://www.instagram.com/teravarna/")
check("'Open' in the media list means all fields", call["fields"][0], "ALL")
check("so the sculpture tag counts for little", calls.specificity(call),
      "open to all")
check("a standard post is not promotion", call["promoted"], False)
check("no email or phone number is kept anywhere",
      any(k in call for k in ("email", "phone"))
      or "someone@example" in json.dumps(call), False)
check("the list row alone still makes a call",
      calls.parse_ocfa(ROW)["description"], "short caption")
check("a residency filed as a call for artists is a residency",
      calls.parse_ocfa(dict(ROW, title="Hayama Artist Residency in Japan",
                            category="Call For Artists"))["type"], "residency")
check("the site's own listings count as promoted",
      calls.parse_ocfa(dict(ROW, organization_title="Open Call for Artists"),
                       detail)["promoted"], True)


print("\npaying to enter, and paying to be shown")
check("a real prize is money you could win",
      calls.prize_money("Top 3 receive cash prizes up to $5,000,"), 5000)
check("the fee is not a prize", calls.prize_money("Entry fee: $25"), 0)
check("a valuation is not a prize",
      calls.prize_money("-A $10,000+ Estimated Artist Package"), 0)
check("a fee in the next sentence does not cancel a prize",
      calls.prize_money("Winner receives $2,000. Entry is $30 per work."), 2000)
check("thousands written the European way",
      calls.prize_money("€1.500 Preisgeld"), 1500)


def exposure(**kw):
    base = {"fee": True, "type": "open call", "title": "", "description": "",
            "rewards": [], "online": False}
    base.update(kw)
    return calls.pay_to_play(base)[0]


check("a fee for a place in a book is paying to be shown",
      exposure(title="The Big Book of Mixed Media Artists 2026"), True)
check("a fee for a virtual exhibition is paying to be shown",
      exposure(title="Home", description="an international virtual exhibition"),
      True)
check("a fee with a cash prize is paying to enter",
      exposure(title="Open Art Competition", online=True,
               rewards=["cash prizes up to $5,000"]), False)
check("a modest prize is still a prize",
      exposure(title="KANE Prize", rewards=["£200 cash, online showcase"]),
      False)
check("a residency's fee buys a studio, not a mention",
      exposure(title="Residency", type="residency", online=True), False)
check("a free call is never paying to be shown",
      exposure(fee=False, title="Book of Artists"), False)
check("online-only with a fee and nothing to win is paying to be shown",
      exposure(title="Themed call", online=True), True)
check("a book of the winner's work is a prize, not a compilation",
      exposure(title="Tom Stoddart Award for Excellence", type="award",
               description="GOST books will collaborate with the recipient to "
                           "create a book of their work."), False)
check("describing the organiser's own members is not selling membership",
      exposure(title="Martin Parr Foundation Awards", type="award",
               description="Membership contributions support emerging "
                           "photographers."), False)
check("but paying for member-artist status is",
      exposure(title="Super Arts", description="0% commission for member artists"),
      True)
sunk = calls.score({"title": "The Big Book of Sculptors", "fee": True,
                    "deadline": "2026-09-20", "description": "bronze"}, TODAY)
fair = calls.score({"title": "Sculpture Prize", "fee": True,
                    "deadline": "2026-09-20", "description": "bronze",
                    "rewards": ["$3,000"]}, TODAY)
check("it sinks below a paid call with a prize", sunk["rank"] < fair["rank"], True)
check("and says why", sunk["pay_why"], "book of sculptors")


print("\nthe same call from two sources")
def listing(source, org, title, deadline):
    return {"id": source + ":" + title, "source": source, "organisation": org,
            "title": title, "deadline": deadline}


check("a rewritten title from the same organiser on the same day is one call",
      calls.same_call(
          listing("ocfa", "Foundwork", "2026 Foundwork Artist Prize: 10,000 USD "
                  "Grant with Studio Visits and Interview", "2026-09-26"),
          listing("artconnect", "Foundwork", "2026 Foundwork Artist Prize",
                  "2026-09-26")), True)
check("two competitions by one organiser on one day are two calls",
      calls.same_call(
          listing("ocfa", "TERAVARNA", "Infinite Expressions | Open Art "
                  "Competition | TERAVARNA", "2026-09-15"),
          listing("artconnect", "TERAVARNA", "Visions Without Limits | OPEN Art "
                  "Competition", "2026-09-15")), False)
check("deadlines a week apart are two calls",
      calls.same_call(listing("ocfa", "X", "Sculpture Prize", "2026-09-01"),
                      listing("artconnect", "X", "Sculpture Prize", "2026-09-08")),
      False)
check("one source never merges with itself",
      calls.same_call(listing("ocfa", "X", "Same", "2026-09-01"),
                      listing("ocfa", "X", "Same", "2026-09-01")), False)

a = listing("artconnect", "Foundwork", "2026 Foundwork Artist Prize", "2026-09-26")
a["fee"] = None
o = listing("ocfa", "Foundwork", "2026 Foundwork Artist Prize: 10,000 USD Grant",
            "2026-09-26")
o.update(fee=True, fee_note="$18", org_instagram="https://instagram.com/foundwork")
merged = calls.merge_duplicate_calls([o, a])
check("two listings become one record", len(merged), 1)
check("the better-structured source leads when neither is known",
      merged[0]["id"], a["id"])
check("the other is remembered as an alias", merged[0]["aliases"], [o["id"]])
check("gaps are filled from the other source",
      (merged[0]["fee"], merged[0]["org_instagram"]),
      (True, "https://instagram.com/foundwork"))
check("both sources are recorded", merged[0]["sources"], ["artconnect", "ocfa"])

# The id must survive a source dropping out, or the stage you set is lost.
inventory = {"calls": {}}
calls.merge(inventory, calls.score_all(merged, TODAY), TODAY)
key = merged[0]["id"]
alone = listing("ocfa", "Foundwork", "2026 Foundwork Artist Prize: 10,000 USD Grant",
                "2026-09-26")
fresh = calls.merge(inventory, calls.score_all(
    calls.merge_duplicate_calls([alone], known=inventory["calls"]), TODAY), TODAY)
check("when ArtConnect drops it, it lands on the same record",
      list(inventory["calls"]), [key])
check("and does not come back as new", len(fresh), 0)

# And the other way round: known first from opencallforartists alone.
inventory = {"calls": {}}
calls.merge(inventory, calls.score_all([dict(o)], TODAY), TODAY)
later = calls.merge_duplicate_calls([dict(o), dict(a)], known=inventory["calls"])
check("an id already in the inventory wins over source order",
      later[0]["id"], o["id"])


print("\nwho may apply, as declared by the organiser")
def declared(scope, country):
    return calls.scope_eligibility({"scope": scope, "country": country})


check("national and American is not you", declared("national", "USA"),
      ("closed", ["USA"]))
check("national and German is you", declared("national", "Germany"),
      ("eligible", ["Germany"]))
check("local and German could be Hamburg - shown, not guessed",
      declared("local", "Germany"), ("unknown", ["Germany"]))
check("international is left to the terms", declared("international", "USA"), None)
check("the declared scope needs no model",
      calls.free_eligibility({"scope": "regional", "country": "Portugal"}),
      ("closed", ["Portugal"]))


print("\nsources that go quiet")
inventory = {"calls": {}, "health": {}}
calls.record_health(inventory, "artconnect", 300, today=date(2026, 9, 1))
calls.record_health(inventory, "artconnect", 0, today=date(2026, 9, 2))
calls.record_health(inventory, "artconnect", 0, today=date(2026, 9, 3))
check("the first silent night is remembered, not the latest",
      inventory["health"]["artconnect"]["failing_since"], "2026-09-02")
check("one silent day is not yet worth a warning",
      board.source_warnings(inventory, today=date(2026, 9, 3)), [])
warning = board.source_warnings(inventory, today=date(2026, 9, 11))
check("after two it is said on the page", len(warning), 1)
check("naming the source and how old its calls are",
      "ArtConnect" in warning[0] and "1 Sep" in warning[0], True)
calls.record_health(inventory, "artconnect", 280, today=date(2026, 9, 12))
check("and it clears the day the source answers again",
      board.source_warnings(inventory, today=date(2026, 9, 12)), [])


print("\nthe card for a call that is listed twice and sells exposure")
row = board.to_call_row({"title": "Book", "pay_to_play": True, "pay_why": "book of",
                         "sources": ["artconnect", "ocfa"],
                         "org_instagram": "https://instagram.com/x"}, key="k")
check("pay-to-be-shown reaches the page", (row["pay"], row["pay_why"]),
      (True, "book of"))
check("sources are named for people", row["sources"],
      ["ArtConnect", "opencallforartists"])
check("the organiser's Instagram reaches the page", row["org_ig"],
      "https://instagram.com/x")

print("\n%d failure(s)" % len(FAILURES))
raise SystemExit(1 if FAILURES else 0)
