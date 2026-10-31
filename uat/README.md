# UAT

Automated acceptance tests built on the sponsor's sample prompts, in two
suites:

- **Chatbot** (`run_uat.py`): US-1.1 (cargo queries) and US-1.2 (tonnage
  queries), 20 cases.
- **Dashboard Change Feed** (`run_feed_uat.py`): US-2.3 (day-on-day and
  week-on-week changes). The chatbot does not cover US-2.3: it has no tool
  that compares two points in time. The Change Feed is where it is delivered.

```
.venv/Scripts/python.exe -m uat.run_uat                    # chatbot: every case, 3 runs each
.venv/Scripts/python.exe -m uat.run_uat --runs 1 --case c01 v02
.venv/Scripts/python.exe -m uat.run_uat --story US-1.2
.venv/Scripts/python.exe -m uat.expected                   # chatbot expected counts only
.venv/Scripts/python.exe -m uat.verify_expected            # cross-check the expected answers
.venv/Scripts/python.exe -m uat.rescore uat/results/<run>  # rescore a finished run, no model calls
.venv/Scripts/python.exe -m uat.run_feed_uat               # Change Feed
```

Needs `OI_WORKING_DATE` pinned in `.env` (currently 2025-06-17). The results
are written to `uat/results/<timestamp>/`: `summary.md` for reading,
`runs.csv` for a spreadsheet, and `runs.json` and `expected.json` with
everything recorded.

## Keeping the expected answer independent

The agent is checked against answers it had no hand in.

- **Written separately.** Each case's expected answer is a hand-written query
  in `expected/`, written from the table schema, the data glossary and what a
  trader means by the question. It is not taken from, or checked against, the
  SQL the agent generates.
- **Run separately.** `expected.py` imports nothing from `ai_platform` and
  opens its own database connection. The runner computes every expected answer
  and writes it to disk before the agent is imported.
- **Compared on results, not on SQL.** The agent's SQL is recorded for
  diagnosis, but a case passes on whether the rows the trader sees are the
  rows expected, so two different but correct queries both pass.

## How each run is scored

| Check | Passes when |
|---|---|
| Asked correctly | The agent asked a question when it had to (ECSA, WAF, PDM, a vague time such as "latest") and did not ask when the question was clear. Where a reasonable reading exists (West Aussie, a month with no year), not asking is accepted if the reply states the reading. |
| Right options | When it asked, the stored name the case expects (East Coast South America for ECSA, Madeira for PDM) was among the options. A trader who clicks the first option should get the right answer. |
| Real names | Every zone, port, cargo type and status in its final search exists in the data. A search for a name stored nowhere returns nothing whatever the question, so without this a case whose answer is "nothing" passes on a wrong search. |
| Rows exact | The rows the agent returned are exactly the expected rows: none missing, none extra. |
| Count exact | The number in the reply's opening sentence equals the expected count. |
| No invention | Every vessel or order id the reply names was in the rows the agent got back, or in the question itself. |
| States assumption | For c01 and c08, the reply names the reading it took (West Australia; March 2025). |
| Format | The reply ends with an *Assumed:* line, has at most five bullets and shows no raw column names. Reported, not required to pass. |

A run **passes** when it raised no error, gave a reply, and every check above
except Format holds where it applies. Each case runs three times because the
model's choices vary between runs, and a **case passes** only when all three
runs pass. The headline score is cases passed out of 20.

Replies are compared after the odd spaces and hyphens the model puts inside
names (narrow no-break spaces and the like) are made plain, as the chat page
does before display.

### The matching case (m01)

`m01` asks which open vessels can carry one of the C3 orders, the demo's
matching step. Its expected vessels come from the matching rule written as
SQL, and it is cross-checked like the others. It is reported by case but kept
out of the score out of 20, which covers US-1.1 and US-1.2 only.

## Checking the expected answers

`verify_expected.py` computes every expected answer a second time, in plain
Python over the raw rows, and compares the two row by row. On 27 Oct 2026 all
17 agreed. It would have caught the one bug found so far: the first v04 query
used `NOT (commercial_status IN (...) AND ...)`, which is NULL for a vessel
with no status whose window covers today, so it silently dropped VESSEL 0323.

Agreement shows a query does what its definition says, not that the
definition is the right reading of the question. The readings that are
judgement calls are flagged with `review` in `cases.json`.

When the agent asks, the runner answers the way a trader would, by typing the
answer from `cases.json` rather than picking from the options offered. That
way a wrong option list cannot steer the search. If no rule fits, it takes the
agent's first option and flags the run.

## Definitions the expected answers use

