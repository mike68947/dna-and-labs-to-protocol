#!/usr/bin/env python3
"""Preventive-screening forward calendar — the "what's due next" tracker.

The `screenings` table is the ONLY place a cadence lives (the routine and the findings
quote none): a small table you populate, from which overdue / next-due is computed off
last_done + cadence. A row may belong to a finding (`issue_id`, set by issues.py --load);
the plan ranks it by that finding's severity.

    python3 screening_calendar.py --init              # create table + seed starter catalog
    python3 screening_calendar.py                     # report: overdue / next due
    python3 screening_calendar.py --plan              # what needs doing, grouped by the trip that clears it
    python3 screening_calendar.py --set "DEXA" 2025-03-14 "T-score -0.8"
    python3 screening_calendar.py --today 2026-01-01  # pin "today" for testing
    python3 screening_calendar.py --self-check

Seed cadences are reasonable DEFAULTS for a general adult — adjust per your
physician's guidance; `--init` is an UPSERT by name (code = DB), so edit SEED and
re-run. Lab-backed rows (DERIVED_FROM) read "last done" straight from test_results
and self-update on every import; imaging/procedure rows are recorded with --set.
"""
import argparse
import calendar
import datetime
import re
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).parent / "labs.db"

# name, domain, cadence_months (None = one-time/as-needed), priority, rationale.
# A general-adult starter set — edit for your age, sex, and physician guidance. Keep a lab
# draw and an imaging study in separate rows: different cadences, different date sources.
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

# Lab-backed screenings read "last done" from test_results — never from the stored last_done
# column, which goes stale on every import. name → the biomarker name_en's that make up the
# panel; a panel is done only when EVERY component has been drawn (MIN of the latest dates),
# so a half-done draw doesn't clear it. Imaging/procedure rows stay out (their dates exist
# nowhere else in the database).
DERIVED_FROM = {
    "Lipid panel": ["Total Cholesterol", "LDL Cholesterol", "HDL Cholesterol", "Triglycerides"],
    "Fasting glucose / HbA1c": ["Glucose (fasting)", "HbA1c"],
}

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
    updated_at TEXT,
    issue_id INTEGER                  -- the finding this row belongs to (issues.py owns it)
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


def derived_dates(conn):
    """{screening name: last fully-drawn date or None} for every DERIVED_FROM row — the one
    place the CLI report and viewer.py both read from, so they can't disagree."""
    out = {}
    for name, markers in DERIVED_FROM.items():
        latest = [conn.execute(
            "SELECT MAX(t.date) FROM test_results t JOIN biomarkers b ON b.id = t.biomarker_id "
            "WHERE b.name_en = ?", (m,)).fetchone()[0] for m in markers]
        out[name] = None if None in latest else min(latest)   # least-recent component rules
    return out


def init(conn):
    """Seed/UPSERT the SEED catalog by name — code = DB. Cadence, priority and rationale follow
    this file; last_done / last_result are never touched. Returns rows newly inserted."""
    conn.execute(SCHEMA)
    now = datetime.datetime.now().isoformat(sep=" ", timespec="seconds")
    before = conn.execute("SELECT COUNT(*) FROM screenings").fetchone()[0]
    for name, domain, cad, prio, why in SEED:
        conn.execute(
            "INSERT INTO screenings (name, domain, cadence_months, priority, rationale, updated_at) "
            "VALUES (?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET domain=excluded.domain, "
            "cadence_months=excluded.cadence_months, priority=excluded.priority, "
            "rationale=excluded.rationale, updated_at=excluded.updated_at",
            (name, domain, cad, prio, why, now))
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM screenings").fetchone()[0] - before


def set_done(conn, name_query, date_str, result):
    datetime.date.fromisoformat(date_str)  # validate
    rows = conn.execute(
        "SELECT id, name FROM screenings WHERE name LIKE ?", (f"%{name_query}%",)
    ).fetchall()
    if len(rows) != 1:
        names = ", ".join(r[1] for r in rows) or "(none)"
        sys.exit(f"'{name_query}' matched {len(rows)} screenings: {names}. Be more specific.")
    sid, name = rows[0]
    if name in DERIVED_FROM:
        print(f"note: '{name}' is lab-backed — the report reads its date from test_results and "
              f"will ignore this --set date. Import the lab result instead.")
    conn.execute(
        "UPDATE screenings SET last_done=?, last_result=?, updated_at=? WHERE id=?",
        (date_str, result, datetime.datetime.now().isoformat(sep=" ", timespec="seconds"), sid))
    conn.commit()
    return name


