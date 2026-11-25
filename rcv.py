#!/usr/bin/env python3
"""How big a change has to be before it is a change: the reference change value (RCV).

Two results of the same analyte differ even when nothing happened — the assay wobbles (CV_A)
and so does the person (within-subject biological variation, CV_I). The RCV is the difference
this noise produces less than 5% of the time; below it, "it went down" is not a finding. One
flat threshold is wrong almost everywhere: HbA1c moves ~1–2% within a person, insulin ~25%.

CV_I is a published number per analyte. It is recorded by hand, with its source, for the
markers that matter (a finding's markers first) — from the EFLM Biological Variation Database
website (biologicalvariation.eu; its API is not open to outside use, and systematic extraction
needs EFLM's written permission) or a published study. A marker with no CV_I on file has NO
noise floor: issues.py says so rather than inventing one.

    python3 rcv.py                                   # what is on file, with each one-draw RCV
    python3 rcv.py --set BIOMARKER_ID CVI --source "where the number comes from"
    python3 rcv.py --self-check

Limits: the RCV assumes the same method and a properly handled sample. It does not see a change
of laboratory or a shipped tube — that is what test_results.lab and the import corridor are for.
CV_A is taken as ½·CV_I (the "desirable" analytical imprecision), since a laboratory's own figure
is rarely on hand.
"""
import argparse
import math
import sqlite3
import sys
from datetime import date
from pathlib import Path

DB = Path(__file__).parent / "labs.db"
Z = 1.96                      # two-sided 95%


def rcv(cvi, n_before=1, n_after=1, cva=None, z=Z):
    """→ (down, up): the fractional fall / rise between the means of n_before and n_after draws
    that noise alone reaches with probability < 5%. Log-normal (Fokkema), so it stays right for
    large CVs; CV in percent."""
    cva = cvi / 2 if cva is None else cva
    sigma = math.sqrt(math.log(1 + (cvi / 100) ** 2) + math.log(1 + (cva / 100) ** 2))
    k = z * sigma * math.sqrt(1 / n_before + 1 / n_after)
    return 1 - math.exp(-k), math.exp(k) - 1


def beyond_noise(cvi, before, after, n_before=1, n_after=1):
    """True when `after` differs from `before` by more than the RCV for these draw counts."""
    if not before:
        return after != before
    down, up = rcv(cvi, n_before, n_after)
    change = (after - before) / abs(before)
    return change > up or change < -down


def load(conn):
    """→ {biomarker_id: cvi}; empty when nothing is on file (no noise floor anywhere)."""
    try:
        return dict(conn.execute("SELECT biomarker_id, cvi FROM biological_variation"))
    except sqlite3.OperationalError:
        return {}


def record(conn, biomarker_id, cvi, source, today=None):
    if not (source or "").strip():
        sys.exit("--source is required: a CV_I without its source is a guess")
    if not conn.execute("SELECT 1 FROM biomarkers WHERE id=?", (biomarker_id,)).fetchone():
        sys.exit(f"no biomarker with id {biomarker_id}")
    conn.execute("INSERT OR REPLACE INTO biological_variation (biomarker_id, cvi, source, recorded_at) "
                 "VALUES (?,?,?,?)", (biomarker_id, float(cvi), source.strip(), today or date.today().isoformat()))
    conn.commit()


def self_check():
    down, up = rcv(6.0)                    # the textbook case: CV_I 6%, CV_A 3% → about −17% / +21%
    assert 0.16 < down < 0.18 and 0.20 < up < 0.21, (down, up)
    assert up > down                       # log-normal: a rise needs more than a fall
    d3, u3 = rcv(6.0, 3, 3)
    assert d3 < down and u3 < up           # the means of three draws are steadier
    assert beyond_noise(6.0, 100, 125) and not beyond_noise(6.0, 100, 110)
    assert beyond_noise(6.0, 100, 80) and not beyond_noise(6.0, 100, 90)
    assert not beyond_noise(25.0, 100, 150) and beyond_noise(25.0, 100, 250)   # a noisy analyte
    c = sqlite3.connect(":memory:")
    c.executescript("CREATE TABLE biomarkers (id INTEGER PRIMARY KEY);"
                    "CREATE TABLE biological_variation (biomarker_id INTEGER PRIMARY KEY, cvi REAL NOT NULL, "
                    "source TEXT NOT NULL, recorded_at TEXT NOT NULL); INSERT INTO biomarkers VALUES (1);")
    assert load(c) == {} and load(sqlite3.connect(":memory:")) == {}
    record(c, 1, 6.0, "invented source for the self-check", "2030-01-01")
    assert load(c) == {1: 6.0}
    print("self-check OK")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--set", nargs=2, metavar=("BIOMARKER_ID", "CVI"))
    ap.add_argument("--source")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    conn = sqlite3.connect(DB)
    from init_db import migrate
    migrate(conn)
    if a.set:
        record(conn, int(a.set[0]), a.set[1], a.source)
    rows = conn.execute("SELECT b.id, b.name_en, b.specimen_en, v.cvi, v.source FROM biological_variation v "
                        "JOIN biomarkers b ON b.id = v.biomarker_id ORDER BY b.name_en").fetchall()
    for bid, name, spec, cvi, src in rows:
        down, up = rcv(cvi)
        print(f"{bid:<5}{name} ({spec}): CV_I {cvi:g}% → one-draw RCV −{down:.0%} / +{up:.0%}   [{src}]")
    print(f"{len(rows)} biomarker(s) with a noise floor — the rest have none")


if __name__ == "__main__":
    sys.exit(main())
