#!/usr/bin/env python3
"""Preventive-screening forward calendar — the "what's due next" tracker.

The per-category `checkup_schedule` columns hold biomarker recheck cadence, but
there was no consolidated, dated view of procedural screenings (CAC, DEXA,
colonoscopy, etc.) and no "next due" computation because last-done dates aren't
in the data. This scaffolds a small `screenings` table you populate, then
computes overdue / next-due from last_done + cadence.

    python3 screening_calendar.py --init              # create table + seed starter catalog
    python3 screening_calendar.py                     # report: overdue / next due
    python3 screening_calendar.py --set "DEXA" 2025-03-14 "T-score -0.8"
    python3 screening_calendar.py --today 2026-01-01  # pin "today" for testing
    python3 screening_calendar.py --self-check

Seed cadences are reasonable DEFAULTS for a general adult — adjust per your
physician's guidance. last_done starts NULL (nothing assumed done).
"""
import argparse
import calendar
import datetime
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).parent / "labs.db"

# name, domain, cadence_months (None = one-time/as-needed), priority, rationale
# name, domain, cadence_months (None = one-time/as-needed), priority, rationale.
# A general-adult starter set — edit for your age, sex, and physician guidance.
SEED = [
    ("Blood pressure check", "cardiovascular", 12, "high",
     "Hypertension screening"),
    ("Lipid panel", "cardiovascular", 12, "high",
     "Cardiovascular risk; annually or per clinician"),
    ("Fasting glucose / HbA1c", "metabolic", 12, "high",
     "Diabetes / prediabetes screening"),
    ("Colonoscopy", "cancer", 120, "high",
     "Colorectal cancer screening from age 45; 10-yr interval if clean"),
    ("Skin / dermatology full-body exam", "cancer", 12, "medium",
     "Melanoma / skin cancer surveillance"),
    ("Dental exam + cleaning", "dental", 6, "medium",
     "Oral health; twice-yearly cleaning"),
    ("Dilated eye exam", "vision", 24, "medium",
     "Vision + retinal/vascular surveillance"),
    ("DEXA bone density", "bone", 60, "medium",
     "Osteoporosis screening; baseline midlife, sooner with risk factors"),
    ("Coronary Artery Calcium (CAC) score", "cardiovascular", 60, "medium",
     "CV risk restratification for intermediate-risk adults"),
    ("Abdominal aortic aneurysm (AAA) ultrasound", "cardiovascular", None, "low",
     "One-time screen for men 65-75 who ever smoked"),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS screenings (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    domain TEXT,
    cadence_months INTEGER,           -- NULL = one-time / as-needed
    last_done TEXT,                   -- ISO date; NULL = never done
    last_result TEXT,
    rationale TEXT,
    priority TEXT,                    -- high / medium / low
    updated_at TEXT
)
"""


def add_months(d: datetime.date, months: int) -> datetime.date:
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])  # clamp (e.g. Jan 31 + 1mo)
    return datetime.date(y, m, day)


def next_due(last_done, cadence_months, today):
    """→ (status, due_date_or_None). status ∈ never-done / not-done / one-time-done /
    OVERDUE / scheduled."""
    if not last_done:
        return ("never done" if cadence_months else "not done", None)
    ld = datetime.date.fromisoformat(last_done)
    if not cadence_months:
        return ("one-time done", None)
    due = add_months(ld, cadence_months)
    return ("OVERDUE", due) if due <= today else ("scheduled", due)


def init(conn):
    conn.execute(SCHEMA)
    now = datetime.datetime.now().isoformat(sep=" ", timespec="seconds")
    n = 0
    for name, domain, cad, prio, why in SEED:
        cur = conn.execute(
            "INSERT OR IGNORE INTO screenings "
            "(name, domain, cadence_months, priority, rationale, updated_at) "
            "VALUES (?,?,?,?,?,?)", (name, domain, cad, prio, why, now))
        n += cur.rowcount
    conn.commit()
    return n


def set_done(conn, name_query, date_str, result):
    datetime.date.fromisoformat(date_str)  # validate
    rows = conn.execute(
        "SELECT id, name FROM screenings WHERE name LIKE ?", (f"%{name_query}%",)
    ).fetchall()
    if len(rows) != 1:
        names = ", ".join(r[1] for r in rows) or "(none)"
        sys.exit(f"'{name_query}' matched {len(rows)} screenings: {names}. Be more specific.")
    sid, name = rows[0]
    conn.execute(
        "UPDATE screenings SET last_done=?, last_result=?, updated_at=? WHERE id=?",
        (date_str, result, datetime.datetime.now().isoformat(sep=" ", timespec="seconds"), sid))
    conn.commit()
    return name


def report(conn, today):
    rows = conn.execute(
        "SELECT name, domain, cadence_months, last_done, last_result, priority "
        "FROM screenings"
    ).fetchall()
    items = []
    for name, domain, cad, last_done, result, prio in rows:
        status, due = next_due(last_done, cad, today)
        items.append({"name": name, "domain": domain, "cad": cad, "last_done": last_done,
                      "result": result, "prio": prio, "status": status, "due": due})
    # Overdue + never-done first (weighted by priority), then soonest due.
    prio_rank = {"high": 0, "medium": 1, "low": 2}
    status_rank = {"OVERDUE": 0, "never done": 1, "not done": 2, "scheduled": 3, "one-time done": 4}
    items.sort(key=lambda r: (status_rank.get(r["status"], 9), prio_rank.get(r["prio"], 9),
                              r["due"] or datetime.date.max))
    mark = {"OVERDUE": "⚠", "never done": "•", "not done": "•", "scheduled": "✓", "one-time done": "✓"}
    print(f"Screening calendar (today = {today})\n")
    print(f"{'':1} {'status':<14}{'next due':<12}{'last done':<12}{'pri':<7}name")
    print("-" * 80)
    for r in items:
        due = r["due"].isoformat() if r["due"] else "—"
        print(f"{mark.get(r['status'],' ')} {r['status']:<14}{due:<12}"
              f"{(r['last_done'] or '—'):<12}{r['prio']:<7}{r['name']}")
    due_now = [r for r in items if r["status"] in ("OVERDUE", "never done")]
    print(f"\n{len(due_now)} of {len(items)} need action (overdue or never done). "
          "Populate last_done with --set once scheduled/completed.")


def self_check():
    t = datetime.date(2026, 7, 3)
    assert add_months(datetime.date(2026, 1, 31), 1) == datetime.date(2026, 2, 28)  # clamp
    assert add_months(datetime.date(2025, 3, 14), 24) == datetime.date(2027, 3, 14)
    assert next_due(None, 12, t) == ("never done", None)
    assert next_due(None, None, t) == ("not done", None)
    assert next_due("2020-01-01", None, t) == ("one-time done", None)
    s, due = next_due("2025-01-01", 12, t)          # due 2026-01-01 < today → overdue
    assert s == "OVERDUE" and due == datetime.date(2026, 1, 1)
    s, due = next_due("2026-06-01", 12, t)          # due 2027-06-01 > today
    assert s == "scheduled" and due == datetime.date(2027, 6, 1)
    print("self-check OK")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--init", action="store_true", help="create table + seed starter catalog")
    ap.add_argument("--set", nargs="+", metavar=("NAME DATE", "RESULT"),
                    help="record a completed screening: NAME DATE [RESULT...]")
    ap.add_argument("--today", metavar="YYYY-MM-DD", help="pin 'today' (default: system date)")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()
    if args.self_check:
        self_check()
        return
    conn = sqlite3.connect(DB)
    if args.init:
        print(f"Seeded {init(conn)} new screening(s) into `screenings`.")
    if args.set:
        if len(args.set) < 2:
            sys.exit("--set needs NAME and DATE, e.g. --set DEXA 2025-03-14 \"T-score -0.8\"")
        name = set_done(conn, args.set[0], args.set[1], " ".join(args.set[2:]) or None)
        print(f"Recorded: {name} done {args.set[1]}.")
    today = datetime.date.fromisoformat(args.today) if args.today else datetime.date.today()
    # Only report if the table exists (post --init).
    if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='screenings'").fetchone():
        print()
        report(conn, today)
    elif not args.init:
        print("No `screenings` table yet — run `python3 screening_calendar.py --init` first.")


if __name__ == "__main__":
    main()