def compute(conn, today):
    """Every row with its resolved status — the one place the report, the plan, issues.py and
    the viewer read. Sorted: overdue / never done first (by priority), then soonest due."""
    rows = conn.execute(
        "SELECT name, domain, cadence_months, last_done, last_result, priority FROM screenings"
    ).fetchall()
    derived = derived_dates(conn)
    items = []
    for name, domain, cad, last_done, result, prio in rows:
        if name in derived:                 # lab-backed: the stored column is ignored
            last_done = derived[name]
        status, due = next_due(last_done, cad, today)
        items.append({"name": name, "domain": domain, "cad": cad, "last_done": last_done,
                      "result": result, "prio": prio, "status": status, "due": due,
                      "derived": name in derived})
    prio_rank = {"high": 0, "medium": 1, "low": 2}
    status_rank = {"OVERDUE": 0, "never done": 1, "not done": 2, "scheduled": 3, "one-time done": 4}
    items.sort(key=lambda r: (status_rank.get(r["status"], 9), prio_rank.get(r["prio"], 9),
                              r["due"] or datetime.date.max, r["name"]))
    return items


# ── plan: the queue turned into trips ──
# ponytail: where a row gets done is read off its NAME — first match wins, anything else is a
# blood draw. Name a new SEED row so a pattern recognises it, or give SEED a `kind` field.
KINDS = [
    ("AT HOME", r"blood pressure|at home|home "),
    ("VISITS", r"referral|consult"),
    ("IMAGING & PROCEDURES", r"ultrasound|\bMRI\b|\bCT\b|DEXA|echocardiogra|\bECG\b|\(CAC\)|colonoscopy|"
                             r"endoscopy|mammogra|\bscan\b"),
    ("VISITS", r"\bexam\b|cleaning|check-?up"),
    ("URINE", r"urine"),
    ("STOOL", r"\bstool\b"),
]
PLAN_ORDER = ["BLOOD", "URINE", "STOOL", "IMAGING & PROCEDURES", "VISITS", "AT HOME"]
YEAR_LATE = 365


def kind(name):
    return next((k for k, pat in KINDS if re.search(pat, name, re.I)), "BLOOD")


def findings(conn):
    """{row name: (finding key, title, severity)} for rows linked to a LIVE finding."""
    try:
        return {n: (k, t, sev) for n, k, t, sev in conn.execute(
            "SELECT s.name, i.key, i.title, i.severity FROM screenings s JOIN issues i ON i.id = s.issue_id "
            "WHERE i.status IN ('open', 'watch')")}
    except sqlite3.OperationalError:          # a database from before findings existed
        return {}


def plan(conn, today):
    """The rows that need action, grouped by the trip that clears them → lines. Ranked by the
    severity of the finding a row belongs to, then its priority, then its due date, then its
    name. viewer.py draws the same plan in JavaScript (`screeningPlan`): keep the two in step."""
    finding, rank = findings(conn), {"high": 0, "medium": 1, "low": 2}
    rows = []
    for r in compute(conn, today):
        if r["status"] not in ("OVERDUE", "never done"):
            continue
        key, _, sev = finding.get(r["name"], (None, None, 0))
        rows.append(dict(r, finding=key, sev=sev, kind=kind(r["name"]),
                         late=(today - r["due"]).days if r["due"] else None))
    rows.sort(key=lambda r: (-r["sev"], rank.get(r["prio"], 9), r["due"] or datetime.date.max, r["name"]))

    def line(r):
        when = ("never done" if r["late"] is None else f"{r['late']} d late" if r["late"] < YEAR_LATE
                else f"{r['late'] / 365:.1f} y late")
        return f"{r['prio']:<7}{when:<11}{r['name']}" + (f"   [{r['finding']} · sev {r['sev']}]" if r["finding"] else "")

    out = [f"Screening plan (today = {today}) — {len(rows)} row(s) need action", "",
           "FIRST — by the severity of the finding a row belongs to, then priority, then how late"]
    out += [f"  {n}. {line(r)}" for n, r in enumerate(rows[:3], 1)]
    for k in PLAN_ORDER:
        mine = [r for r in rows if r["kind"] == k]
        if mine:
            out += ["", f"{k} — {len(mine)}"] + [f"  {line(r)}" for r in mine]
    old = sum(1 for r in rows if (r["late"] or 0) >= YEAR_LATE)
    if old:
        out += ["", f"{old} row(s) are more than a year late. Do them, or change the cadence / drop the row "
                    "in SEED: a queue that never empties stops being read."]
    return out


