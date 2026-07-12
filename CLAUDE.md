# CLAUDE.md

Guidance for Claude Code working in this repo.

## What this is

A personal health-data tracker. You bring lab results (any text format), imaging/report
text, and DNA (a VCF or a 23andMe/AncestryDNA raw file); Claude imports them into a local
SQLite database (`labs.db`), and `viewer.py` renders a single self-contained `viewer.html`
with charts, genetic variants, per-category insights, and a master protocol.

**`labs.db` is the source of truth.** It's git-ignored and rebuilt by `init_db.py`. All
edits happen against `labs.db` directly (or against `data/seed.json` if you want them in
the seeded example). No external dependencies — Python stdlib only; Chart.js loads from a
CDN in the generated HTML.

## Commands

```bash
python3 init_db.py                 # create labs.db from schema.sql + seed the example (--force to overwrite)
python3 viewer.py                  # regenerate viewer.html + master_protocol.md  (run after ANY db change)
python3 import_dna.py <file>       # match a VCF or 23andMe raw file → variants table
python3 export_for_llm.py          # full lab time-series → labs_export.md (feed to an LLM)
python3 export_summary_for_llm.py  # condensed per-biomarker summary → labs_summary.md
python3 screening_calendar.py      # what preventive screening is due next
open viewer.html
```

Self-checks (no writes): `python3 inspect_import.py --self-check`, `python3 import_dna.py
--self-check`, `python3 seed_ref_ranges.py --self-check`, `python3 screening_calendar.py
--self-check`.

## Data model (`labs.db`)

`categories` · `biomarkers` (name, specimen, unit, ref_low/high, opt_low/high) ·
`biomarker_categories` (junction) · `test_results` (biomarker_id, date, **value is TEXT** —
preserves `<10`, `Negative`) · `category_insights` (the Claude-authored layer) · `variants`
(from your DNA) · `unified_protocol` (one master blob) · `screenings` · `documents`
(imaging/report text — LLM context, not viewer-rendered). Full schema in `schema.sql`.

Categories are a deliberately small set of 16 (edit `schema.sql` to change them).
Biomarker lookup key is `(name_en, specimen_en, unit)` — name alone isn't unique
(e.g. Creatinine exists for blood and urine).

## Importing labs (you read the report; no parser ships)

The user drops a lab report (text, PDF-transcript, or a photo you transcribe) in `inputs/`.
You extract the values — there's no format parser, that's the point.

1. For each analyte: find its biomarker by `(name_en, specimen_en, unit)`, or `INSERT` a new
   one. Set `ref_low`/`ref_high` from the reference range **the report itself states** (most
   do). Attach `biomarker_categories`.
2. Build the proposed `(biomarker_id, date, value)` rows. Keep `value` as TEXT.
3. **SCREEN before inserting** — this dataset's recurring bug is values in the wrong unit or
   with decimal-shift typos:
   ```python
   from inspect_import import inspect, report
   flags = inspect(conn, rows)
   report(flags)          # resolve EVERY 'SUSPECT' before committing — never insert unreviewed values
   ```
   A value in a *plausible* magnitude can still be a unit swap the screen won't catch —
   cross-check sibling markers and the reference range by hand.
4. `INSERT` into `test_results`, deduping on `(biomarker_id, date)`.
5. `python3 viewer.py`.

## Importing DNA

```bash
python3 import_dna.py inputs/genome.txt          # 23andMe/AncestryDNA raw OR .vcf; auto-detected
python3 import_dna.py inputs/genome.vcf --build 38   # force build if the VCF header is ambiguous
```
It matches the file against `data/known_variants.json` (a curated ~46-SNP catalogue spanning
pharmacogenomics, longevity, nutrition, fitness, and cardiovascular/iron risk), tries both
DNA strands, handles APOE's two-SNP ε genotype, and writes interpreted rows into `variants`.
`example_genome.txt` is a synthetic file for testing.

**VCF matching:** by rsID (the ID column) when present; when the ID column is blank (`.`, as in
a raw whole-genome VCF), it falls back to matching by chromosome + position. Positions are
build-specific, so the genome build (GRCh37/hg19 vs GRCh38/hg38) is auto-detected from the VCF
header (`##contig` lengths or a `##reference`/assembly string) — override with `--build 37|38`.
23andMe/AncestryDNA raw files are always GRCh37.

**To cover more variants:** add entries to `data/known_variants.json` (see its `_README`):
`rsID → {gene, category (a name_en from schema.sql), note, genotypes{gt: interpretation},
risk[]}`. Then run `python3 enrich_variants.py` to backfill `chrom` + dual-build `pos` from
Ensembl (needed only for the position fallback; rsID matching works without it). Keep
interpretations honest — common-variant effects are small and often population-specific.
Re-run `import_dna.py` + `viewer.py`.

## Importing imaging / reports

Transcribe the finding into `documents` (`title, doc_date, doc_type, category_id, body`,
optional `file_path`). Then fold the clinically relevant conclusion into the matching
`category_insights.insight` so it surfaces in the viewer. `documents` itself is LLM context,
not a viewer panel.

## Authoring category_insights

One row per category. Write into these columns (the viewer composes a Protocol tab from the
five structured domains, and an Assessment tab from the rest):
- `insight` — lab-based narrative (**required**, NOT NULL).
- `insight_dna` — what the person's variants add (optional).
- `supplements` / `diet` / `activity` / `lifestyle` / `checkup_schedule` — the protocol.
- `concordance` — for genomically-loaded categories: one row per line,
  `mechanism|predicted|observed|verdict`, verdict ∈ `CONFIRMS`/`PARTIAL`/`UNRESOLVED`/
  `CONTRADICTS`/`FAVORABLE` (bare keyword only). Rendered as a "do the labs confirm the
  genetic prediction?" table.

Ground every claim in the person's actual values and variants. Use `date('now')` for
`updated_at`. The seeded rows (categories 1, 4, 5, 15) are worked examples — follow their
shape, replace their content.

## Synthesizing the master protocol

`unified_protocol` is one hand-curated blob consolidating the active per-category protocols
into a single document (supplements by tier, diet, activity, lifestyle, monitoring,
pharmacogenomic notes). It is NOT auto-composed — when you change a per-category protocol,
reconcile the unified blob too (grep it for the supplement/rule/number that changed).
Use `═══ SECTION ═══` and `─── sub ───` separators (the viewer styles them). Only include
items actually in use.

## Conventions

- After ANY db change, run `python3 viewer.py`.
- `test_results.value` is TEXT, not numeric.
- No dependencies beyond the Python standard library.
- **Privacy:** this is personal medical data. Keep `labs.db`, `inputs/`, and generated
  exports out of git (see `.gitignore`); share only the code and the synthetic seed.
