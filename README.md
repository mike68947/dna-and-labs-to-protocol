# DNA and labs to protocol

Turn your lab results, DNA, and imaging reports into a single self-contained `viewer.html`
— charts, genetic-variant interpretations, per-category insights, and a personalized
protocol — built and kept current by a coding agent.

## How to use it

This repo is meant to be handed to a coding agent like [Claude Code](https://claude.com/claude-code)
(or Cursor, etc.). **You bring the data; the agent does the parsing, importing, and analysis** —
it reads `CLAUDE.md` for exactly how. You don't write parsers or convert anything.

1. **Point your agent at the repo** — give it the link and ask it to clone:
   `https://github.com/mike68947/dna-and-labs-to-protocol`

2. **Put your data in `inputs/`** — any format; the agent reads it as-is:
   - **Labs** — lab-report PDFs, screenshots, pasted text, portal exports. Any layout, any language.
   - **DNA** — a VCF (`.vcf` / `.vcf.gz`, annotated or raw) or a 23andMe / AncestryDNA raw `.txt`.
   - **Imaging & reports** — radiology / ultrasound / cardiology / consult report text.

3. **Ask the agent to build it.** For example:

   > Read CLAUDE.md, then start a clean database (`init_db.py --empty`). Import everything in
   > `inputs/` into labs.db — screen the labs for unit errors, match my DNA, and transcribe the
   > imaging reports. Then author per-category insights and a master protocol grounded in my
   > numbers, and build the viewer.

   Then `open viewer.html`.

The agent creates the database, screens every lab value for unit/decimal errors before inserting,
interprets your genotypes (and looks up any variant on demand), writes insights and a protocol
grounded in *your* results, and regenerates the viewer after each change.

## What you get

- **Interactive viewer** — every biomarker's history as a chart, reference/optimal-range
  lines, a Longevity Dashboard of the mortality-moving markers, and a Screening Calendar.
- **DNA variants** — your genotypes matched against a curated catalogue (pharmacogenomics,
  longevity, nutrition, fitness, cardiovascular/iron), with plain-language interpretations
  and APOE ε genotype. The catalogue is just the *interpreted highlights* — your genome file
  stays fully queryable, so the agent can look up **any** variant on demand and add it.
- **Insights & protocol** — a per-category assessment and a consolidated master protocol,
  grounded in your values and variants (not generic advice).
- **LLM exports** — one-file Markdown dumps of your data (`export_for_llm.py` /
  `export_summary_for_llm.py`) to paste into any chat.

## Try it without your data

Want to see it first? The default init seeds a small synthetic example so the viewer renders
immediately:

```bash
python3 init_db.py     # create labs.db + a tiny synthetic demo
python3 viewer.py      # build viewer.html
open viewer.html
```

You can also drive it by hand: `python3 import_dna.py inputs/your_genome.vcf` matches your DNA,
and `python3 lookup_variant.py rs1801133` looks up any variant. (Labs and imaging still go
through the agent — there's no parser to run by hand.)

## Runs on

Python's standard library, plus optional [`bcftools`](https://samtools.github.io/bcftools/) for
fast VCF queries (a pure-Python scan is used when it's absent). No build step, no services.

## Privacy

`labs.db`, `inputs/`, and generated exports are git-ignored — only the code and the synthetic
seed are shared. The one outbound network call sends a public rsID (never your genotypes) to
Ensembl to locate an uncatalogued variant; `--offline` skips it. **Not medical advice** — genetic
effects here are small, population-dependent leans, not diagnoses; confirm anything actionable
(especially pharmacogenomics) with a clinician and clinical-grade testing.

## Files

`CLAUDE.md` (the agent playbook) · `schema.sql` (data model) · `init_db.py` (create; `--empty`
for your own data) · `viewer.py` (build the HTML) · `import_dna.py` (DNA; VCF or 23andMe) ·
`lookup_variant.py` (query any variant on demand) · `enrich_variants.py` (variant coordinates
from Ensembl) · `inspect_import.py` (unit-error screen) · `seed_ref_ranges.py` ·
`screening_calendar.py` · `export_*_for_llm.py` · `data/` (seed + variant catalogue).
