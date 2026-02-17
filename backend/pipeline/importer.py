"""Import AssessmentRecord objects into the SQL database.

Handles UPSERT logic: new records are inserted, existing records
(matched on agency_id + source_id) are updated.
"""

import logging

from sqlalchemy import text

import db
from sources.base_source import AssessmentRecord

log = logging.getLogger(__name__)


def import_records(records: list[AssessmentRecord]) -> dict:
    """Import a list of AssessmentRecords into the database.

    Returns stats dict with counts for each table.
    """
    stats = {
        "drugs_inserted": 0,
        "drugs_existing": 0,
        "assessments_inserted": 0,
        "assessments_updated": 0,
        "outcomes_inserted": 0,
        "comparators_inserted": 0,
    }

    engine = db.get_engine()
    batch_size = 50

    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        with engine.begin() as conn:
            for record in batch:
                # 1. Upsert drug
                drug_id = _upsert_drug(conn, record, stats)

                # 2. Upsert assessment
                assessment_id = _upsert_assessment(conn, record, drug_id, stats)

                # 3. Insert subgroup outcomes (delete + re-insert)
                _replace_outcomes(conn, assessment_id, record, stats)

                # 4. Insert comparators (delete + re-insert)
                _replace_comparators(conn, assessment_id, record, stats)

        if (i + batch_size) % 200 == 0 or i + batch_size >= len(records):
            log.info("  ... processed %d / %d records", min(i + batch_size, len(records)), len(records))

    log.info(
        "Import complete: %d drugs (new: %d), %d assessments (new: %d, updated: %d), "
        "%d outcomes, %d comparators",
        stats["drugs_inserted"] + stats["drugs_existing"],
        stats["drugs_inserted"],
        stats["assessments_inserted"] + stats["assessments_updated"],
        stats["assessments_inserted"],
        stats["assessments_updated"],
        stats["outcomes_inserted"],
        stats["comparators_inserted"],
    )
    return stats


def _upsert_drug(conn, record: AssessmentRecord, stats: dict) -> int:
    """Find or create drug record. Returns drug_id."""
    # Look up by INN + brand_name
    row = conn.execute(
        text("SELECT drug_id FROM drugs WHERE inn = :inn AND brand_name = :brand"),
        {"inn": record.drug_inn, "brand": record.drug_brand},
    ).fetchone()

    if row:
        # Update orphan_drug flag if it changed (e.g. fix from parser)
        if record.orphan_drug:
            conn.execute(
                text("UPDATE drugs SET orphan_drug = 1 WHERE drug_id = :id AND orphan_drug = 0"),
                {"id": row[0]},
            )
        stats["drugs_existing"] += 1
        return row[0]

    # Insert new drug
    result = conn.execute(
        text("""
            INSERT INTO drugs (inn, brand_name, atc_code, orphan_drug)
            OUTPUT INSERTED.drug_id
            VALUES (:inn, :brand, :atc, :orphan)
        """),
        {
            "inn": record.drug_inn,
            "brand": record.drug_brand,
            "atc": record.atc_code or None,
            "orphan": 1 if record.orphan_drug else 0,
        },
    )
    drug_id = result.fetchone()[0]
    stats["drugs_inserted"] += 1
    return drug_id


