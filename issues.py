#!/usr/bin/env python3
"""Findings — what is wrong, what "good" means, what is prescribed, and whether it is working.

A finding is ONE problem with one exit criterion ("LDL above target", not "Heart"). Four tables
hold it, and they are written only through this script, so every judgement keeps its reason:

  issues         the finding: plain-language summary, two scores, status, exit criterion (done_when)
  issue_markers  what measures it and what "good" means (target_low / target_high; both NULL = a
                 context marker with no target)
  issue_actions  the prescription: take / conditional / avoid an item, or a diet / activity /
                 lifestyle / refer rule — each with its reason. No dose (the routine holds doses) and
                 no lab value (test_results holds values).
  issue_events   why a score, status, target or action changed — written in the same transaction as
                 the change; --check fails when the history and the tables disagree.

NOT stored: whether a marker is on target, which way it moved, whether the move clears the analyte's
noise floor. That is computed from test_results on every read, so it cannot go stale. A direction
needs two COMPARABLE draws — the latest not stale, the two at most 400 days apart, from the same
laboratory — otherwise the marker reads "off target" with the reason. A move inside the analyte's
noise floor (rcv.py; only where a CV_I is on file) is "flat".

A symptom score or an imaging measurement is an ordinary biomarker (specimen "Self-report" /
"Imaging"), so it gets a chart, a target and the import screen with no further tables.

Scores, 0–100 — an editorial reading, not validated clinical scores. Keep them apart: an
unconfirmed result lowers confidence, not severity.
  Severity    how much harm the finding could do if left unmanaged (50+ disease or a risk factor for a
              serious event · 25–49 a real but mild abnormality · 1–24 reassuring, maintain and monitor)
  Confidence  how firmly your own data establish it (75+ repeated or confirmed · 50–74 real evidence
              with a gap · 25–49 a single unconfirmed value)
Status: open (act on it) · watch (monitor only) · resolved · retired (the finding was withdrawn).

    python3 issues.py                     # every finding, its state, what is off target
    python3 issues.py --show KEY
    python3 issues.py --load inputs/findings.json   # create findings; existing keys are left alone
    python3 issues.py --set KEY severity=40 status=watch --why "what changed and why"
    python3 issues.py --marker KEY BIOMARKER_ID --low 1.0 --high 3.0 --why "…"
    python3 issues.py --action KEY KIND NAME [--on BIOMARKER_ID] [--detail …] --why "…"
    python3 issues.py --drop-action KEY KIND NAME [--on BIOMARKER_ID] --why "…"
    python3 issues.py --check             # CONSISTENCY — check_all.py gates on it
    python3 issues.py --self-check
"""
import argparse
import datetime
import json
import re
import sqlite3
import sys
from pathlib import Path

import rcv
from protocol_view import LAB_VALUE

ROOT = Path(__file__).parent
DB = ROOT / "labs.db"
STATUSES = ("open", "watch", "resolved", "retired")
LIVE = ("open", "watch")
FIELDS = ("category_id", "title", "summary", "severity", "confidence", "status", "done_when")
STALE_DAYS = 400        # a marker last read longer ago than this says little about today
ACTION_KINDS = ("take", "conditional", "avoid", "diet", "activity", "lifestyle", "refer")
ACTION_TAG = re.compile(r"\[([+~-])(\w+) (.+?)(?: @(\d+))?\]$")   # ends every action event; --check replays it


def _rows(conn, sql, args=()):
    cur = conn.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur]


def _issue(conn, key):
    rows = _rows(conn, "SELECT * FROM issues WHERE key=?", (key,))
    if not rows:
        sys.exit(f"no finding with key {key!r} — `python3 issues.py` lists them")
    return rows[0]


def _need_why(why):
    if not (why or "").strip():
        sys.exit("--why is required: the reason is the history")


def _event(conn, issue_id, kind, why, date=None):
    sev, conf = conn.execute("SELECT severity, confidence FROM issues WHERE id=?", (issue_id,)).fetchone()
    conn.execute("INSERT INTO issue_events (issue_id, date, kind, severity, confidence, why) VALUES (?,?,?,?,?,?)",
                 (issue_id, date or datetime.date.today().isoformat(), kind, sev, conf, why))