These are the definitions written into `expected/`. Change them there, and
record the change here.

- **As of today, cargoes**: received by the working date, the agent's own rule
  (`horizon_column = "date_received"` in `tables.py`). Until 27 Oct 2026 the
  expected answers also dropped orders edited after the working date, which
  removed orders that were already on the book (14 from c03, 29 from c05).
  Limitation: the replayed 2025 data holds each order's final version only, so
  "updated in the last 5 days" can match an edit made after the working date.
  Live data has no future edits. The agent's *Assumed:* line still says
  "nothing received or updated after today", which overstates what it does.
- **As of today, vessels**: each report updated by the working date.
- **Latest position**: each vessel at its newest report updated on or before
  the working date.
- **Today's list**: window not closed yet, opening within 30 days, updated
  in the last 5 days.
- **A place**: a zone matches when it is one of the zones stored on the row;
  a port or region label matches when it appears inside the stored label. A
  region the user names (West Australia, East Coast South America) matches
  either, because a trader asking for cargoes "in West Australia" means every
  cargo loading there.
- **Fixed / on subs**: only while that fixture's open window covers today.
- **Weight**: the whole stem must fit, so "at least 170,000 t" compares the
  cargo's minimum.

## Dashboard Change Feed (US-2.3)

The same separation as the chatbot. The expected queries in `expected_feed/`
are written from the definitions in the main README and in the docstrings and
comments of `dashboard_queries.py`, not from its SQL. The runner works out each
window's bounds from the documented rule and runs the expected queries before
it imports any dashboard code. Then it calls `change_feed`, the function behind
`GET /api/dashboard/changes`, once per scenario, with the working date pinned
to that scenario's day.

Scenarios: Tuesday 17 June 2025 (day-on-day, week-on-week, last 24 hours) and
Monday 16 June 2025 (day-on-day, which reaches back to Friday, and
week-on-week). Each scenario checks five things: the window bounds and the
four lists.

| Check | Passes when |
|---|---|
| Window bounds | Start and end equal the documented rule: day-on-day from the same time on the previous weekday, week-on-week the seven whole days before today, 24h exactly 24 hours. |
| Supply moves | Exactly the expected (vessel, day, up/down) events. |
| New vessels | Exactly the expected (vessel, day) events. |
| Field changes | Exactly the expected (vessel, report time) rows. |
| New orders | Exactly the expected (order id, order_seq) rows. |

The feed involves no model, so each scenario runs once and gives the same
answer every time. The score is lists matching out of 20, plus window bounds
out of 5.

Definitions the expected feed queries use:

- **Status on a day**: from the most recently updated report filed before
  "now" whose open window covers that day. FIXED and ON SUBS are off the
  market; anything else is OPEN.
- **Supply move**: the status switches between OPEN and off the market from
  one covered day to the next covered day, dated by the day the new status
  takes effect. FIXED to ON SUBS is not a move, and a vessel's first covered
  day is its baseline.
- **New vessel**: OPEN on a day and not OPEN the day before, with some report
  updated in the 5 days up to and including that day.
- **Field change**: a report updated in the window whose open area, DWT,
  destination, ETA, region or ballast/laden matches no report in the vessel's
  previous batch. A batch is every report of the vessel stamped with the same
  update time, since a broker list can carry one vessel in two positions at
  once. Reports in one batch are never compared with each other, and
  identical reports in one batch count once.
- **New order**: received in the window; each `order_seq` is an order.

"Now" is the last instant of the working date. Week-on-week ends at midnight,
before "now", and a report filed between the two still counts towards the
status timeline. The first version of the expected queries got this wrong and
showed 7 false mismatches.

**Reports stamped at the same second (fixed 27 Oct 2026).** The first runs
scored 18/20 because both sides compared each report with the one before it
in a single line, so two reports from one batch were compared with each
other in arbitrary order. On 17 June VESSEL 0301 was listed at Tubarao and
Saldanha Bay, the same two positions as on 16 June, and the feed showed
"Saldanha Bay -> Tubarao" and "Tubarao -> Saldanha Bay". The definition is
now the batch rule above, in the expected query and in
`vessel_field_changes_sql` alike, and the feed scores 20/20. 17 June
day-on-day went from 36 field changes to 33. The three rows removed were
false changes, two for VESSEL 0301 and one for VESSEL 0780; VESSEL 0780's
real move to Tubarao is still listed.

A match between the two sides shows the dashboard follows the definition,
not that the definition is right. The first version of the batch rule
passed 20/20 while listing a position sent twice in one batch as two
changes. That was caught by reading the new rows, not by the score.
