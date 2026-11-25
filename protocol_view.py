#!/usr/bin/env python3
"""The protocol in two halves, as the viewer, master_protocol.md and the gate read it.

  Findings  (issues.py) — each finding carries its own prescription in issue_actions: what to take,
            avoid or change about it, and why. No doses there.
  Routine   (unified_protocol.protocol) — what every finding shares, in five ═══ sections:
            1. SCHEDULE (the doses, written once, as `• Item — dose, timing`), 2. DIET, 3. ACTIVITY,
            4. LIFESTYLE (rules no single finding owns), 5. MEDICINES & GENES (a prescriber sheet).

One fact, one place: a dose lives only in SCHEDULE, a target only in issue_markers, a value only in
test_results, a cadence only in screenings. check_routine() is the gate that keeps it so.

    python3 protocol_view.py --self-check
"""
import re
import sys

ROUTINE = ('SCHEDULE', 'DIET', 'ACTIVITY', 'LIFESTYLE', 'MEDICINES & GENES')
# the action kinds whose rules the viewer lists under each routine section
ROUTINE_KINDS = {'SCHEDULE': ('take', 'conditional', 'avoid'), 'DIET': ('diet',),
                 'ACTIVITY': ('activity',), 'LIFESTYLE': ('lifestyle',), 'MEDICINES & GENES': ('refer',)}
SECTION_RE = re.compile(r'^═{20,}\n(\d)\. ([^\n]+)\n═{20,}\n', re.M)
ITEM_RE = re.compile(r'^• (.+?) — ', re.M)        # a SCHEDULE bullet: `• Item — dose, timing`
GENERAL = ' (general)'                             # a SCHEDULE item no finding prescribes, kept on purpose
# a number in a LAB unit (a dose is in mg / g / µg / IU, never per litre): a result or a target
LAB_VALUE = re.compile(r'\d\s*(?:[mµunp]mol/(?:L|mol)\b|umol/L\b|[mµunp]?g/(?:dL|mL|L)\b|m?I?U/L\b'
                       r'|[×x]\s*10\^?[⁰¹²³⁴⁵⁶⁷⁸⁹\d]+\s*/L)', re.I)
LIVE = ('open', 'watch')


def norm(name):
    return ' '.join(name.replace(GENERAL, '').split()).casefold()


def routine_sections(text):
    """→ (preamble, [(number, title, body)]). Raises ValueError unless the five sections are each
    there once, in order."""
    found = list(SECTION_RE.finditer(text))
    got = [(int(m[1]), m[2].strip()) for m in found]
    if got != list(enumerate(ROUTINE, 1)):
        raise ValueError(f'routine sections are {got or "missing"} — expected '
                         + ', '.join(f'{n}. {t}' for n, t in enumerate(ROUTINE, 1)))
    ends = [m.start() for m in found[1:]] + [len(text)]
    return (text[:found[0].start()].strip('\n'),
            [(n, t, text[m.end():e].strip('\n')) for (n, t), m, e in zip(got, found, ends)])


def schedule_items(text):
    """The item names of 1. SCHEDULE, one per bullet, in order (an item may recur per time slot)."""
    return [m[1].strip() for m in ITEM_RE.finditer(routine_sections(text)[1][0][2])]


def routine_text(conn):
    row = conn.execute('SELECT protocol FROM unified_protocol ORDER BY id DESC LIMIT 1').fetchone()
    return row[0] if row else ''


def live_actions(conn):
    return conn.execute("SELECT a.kind, a.name, a.detail, i.key, i.title FROM issue_actions a "
                        "JOIN issues i ON i.id = a.issue_id WHERE i.status IN ('open','watch') "
                        "ORDER BY i.severity DESC, i.key, a.kind, a.name").fetchall()


