"""
Export lab results to a single Markdown file for feeding to LLMs.

Reads labs.db and writes labs_export.md — biomarker time-series only (no
genetics, insights, or unified protocol). Long format grouped by category;
each biomarker listed once under its primary category.

With --summary, writes labs_summary.md instead: per biomarker, the historical
average of all numeric values plus the latest two values with their dates,
rather than the full series.

    python3 export_for_llm.py              # full time-series → labs_export.md
    python3 export_for_llm.py --summary    # condensed      → labs_summary.md
    python3 export_for_llm.py --self-check
"""
import argparse
import sqlite3
from datetime import date
from pathlib import Path
import os

os.umask(0o077)  # PHI: files this script writes stay owner-only

DB = Path('labs.db')


def fmt_ref(low, high):
    """Render a reference range; omit when neither bound is recorded."""
    if low is None and high is None:
        return ''
    lo = '' if low is None else f'{low:g}' if isinstance(low, float) else str(low)
    hi = '' if high is None else f'{high:g}' if isinstance(high, float) else str(high)
    return f' · ref {lo}–{hi}'


def parse_numeric(value):
    """Best-effort parse of a TEXT lab value to float, else None.

    Handles qualifiers (<, >, ≤, ≥), trailing %, and comma decimals (0,29).
    Non-numeric strings (Negative, Positive, Detected) return None.
    """
    if value is None:
        return None
    s = value.strip().lstrip('<>≤≥').rstrip('%').strip().replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return None


def fmt_num(x):
    """Round an average to 4 decimals and strip trailing zeros."""
    return f'{round(x, 4):g}'


def load_data():
    con = sqlite3.connect(DB)
    cur = con.cursor()

    categories = dict(cur.execute('SELECT id, name_en FROM categories').fetchall())

    # biomarker_id → sorted list of category_ids (primary = lowest id)
    bm_cats = {}
    for bid, cid in cur.execute('SELECT biomarker_id, category_id FROM biomarker_categories'):
        bm_cats.setdefault(bid, []).append(cid)
    for bid in bm_cats:
        bm_cats[bid].sort()

    biomarkers = {
        b[0]: {'name': b[1], 'spec': b[3] or '', 'unit': b[4] or '',
               'ref_low': b[5], 'ref_high': b[6]}
        for b in cur.execute(
            'SELECT id, name_en, name_ru, specimen_en, unit, ref_low, ref_high '
            'FROM biomarkers'
        )
    }

    # biomarker_id → [(date, value), ...] sorted by date ascending
    results = {}
    for bid, d, v in cur.execute(
        'SELECT biomarker_id, date, value FROM test_results ORDER BY date'
    ):
        results.setdefault(bid, []).append((d, v))

    stats = cur.execute(
        'SELECT COUNT(*), COUNT(DISTINCT date), MIN(date), MAX(date) FROM test_results'
    ).fetchone()

    con.close()
    return categories, bm_cats, biomarkers, results, stats


def detail_line(series, summary):
    """The one line rendered under each biomarker — the only mode-dependent content."""
    if not summary:
        return ' · '.join(f'{d}={v}' for d, v in series)

    nums = [n for n in (parse_numeric(v) for _, v in series) if n is not None]
    avg = f'avg {fmt_num(sum(nums) / len(nums))} (n={len(nums)})' if nums else 'avg n/a'
    latest = ', '.join(f'{d}={v}' for d, v in reversed(series[-2:]))  # most recent first
    return f'{avg} · latest: {latest}'


