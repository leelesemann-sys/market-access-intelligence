"""Abstract base class for HTA data sources.

Each country/agency gets its own concrete implementation
(e.g., gba.py for Germany, nice.py for UK).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date


@dataclass
class AssessmentRecord:
    """Standardized record returned by all HTA sources."""

    # Source identification
    agency_id: str                          # 'gba', 'nice'
    source_id: str                          # G-BA: ID_BE_AKZ, NICE: TA ID
    source_numeric_id: int = None           # G-BA: ID_BE, NICE: TA number

    # Drug
    drug_inn: str = None
    drug_brand: str = None
    atc_code: str = None
    orphan_drug: bool = False

    # Classification
    assessment_type: str = None             # 'regular', 'orphan', 'antibiotic'
    therapeutic_area: str = None
    indication: str = None
    indication_short: str = None
    indication_icd10: list[str] = field(default_factory=list)

    # Dates
    decision_date: date = None
    decision_end_date: date = None
    publication_year: str = None            # NICE financial year

    # Original rating (source-specific, never modified)
    source_rating: str = None
    source_detail: str = None

    # Harmonized outcome
    overall_outcome: str = None             # 'positive', 'restricted', 'negative'

    # Metadata
    source_url: str = None
    pdf_url: str = None
    process_type: str = None                # NICE: STA/MTA
    technology_type: str = None             # NICE: Pharmaceutical/Device

    # G-BA specific: subgroup outcomes
    subgroups: list = field(default_factory=list)

    # G-BA specific: comparators
    comparators: list = field(default_factory=list)


@dataclass
class SubgroupOutcome:
    """G-BA patient subgroup with its own benefit assessment."""

    subgroup_id: int = None
    subgroup_name: str = None
    patient_population: str = None
    indication_decision: str = None
    indication_short: str = None

    benefit_extent: str = None              # ZN_A enum
    benefit_text: str = None                # ZN_TEXT
    evidence_certainty: str = None          # Beleg/Hinweis/Anhaltspunkt

    ep_mortality_symbol: str = None
    ep_mortality_text: str = None
    ep_morbidity_symbol: str = None
    ep_morbidity_text: str = None
    ep_qol_symbol: str = None
    ep_qol_text: str = None
    ep_safety_symbol: str = None
    ep_safety_text: str = None

    rationale_summary: str = None

    comparator_name: str = None
    comparator_atc: str = None


@dataclass
class ComparatorRecord:
    """Comparator therapy used in an assessment."""

    comparator_name: str = None
    comparator_atc: str = None
    comparator_type: str = None             # 'active', 'placebo', 'bsc'


class HTASource(ABC):
    """Abstract base for HTA data source adapters."""

    @property
    @abstractmethod
    def agency_id(self) -> str:
        """Unique agency identifier (e.g., 'gba', 'nice')."""

    @abstractmethod
    def fetch(self) -> list[AssessmentRecord]:
        """Download and parse all assessments from the source.

        Returns a list of AssessmentRecord objects ready for import.
        """

    @abstractmethod
    def fetch_updates(self, since: date) -> list[AssessmentRecord]:
        """Fetch only assessments updated since the given date.

        For sources without delta support, this may fetch everything
        and filter client-side.
        """
