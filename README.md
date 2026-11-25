# DNA and labs to protocol

Turn your lab results, DNA, and imaging reports into a single self-contained `viewer.html`
— charts, genetic-variant interpretations, a short list of findings (what is off, what "good"
means, what to do about it, and whether it is working), and a daily routine — built and kept
current by a coding agent.

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
   > imaging reports. Then propose findings grounded in my numbers (show me before loading
   > them), write the daily routine, and build the viewer.

   Then `open viewer.html`.

The agent creates the database, screens every lab value before inserting it (wrong units,
decimal shifts, a value far outside everything that marker has read), interprets your genotypes
(and looks up any variant on demand), proposes findings and a routine grounded in *your* results,
and regenerates the viewer after each change. `check_all.py` catches the layers disagreeing.

## What you get

- **Interactive viewer** — every biomarker's history as a chart, reference/optimal-range
  lines, a Longevity Dashboard of the mortality-moving markers, and a Screening Calendar.
- **DNA variants** — your genotypes matched against a curated catalogue (pharmacogenomics,
  longevity, nutrition, fitness, cardiovascular/iron), with plain-language interpretations
  and APOE ε genotype, and for each the effect allele and how many copies you carry (unknown
  stays unknown). The catalogue is just the *interpreted highlights* — your genome file
  stays fully queryable, so the agent can look up **any** variant on demand and add it.
- **Findings** — each one a single problem with a target, an exit criterion and its own
  prescription (take / avoid / diet / activity / lifestyle / refer, each with its reason). Whether
  each marker is on target, and which way it moved, is worked out from your results every time —
  a trend needs two comparable draws from the same lab, and a move inside the analyte's noise is
  not called a trend.
- **Daily routine** — the doses (written once), the general rules, and a medicines-and-genes sheet
  for prescribers; checked against the findings so nothing prescribed goes undosed.
- **Screening plan** — what to test next, grouped by trip (blood draw, imaging, visits, at home)
  and ranked by the finding each test serves.
- **Category narratives** — an optional per-category read of the labs and what the DNA adds.
- **LLM exports** — one-file Markdown dumps of your data (`export_for_llm.py`, or `--summary`
  for a condensed version) to paste into any chat.

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
Chart.js is vendored and inlined, so the generated viewer works fully offline and makes no
network requests.

## Privacy

`labs.db`, `inputs/`, your import scripts, backups and generated exports are git-ignored — only
the code and the synthetic seed are shared. `labs.db` is the only copy of your data once you import
it: `python3 init_db.py --backup` keeps gzipped snapshots in `backups/`. The one outbound network call sends a public rsID (never your genotypes) to
Ensembl to locate an uncatalogued variant; `--offline` skips it. **Not medical advice** — genetic
effects here are small, population-dependent leans, not diagnoses; confirm anything actionable
(especially pharmacogenomics) with a clinician and clinical-grade testing.

## Files

`CLAUDE.md` (the agent manual) · `schema.sql` (data model) · `init_db.py` (create; `--empty`
for your own data; `--backup`) · `viewer.py` (build the HTML) · `issues.py` (findings) ·
`protocol_view.py` (the routine and its checks) · `rcv.py` (noise floors) · `inspect_import.py`
(the import screen) · `import_dna.py` (DNA; VCF or 23andMe) · `lookup_variant.py` (any variant on
demand) · `enrich_variants.py` (variant coordinates from Ensembl) · `check_variants.py` ·
`seed_ref_ranges.py` · `screening_calendar.py` · `export_for_llm.py` · `check_all.py` (every
self-check, the consistency gates, the reports) · `.claude/skills/` (import-labs, the routine, a
category narrative) · `data/` (seed, variant catalogue, `user_facts.example.md`) · `vendor/`
(Chart.js, inlined) · `AGENTS.md` (pointer for non-Claude agents).