def report(conn, today):
    items = compute(conn, today)
    mark = {"OVERDUE": "⚠", "never done": "•", "not done": "•", "scheduled": "✓", "one-time done": "✓"}
    print(f"Screening calendar (today = {today})\n")
    print(f"{'':1} {'status':<14}{'next due':<12}{'last done':<12}{'pri':<7}name")
    print("-" * 80)
    for r in items:
        due = r["due"].isoformat() if r["due"] else "—"
        ld = (r["last_done"] or "—") + ("*" if r["derived"] else "")
        print(f"{mark.get(r['status'],' ')} {r['status']:<14}{due:<12}"
              f"{ld:<12}{r['prio']:<7}{r['name']}")
    due_now = [r for r in items if r["status"] in ("OVERDUE", "never done")]
    print(f"\n{len(due_now)} of {len(items)} need action (overdue or never done).")
    print(f"* {sum(r['derived'] for r in items)} lab-backed row(s) read last-done from test_results "
          "(self-update on import). Record the imaging/procedure rows with --set.")
    # a mapped biomarker not in the DB keeps its panel 'never done' until imported under that name
    missing = [m for ms in DERIVED_FROM.values() for m in ms
               if not conn.execute("SELECT 1 FROM biomarkers WHERE name_en = ?", (m,)).fetchone()]
    if missing:
        print(f"  {len(missing)} mapped biomarker(s) not in `biomarkers` yet — import under exactly "
              f"these names: {'; '.join(missing)}")


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
    # derived dates: MIN over components; a missing component → None; unmapped rows untouched
    mem = sqlite3.connect(":memory:")
    mem.executescript("""
        CREATE TABLE biomarkers (id INTEGER PRIMARY KEY, name_en TEXT);
        CREATE TABLE test_results (id INTEGER PRIMARY KEY, biomarker_id INTEGER, date TEXT, value TEXT);
        INSERT INTO biomarkers VALUES (1,'Total Cholesterol'),(2,'LDL Cholesterol'),(3,'HDL Cholesterol'),
                                      (4,'Triglycerides'),(5,'Glucose (fasting)');
        INSERT INTO test_results (biomarker_id, date, value) VALUES
          (1,'2026-01-10','5'),(1,'2026-06-01','5'),(2,'2026-06-01','3'),(3,'2026-06-01','1'),
          (4,'2026-03-01','1'),(5,'2026-06-01','5');""")
    d = derived_dates(mem)
    assert d["Lipid panel"] == "2026-03-01", d          # triglycerides not redrawn → panel dated by it
    assert d["Fasting glucose / HbA1c"] is None, d      # HbA1c never drawn → not done
    # init is an UPSERT: idempotent, follows SEED on re-run, never touches last_done
    assert init(mem) == len(SEED) and init(mem) == 0
    mem.execute("UPDATE screenings SET cadence_months = 99, last_done = '2025-01-01' WHERE name = 'Colonoscopy'")
    init(mem)
    assert mem.execute("SELECT cadence_months, last_done FROM screenings WHERE name = 'Colonoscopy'").fetchone() \
        == (120, '2025-01-01')
    # the plan: trips read off the names; a row of a live finding ranks first; a year-late row is counted
    assert [kind(n) for n, *_ in SEED] == ["AT HOME", "BLOOD", "BLOOD", "IMAGING & PROCEDURES", "VISITS", "VISITS",
                                            "VISITS", "IMAGING & PROCEDURES", "IMAGING & PROCEDURES",
                                            "IMAGING & PROCEDURES"], [kind(n) for n, *_ in SEED]
    mem.executescript("""CREATE TABLE issues (id INTEGER PRIMARY KEY, key TEXT, title TEXT, severity INT, status TEXT);
        INSERT INTO issues VALUES (1, 'testium-high', 'Testium high', 60, 'open');
        UPDATE screenings SET issue_id = 1 WHERE name = 'Dilated eye exam';""")
    lines = plan(mem, t)
    assert lines[3].strip().startswith("1. medium") and "testium-high · sev 60" in lines[3], lines[:5]
    assert any(ln.startswith("VISITS — ") for ln in lines)
    print("self-check OK")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--init", action="store_true", help="create table + seed starter catalog")
    ap.add_argument("--set", nargs="+", metavar=("NAME DATE", "RESULT"),
                    help="record a completed screening: NAME DATE [RESULT...]")
    ap.add_argument("--plan", action="store_true", help="what needs doing, grouped by trip")
    ap.add_argument("--today", metavar="YYYY-MM-DD", help="pin 'today' (default: system date)")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()
    if args.self_check:
        self_check()
        return
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA foreign_keys = ON")
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
        if args.plan:
            print("\n".join(plan(conn, today)))
        else:
            report(conn, today)
    elif not args.init:
        print("No `screenings` table yet — run `python3 screening_calendar.py --init` first.")


if __name__ == "__main__":
    main()
