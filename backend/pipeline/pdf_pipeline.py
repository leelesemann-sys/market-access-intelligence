"""PDF Pipeline: Scrape → Download → Parse → Chunk → SQL → Embed → Index.

Processes G-BA "Tragende Gründe" PDFs into search-ready chunks.
"""
import logging
from datetime import datetime

from sqlalchemy import text

import db
from pipeline.pdf_scraper import scrape_and_store
from pipeline.pdf_parser import download_pdf, extract_text, detect_sections
from pipeline.chunker import chunk_document
from pipeline.embedder import embed_and_collect
from pipeline.indexer import create_index, upload_documents, mark_indexed

log = logging.getLogger(__name__)


def _get_tragende_gruende(limit: int | None = None) -> list[dict]:
    """Get assessments that have Tragende Gründe PDFs but no chunks yet."""
    query = """
        SELECT d.document_id, d.assessment_id, d.doc_url,
               a.source_id, drg.inn AS drug_inn
        FROM documents d
        JOIN assessments a ON a.assessment_id = d.assessment_id
        JOIN drugs drg ON drg.drug_id = a.drug_id
        WHERE d.doc_type = 'tragende_gruende'
          AND NOT EXISTS (
              SELECT 1 FROM pdf_chunks pc WHERE pc.document_id = d.document_id
          )
        ORDER BY a.decision_date DESC
    """
    rows = db.fetch_all(query)
    results = [
        {
            "document_id": r[0],
            "assessment_id": r[1],
            "doc_url": r[2],
            "source_id": r[3],
            "drug_inn": r[4],
        }
        for r in rows
    ]
    if limit:
        results = results[:limit]
    return results


def _store_chunks(document_id: int, assessment_id: int,
                  source_id: str, drug_inn: str, doc_url: str,
                  chunks: list[dict]) -> int:
    """Insert chunks into pdf_chunks and search_documents tables.

    Returns number of chunks stored.
    """
    engine = db.get_engine()
    stored = 0

    with engine.begin() as conn:
        for chunk in chunks:
            # Insert into pdf_chunks
            result = conn.execute(text("""
                INSERT INTO pdf_chunks
                    (document_id, assessment_id, chunk_index, section_title,
                     chunk_text, char_count, embedding_hash)
                VALUES
                    (:doc_id, :assess_id, :idx, :section,
                     :text, :chars, :hash)
            """), {
                "doc_id": document_id,
                "assess_id": assessment_id,
                "idx": chunk["chunk_index"],
                "section": chunk["section_title"],
                "text": chunk["chunk_text"],
                "chars": chunk["char_count"],
                "hash": chunk["embedding_hash"],
            })

            chunk_id = conn.execute(text("SELECT SCOPE_IDENTITY()")).scalar()

            # Build search_documents ID
            search_doc_id = f"gba-pdf-{source_id}-c{chunk['chunk_index']}"

            # Insert into search_documents
            conn.execute(text("""
                INSERT INTO search_documents
                    (id, agency_id, country_code, drug_inn,
                     indication, embedding_text,
                     doc_type, parent_id, chunk_index, section_title,
                     document_url)
                SELECT :id, 'gba', 'DE', :inn,
                       :indication, :emb_text,
                       'pdf_chunk', :parent_id, :idx, :section,
                       :doc_url
                WHERE NOT EXISTS (
                    SELECT 1 FROM search_documents WHERE id = :id
                )
            """), {
                "id": search_doc_id,
                "inn": drug_inn,
                "indication": chunk["section_title"],
                "emb_text": chunk["embedding_text"],
                "parent_id": f"gba-{source_id}",
                "idx": chunk["chunk_index"],
                "section": chunk["section_title"],
                "doc_url": doc_url,
            })

            # Update pdf_chunks with search_doc_id
            if chunk_id:
                conn.execute(text("""
                    UPDATE pdf_chunks SET search_doc_id = :sid
                    WHERE chunk_id = :cid
                """), {"sid": search_doc_id, "cid": chunk_id})

            stored += 1

    return stored


def run_pdf_pipeline(limit: int | None = None,
                     skip_scrape: bool = False,
                     skip_download: bool = False) -> dict:
    """Run the full PDF processing pipeline.

    Args:
        limit: Max documents to process (for testing).
        skip_scrape: Skip scraping PDF URLs (use existing).
        skip_download: Skip downloading PDFs (use cached files).

    Returns:
        Dict with stats: {scraped, downloaded, parsed, chunks_total, embedded, indexed, errors}.
    """
    stats = {
        "scraped": 0, "downloaded": 0, "parsed": 0,
        "chunks_total": 0, "embedded": 0, "indexed": 0, "errors": 0,
    }

    # Step 1: Scrape PDF URLs from G-BA pages
    if not skip_scrape:
        log.info("=== Step 1: Scrape PDF URLs ===")
        scrape_stats = scrape_and_store(limit=limit)
        stats["scraped"] = scrape_stats.get("scraped", 0)
        log.info("Scraped: %s", scrape_stats)

    # Step 2: Download, parse, chunk, store
    log.info("=== Step 2: Download + Parse + Chunk ===")
    docs = _get_tragende_gruende(limit=limit)
    log.info("Found %d Tragende Gründe documents to process", len(docs))

    for i, doc in enumerate(docs):
        try:
            # Download
            if not skip_download:
                pdf_path = download_pdf(doc["doc_url"], doc["source_id"])
                if not pdf_path:
                    stats["errors"] += 1
                    continue
                stats["downloaded"] += 1
            else:
                # Try to find cached file
                pdf_path = download_pdf(doc["doc_url"], doc["source_id"])
                if not pdf_path:
                    stats["errors"] += 1
                    continue

            # Parse
            pages = extract_text(pdf_path)
            if not pages:
                log.warning("No text extracted from %s", doc["source_id"])
                stats["errors"] += 1
                continue

            sections = detect_sections(pages)
            stats["parsed"] += 1

            # Chunk
            metadata = {
                "drug_inn": doc["drug_inn"],
                "source_id": doc["source_id"],
            }
            chunks = chunk_document(sections, metadata)

            if not chunks:
                log.warning("No chunks generated for %s", doc["source_id"])
                continue

            # Store in SQL
            stored = _store_chunks(
                document_id=doc["document_id"],
                assessment_id=doc["assessment_id"],
                source_id=doc["source_id"],
                drug_inn=doc["drug_inn"],
                doc_url=doc["doc_url"],
                chunks=chunks,
            )
            stats["chunks_total"] += stored

        except Exception as e:
            log.error("Error processing %s: %s", doc["source_id"], e)
            stats["errors"] += 1

        if (i + 1) % 25 == 0:
            log.info("  ... %d / %d processed (%d chunks)",
                     i + 1, len(docs), stats["chunks_total"])

    log.info("Parse+chunk complete: %d parsed, %d chunks, %d errors",
             stats["parsed"], stats["chunks_total"], stats["errors"])

    # Step 3: Embed + Index (uses existing pipeline)
    if stats["chunks_total"] > 0:
        log.info("=== Step 3: Embed + Index ===")
        create_index()

        docs_with_vectors = embed_and_collect(batch_size=100)
        stats["embedded"] = len(docs_with_vectors)

        if docs_with_vectors:
            uploaded = upload_documents(docs_with_vectors)
            stats["indexed"] = uploaded
            doc_ids = [d["id"] for d in docs_with_vectors]
            mark_indexed(doc_ids)

    log.info("PDF pipeline complete: %s", stats)
    return stats
