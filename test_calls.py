"""Checks for open calls: deadlines, runway, eligibility and the call cards.

No network and no model. The eligibility rules get the most attention here
because they are the only part of the project that removes something from
view, and a call wrongly marked shut is a call you never hear about again.

Run: python test_calls.py
"""

from datetime import date

import board
import calls

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
check("a nearby city is not you",
      verdict("Applicants must live and work in Berlin."), "closed")
check("an explicit welcome to everyone wins over a named nationality",
      verdict("We invite Nigerian sculptors, though international artists "
              "may also apply."), "eligible")
check("where it happens is not who may enter",
      verdict("The residency takes place in Finland. Ceramic artists "
              "worldwide may apply."), "open")

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

print("\n%d failure(s)" % len(FAILURES))
raise SystemExit(1 if FAILURES else 0)
