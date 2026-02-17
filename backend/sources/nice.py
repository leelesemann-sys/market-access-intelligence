"""NICE (UK) data source adapter.

Downloads the Technology Appraisal Recommendations Excel file and parses
it into AssessmentRecord objects.

Data source: https://a.storyblok.com/f/243782/x/2cb3eacc3b/ta-recommendations.xlsx
Format: Excel (206 KB), 9 columns, ~1475 records
Coverage: All NICE Technology Appraisals since 1999/00
"""

import logging
import re
from datetime import date
from pathlib import Path

import pandas as pd
import requests

import config
from sources.base_source import AssessmentRecord, HTASource

log = logging.getLogger(__name__)

# Categorisation → overall_outcome mapping (case-insensitive)
CATEGORISATION_TO_OUTCOME = {
    "recommended": "positive",
    "recommended (cdf)": "positive",
    "recommended (imf)": "positive",
    "optimised": "restricted",
    "optimised (cdf)": "restricted",
    "optimised (imf)": "restricted",
    "only in research": "restricted",
    "not recommended": "negative",
    "terminated appraisal - non submission": None,  # excluded
}

# Technology type normalization
TECH_TYPE_NORM = {
    "Medical Device": "Medical device",
}


def _normalize_categorisation(raw: str) -> str:
    """Normalize case inconsistencies in NICE categorisation values."""
    if not raw or pd.isna(raw):
        return ""
    # Map known variants to canonical form
    lookup = {
        "recommended": "Recommended",
        "not recommended": "Not recommended",
        "not Recommended": "Not recommended",
        "only in research": "Only in research",
        "optimised": "Optimised",
    }
    stripped = raw.strip()
    lower = stripped.lower()
    # Check exact lower-case matches first
    for key, canonical in lookup.items():
        if lower == key:
            return canonical
    # For compound values like "Recommended (CDF)", capitalize first word
    if lower.startswith("recommended"):
        return "Recommended" + stripped[len("recommended"):]
    if lower.startswith("optimised"):
        return "Optimised" + stripped[len("optimised"):]
    if lower.startswith("not recommended"):
        return "Not recommended" + stripped[len("not recommended"):]
    if lower.startswith("only in research"):
        return "Only in research" + stripped[len("only in research"):]
    if lower.startswith("terminated"):
        return "Terminated Appraisal - non submission"
    return stripped


def _extract_inn_from_technology(tech: str) -> str:
    """Extract INN (drug name) from NICE Technology column.

    Examples:
        "Pembrolizumab" → "Pembrolizumab"
        "Pembrolizumab with pemetrexed and platinum-based chemotherapy"
            → "Pembrolizumab"
        "Sacubitril valsartan" → "Sacubitril valsartan"
        "Natalizumab (originator and biosimilar)" → "Natalizumab"
        "Hip prostheses that have met the appropriate benchmark" → (unchanged)
    """
    if not tech:
        return ""
    # Remove parenthetical annotations
    cleaned = re.sub(r"\s*\([^)]*\)\s*", " ", tech).strip()
    # For combinations with "with", take the first drug
    # But keep compound INNs like "sacubitril valsartan"
    if " with " in cleaned.lower():
        cleaned = cleaned.split(" with ")[0].strip()
    # Remove trailing "and ..." for combinations
    if " and " in cleaned.lower() and len(cleaned.split()) > 3:
        parts = cleaned.split(" and ")
        cleaned = parts[0].strip()
    return cleaned


def _ta_number(ta_id: str) -> int:
    """Extract numeric part from TA ID. 'TA1125' → 1125."""
    match = re.search(r"(\d+)", ta_id)
    return int(match.group(1)) if match else 0


def _year_from_publication(pub_year: str) -> int:
    """Extract the first calendar year from financial year. '2024/25' → 2024."""
    if not pub_year or pd.isna(pub_year):
        return 0
    match = re.match(r"(\d{4})", str(pub_year))
    return int(match.group(1)) if match else 0