def check_routine(conn):
    """CONSISTENCY: the routine against the findings' prescriptions → violation strings.
      ROUTINE  five sections, in order
      ACT1     every take / conditional of a live finding has a SCHEDULE bullet (its dose lives there)
      ACT2     no avoided item has one
      ACT3     every SCHEDULE item is prescribed by a live finding, or tagged `(general)`
      VALUES   the routine quotes no lab value or target (they live in test_results / issue_markers)"""
    text, acts = routine_text(conn), live_actions(conn)
    bad = []
    if not text.strip():
        need = sorted({n for k, n, *_ in acts if k in ('take', 'conditional')})
        return [f'ACT1: no routine yet, but live findings prescribe {", ".join(need)}'] if need else []
    try:
        items = schedule_items(text)
    except ValueError as e:
        return [f'ROUTINE: {e}']
    have = {norm(i) for i in items}
    general = {norm(i) for i in items if i.endswith(GENERAL)}
    prescribed = {norm(n) for k, n, *_ in acts if k in ('take', 'conditional')}
    for kind, name, _, key, _ in acts:
        if kind in ('take', 'conditional') and norm(name) not in have:
            bad.append(f'ACT1: {key} prescribes {name!r} ({kind}) but 1. SCHEDULE has no bullet for it')
        if kind == 'avoid' and norm(name) in have:
            bad.append(f'ACT2: {key} says avoid {name!r}, yet 1. SCHEDULE doses it')
    for item in dict.fromkeys(items):
        if norm(item) not in prescribed and norm(item) not in general:
            bad.append(f'ACT3: SCHEDULE item {item!r} is prescribed by no live finding — '
                       f'add the action to a finding, or tag it `{GENERAL.strip()}`')
    for m in LAB_VALUE.finditer(text):
        line = text[text.rfind('\n', 0, m.start()) + 1:text.find('\n', m.end()) % (len(text) + 1)]
        bad.append(f'VALUES: the routine quotes a lab value — {line.strip()[:90]!r}')
    return bad


def build_protocol_view(issues, categories):
    """`issues` is issues.view(conn), `categories` {id: name} → the categories that hold a finding,
    most severe first; a category's scores are those of its most severe live finding."""
    groups = []
    for cid, title in categories.items():
        mine = [i for i in issues if i['category_id'] == cid]
        if not mine:
            continue
        live = [i for i in mine if i['status'] in LIVE]
        lead = max(live, key=lambda i: i['severity'], default=None)
        groups.append(dict(id=cid, title=title, issues=mine,
                           severity=lead['severity'] if lead else 0,
                           confidence=lead['confidence'] if lead else None,
                           description=' · '.join(i['title'] for i in live)))
    groups.sort(key=lambda g: -g['severity'])            # stable: category order breaks ties
    return {'groups': groups}


def routine_data(conn):
    """The routine for the viewer: its sections, the rules the live findings add to each, and which
    findings prescribe each SCHEDULE item. None when there is no routine; {'error'} when malformed."""
    text = routine_text(conn)
    if not text.strip():
        return None
    try:
        preamble, sections = routine_sections(text)
    except ValueError as e:
        return {'error': str(e), 'text': text}
    acts = live_actions(conn)
    links = {}
    for item in schedule_items(text):
        links[item] = [{'key': key, 'title': title} for kind, name, _, key, title in acts
                       if kind in ('take', 'conditional') and norm(name) == norm(item)]
    return {'preamble': preamble, 'links': links, 'sections': [
        {'n': n, 'title': t, 'body': body,
         'rules': [{'kind': kind, 'name': name, 'detail': detail, 'key': key, 'issue': title}
                   for kind, name, detail, key, title in acts if kind in ROUTINE_KINDS[t]]}
        for n, t, body in sections]}


KIND_LABEL = {'take': 'Take', 'conditional': 'If triggered', 'avoid': 'Avoid', 'diet': 'Diet',
              'activity': 'Activity', 'lifestyle': 'Lifestyle', 'refer': 'Refer / tell'}


def protocol_md(view, routine, stamp):
    """master_protocol.md: the live findings with their prescriptions, then the routine verbatim."""
    out = [f'# Protocol\n\n_Generated {stamp} from labs.db — findings first, then the daily routine._\n']
    for g in view['groups']:
        live = [i for i in g['issues'] if i['status'] in LIVE]
        if not live:
            continue
        out.append(f'## {g["title"]}\n')
        for i in live:
            out.append(f'### {i["title"]} — {i["state"]} (severity {i["severity"]}, confidence '
                       f'{i["confidence"] if i["confidence"] is not None else "–"}, {i["status"]})\n')
            out.append(i['summary'] + '\n')
            for m in i['markers']:
                val = f'{m["text"]} {m["unit"] or ""} on {m["date"]}' if 'latest' in m else 'never measured'
                out.append(f'- {m["name"]}: {val.strip()} · target {m["target"] or "—"} · {m["state"]}')
            if i['done_when']:
                out.append(f'\n**Done when:** {i["done_when"]}')
            for p in i['prescribed']:
                out.append(f'- **{KIND_LABEL[p["kind"]]}** {p["name"]}'
                           + (f' — {p["detail"]}' if p['detail'] else '') + f' _({p["why"]})_')
            out.append('')
    if routine and 'error' not in routine:
        text = routine['preamble'] + '\n\n' + '\n\n'.join(
            f'{"═" * 40}\n{s["n"]}. {s["title"]}\n{"═" * 40}\n{s["body"]}' for s in routine['sections'])
        out.append(f'## Daily routine\n\n```\n{text.strip()}\n```\n')
    return '\n'.join(out)


