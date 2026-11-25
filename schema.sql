-- DNA and labs to protocol — schema. labs.db is the source of truth.
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
    value         TEXT,                -- TEXT preserves qualifiers: "<10", ">750", "Negative"
    lab           TEXT,                -- the laboratory that measured it ("… (shipped sample)" when the tube travelled)
    UNIQUE(biomarker_id, date)         -- one value per marker per date; import with INSERT OR IGNORE
);

-- ── Findings: the unit of the protocol (written only through issues.py) ──────
-- One row is ONE finding with one exit criterion. What is stored is the judgement and its
-- history; whether a marker is on target and which way it moved is computed on every read.
CREATE TABLE issues (
    id          INTEGER PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,     -- short slug, e.g. 'ldl-above-target'
    category_id INTEGER NOT NULL REFERENCES categories(id),   -- groups findings in the viewer
    title       TEXT NOT NULL,
    summary     TEXT NOT NULL,            -- plain language; never a value (values live in test_results)
    severity    INTEGER NOT NULL CHECK (severity BETWEEN 0 AND 100),
    confidence  INTEGER CHECK (confidence BETWEEN 0 AND 100),
    status      TEXT NOT NULL CHECK (status IN ('open','watch','resolved','retired')),
    done_when   TEXT,                     -- the exit criterion
    opened_at   TEXT NOT NULL,
    closed_at   TEXT                      -- set exactly when resolved / retired
);

CREATE TABLE issue_markers (              -- what measures a finding, and what "good" means
    issue_id     INTEGER NOT NULL REFERENCES issues(id),
    biomarker_id INTEGER NOT NULL REFERENCES biomarkers(id),
    target_low   REAL,                    -- both NULL = a context marker with no target
    target_high  REAL,
    note         TEXT,
    PRIMARY KEY (issue_id, biomarker_id),
    CHECK (target_low IS NULL OR target_high IS NULL OR target_low <= target_high)
);

CREATE TABLE issue_events (               -- why a score, status, target or action changed
    id         INTEGER PRIMARY KEY,
    issue_id   INTEGER NOT NULL REFERENCES issues(id),
    date       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    severity   INTEGER,
    confidence INTEGER,
    why        TEXT NOT NULL CHECK (length(trim(why)) > 0)
);

CREATE TABLE issue_actions (              -- the prescription for a finding (no dose: the routine holds it)
    id           INTEGER PRIMARY KEY,
    issue_id     INTEGER NOT NULL REFERENCES issues(id),
    kind         TEXT NOT NULL CHECK (kind IN ('take','conditional','avoid','diet','activity','lifestyle','refer')),
    name         TEXT NOT NULL,
    biomarker_id INTEGER REFERENCES biomarkers(id),   -- optional: the marker this item is aimed at
    detail       TEXT,
    why          TEXT NOT NULL CHECK (length(trim(why)) > 0)
);
CREATE UNIQUE INDEX idx_issue_actions ON issue_actions (issue_id, kind, name, COALESCE(biomarker_id, 0));

CREATE TABLE biological_variation (       -- noise floor per analyte (rcv.py --set; never guessed)
    biomarker_id INTEGER PRIMARY KEY REFERENCES biomarkers(id),
    cvi          REAL NOT NULL CHECK (cvi > 0),   -- within-subject biological variation, %
    source       TEXT NOT NULL,                   -- where the number comes from
    recorded_at  TEXT NOT NULL
);

-- ── Claude-authored background ───────────────────────────────────────────────
CREATE TABLE category_insights (      -- the assessment of a category; the prescription lives in findings
    id                INTEGER PRIMARY KEY,
    category_id       INTEGER NOT NULL UNIQUE REFERENCES categories(id),
    insight           TEXT NOT NULL,   -- lab-based narrative assessment
    insight_dna       TEXT,            -- DNA-enriched detail (optional)
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
    zygosity     TEXT,                 -- relative to the catalogue's RISK genotype, not the reference genome:
                                       -- 'het' | 'hom-alt' (the flagged homozygote) | 'hom-ref' (another listed
                                       -- homozygote) | 'hom' (direction unknown) | 'hemi' (one copy, e.g. male X)
    effect_allele TEXT,                -- the allele the interpretation is about, on the call's strand (NULL = no source)
    effect_copies INTEGER,             -- copies of it in the call: 0 / 1 / 2 (NULL = unknown). Read direction HERE.
    UNIQUE(category_id, rsid)
);

CREATE TABLE unified_protocol (       -- the daily routine: five ═══ sections, doses in 1. SCHEDULE only
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
    updated_at TEXT,
    issue_id INTEGER REFERENCES issues(id)   -- the finding this row belongs to (set by issues.py)
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