class NICESource(HTASource):
    """NICE (UK) data source — Excel-based."""

    @property
    def agency_id(self) -> str:
        return "nice"

    def fetch(self) -> list[AssessmentRecord]:
        """Download and parse the NICE TA Recommendations Excel."""
        excel_path = self._download()
        return self._parse(excel_path)

    def fetch_updates(self, since: date) -> list[AssessmentRecord]:
        """NICE Excel is a complete file — fetch all then filter."""
        all_records = self.fetch()
        return [r for r in all_records
                if r.decision_date and r.decision_date >= since]

    def _download(self) -> Path:
        """Download Excel file from Storyblok CDN."""
        download_dir = Path(__file__).parent.parent.parent / "data" / "downloads"
        download_dir.mkdir(parents=True, exist_ok=True)
        excel_path = download_dir / "nice_ta_recommendations.xlsx"

        log.info("Downloading NICE Excel from %s ...", config.NICE_EXCEL_URL)
        resp = requests.get(config.NICE_EXCEL_URL, timeout=30)
        resp.raise_for_status()

        excel_path.write_bytes(resp.content)
        log.info("Downloaded %.1f KB → %s", len(resp.content) / 1024, excel_path)
        return excel_path

    def _parse(self, excel_path: Path) -> list[AssessmentRecord]:
        """Parse NICE Excel into AssessmentRecord objects."""
        log.info("Parsing %s ...", excel_path)
        df = pd.read_excel(excel_path)

        records = []
        skipped = 0
        for _, row in df.iterrows():
            record = self._parse_row(row)
            if record is None:
                skipped += 1
                continue
            records.append(record)

        log.info("Parsed %d NICE records (%d skipped — terminated/non-submission)",
                 len(records), skipped)
        return records

    def _parse_row(self, row) -> AssessmentRecord | None:
        """Parse a single Excel row into an AssessmentRecord."""
        ta_id = str(row.get("TA ID", "")).strip()
        rec_no = row.get("Rec no.", "")
        categorisation_raw = str(row.get("Categorisation (for specific recommendation)", "")).strip()
        categorisation = _normalize_categorisation(categorisation_raw)

        # Determine outcome
        outcome = CATEGORISATION_TO_OUTCOME.get(categorisation.lower())
        if outcome is None:
            # Terminated / non-submission — skip
            return None

        technology = str(row.get("Technology", "")).strip()
        indication = str(row.get("Indication", "")).strip() if pd.notna(row.get("Indication")) else ""
        comment = str(row.get("Comment", "")).strip() if pd.notna(row.get("Comment")) else ""
        pub_year = str(row.get("Year of Publication", "")).strip()
        process_type = str(row.get("STA/MTA process", "")).strip()
        tech_type = str(row.get("Technology type", "")).strip()
        tech_type = TECH_TYPE_NORM.get(tech_type, tech_type)

        # Extract INN from technology name
        inn = _extract_inn_from_technology(technology)

        # Approximate decision date from financial year (April of that year)
        year = _year_from_publication(pub_year)
        decision_date = date(year, 4, 1) if year else None

        # Use TA ID + Rec no. as source_id to keep all sub-recommendations
        # (a single TA can evaluate multiple drugs for the same indication)
        source_id = f"{ta_id}-{int(rec_no)}" if pd.notna(rec_no) else ta_id

        return AssessmentRecord(
            agency_id="nice",
            source_id=source_id,
            source_numeric_id=int(rec_no) if pd.notna(rec_no) else _ta_number(ta_id),
            drug_inn=inn,
            drug_brand=technology,  # NICE lists full technology name as "brand"
            atc_code="",  # Not available in NICE Excel
            orphan_drug=False,
            assessment_type="regular",
            therapeutic_area="",  # Not directly available
            indication=indication,
            indication_short=indication[:255] if indication else "",
            indication_icd10=[],
            decision_date=decision_date,
            publication_year=pub_year,
            source_rating=categorisation,
            source_detail=comment,
            overall_outcome=outcome,
            source_url=f"https://www.nice.org.uk/guidance/{ta_id.lower()}" if ta_id else "",
            process_type=process_type,
            technology_type=tech_type,
        )
