---
name: regenerate-unified-protocol
description: Rebuild the master unified_protocol blob in labs.db — a single consolidated document (supplements by tier, diet, activity, lifestyle, monitoring, pharmacogenomic notes) synthesized from the per-category insights, latest labs, and variants. Use after a major lab import or when the per-category protocols have drifted from the master.
---

# Regenerate the unified master protocol

`unified_protocol` is one hand-curated blob that sits *above* the per-category data. Nothing
auto-composes it — you synthesize it.

## Steps

1. **Gather**: read `user_facts.md` (this dir) for age/sex/history; the latest value per
   biomarker; all non-null `category_insights` protocol columns; `variants`; the person's
   current supplement/medication stack (ask the user, or read a stack file if they keep one).

2. **Synthesize** a single document with `═══ SECTION ═══` / `─── sub ───` separators (the
   viewer styles them). Suggested sections: **A. Supplements** (tiered), **B. Diet**,
   **C. Activity**, **D. Lifestyle**, **E. Monitoring** (labs + cadence + targets),
   **H. Pharmacogenomics**. Include only items actually in use; note deferred items separately.

3. **Commit** (git is your backup — commit first):
   ```sql
   UPDATE unified_protocol SET protocol = :blob, updated_at = datetime('now') WHERE id = 1;
   -- or INSERT if the table is empty
   ```

4. **Consistency**: every supplement/dose/rule in the blob should match the per-category
   `category_insights` it came from — reconcile any that changed. Then `python3 viewer.py`.

The `data/seed.json` `unified_protocol` string is a worked example of the target shape.
