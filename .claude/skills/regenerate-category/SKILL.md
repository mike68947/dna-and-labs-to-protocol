---
name: regenerate-category
description: Rewrite one category_insights row in labs.db — refresh the `insight` against the latest labs, variants, and stack, fill the 5 structured protocol columns (supplements/diet/activity/lifestyle/checkup_schedule), and add `concordance` for genomically-loaded categories. Use when a category has drifted or the user asks to regenerate it.
---

# Regenerate a category insight

Rewrite a single `category_insights` row grounded in the person's real data.

## Steps

1. **Gather inputs** for the target category id:
   ```sql
   -- current row
   SELECT * FROM category_insights WHERE category_id = :id;
   -- biomarker trajectory (every value, oldest→newest)
   SELECT b.name_en, t.date, t.value FROM biomarkers b
     JOIN biomarker_categories bc ON bc.biomarker_id = b.id
     JOIN test_results t ON t.biomarker_id = b.id
     WHERE bc.category_id = :id ORDER BY b.name_en, t.date;
   -- variants in this category
   SELECT rsid, gene, genotype, zygosity, relevance FROM variants WHERE category_id = :id;
   ```
   Also skim `unified_protocol.protocol` for anything this category touches.

2. **Write the columns** (see CLAUDE.md → "Authoring category_insights"):
   - `insight` (required) grounded in the actual trajectory + latest values.
   - `insight_dna` if variants are present.
   - the 5 protocol domains for items actually in use.
   - `concordance` (`mechanism|predicted|observed|verdict`, bare verdict keyword) when the
     category is genomically loaded — does the lab evidence confirm the genetic prediction?

3. **Commit**: `UPDATE category_insights SET ... , updated_at = date('now') WHERE category_id = :id;`
   (git is your backup — commit before large rewrites.)

4. **Reconcile + rebuild**: if a supplement/dose/rule changed, grep `unified_protocol` for it
   and update that too. Then `python3 viewer.py`.
