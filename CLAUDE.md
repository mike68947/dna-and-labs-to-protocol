# CLAUDE.md

Guidance for Claude Code working in this repo.

## What this is

A personal health-data tracker. You bring lab results (any text format), imaging/report
text, and DNA (a VCF or a 23andMe/AncestryDNA raw file); Claude imports them into a local
SQLite database (`labs.db`), and `viewer.py` renders a single self-contained `viewer.html`
with charts, genetic variants, per-category insights, and a master protocol.

**`labs.db` is the source of truth, and once it holds your data nothing can rebuild it** — it is
git-ignored, and `init_db.py` only creates the demo or an empty database. Back it up before every
session that writes to it: `python3 init_db.py --backup` (gzipped into `backups/`, newest 14 kept;
restore with `gunzip -c backups/labs-<stamp>.db.gz > labs.db`). All
edits happen against `labs.db` directly (or against `data/seed.json` if you want them in
the seeded example). Python standard library only, plus optional `bcftools` for fast VCF
queries (pure-Python fallback); Chart.js is vendored (`vendor/chart.umd.min.js`, pinned 4.5.1)
and inlined into the generated HTML — the viewer makes no network requests.

## Commands

```bash
python3 init_db.py                 # create labs.db with a synthetic demo (--force to overwrite)
python3 init_db.py --empty         # clean start for the user's OWN data: schema + categories, no demo rows
python3 viewer.py                  # regenerate viewer.html + master_protocol.md  (run after ANY db change)
python3 import_dna.py <file>       # match a VCF or 23andMe raw file → variants table
python3 export_for_llm.py          # full lab time-series → labs_export.md (feed to an LLM)
python3 export_for_llm.py --summary  # condensed per-biomarker summary → labs_summary.md
python3 screening_calendar.py      # what preventive screening is due next (lab-backed rows self-update from test_results)
python3 check_all.py               # every --self-check + CONSISTENCY gate + stale/freshness reports (run after viewer.py)
open viewer.html
```

Every script has a `--self-check` (no writes); `python3 check_all.py` runs them all, then the
**CONSISTENCY** gate (foreign-key orphans, future-dated results — exit 1) and the **REPORTS**
(insights older than their labs, generated files older than `labs.db` — informational, never
gate). Keep that split when adding a check: a gate that cries wolf gets ignored.

**Typical flow for the user's own data:** `python3 init_db.py --empty` → they drop their lab,
DNA, and imaging files in `inputs/` → import the labs (below), `python3 import_dna.py` their
genome, transcribe the imaging into `documents` → author `category_insights` + the master
protocol → `python3 viewer.py` → `python3 check_all.py`.

## Data model (`labs.db`)

`categories` · `biomarkers` (name, specimen, unit, ref_low/high, opt_low/high) ·
`biomarker_categories` (junction) · `test_results` (biomarker_id, date, **value is TEXT** —
preserves `<10`, `Negative`) · `category_insights` (the Claude-authored layer) · `variants`
(from your DNA) · `unified_protocol` (one master blob) · `screenings` · `documents`
(imaging/report text — LLM context, not viewer-rendered). Full schema in `schema.sql`.

Categories are a deliberately small set of 16 (edit `schema.sql` to change them).
Biomarker lookup key is `(name_en, specimen_en, unit)` — name alone isn't unique
(e.g. Creatinine exists for blood and urine).

## Importing labs and clinical documents

Use the `import-labs` skill (`.claude/skills/import-labs/SKILL.md`): back up, read the report
(draw date, lab, specimen, units), insert only through
`inspect_import.commit_import(conn, rows, lab=…, resolved={…})` from a dated
`imports/YYYY-MM-DD_<lab>.py`, then `viewer.py` → `check_all.py`.

## Importing DNA

```bash
python3 import_dna.py inputs/genome.txt          # 23andMe/AncestryDNA raw OR .vcf; auto-detected
python3 import_dna.py inputs/genome.vcf --build 38   # force build if the VCF header is ambiguous
```
It matches the file against `data/known_variants.json` (a curated ~46-SNP catalogue spanning
pharmacogenomics, longevity, nutrition, fitness, and cardiovascular/iron risk), tries both
DNA strands (except palindromic A/T and C/G SNPs, which are taken as reported — a flip there is
undetectable), handles APOE's two-SNP ε genotype, and writes interpreted rows into `variants`.
Zygosity is `het` / `hom-alt` / `hom-ref` when the entry has a `risk` list, else plain `hom`
(homozygous, effect allele unknown — don't read `hom` as "reference"). Non-PASS VCF calls are skipped.
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

## DNA analysis phase (query any variant, not just the catalogue)

The catalogue is the *interpreted highlights*, not the limit. **The genome file in `inputs/`
is the source of truth for every genotype — keep it there** (it's git-ignored). `import_dna.py`
only writes the curated matches to `variants`; the rest of the genome stays in the file,
queryable on demand. To analyze a topic that needs SNPs beyond the catalogue:

1. **Query** any rsID(s) straight from the file — no re-import, no DB bloat:
   ```bash
   python3 lookup_variant.py rs1801133 rs662 rs1229984   # genotype + interpretation if catalogued
   ```
   It reads the VCF with bcftools when available (fast random access on a bgzipped, tabix-indexed
   file) or a pure-Python scan otherwise, and auto-detects the build. For an un-annotated VCF it
   resolves any requested rsID's position from Ensembl on demand — so you're not limited to
   catalogued rsIDs (`--offline` skips the network and uses catalogue coordinates only). A
   resolved position with no record in the VCF is reported as homozygous reference. Read-only —
   it never writes the DB.
2. **Interpret** the genotypes (your own knowledge, or fetch context from Ensembl/ClinVar).
3. **Promote** anything worth surfacing into the viewer — insert an interpreted row:
   ```sql
   INSERT INTO variants (category_id, rsid, gene, relevance, genotype, zygosity)
   VALUES (:cid, :rsid, :gene, :interpretation, :genotype, :zygosity);
   ```
   To make it a *reusable, auto-interpreted* highlight (so future imports label it), add the
   SNP to `data/known_variants.json` and run `python3 enrich_variants.py`. Then `python3 viewer.py`.

Out of scope (upgrade path): gene-region scanning and novel/rare-variant pathogenicity scoring
(bcftools + snpEff + Ensembl VEP) — heavy and WGS-only; add it only if you need to discover
variants that don't already have a known rsID.

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

- After ANY db change, run `python3 viewer.py`, then `python3 check_all.py`.
- `test_results.value` is TEXT, not numeric.
- Python standard library only, plus optional `bcftools` for fast VCF querying (auto-detected;
  a pure-Python scan runs when it's absent). Chart.js is vendored at `vendor/chart.umd.min.js`
  (pinned 4.5.1, MIT) and inlined — the viewer makes no network requests. Don't reintroduce a CDN
  tag (the page holds the whole dataset); to upgrade, re-download a pinned build into `vendor/`
  and re-run `viewer.py`.
- **Privacy:** this is personal medical data. Keep `labs.db`, `inputs/`, `imports/`, and generated
  exports out of git (see `.gitignore`); share only the code and the synthetic seed.
