# Health Tracker

A local, private, dependency-free tracker for your lab results, DNA, and imaging reports.
Drop your data in, and it builds a single self-contained `viewer.html` with charts,
genetic-variant interpretations, per-category insights, and a consolidated protocol.

Designed to be driven with [Claude Code](https://claude.com/claude-code) (or any coding
agent): **you don't write parsers** — you ask Claude to read your messy lab PDFs/screenshots
and import them. Everything is Python standard library; nothing leaves your machine.

## Quick start

```bash
python3 init_db.py     # create labs.db + a small synthetic example
python3 viewer.py      # build viewer.html
open viewer.html       # explore the example immediately
```

Then replace the example with your own data:

```bash
# DNA (23andMe/AncestryDNA raw export, or a VCF — annotated or raw):
python3 import_dna.py path/to/your_genome.txt
python3 viewer.py

# Labs & imaging: ask Claude — "import the lab report in inputs/ into labs.db"
# (Claude reads the file, screens for unit errors, and inserts the values. See CLAUDE.md.)
```

## What you get

- **Interactive viewer** — every biomarker's history as a chart, reference/optimal-range
  lines, a Longevity Dashboard of the mortality-moving markers, and a Screening Calendar.
- **DNA variants** — your genotypes matched against a curated catalogue (pharmacogenomics,
  longevity, nutrition, fitness, cardiovascular/iron), with plain-language interpretations
  and APOE ε genotype. The catalogue is just the *interpreted highlights* — your genome file
  stays fully queryable: `python3 lookup_variant.py rs1801133` looks up **any** variant on
  demand. Extend the catalogue in `data/known_variants.json`.
- **Insights & protocol** — Claude authors a per-category assessment and a master protocol
  grounded in *your* values and variants.
- **LLM exports** — `export_for_llm.py` / `export_summary_for_llm.py` dump your data to one
  Markdown file to paste into any chat.

## Privacy

`labs.db`, `inputs/`, and the generated exports are git-ignored. Only the code and the
synthetic seed data are meant to be shared. This is not medical advice — genetic effects
here are small, population-dependent leans, not diagnoses. Confirm anything actionable
(especially pharmacogenomics) with a clinician and clinical-grade testing.

## Files

`schema.sql` (data model) · `init_db.py` (create+seed) · `viewer.py` (build the HTML) ·
`import_dna.py` (DNA; VCF or 23andMe, matches by rsID or, for raw VCFs, chrom:pos with genome-
build auto-detection) · `lookup_variant.py` (query any variant from your genome on demand) ·
`enrich_variants.py` (backfill variant coordinates from Ensembl) ·
`inspect_import.py` (unit-error screen) · `seed_ref_ranges.py` · `screening_calendar.py` ·
`export_*_for_llm.py` · `data/` (seed + variant catalogue) · `CLAUDE.md` (the agent playbook).