# ── self-check: invented items and findings only ──
ROUTINE_FIXTURE = '\n'.join(['DAILY ROUTINE', 'Doses live here.', ''] + [
    f'{"═" * 30}\n{n}. {t}\n{"═" * 30}' + ('\n─── BREAKFAST ───\n• Testiumol — 5 mg, with food\n'
                                          '• Fakeine (general) — 1 g\n' if t == 'SCHEDULE' else '\n• a rule\n')
    for n, t in enumerate(ROUTINE, 1)])


def self_check():
    import sqlite3
    pre, secs = routine_sections(ROUTINE_FIXTURE)
    assert pre == 'DAILY ROUTINE\nDoses live here.' and [t for _, t, _ in secs] == list(ROUTINE)
    assert schedule_items(ROUTINE_FIXTURE) == ['Testiumol', 'Fakeine (general)']
    for broken in ('x', ROUTINE_FIXTURE.replace('2. DIET', '2. FOOD')):
        try:
            routine_sections(broken)
            raise AssertionError('a routine without its five sections in order must fail')
        except ValueError:
            pass
    c = sqlite3.connect(':memory:')
    c.executescript("""
        CREATE TABLE unified_protocol (id INTEGER PRIMARY KEY, protocol TEXT);
        CREATE TABLE issues (id INTEGER PRIMARY KEY, key TEXT, title TEXT, severity INT, status TEXT);
        CREATE TABLE issue_actions (issue_id INT, kind TEXT, name TEXT, detail TEXT);
        INSERT INTO issues VALUES (1, 'testium-high', 'Testium high', 40, 'open'), (2, 'old', 'Old', 10, 'resolved');
        INSERT INTO issue_actions VALUES (1, 'take', 'Testiumol', NULL), (1, 'diet', 'Less fakeose', NULL),
                                         (2, 'take', 'Gone item', NULL);""")
    c.execute('INSERT INTO unified_protocol (protocol) VALUES (?)', (ROUTINE_FIXTURE,))
    assert check_routine(c) == [], check_routine(c)
    c.execute("INSERT INTO issue_actions VALUES (1, 'take', 'Mockium', NULL), (1, 'avoid', 'Fakeine', NULL)")
    c.execute('UPDATE unified_protocol SET protocol = ?', (ROUTINE_FIXTURE.replace(
        '• Fakeine (general) — 1 g', '• Fakeine (general) — 1 g\n• Stray — 2 g\nkeep Testium under 5 mmol/L'),))
    bad = check_routine(c)
    assert sorted(b.split(':')[0] for b in bad) == ['ACT1', 'ACT2', 'ACT3', 'VALUES'], bad
    assert not LAB_VALUE.search('Testiumol — 2000 IU, 5 mg, 1 g/day') and LAB_VALUE.search('a 4.0 ×10⁹/L count')
    r = routine_data(c)
    assert r['links']['Testiumol'] == [{'key': 'testium-high', 'title': 'Testium high'}]
    assert [x['name'] for x in r['sections'][1]['rules']] == ['Less fakeose']
    fake = [dict(category_id=3, title='Testium high', severity=40, confidence=30, status='open'),
            dict(category_id=3, title='Testium gone', severity=90, confidence=90, status='resolved'),
            dict(category_id=4, title='Fakeose low', severity=40, confidence=55, status='watch'),
            dict(category_id=2, title='Mockium low', severity=25, confidence=50, status='open')]
    groups = build_protocol_view(fake, {2: 'B', 3: 'C', 4: 'D', 5: 'E'})['groups']
    assert [g['id'] for g in groups] == [3, 4, 2] and groups[0]['description'] == 'Testium high', groups
    print('self-check OK')


if __name__ == '__main__':
    if '--self-check' in sys.argv:
        self_check()
    else:
        print(__doc__)
