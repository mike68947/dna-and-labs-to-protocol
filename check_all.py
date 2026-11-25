#!/usr/bin/env python3
"""Run every check in one command — the gate to run after `python3 viewer.py`.

Every script here has a --self-check, and until this file existed nothing ran them.
Two classes of check, deliberately kept apart:

  CONSISTENCY — two layers disagree about the same fact, or the database breaks a
  structural invariant. A defect; it gates (exit 1) until reconciled.

  REPORTS — a work queue: insights older than the labs they describe, generated files
  older than labs.db. Informational, never gates. Staleness is almost never empty, so
  gating on it would only train the habit of ignoring the gate.

Keep that split when adding a check. A blocking channel that cries wolf gets overridden,
and then it protects nothing.

    python3 check_all.py               # self-checks, CONSISTENCY gate, REPORTS; exit 1 on FAIL/BROKEN
    python3 check_all.py --self-check
"""
import argparse
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DB = ROOT / "labs.db"

SELF_CHECKS = ["init_db.py", "inspect_import.py", "import_dna.py", "lookup_variant.py", "check_variants.py",
               "seed_ref_ranges.py",
               "screening_calendar.py", "export_for_llm.py", "viewer.py"]
# generated from labs.db; warn when older than it (or, for the viewer, missing)
ARTIFACTS = ["viewer.html", "master_protocol.md", "labs_export.md", "labs_summary.md"]


def db_invariants(con):
    """→ violation strings (empty = clean). Facts labs.db must hold that no per-script
    self-check owns: no foreign-key orphans (SQLite only enforces REFERENCES on connections
    that ask, so a row written without the pragma can dangle) and no future-dated result."""
    bad = [f"foreign_key_check: {t} rowid={rid} → {parent}" for t, rid, parent, _ in
           con.execute("PRAGMA foreign_key_check")]
    fut = con.execute("SELECT count(*) FROM test_results WHERE date > date('now')").fetchone()[0]
    if fut:
        bad.append(f"{fut} test_results row(s) dated in the future")
    return bad


def stale_insights(con):
    """→ report lines: categories with labs but no insight, or an insight older than the
    newest result of any of its biomarkers. Membership comes from biomarker_categories.
    # ponytail: junction-table staleness; a prose-citation index (which insight *mentions*
    # which biomarker) is the upgrade if insights cite markers outside their own category."""
    rows = con.execute("""
        SELECT c.id, c.name_en, ci.updated_at, MAX(t.date)
        FROM categories c
        LEFT JOIN category_insights ci ON ci.category_id = c.id
        LEFT JOIN biomarker_categories bc ON bc.category_id = c.id
        LEFT JOIN test_results t ON t.biomarker_id = bc.biomarker_id
        GROUP BY c.id ORDER BY c.id""").fetchall()
    out = []
    for cid, name, updated, newest in rows:
        if newest is None:
            continue                                   # no labs in this category: nothing to say
        if updated is None:
            out.append(f"cat {cid} {name}: labs to {newest}, no insight yet")
        elif str(updated)[:10] < newest:
            out.append(f"cat {cid} {name}: insight {str(updated)[:10]} < newest lab {newest}")
    return out


BACKUP_LAG_DAYS = 3


def freshness():
    """→ warning strings: generated artifacts older than labs.db (viewer.html also if missing),
    and the newest backup more than BACKUP_LAG_DAYS behind labs.db (none at all counts)."""
    db_mtime = DB.stat().st_mtime
    warns = []
    snaps = sorted((ROOT / "backups").glob("labs-*.db.gz"), key=lambda p: p.stat().st_mtime)
    if not snaps:
        warns.append("backups/: no snapshot of labs.db — python3 init_db.py --backup")
    elif db_mtime - snaps[-1].stat().st_mtime > BACKUP_LAG_DAYS * 86400:
        warns.append(f"backups/: newest snapshot is more than {BACKUP_LAG_DAYS} days behind labs.db "
                     "— python3 init_db.py --backup")
    for name in ARTIFACTS:
        p = ROOT / name
        if not p.exists():
            if name == "viewer.html":
                warns.append("viewer.html: missing — run viewer.py")
        elif p.stat().st_mtime < db_mtime:
            warns.append(f"{name}: older than labs.db — regenerate")
    return warns