def category_id(conn, value):
    """A category by id or by name_en → its id (exits on an unknown one)."""
    row = conn.execute("SELECT id FROM categories WHERE id=? OR lower(name_en)=lower(?)",
                       (value if str(value).isdigit() else -1, str(value))).fetchone()
    if not row:
        sys.exit(f"unknown category {value!r} — `sqlite3 labs.db 'SELECT id, name_en FROM categories'`")
    return row[0]


# ── target arithmetic: ("min", x) want ≥ x · ("max", x) want ≤ x · ("range", lo, hi) ──

def first_num(s):
    """The value at the START of a stored result ("<35" → 35.0, a censored bound); None for text."""
    m = re.match(r"\s*[<>≤≥~]*\s*(-?\d+(?:\.\d+)?)", (s or "").replace(",", "."))
    return float(m.group(1)) if m else None


def target(low, high):
    if low is not None and high is not None:
        return ("range", float(low), float(high))
    if low is not None:
        return ("min", float(low))
    if high is not None:
        return ("max", float(high))
    return None


def target_met(x, t):
    return x >= t[1] if t[0] == "min" else x <= t[1] if t[0] == "max" else t[1] <= x <= t[2]


def toward_target(latest, prior, t):
    """'improving' | 'worsening' | 'flat' from the most recent move (latest vs prior draw)."""
    if prior is None or abs(latest - prior) < 1e-9:
        return "flat"
    want_up = t[0] == "min" or (t[0] == "range" and latest < t[1])
    return "improving" if (latest > prior) == want_up else "worsening"


def fmt_target(t):
    return {"min": f"≥{t[1]:g}", "max": f"≤{t[1]:g}"}.get(t[0]) or f"{t[1]:g}–{t[2]:g}"


# ── derived state: computed on every read, never stored ──

def marker_state(conn, m, cvi, today):
    pts = [(d, v, raw, lab) for d, raw, lab in conn.execute(
        "SELECT date, value, lab FROM test_results WHERE biomarker_id=? ORDER BY date", (m["biomarker_id"],))
        if (v := first_num(raw)) is not None]
    t = target(m["target_low"], m["target_high"])
    out = {"biomarker_id": m["biomarker_id"], "name": m["name_en"], "specimen": m["specimen_en"],
           "unit": m["unit"], "target": fmt_target(t) if t else None, "note": m["note"], "n": len(pts),
           "floor": bool(cvi)}
    if not pts:
        return dict(out, state="no data")
    (date, latest, raw, lab), p = pts[-1], (pts[-2] if len(pts) > 1 else None)
    day = lambda d: datetime.date.fromisoformat(d[:10])      # noqa: E731
    out.update(latest=latest, text=raw.strip(), date=date[:10], stale=(today - day(date)).days > STALE_DAYS)
    # a direction needs two comparable draws; otherwise the honest word is "off target", with the reason
    # (an unknown laboratory on either draw is not counted as a change)
    no_trend = ("stale" if out["stale"] else "one draw" if p is None
                else "long gap" if (day(date) - day(p[0])).days > STALE_DAYS
                else "lab changed" if lab and p[3] and lab != p[3] else None)
    if t is None:
        state = "context"
    elif target_met(latest, t):
        state = "on target"
    elif no_trend:
        state, out["no_trend"] = "off target", no_trend
    else:
        state = toward_target(latest, p[1], t)
        if state != "flat" and cvi and not rcv.beyond_noise(cvi, p[1], latest):
            state, out["within_noise"] = "flat", True        # a move inside the analyte's own noise
    return dict(out, state=state)


def rollup(markers):
    """One word for the finding, from its targeted markers; worst first."""
    states = [m["state"] for m in markers if m["target"]]
    if not states:
        return "no target"      # judged by imaging, a referral or a symptom, not by a number here
    for s, word in (("worsening", "worsening"), ("off target", "off target"), ("flat", "not moving"),
                    ("improving", "improving"), ("no data", "no data")):
        if s in states:
            return word
    return "on target"


