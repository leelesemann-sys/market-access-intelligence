"""Denormalize: assessments + drugs → search_documents.

Builds a flat search table from the normalized tables.
Each assessment becomes one search document with an embedding text
composed from drug, indication, outcome, and comparator info.
"""
import hashlib
import logging

import db

log = logging.getLogger(__name__)


def _build_embedding_text(row: dict) -> str:
    """Compose the text that will be embedded for vector search."""
    parts = []

    agency_label = {"gba": "G-BA (Germany)", "nice": "NICE (UK)"}.get(
        row.get("agency_id"), row.get("agency_id", "")
    )

    if row.get("drug_inn"):
        brand = row.get("drug_brand", "")
        if brand and brand != row["drug_inn"]:
            parts.append(f"Drug: {row['drug_inn']} ({brand})")
        else:
            parts.append(f"Drug: {row['drug_inn']}")

    if row.get("atc_code"):
        parts.append(f"ATC: {row['atc_code']}")

    parts.append(f"Agency: {agency_label}")

    if row.get("therapeutic_area"):
        parts.append(f"Therapeutic area: {row['therapeutic_area']}")

    if row.get("indication"):
        ind = row["indication"][:1500]
        parts.append(f"Indication: {ind}")

    if row.get("source_rating"):
        parts.append(f"Assessment result: {row['source_rating']}")

    if row.get("overall_outcome"):
        parts.append(f"Overall outcome: {row['overall_outcome']}")

    if row.get("comparator_names"):
        parts.append(f"Comparators: {row['comparator_names']}")

    if row.get("benefit_extent"):
        parts.append(f"Benefit extent: {row['benefit_extent']}")

    if row.get("evidence_certainty"):
        parts.append(f"Evidence certainty: {row['evidence_certainty']}")

    if row.get("endpoint_summary"):
        parts.append(f"Endpoints: {row['endpoint_summary'][:500]}")

    if row.get("nice_comment"):
        parts.append(f"Comment: {row['nice_comment'][:500]}")

    return "\n".join(parts)


