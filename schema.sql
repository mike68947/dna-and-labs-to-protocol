-- health-tracker-template schema. labs.db is the source of truth.
-- Created by init_db.py; edited thereafter by Claude as you import data.

-- ── Core ─────────────────────────────────────────────────────────────────────
CREATE TABLE categories (
    id       INTEGER PRIMARY KEY,
    name_en  TEXT NOT NULL,
    name_ru  TEXT                     -- kept for viewer compatibility; seed = name_en (hides the bilingual sub-line)
);

CREATE TABLE biomarkers (
    id           INTEGER PRIMARY KEY,
    name_en      TEXT NOT NULL,
    name_ru      TEXT,
    specimen_en  TEXT,
    specimen_ru  TEXT,
    unit         TEXT,
    ref_low      REAL,                -- population reference range (from the lab report, or seed_ref_ranges.py)
    ref_high     REAL,
    opt_low      REAL,                -- optional "optimal band" (tighter longevity target) → Longevity Dashboard
    opt_high     REAL,
    pers_bands   TEXT,                -- optional JSON: age/sex personalized interval (advanced; usually null)
    ref_source   TEXT,
    UNIQUE(name_en, specimen_en, unit)
);

CREATE TABLE biomarker_categories (
    biomarker_id  INTEGER NOT NULL REFERENCES biomarkers(id),
    category_id   INTEGER NOT NULL REFERENCES categories(id),
    PRIMARY KEY (biomarker_id, category_id)
);

CREATE TABLE test_results (
    id            INTEGER PRIMARY KEY,
    biomarker_id  INTEGER NOT NULL REFERENCES biomarkers(id),
    date          DATE NOT NULL,
    value         TEXT                 -- TEXT preserves qualifiers: "<10", ">750", "Negative"
);

-- ── Claude-authored layer ────────────────────────────────────────────────────
CREATE TABLE category_insights (
    id                INTEGER PRIMARY KEY,
    category_id       INTEGER NOT NULL UNIQUE REFERENCES categories(id),
    insight           TEXT NOT NULL,   -- lab-based narrative assessment
    insight_dna       TEXT,            -- DNA-enriched detail (optional)
    supplements       TEXT,            -- the 5 structured protocol domains (viewer composes them into a Protocol tab)
    diet              TEXT,
    activity          TEXT,
    lifestyle         TEXT,
    checkup_schedule  TEXT,
    concordance       TEXT,            -- pipe rows: mechanism|predicted|observed|verdict
                                       -- verdict ∈ CONFIRMS/PARTIAL/UNRESOLVED/CONTRADICTS/FAVORABLE
    updated_at        DATE NOT NULL DEFAULT (date('now'))
);

CREATE TABLE variants (               -- populated by import_dna.py from your DNA file
    id           INTEGER PRIMARY KEY,
    category_id  INTEGER NOT NULL REFERENCES categories(id),
    rsid         TEXT NOT NULL,
    gene         TEXT,
    relevance    TEXT,                 -- interpretation string (viewer renders this)
    genotype     TEXT,                 -- e.g. "CT" or "ε3/ε4"
    zygosity     TEXT,                 -- 'het' | 'hom-alt' | 'hom-ref'
    UNIQUE(category_id, rsid)
);

CREATE TABLE unified_protocol (
    id          INTEGER PRIMARY KEY,
    protocol    TEXT NOT NULL,
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE screenings (             -- forward "what's due" calendar (seeded by screening_calendar.py)
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    domain TEXT,
    cadence_months INTEGER,           -- NULL = one-time / as-needed
    last_done TEXT,
    last_result TEXT,
    rationale TEXT,
    priority TEXT,
    updated_at TEXT
);

CREATE TABLE documents (              -- imaging / report text Claude ingests (LLM context; not viewer-rendered)
    id          INTEGER PRIMARY KEY,
    title       TEXT NOT NULL,
    doc_date    DATE,
    doc_type    TEXT,                  -- 'report' | 'imaging' | 'note'
    category_id INTEGER REFERENCES categories(id),
    body        TEXT,
    file_path   TEXT
);

-- ── Seed categories (name_ru = name_en so the bilingual UI stays hidden) ──────
INSERT INTO categories (id, name_en, name_ru) VALUES
 (1,  'Biological Age & Longevity',           'Biological Age & Longevity'),
 (2,  'Complete Blood Count (CBC)',           'Complete Blood Count (CBC)'),
 (3,  'Metabolic & Glucose',                  'Metabolic & Glucose'),
 (4,  'Lipid Panel',                          'Lipid Panel'),
 (5,  'Cardiovascular Risk',                  'Cardiovascular Risk'),
 (6,  'Liver Function',                       'Liver Function'),
 (7,  'Kidney Function',                       'Kidney Function'),
 (8,  'Electrolytes & Minerals',              'Electrolytes & Minerals'),
 (9,  'Thyroid Function',                     'Thyroid Function'),
 (10, 'Hormones',                             'Hormones'),
 (11, 'Inflammation & Immunity',              'Inflammation & Immunity'),
 (12, 'Iron Studies',                         'Iron Studies'),
 (13, 'Vitamins',                             'Vitamins'),
 (14, 'Gut & Microbiome',                     'Gut & Microbiome'),
 (15, 'Drug Metabolism (Pharmacogenomics)',   'Drug Metabolism (Pharmacogenomics)'),
 (16, 'Fitness & Muscle',                     'Fitness & Muscle');
