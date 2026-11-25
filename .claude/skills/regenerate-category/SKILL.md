---
name: regenerate-category
description: Rewrite one category's assessment in labs.db (category_insights) — the narrative read of its labs, what the person's variants add, and the concordance table (do the labs confirm the genetic prediction?). Use when check_all reports a category's insight older than its labs, or the user asks to refresh a category. Not for prescriptions: those live on findings (issues.py).
---

# Regenerate a category assessment

A category row is background: what the labs and variants of one panel say. It prescribes
nothing — a problem worth acting on becomes a finding (`issues.py --load`) with its own
targets and actions, and doses live in the routine.

## Steps

0. **Back up**: `python3 init_db.py --backup`.
1. **Gather** for the category id:
   ```sql
   SELECT * FROM category_insights WHERE category_id = :id;
   SELECT b.name_en, b.unit, t.date, t.value, t.lab FROM biomarkers b
     JOIN biomarker_categories bc ON bc.biomarker_id = b.id
     JOIN test_results t ON t.biomarker_id = b.id
     WHERE bc.category_id = :id ORDER BY b.name_en, t.date;
   SELECT rsid, gene, genotype, zygosity, effect_allele, effect_copies, relevance
     FROM variants WHERE category_id = :id;
   ```
   and `python3 issues.py` for the findings in this category.
2. **Write**:
   - `insight` (required) — the trajectory and what it means, grounded in the actual values.
   - `insight_dna` — what the variants add. Read direction from `effect_copies` (0 / 1 / 2;
     NULL = unknown), never from the zygosity word; with 0 copies, say the risk allele is absent.
   - `concordance` for a genomically loaded category: one row per line,
     `mechanism|predicted|observed|verdict`, verdict a bare keyword (CONFIRMS / PARTIAL /
     UNRESOLVED / CONTRADICTS / FAVORABLE).
   No prescriptions here. If the read shows a new problem, propose a finding instead.
3. **Commit**: `UPDATE category_insights SET insight=…, insight_dna=…, concordance=…,
   updated_at=date('now') WHERE category_id=:id` (INSERT if the row is missing).
4. `python3 viewer.py && python3 check_all.py`.