def _upsert_assessment(conn, record: AssessmentRecord, drug_id: int, stats: dict) -> int:
    """Insert or update assessment. Returns assessment_id."""
    # Check if exists
    row = conn.execute(
        text("SELECT assessment_id FROM assessments WHERE agency_id = :agency AND source_id = :src"),
        {"agency": record.agency_id, "src": record.source_id},
    ).fetchone()

    if row:
        # Update existing
        conn.execute(
            text("""
                UPDATE assessments SET
                    drug_id = :drug_id,
                    source_numeric_id = :src_num,
                    assessment_type = :atype,
                    therapeutic_area = :area,
                    indication = :indication,
                    indication_short = :ind_short,
                    indication_icd10 = :icd10,
                    decision_date = :ddate,
                    decision_end_date = :dend,
                    publication_year = :pub_year,
                    source_rating = :rating,
                    source_detail = :detail,
                    overall_outcome = :outcome,
                    source_url = :url,
                    process_type = :proc,
                    technology_type = :tech,
                    updated_at = GETDATE()
                WHERE assessment_id = :aid
            """),
            {
                "aid": row[0],
                "drug_id": drug_id,
                "src_num": record.source_numeric_id,
                "atype": record.assessment_type,
                "area": record.therapeutic_area or None,
                "indication": record.indication or None,
                "ind_short": record.indication_short or None,
                "icd10": ",".join(record.indication_icd10) if record.indication_icd10 else None,
                "ddate": record.decision_date,
                "dend": record.decision_end_date,
                "pub_year": record.publication_year,
                "rating": record.source_rating,
                "detail": record.source_detail or None,
                "outcome": record.overall_outcome,
                "url": record.source_url,
                "proc": record.process_type,
                "tech": record.technology_type,
            },
        )
        stats["assessments_updated"] += 1
        return row[0]

    # Insert new
    result = conn.execute(
        text("""
            INSERT INTO assessments (
                agency_id, drug_id, source_id, source_numeric_id,
                assessment_type, therapeutic_area, indication, indication_short,
                indication_icd10, decision_date, decision_end_date, publication_year,
                source_rating, source_detail, overall_outcome,
                source_url, pdf_url, process_type, technology_type
            )
            OUTPUT INSERTED.assessment_id
            VALUES (
                :agency, :drug_id, :src, :src_num,
                :atype, :area, :indication, :ind_short,
                :icd10, :ddate, :dend, :pub_year,
                :rating, :detail, :outcome,
                :url, :pdf, :proc, :tech
            )
        """),
        {
            "agency": record.agency_id,
            "drug_id": drug_id,
            "src": record.source_id,
            "src_num": record.source_numeric_id,
            "atype": record.assessment_type,
            "area": record.therapeutic_area or None,
            "indication": record.indication or None,
            "ind_short": record.indication_short or None,
            "icd10": ",".join(record.indication_icd10) if record.indication_icd10 else None,
            "ddate": record.decision_date,
            "dend": record.decision_end_date,
            "pub_year": record.publication_year,
            "rating": record.source_rating,
            "detail": record.source_detail or None,
            "outcome": record.overall_outcome,
            "url": record.source_url,
            "pdf": record.pdf_url,
            "proc": record.process_type,
            "tech": record.technology_type,
        },
    )
    assessment_id = result.fetchone()[0]
    stats["assessments_inserted"] += 1
    return assessment_id


def _replace_outcomes(conn, assessment_id: int, record: AssessmentRecord, stats: dict):
    """Delete and re-insert subgroup outcomes for an assessment."""
    conn.execute(
        text("DELETE FROM assessment_outcomes WHERE assessment_id = :aid"),
        {"aid": assessment_id},
    )

    for sg in record.subgroups:
        conn.execute(
            text("""
                INSERT INTO assessment_outcomes (
                    assessment_id, subgroup_id,
                    subgroup_name, patient_population,
                    indication_decision, indication_short,
                    benefit_extent, benefit_text, evidence_certainty,
                    ep_mortality_symbol, ep_mortality_text,
                    ep_morbidity_symbol, ep_morbidity_text,
                    ep_qol_symbol, ep_qol_text,
                    ep_safety_symbol, ep_safety_text,
                    rationale_summary
                ) VALUES (
                    :aid, :sgid,
                    :sgname, :pop,
                    :ind_dec, :ind_short,
                    :benefit, :benefit_text, :evidence,
                    :mort_sym, :mort_text,
                    :morb_sym, :morb_text,
                    :qol_sym, :qol_text,
                    :safety_sym, :safety_text,
                    :rationale
                )
            """),
            {
                "aid": assessment_id,
                "sgid": sg.subgroup_id,
                "sgname": sg.subgroup_name or None,
                "pop": sg.patient_population or None,
                "ind_dec": sg.indication_decision or None,
                "ind_short": sg.indication_short or None,
                "benefit": sg.benefit_extent or None,
                "benefit_text": sg.benefit_text or None,
                "evidence": sg.evidence_certainty or None,
                "mort_sym": sg.ep_mortality_symbol or None,
                "mort_text": sg.ep_mortality_text or None,
                "morb_sym": sg.ep_morbidity_symbol or None,
                "morb_text": sg.ep_morbidity_text or None,
                "qol_sym": sg.ep_qol_symbol or None,
                "qol_text": sg.ep_qol_text or None,
                "safety_sym": sg.ep_safety_symbol or None,
                "safety_text": sg.ep_safety_text or None,
                "rationale": sg.rationale_summary or None,
            },
        )
        stats["outcomes_inserted"] += 1


def _replace_comparators(conn, assessment_id: int, record: AssessmentRecord, stats: dict):
    """Delete and re-insert comparators for an assessment."""
    conn.execute(
        text("DELETE FROM comparators WHERE assessment_id = :aid"),
        {"aid": assessment_id},
    )

    # Deduplicate comparators by name
    seen = set()
    for comp in record.comparators:
        if comp.comparator_name in seen:
            continue
        seen.add(comp.comparator_name)

        conn.execute(
            text("""
                INSERT INTO comparators (assessment_id, comparator_name, comparator_atc, comparator_type)
                VALUES (:aid, :name, :atc, :ctype)
            """),
            {
                "aid": assessment_id,
                "name": comp.comparator_name,
                "atc": comp.comparator_atc or None,
                "ctype": comp.comparator_type or "active",
            },
        )
        stats["comparators_inserted"] += 1
