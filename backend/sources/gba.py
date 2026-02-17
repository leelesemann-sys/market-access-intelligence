"""G-BA (Germany) data source adapter.

Downloads the machine-readable XML file (G-BA_Beschluss_Info) containing
all benefit assessment decisions under § 35a SGB V, and parses it into
AssessmentRecord objects.

Data source: https://ais.g-ba.de/aktuelle-version
Format: XML (24+ MB), updated 1st and 15th of each month
Coverage: All currently valid decisions since 2011 (~998 decisions, ~1658 subgroups)
"""

import html
import logging
import re
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

import requests

import config
from sources.base_source import (
    AssessmentRecord,
    ComparatorRecord,
    HTASource,
    SubgroupOutcome,
)

log = logging.getLogger(__name__)

# HTML entity → readable symbol mapping for endpoint arrows
ENDPOINT_SYMBOLS = {
    "&uarr;&uarr;": "↑↑",
    "&uarr;": "↑",
    "&harr;": "↔",
    "&darr;": "↓",
    "&darr;&darr;": "↓↓",
    "n.b.": "n.b.",
    "&Oslash;": "∅",
}

# ZN_A value → overall_outcome mapping
ZN_A_TO_OUTCOME = {
    "erheblich": "positive",
    "beträchtlich": "positive",
    "betraechtlich": "positive",           # XML uses ae instead of ä
    "gering": "positive",
    "nicht quantifizierbar": "positive",
    "nicht quantifizierbar, weil die wissenschaftliche Datengrundlage dies nicht zulässt": "positive",
    "nicht quantifizierbar, weil die wissenschaftliche Datengrundlage dies nicht zulaesst": "positive",
    "nicht quantifizierbar, weil die erforderlichen Nachweise nicht vollständig sind": "restricted",
    "nicht quantifizierbar, weil die erforderlichen Nachweise nicht vollstaendig sind": "restricted",
    "gilt als belegt": "positive",
    "ist nicht belegt": "negative",
    "gilt als nicht belegt": "negative",
    "geringerer Nutzen": "negative",
}

# REG_NB → assessment_type
REG_NB_TO_TYPE = {
    "Beschluss_reg": "regular",
    "Beschluss_orph": "orphan",
    "Beschluss_antib": "antibiotic",
}


def _attr(el, tag, default=""):
    """Get value attribute from child element, or default."""
    child = el.find(tag)
    if child is not None:
        return child.get("value", default)
    return default


def _text(el, tag, default=""):
    """Get text content from child element (for CDATA/HTML fields)."""
    child = el.find(tag)
    if child is not None and child.text:
        return child.text
    return default


def _strip_html(html_str):
    """Remove HTML tags and decode entities from CDATA content."""
    if not html_str:
        return ""
    # Decode HTML entities
    text = html.unescape(html_str)
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _decode_endpoint_symbol(raw):
    """Convert HTML entity arrow symbols to readable characters."""
    if not raw:
        return ""
    decoded = html.unescape(raw)
    # Map known symbols
    for entity, symbol in ENDPOINT_SYMBOLS.items():
        entity_decoded = html.unescape(entity)
        if decoded == entity_decoded:
            return symbol
    return decoded


def _collect_atc_codes(ws_info_el):
    """Extract ATC codes from WS_INFO or WS_INFO_BEW elements."""
    codes = []
    if ws_info_el is None:
        return ""
    for atc_el in ws_info_el.findall(".//ATC/ATC_CODE"):
        code = atc_el.get("value", "")
        if code:
            codes.append(code)
    return ",".join(codes)


def _determine_overall_outcome(subgroups):
    """Determine overall assessment outcome from subgroup results.

    Logic: If ANY subgroup has positive benefit → positive.
    If all negative → negative. Mixed → restricted.
    """
    if not subgroups:
        return "negative"

    outcomes = set()
    for sg in subgroups:
        raw = sg.benefit_extent or ""
        # Try both original and XML-encoded variants
        outcome = ZN_A_TO_OUTCOME.get(raw)
        if outcome is None:
            # Normalize: replace umlauts
            normalized = raw.replace("ä", "ae").replace("ü", "ue").replace("ö", "oe").replace("ß", "ss")
            outcome = ZN_A_TO_OUTCOME.get(normalized, "negative")
        outcomes.add(outcome)

    if outcomes == {"positive"}:
        return "positive"
    if outcomes == {"negative"}:
        return "negative"
    return "restricted"  # mixed results across subgroups


