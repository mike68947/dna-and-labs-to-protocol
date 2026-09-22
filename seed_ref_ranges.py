#!/usr/bin/env python3
"""Apply researched population reference ranges (ref_low/ref_high) to biomarkers
that had none, so their click-through charts show boundary lines.

Data lives in data/ref_ranges.json (id -> {name, low, high, source}), keyed to the
biomarkers in data/seed.json, in each biomarker's exact stored unit. Ranges are
population reference intervals from standard catalogs (Mayo/ARUP/LabCorp); adjust
for age/sex/lab as needed. Markers with no consensus reference are left out.

Guarded twice: a range is applied only to the biomarker with BOTH that id and that
name (ids are seed-specific — your own labs.db reuses them for other markers), and
only where ref_low AND ref_high are currently NULL — never overwriting a range
recorded from a lab report. Provenance stored in ref_source. Idempotent. For your
own data, reference ranges come from the report itself (see CLAUDE.md).

    python3 seed_ref_ranges.py --self-check
    python3 seed_ref_ranges.py
"""
import argparse, json, sqlite3
from pathlib import Path
DB = Path(__file__).parent / "labs.db"
DATA = Path(__file__).parent / "data" / "ref_ranges.json"
SEED = Path(__file__).parent / "data" / "seed.json"

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
        assert r.get("name"), f"{bid}: missing name (ranges are matched by id AND name)"
    # every id must point at the seed biomarker it was written for — apply() skips a mismatch
    # silently, which would hide a range keyed to an id whose marker was renamed or removed
    seed = {b["id"]: b["name"] for b in json.loads(SEED.read_text(encoding="utf-8"))["biomarkers"]}
    wrong = [f"{b}: {r['name']!r} vs seed {seed.get(b)!r}" for b, r in d.items() if seed.get(b) != r["name"]]
    assert not wrong, f"ref_ranges.json ids don't match data/seed.json: {wrong}"
    print(f"self-check OK ({len(d)} ranges)")

def apply(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(biomarkers)")}
    if "ref_source" not in cols:
        conn.execute("ALTER TABLE biomarkers ADD COLUMN ref_source TEXT")
    d = load()
    applied = skipped = 0
    for bid, r in d.items():
        row = conn.execute("SELECT name_en, ref_low, ref_high FROM biomarkers WHERE id=?", (bid,)).fetchone()
        if row is None or row[0] != r["name"]:   # not this DB's marker (ids are seed-specific) → never touch
            continue
        if row[1] is not None or row[2] is not None:   # guard: never overwrite existing
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
    conn.execute("PRAGMA foreign_keys = ON")
    applied, skipped = apply(conn)
    print(f"Applied {applied} reference ranges (skipped {skipped} already-bounded). "
          "Run `python3 viewer.py`.")

if __name__ == "__main__":
    main()
