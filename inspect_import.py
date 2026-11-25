#!/usr/bin/env python3
"""Critically inspect proposed new test_results BEFORE inserting them.

Lab data commonly arrives in the wrong unit or with decimal-shift typos
(androgens in ng/mL vs SI, HCT/PCT as fraction-not-%, absolute counts in
cells/µL vs ×10⁹/L, mg/L vs mmol/L electrolytes, 430.54 for 43.05). Every
import must be screened for these before the INSERT — that is the one place
the bug can be stopped cheaply.

Each proposed value is checked against (a) the biomarker's own history — its
median (gross scale errors, known conversion factors) and its corridor (more than
25% outside everything it has read in ≥4 prior draws) — and (b) its reference
range. Anything off is flagged SUSPECT for human review.

    from inspect_import import commit_import      # inside imports/YYYY-MM-DD_<lab>.py
    n = commit_import(conn, [(biomarker_id, date, value_str), ...], lab="<laboratory>",
                      resolved={(biomarker_id, date): "why this value stands"})
    conn.commit()

commit_import is the one sanctioned way to add results: it screens, refuses to
write while any SUSPECT row is unacknowledged, requires the laboratory, and skips
rows already present for the same (biomarker_id, date).

    python3 inspect_import.py rows.json           # standalone; rows=[[bid,date,value],...]
    python3 inspect_import.py --self-check

LIMITATION: a unit swap that lands inside the corridor, or a biomarker with
fewer than 4 prior draws, still needs judgment — cross-check against sibling
markers and the ref range.
"""
import argparse, json, re, sys, sqlite3, statistics as st
from pathlib import Path

from init_db import migrate

# known conversion ratios (>1) → human label, to name a suspected mismatch
FACTORS = {
    10: 'decimal shift / ×10', 100: '×100', 1000: 'cells/µL↔×10⁹/L or SI-prefix/×1000',
    3.467: 'testosterone/DHT ng/mL↔nmol/L (or ×10 vs ng/dL)',
    24.305: 'Mg mg/L↔mmol/L', 39.098: 'K mg/L↔mmol/L', 22.99: 'Na mg/L↔mmol/L',
    18.02: 'glucose mg/dL↔mmol/L', 88.4: 'creatinine mg/dL↔µmol/L',
    2.496: 'vitamin D ng/mL↔nmol/L', 59.48: 'uric acid mg/dL↔µmol/L',
    17.1: 'bilirubin mg/dL↔µmol/L',
}


def _num(v):
    m = re.match(r'^[<>≤≥~]*\s*(-?\d+\.?\d*)$', str(v).strip().replace(',', '.'))
    return float(m.group(1)) if m else None


def _known(ratio):
    for f, lbl in FACTORS.items():
        if abs(ratio - f) / f < 0.08:
            return lbl
    return None


CORRIDOR_SLACK = 0.25   # fraction beyond the historical min/max before a value is SUSPECT
CORRIDOR_MIN_N = 4      # prior draws needed for "everything it has ever read" to mean something


def outside_corridor(x, hist, slack=CORRIDOR_SLACK):
    """Reason string when x lies further than `slack` outside the min–max of `hist`
    (prior numeric values), else None. Catches the plausible-looking outlier the median
    screen misses: a wrong tube, a new method, a sample that travelled badly."""
    if len(hist) < CORRIDOR_MIN_N:
        return None
    lo, hi = min(hist), max(hist)
    if hi > 0 and x > hi * (1 + slack):
        return f"{(x / hi - 1) * 100:.0f}% above the highest of {len(hist)} prior draws ({lo:g}–{hi:g})"
    if lo > 0 and x < lo * (1 - slack):
        return f"{(1 - x / lo) * 100:.0f}% below the lowest of {len(hist)} prior draws ({lo:g}–{hi:g})"
    return None


