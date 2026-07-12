#!/usr/bin/env python3
"""Critically inspect proposed new test_results BEFORE inserting them.

Lab data commonly arrives in the wrong unit or with decimal-shift typos
(androgens in ng/mL vs SI, HCT/PCT as fraction-not-%, absolute counts in
cells/µL vs ×10⁹/L, mg/L vs mmol/L electrolytes, 430.54 for 43.05). Every
import must be screened for these before the INSERT — that is the one place
the bug can be stopped cheaply.

Each proposed value is checked against (a) the biomarker's own history and
(b) its reference range. Gross scale mismatches and matches to known
unit-conversion factors are flagged SUSPECT for human review.

    from inspect_import import inspect            # inside a /tmp import script
    flags = inspect(conn, [(biomarker_id, date, value_str), ...])
    # resolve every flag['verdict']=='SUSPECT' before committing the insert

    python3 inspect_import.py rows.json           # standalone; rows=[[bid,date,value],...]
    python3 inspect_import.py --self-check

LIMITATION: catches gross scale errors (≥~3–5× off, or a known factor). A unit
swap that lands in the same magnitude (e.g. a low-but-plausible value) still
needs clinical judgment — cross-check against sibling markers and the ref range.
"""
import json, re, sys, sqlite3, statistics as st
from pathlib import Path

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


def inspect(conn, rows):
    """rows: iterable of (biomarker_id, date, value). Returns a list of dicts,
    each with verdict 'OK'|'SUSPECT' and a reason. Non-numeric values pass as OK."""
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


def _self_check():
    conn = sqlite3.connect(':memory:')
    conn.execute("CREATE TABLE biomarkers(id INT, name_en TEXT, unit TEXT, ref_low REAL, ref_high REAL)")
    conn.execute("CREATE TABLE test_results(biomarker_id INT, date TEXT, value TEXT)")
    conn.execute("INSERT INTO biomarkers VALUES(1,'Magnesium','mmol/L',0.7,1.0)")
    for d, v in [('a', '0.9'), ('b', '0.95'), ('c', '0.88'), ('d', '1.0')]:
        conn.execute("INSERT INTO test_results VALUES(1,?,?)", (d, v))
    ok = inspect(conn, [(1, 'e', '0.92')])          # normal → OK
    mgL = inspect(conn, [(1, 'e', '22.1')])          # mmol/L value entered as mg/L (×24.3) → SUSPECT
    dec = inspect(conn, [(1, 'e', '9.0')])           # decimal shift ×10 → SUSPECT
    ref = inspect(conn, [(1, 'e', '15')])            # >8× ref_high 1.0 → SUSPECT
    assert ok[0]['verdict'] == 'OK', ok
    assert mgL[0]['verdict'] == 'SUSPECT' and 'Mg mg/L' in mgL[0]['reason'], mgL
    assert dec[0]['verdict'] == 'SUSPECT', dec
    assert ref[0]['verdict'] == 'SUSPECT', ref
    print("self-check OK")


def main():
    if '--self-check' in sys.argv:
        _self_check()
        return
    args = [a for a in sys.argv[1:] if not a.startswith('-')]
    if not args:
        print("usage: inspect_import.py rows.json  (rows=[[bid,date,value],...])  |  --self-check")
        return
    rows = json.loads(Path(args[0]).read_text())
    conn = sqlite3.connect(Path(__file__).parent / 'labs.db')
    sys.exit(1 if report(inspect(conn, rows)) else 0)


if __name__ == '__main__':
    main()
