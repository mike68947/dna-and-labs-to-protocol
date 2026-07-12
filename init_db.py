#!/usr/bin/env python3
"""Create labs.db from schema.sql and seed it with a tiny synthetic example, so
`python3 viewer.py` renders out of the box. Replace the example data with your
own (import your labs/DNA/imaging — see CLAUDE.md).

    python3 init_db.py            # create + seed labs.db (refuses if it exists)
    python3 init_db.py --force    # overwrite an existing labs.db
"""
import json
import sqlite3
import sys
from pathlib import Path

import seed_ref_ranges
import screening_calendar

HERE = Path(__file__).parent
DB = HERE / "labs.db"

INSIGHT_COLS = ["category_id", "insight", "insight_dna", "supplements", "diet",
                "activity", "lifestyle", "checkup_schedule", "concordance"]


def main():
    force = "--force" in sys.argv
    if DB.exists() and not force:
        sys.exit(f"{DB} already exists. Use --force to overwrite, or edit it directly.")
    DB.unlink(missing_ok=True)

    conn = sqlite3.connect(DB)
    conn.executescript((HERE / "schema.sql").read_text())

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
    applied, _ = seed_ref_ranges.apply(conn)      # fills ref_low/ref_high from data/ref_ranges.json
    n_screen = screening_calendar.init(conn)      # seeds the starter screening calendar
    conn.commit()

    nb = conn.execute("SELECT COUNT(*) FROM biomarkers").fetchone()[0]
    nr = conn.execute("SELECT COUNT(*) FROM test_results").fetchone()[0]
    nv = conn.execute("SELECT COUNT(*) FROM variants").fetchone()[0]
    conn.close()
    print(f"Created {DB.name}: {nb} biomarkers, {nr} results, {nv} example variants, "
          f"{applied} ref-ranges, {n_screen} screenings.")
    print("Next: python3 viewer.py && open viewer.html")


if __name__ == "__main__":
    main()