def inspect(conn, rows, lab=None):
    """rows: iterable of (biomarker_id, date, value). Returns a list of dicts,
    each with verdict 'OK'|'SUSPECT' and a reason. Non-numeric values pass as OK.
    `lab` (this batch's laboratory) sharpens a corridor flag when it differs from the
    laboratory of the biomarker's previous result."""
    migrate(conn)                       # a database from before test_results.lab gains it
    out = []
    for bid, date, value in rows:
        x = _num(value)
        rec = {'id': bid, 'date': date, 'value': value, 'verdict': 'OK', 'reason': ''}
        if x is None:                       # qualitative / below-detection — nothing to scale
            out.append(rec)
            continue
        meta = conn.execute("SELECT name_en, unit, ref_low, ref_high FROM biomarkers "
                            "WHERE id=?", (bid,)).fetchone() or ('?', '', None, None)
        name, unit, rl, rh = meta
        rec['name'], rec['unit'] = name, unit
        hist = [_num(v) for (v,) in conn.execute(
            "SELECT value FROM test_results WHERE biomarker_id=? AND date!=?", (bid, date))]
        hist = [h for h in hist if h is not None and h > 0]
        reasons = []
        if x > 0 and len(hist) >= 3:                     # vs own history
            med = st.median(hist)
            if med > 0:
                r = x / med
                big = max(r, 1 / r)
                lbl = _known(big)
                if big >= 5 or (big >= 2 and lbl):
                    reasons.append(f"{r:.2g}× series median {med:g} "
                                   f"(seen {min(hist):g}–{max(hist):g})"
                                   + (f" — matches {lbl}" if lbl else ""))
        why = outside_corridor(x, hist) if x > 0 else None   # vs everything it has ever read
        if why:
            prev = conn.execute("SELECT lab FROM test_results WHERE biomarker_id=? AND date!=? "
                                "ORDER BY date DESC LIMIT 1", (bid, date)).fetchone()
            if lab and prev and prev[0] and prev[0] != lab:    # unknown previous lab: say nothing
                why += f" — FIRST RESULT FROM A NEW LAB (previous: {prev[0]}): method, units, sample handling?"
            reasons.append(why)
        if rh is not None and rh > 0 and x > 8 * rh:      # vs reference range
            reasons.append(f"{x / rh:.1f}× ref_high {rh:g}")
        if rl is not None and rl > 0 and 0 < x < rl / 8:
            reasons.append(f"{rl / x:.1f}× below ref_low {rl:g}")
        if reasons:
            rec['verdict'], rec['reason'] = 'SUSPECT', '; '.join(reasons)
        out.append(rec)
    return out


def report(flags):
    """Print flags; return the count of SUSPECT rows (nonzero → gate the insert)."""
    n = 0
    for f in flags:
        if f['verdict'] == 'SUSPECT':
            n += 1
            print(f"⚠ SUSPECT [{f['id']}] {f.get('name', '?')[:30]:30} {f['date']} = "
                  f"{f['value']} {f.get('unit', '')}\n         → {f['reason']}")
    print(f"\n{n}/{len(flags)} rows SUSPECT — resolve each before inserting." if n
          else f"All {len(flags)} rows passed inspection.")
    return n


def commit_import(conn, rows, lab, resolved=None):
    """Screen, then insert. `lab` is mandatory (who measured it; add "(shipped sample)" when
    the tube travelled). Every SUSPECT row must be acknowledged in `resolved` =
    {(biomarker_id, date): why it is accepted as it stands}; otherwise this raises
    ValueError and writes nothing. Rows already present for the same (biomarker_id, date)
    are skipped. Returns the number inserted; the caller commits."""
    if not (lab or "").strip():
        raise ValueError("lab is required: name the laboratory (and '(shipped sample)' if it was)")
    rows, resolved = list(rows), resolved or {}
    unresolved = [f for f in inspect(conn, rows, lab)
                  if f['verdict'] == 'SUSPECT' and (f['id'], f['date']) not in resolved]
    if unresolved:
        report(unresolved)
        raise ValueError(f"{len(unresolved)} SUSPECT row(s) unresolved: fix the value/unit, or "
                         "acknowledge each in resolved={(biomarker_id, date): reason}")
    n = 0
    for bid, date, value in rows:
        n += conn.execute("INSERT OR IGNORE INTO test_results (biomarker_id, date, value, lab) "
                          "VALUES (?,?,?,?)", (bid, date, str(value), lab)).rowcount
    return n


