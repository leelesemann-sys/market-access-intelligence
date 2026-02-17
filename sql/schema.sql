-- HTA Intelligence Database Schema
-- Azure SQL (hta_intelligence_db)
-- Created: 2026-02-17

-- ============================================================
-- Reference tables
-- ============================================================

CREATE TABLE agencies (
    agency_id       VARCHAR(20) PRIMARY KEY,
    agency_name     NVARCHAR(200) NOT NULL,
    country_code    CHAR(2) NOT NULL,
    website_url     VARCHAR(500),
    data_source     VARCHAR(100)        -- 'xml', 'excel', 'api', 'csv'
);

INSERT INTO agencies VALUES
    ('gba',  'Gemeinsamer Bundesausschuss (G-BA)', 'DE', 'https://www.g-ba.de', 'xml'),
    ('nice', 'National Institute for Health and Care Excellence (NICE)', 'GB', 'https://www.nice.org.uk', 'excel');

CREATE TABLE drugs (
    drug_id         INT IDENTITY PRIMARY KEY,
    inn             NVARCHAR(200),              -- International Nonproprietary Name
    brand_name      NVARCHAR(200),
    atc_code        VARCHAR(10),
    atc_description NVARCHAR(500),
    orphan_drug     BIT DEFAULT 0,
    created_at      DATETIME2 DEFAULT GETDATE(),

    CONSTRAINT uq_drugs_inn_brand UNIQUE (inn, brand_name)
);

CREATE INDEX ix_drugs_inn ON drugs(inn);
CREATE INDEX ix_drugs_atc ON drugs(atc_code);

-- ============================================================
-- Core assessment tables
-- ============================================================

CREATE TABLE assessments (
    assessment_id       INT IDENTITY PRIMARY KEY,
    agency_id           VARCHAR(20) NOT NULL REFERENCES agencies(agency_id),
    drug_id             INT REFERENCES drugs(drug_id),

    -- Source identifiers
    source_id           VARCHAR(200) NOT NULL,  -- G-BA: ID_BE_AKZ / NICE: TA ID
    source_numeric_id   INT,                    -- G-BA: ID_BE / NICE: TA number

    -- Classification
    assessment_type     VARCHAR(30),            -- 'regular', 'orphan', 'antibiotic'
    therapeutic_area    VARCHAR(100),
    indication          NVARCHAR(MAX),
    indication_short    NVARCHAR(255),
    indication_icd10    VARCHAR(500),           -- comma-separated ICD-10 codes

    -- Dates
    decision_date       DATE,
    decision_end_date   DATE,                   -- for time-limited decisions
    publication_year    VARCHAR(10),            -- NICE: financial year "2024/25"

    -- Original source rating (never modified)
    source_rating       NVARCHAR(500),
    source_detail       NVARCHAR(MAX),

    -- Harmonized outcome (ternary: positive/restricted/negative)
    overall_outcome     VARCHAR(20),            -- 'positive', 'restricted', 'negative'

    -- Metadata
    source_url          VARCHAR(1000),
    pdf_url             VARCHAR(1000),
    process_type        VARCHAR(50),            -- NICE: 'STA', 'MTA', 'STA (review)'
    technology_type     VARCHAR(50),            -- NICE: 'Pharmaceutical', 'Medical device'

    imported_at         DATETIME2 DEFAULT GETDATE(),
    updated_at          DATETIME2,

    CONSTRAINT uq_assessments_source UNIQUE (agency_id, source_id)
);

CREATE INDEX ix_assessments_agency ON assessments(agency_id);
CREATE INDEX ix_assessments_drug ON assessments(drug_id);
CREATE INDEX ix_assessments_date ON assessments(decision_date);
CREATE INDEX ix_assessments_outcome ON assessments(overall_outcome);

-- ============================================================
-- G-BA specific: subgroup-level outcomes
-- ============================================================

CREATE TABLE assessment_outcomes (
    outcome_id          INT IDENTITY PRIMARY KEY,
    assessment_id       INT NOT NULL REFERENCES assessments(assessment_id),
    subgroup_id         INT,                    -- G-BA: ID_PAT_GR

    -- Patient group
    subgroup_name       NVARCHAR(1500),
    patient_population  NVARCHAR(MAX),
    indication_decision NVARCHAR(MAX),          -- AWG_BESCHLUSS
    indication_short    NVARCHAR(255),          -- AWG_KURZ

    -- Benefit assessment
    benefit_extent      VARCHAR(100),           -- G-BA: ZN_A enum value
    benefit_text        NVARCHAR(1500),         -- G-BA: ZN_TEXT
    evidence_certainty  VARCHAR(30),            -- G-BA: Beleg/Hinweis/Anhaltspunkt

    -- Endpoint summaries (arrows + descriptions)
    ep_mortality_symbol VARCHAR(20),
    ep_mortality_text   NVARCHAR(1000),
    ep_morbidity_symbol VARCHAR(20),
    ep_morbidity_text   NVARCHAR(1000),
    ep_qol_symbol       VARCHAR(20),
    ep_qol_text         NVARCHAR(1000),
    ep_safety_symbol    VARCHAR(20),
    ep_safety_text      NVARCHAR(1000),

    -- Supporting rationale summary
    rationale_summary   NVARCHAR(4000),

    CONSTRAINT uq_outcomes_subgroup UNIQUE (assessment_id, subgroup_id)
);

