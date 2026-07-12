"""
Generate viewer.html — a standalone lab results viewer from labs.db.
"""
import sqlite3
import json
import sys
from pathlib import Path

DB = Path('labs.db')
OUT = Path('viewer.html')
PROTOCOL_OUT = Path('master_protocol.md')

DOMAIN_HEADINGS = [
    ('supplements',      '═══ SUPPLEMENTS ═══'),
    ('diet',             '═══ DIET ═══'),
    ('activity',         '═══ ACTIVITY ═══'),
    ('lifestyle',        '═══ LIFESTYLE ═══'),
    ('checkup_schedule', '═══ CHECKUP SCHEDULE ═══'),
]


def compose_protocol(supplements, diet, activity, lifestyle, checkups):
    """Concatenate non-null domain fields with ═══ headers. Returns None if all empty."""
    parts = []
    values = {'supplements': supplements, 'diet': diet, 'activity': activity,
              'lifestyle': lifestyle, 'checkup_schedule': checkups}
    for key, heading in DOMAIN_HEADINGS:
        v = values[key]
        if v and v.strip():
            parts.append(f'{heading}\n\n{v.strip()}')
    return '\n\n'.join(parts) if parts else None


def load_data():
    con = sqlite3.connect(DB)
    cur = con.cursor()

    categories = cur.execute(
        'SELECT id, name_en, name_ru FROM categories ORDER BY id'
    ).fetchall()

    biomarkers = cur.execute(
        'SELECT id, name_en, name_ru, specimen_en, unit, opt_low, opt_high, '
        'pers_bands, ref_low, ref_high FROM biomarkers ORDER BY id'
    ).fetchall()

    bm_cats = {}  # biomarker_id → [category_id, ...]
    for bid, cid in cur.execute('SELECT biomarker_id, category_id FROM biomarker_categories'):
        bm_cats.setdefault(bid, []).append(cid)

    results = cur.execute(
        'SELECT biomarker_id, date, value FROM test_results ORDER BY date'
    ).fetchall()

    # Insights — the protocol is composed from the structured domain columns
    # (supplements/diet/activity/lifestyle/checkup_schedule).
    insights = {}
    for row in cur.execute(
        'SELECT category_id, insight, insight_dna, '
        'supplements, diet, activity, lifestyle, checkup_schedule, concordance '
        'FROM category_insights'
    ):
        (cid, text, dna, supplements, diet,
         activity, lifestyle, checkups, concordance) = row
        protocol = compose_protocol(supplements, diet, activity, lifestyle, checkups)
        insights[cid] = {
            'text': text, 'dna': dna,
            'protocol': protocol, 'concordance': concordance,
        }

    # variants (genotypes matched from the user's DNA file by import_dna.py)
    rsids = {}  # category_id → [{rsid, gene, relevance, genotype, zygosity}, ...]
    for row in cur.execute(
        'SELECT category_id, rsid, gene, relevance, genotype, zygosity '
        'FROM variants ORDER BY category_id, gene'
    ):
        rsids.setdefault(row[0], []).append({
            'rsid': row[1], 'gene': row[2] or '', 'rel': row[3] or '',
            'gt': row[4] or '', 'zyg': row[5] or '',
        })

    # Unified protocol
    unified = ''
    try:
        row = cur.execute('SELECT protocol FROM unified_protocol ORDER BY id DESC LIMIT 1').fetchone()
        if row:
            unified = row[0]
    except Exception:
        pass

    # Screening calendar (table may not exist if screening_calendar.py --init hasn't run)
    screenings = []
    try:
        screenings = cur.execute(
            'SELECT name, domain, cadence_months, last_done, last_result, priority, rationale '
            'FROM screenings ORDER BY name'
        ).fetchall()
    except Exception:
        pass

    con.close()
    return categories, biomarkers, bm_cats, results, insights, rsids, unified, screenings


def build_json(categories, biomarkers, bm_cats, results, insights, rsids, unified, screenings):
    cats = [{'id': c[0], 'en': c[1], 'ru': c[2]} for c in categories]

    bms = []
    for b in biomarkers:
        entry = {
            'id': b[0],
            'en': b[1],
            'ru': b[2],
            'spec': b[3] or '',
            'unit': b[4] or '',
            'cats': bm_cats.get(b[0], []),
        }
        if b[5] is not None or b[6] is not None:
            entry['ol'] = b[5]  # optimal-band low (longevity-optimal, not lab-ref)
            entry['oh'] = b[6]  # optimal-band high
        if b[7]:  # pers_bands JSON: personalized age/sex/genetics reference interval
            pb = json.loads(b[7])
            entry['pr'] = {'m': pb['mode'], 'pts': pb['pts']}  # step bands or interp anchors
            entry['pb'] = pb['basis']    # provenance string (reasoning + source)
        if b[8] is not None or b[9] is not None:  # lab/population reference range (flat)
            entry['rl'] = b[8]
            entry['rh'] = b[9]
        bms.append(entry)

    # dates list (sorted)
    dates = sorted({r[1] for r in results})

    # results: {biomarker_id: {date_index: value}}
    date_idx = {d: i for i, d in enumerate(dates)}
    res = {}
    for bid, date, value in results:
        res.setdefault(str(bid), {})[date_idx[date]] = value

    # insights: {category_id: {text, genomics}}
    ins = {str(k): v for k, v in insights.items()}

    # rsids: {category_id: [{rsid, gene, rel}]}
    rs = {str(k): v for k, v in rsids.items()}

    # screenings: [{name, domain, cadence, last, result, prio}]
    scr = [{'name': s[0], 'domain': s[1] or '', 'cadence': s[2], 'last': s[3],
            'result': s[4] or '', 'prio': s[5] or '', 'rationale': s[6] or ''} for s in screenings]

    return {'cats': cats, 'bms': bms, 'dates': dates, 'res': res, 'ins': ins,
            'rs': rs, 'unified': unified, 'screenings': scr, 'dob': read_dob()}


# DOB drives age-at-draw for age-banded personalized ranges. Lives only in the
# git-ignored user_facts.md; fall back to the known value if the file is absent.
USER_FACTS = Path('.claude/skills/regenerate-unified-protocol/user_facts.md')


def read_dob(default='1980-01-01'):
    try:
        import re
        m = re.search(r'DOB:\**\s*(\d{4}-\d{2}-\d{2})', USER_FACTS.read_text(encoding='utf-8'))
        return m.group(1) if m else default
    except OSError:
        return default


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lab Results Viewer</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #0f1117;
  --panel: #1a1d27;
  --border: #2d3045;
  --accent: #5b8dee;
  --accent2: #7c5ce8;
  --text: #e0e4f0;
  --muted: #8890a8;
  --hover: #22263a;
  --active: #1e2e54;
  --cell-w: 88px;
}
body { background: var(--bg); color: var(--text); font: 13px/1.4 'Inter', system-ui, sans-serif; display: flex; height: 100vh; overflow: hidden; }

