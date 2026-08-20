"""Parser checks for the fiddly bits of each source.

These are the cases that actually went wrong while building the parsers, so
they are worth keeping. Run: python test_scraper.py
"""

import scraper
import board

FAILURES = []


def check(name, got, want):
    if got == want:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s:\n       got  %r\n       want %r" % (name, got, want))
        FAILURES.append(name)


print("artatberlin - vernissage date and time")
RUN = ("2026-09-10", "2026-10-01")
cases = [
    ("Vernissage: Donnerstag, 10. September 2026 von 18:00 bis 20:00 Uhr",
     RUN, "2026-09-10T18:00"),
    ("Eröffnung: Donnerstag, 10. September, 18:00 - 21:00 Uhr",
     RUN, "2026-09-10T18:00"),
    ("Vernissage: Donnerstag, 10. September 2026, ab 19 Uhr",
     RUN, "2026-09-10T19:00"),
    ("Vernissage: Donnerstag, 10. September 2026, 19:00-21:30 Uhr",
     RUN, "2026-09-10T19:00"),
    # A bare "(Vernissage: 10.09.)" cross-reference carries no month name.
    ("(Vernissage: 10.09.) die Ausstellung", RUN, None),
]
for text, run, want in cases:
    check(text[:46], scraper.aab_opening_datetime(text, 2026, run), want)

# The preview block at the foot of a post advertises other shows at the venue.
body = ("Vernissage: Donnerstag, 10. September 2026, 18:00 Uhr "
        "Preview Eröffnung: Freitag, 29. November 2026, 15 Uhr")
check("ignores a preview of another show",
      scraper.aab_opening_datetime(body, 2026, RUN), "2026-09-10T18:00")

print("\nartatberlin - title splitting")
check("artist | title | gallery | run",
      scraper.aab_split_title(
          "Clemens Krauss | Sediments | Galerie Crone | 10.09.-31.10.2026"),
      ("Clemens Krauss", "Sediments", "Galerie Crone",
       ("2026-09-10", "2026-10-31")))
check("placeholder run still comes off the venue",
      scraper.aab_split_title(
          "Michael Sailstorfer | Pull the Rug | Galerie Judin | 10.09.2026-(folgt)")[2],
      "Galerie Judin")
check("run crossing the new year",
      scraper._aab_run_dates("09.10.-17.01.2027"), ("2026-10-09", "2027-01-17"))

print("\nartatberlin - boilerplate trimming")
text = ("Galerie X zeigt die Ausstellung. Der eigentliche Text. "
        "Zur Galerie Bildunterschrift Titel: Foto Preview andere Ausstellung")
check("cuts at 'Zur Galerie'",
      scraper._strip_boilerplate(text),
      "Galerie X zeigt die Ausstellung. Der eigentliche Text.")
check("strips the '#5120ARTatBerlin' lead-in",
      scraper._strip_boilerplate("bis 03.10. | #5120ARTatBerlin | Der Text."),
      "Der Text.")

print("\nberlin art link - opening times")
check("range takes the start, meridiem from the end",
      scraper._bal_start_time("Tuesday, July 28; 7-10pm"), (19, "00"))
check("single time with minutes",
      scraper._bal_start_time("Wednesday, July 29; 6:30pm"), (18, "30"))
check("day number is not read as an hour",
      scraper._bal_start_time("Tuesday, July 28; 6pm"), (18, "00"))
check("abbreviated months in a run",
      scraper._bal_run("July 28-Aug. 2, 2026", 2026), ("2026-07-28", "2026-08-02"))
check("same-month run",
      scraper._bal_run("Aug. 7-23, 2026", 2026), ("2026-08-07", "2026-08-23"))

print("\nrundgang - German dates")
check("dd.mm.yyyy", scraper._de_date_iso("Fr, 21.08.2026"), "2026-08-21")
check("no date", scraper._de_date_iso("Vernissage"), None)

print("\nshared helpers")
check("double-encoded entities",
      scraper.clean("Robert McNally &amp;amp; Charlie"),
      "Robert McNally & Charlie")
check("id is stable across runs",
      scraper.event_id("Galerie KK5", "land in between", "2026-09-04"),
      scraper.event_id("galerie kk5 ", "Land In Between", "2026-09-04T18:00"))
check("id differs for a different show",
      scraper.event_id("Galerie KK5", "one", "2026-09-04")
      == scraper.event_id("Galerie KK5", "two", "2026-09-04"), False)

print("\npage formatting")
check("blurb drops the announcement sentence, keeps the ordinal intact",
      board.summarise({"raw_description":
                       "Galerie Judin zeigt ab Donnerstag, 10. September 2026 "
                       "die Ausstellung Pull the Rug. Eine der pragenden "
                       "Erfahrungen unserer Zeit."}),
      "Eine der pragenden Erfahrungen unserer Zeit.")
check("blurb stops where the date block starts",
      board.summarise({"raw_description":
                       "Foto Tschernow Vernissage: Freitag, 28. August 2026"}),
      "Foto Tschernow")
check("opening with a time",
      board.describe_dates({"vernissage_datetime": "2026-08-21T18:00"}),
      "Fri 21 Aug, 18:00")
check("opening and closing together",
      board.describe_dates({"vernissage_datetime": "2026-08-21T18:00",
                            "exhibition_end": "2026-08-30"}),
      "Fri 21 Aug, 18:00 – 30 Aug")
check("a run with no announced opening",
      board.describe_dates({"exhibition_end": "2026-09-04"}), "until 4 Sep")
check("nothing announced at all",
      board.describe_dates({}), "dates not announced")

print("\n%d failure(s)" % len(FAILURES))
raise SystemExit(1 if FAILURES else 0)
