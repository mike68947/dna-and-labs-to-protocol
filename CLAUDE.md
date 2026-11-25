# CLAUDE.md

The agent manual for this repo (other agents read it through `AGENTS.md`). Each rule is one line,
with its reason in a few words.

A personal health-data tracker. The user brings lab results (any format), imaging and consult
reports, and DNA (a VCF or a 23andMe / AncestryDNA raw file). You import them into `labs.db`
(SQLite), keep a short list of **findings** — what is wrong, what "good" means, what is
prescribed, and whether it is working — plus a **daily routine** that holds the doses, and
`viewer.py` renders it all into one self-contained `viewer.html`.

## When the user wants X, start at Y

| The user wants… | Start at |
|---|---|
| to start with their own data | `python3 init_db.py --empty`, then copy `data/user_facts.example.md` to `inputs/user_facts.md` and fill it in |
| to add a lab report (PDF, photo, table) or an imaging / consult / procedure report | the `import-labs` skill |
| to import their DNA | `python3 import_dna.py inputs/<file>` (`--build 37\|38` if the VCF header is ambiguous) |
| to look up any variant, catalogued or not | `python3 lookup_variant.py rs… rs…` (`--offline`: no Ensembl) |
| to see the findings and whether each is moving toward its target | `python3 issues.py` (`--show KEY` for one) |
| to add findings | write them as JSON under `inputs/` (shape below) → `python3 issues.py --load inputs/<file>.json` |
| to rescore, close or retarget a finding | `issues.py --set KEY field=value --why "…"` · `--marker KEY BIOMARKER_ID --low … --high … --why "…"` |
| to change what is prescribed for a finding | `issues.py --action KEY KIND NAME [--on BIOMARKER_ID] [--detail …] --why "…"` · `--drop-action KEY KIND NAME --why "…"` |
| to write or change the daily routine (a dose, a general rule, the medicines-and-genes sheet) | the `regenerate-unified-protocol` skill |
| to refresh a category's narrative | the `regenerate-category` skill |
| to give a marker a noise floor | `python3 rcv.py --set BIOMARKER_ID CVI --source "…"` |
| to know what to test next, or what is overdue | `python3 screening_calendar.py --plan` |
| to record a screening that was done | `python3 screening_calendar.py --set "<name>" YYYY-MM-DD "<finding>"` |
| to change a cadence or add a screening item | edit `SEED` in `screening_calendar.py` → `python3 screening_calendar.py --init` |
| to know whether anything disagrees | `python3 check_all.py` |
| to look at it all | `python3 viewer.py && open viewer.html` |
| to feed it to another LLM | `python3 export_for_llm.py` (`--summary` for the short form) and `master_protocol.md` |
| to back up, or undo damage | `python3 init_db.py --backup` · restore: `gunzip -c backups/labs-<stamp>.db.gz > labs.db` |

**A first build for a new user:** `init_db.py --empty` → import the labs (skill) and the DNA →
propose findings from what is off (show them to the user before loading) → write the routine →
optionally a narrative per category → `viewer.py` → `check_all.py`.

## Rules

### Every change
- `labs.db` is the only copy of the user's data: nothing rebuilds it and git ignores it — `python3 init_db.py --backup` before any session that writes to it.
- After any DB change: `python3 viewer.py`, then `python3 check_all.py`.
- `check_all.py` gates (exit 1) only when two layers disagree about the same fact; a work queue is a report and never gates — a gate that cries wolf gets ignored.
- When you add a rule, add the check that enforces it; prose alone drifts.
- One fact, one place: a value lives in `test_results`, a target in `issue_markers`, a dose in the routine's SCHEDULE, a cadence in `screenings`. Nothing quotes another layer's fact.
- Python standard library only (plus optional `bcftools`). Chart.js stays vendored at `vendor/chart.umd.min.js` (pinned 4.5.1) and inlined — never a CDN tag: the page holds the whole dataset.
- A `labs.db` made by an older version gains new tables and columns on its own (`init_db.migrate`, run by the viewer, the import screen, `issues.py`).

### Privacy
- `labs.db`, `inputs/`, `imports/`, `backups/` and the generated files stay out of git (`.gitignore`); share only code and the synthetic seed.
- Personal details (DOB, history, goals) go in `inputs/user_facts.md`, never in a tracked file.
- A code fixture uses an invented analyte ("Testium") and invented findings — a real one will sooner or later equal someone's result.
- The only network calls send a public rsID to Ensembl (`lookup_variant.py`, `enrich_variants.py`); `--offline` skips them.

### Importing results
- Every lab report or clinical document goes in through the `import-labs` skill.
- Look a biomarker up by `(name_en, specimen_en, unit)` — the name alone is not unique (creatinine: blood and urine).
- Insert only through `inspect_import.commit_import(conn, rows, lab=…, resolved={…})`, from a dated `imports/YYYY-MM-DD_<lab>.py`; acknowledge every `SUSPECT` with a reason, never insert one silently.
- Check each value against its history, its sibling markers and the reference range even at a plausible magnitude: units change between labs, often for a whole class of analytes at once.
- Every result names its laboratory (`test_results.lab`, "… (shipped sample)" when the tube travelled): a trend needs two draws from the same lab.
- `test_results.value` is TEXT: keep qualifiers as written (`<10`, `>750`, `Negative`).
- A symptom score or an imaging measurement is an ordinary biomarker (specimen `Self-report` / `Imaging`), so it gets a chart, a target and the import screen.