def view(conn, today=None):
    """Every finding with its computed state — what the report, the viewer and the export read."""
    today = today or datetime.date.today()
    cvis = rcv.load(conn)
    try:
        import screening_calendar
        cal = {r["name"]: r for r in screening_calendar.compute(conn, today)}
    except (sqlite3.OperationalError, AttributeError):
        cal = {}
    out = []
    for i in _rows(conn, "SELECT i.*, c.name_en AS category FROM issues i JOIN categories c "
                         "ON c.id = i.category_id ORDER BY i.severity DESC, i.id"):
        ms = _rows(conn, "SELECT m.*, b.name_en, b.specimen_en, b.unit FROM issue_markers m "
                         "JOIN biomarkers b ON b.id = m.biomarker_id WHERE m.issue_id=? "
                         "ORDER BY (m.target_low IS NULL AND m.target_high IS NULL), b.name_en", (i["id"],))
        i["markers"] = [marker_state(conn, m, cvis.get(m["biomarker_id"]), today) for m in ms]
        i["state"] = rollup(i["markers"]) if i["status"] in LIVE else i["status"]
        i["prescribed"] = _rows(conn, "SELECT a.kind, a.name, a.detail, a.why, a.biomarker_id, b.name_en AS marker "
                                      "FROM issue_actions a LEFT JOIN biomarkers b ON b.id = a.biomarker_id "
                                      "WHERE a.issue_id=? ORDER BY a.kind, a.name", (i["id"],))
        i["screenings"] = [{"name": n, "status": cal[n]["status"] if n in cal else "",
                            "due": cal[n]["due"].isoformat() if n in cal and cal[n]["due"] else None}
                           for (n,) in conn.execute("SELECT name FROM screenings WHERE issue_id=? ORDER BY name",
                                                    (i["id"],))]
        i["events"] = _rows(conn, "SELECT date, kind, severity, confidence, why FROM issue_events "
                                  "WHERE issue_id=? ORDER BY date, id", (i["id"],))
        out.append(i)
    return out


# ── writes: every one leaves an event ──

def set_fields(conn, key, changes, why, today=None):
    _need_why(why)
    today = today or datetime.date.today().isoformat()
    row = _issue(conn, key)
    if "category" in changes:
        changes["category_id"] = category_id(conn, changes.pop("category"))
    bad = set(changes) - set(FIELDS)
    if bad:
        sys.exit(f"cannot set {sorted(bad)} — settable: category, {', '.join(FIELDS[1:])}")
    for f in ("severity", "confidence", "category_id"):
        if f in changes:
            changes[f] = None if str(changes[f]).lower() in ("none", "") else int(changes[f])
    changed = {f: v for f, v in changes.items() if v != row[f]}
    if not changed:
        sys.exit("nothing to change")
    status = changed.get("status", row["status"])
    if status not in STATUSES:
        sys.exit(f"status must be one of {STATUSES}")
    if "status" in changed:
        changed["closed_at"] = None if status in LIVE else today
        kind = status if status not in LIVE else ("reopened" if row["status"] not in LIVE else "status")
    else:
        kind = "rescored" if {"severity", "confidence"} & set(changed) else "edited"
    conn.execute(f"UPDATE issues SET {', '.join(f'{f}=?' for f in changed)} WHERE id=?",
                 (*changed.values(), row["id"]))
    was = "; ".join(f"{f} {row[f]!r} → {v!r}" for f, v in changed.items()
                    if f in ("severity", "confidence", "status", "category_id"))
    _event(conn, row["id"], kind, f"{why} [{was}]" if was else why, today)
    conn.commit()
    return kind


def set_marker(conn, key, biomarker_id, low, high, why, note=None, today=None):
    _need_why(why)
    row = _issue(conn, key)
    bm = conn.execute("SELECT name_en, specimen_en FROM biomarkers WHERE id=?", (biomarker_id,)).fetchone()
    if not bm:
        sys.exit(f"no biomarker with id {biomarker_id}")
    old = conn.execute("SELECT target_low, target_high FROM issue_markers WHERE issue_id=? AND biomarker_id=?",
                       (row["id"], biomarker_id)).fetchone()
    conn.execute("INSERT INTO issue_markers (issue_id, biomarker_id, target_low, target_high, note) "
                 "VALUES (?,?,?,?,?) ON CONFLICT(issue_id, biomarker_id) DO UPDATE SET "
                 "target_low=excluded.target_low, target_high=excluded.target_high, "
                 "note=COALESCE(excluded.note, note)", (row["id"], biomarker_id, low, high, note))
    fmt = lambda t: fmt_target(t) if t else "no target"     # noqa: E731
    _event(conn, row["id"], "retargeted",
           f"{why} [{bm[0]} ({bm[1]}): {fmt(target(*old)) if old else 'not linked'} → {fmt(target(low, high))}]",
           today)
    conn.commit()