/* ── Sidebar ── */
#sidebar {
  width: 260px; min-width: 200px; background: var(--panel); border-right: 1px solid var(--border);
  display: flex; flex-direction: column; overflow: hidden;
}
#sidebar-header { padding: 14px 12px 10px; border-bottom: 1px solid var(--border); }
#sidebar-header h1 { font-size: 14px; font-weight: 600; margin-bottom: 8px; color: var(--accent); }
#search-box {
  width: 100%; padding: 6px 10px; background: var(--bg); border: 1px solid var(--border);
  border-radius: 6px; color: var(--text); font-size: 12px; outline: none;
}
#search-box:focus { border-color: var(--accent); }
#cat-list { flex: 1; overflow-y: auto; padding: 4px 0; }
.cat-item {
  padding: 7px 14px; cursor: pointer; font-size: 12px; color: var(--muted);
  border-left: 3px solid transparent; transition: background .15s;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.cat-item:hover { background: var(--hover); color: var(--text); }
.cat-item.active { background: var(--active); color: var(--accent); border-left-color: var(--accent); }
.cat-count { float: right; background: var(--border); border-radius: 10px; padding: 1px 6px; font-size: 10px; color: var(--muted); }
#unified-btn, #longevity-btn, #screening-btn {
  margin: 8px 10px 0; padding: 9px 14px; cursor: pointer; font-size: 12px; font-weight: 600;
  color: #fff; background: linear-gradient(135deg, var(--accent), var(--accent2)); border: none;
  border-radius: 8px; font-family: inherit; transition: opacity .15s; text-align: left; display: block; width: calc(100% - 20px);
}
#unified-btn { margin-bottom: 8px; }
#unified-btn:hover, #longevity-btn:hover, #screening-btn:hover { opacity: .85; }
#unified-btn.active, #longevity-btn.active, #screening-btn.active { box-shadow: 0 0 0 2px var(--accent); }

/* ── Main ── */
#main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
#toolbar { padding: 10px 16px; border-bottom: 1px solid var(--border); background: var(--panel); display: flex; align-items: center; gap: 12px; }
#cat-title { font-weight: 600; font-size: 14px; flex: 1; }
#bm-search { padding: 5px 10px; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-size: 12px; width: 200px; outline: none; }
#bm-search:focus { border-color: var(--accent); }
#row-count { color: var(--muted); font-size: 12px; }

