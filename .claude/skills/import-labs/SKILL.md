---
name: import-labs
description: Import a lab report (PDF, photo, pasted table, portal export) or a clinical document (ultrasound, MRI, consult, procedure) into labs.db, then run the post-import cascade. Use when the user drops a report in inputs/ or says "import these labs", "here are my results", "add this ultrasound".
---

# import-labs

Adding data is how the project learns anything, and it is the step most likely to corrupt it:
a wrong unit, a wrong specimen, a report date taken for the draw date. No parser ships — you
read the report — so every step below exists to catch what reading gets wrong.

## 0. Back up first
```bash
python3 init_db.py --backup      # labs.db is the only copy of the user's data
```

## 1. Lab results
1. **Read the report.** Note the laboratory (add `(shipped sample)` when the tube travelled), the
   DRAW date (not the report date) and the specimen of every analyte. Skip anything marked
   pending. Keep qualifiers as written: `<10`, `>750`, `Negative` (`value` is TEXT).
2. **Watch the units.** Labs differ, often for a whole class of analytes at once: androgens in
   ng/mL vs nmol/L, counts in cells/µL vs ×10⁹/L, electrolytes in mg/L vs mmol/L, a fraction vs %,
   glucose and lipids in mg/dL vs mmol/L, HbA1c in % vs mmol/mol. Convert to the unit already in
   `biomarkers.unit` for that marker.
3. **Map each analyte to a biomarker** by `(name_en, specimen_en, unit)` — the name alone is not
   unique (creatinine exists in blood and urine). Create one (plus its `biomarker_categories`
   row, and `ref_low` / `ref_high` from the range the report states) only when nothing matches.
4. **Write `imports/YYYY-MM-DD_<lab>.py`** (local, git-ignored — it embeds values). It inserts
   only through:
   ```python
   from inspect_import import commit_import
   n = commit_import(conn, rows, lab="<laboratory>", resolved={(bid, date): "why it stands"})
   conn.commit()
   ```
   Every SUSPECT is either a unit error you fix or a real value you acknowledge in `resolved`
   with the reason (ask the user when unsure). Never insert one silently. A value in a plausible
   magnitude can still be wrong: cross-check its sibling markers and the reference range.

## 2. Clinical documents (imaging, consults, procedures)
1. Transcribe the text into `documents` (`title, doc_date, doc_type, category_id, body`,
   optional `file_path`).
2. **A measurement that tracks something becomes a number**: a nodule or stone size, a stenosis
   %, a bone-density T-score. Store it as a biomarker with `specimen_en = 'Imaging'`
   (`'Self-report'` for a symptom score) and insert it with `commit_import`, `lab` = the centre.
3. If it is a screening on the calendar, close the row:
   `python3 screening_calendar.py --set "<row name>" YYYY-MM-DD "<finding>"`.

## 3. The cascade (always, in this order)
1. `python3 issues.py` — re-read each finding whose markers moved (`--show KEY`). Rescore or
   close it when the data settles it (`--set KEY status=resolved --why "two comparable draws on
   target"`); ask the user when it is a judgement call. A new abnormality no finding covers
   becomes a proposed finding: JSON under `inputs/`, loaded with `--load`. An imaging
   measurement that tracks a finding gets attached: `--marker KEY BIOMARKER_ID --low … --high …`.
2. If a finding's prescription changes, write it with `issues.py --action / --drop-action
   … --why`, and give a new take / conditional its dose in the routine (the
   `regenerate-unified-protocol` skill) in the same change.
3. `python3 viewer.py && python3 check_all.py` — the import is not done while the gate is red.
   Its stale-insight report lists the category narratives that now lag the new labs.

## 4. Report to the user
- Rows inserted: count, laboratory, draw date. Each SUSPECT and how it was settled.
- Findings that changed state or score, and why. Prescriptions that changed.
- Calendar rows that closed.
