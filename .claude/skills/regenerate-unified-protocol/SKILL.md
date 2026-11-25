---
name: regenerate-unified-protocol
description: Write or change the daily routine (unified_protocol in labs.db) — the doses in 1. SCHEDULE, the general diet / activity / lifestyle rules, the medicines-and-genes sheet — whole or one section. Use after a finding gains or loses a take / conditional / avoid action, after a dose change, or when the user asks to (re)write the routine. Not for what is prescribed for one finding (that is `issues.py --action`).
---

# The daily routine

The protocol has two halves. Each **finding** carries its own prescription (`issue_actions`:
take / conditional / avoid an item, a diet / activity / lifestyle / refer rule — with the
reason, no dose). The **routine** is what every finding shares, and the one place a dose is
written. One fact, one place: no lab value or target here (they live in `test_results` and
`issue_markers`), no cadence (the screening calendar holds those).

## Shape (the gate checks it)

Five sections, each once, in order, with a three-line banner:

```
════════════════════════════════════════════
1. SCHEDULE
════════════════════════════════════════════
─── Breakfast ───
• Item — dose, timing
```
then `2. DIET`, `3. ACTIVITY`, `4. LIFESTYLE`, `5. MEDICINES & GENES`. A short preamble may
precede section 1.

- **1. SCHEDULE** — one bullet `• Item — dose, timing` per item per time slot. Every take /
  conditional action of a live finding needs a bullet (ACT1); an avoided item never has one
  (ACT2); every bullet is prescribed by a live finding or tagged `Item (general) — …` (ACT3).
  A conditional item keeps its trigger in its finding's `detail`.
- **2–4** — the general rules no single finding owns (each finding's own diet / activity /
  lifestyle rules are shown beside these by the viewer; don't repeat them).
- **5. MEDICINES & GENES** — a prescriber sheet: pharmacogenomic variants and what each
  means for a drug class. Read direction from `variants.effect_copies`, never the zygosity word.

Prescribe from clinical logic, the person's results and variants — not from what they happen
to be taking today.

## Steps

1. Gather: `inputs/user_facts.md` (age, sex, history; template `data/user_facts.example.md`),
   `python3 issues.py` (the live findings and their actions), the current routine
   (`SELECT protocol FROM unified_protocol`), the pharmacogenomic `variants`.
2. Draft to `inputs/routine_draft.txt` (git-ignored).
3. Commit:
   ```bash
   python3 init_db.py --backup
   python3 - <<'PY'
   import pathlib, sqlite3, protocol_view as pv
   text = pathlib.Path("inputs/routine_draft.txt").read_text()
   pv.routine_sections(text)          # raises unless the five sections are there, in order
   c = sqlite3.connect("labs.db")
   c.execute("DELETE FROM unified_protocol")
   c.execute("INSERT INTO unified_protocol (protocol) VALUES (?)", (text,))
   c.commit()
   PY
   python3 viewer.py && python3 check_all.py
   ```
   A red ACT1–3 or VALUES line means the routine and the findings disagree: fix whichever is
   wrong (the routine, or the finding's action via `issues.py --action / --drop-action`).
