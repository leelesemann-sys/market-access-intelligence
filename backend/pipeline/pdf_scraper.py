"""Scrape PDF URLs from G-BA assessment pages.

Each G-BA assessment has a detail page (source_url) with links to
multiple PDF documents. This module discovers and classifies those links.
"""
import logging
import re
import time

import requests
from lxml import html as lxml_html

import db

log = logging.getLogger(__name__)

# PDF type classification by filename keywords
PDF_CLASSIFIERS = [
    ("tragende_gruende", re.compile(r"(tragende|trag).{0,5}(gr[uü]nde|gruende)", re.I)),
    ("beschlusstext", re.compile(r"beschluss.{0,5}(text)?.*banz", re.I)),
    ("iqwig", re.compile(r"iqwig|nutzenbewertung", re.I)),
    ("zusammenfassung", re.compile(r"zusammenfass", re.I)),
    ("geltende_fassung", re.compile(r"geltende.{0,5}fassung", re.I)),
]

G_BA_BASE = "https://www.g-ba.de"
RATE_LIMIT_SEC = 1.0
MAX_RETRIES = 3


def _classify_pdf(url: str, link_text: str = "") -> str:
    """Classify a PDF URL by its filename or link text."""
    combined = url + " " + link_text
    for doc_type, pattern in PDF_CLASSIFIERS:
        if pattern.search(combined):
            return doc_type
    return "other"


def _scrape_page(url: str) -> list[dict]:
    """Scrape a single G-BA assessment page for PDF links.

    Returns list of {doc_type, doc_url, doc_title}.
    """
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, timeout=30, headers={
                "User-Agent": "Mozilla/5.0 (compatible; HTAIntelligence/1.0)"
            })
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            if attempt < MAX_RETRIES - 1:
                wait = 2 ** (attempt + 1)
                log.warning("Retry %d for %s: %s (waiting %ds)", attempt + 1, url, e, wait)
                time.sleep(wait)
            else:
                log.error("Failed to fetch %s after %d retries: %s", url, MAX_RETRIES, e)
                return []

    tree = lxml_html.fromstring(resp.content)
    pdfs = []

    # Find all links to PDFs
    for link in tree.xpath("//a[contains(@href, '/downloads/')]"):
        href = link.get("href", "")
        if not href.lower().endswith(".pdf"):
            continue

        # Make absolute URL
        if href.startswith("/"):
            href = G_BA_BASE + href

        link_text = (link.text_content() or "").strip()
        doc_type = _classify_pdf(href, link_text)

        pdfs.append({
            "doc_type": doc_type,
            "doc_url": href,
            "doc_title": link_text[:500] if link_text else "",
        })

    return pdfs


def scrape_and_store(limit: int = None) -> dict:
    """Scrape PDF URLs for all G-BA assessments and store in documents table.

    Args:
        limit: Max assessments to process (for testing).

    Returns:
        Dict with counts: {scraped, pdfs_found, tragende_gruende, errors}.
    """
    # Get assessments that haven't been scraped yet
    query = """
        SELECT a.assessment_id, a.source_id, a.source_url
        FROM assessments a
        WHERE a.agency_id = 'gba'
          AND a.source_url IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM documents d WHERE d.assessment_id = a.assessment_id
          )
        ORDER BY a.decision_date DESC
    """
    rows = db.fetch_all(query)

    if limit:
        rows = rows[:limit]

    log.info("Scraping PDF URLs for %d G-BA assessments...", len(rows))

    stats = {"scraped": 0, "pdfs_found": 0, "tragende_gruende": 0, "errors": 0}

    for i, row in enumerate(rows):
        assessment_id, source_id, source_url = row[0], row[1], row[2]

        if not source_url:
            continue

        pdfs = _scrape_page(source_url)

        if not pdfs:
            stats["errors"] += 1
            log.debug("No PDFs found on %s", source_url)
        else:
            stats["pdfs_found"] += len(pdfs)

            # Store in documents table
            engine = db.get_engine()
            with engine.begin() as conn:
                from sqlalchemy import text
                for pdf in pdfs:
                    conn.execute(text("""
                        INSERT INTO documents (assessment_id, doc_type, doc_title, doc_url)
                        SELECT :aid, :dtype, :title, :url
                        WHERE NOT EXISTS (
                            SELECT 1 FROM documents
                            WHERE assessment_id = :aid AND doc_url = :url
                        )
                    """), {
                        "aid": assessment_id,
                        "dtype": pdf["doc_type"],
                        "title": pdf["doc_title"],
                        "url": pdf["doc_url"],
                    })

                    if pdf["doc_type"] == "tragende_gruende":
                        stats["tragende_gruende"] += 1
                        # Also update assessment's pdf_url
                        conn.execute(text("""
                            UPDATE assessments SET pdf_url = :url
                            WHERE assessment_id = :aid AND pdf_url IS NULL
                        """), {"url": pdf["doc_url"], "aid": assessment_id})

        stats["scraped"] += 1

        if (i + 1) % 50 == 0:
            log.info("  ... %d / %d scraped (%d PDFs found)",
                     i + 1, len(rows), stats["pdfs_found"])

        time.sleep(RATE_LIMIT_SEC)

    log.info("Scraping complete: %d pages, %d PDFs, %d Tragende Gründe, %d errors",
             stats["scraped"], stats["pdfs_found"],
             stats["tragende_gruende"], stats["errors"])
    return stats
