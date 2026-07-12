#!/usr/bin/env python3
"""Apply researched population reference ranges (ref_low/ref_high) to biomarkers
that had none, so their click-through charts show boundary lines.

Data lives in ref_ranges.json (id -> {low, high, source}), in each biomarker's
exact stored unit. Ranges are population reference intervals from standard
catalogs (Mayo/ARUP/LabCorp); adjust for age/sex/lab as needed. Markers with no
consensus reference are left out (not invented).

Guarded: only fills rows where ref_low AND ref_high are currently NULL — never
overwrites a range already recorded from the lab report. Provenance stored in
ref_source. Idempotent. labs.db is the source of truth (see CLAUDE.md).

    python3 seed_ref_ranges.py --self-check
    python3 seed_ref_ranges.py
"""
import argparse, json, sqlite3
from pathlib import Path
DB = Path(__file__).parent / "labs.db"
DATA = Path(__file__).parent / "data" / "ref_ranges.json"

def load():
    return {int(k): v for k, v in json.loads(DATA.read_text(encoding="utf-8")).items()}

def self_check():
    d = load()
    for bid, r in d.items():
        lo, hi = r["low"], r["high"]
        assert lo is not None or hi is not None, f"{bid}: no bound"
        if lo is not None and hi is not None:
            assert lo < hi, f"{bid}: low {lo} >= high {hi}"
        assert r.get("source"), f"{bid}: missing source"
    print(f"self-check OK ({len(d)} ranges)")

def apply(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(biomarkers)")}
    if "ref_source" not in cols:
        conn.execute("ALTER TABLE biomarkers ADD COLUMN ref_source TEXT")
    d = load()
    applied = skipped = 0
    for bid, r in d.items():
        row = conn.execute("SELECT ref_low, ref_high FROM biomarkers WHERE id=?", (bid,)).fetchone()
        if row is None:
            continue
        if row[0] is not None or row[1] is not None:   # guard: never overwrite existing
            skipped += 1
            continue
        conn.execute("UPDATE biomarkers SET ref_low=?, ref_high=?, ref_source=? WHERE id=?",
                     (r["low"], r["high"], r["source"], bid))
        applied += 1
    conn.commit()
    return applied, skipped

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--self-check", action="store_true")
    a = ap.parse_args()
    self_check()
    if a.self_check:
        return
    conn = sqlite3.connect(DB)
    applied, skipped = apply(conn)
    print(f"Applied {applied} reference ranges (skipped {skipped} already-bounded). "
          "Run `python3 viewer.py`.")

if __name__ == "__main__":
    main()
