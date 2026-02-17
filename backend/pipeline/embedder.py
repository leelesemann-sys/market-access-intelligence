"""Embedding generation via Azure OpenAI.

Computes 256-dim embeddings for all search_documents that have
embedding_text but no indexed_at timestamp (not yet indexed).
"""
import logging

from openai import AzureOpenAI

import config
import db

log = logging.getLogger(__name__)

_client = None


def _get_client() -> AzureOpenAI:
    global _client
    if _client is None:
        _client = AzureOpenAI(
            azure_endpoint=config.OPENAI_ENDPOINT,
            api_key=config.OPENAI_KEY,
            api_version="2024-06-01",
        )
    return _client


def _embed_batch(texts: list[str]) -> list[list[float]]:
    """Compute embeddings for a batch of texts."""
    client = _get_client()
    response = client.embeddings.create(
        input=texts,
        model=config.OPENAI_EMBEDDING_DEPLOYMENT,
        dimensions=config.OPENAI_EMBEDDING_DIMENSIONS,
    )
    return [item.embedding for item in response.data]


def get_embedding(text: str) -> list[float]:
    """Compute a single embedding (for query time)."""
    return _embed_batch([text])[0]


def embed_and_collect(batch_size: int = 100) -> list[dict]:
    """Compute embeddings and return documents with vectors for indexing.

    Returns list of dicts with all search_documents fields + content_vector.
    """
    rows = db.fetch_all("""
        SELECT id, embedding_text, agency_id, country_code,
               drug_inn, drug_brand, atc_code,
               therapeutic_area, indication, source_rating, overall_outcome,
               comparator_names, decision_date, decision_year,
               benefit_extent, evidence_certainty, endpoint_summary,
               nice_process, nice_comment, source_url
        FROM search_documents
        WHERE embedding_text IS NOT NULL
          AND indexed_at IS NULL
        ORDER BY id
    """)

    if not rows:
        log.info("No documents to embed")
        return []

    log.info("%d documents to embed", len(rows))
    results = []

    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        texts = [r[1] for r in batch]  # embedding_text

        try:
            vectors = _embed_batch(texts)
        except Exception as e:
            log.error("Embedding error at batch %d: %s", i, e)
            continue

        for row, vector in zip(batch, vectors):
            results.append({
                "id": row[0],
                "embedding_text": row[1],
                "agency_id": row[2],
                "country_code": row[3],
                "drug_inn": row[4],
                "drug_brand": row[5],
                "atc_code": row[6],
                "therapeutic_area": row[7],
                "indication": row[8],
                "source_rating": row[9],
                "overall_outcome": row[10],
                "comparator_names": row[11],
                "decision_date": row[12],
                "decision_year": row[13],
                "benefit_extent": row[14],
                "evidence_certainty": row[15],
                "endpoint_summary": row[16],
                "nice_process": row[17],
                "nice_comment": row[18],
                "source_url": row[19],
                "content_vector": vector,
            })

        if (i + batch_size) % 500 == 0 or i + batch_size >= len(rows):
            log.info("  ... %d / %d embedded", len(results), len(rows))

    log.info("Embedding complete: %d documents with vectors", len(results))
    return results