/* ── View Toggle ── */
.view-toggle {
  display: flex; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; overflow: hidden;
}
.view-toggle button {
  padding: 4px 14px; border: none; background: none; color: var(--muted); font-size: 12px;
  cursor: pointer; transition: all .15s; font-family: inherit;
}
.view-toggle button:not(:last-child) { border-right: 1px solid var(--border); }
.view-toggle button.active { background: var(--accent); color: #fff; }
.view-toggle button:hover:not(.active) { color: var(--text); background: var(--hover); }

/* ── Insights Panel ── */
#insights-panel { display: none; flex: 1; overflow-y: auto; padding: 24px 28px; }
#insights-panel.visible { display: block; }
.insight-text {
  font-size: 13px; line-height: 1.7; color: var(--text); max-width: 800px;
  white-space: pre-wrap; margin-bottom: 28px;
}
.rsid-section h3 { font-size: 13px; font-weight: 600; color: var(--accent); margin-bottom: 10px; }
.rsid-table { width: 100%; max-width: 800px; border-collapse: collapse; }
.rsid-table th {
  text-align: left; padding: 7px 10px; font-size: 11px; font-weight: 500; color: var(--muted);
  border-bottom: 2px solid var(--border); background: var(--panel);
}
.rsid-table td {
  padding: 6px 10px; font-size: 12px; border-bottom: 1px solid var(--border);
}
.rsid-table td:first-child { color: var(--accent); font-family: 'SF Mono', 'Fira Code', monospace; font-size: 11px; }
.rsid-table td:nth-child(2) { color: var(--accent2); font-weight: 500; font-size: 11px; }
.rsid-table tr:hover { background: var(--hover); }
.rsid-table .gt-cell { font-family: 'SF Mono', 'Fira Code', monospace; font-size: 11px; }
.rsid-table .gt-cell.hom-alt { color: #e74c3c; }
.rsid-table .gt-cell.het { color: #f39c12; }
.rsid-table .gt-cell.hom-ref { color: #2ecc71; }
.rsid-table .gt-cell.no-data { color: var(--border); }
.no-insight { color: var(--muted); font-style: italic; }

/* ── Insight sub-tabs ── */
.insight-tabs { display: flex; gap: 0; margin-bottom: 20px; border-bottom: 2px solid var(--border); }
.insight-tab {
  padding: 8px 18px; cursor: pointer; font-size: 12px; font-weight: 500; color: var(--muted);
  border: none; background: none; border-bottom: 2px solid transparent; margin-bottom: -2px;
  font-family: inherit; transition: all .15s;
}
.insight-tab:hover { color: var(--text); }
.insight-tab.active { color: var(--accent); border-bottom-color: var(--accent); }
.insight-tab.has-content::after { content: ''; display: inline-block; width: 5px; height: 5px; border-radius: 50%; background: var(--accent2); margin-left: 6px; vertical-align: middle; }
.insight-pane { display: none; }
.insight-pane.active { display: block; }

/* ── Protocol ── */
.protocol-text {
  font-size: 13px; line-height: 1.8; color: var(--text); max-width: 840px; white-space: pre-wrap;
}
.protocol-text .section-header {
  color: var(--accent); font-weight: 600; font-size: 14px; margin-top: 20px; display: block;
  border-bottom: 1px solid var(--border); padding-bottom: 4px; margin-bottom: 8px;
}

/* ── Personalized-range status dot (age/sex/genetics) ── */
.snap-range { white-space: nowrap; cursor: help; }
.rng-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
  margin-right: 6px; vertical-align: middle; background: var(--muted); }
.rng-dot.in   { background: #2ecc71; }
.rng-dot.high { background: #f39c12; }
.rng-dot.low  { background: #5b8dee; }

/* ── Combined Assessment sections ── */
.assess-section { margin-bottom: 28px; max-width: 1000px; }
.assess-h {
  font-size: 12px; font-weight: 600; color: var(--accent2); margin-bottom: 10px;
  padding-bottom: 4px; border-bottom: 1px solid var(--border);
  text-transform: uppercase; letter-spacing: 0.06em;
}
.snap-table, .burden-table, .conc-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.snap-table th, .burden-table th, .conc-table th {
  text-align: left; padding: 6px 10px; font-size: 10px; font-weight: 500; color: var(--muted);
  border-bottom: 1px solid var(--border); text-transform: uppercase; letter-spacing: 0.04em;
}
.snap-table td, .burden-table td, .conc-table td {
  padding: 7px 10px; border-bottom: 1px solid var(--border); vertical-align: top;
}
.snap-table tr:hover, .burden-table tr:hover, .conc-table tr:hover { background: var(--hover); }
.snap-val { font-weight: 500; color: var(--text); white-space: nowrap; }
.snap-unit { color: var(--muted); font-size: 10px; margin-left: 4px; }
.snap-trend { text-align: center; font-size: 14px; font-weight: 600; }
.snap-trend.up { color: #f39c12; }
.snap-trend.down { color: #5b8dee; }
.snap-trend.flat { color: var(--muted); }
.snap-prior, .snap-date { color: var(--muted); font-size: 11px; }
.gene-cell { font-weight: 500; color: var(--accent); font-family: 'SF Mono', 'Fira Code', monospace; }
.zyg-cell { color: var(--text); }
.count-cell { text-align: right; color: var(--muted); }
.z-hom { color: #e74c3c; font-weight: 500; }
.z-het { color: #f39c12; font-weight: 500; }
.z-ref { color: #2ecc71; font-weight: 500; }
.conc-table td:nth-child(4) { font-weight: 600; text-align: center; white-space: nowrap; font-size: 10px; letter-spacing: 0.04em; }
.v-confirms { color: #2ecc71; }
.v-partial { color: #f39c12; }
.v-unresolved { color: var(--muted); }
.v-contradicts { color: #e74c3c; }
.v-favorable { color: #5b8dee; }
.assess-narrative { font-size: 13px; line-height: 1.7; color: var(--text); max-width: 800px; white-space: pre-wrap; }

.genomics-section { margin-top: 28px; }
.genomics-section h3 { font-size: 13px; font-weight: 600; color: var(--accent2); margin-bottom: 10px; }
.genomics-text { font-size: 12px; line-height: 1.7; color: var(--text); white-space: pre-wrap; font-family: 'SF Mono', 'Fira Code', monospace; max-width: 800px; }
.legend { display: flex; gap: 16px; margin-bottom: 12px; font-size: 11px; }
.legend span { display: flex; align-items: center; gap: 4px; }
.legend .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.legend .dot-hom { background: #e74c3c; }
.legend .dot-het { background: #f39c12; }
.legend .dot-none { background: var(--border); }

/* ── Table ── */
#table-wrap { flex: 1; overflow: auto; }
table { border-collapse: collapse; }
thead { position: sticky; top: 0; z-index: 10; }
th {
  background: var(--panel); border-bottom: 2px solid var(--border); border-right: 1px solid var(--border);
  padding: 7px 8px; font-size: 11px; font-weight: 500; color: var(--muted); white-space: nowrap;
}
th.col-name { min-width: 220px; width: 220px; text-align: left; position: sticky; left: 0; z-index: 20; background: var(--panel); }
th.col-spec { min-width: 100px; width: 100px; text-align: left; }
th.col-unit { min-width: 70px; width: 70px; text-align: center; }
th.col-date { min-width: var(--cell-w); width: var(--cell-w); text-align: center; }
th.col-date span { display: block; }
th.col-date .yr { font-size: 10px; color: var(--accent2); }
th.col-date .md { font-size: 11px; }
tbody tr { transition: background .1s; }
tbody tr:hover { background: var(--hover); }
tbody tr.filtered { display: none; }
td {
  border-bottom: 1px solid var(--border); border-right: 1px solid var(--border);
  padding: 6px 8px; vertical-align: middle;
}
td.col-name {
  position: sticky; left: 0; background: var(--bg); z-index: 5;
  cursor: pointer; min-width: 220px;
}
tbody tr:hover td.col-name { background: var(--hover); }
.bm-name { font-size: 12px; font-weight: 500; }
.bm-ru { font-size: 10px; color: var(--muted); margin-top: 2px; }
td.col-spec { font-size: 11px; color: var(--muted); }
td.col-unit { font-size: 11px; color: var(--muted); text-align: center; }
td.col-date { text-align: center; font-size: 12px; min-width: var(--cell-w); }
td.col-date.has-val { color: var(--text); }
td.col-date:not(.has-val) { color: var(--border); }
.empty-state { padding: 40px; text-align: center; color: var(--muted); }

/* ── Chart modal ── */
#modal-overlay {
  display: none; position: fixed; inset: 0; background: rgba(0,0,0,.7);
  z-index: 100; align-items: center; justify-content: center;
}
#modal-overlay.open { display: flex; }
#modal {
  background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
  width: min(760px, 94vw); max-height: 86vh; display: flex; flex-direction: column;
  overflow: hidden; box-shadow: 0 24px 60px rgba(0,0,0,.5);
}
#modal-head { padding: 16px 20px 12px; border-bottom: 1px solid var(--border); display: flex; align-items: flex-start; gap: 10px; }
#modal-title { flex: 1; }
#modal-title .name-en { font-size: 15px; font-weight: 600; }
#modal-title .name-ru { font-size: 11px; color: var(--muted); margin-top: 3px; }
#modal-title .meta { font-size: 11px; color: var(--muted); margin-top: 4px; }
#modal-close { background: none; border: none; color: var(--muted); font-size: 18px; cursor: pointer; padding: 2px 6px; border-radius: 4px; }
#modal-close:hover { background: var(--hover); color: var(--text); }
#modal-body { padding: 16px 20px; overflow-y: auto; flex: 1; }
#chart-container { position: relative; height: 240px; }
#val-table { margin-top: 20px; width: 100%; border-collapse: collapse; }
#val-table th, #val-table td { padding: 6px 10px; border-bottom: 1px solid var(--border); font-size: 12px; text-align: left; }
#val-table th { color: var(--muted); font-weight: 500; }
#val-table td:last-child { color: var(--accent); font-weight: 500; }

/* scrollbar */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
</style>
</head>
<body>

<div id="sidebar">
  <div id="sidebar-header">
    <h1>Lab Results</h1>
    <input id="search-box" type="search" placeholder="Search categories…" oninput="filterCats(this.value)">
  </div>
  <button id="longevity-btn" onclick="showLongevity()">🧬 Longevity Dashboard</button>
  <button id="screening-btn" onclick="showScreening()">🩺 Screening Calendar</button>
  <button id="unified-btn" onclick="showUnified()">Unified Protocol</button>
  <div id="cat-list"></div>
</div>

<div id="main">
  <div id="toolbar">
    <span id="cat-title">Select a category</span>
    <div class="view-toggle" id="view-toggle" style="display:none">
      <button class="active" onclick="setView('biomarkers')">Biomarkers</button>
      <button onclick="setView('insights')">Insights</button>
    </div>
    <input id="bm-search" type="search" placeholder="Filter biomarkers…" oninput="filterRows(this.value)">
    <span id="row-count"></span>
  </div>
  <div id="table-wrap">
    <div class="empty-state" id="empty-state">← Select a category to view results</div>
    <table id="data-table" style="display:none">
      <thead id="thead"></thead>
      <tbody id="tbody"></tbody>
    </table>
  </div>
  <div id="insights-panel"></div>
</div>

<div id="modal-overlay" onclick="if(event.target===this)closeModal()">
  <div id="modal">
    <div id="modal-head">
      <div id="modal-title"></div>
      <button id="modal-close" onclick="closeModal()">✕</button>
    </div>
    <div id="modal-body">
      <div id="chart-container"><canvas id="chart-canvas"></canvas></div>
      <table id="val-table"></table>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script>
const DATA = __DATA__;

// ── Personalized ranges (age/sex/genetics) ──────────────────────────────────
// b.pr = [[age_min, age_max, low_or_null, high_or_null], ...] (step function of
// age); b.pb = provenance. Bounds change with age, so we evaluate at age-at-draw.
const DOB = new Date(DATA.dob || '1980-01-01');
function ageAt(dateStr) { return (new Date(dateStr) - DOB) / (365.25 * 86400000); }
function lerp(a, b, t) { return (a == null || b == null) ? null : a + (b - a) * t; }
// pr = {m:'step'|'interp', pts}. Returns [lo, hi] (either may be null) at `age`.
function personalBand(pr, age) {
  if (!pr) return null;
  const pts = pr.pts;
  if (pr.m === 'interp') {              // linear interpolation between age anchors
    if (age <= pts[0][0]) return [pts[0][1], pts[0][2]];
    const last = pts[pts.length - 1];
    if (age >= last[0]) return [last[1], last[2]];
    for (let i = 0; i < pts.length - 1; i++) {
      const [a0, lo0, hi0] = pts[i], [a1, lo1, hi1] = pts[i + 1];
      if (age >= a0 && age < a1) {
        const t = (age - a0) / (a1 - a0);
        return [lerp(lo0, lo1, t), lerp(hi0, hi1, t)];
      }
    }
    return null;
  }
  for (const [amin, amax, lo, hi] of pts) {   // step: containing band
    if (age >= amin && age < amax) return [lo, hi];
  }
  return null;
}
// Status of a value vs a personalized band: 'high' | 'low' | 'in' | '' (unknown).
function bandStatus(val, band) {
  if (!band) return '';
  const v = parseNum(val);
  if (isNaN(v)) return '';
  const [lo, hi] = band;
  if (hi != null && v > hi) return 'high';
  if (lo != null && v < lo) return 'low';
  return 'in';
}
function fmtBound(n) {
  if (Number.isInteger(n)) return String(n);
  return String(+n.toFixed(Math.abs(n) >= 10 ? 0 : 2));  // interp values are non-integer
}

// ── Index ───────────────────────────────────────────────────────────────────
const catBms = {};  // cat_id → [bm]
DATA.bms.forEach(b => {
  b.cats.forEach(cid => {
    (catBms[cid] = catBms[cid] || []).push(b);
  });
});

// ── Sidebar ──────────────────────────────────────────────────────────────────
const catList = document.getElementById('cat-list');
let activeCat = null;

function renderSidebar(filter = '') {
  catList.innerHTML = '';
  const q = filter.toLowerCase();
  // "All" entry
  if (!q || 'all'.includes(q)) {
    const div = document.createElement('div');
    div.className = 'cat-item' + (activeCat === 'all' ? ' active' : '');
    div.innerHTML = `All <span class="cat-count">${DATA.bms.length}</span>`;
    div.onclick = () => selectAll();
    catList.appendChild(div);
  }
  DATA.cats.forEach(c => {
    if (q && !c.en.toLowerCase().includes(q) && !c.ru.toLowerCase().includes(q)) return;
    const cnt = (catBms[c.id] || []).length;
    if (!cnt) return;
    const div = document.createElement('div');
    div.className = 'cat-item' + (activeCat === c.id ? ' active' : '');
    div.innerHTML = `<span style="color:var(--muted);font-size:10px;margin-right:4px;">${c.id}</span>${escH(c.en)} <span class="cat-count">${cnt}</span>`;
    div.title = c.ru;
    div.onclick = () => selectCat(c);
    catList.appendChild(div);
  });
}

function filterCats(v) { renderSidebar(v); }

let currentView = 'biomarkers';

function selectAll() {
  activeCat = 'all';
  renderSidebar(document.getElementById('search-box').value);
  document.getElementById('unified-btn').classList.remove('active');
  document.getElementById('longevity-btn').classList.remove('active');
  document.getElementById('screening-btn').classList.remove('active');
  document.getElementById('cat-title').textContent = 'All Biomarkers';
  document.getElementById('bm-search').value = '';
  document.getElementById('view-toggle').style.display = 'none';
  document.getElementById('insights-panel').classList.remove('visible');
  document.getElementById('table-wrap').style.display = '';
  document.getElementById('bm-search').style.display = '';
  document.getElementById('row-count').style.display = '';
  renderTable(DATA.bms);
}

function selectCat(c) {
  activeCat = c.id;
  renderSidebar(document.getElementById('search-box').value);
  document.getElementById('cat-title').textContent = `[${c.id}] ${c.en}`;
  document.getElementById('bm-search').value = '';
  document.getElementById('view-toggle').style.display = '';
  if (currentView === 'biomarkers') {
    showBiomarkers(c.id);
  } else {
    showInsights(c.id);
  }
}

function setView(view) {
  currentView = view;
  document.querySelectorAll('.view-toggle button').forEach(btn => {
    btn.classList.toggle('active', btn.textContent.toLowerCase() === view);
  });
  if (!activeCat) return;
  if (view === 'biomarkers') {
    showBiomarkers(activeCat);
  } else {
    showInsights(activeCat);
  }
}

function showBiomarkers(catId) {
  document.getElementById('insights-panel').classList.remove('visible');
  document.getElementById('table-wrap').style.display = '';
  document.getElementById('bm-search').style.display = '';
  document.getElementById('row-count').style.display = '';
  renderTable(catBms[catId] || []);
}

function showInsights(catId) {
  document.getElementById('table-wrap').style.display = 'none';
  document.getElementById('bm-search').style.display = 'none';
  document.getElementById('row-count').style.display = 'none';
  renderInsights(catId);
}

function renderInsights(catId) {
  const panel = document.getElementById('insights-panel');
  const insObj = DATA.ins[String(catId)] || {};
  const rsids = DATA.rs[String(catId)] || [];
  const hasProtocol = insObj.protocol;
  const found = rsids.filter(r => r.gt).length;

  // Tabs: Assessment (combined) | Protocol (conditional) | Variants
  let tabs = `<div class="insight-tabs">`;
  tabs += `<button class="insight-tab active" onclick="switchInsightTab(this,'pane-assess')">Assessment</button>`;
  if (hasProtocol) tabs += `<button class="insight-tab has-content" onclick="switchInsightTab(this,'pane-protocol')">Protocol</button>`;
  tabs += `<button class="insight-tab" onclick="switchInsightTab(this,'pane-variants')">Variants (${found}/${rsids.length})</button>`;
  tabs += `</div>`;

  const paneAssess = buildAssessmentPane(catId, insObj, rsids);

  // Protocol pane (unchanged)
  let paneProtocol = '';
  if (hasProtocol) {
    let protocolHtml = escH(insObj.protocol);
    protocolHtml = protocolHtml.replace(/^(═+\s*.+?\s*═+)$/gm, '<span class="section-header">$1</span>');
    paneProtocol = `<div class="insight-pane" id="pane-protocol"><div class="protocol-text">${protocolHtml}</div></div>`;
  }

  // Variants pane (unchanged — full per-rsID detail)
  let paneVariants = `<div class="insight-pane" id="pane-variants">`;
  if (rsids.length) {
    paneVariants += `<div class="rsid-section"><h3>Genetic Variants (${found} of ${rsids.length} found in genome)</h3>`;
    paneVariants += `<div class="legend"><span><span class="dot dot-hom"></span> Homozygous alt</span><span><span class="dot dot-het"></span> Heterozygous</span><span><span class="dot dot-none"></span> Not found</span></div>`;
    paneVariants += `<table class="rsid-table"><thead><tr><th>rsID</th><th>Gene</th><th>Genotype</th><th>Zygosity</th><th>Relevance</th></tr></thead><tbody>`;
    const sorted = [...rsids].sort((a, b) => (b.gt ? 1 : 0) - (a.gt ? 1 : 0));
    sorted.forEach(r => {
      const cls = r.zyg || 'no-data';
      const gt = r.gt || '-';
      const zyg = r.zyg || '-';
      paneVariants += `<tr><td>${escH(r.rsid)}</td><td>${escH(r.gene)}</td><td class="gt-cell ${cls}">${escH(gt)}</td><td class="gt-cell ${cls}">${escH(zyg)}</td><td>${escH(r.rel)}</td></tr>`;
    });
    paneVariants += `</tbody></table></div>`;
  } else {
    paneVariants += `<div class="no-insight">No genetic variants listed for this category.</div>`;
  }
  paneVariants += `</div>`;

  panel.innerHTML = tabs + paneAssess + paneProtocol + paneVariants;
  panel.classList.add('visible');
}

// ── Combined Assessment pane ──────────────────────────────────────────────────
function buildAssessmentPane(catId, insObj, rsids) {
  const bms = catBms[catId] || [];
  let html = `<div class="insight-pane active" id="pane-assess">`;
  html += buildLabSnapshot(bms);
  html += buildVariantBurden(rsids);
  if (insObj.concordance) html += buildConcordance(insObj.concordance);
  if (insObj.text) {
    html += `<div class="assess-section"><h3 class="assess-h">Assessment</h3>`
         +  `<div class="assess-narrative">${escH(insObj.text)}</div></div>`;
  }
  if (insObj.dna) {
    html += `<div class="assess-section"><h3 class="assess-h">DNA-enriched detail</h3>`
         +  `<div class="assess-narrative">${escH(insObj.dna)}</div></div>`;
  }
  if (!bms.length && !rsids.length && !insObj.concordance && !insObj.text && !insObj.dna) {
    html += `<div class="no-insight">No assessment available for this category.</div>`;
  }
  html += `</div>`;
  return html;
}

function buildLabSnapshot(bms) {
  if (!bms.length) return '';
  const rows = [];
  bms.forEach(b => {
    const resMap = DATA.res[String(b.id)] || {};
    const points = [];
    DATA.dates.forEach((d, i) => {
      if (resMap[i] !== undefined) points.push({date: d, value: resMap[i]});
    });
    if (!points.length) return;
    const latest = points[points.length - 1];
    const prior  = points.length > 1 ? points[points.length - 2] : null;
    let trend = '', trendCls = '';
    if (prior) {
      const lv = parseFloat(latest.value);
      const pv = parseFloat(prior.value);
      if (!isNaN(lv) && !isNaN(pv) && pv !== 0) {
        const ratio = lv / pv;
        if (ratio > 1.05)      { trend = '↑'; trendCls = 'up'; }
        else if (ratio < 0.95) { trend = '↓'; trendCls = 'down'; }
        else                   { trend = '→'; trendCls = 'flat'; }
      }
    }
    const band = personalBand(b.pr, ageAt(latest.date));
    rows.push({name: b.en, unit: b.unit, latest: latest.value, date: latest.date,
               trend, trendCls, prior: prior ? prior.value : null,
               priorDate: prior ? prior.date : null,
               band, basis: b.pb || '', status: bandStatus(latest.value, band)});
  });
  if (!rows.length) return '';
  let html = `<div class="assess-section"><h3 class="assess-h">Lab Snapshot (${rows.length})</h3>`
          +  `<table class="snap-table"><thead><tr>`
          +  `<th>Biomarker</th><th>Latest</th><th>Your range</th><th>Trend</th><th>Prior</th><th>Date</th>`
          +  `</tr></thead><tbody>`;
  rows.forEach(r => {
    const unitHtml = r.unit ? `<span class="snap-unit">${escH(r.unit)}</span>` : '';
    let rangeHtml = '<span class="snap-prior">—</span>';
    if (r.band) {
      const [lo, hi] = r.band;
      const txt = (lo != null ? fmtBound(lo) : '') + '–' + (hi != null ? fmtBound(hi) : '');
      const dot = r.status ? `<span class="rng-dot ${r.status}"></span>` : '';
      rangeHtml = `<span class="snap-range" title="${escH(r.basis)}">${dot}${escH(txt)}</span>`;
    }
    html += `<tr>`
         +  `<td>${escH(r.name)}</td>`
         +  `<td class="snap-val">${escH(String(r.latest))}${unitHtml}</td>`
         +  `<td>${rangeHtml}</td>`
         +  `<td class="snap-trend ${r.trendCls}">${r.trend}</td>`
         +  `<td class="snap-prior">${r.prior !== null ? escH(String(r.prior)) : '—'}</td>`
         +  `<td class="snap-date">${r.date}</td>`
         +  `</tr>`;
  });
  html += `</tbody></table></div>`;
  return html;
}

function buildVariantBurden(rsids) {
  if (!rsids.length) return '';
  const found = rsids.filter(r => r.gt);
  if (!found.length) return '';
  const byGene = {};
  found.forEach(r => {
    const g = r.gene || '(unknown)';
    if (!byGene[g]) byGene[g] = {homAlt: 0, het: 0, homRef: 0};
    if (r.zyg === 'hom-alt')      byGene[g].homAlt++;
    else if (r.zyg === 'het')     byGene[g].het++;
    else if (r.zyg === 'hom-ref') byGene[g].homRef++;
  });
  const genes = Object.keys(byGene).sort((a, b) => {
    const sa = byGene[a].homAlt * 2 + byGene[a].het;
    const sb = byGene[b].homAlt * 2 + byGene[b].het;
    return sb - sa || a.localeCompare(b);
  });
  let html = `<div class="assess-section"><h3 class="assess-h">Variant Burden (${found.length} of ${rsids.length} genotyped)</h3>`
          +  `<table class="burden-table"><tbody>`;
  genes.forEach(g => {
    const b = byGene[g];
    const total = b.homAlt + b.het + b.homRef;
    const parts = [];
    if (b.homAlt)  parts.push(`<span class="z-hom">${b.homAlt} hom-alt</span>`);
    if (b.het)     parts.push(`<span class="z-het">${b.het} het</span>`);
    if (b.homRef)  parts.push(`<span class="z-ref">${b.homRef} hom-ref</span>`);
    html += `<tr><td class="gene-cell">${escH(g)}</td>`
         +  `<td class="zyg-cell">${parts.join(' + ')}</td>`
         +  `<td class="count-cell">${total}</td></tr>`;
  });
  html += `</tbody></table></div>`;
  return html;
}

function buildConcordance(text) {
  const rows = text.split('\n')
    .map(l => l.trim())
    .filter(l => l && !l.startsWith('#'))
    .map(l => l.split('|').map(s => s.trim()))
    .filter(parts => parts.length === 4);
  if (!rows.length) return '';
  const verdictClass = {
    'CONFIRMS': 'v-confirms', 'PARTIAL': 'v-partial',
    'UNRESOLVED': 'v-unresolved', 'CONTRADICTS': 'v-contradicts',
    'FAVORABLE': 'v-favorable',
  };
  let html = `<div class="assess-section"><h3 class="assess-h">Concordance — do labs confirm the genetic prediction?</h3>`
          +  `<table class="conc-table"><thead><tr>`
          +  `<th>Mechanism</th><th>Predicted</th><th>Observed</th><th>Verdict</th>`
          +  `</tr></thead><tbody>`;
  rows.forEach(([m, p, o, v]) => {
    const cls = verdictClass[v] || '';
    html += `<tr><td>${escH(m)}</td><td>${escH(p)}</td><td>${escH(o)}</td>`
         +  `<td class="${cls}">${escH(v)}</td></tr>`;
  });
  html += `</tbody></table></div>`;
  return html;
}

function switchInsightTab(btn, paneId) {
  btn.closest('.insight-tabs').querySelectorAll('.insight-tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  btn.closest('#insights-panel').querySelectorAll('.insight-pane').forEach(p => p.classList.remove('active'));
  document.getElementById(paneId).classList.add('active');
}

function showUnified() {
  if (!DATA.unified) return;
  // Deselect category
  activeCat = null;
  renderSidebar(document.getElementById('search-box').value);
  document.getElementById('longevity-btn').classList.remove('active');
  document.getElementById('screening-btn').classList.remove('active');
  document.getElementById('unified-btn').classList.add('active');
  document.getElementById('cat-title').textContent = 'Unified Master Protocol';
  document.getElementById('view-toggle').style.display = 'none';
  document.getElementById('table-wrap').style.display = 'none';
  document.getElementById('bm-search').style.display = 'none';
  document.getElementById('row-count').style.display = 'none';
  document.getElementById('data-table').style.display = 'none';
  document.getElementById('empty-state').style.display = 'none';

  let html = escH(DATA.unified);
  // Style section headers (═══ lines)
  html = html.replace(/^(═+\s*.+?\s*═+)$/gm, '<span class="section-header">$1</span>');
  // Style sub-headers (─── lines)
  html = html.replace(/^(─+\s*.+?\s*─+)$/gm, '<span style="color:var(--accent2);font-weight:600;font-size:13px;display:block;margin-top:16px;">$1</span>');

  const panel = document.getElementById('insights-panel');
  panel.innerHTML = '<div class="protocol-text" style="padding-bottom:40px;">' + html + '</div>';
  panel.classList.add('visible');
}

// ── Longevity dashboard ──────────────────────────────────────────────────────
// Curated view of the mortality-moving markers (those with an optimal band
// seeded via seed_optimal_bands.py), each with an optimal-band status and a
// real least-squares trend — not the ±5% last-vs-prior arrow used elsewhere.

function parseNum(v) {
  // Strip qualifiers like "<10.00", ">750", "≤2.0" before parsing.
  if (v === null || v === undefined) return NaN;
  return parseFloat(String(v).replace(/^[<>≤≥~]+/, ''));
}

function leastSqSlopePerYear(points) {
  // points: [{date:'YYYY-MM-DD', value}]. Returns slope in units/year, or null.
  const pts = points
    .map(p => ({t: Date.parse(p.date), y: parseNum(p.value)}))
    .filter(p => !isNaN(p.t) && !isNaN(p.y));
  if (pts.length < 2) return null;
  const t0 = pts[0].t;
  const xs = pts.map(p => (p.t - t0) / 86400000);  // days since first
  const ys = pts.map(p => p.y);
  const n = xs.length;
  const mx = xs.reduce((a, b) => a + b, 0) / n;
  const my = ys.reduce((a, b) => a + b, 0) / n;
  let num = 0, den = 0;
  for (let i = 0; i < n; i++) { num += (xs[i] - mx) * (ys[i] - my); den += (xs[i] - mx) ** 2; }
  if (den === 0) return null;
  return (num / den) * 365;  // per-day slope → per-year
}

function showLongevity() {
  activeCat = null;
  renderSidebar(document.getElementById('search-box').value);
  document.getElementById('unified-btn').classList.remove('active');
  document.getElementById('screening-btn').classList.remove('active');
  document.getElementById('longevity-btn').classList.add('active');
  document.getElementById('cat-title').textContent = 'Longevity Dashboard';
  document.getElementById('view-toggle').style.display = 'none';
  document.getElementById('table-wrap').style.display = 'none';
  document.getElementById('bm-search').style.display = 'none';
  document.getElementById('row-count').style.display = 'none';
  document.getElementById('data-table').style.display = 'none';
  document.getElementById('empty-state').style.display = 'none';

  const rows = [];
  DATA.bms.forEach(b => {
    if (b.ol === undefined && b.oh === undefined) return;
    const resMap = DATA.res[String(b.id)] || {};
    const points = [];
    DATA.dates.forEach((d, i) => { if (resMap[i] !== undefined) points.push({date: d, value: resMap[i]}); });
    if (!points.length) return;
    const latest = points[points.length - 1];
    const val = parseNum(latest.value);
    // Status vs optimal band.
    let status = 'in', statusTxt = 'optimal';
    if (b.oh != null && !isNaN(val) && val > b.oh) { status = 'high'; statusTxt = 'above optimal'; }
    else if (b.ol != null && !isNaN(val) && val < b.ol) { status = 'low'; statusTxt = 'below optimal'; }
    const slope = leastSqSlopePerYear(points);
    // Direction relative to optimal: is the trend moving toward the band?
    let arrow = '→', moving = '';
    if (slope !== null && Math.abs(slope) > 1e-9) {
      arrow = slope > 0 ? '↑' : '↓';
      if (status === 'high') moving = slope < 0 ? 'toward' : 'away';
      else if (status === 'low') moving = slope > 0 ? 'toward' : 'away';
    }
    const band = (b.ol != null ? b.ol : '') + '–' + (b.oh != null ? b.oh : '');
    rows.push({name: b.en, unit: b.unit, latest: latest.value, date: latest.date,
               band, status, statusTxt, slope, arrow, moving, n: points.length});
  });
  // Out-of-optimal first, then by name.
  const order = {high: 0, low: 0, in: 1};
  rows.sort((a, b) => (order[a.status] - order[b.status]) || a.name.localeCompare(b.name));

  const off = rows.filter(r => r.status !== 'in').length;
  let html = `<div class="protocol-text" style="padding:8px 4px 40px;">`
    + `<p style="color:var(--muted);font-size:12px;margin-bottom:14px;">`
    + `${rows.length} mortality-moving markers · <b>${off}</b> outside longevity-optimal band. `
    + `Bands are longevity-optimal (tighter than lab reference). Trend = least-squares slope per year.</p>`
    + `<table class="snap-table"><thead><tr>`
    + `<th>Marker</th><th>Latest</th><th>Optimal band</th><th>Status</th><th>Trend/yr</th></tr></thead><tbody>`;
  const col = {high: '#f39c12', low: '#5b8dee', in: '#2ecc71'};
  rows.forEach(r => {
    const unitHtml = r.unit ? `<span class="snap-unit">${escH(r.unit)}</span>` : '';
    let trendTxt = '—';
    if (r.slope !== null) {
      const mag = Math.abs(r.slope) >= 1 ? r.slope.toFixed(1) : r.slope.toFixed(3);
      const mv = r.moving ? ` <span style="color:${r.moving === 'toward' ? '#2ecc71' : '#e74c3c'}">(${r.moving})</span>` : '';
      trendTxt = `${r.arrow} ${mag}${unitHtml ? '' : ''}${mv}`;
    }
    html += `<tr>`
      + `<td>${escH(r.name)}</td>`
      + `<td class="snap-val">${escH(String(r.latest))}${unitHtml}</td>`
      + `<td class="snap-prior">${escH(r.band)}</td>`
      + `<td style="font-weight:600;color:${col[r.status]}">${r.statusTxt}</td>`
      + `<td>${trendTxt} <span style="color:var(--muted);font-size:11px;">n=${r.n}</span></td>`
      + `</tr>`;
  });
  html += `</tbody></table></div>`;

  const panel = document.getElementById('insights-panel');
  panel.innerHTML = html;
  panel.classList.add('visible');
}

// ── Screening calendar ───────────────────────────────────────────────────────
// Consolidated "next due" view of procedural screenings (from the `screenings`
// table). "Today" is the browser's real date — this is a live forward calendar.

function addMonths(dateStr, months) {
  const d = new Date(dateStr + 'T00:00:00');
  const day = d.getDate();
  const t = new Date(d.getTime());
  t.setDate(1);
  t.setMonth(t.getMonth() + months);
  const lastDay = new Date(t.getFullYear(), t.getMonth() + 1, 0).getDate();
  t.setDate(Math.min(day, lastDay));  // clamp (Jan 31 + 1mo → Feb 28)
  return t;
}

function screeningStatus(s, today) {
  if (!s.last) return {status: s.cadence ? 'never done' : 'not done', due: null};
  if (!s.cadence) return {status: 'one-time done', due: null};
  const due = addMonths(s.last, s.cadence);
  return due <= today ? {status: 'OVERDUE', due} : {status: 'scheduled', due};
}

function showScreening() {
  activeCat = null;
  renderSidebar(document.getElementById('search-box').value);
  document.getElementById('unified-btn').classList.remove('active');
  document.getElementById('longevity-btn').classList.remove('active');
  document.getElementById('screening-btn').classList.add('active');
  document.getElementById('cat-title').textContent = 'Screening Calendar';
  document.getElementById('view-toggle').style.display = 'none';
  document.getElementById('table-wrap').style.display = 'none';
  document.getElementById('bm-search').style.display = 'none';
  document.getElementById('row-count').style.display = 'none';
  document.getElementById('data-table').style.display = 'none';
  document.getElementById('empty-state').style.display = 'none';

  const panel = document.getElementById('insights-panel');
  const scr = DATA.screenings || [];
  if (!scr.length) {
    panel.innerHTML = `<div class="protocol-text" style="padding:12px 4px;">`
      + `<p>No screening calendar yet. Create + seed it:</p>`
      + `<pre style="background:var(--card);padding:10px;border-radius:6px;">`
      + `python3 screening_calendar.py --init</pre></div>`;
    panel.classList.add('visible');
    return;
  }
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const rows = scr.map(s => ({...s, ...screeningStatus(s, today)}));
  const prioRank = {high: 0, medium: 1, low: 2};
  const statusRank = {'OVERDUE': 0, 'never done': 1, 'not done': 2, 'scheduled': 3, 'one-time done': 4};
  const far = new Date(8640000000000000);
  rows.sort((a, b) => (statusRank[a.status] - statusRank[b.status])
    || (prioRank[a.prio] ?? 9) - (prioRank[b.prio] ?? 9)
    || ((a.due || far) - (b.due || far)));

  const col = {'OVERDUE': '#e74c3c', 'never done': '#f39c12', 'not done': '#f39c12',
               'scheduled': '#2ecc71', 'one-time done': '#2ecc71'};
  const actionable = rows.filter(r => r.status === 'OVERDUE' || r.status === 'never done').length;
  // Format from LOCAL components — toISOString() would shift the day in +UTC zones.
  const pad = n => String(n).padStart(2, '0');
  const iso = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  let html = `<div class="protocol-text" style="padding:8px 4px 40px;">`
    + `<p style="color:var(--muted);font-size:12px;margin-bottom:14px;">`
    + `${rows.length} screenings · <b>${actionable}</b> need action (overdue or never done). `
    + `Cadences are starter defaults — record completions with `
    + `<code>python3 screening_calendar.py --set "&lt;name&gt;" &lt;date&gt;</code>.</p>`
    + `<table class="snap-table"><thead><tr>`
    + `<th>Status</th><th>Next due</th><th>Last done</th><th>Every</th><th>Pri</th><th>Screening</th>`
    + `</tr></thead><tbody>`;
  rows.forEach(r => {
    const every = r.cadence ? `${r.cadence < 12 ? r.cadence + 'mo' : (r.cadence / 12) + 'y'}` : 'once';
    html += `<tr>`
      + `<td style="font-weight:600;color:${col[r.status]}">${r.status}</td>`
      + `<td class="snap-date">${r.due ? iso(r.due) : '—'}</td>`
      + `<td class="snap-date">${r.last || '—'}</td>`
      + `<td class="snap-prior">${every}</td>`
      + `<td>${r.prio}</td>`
      + `<td>${escH(r.name)}<div style="color:var(--muted);font-size:11px;">${escH(r.rationale || r.domain)}</div></td>`
      + `</tr>`;
  });
  html += `</tbody></table></div>`;
  panel.innerHTML = html;
  panel.classList.add('visible');
}

// Deselect unified/longevity/screening btns when selecting a category
const _origSelectCat = selectCat;
selectCat = function(c) {
  document.getElementById('unified-btn').classList.remove('active');
  document.getElementById('longevity-btn').classList.remove('active');
  document.getElementById('screening-btn').classList.remove('active');
  _origSelectCat(c);
};

// ── Table ────────────────────────────────────────────────────────────────────
let currentBms = [];
let activeDates = [];

function renderTable(bms) {
  currentBms = bms;

  activeDates = DATA.dates.filter((_, i) =>
    bms.some(b => DATA.res[String(b.id)]?.[i] !== undefined)
  );
  const dateIdxMap = {};
  activeDates.forEach(d => { dateIdxMap[d] = DATA.dates.indexOf(d); });

  // thead
  const thead = document.getElementById('thead');
  const tr = document.createElement('tr');
  tr.innerHTML = `<th class="col-name">Biomarker</th><th class="col-spec">Specimen</th><th class="col-unit">Unit</th>`;
  activeDates.forEach(d => {
    const [yr, mo, dy] = d.split('-');
    tr.innerHTML += `<th class="col-date"><span class="yr">${yr}</span><span class="md">${mo}-${dy}</span></th>`;
  });
  thead.innerHTML = '';
  thead.appendChild(tr);

  // tbody
  const tbody = document.getElementById('tbody');
  tbody.innerHTML = '';
  bms.forEach(b => {
    const tr = document.createElement('tr');
    tr.dataset.enLower = b.en.toLowerCase();
    tr.dataset.ruLower = b.ru.toLowerCase();
    let cells = `
      <td class="col-name" onclick="openModal(${b.id})">
        <div class="bm-name">${escH(b.en)}</div>
        ${b.en !== b.ru ? `<div class="bm-ru">${escH(b.ru)}</div>` : ''}
      </td>
      <td class="col-spec">${escH(b.spec)}</td>
      <td class="col-unit">${escH(b.unit)}</td>`;
    activeDates.forEach(d => {
      const di = dateIdxMap[d];
      const v = DATA.res[String(b.id)]?.[di];
      if (v !== undefined) {
        cells += `<td class="col-date has-val">${escH(String(v))}</td>`;
      } else {
        cells += `<td class="col-date">·</td>`;
      }
    });
    tr.innerHTML = cells;
    tbody.appendChild(tr);
  });

  document.getElementById('empty-state').style.display = 'none';
  document.getElementById('data-table').style.display = '';
  updateRowCount();
}

function filterRows(v) {
  const q = v.toLowerCase();
  document.querySelectorAll('#tbody tr').forEach(tr => {
    const match = !q || tr.dataset.enLower.includes(q) || tr.dataset.ruLower.includes(q);
    tr.classList.toggle('filtered', !match);
  });
  updateRowCount();
}

function updateRowCount() {
  const total = document.querySelectorAll('#tbody tr').length;
  const visible = document.querySelectorAll('#tbody tr:not(.filtered)').length;
  document.getElementById('row-count').textContent =
    visible === total ? `${total} biomarkers` : `${visible} / ${total}`;
}

// ── Chart modal ──────────────────────────────────────────────────────────────
let chartInst = null;

function openModal(bmId) {
  const b = DATA.bms.find(x => x.id === bmId);
  if (!b) return;

  const resMap = DATA.res[String(bmId)] || {};
  const points = [];
  DATA.dates.forEach((d, i) => {
    if (resMap[i] !== undefined) points.push({ date: d, value: resMap[i] });
  });

  document.getElementById('modal-title').innerHTML = `
    <div class="name-en">${escH(b.en)}</div>
    ${b.en !== b.ru ? `<div class="name-ru">${escH(b.ru)}</div>` : ''}
    <div class="meta">${[b.spec, b.unit].filter(Boolean).join(' · ')}</div>`;

  // Chart — only numeric points
  const numPts = points.filter(p => !isNaN(parseFloat(p.value)));
  if (chartInst) { chartInst.destroy(); chartInst = null; }
  if (numPts.length >= 2) {
    const ctx = document.getElementById('chart-canvas').getContext('2d');
    // Personalized bounds at each point's age-at-draw (age-banded → stepped line).
    const bandAt = numPts.map(p => personalBand(b.pr, ageAt(p.date)));
    const stepped = !b.pr || b.pr.m !== 'interp';  // interp markers slope smoothly
    const boundDs = (label, idx, color) => {
      const arr = bandAt.map(bd => (bd && bd[idx] != null) ? bd[idx] : null);
      if (!arr.some(v => v != null)) return null;
      return { label, data: arr, borderColor: color, borderDash: [5, 4],
               borderWidth: 1.5, pointRadius: 0, fill: false, stepped,
               tension: 0, spanGaps: true };
    };
    const datasets = [{
          label: 'value',
          data: numPts.map(p => parseFloat(p.value)),
          borderColor: '#5b8dee',
          backgroundColor: 'rgba(91,141,238,.12)',
          pointBackgroundColor: '#5b8dee',
          pointRadius: 4,
          tension: 0.3,
          fill: true,
    }];
    const loDs = boundDs('your range (low)', 0, 'rgba(46,204,113,.6)');
    const hiDs = boundDs('your range (high)', 1, 'rgba(243,156,18,.6)');
    if (loDs) datasets.push(loDs);
    if (hiDs) datasets.push(hiDs);
    // Fallback: flat lab/population reference lines when there's no personalized band.
    if (!b.pr && (b.rl != null || b.rh != null)) {
      const flat = (label, y, color) => ({ label, data: numPts.map(() => y),
        borderColor: color, borderDash: [5, 4], borderWidth: 1.5, pointRadius: 0,
        fill: false, tension: 0 });
      if (b.rl != null) datasets.push(flat('ref (low)', b.rl, 'rgba(46,204,113,.5)'));
      if (b.rh != null) datasets.push(flat('ref (high)', b.rh, 'rgba(243,156,18,.5)'));
    }
    chartInst = new Chart(ctx, {
      type: 'line',
      data: { labels: numPts.map(p => p.date), datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: datasets.length > 1, labels: { color: '#8890a8', font: { size: 10 }, boxWidth: 18 } }, tooltip: {
          callbacks: { label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y}${b.unit ? ' ' + b.unit : ''}` }
        }},
        scales: {
          x: { ticks: { color: '#8890a8', font: { size: 10 } }, grid: { color: '#2d3045' } },
          y: { ticks: { color: '#8890a8', font: { size: 10 } }, grid: { color: '#2d3045' } }
        }
      }
    });
    document.getElementById('chart-container').style.display = '';
  } else {
    document.getElementById('chart-container').style.display = 'none';
  }

  // Value table
  const vt = document.getElementById('val-table');
  vt.innerHTML = `<tr><th>Date</th><th>Value</th></tr>` +
    points.map(p => `<tr><td>${p.date}</td><td>${escH(String(p.value))}${b.unit ? ' <span style="color:var(--muted);font-size:11px">' + escH(b.unit) + '</span>' : ''}</td></tr>`).join('');

  document.getElementById('modal-overlay').classList.add('open');
}

function closeModal() {
  document.getElementById('modal-overlay').classList.remove('open');
  if (chartInst) { chartInst.destroy(); chartInst = null; }
}

document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });

// ── Utils ────────────────────────────────────────────────────────────────────
function escH(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Init ─────────────────────────────────────────────────────────────────────
renderSidebar();
</script>
</body>
</html>
"""


def main():
    if not DB.exists():
        print(f'Error: {DB} not found. Run: python3 init_db.py')
        sys.exit(1)

    print('Loading data...')
    categories, biomarkers, bm_cats, results, insights, rsids, unified, screenings = load_data()
    data = build_json(categories, biomarkers, bm_cats, results, insights, rsids, unified, screenings)

    json_str = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    html = HTML_TEMPLATE.replace('__DATA__', json_str)
    OUT.write_text(html, encoding='utf-8')

    # Export unified protocol to markdown
    if unified:
        con = sqlite3.connect(DB)
        ts = con.execute('SELECT updated_at FROM unified_protocol ORDER BY id DESC LIMIT 1').fetchone()[0]
        con.close()
        md = f'# Unified Master Protocol\n\n_Last updated: {ts}_\n\n```\n{unified}\n```\n'
        PROTOCOL_OUT.write_text(md, encoding='utf-8')
        print(f'  → {PROTOCOL_OUT} ({len(md)} chars)')

    cats = len(data['cats'])
    bms = len(data['bms'])
    dates = len(data['dates'])
    res = sum(len(v) for v in data['res'].values())
    print(f'Done → {OUT}')
    print(f'  {cats} categories, {bms} biomarkers, {dates} dates, {res} values')
    print(f'  Open: open {OUT}')


if __name__ == '__main__':
    main()