def refresh() -> int:
    """Populate search_documents from assessments + drugs.

    Uses UPSERT logic: inserts new docs and updates changed ones.
    Returns count of new/updated documents.
    """
    count_before = db.fetch_all("SELECT COUNT(*) FROM search_documents")[0][0]

    # Insert new search documents from G-BA assessments
    db.execute("""
        INSERT INTO search_documents (
            id, agency_id, country_code,
            drug_inn, drug_brand, atc_code,
            therapeutic_area, indication, source_rating, overall_outcome,
            comparator_names,
            decision_date, decision_year,
            benefit_extent, evidence_certainty, endpoint_summary,
            nice_process, nice_comment,
            source_url
        )
        SELECT
            CONCAT('gba-', a.source_id) AS id,
            'gba', 'DE',
            d.inn, d.brand_name, d.atc_code,
            a.therapeutic_area, a.indication, a.source_rating, a.overall_outcome,
            -- Aggregate comparators
            (SELECT STRING_AGG(c.comparator_name, '; ')
             FROM comparators c WHERE c.assessment_id = a.assessment_id),
            a.decision_date, YEAR(a.decision_date),
            -- Best benefit from subgroups
            (SELECT TOP 1 ao.benefit_extent
             FROM assessment_outcomes ao
             WHERE ao.assessment_id = a.assessment_id
             ORDER BY CASE ao.benefit_extent
                WHEN 'erheblich' THEN 1
                WHEN 'beträchtlich' THEN 2
                WHEN 'gering' THEN 3
                WHEN 'nicht quantifizierbar' THEN 4
                WHEN 'nicht quantifizierbar (Datengrundlage)' THEN 5
                WHEN 'nicht quantifizierbar (Nachweise)' THEN 6
                WHEN 'gilt als belegt' THEN 7
                WHEN 'gilt als nicht belegt' THEN 8
                WHEN 'ist nicht belegt' THEN 9
                WHEN 'geringerer Nutzen' THEN 10
                ELSE 99 END),
            -- Best evidence
            (SELECT TOP 1 ao.evidence_certainty
             FROM assessment_outcomes ao
             WHERE ao.assessment_id = a.assessment_id
               AND ao.evidence_certainty IS NOT NULL
             ORDER BY CASE ao.evidence_certainty
                WHEN 'Beleg' THEN 1
                WHEN 'Hinweis' THEN 2
                WHEN 'Anhaltspunkt' THEN 3
                ELSE 99 END),
            -- Endpoint summary
            (SELECT TOP 1
                CONCAT_WS(' | ',
                    CASE WHEN ao.ep_mortality_symbol IS NOT NULL
                         THEN CONCAT('Mortality: ', ao.ep_mortality_symbol) END,
                    CASE WHEN ao.ep_morbidity_symbol IS NOT NULL
                         THEN CONCAT('Morbidity: ', ao.ep_morbidity_symbol) END,
                    CASE WHEN ao.ep_qol_symbol IS NOT NULL
                         THEN CONCAT('QoL: ', ao.ep_qol_symbol) END,
                    CASE WHEN ao.ep_safety_symbol IS NOT NULL
                         THEN CONCAT('Safety: ', ao.ep_safety_symbol) END
                )
             FROM assessment_outcomes ao
             WHERE ao.assessment_id = a.assessment_id
               AND ao.ep_mortality_symbol IS NOT NULL),
            NULL, NULL,  -- nice_process, nice_comment
            a.source_url
        FROM assessments a
        JOIN drugs d ON a.drug_id = d.drug_id
        WHERE a.agency_id = 'gba'
          AND NOT EXISTS (
              SELECT 1 FROM search_documents sd
              WHERE sd.id = CONCAT('gba-', a.source_id)
          )
    """)

    # Insert new search documents from NICE assessments
    db.execute("""
        INSERT INTO search_documents (
            id, agency_id, country_code,
            drug_inn, drug_brand, atc_code,
            therapeutic_area, indication, source_rating, overall_outcome,
            comparator_names,
            decision_date, decision_year,
            benefit_extent, evidence_certainty, endpoint_summary,
            nice_process, nice_comment,
            source_url
        )
        SELECT
            CONCAT('nice-', a.source_id) AS id,
            'nice', 'GB',
            d.inn, d.brand_name, NULL,
            a.therapeutic_area, a.indication, a.source_rating, a.overall_outcome,
            NULL,  -- NICE Excel has no comparators
            a.decision_date, YEAR(a.decision_date),
            NULL, NULL, NULL,  -- G-BA-specific fields
            a.process_type, a.source_detail,
            a.source_url
        FROM assessments a
        JOIN drugs d ON a.drug_id = d.drug_id
        WHERE a.agency_id = 'nice'
          AND NOT EXISTS (
              SELECT 1 FROM search_documents sd
              WHERE sd.id = CONCAT('nice-', a.source_id)
          )
    """)

    count_after = db.fetch_all("SELECT COUNT(*) FROM search_documents")[0][0]
    new_docs = count_after - count_before
    log.info("Denormalized: %d new documents (total: %d)", new_docs, count_after)

    # Build embedding texts in Python (more flexible than SQL)
    _update_embedding_texts()

    return new_docs


def _update_embedding_texts():
    """Build embedding_text + embedding_hash for documents that need it."""
    rows = db.fetch_all("""
        SELECT id, agency_id, drug_inn, drug_brand, atc_code,
               therapeutic_area, indication, source_rating, overall_outcome,
               comparator_names, benefit_extent, evidence_certainty,
               endpoint_summary, nice_comment
        FROM search_documents
        WHERE embedding_text IS NULL
    """)

    if not rows:
        return

    engine = db.get_engine()
    raw_conn = engine.raw_connection()
    cursor = raw_conn.cursor()

    for row in rows:
        data = {
            "id": row[0], "agency_id": row[1], "drug_inn": row[2],
            "drug_brand": row[3], "atc_code": row[4],
            "therapeutic_area": row[5], "indication": row[6],
            "source_rating": row[7], "overall_outcome": row[8],
            "comparator_names": row[9], "benefit_extent": row[10],
            "evidence_certainty": row[11], "endpoint_summary": row[12],
            "nice_comment": row[13],
        }
        emb_text = _build_embedding_text(data)
        emb_hash = hashlib.sha256(emb_text.encode("utf-8")).hexdigest()
        cursor.execute(
            "UPDATE search_documents SET embedding_text=?, embedding_hash=? WHERE id=?",
            emb_text, emb_hash, row[0],
        )

    raw_conn.commit()
    cursor.close()
    raw_conn.close()
    log.info("Built embedding text for %d documents", len(rows))