def _tag(op, kind, name, on):
    return f"[{op}{kind} {name}{f' @{on}' if on is not None else ''}]"


def set_action(conn, key, kind, name, why, on=None, detail=None, today=None, commit=True):
    """Add or change one prescribed action → 'added' | 'changed' | 'unchanged'."""
    _need_why(why)
    if kind not in ACTION_KINDS:
        sys.exit(f"kind must be one of {', '.join(ACTION_KINDS)}")
    name = (name or "").strip()
    if not name or set(name) & set("[]@"):
        sys.exit(f"{name!r}: name an action plainly (no [ ] @) — a dose goes in the routine, not here")
    row = _issue(conn, key)
    if on is not None and not conn.execute("SELECT 1 FROM issue_markers WHERE issue_id=? AND biomarker_id=?",
                                           (row["id"], on)).fetchone():
        sys.exit(f"{key}: biomarker {on} is not one of this finding's markers — link it with --marker first")
    old = conn.execute("SELECT detail, why FROM issue_actions WHERE issue_id=? AND kind=? AND name=? "
                       "AND biomarker_id IS ?", (row["id"], kind, name, on)).fetchone()
    if old == (detail, why):
        return "unchanged"
    if old:
        conn.execute("UPDATE issue_actions SET detail=?, why=? WHERE issue_id=? AND kind=? AND name=? "
                     "AND biomarker_id IS ?", (detail, why, row["id"], kind, name, on))
    else:
        conn.execute("INSERT INTO issue_actions (issue_id, kind, name, biomarker_id, detail, why) "
                     "VALUES (?,?,?,?,?,?)", (row["id"], kind, name, on, detail, why))
    _event(conn, row["id"], "action", f"{why} {_tag('~' if old else '+', kind, name, on)}", today)
    if commit:
        conn.commit()
    return "changed" if old else "added"


def drop_action(conn, key, kind, name, why, on=None, today=None):
    _need_why(why)
    row = _issue(conn, key)
    n = conn.execute("DELETE FROM issue_actions WHERE issue_id=? AND kind=? AND name=? AND biomarker_id IS ?",
                     (row["id"], kind, name, on)).rowcount
    if not n:
        sys.exit(f"{key}: no {kind} action named {name!r}" + (f" on biomarker {on}" if on is not None else ""))
    _event(conn, row["id"], "action", f"{why} {_tag('-', kind, name, on)}", today)
    conn.commit()


def load(conn, entries, today=None):
    """Create findings from a JSON list (or a path to one). A key that exists is left alone — an
    existing finding changes through --set / --marker / --action, where the reason is recorded — but
    its `actions` are applied either way (each carries its own `why`). → (created, skipped, acted).
    `opened_at` is when the finding dates from; its first event is dated the day it was scored."""
    if not isinstance(entries, list):
        entries = json.loads(Path(entries).read_text())
    today = today or datetime.date.today().isoformat()
    created, skipped, acted = [], [], 0
    for e in entries:
        if conn.execute("SELECT 1 FROM issues WHERE key=?", (e["key"],)).fetchone():
            skipped.append(e["key"])
        else:
            status, closed = e.get("status", "open"), e.get("closed_at")
            assert (status in LIVE) == (closed is None), f"{e['key']}: closed_at must be set exactly when closed"
            iid = conn.execute(
                "INSERT INTO issues (key, category_id, title, summary, severity, confidence, status, done_when, "
                "opened_at, closed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (e["key"], category_id(conn, e["category"]), e["title"], e["summary"], e["severity"],
                 e.get("confidence"), status, e.get("done_when"), e.get("opened_at", today), closed)).lastrowid
            for m in e.get("markers", []):
                name = conn.execute("SELECT name_en FROM biomarkers WHERE id=?", (m["id"],)).fetchone()
                # the name beside the id guards against a mistyped id
                assert name and m["name"].lower() in name[0].lower(), \
                    f"{e['key']}: biomarker {m['id']} is {name and name[0]!r}, not {m['name']!r}"
                conn.execute("INSERT INTO issue_markers VALUES (?,?,?,?,?)",
                             (iid, m["id"], m.get("low"), m.get("high"), m.get("note")))
            for name in e.get("screenings", []):
                n = conn.execute("UPDATE screenings SET issue_id=? WHERE name=?", (iid, name)).rowcount
                assert n == 1, f"{e['key']}: no screenings row named {name!r}"
            _event(conn, iid, "opened" if status in LIVE else status, e["why"], today)
            created.append(e["key"])
        acted += sum(set_action(conn, e["key"], a["kind"], a["name"], a["why"], a.get("marker"), a.get("detail"),
                                today, commit=False) != "unchanged" for a in e.get("actions", []))
    conn.commit()
    return created, skipped, acted


