-- Phase 1d: PDF Processing Schema Extensions
-- Run against hta_intelligence_db

-- ============================================================
-- 1. Extend search_documents for PDF chunks
-- ============================================================

ALTER TABLE search_documents ADD
    doc_type        VARCHAR(20) DEFAULT 'assessment',
    parent_id       VARCHAR(200),
    chunk_index     INT,
    section_title   NVARCHAR(500),
    document_url    VARCHAR(1000);
GO

CREATE INDEX ix_search_doctype ON search_documents(doc_type);
GO

-- ============================================================
-- 2. PDF chunk tracking table
-- ============================================================

CREATE TABLE pdf_chunks (
    chunk_id        INT IDENTITY PRIMARY KEY,
    document_id     INT NOT NULL REFERENCES documents(document_id),
    assessment_id   INT NOT NULL REFERENCES assessments(assessment_id),
    chunk_index     INT NOT NULL,
    section_title   NVARCHAR(500),
    chunk_text      NVARCHAR(MAX),
    char_count      INT,
    embedding_hash  VARCHAR(64),
    search_doc_id   VARCHAR(200),
    indexed_at      DATETIME2,

    CONSTRAINT uq_chunks UNIQUE (document_id, chunk_index)
);

CREATE INDEX ix_chunks_assessment ON pdf_chunks(assessment_id);
CREATE INDEX ix_chunks_document ON pdf_chunks(document_id);
GO
