"""
Export all lab results to a single Markdown file for feeding to LLMs.

Reads labs.db and writes labs_export.md — biomarker time-series only
(no genetics, insights, or unified protocol). Long format grouped by
category; each biomarker listed once under its primary category.
"""
import argparse
import sqlite3
from datetime import date
from pathlib import Path

DB = Path('labs.db')
OUT_STEM = 'labs_export'


def fmt_ref(low, high):
    """Render a reference range; omit when neither bound is recorded."""
    if low is None and high is None:
        return ''
    lo = '' if low is None else f'{low:g}' if isinstance(low, float) else str(low)
    hi = '' if high is None else f'{high:g}' if isinstance(high, float) else str(high)
    return f' · ref {lo}–{hi}'


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


def build_markdown(categories, bm_cats, biomarkers, results, stats):
    n_results, n_dates, min_date, max_date = stats
    n_biomarkers = len(results)

    lines = [
        '# Lab Results Export',
        '',
        f'Source: labs.db · Generated: {date.today().isoformat()}',
        f'Coverage: {n_dates} test dates, {min_date} → {max_date} · '
        f'{n_biomarkers} biomarkers with results · {n_results} results',
        '',
        '> Values are verbatim from source labs (TEXT): qualifiers like '
        '`<10`, `Negative`, `47%` are preserved as-is.',
        '> Reference range shown as `ref low–high` (from the lab); omitted when not recorded.',
        '> Each biomarker is listed once, under its primary category. '
        'Date=value pairs are chronological (oldest → newest).',
        '',
    ]

    # Assign each biomarker (that has results) to its primary category section.
    # primary = lowest category_id; biomarkers with no category mapping go to "Uncategorized".
    UNCATEGORIZED = -1
    section = {}  # category_id → [biomarker_id, ...]
    for bid in results:
        cids = bm_cats.get(bid)
        primary = cids[0] if cids else UNCATEGORIZED
        section.setdefault(primary, []).append(bid)

    def cat_name(cid):
        return 'Uncategorized' if cid == UNCATEGORIZED else categories.get(cid, f'Category {cid}')

    for cid in sorted(section, key=lambda c: cat_name(c).lower()):
        lines.append(f'## {cat_name(cid)}')
        lines.append('')
        for bid in sorted(section[cid], key=lambda b: biomarkers[b]['name'].lower()):
            bm = biomarkers[bid]
            others = [cat_name(c) for c in bm_cats.get(bid, []) if c != cid]
            also = f' _(also: {", ".join(others)})_' if others else ''
            header = f'- **{bm["name"]}** — {bm["spec"]}, {bm["unit"]}{fmt_ref(bm["ref_low"], bm["ref_high"])}{also}'
            series = ' · '.join(f'{d}={v}' for d, v in results[bid])
            lines.append(header)
            lines.append(f'  - {series}')
        lines.append('')

    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description='Export all lab results to a single file for LLMs.')
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