### Findings (`issues.py`)
- One finding is ONE problem with one exit criterion (`done_when`) — "LDL above target", not "Heart"; its category only groups it.
- Write findings only through `issues.py` (`--load`, `--set`, `--marker`, `--action`, `--drop-action`, each with `--why`); a hand `UPDATE` is caught by `--check`, because the reason would be missing.
- Severity (harm if unmanaged) and confidence (how firmly the user's own data establish it) are kept apart: an unconfirmed result lowers confidence, not severity. Scales in `issues.py`.
- No lab value in a finding's text or an action (gate) — the viewer shows the values next to it, always current.
- An action names no dose (the routine holds doses): `take` / `conditional` (trigger in `detail`) / `avoid` an item, or a `diet` / `activity` / `lifestyle` / `refer` rule.
- Prescribe from clinical logic and the user's results and variants, not from what they happen to take today.
- A direction needs two comparable draws (latest not stale, ≤400 days apart, same lab); otherwise the marker reads "off target" with the reason.
- A move inside the analyte's noise floor is "flat" — but only where a CV_I is on file with its source (`rcv.py`; e.g. one lookup on biologicalvariation.eu, whose API is closed to outside use). Never invent one.
- A withdrawn finding gets `status=retired` with the reason, not a delete.
- Finding JSON (a list): `key, category (name or id), title, summary, severity, confidence, status, done_when, opened_at, why, markers [{id, name, low, high, note}], screenings [row names], actions [{kind, name, why, marker?, detail?}]`. `data/seed.json → issues` is a worked example.

### The routine (`unified_protocol`)
- Five `═══` sections in order — 1. SCHEDULE, 2. DIET, 3. ACTIVITY, 4. LIFESTYLE, 5. MEDICINES & GENES; a SCHEDULE bullet is `• Item — dose, timing`.
- Every take / conditional of a live finding has a SCHEDULE bullet, no avoided item has one, every bullet is prescribed or tagged `(general)` (gate ACT1–3); it quotes no lab value (gate).
- DIET / ACTIVITY / LIFESTYLE hold the general rules; a finding's own rules sit on the finding and the viewer shows them beside the section.

### Genetic variants
- Read direction from `variants.effect_allele` / `effect_copies` (0 / 1 / 2; NULL = unknown), never from the zygosity word.
- `zygosity` is relative to the catalogue's risk genotype, not the reference genome; a reference genome often carries the minor or the effect allele, so a VCF's hom-ref / hom-alt says nothing about risk.
- An effect allele comes only from a source (the entry's `risk` list, or `effect` + `source`, a PMID or GWAS Catalog study); otherwise it stays NULL.
- Catalogue palindromic SNPs (A/T, C/G) are written on the genomic plus strand; on a minus-strand gene the paper's allele is the complement of the genome call.
- A site absent from the file is not a genotype (not on the array, no call, filtered, or homozygous reference in a variant-only VCF); `lookup_variant.py` names the reference letters and says "if covered".
- Prose may not write up a variant with 0 effect copies as carried (gate).
- To surface an uncatalogued variant, promote it: `INSERT INTO variants (category_id, rsid, gene, relevance, genotype, zygosity, effect_allele, effect_copies)` with the same conventions; `check_all.py` re-reads every promoted row from the genome file. To make it reusable, add it to `data/known_variants.json` (see its `_README`) and run `python3 enrich_variants.py`.

### Screening calendar
- `screenings` is the only source of cadence and due dates; record a done screening with `--set`, never in code.
- Name a new row so `KINDS` recognises where it is done; `--plan` and the viewer's `screeningPlan` mirror each other — change both.
- A row more than a year late: do it, or change its cadence.

## Commands

```bash
python3 init_db.py                 # create labs.db with a synthetic demo (--force overwrites, after a backup)
python3 init_db.py --empty         # clean start for the user's own data
python3 init_db.py --backup        # gzipped snapshot into backups/ (newest 14 kept)
python3 viewer.py                  # regenerate viewer.html + master_protocol.md (after ANY db change)
python3 check_all.py               # every self-check, the CONSISTENCY gates, the reports
python3 issues.py                  # findings and their state (--show, --load, --set, --marker, --action, --check)
python3 screening_calendar.py --plan
python3 import_dna.py <file>       # VCF or 23andMe / AncestryDNA raw → variants
python3 lookup_variant.py rs…      # any variant from the genome file, on demand
python3 rcv.py                     # noise floors on file (--set BIOMARKER_ID CVI --source "…")
python3 export_for_llm.py          # lab time-series → labs_export.md (--summary → labs_summary.md)
```

## What `check_all.py` runs

- **CONSISTENCY (gates)** — every script's `--self-check`; db invariants (no foreign-key orphan, no future-dated result); `issues.py --check` (every score, status and action has its event; no lab value in a finding; nothing both taken and avoided); the routine vs the findings (ROUTINE, ACT1–3, VALUES); `check_variants.py` (no 0-copy variant written up as carried; promoted rows match the genome file).
- **REPORTS (never gate)** — the findings report (off target with nothing prescribed, markers with no noise floor), category narratives older than their labs, generated files older than `labs.db`, no backup within 3 days of the last change.

## Data model (`labs.db`, full schema in `schema.sql`)

`categories` (16) · `biomarkers` (name, specimen, unit, ref_low/high, opt_low/high) ·
`biomarker_categories` · `test_results` (biomarker_id, date, **value TEXT**, lab; one per marker per
date) · `issues` / `issue_markers` / `issue_actions` / `issue_events` (the findings) ·
`biological_variation` (CV_I per marker, with source) · `unified_protocol` (the routine) ·
`category_insights` (background narrative per category: insight, insight_dna, concordance
`mechanism|predicted|observed|verdict`) · `variants` (genotype, zygosity, effect_allele,
effect_copies) · `screenings` (cadence, last done, `issue_id`) · `documents` (imaging / report
text, LLM context).