# ── the gate ──

def check(conn):
    """CONSISTENCY → violation strings (empty = consistent): every score, status and action has its
    event; no lab value sits in a finding's text or an action; no item is both taken and avoided."""
    bad = []
    for i in _rows(conn, "SELECT * FROM issues ORDER BY id"):
        k = i["key"]
        if (i["status"] in LIVE) != (i["closed_at"] is None):
            bad.append(f"{k}: status {i['status']} with closed_at {i['closed_at']!r}")
        last = conn.execute("SELECT severity, confidence FROM issue_events WHERE issue_id=? "
                            "ORDER BY date DESC, id DESC LIMIT 1", (i["id"],)).fetchone()
        if not last:
            bad.append(f"{k}: no event — a finding enters through --load, never a bare INSERT")
        elif last != (i["severity"], i["confidence"]):
            bad.append(f"{k}: scores {i['severity']}/{i['confidence']} but the last event recorded "
                       f"{last[0]}/{last[1]} — changed without --set, so the reason is missing")
        for f in ("title", "summary", "done_when"):
            if i[f] and LAB_VALUE.search(i[f]):
                bad.append(f"{k}: {f} quotes a lab value — values live in test_results, targets in issue_markers")
    rows = _rows(conn, "SELECT a.*, i.key, i.status FROM issue_actions a JOIN issues i ON i.id = a.issue_id")
    keys = dict(conn.execute("SELECT id, key FROM issues"))
    last = {}                                   # replay the action events: what the history says exists
    for iid, why in conn.execute("SELECT issue_id, why FROM issue_events WHERE kind='action' ORDER BY date, id"):
        if m := ACTION_TAG.search(why):
            last[(iid, m[2], m[3], int(m[4]) if m[4] else None)] = m[1]
    have = set()
    for a in rows:
        k = (a["issue_id"], a["kind"], a["name"], a["biomarker_id"])
        have.add(k)
        if last.get(k) not in ("+", "~"):
            bad.append(f"{a['key']}: action {a['kind']} {a['name']!r} is not in the history — written without "
                       "--action / --load, so its reason is missing")
        if any(a[f] and LAB_VALUE.search(a[f]) for f in ("name", "detail", "why")):
            bad.append(f"{a['key']}: action {a['kind']} {a['name']!r} quotes a lab value")
    for (iid, kind, name, on), op in last.items():
        if op in "+~" and (iid, kind, name, on) not in have and iid in keys:
            bad.append(f"{keys[iid]}: action {kind} {name!r} is in the history but not in the table — "
                       "removed without --drop-action")
    live = [a for a in rows if a["status"] in LIVE]
    for t in (a for a in live if a["kind"] in ("take", "conditional")):
        for v in (a for a in live if a["kind"] == "avoid" and a["name"].casefold() == t["name"].casefold()):
            bad.append(f"{t['key']} prescribes {t['name']!r} ({t['kind']}) while {v['key']} says avoid it — "
                       "one of them is wrong")
    return bad


MARK = {"worsening": "⚠", "off target": "○", "not moving": "•", "improving": "↗", "on target": "✓"}


