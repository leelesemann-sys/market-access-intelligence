"""Download and parse PDF documents into text.

Uses pdfplumber for text extraction — handles German characters,
tables, and multi-column layouts well.
"""
import logging
import re
import time
from pathlib import Path

import pdfplumber
import requests

log = logging.getLogger(__name__)

PDF_DIR = Path(__file__).parent.parent.parent / "data" / "downloads" / "pdfs" / "gba"

# Section heading patterns in Tragende Gründe documents
SECTION_PATTERNS = [
    re.compile(r"^\s*(\d+\.?\s+.{10,80})$", re.MULTILINE),  # "1. Zusatznutzen..."
    re.compile(r"^((?:I{1,3}V?|V?I{0,3})\.\s+.{10,80})$", re.MULTILINE),  # Roman numerals
]


def download_pdf(url: str, source_id: str) -> Path | None:
    """Download a PDF to local storage.

    Saves to data/downloads/pdfs/gba/{source_id}/tragende_gruende.pdf.
    Returns the local path, or None on failure.
    """
    # Sanitize source_id for filesystem
    safe_id = re.sub(r'[^\w\-]', '_', source_id)
    pdf_dir = PDF_DIR / safe_id
    pdf_dir.mkdir(parents=True, exist_ok=True)

    # Determine filename from URL
    filename = url.rsplit("/", 1)[-1] if "/" in url else "document.pdf"
    local_path = pdf_dir / filename

    if local_path.exists() and local_path.stat().st_size > 0:
        log.debug("PDF already exists: %s", local_path)
        return local_path

    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=60, headers={
                "User-Agent": "Mozilla/5.0 (compatible; HTAIntelligence/1.0)"
            })
            resp.raise_for_status()
            local_path.write_bytes(resp.content)
            log.debug("Downloaded %d KB → %s", len(resp.content) // 1024, local_path)
            return local_path
        except requests.RequestException as e:
            if attempt < 2:
                time.sleep(2 ** (attempt + 1))
            else:
                log.error("Failed to download %s: %s", url, e)
                return None


def extract_text(pdf_path: Path) -> list[dict]:
    """Extract text from a PDF file.

    Returns list of {page_num, text} dicts.
    """
    pages = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append({"page_num": i + 1, "text": text})
    except Exception as e:
        log.error("Failed to parse %s: %s", pdf_path, e)
        return []

    return pages


def detect_sections(pages: list[dict]) -> list[dict]:
    """Detect section boundaries in extracted text.

    Returns list of {title, start_page, text} dicts.
    Merges text between section headings into coherent blocks.
    """
    full_text = "\n".join(p["text"] for p in pages)

    # Find all section headings
    headings = []
    for pattern in SECTION_PATTERNS:
        for match in pattern.finditer(full_text):
            title = match.group(1).strip()
            # Filter out false positives (too short, page numbers, etc.)
            if len(title) > 15 and not title.replace(".", "").strip().isdigit():
                headings.append((match.start(), title))

    # Deduplicate and sort by position
    seen = set()
    unique_headings = []
    for pos, title in sorted(headings):
        # Skip duplicates (same title within 200 chars)
        key = title[:30].lower()
        if key not in seen:
            seen.add(key)
            unique_headings.append((pos, title))

    if not unique_headings:
        # No sections found — return full text as one block
        return [{"title": "Volltext", "text": full_text}]

    # Split text at section boundaries
    sections = []
    for i, (pos, title) in enumerate(unique_headings):
        end = unique_headings[i + 1][0] if i + 1 < len(unique_headings) else len(full_text)
        section_text = full_text[pos:end].strip()
        if section_text:
            sections.append({"title": title, "text": section_text})

    # Add any text before the first heading as "Einleitung"
    if unique_headings[0][0] > 100:
        intro_text = full_text[:unique_headings[0][0]].strip()
        if intro_text:
            sections.insert(0, {"title": "Einleitung", "text": intro_text})

    return sections
