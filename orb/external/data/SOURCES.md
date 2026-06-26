# Macro-event skip list — sources & compilation rules

`macro_events.csv` is the §4.5 skip list: scheduled FOMC announcement days plus
CPI and NFP (Employment Situation) release days, **2018-01-01 → present**. On a
listed date the whole session is skipped (no entry).

## Sources (authoritative, captured 2026-06-26)

| Category | Source | Provenance |
|---|---|---|
| **CPI** | ALFRED/FRED "Consumer Price Index" release-dates export — **source agency: U.S. Bureau of Labor Statistics** (`bls.gov/cpi`) | St. Louis Fed ALFRED archival release dates |
| **NFP** | ALFRED/FRED "Employment Situation" release-dates export — **source agency: U.S. Bureau of Labor Statistics** (`bls.gov/ces`) | St. Louis Fed ALFRED archival release dates |
| **FOMC** | Federal Reserve FOMC calendar pages (2018, 2019, 2020, 2021–2027) | `federalreserve.gov/monetarypolicy/fomccalendars.htm` (+ historical year pages) |

These files were supplied directly by the user because this environment's egress
policy blocks `federalreserve.gov` and `bls.gov` (and WebFetch). Dates were
**not** reconstructed from memory — CPI/NFP were parsed programmatically from the
ALFRED exports; every FOMC date was traced to its statement press-release URL
(`monetaryYYYYMMDDa.htm`) on the Fed calendar page. Regenerate with
`scripts/build_macro_calendar.py`.

## Compilation rules (decided with the user)

**CPI / NFP — keep ALL release dates (no separation).** The ALFRED lists record
every date any series in the release was revised, so they include non-headline
entries (annual CPI seasonal-factor revisions each February; the CES preliminary
benchmark revision; occasional corrections). Per instruction these are **kept**
as skip days — they are real scheduled 8:30 ET releases the market trades on, and
the filter's job is to stay flat on volatile data days, so over-including is
correct. Multi-date months are reported below, not removed.

**Shutdown-delayed dates — use the ACTUAL release date, flag, don't correct.**
For a backtest the day the data actually hit is the day the market moved. The
2025 government shutdown shifted several late-2025 releases; actual dates are kept
and flagged (below).

**FOMC — "announcement days only" = entries carrying a formal FOMC _Statement_.**
The Fed labels genuine policy statements "Statement" and administrative facility
actions "Press Release". We include every calendar entry with a **Statement**
(scheduled rate decisions + emergency cuts + notation-vote *statements*) and the
announcement date = the statement's press-release date. We exclude the cancelled
meeting and the "Press Release"-only facility actions (documented below).

## Counts (2018-01-01 → present)

| year | CPI | NFP | FOMC | total |
|---|---|---|---|---|
| 2018 | 12 | 12 | 8 | 32 |
| 2019 | 13 | 12 | 9 | 34 |
| 2020 | 13 | 13 | 11 | 37 |
| 2021 | 13 | 12 | 8 | 33 |
| 2022 | 13 | 12 | 8 | 33 |
| 2023 | 13 | 12 | 8 | 33 |
| 2024 | 13 | 14 | 8 | 35 |
| 2025 | 11 | 11 | 9 | 31 |
| 2026 (YTD) | 6 | 6 | 4 | 16 |
| **all** | **107** | **104** | **73** | **284** |

284 rows; 281 unique calendar dates (3 dates are both CPI and FOMC — see
collisions). 2026 is partial (through the June data in the source files).

## Multi-date months (more than one release that month — all kept)

- **CPI** (earlier Feb date each year = annual seasonal-factor revision; later =
  headline January CPI): 2019-02 (11, 13), 2020-02 (11, 13), 2021-02 (08, 10),
  2022-02 (08, 10), 2023-02 (10, 14), 2024-02 (09, 13).
- **NFP**: 2020-05 (08, 11), 2024-01 (05, 10), 2024-08 (02, 21 — 08-21 is the CES
  preliminary benchmark revision).

## FOMC non-scheduled entries INCLUDED (flagged for review)

| date | nature |
|---|---|
| 2019-10-11 | unscheduled (meeting Oct 4); reserve-management / T-bill purchase announcement; formal Statement |
| 2020-03-03 | emergency 50bp cut; unscheduled meeting labeled Mar 2, Statement released Mar 3 |
| 2020-03-15 | emergency 100bp cut; unscheduled **Sunday** announcement — **non-session day** (won't skip a session) |
| 2020-03-23 | notation vote; uncapped QE + credit facilities; formal Statement |
| 2020-08-27 | notation vote; new policy framework (avg inflation targeting); formal Statement |
| 2025-08-22 | notation vote; 5-year framework review; Statement on Longer-Run Goals |

If you want a strict "scheduled rate decisions + the two emergency cuts" set,
drop 2019-10-11, 2020-03-23, 2020-08-27, and 2025-08-22.

## FOMC entries EXCLUDED (documented)

- **2020-03-17/18** — scheduled meeting **cancelled** (no statement).
- **2020-03-19** — notation vote (dollar swap lines); labeled "Press Release", not "Statement".
- **2020-03-31** — notation vote (FIMA repo facility); labeled "Press Release", not "Statement".

## Shutdown-delayed / divergent dates (actual kept, flagged)

- CPI 2025-10-24 (Sept 2025 CPI, delayed), CPI 2025-12-18 (delayed).
- NFP 2025-11-20 (Sept 2025 jobs report, delayed), NFP 2025-12-16 (delayed).
- Missing months from the 2025 shutdown: **CPI 2025-11**, **NFP 2025-10** (no release that month).

## Same-day collisions (both events; skip-set dedupes to one date)

2019-12-11 (CPI + FOMC), 2020-06-10 (CPI + FOMC), 2024-06-12 (CPI + FOMC).

## Could-not-confirm

None. Every date is traced to a source row (ALFRED) or a statement URL (Fed).