def report(conn):
    rows = view(conn)
    if not rows:
        print("no findings yet — write them as JSON under inputs/ and `python3 issues.py --load FILE`")
        return
    print(f"{'sev':>3} {'conf':>4}  {'status':<9}{'state':<13}finding")
    print("-" * 78)
    for i in rows:
        conf = "–" if i["confidence"] is None else i["confidence"]
        print(f"{i['severity']:>3} {conf:>4}  {i['status']:<9}{MARK.get(i['state'], ' ')} {i['state']:<11}"
              f"{i['key']} — {i['title']}")
        if i["status"] not in LIVE:
            continue
        for m in i["markers"]:
            if m["target"] and m["state"] != "on target":
                val = f"{m['text']} on {m['date']}" if "latest" in m else "never measured"
                flags = "".join(f" [{f}]" for f in (m.get("stale") and "stale",
                                                    m.get("within_noise") and "within noise",
                                                    m.get("no_trend") not in (None, "stale") and m["no_trend"]) if f)
                print(f"{'':22}{m['state']:<10} {m['name']}: {val}, target {m['target']}{flags}")
        due = [s["name"] for s in i["screenings"] if s["status"] in ("due", "OVERDUE", "never done")]
        if due:
            print(f"{'':22}to do      " + f"\n{'':33}".join(due))
        if i["prescribed"]:
            print(f"{'':22}prescribed " + " · ".join(dict.fromkeys(f"{a['kind']} {a['name']}" for a in i["prescribed"])))
    live = [i for i in rows if i["status"] in LIVE]
    n = lambda s: sum(i["state"] == s for i in live)      # noqa: E731
    print(f"\n{len(live)} live finding(s): {n('worsening')} worsening · {n('off target')} off target (no comparable "
          f"pair) · {n('not moving')} not moving · {n('improving')} improving · {n('on target')} on target · "
          f"{n('no data')} no data · {n('no target')} with no numeric target.  {len(rows) - len(live)} closed.")
    idle = [i["key"] for i in live if i["state"] in ("worsening", "off target", "not moving") and not i["prescribed"]]
    if idle:
        print(f"{len(idle)} off-target finding(s) with nothing prescribed: {', '.join(idle)}")
    floorless = sorted({m["name"] for i in live for m in i["markers"] if m["target"] and not m["floor"]})
    if floorless:
        print(f"no noise floor on file for {len(floorless)} targeted marker(s) (rcv.py --set): {', '.join(floorless)}")


def show(conn, key):
    i = next((x for x in view(conn) if x["key"] == key), None) or _issue(conn, key)
    print(f"{i['key']} — {i['title']}   [{i['category']}]")
    print(f"severity {i['severity']} · confidence {i['confidence']} · {i['status']} · state: {i['state']}"
          f" · opened {i['opened_at']}" + (f" · closed {i['closed_at']}" if i["closed_at"] else ""))
    print(f"\n{i['summary']}")
    if i["done_when"]:
        print(f"\nDone when: {i['done_when']}")
    if i["markers"]:
        print("\nMarkers:")
    for m in i["markers"]:
        val = f"{m['text']} {m['unit'] or ''} on {m['date']}" if "latest" in m else "never measured"
        print(f"  {m['biomarker_id']:<5}{m['name']} ({m['specimen']}): {val} · target {m['target'] or '—'}"
              f" · {m['state']}" + (" · stale" if m.get("stale") else ""))
    if i["prescribed"]:
        print("\nPrescribed:")
    for a in i["prescribed"]:
        print(f"  {a['kind']:<12}{a['name']}" + (f" ({a['detail']})" if a["detail"] else "")
              + (f" → {a['marker']}" if a["marker"] else "") + f" — {a['why']}")
    if i["screenings"]:
        print("\nCalendar:")
    for s in i["screenings"]:
        print(f"  {s['status']:<12}{s['due'] or '—':<12}{s['name']}")
    print("\nHistory:")
    for e in i["events"]:
        print(f"  {e['date']}  {e['kind']:<10} {e['severity']}/{e['confidence']}  {e['why']}")


