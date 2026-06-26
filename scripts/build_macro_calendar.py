"""Regenerate orb/external/data/macro_events.csv from the authoritative sources.

Usage:
    python scripts/build_macro_calendar.py <cpi_release_dates.txt> <nfp_release_dates.txt> [out.csv]

Inputs (see orb/external/data/SOURCES.md for provenance):
  - cpi_release_dates.txt : ALFRED/FRED "Consumer Price Index" release-dates
                            export (source agency: U.S. BLS).
  - nfp_release_dates.txt : ALFRED/FRED "Employment Situation" release-dates
                            export (source agency: U.S. BLS).
  - FOMC announcement days are encoded below, each traced to the statement
    press-release URL (monetaryYYYYMMDDa.htm) on the Federal Reserve FOMC
    calendar pages (2018, 2019, 2020, 2021-2027).

Rules (decided with the user):
  - CPI/NFP: keep ALL release dates >= 2018-01-01 (incl. revision/benchmark and
    shutdown-delayed actual dates); report multi-date months and divergences.
  - FOMC: include every calendar entry carrying a formal "Statement" (scheduled
    rate decisions + emergency cuts + notation-vote statements); exclude the
    cancelled meeting and "Press Release"-only facility actions.
"""

import datetime as dt
import re
import sys
from collections import defaultdict

START = dt.date(2018, 1, 1)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_OUT = "orb/external/data/macro_events.csv"

CPI_SRC = "ALFRED/FRED Consumer Price Index release dates (source: U.S. BLS)"
NFP_SRC = "ALFRED/FRED Employment Situation release dates (source: U.S. BLS)"
FOMC_SRC = "Federal Reserve FOMC calendar (statement press-release date)"

SHUTDOWN_NOTE = "shutdown-delayed: actual release date diverges from normal schedule (2025 govt shutdown)"
SHUTDOWN_DATES = {
    dt.date(2025, 10, 24), dt.date(2025, 12, 18),   # CPI
    dt.date(2025, 11, 20), dt.date(2025, 12, 16),   # NFP
}
NAMED_EXTRA = {dt.date(2024, 8, 21): "CES preliminary benchmark revision (NFP)"}

FOMC_SCHEDULED = [
    "2018-01-31", "2018-03-21", "2018-05-02", "2018-06-13",
    "2018-08-01", "2018-09-26", "2018-11-08", "2018-12-19",
    "2019-01-30", "2019-03-20", "2019-05-01", "2019-06-19",
    "2019-07-31", "2019-09-18", "2019-10-30", "2019-12-11",
    # 2020 scheduled March 17-18 meeting was CANCELLED -> no entry
    "2020-01-29", "2020-04-29", "2020-06-10", "2020-07-29",
    "2020-09-16", "2020-11-05", "2020-12-16",
    "2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16",
    "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15",
    "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15",
    "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
    "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14",
    "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",  # year to date
]
# STRICT published-schedule policy: the only non-scheduled FOMC days kept are the
# two March 2020 emergency rate cuts (kept by explicit user decision). All other
# unscheduled Fed statements are EXCLUDED -- they were not knowable in advance, so
# skip-listing them is hindsight a live system could not act on. Trade through them.
# Dropped vs the earlier compilation:
#   2019-10-11 (reserve-mgmt/T-bill announcement), 2020-03-23 (uncapped QE),
#   2020-08-27 (policy framework), 2025-08-22 (5-year framework review).
FOMC_SPECIAL = [
    ("2020-03-03", "emergency 50bp cut; unscheduled meeting labeled Mar 2, Statement released Mar 3 (monetary20200303a); kept by explicit decision"),
    ("2020-03-15", "emergency 100bp cut; unscheduled SUNDAY announcement (monetary20200315a) - NON-SESSION day; kept by explicit decision"),
]


def parse_alfred(path):
    out = []
    with open(path) as fh:
        for line in fh:
            s = line.strip()
            if DATE_RE.match(s):
                d = dt.date.fromisoformat(s)
                if d >= START:
                    out.append(d)
    return sorted(out)


def month_groups(dates):
    mg = defaultdict(list)
    for d in dates:
        mg[(d.year, d.month)].append(d)
    return mg


def build(cpi_path, nfp_path, out_path):
    cpi = parse_alfred(cpi_path)
    nfp = parse_alfred(nfp_path)
    rows = []

    for label, dates, src in (("CPI", cpi, CPI_SRC), ("NFP", nfp, NFP_SRC)):
        mg = month_groups(dates)
        for d in dates:
            note = ""
            same = sorted(mg[(d.year, d.month)])
            if len(same) > 1 and d != same[0]:
                note = "extra same-month release (revision/benchmark) - kept per include-all rule"
            if d in NAMED_EXTRA:
                note = NAMED_EXTRA[d]
            if d in SHUTDOWN_DATES:
                note = (note + "; " if note else "") + SHUTDOWN_NOTE
            rows.append((d, label, src, note))

    for s in FOMC_SCHEDULED:
        rows.append((dt.date.fromisoformat(s), "FOMC", FOMC_SRC, ""))
    for s, note in FOMC_SPECIAL:
        rows.append((dt.date.fromisoformat(s), "FOMC", FOMC_SRC, note))

    rows.sort(key=lambda r: (r[0], r[1]))
    with open(out_path, "w") as fh:
        fh.write("date,event_type,source,note\n")
        for d, etype, src, note in rows:
            fh.write(f'{d.isoformat()},{etype},"{src}","{note.replace(chr(34), chr(39))}"\n')
    return len(rows), cpi, nfp


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    out = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_OUT
    n, cpi, nfp = build(sys.argv[1], sys.argv[2], out)
    print(f"wrote {n} rows to {out}  (CPI={len(cpi)}, NFP={len(nfp)})")
