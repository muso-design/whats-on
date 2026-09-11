"""Checks for guessing unclassified shows from their pictures.

No network and no model: the vision call and the image fetch are stubbed,
because what matters here is what the code does with an answer - who gets a
guess, what it may change, and what it may never change.

Run: python test_guess.py
"""

import board
import guess
import state

FAILURES = []


def check(name, got, want):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s: got %r, want %r" % (name, got, want))
        FAILURES.append(name)


ANSWERS = {}
LOOKED = []


def fake_look(url):
    LOOKED.append(url)
    return ANSWERS.get(url)


guess.look = fake_look
guess.vision_available = lambda: True


def show(**kw):
    base = {"id": kw.get("title", "x"), "title": "x", "medium_tier": 0,
            "medium_confidence": "unknown", "rank": 0}
    base.update(kw)
    return base


print("who gets a guess")
ANSWERS.update({
    "img/unicorn.jpg": {"kind": "sculpture", "seen": "black unicorn head"},
    "img/still.jpg": {"kind": "painting", "seen": "still life, fruit"},
    "img/poster.jpg": {"kind": "text or poster", "seen": "large lettering"},
    "img/room.jpg": {"kind": "installation", "seen": "dark corridor, red light"},
})
unknown = show(title="Trost", image="img/unicorn.jpg")
still = show(title="Still", image="img/still.jpg")
poster = show(title="Poster", image="img/poster.jpg")
blind = show(title="No picture")
known = show(title="Known", image="img/room.jpg", medium_tier=3,
             medium_confidence="keywords", rank=300)
events = [unknown, still, poster, blind, known]
cache = guess.enrich(events, cache={}, verbose=False, cache_path=None)

check("an unclassified show with a picture gets a guess", unknown["medium_guess"],
      "sculpture")
check("in the family the page filters by", unknown["guess_family"], "sculpture")
check("with what the model saw", unknown["guess_seen"], "black unicorn head")
check("a poster is not guessed at", poster.get("medium_guess"), None)
check("a show with no picture gets nothing", blind.get("medium_guess"), None)
check("a classified show is never looked at", "img/room.jpg" in LOOKED, False)
check("and never given a guess", known.get("medium_guess"), None)
check("a guess never classifies a show", unknown["medium_tier"], 0)
check("installation counts as sculpture for filtering",
      guess.FAMILY["installation"], "sculpture")

print("\nwhat a guess may do to the order")
def rank(ev):
    return board.to_row(dict(ev, status="running"))["rank"]


check("a guessed sculpture floats above other unclassified shows on the page",
      rank(unknown) > rank(still) > rank(poster), True)
check("but stays far below anything actually classified",
      rank(unknown) < rank(known), True)
check("the stored rank is the classifier's, untouched", unknown["rank"], 0)
guess.enrich(events, cache=cache, verbose=False, cache_path=None)
guess.enrich(events, cache=cache, verbose=False, cache_path=None)
check("running it again changes nothing", rank(unknown), 20)

print("\nwhat is remembered")
LOOKED.clear()
again = show(title="Trost", image="img/unicorn.jpg")
guess.enrich([again], cache=cache, verbose=False, cache_path=None)
check("a picture already looked at is not looked at again", LOOKED, [])
check("its guess comes from memory", again["medium_guess"], "sculpture")
guess.vision_available = lambda: False
offline = show(title="Trost", image="img/unicorn.jpg")
guess.enrich([offline], cache=cache, verbose=False, cache_path=None)
check("with no model, remembered guesses still apply (the nightly job)",
      offline["medium_guess"], "sculpture")
guess.vision_available = lambda: True

print("\nthe day a show becomes classified")
became = show(title="Trost", image="img/unicorn.jpg")
guess.enrich([became], cache=cache, verbose=False, cache_path=None)
became.update(medium_tier=3, medium_confidence="keywords", rank=300)
guess.enrich([became], cache=cache, verbose=False, cache_path=None)
check("its guess is cleared", became.get("medium_guess"), None)
check("and the page ranks it as the classifier did", rank(became), 300)

print("\nthe budget")
LOOKED.clear()
many = [show(title="s%d" % i, image="img/n%d.jpg" % i) for i in range(5)]
guess.enrich(many, cache={}, budget=2, verbose=False, cache_path=None)
check("no more pictures than the budget per run", len(LOOKED), 2)
LOOKED.clear()
shared = [show(title="a", image="img/same.jpg"), show(title="b", image="img/same.jpg")]
guess.enrich(shared, cache={}, verbose=False, cache_path=None)
check("two shows sharing a picture cost one look", len(LOOKED), 1)

print("\nthe answers reach the nightly job")
import scheduled_refresh  # noqa: E402
check("the local refresh commits the remembered answers",
      "guesses.json" in scheduled_refresh.DATA_FILES, True)

print("\nthe answer is kept with the show")
check("the inventory keeps the guess fields",
      all(f in state.KEEP_FIELDS for f in ("medium_guess", "guess_family",
                                          "guess_seen")), True)
row = board.to_row(dict(unknown, status="running"))
check("and the page gets it", (row["guess"], row["guess_family"], row["guess_seen"]),
      ("sculpture", "sculpture", "black unicorn head"))
check("while the show stays in the unclassified bucket", row["medium"],
      "medium unknown")

print("\npictures a site does not want shown elsewhere")
# index-berlin answers 403 to image requests from other sites. Its pictures
# are looked at for the guess and left off the cards.
walled = board.to_row({"id": "w", "title": "t", "status": "running",
                       "image": "https://www.indexberlin.com/images/x.jpg?w=300"})
check("an index-berlin picture is not put on a card", walled["image"], "")
open_pic = board.to_row({"id": "o", "title": "t", "status": "running",
                         "image": "https://www.rundgang-kunst.de/uploads/x.jpg"})
check("other sources' pictures still are", open_pic["image"],
      "https://www.rundgang-kunst.de/uploads/x.jpg")
check("a lookalike host is not mistaken for index-berlin",
      board.showable_image("https://notindexberlin.com/x.jpg"),
      "https://notindexberlin.com/x.jpg")

print("\n%d failure(s)" % len(FAILURES))
raise SystemExit(1 if FAILURES else 0)