class GBASource(HTASource):
    """G-BA (Germany) data source."""

    @property
    def agency_id(self) -> str:
        return "gba"

    def fetch(self) -> list[AssessmentRecord]:
        """Download and parse the complete G-BA XML file."""
        xml_path = self._download()
        return self._parse(xml_path)

    def fetch_updates(self, since: date) -> list[AssessmentRecord]:
        """G-BA XML is a complete dump — fetch all then filter."""
        all_records = self.fetch()
        return [r for r in all_records if r.decision_date and r.decision_date >= since]

    def _download(self) -> Path:
        """Download XML via POST → 302 → S3 signed URL."""
        download_dir = Path(__file__).parent.parent.parent / "data" / "downloads"
        download_dir.mkdir(parents=True, exist_ok=True)
        xml_path = download_dir / "gba_beschluss_info.xml"

        log.info("Downloading G-BA XML from %s ...", config.GBA_XML_URL)

        # Step 1: POST to get redirect
        resp = requests.post(
            config.GBA_XML_URL,
            data={"nutzungsbedingungenAkzeptiert": "1"},
            allow_redirects=False,
            timeout=30,
        )
        if resp.status_code != 302:
            raise RuntimeError(f"Expected 302 redirect, got {resp.status_code}")

        s3_url = resp.headers["Location"]

        # Step 2: GET the actual XML from S3
        resp2 = requests.get(s3_url, timeout=120)
        resp2.raise_for_status()

        xml_path.write_bytes(resp2.content)
        size_mb = len(resp2.content) / 1024 / 1024
        log.info("Downloaded %.1f MB → %s", size_mb, xml_path)

        return xml_path

    def _parse(self, xml_path: Path) -> list[AssessmentRecord]:
        """Parse G-BA XML into AssessmentRecord objects."""
        log.info("Parsing %s ...", xml_path)
        tree = ET.parse(xml_path)
        root = tree.getroot()

        records = []
        for be in root.findall("BE"):
            record = self._parse_decision(be)
            if record:
                records.append(record)

        log.info("Parsed %d decisions with %d total subgroups",
                 len(records), sum(len(r.subgroups) for r in records))
        return records

    def _parse_decision(self, be) -> AssessmentRecord:
        """Parse a single BE (Beschluss) element."""
        source_id = _attr(be, "ID_BE_AKZ")
        source_numeric_id_str = _attr(be, "ID_BE")
        reg_nb = _attr(be, "REG_NB")
        url = _attr(be, "URL")

        # ZUL (authorization) — take the first one for drug info
        zul = be.find("ZUL")
        brand_name = _attr(zul, "NAME_HN") if zul is not None else ""
        indication_full = _strip_html(_text(zul, "AWG")) if zul is not None else ""
        orphan = _attr(zul, "SOND_ZUL_ORPHAN") == "true" if zul is not None else False

        # Parse all patient groups
        subgroups = []
        comparators = []
        icd_codes = []
        drug_inn = ""
        atc_code = ""

        pat_coll = be.find("PAT_GR_INFO_COLLECTION")
        if pat_coll is not None:
            for pg in pat_coll.findall("ID_PAT_GR"):
                sg, comps = self._parse_patient_group(pg)
                subgroups.append(sg)
                comparators.extend(comps)

                # Drug info from first patient group
                if not drug_inn:
                    ws_bew = pg.find("WS_BEW")
                    if ws_bew is not None:
                        drug_inn = _attr(ws_bew, "NAME_WS_BEW")
                        atc_code = _collect_atc_codes(ws_bew.find("WS_INFO_BEW"))

                # ICD codes from first patient group
                if not icd_codes:
                    for icd in pg.findall("ICD"):
                        code = _attr(icd, "ID_ICD")
                        if code:
                            icd_codes.append(code)

        # Determine overall outcome from subgroup results
        overall_outcome = _determine_overall_outcome(subgroups)

        # Build source_rating from most favorable subgroup
        source_rating = self._best_source_rating(subgroups)

        # Decision date from first subgroup
        decision_date = None
        if subgroups:
            date_str = _attr(
                pat_coll.findall("ID_PAT_GR")[0], "DATUM_BE_VOM"
            ) if pat_coll is not None else ""
            if date_str:
                try:
                    decision_date = date.fromisoformat(date_str)
                except ValueError:
                    pass

        # Indication short from first subgroup
        indication_short = ""
        if pat_coll is not None:
            first_pg = pat_coll.findall("ID_PAT_GR")[0] if pat_coll.findall("ID_PAT_GR") else None
            if first_pg is not None:
                indication_short = _attr(first_pg, "AWG_KURZ")

        return AssessmentRecord(
            agency_id="gba",
            source_id=source_id,
            source_numeric_id=int(source_numeric_id_str) if source_numeric_id_str.isdigit() else None,
            drug_inn=drug_inn,
            drug_brand=brand_name,
            atc_code=atc_code.split(",")[0] if atc_code else "",  # primary ATC
            orphan_drug=orphan,
            assessment_type=REG_NB_TO_TYPE.get(reg_nb, "regular"),
            therapeutic_area="",  # will be derived from ICD codes later
            indication=indication_full,
            indication_short=indication_short,
            indication_icd10=icd_codes,
            decision_date=decision_date,
            source_rating=source_rating,
            source_detail="",  # full detail would come from Tragende Gründe PDF
            overall_outcome=overall_outcome,
            source_url=url,
            subgroups=subgroups,
            comparators=comparators,
        )

    def _parse_patient_group(self, pg) -> tuple[SubgroupOutcome, list[ComparatorRecord]]:
        """Parse a single patient group element."""
        subgroup_id = int(pg.get("value", "0"))

        # Benefit assessment
        zvt_zn = pg.find("ZVT_ZN")
        benefit_extent = _attr(zvt_zn, "ZN_A") if zvt_zn is not None else ""
        benefit_text = _strip_html(_text(zvt_zn, "ZN_TEXT")) if zvt_zn is not None else ""
        evidence = _attr(zvt_zn, "ZN_W") if zvt_zn is not None else ""
        comparator_in_zn = _attr(zvt_zn, "NAME_ZVT_ZN") if zvt_zn is not None else ""

        # Endpoint summaries
        def _ep(tag_prefix):
            ep_el = pg.find(f"ZSF_EP_{tag_prefix}")
            if ep_el is None:
                return "", ""
            symbol = _decode_endpoint_symbol(_attr(ep_el, f"EP_{tag_prefix}_GRAF"))
            text = _attr(ep_el, f"EP_{tag_prefix}_BES")
            return symbol, text

        mort_sym, mort_text = _ep("MORT")
        morb_sym, morb_text = _ep("MORB")
        qol_sym, qol_text = _ep("LEBQ")
        safety_sym, safety_text = _ep("UE")

        # Comparator therapy (from ZVT_BEST)
        comparators = []
        zvt_best = pg.find("ZVT_BEST")
        if zvt_best is not None:
            comp_name = _attr(zvt_best, "NAME_ZVT_BEST")
            comp_atc = _collect_atc_codes(zvt_best)
            if comp_name:
                comparators.append(ComparatorRecord(
                    comparator_name=comp_name,
                    comparator_atc=comp_atc,
                    comparator_type="active",
                ))

        sg = SubgroupOutcome(
            subgroup_id=subgroup_id,
            subgroup_name=_strip_html(_text(pg, "NAME_PAT_GR")),
            patient_population=_strip_html(_text(pg, "NAME_PAT_GR")),
            indication_decision=_strip_html(_text(pg, "AWG_BESCHLUSS") or _attr(pg, "AWG_BESCHLUSS")),
            indication_short=_attr(pg, "AWG_KURZ"),
            benefit_extent=benefit_extent,
            benefit_text=benefit_text,
            evidence_certainty=evidence,
            ep_mortality_symbol=mort_sym,
            ep_mortality_text=mort_text,
            ep_morbidity_symbol=morb_sym,
            ep_morbidity_text=morb_text,
            ep_qol_symbol=qol_sym,
            ep_qol_text=qol_text,
            ep_safety_symbol=safety_sym,
            ep_safety_text=safety_text,
            rationale_summary=_strip_html(_text(pg, "ZSF_TRG")),
            comparator_name=comparator_in_zn or (comparators[0].comparator_name if comparators else ""),
            comparator_atc=comparators[0].comparator_atc if comparators else "",
        )

        return sg, comparators

    def _best_source_rating(self, subgroups):
        """Return the most favorable benefit extent across subgroups."""
        priority = [
            "erheblich",
            "beträchtlich", "betraechtlich",
            "gering",
            "nicht quantifizierbar",
            "gilt als belegt",
        ]
        for p in priority:
            for sg in subgroups:
                if sg.benefit_extent and sg.benefit_extent.startswith(p):
                    return sg.benefit_extent
        # Fallback: first subgroup's rating
        return subgroups[0].benefit_extent if subgroups else ""