def self_check():
    """Invented analytes and findings only — real ones are personal data and never appear in code."""
    c = sqlite3.connect(":memory:")
    c.executescript((ROOT / "schema.sql").read_text())
    c.executescript("""
        INSERT INTO biomarkers (id, name_en, specimen_en, unit) VALUES
            (1, 'Testium', 'Blood', 'u'), (2, 'Fakeose', 'Blood', 'u'), (3, 'Mockium', 'Blood', 'u');
        INSERT INTO test_results (biomarker_id, date, value, lab) VALUES
            (1, '2029-01-10', '8.0', 'Lab A'), (1, '2029-06-10', '6.5', 'Lab A'),
            (2, '2029-01-10', '50', 'Lab A'),  (2, '2029-06-10', '40', 'Lab B'),
            (3, '2027-01-10', '<5', NULL);""")
    today = datetime.date(2029, 7, 1)
    created, _, acted = load(c, [
        {"key": "testium-high", "category": "Lipid Panel", "title": "Testium above target",
         "summary": "Invented finding.", "severity": 40, "confidence": 60, "why": "two draws above",
         "done_when": "two comparable draws on target",
         "markers": [{"id": 1, "name": "Testium", "high": 5}, {"id": 2, "name": "Fakeose", "low": 60},
                     {"id": 3, "name": "Mockium", "low": 10}],
         "actions": [{"kind": "take", "name": "Testiumol", "why": "lowers it", "marker": 1},
                     {"kind": "diet", "name": "Less fakeose", "why": "general"}]}], "2029-07-01")
    assert created == ["testium-high"] and acted == 2
    v = view(c, today)[0]
    st = {m["name"]: m for m in v["markers"]}
    assert st["Testium"]["state"] == "improving", st["Testium"]          # 8.0 → 6.5, toward ≤5
    assert st["Fakeose"]["state"] == "off target" and st["Fakeose"]["no_trend"] == "lab changed"
    assert st["Mockium"]["state"] == "off target" and st["Mockium"]["no_trend"] == "stale"
    assert st["Mockium"]["text"] == "<5" and v["state"] == "off target"
    rcv.record(c, 1, 25.0, "invented source", "2029-07-01")             # a noisy analyte: −19% is noise
    assert next(m for m in view(c, today)[0]["markers"] if m["name"] == "Testium")["within_noise"]
    assert check(c) == [], check(c)
    # every change through the script leaves a reason; a bare UPDATE or INSERT is caught
    set_fields(c, "testium-high", {"severity": 45}, "rescored", "2029-07-02")
    assert check(c) == []
    c.execute("UPDATE issues SET severity = 70")
    c.execute("INSERT INTO issue_actions (issue_id, kind, name, why) VALUES (1, 'avoid', 'Testiumol', 'x')")
    bad = check(c)
    assert any("changed without --set" in b for b in bad) and any("not in the history" in b for b in bad)
    assert any("says avoid it" in b for b in bad), bad
    c.execute("DELETE FROM issue_actions WHERE kind='avoid'")
    c.execute("UPDATE issues SET severity = 45, summary = 'keep it under 5 mmol/L'")
    assert any("quotes a lab value" in b for b in check(c))
    c.execute("UPDATE issues SET summary = 'Invented finding.'")
    drop_action(c, "testium-high", "diet", "Less fakeose", "not needed", today="2029-07-03")
    set_marker(c, "testium-high", 2, 55, None, "retargeted", today="2029-07-03")
    set_fields(c, "testium-high", {"status": "resolved"}, "on target twice", "2029-07-04")
    assert check(c) == [], check(c)
    assert _issue(c, "testium-high")["closed_at"] == "2029-07-04"
    assert fmt_target(target(None, 5)) == "≤5" and toward_target(6, 7, ("range", 1, 5)) == "improving"
    print("self-check OK")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--load", metavar="FILE")
    ap.add_argument("--show", metavar="KEY")
    ap.add_argument("--set", nargs="+", metavar=("KEY", "FIELD=VALUE"))
    ap.add_argument("--marker", nargs=2, metavar=("KEY", "BIOMARKER_ID"))
    ap.add_argument("--low", type=float)
    ap.add_argument("--high", type=float)
    ap.add_argument("--note")
    ap.add_argument("--action", nargs=3, metavar=("KEY", "KIND", "NAME"))
    ap.add_argument("--drop-action", nargs=3, metavar=("KEY", "KIND", "NAME"))
    ap.add_argument("--on", type=int, metavar="BIOMARKER_ID")
    ap.add_argument("--detail")
    ap.add_argument("--why")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.self_check:
        return self_check()
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA foreign_keys = ON")
    from init_db import migrate
    migrate(conn)
    if a.check:
        bad = check(conn)
        print("\n".join(bad) if bad else "OK — every finding, score and action has its reason on file")
        return 1 if bad else 0
    if a.load:
        created, skipped, acted = load(conn, a.load)
        print(f"created {len(created)}: {', '.join(created) or '—'} · left alone {len(skipped)} · "
              f"actions added or changed {acted}")
    elif a.set:
        changes = dict(kv.split("=", 1) for kv in a.set[1:])
        print(set_fields(conn, a.set[0], changes, a.why))
    elif a.marker:
        set_marker(conn, a.marker[0], int(a.marker[1]), a.low, a.high, a.why, a.note)
    elif a.action:
        print(set_action(conn, *a.action, a.why, a.on, a.detail))
    elif a.drop_action:
        drop_action(conn, *a.drop_action, a.why, a.on)
    elif a.show:
        show(conn, a.show)
    else:
        report(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