def build_markdown(categories, bm_cats, biomarkers, results, stats, summary=False):
    n_results, n_dates, min_date, max_date = stats

    preamble = [
        '> For each biomarker: historical average of all numeric values (n = count '
        'averaged), then the latest two values with dates (most recent first).',
        '> Averages skip non-numeric values (`Negative`, etc.); qualifiers like '
        '`<10` / `47%` / comma-decimals are parsed to their number for averaging '
        'but shown verbatim in the latest values.',
    ] if summary else [
        '> Values are verbatim from source labs (TEXT): qualifiers like '
        '`<10`, `Negative`, `47%` are preserved as-is.',
    ]
    # Full mode appends the ordering note to this line rather than adding its own.
    listed_once = '> Each biomarker is listed once, under its primary category.'
    if not summary:
        listed_once += ' Date=value pairs are chronological (oldest → newest).'

    lines = [
        f'# Lab Results {"Summary" if summary else "Export"}',
        '',
        f'Source: labs.db · Generated: {date.today().isoformat()}',
        f'Coverage: {n_dates} test dates, {min_date} → {max_date} · '
        f'{len(results)} biomarkers with results · {n_results} results',
        '',
        *preamble,
        '> Reference range shown as `ref low–high` (from the lab); omitted when not recorded.',
        listed_once,
        '',
    ]

    # Assign each biomarker (that has results) to its primary category section.
    # primary = lowest category_id; biomarkers with no category mapping go to "Uncategorized".
    UNCATEGORIZED = -1
    section = {}  # category_id → [biomarker_id, ...]
    for bid in results:
        cids = bm_cats.get(bid)
        section.setdefault(cids[0] if cids else UNCATEGORIZED, []).append(bid)

    def cat_name(cid):
        return 'Uncategorized' if cid == UNCATEGORIZED else categories.get(cid, f'Category {cid}')

    for cid in sorted(section, key=lambda c: cat_name(c).lower()):
        lines.append(f'## {cat_name(cid)}')
        lines.append('')
        for bid in sorted(section[cid], key=lambda b: biomarkers[b]['name'].lower()):
            bm = biomarkers[bid]
            others = [cat_name(c) for c in bm_cats.get(bid, []) if c != cid]
            also = f' _(also: {", ".join(others)})_' if others else ''
            lines.append(
                f'- **{bm["name"]}** — {bm["spec"]}, {bm["unit"]}'
                f'{fmt_ref(bm["ref_low"], bm["ref_high"])}{also}'
            )
            lines.append(f'  - {detail_line(results[bid], summary)}')
        lines.append('')

    return '\n'.join(lines)


def self_check():
    assert parse_numeric('<10') == 10.0
    assert parse_numeric('≥750') == 750.0
    assert parse_numeric('47%') == 47.0
    assert parse_numeric('0,29') == 0.29
    assert parse_numeric('Negative') is None
    assert parse_numeric('Detected') is None
    assert parse_numeric(None) is None
    assert fmt_num(1.23456789) == '1.2346'
    assert fmt_num(5.0) == '5'
    assert fmt_ref(None, None) == ''
    assert fmt_ref(3.5, 7.0) == ' · ref 3.5–7'
    assert fmt_ref(None, 7.0) == ' · ref –7'

    series = [('2026-01-01', '10'), ('2026-02-01', 'Negative'), ('2026-03-01', '20')]
    assert detail_line(series, summary=False) == \
        '2026-01-01=10 · 2026-02-01=Negative · 2026-03-01=20'
    # average over the 2 numeric values; latest two shown newest-first, verbatim
    assert detail_line(series, summary=True) == \
        'avg 15 (n=2) · latest: 2026-03-01=20, 2026-02-01=Negative'
    assert detail_line([('2026-01-01', 'Negative')], summary=True) == \
        'avg n/a · latest: 2026-01-01=Negative'
    print('self-check OK')


def main():
    ap = argparse.ArgumentParser(description='Export lab results to a single file for LLMs.')
    ap.add_argument('--summary', action='store_true',
                    help='condensed export (avg + latest two) to labs_summary.md '
                         'instead of the full time-series to labs_export.md')
    ap.add_argument('--self-check', action='store_true', help='run self-tests and exit')
    args = ap.parse_args()

    if args.self_check:
        self_check()
        return

    out = Path('labs_summary.md' if args.summary else 'labs_export.md')
    categories, bm_cats, biomarkers, results, stats = load_data()
    md = build_markdown(categories, bm_cats, biomarkers, results, stats, summary=args.summary)
    out.write_text(md, encoding='utf-8')
    size_kb = out.stat().st_size / 1024
    print(f'Wrote {out} — {len(results)} biomarkers, {stats[0]} results, {size_kb:.0f} KB')


if __name__ == '__main__':
    main()