def run_self_check(script):
    """→ (ok, output). A missing script is a failure, not a silent skip."""
    path = ROOT / script
    if not path.exists():
        return False, f"missing: {script}"
    p = subprocess.run([sys.executable, str(path), "--self-check"], capture_output=True, text=True, cwd=ROOT)
    return p.returncode == 0, (p.stdout + p.stderr).rstrip()


def self_check():
    missing = [s for s in SELF_CHECKS if not (ROOT / s).exists()]
    assert not missing, f"referenced but absent: {missing}"
    mem = sqlite3.connect(":memory:")
    mem.executescript("""
        CREATE TABLE categories (id INTEGER PRIMARY KEY, name_en TEXT);
        CREATE TABLE biomarkers (id INTEGER PRIMARY KEY);
        CREATE TABLE biomarker_categories (biomarker_id INTEGER, category_id INTEGER);
        CREATE TABLE test_results (id INTEGER PRIMARY KEY,
            biomarker_id INTEGER REFERENCES biomarkers(id), date TEXT, value TEXT);
        CREATE TABLE category_insights (category_id INTEGER, updated_at TEXT);
        INSERT INTO categories VALUES (1, 'Lipids'), (2, 'Liver'), (3, 'Empty');
        INSERT INTO biomarkers VALUES (1), (2);
        INSERT INTO biomarker_categories VALUES (1, 1), (2, 2);
        INSERT INTO test_results VALUES (1, 1, '2026-03-01', '5'), (2, 2, '2026-03-01', '30');
        INSERT INTO category_insights VALUES (1, '2026-01-15');""")
    assert db_invariants(mem) == [], db_invariants(mem)
    st = stale_insights(mem)
    assert len(st) == 2 and "cat 1" in st[0] and "<" in st[0] and "cat 2" in st[1] and "no insight" in st[1], st
    mem.execute("UPDATE category_insights SET updated_at = '2026-03-01'")   # same-day regen is current
    assert not any("cat 1" in s for s in stale_insights(mem))
    mem.execute("INSERT INTO test_results VALUES (3, 999, '2999-12-31', '1')")  # orphan + future date
    bad = db_invariants(mem)
    assert len(bad) == 2 and "foreign_key_check" in bad[0] and "future" in bad[1], bad
    print(f"self-check OK — {len(SELF_CHECKS)} self-checks wired")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-check", action="store_true")
    if ap.parse_args().self_check:
        self_check()
        return 0
    if not DB.exists():
        sys.exit(f"{DB} not found — run python3 init_db.py")

    print("── self-checks ──")
    broken = []
    for s in SELF_CHECKS:
        ok, out = run_self_check(s)
        if not ok:
            broken.append(s)
            print(f"  ✗ {s}\n{out}")
    print(f"  {len(SELF_CHECKS) - len(broken)}/{len(SELF_CHECKS)} passed")

    con = sqlite3.connect(DB)
    print("\n── db invariants (FK orphans, future dates) [CONSISTENCY] ──")
    bad = db_invariants(con)
    print("\n".join(bad) if bad else "OK — no orphans, no future-dated results")

    import check_variants as CV
    print("\n── variants: direction in prose, promoted rows vs genome [CONSISTENCY] ──")
    vbad = [f"{w}: {r} {what} written up as carried — \"{s}\"" for w, r, what, s in CV.carried_claims(con)]
    mism, unread = CV.promoted_vs_genome(con)
    vbad += mism
    print("\n".join(vbad) if vbad else "OK — no 0-copy variant written up as carried; promoted rows match the genome")
    for u in unread:
        print(f"  (report) {u}")
    bad += vbad

    print("\n── stale insights vs labs [report] ──")
    st = stale_insights(con)
    print("\n".join(st) if st else "OK — every category with labs has an insight at least as new")
    con.close()

    print("\n── generated files and backups vs labs.db [report] ──")
    fresh = freshness()
    print("\n".join(fresh) if fresh else "OK — artifacts newer than labs.db, a recent backup exists")

    print("\n" + "=" * 60)
    # A broken self-check and a data inconsistency are different failures — reporting a
    # tooling defect as "consistency" sends you hunting for a data disagreement that isn't there.
    if broken:
        print(f"BROKEN — {len(broken)} self-check(s) did not run cleanly (tooling, not data): {', '.join(broken)}")
    if bad:
        print(f"FAIL — {len(bad)} consistency violation(s) need reconciling")
    if broken or bad:
        return 1
    print("OK — no consistency check is failing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
