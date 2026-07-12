"""
Export a condensed lab-results summary to Markdown for feeding to LLMs.

Reads labs.db and writes labs_summary.md. For each biomarker (grouped by
primary category, listed once), outputs the historical average of all numeric
values plus the latest two values with their dates — instead of the full
time-series produced by export_for_llm.py.
"""
import argparse
import sqlite3
from datetime import date
from pathlib import Path

DB = Path('labs.db')
OUT_STEM = 'labs_summary'


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
    Non-numeric strings (Negative, Positive, обнаружено) return None.
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

    bm_cats = {}  # biomarker_id → sorted [category_id, ...] (primary = lowest)
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

    results = {}  # biomarker_id → [(date, value), ...] ascending by date
    for bid, d, v in cur.execute(
        'SELECT biomarker_id, date, value FROM test_results ORDER BY date'
    ):
        results.setdefault(bid, []).append((d, v))

    stats = cur.execute(
        'SELECT COUNT(*), COUNT(DISTINCT date), MIN(date), MAX(date) FROM test_results'
    ).fetchone()

    con.close()
    return categories, bm_cats, biomarkers, results, stats


def build_markdown(categories, bm_cats, biomarkers, results, stats):
    n_results, n_dates, min_date, max_date = stats
    n_biomarkers = len(results)

    lines = [
        '# Lab Results Summary',
        '',
        f'Source: labs.db · Generated: {date.today().isoformat()}',
        f'Coverage: {n_dates} test dates, {min_date} → {max_date} · '
        f'{n_biomarkers} biomarkers with results · {n_results} results',
        '',
        '> For each biomarker: historical average of all numeric values (n = count '
        'averaged), then the latest two values with dates (most recent first).',
        '> Averages skip non-numeric values (`Negative`, etc.); qualifiers like '
        '`<10` / `47%` / comma-decimals are parsed to their number for averaging '
        'but shown verbatim in the latest values.',
        '> Reference range shown as `ref low–high` (from the lab); omitted when not recorded.',
        '> Each biomarker is listed once, under its primary category.',
        '',
    ]

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

            series = results[bid]
            nums = [n for n in (parse_numeric(v) for _, v in series) if n is not None]
            avg = f'avg {fmt_num(sum(nums) / len(nums))} (n={len(nums)})' if nums else 'avg n/a'

            latest = list(reversed(series[-2:]))  # most recent first
            latest_str = ', '.join(f'{d}={v}' for d, v in latest)

            header = f'- **{bm["name"]}** — {bm["spec"]}, {bm["unit"]}{fmt_ref(bm["ref_low"], bm["ref_high"])}{also}'
            lines.append(header)
            lines.append(f'  - {avg} · latest: {latest_str}')
        lines.append('')

    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description='Export a condensed lab-results summary for LLMs.')
    ap.add_argument('--txt', action='store_true',
                    help='write a .txt file instead of the default .md')
    args = ap.parse_args()

    out = Path(f'{OUT_STEM}.txt' if args.txt else f'{OUT_STEM}.md')
    categories, bm_cats, biomarkers, results, stats = load_data()
    md = build_markdown(categories, bm_cats, biomarkers, results, stats)
    out.write_text(md, encoding='utf-8')
    size_kb = out.stat().st_size / 1024
    print(f'Wrote {out} — {len(results)} biomarkers, {stats[0]} results, {size_kb:.0f} KB')


if __name__ == '__main__':
    main()
