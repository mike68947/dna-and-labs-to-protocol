#!/usr/bin/env python3
"""Create labs.db from schema.sql and seed it with a tiny synthetic example, so
`python3 viewer.py` renders out of the box. Replace the example data with your
own (import your labs/DNA/imaging — see CLAUDE.md).

    python3 init_db.py            # create labs.db + a tiny synthetic demo (renders out of the box)
    python3 init_db.py --empty    # clean start: schema + categories + screenings, NO demo data
    python3 init_db.py --force    # overwrite an existing labs.db (backs it up first)
    python3 init_db.py --backup   # gzipped snapshot of labs.db into backups/ (keeps the newest 14)

Once you import your own data, labs.db is the ONLY copy of it — nothing rebuilds it, and
it is git-ignored. Take a backup before every session that writes to it. Restore:
    gunzip -c backups/labs-<stamp>.db.gz > labs.db
"""
import gzip
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import seed_ref_ranges
import screening_calendar

HERE = Path(__file__).parent
DB = HERE / "labs.db"
BACKUPS = HERE / "backups"
KEEP = 14


def backup(db=DB, out_dir=BACKUPS, keep=KEEP):
    """Snapshot `db` with SQLite's online-backup API (consistent even mid-write), gzip it
    into out_dir/labs-<stamp>.db.gz, keep the newest `keep`. → the new file's path."""
    out_dir.mkdir(exist_ok=True)
    raw = out_dir / f"labs-{time.strftime('%Y%m%d-%H%M%S')}.db"
    src, dst = sqlite3.connect(db), sqlite3.connect(raw)
    src.backup(dst)
    src.close()
    dst.close()
    gz = raw.with_name(raw.name + ".gz")
    with open(raw, "rb") as f, gzip.open(gz, "wb") as g:
        shutil.copyfileobj(f, g)
    raw.unlink()
    for old in sorted(out_dir.glob("labs-*.db.gz"))[:-keep]:
        old.unlink()
    return gz

INSIGHT_COLS = ["category_id", "insight", "insight_dna", "supplements", "diet",
                "activity", "lifestyle", "checkup_schedule", "concordance"]


def main():
    if "--self-check" in sys.argv:
        return _self_check()
    if "--backup" in sys.argv:
        if not DB.exists():
            sys.exit(f"{DB} not found — nothing to back up.")
        print(f"Backed up to {backup().relative_to(HERE)}  (restore: gunzip -c <file> > labs.db)")
        return
    force = "--force" in sys.argv
    empty = "--empty" in sys.argv                 # clean start for your own data (no demo rows)
    if DB.exists() and not force:
        sys.exit(f"{DB} already exists. Use --force to overwrite, or edit it directly.")
    if DB.exists():                               # --force: never destroy the only copy unsaved
        print(f"Backed up the existing {DB.name} to {backup().relative_to(HERE)}")
    DB.unlink(missing_ok=True)

    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA foreign_keys = ON")   # SQLite ignores the declared REFERENCES unless asked
    conn.executescript((HERE / "schema.sql").read_text())   # tables + the 16 categories

    applied = 0
    if not empty:
        seed = json.loads((HERE / "data" / "seed.json").read_text())

        for b in seed["biomarkers"]:
            conn.execute(
                "INSERT INTO biomarkers "
                "(id, name_en, name_ru, specimen_en, specimen_ru, unit, opt_low, opt_high) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (b["id"], b["name"], b["name"], b.get("specimen"), b.get("specimen"),
                 b.get("unit"), b.get("opt_low"), b.get("opt_high")))
            for cid in b["cats"]:
                conn.execute("INSERT INTO biomarker_categories (biomarker_id, category_id) VALUES (?,?)",
                             (b["id"], cid))

        conn.executemany("INSERT INTO test_results (biomarker_id, date, value) VALUES (?,?,?)",
                         [(r["bid"], r["date"], r["value"]) for r in seed["results"]])

        for ins in seed.get("insights", []):
            conn.execute(
                f"INSERT INTO category_insights ({','.join(INSIGHT_COLS)}) "
                f"VALUES ({','.join('?' * len(INSIGHT_COLS))})",
                [ins.get(c) for c in INSIGHT_COLS])

        conn.executemany(
            "INSERT INTO variants (category_id, rsid, gene, relevance, genotype, zygosity) "
            "VALUES (?,?,?,?,?,?)",
            [(v["category_id"], v["rsid"], v.get("gene"), v.get("relevance"),
              v.get("genotype"), v.get("zygosity")) for v in seed.get("variants", [])])

        if seed.get("unified_protocol"):
            conn.execute("INSERT INTO unified_protocol (protocol) VALUES (?)", (seed["unified_protocol"],))

        conn.commit()
        applied, _ = seed_ref_ranges.apply(conn)  # fills ref_low/ref_high from data/ref_ranges.json

    n_screen = screening_calendar.init(conn)      # seeds the starter screening calendar (both modes)
    conn.commit()

    ncat = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    nb = conn.execute("SELECT COUNT(*) FROM biomarkers").fetchone()[0]
    nr = conn.execute("SELECT COUNT(*) FROM test_results").fetchone()[0]
    conn.close()
    if empty:
        print(f"Created {DB.name} (empty): {ncat} categories, {n_screen} screenings. "
              "Now import your data — see CLAUDE.md.")
    else:
        print(f"Created {DB.name}: {nb} biomarkers, {nr} results, {ncat} categories, "
              f"{applied} ref-ranges, {n_screen} screenings.")
    print("Next: python3 viewer.py && open viewer.html")


def _self_check():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        db = d / "t.db"
        c = sqlite3.connect(db)
        c.execute("CREATE TABLE t (x)")
        c.execute("INSERT INTO t VALUES ('Testium')")
        c.commit()
        c.close()
        for _ in range(3):
            gz = backup(db, d / "b", keep=2)
            time.sleep(1.01)                      # distinct stamps
        kept = sorted((d / "b").glob("labs-*.db.gz"))
        assert len(kept) == 2 and kept[-1] == gz, kept
        restored = d / "r.db"
        restored.write_bytes(gzip.decompress(gz.read_bytes()))
        assert sqlite3.connect(restored).execute("SELECT x FROM t").fetchone() == ("Testium",)
    print("self-check OK")


if __name__ == "__main__":
    main()