def _self_check():
    conn = sqlite3.connect(':memory:')
    conn.execute("CREATE TABLE biomarkers(id INT, name_en TEXT, unit TEXT, ref_low REAL, ref_high REAL)")
    conn.execute("CREATE TABLE test_results(biomarker_id INT, date TEXT, value TEXT, UNIQUE(biomarker_id, date))")
    # an invented analyte: a fixture never names a real one
    conn.execute("INSERT INTO biomarkers VALUES(1,'Testium','mmol/L',0.7,1.0)")
    for d, v in [('a', '0.9'), ('b', '0.95'), ('c', '0.88'), ('d', '1.0')]:
        conn.execute("INSERT INTO test_results VALUES(1,?,?)", (d, v))
    migrate(conn)                                    # an old database gains the column
    conn.execute("UPDATE test_results SET lab='Lab A'")
    ok = inspect(conn, [(1, 'e', '0.92')])          # normal → OK
    mgL = inspect(conn, [(1, 'e', '22.1')])          # mmol/L value entered as mg/L (×24.3) → SUSPECT
    dec = inspect(conn, [(1, 'e', '9.0')])           # decimal shift ×10 → SUSPECT
    ref = inspect(conn, [(1, 'e', '15')])            # >8× ref_high 1.0 → SUSPECT
    assert ok[0]['verdict'] == 'OK', ok
    assert mgL[0]['verdict'] == 'SUSPECT' and 'Mg mg/L' in mgL[0]['reason'], mgL
    assert dec[0]['verdict'] == 'SUSPECT', dec
    assert ref[0]['verdict'] == 'SUSPECT', ref
    # the corridor: 1.3 is only 1.4× the median (the scale screen stays silent) but 30% above
    # anything on record. A new lab is named when known.
    cor = inspect(conn, [(1, 'e', '1.3')], lab='Lab B')
    assert cor[0]['verdict'] == 'SUSPECT' and 'above the highest of 4' in cor[0]['reason'], cor
    assert 'NEW LAB (previous: Lab A)' in cor[0]['reason'], cor
    assert inspect(conn, [(1, 'e', '1.2')])[0]['verdict'] == 'OK'      # 20% above: inside the slack
    assert outside_corridor(5.0, [8.0, 9.0, 7.0]) is None             # <4 draws: no corridor yet
    # commit_import: refuses without a lab, refuses an unacknowledged SUSPECT, skips duplicates
    for bad in (dict(lab=''), dict(lab='Lab B')):
        try:
            commit_import(conn, [(1, 'e', '1.3')], **bad)
            raise AssertionError(f"commit_import accepted {bad}")
        except ValueError:
            pass
    assert conn.execute("SELECT count(*) FROM test_results WHERE date='e'").fetchone()[0] == 0
    assert commit_import(conn, [(1, 'e', '1.3'), (1, 'a', '0.9')], lab='Lab B',
                         resolved={(1, 'e'): 'repeat draw confirmed'}) == 1   # 'a' already present
    assert conn.execute("SELECT lab FROM test_results WHERE date='e'").fetchone()[0] == 'Lab B'
    print("self-check OK")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('rows', nargs='?', metavar='rows.json',
                    help='JSON file of [[biomarker_id, date, value], ...] to screen')
    ap.add_argument('--self-check', action='store_true', help='run self-tests and exit')
    args = ap.parse_args()

    if args.self_check:
        _self_check()
        return
    if not args.rows:
        ap.error('rows.json is required (or pass --self-check)')   # used to print usage and exit 0
    rows = json.loads(Path(args.rows).read_text())
    conn = sqlite3.connect(Path(__file__).parent / 'labs.db')
    sys.exit(1 if report(inspect(conn, rows)) else 0)


if __name__ == '__main__':
    main()