-- ============================================================
-- Comparator therapies
-- ============================================================

CREATE TABLE comparators (
    comparator_id       INT IDENTITY PRIMARY KEY,
    assessment_id       INT NOT NULL REFERENCES assessments(assessment_id),
    outcome_id          INT REFERENCES assessment_outcomes(outcome_id),
    comparator_name     NVARCHAR(3000),
    comparator_atc      VARCHAR(500),           -- ATC codes, comma-separated
    comparator_type     VARCHAR(50)             -- 'active', 'placebo', 'bsc'
);

-- ============================================================
-- Documents (links to PDFs)
-- ============================================================

CREATE TABLE documents (
    document_id         INT IDENTITY PRIMARY KEY,
    assessment_id       INT NOT NULL REFERENCES assessments(assessment_id),
    doc_type            VARCHAR(50),            -- 'dossier', 'agency_report', 'decision', 'summary'
    doc_title           NVARCHAR(500),
    doc_url             VARCHAR(1000),
    local_path          VARCHAR(500),
    parsed              BIT DEFAULT 0,
    parsed_at           DATETIME2
);

-- ============================================================
-- Denormalized search table (for Azure AI Search / RAG)
-- ============================================================

CREATE TABLE search_documents (
    id                  VARCHAR(200) PRIMARY KEY,   -- 'gba-{source_id}' / 'nice-{ta_id}'
    agency_id           VARCHAR(20),
    country_code        CHAR(2),

    -- Drug info
    drug_inn            NVARCHAR(200),
    drug_brand          NVARCHAR(200),
    atc_code            VARCHAR(10),

    -- Assessment info
    therapeutic_area    VARCHAR(100),
    indication          NVARCHAR(MAX),
    source_rating       NVARCHAR(500),
    overall_outcome     VARCHAR(20),
    comparator_names    NVARCHAR(MAX),

    -- Dates
    decision_date       DATE,
    decision_year       INT,

    -- G-BA specific (NULL for NICE)
    benefit_extent      VARCHAR(100),
    evidence_certainty  VARCHAR(30),
    endpoint_summary    NVARCHAR(MAX),

    -- NICE specific (NULL for G-BA)
    nice_process        VARCHAR(50),
    nice_comment        NVARCHAR(MAX),

    -- Source link
    source_url          VARCHAR(1000),

    -- Embedding
    embedding_text      NVARCHAR(MAX),
    embedding_hash      VARCHAR(64),

    -- Status
    indexed_at          DATETIME2
);

CREATE INDEX ix_search_agency ON search_documents(agency_id);
CREATE INDEX ix_search_year ON search_documents(decision_year);
CREATE INDEX ix_search_outcome ON search_documents(overall_outcome);

-- ============================================================
-- Analytics views
-- ============================================================
GO

CREATE VIEW v_benefit_distribution AS
SELECT
    a.agency_id,
    a.therapeutic_area,
    a.overall_outcome,
    a.source_rating,
    YEAR(a.decision_date) AS decision_year,
    COUNT(*) AS assessment_count
FROM assessments a
WHERE a.decision_date IS NOT NULL
GROUP BY a.agency_id, a.therapeutic_area, a.overall_outcome, a.source_rating, YEAR(a.decision_date);
GO

CREATE VIEW v_matched_drug_pairs AS
SELECT
    d.inn,
    d.atc_code,
    d.orphan_drug,
    gba.source_id       AS gba_source_id,
    gba.source_rating   AS gba_rating,
    gba.overall_outcome AS gba_outcome,
    gba.decision_date   AS gba_date,
    gba.therapeutic_area,
    nice.source_id      AS nice_source_id,
    nice.source_rating  AS nice_rating,
    nice.overall_outcome AS nice_outcome,
    nice.decision_date  AS nice_date,
    CASE
        WHEN gba.overall_outcome = nice.overall_outcome THEN 'concordant'
        ELSE 'discordant'
    END AS concordance
FROM drugs d
JOIN assessments gba  ON d.drug_id = gba.drug_id  AND gba.agency_id = 'gba'
JOIN assessments nice ON d.drug_id = nice.drug_id  AND nice.agency_id = 'nice';
GO
