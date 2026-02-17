"""Hybrid retrieval: Vector + BM25 + Semantic reranking.

Searches the Azure AI Search index and returns relevant
assessment documents as context for the LLM.
"""
import logging

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery

import config
from pipeline.embedder import get_embedding

log = logging.getLogger(__name__)


def _get_search_client() -> SearchClient:
    return SearchClient(
        endpoint=config.SEARCH_ENDPOINT,
        index_name=config.INDEX_NAME,
        credential=AzureKeyCredential(config.SEARCH_KEY),
    )


def _build_filter(filters: dict) -> str | None:
    """Build OData filter from a dict of filter conditions."""
    parts = []
    if filters.get("agency_id"):
        agencies = filters["agency_id"]
        if isinstance(agencies, str):
            agencies = [agencies]
        clause = " or ".join(f"agency_id eq '{a}'" for a in agencies)
        parts.append(f"({clause})")
    if filters.get("overall_outcome"):
        outcomes = filters["overall_outcome"]
        if isinstance(outcomes, str):
            outcomes = [outcomes]
        clause = " or ".join(f"overall_outcome eq '{o}'" for o in outcomes)
        parts.append(f"({clause})")
    if filters.get("decision_year_min"):
        parts.append(f"decision_year ge {filters['decision_year_min']}")
    if filters.get("decision_year_max"):
        parts.append(f"decision_year le {filters['decision_year_max']}")
    return " and ".join(parts) if parts else None


def _format_context(result) -> dict:
    """Format a search result into a context dict for the LLM."""
    return {
        "id": result["id"],
        "agency_id": result.get("agency_id", ""),
        "drug_inn": result.get("drug_inn", ""),
        "drug_brand": result.get("drug_brand", ""),
        "indication": result.get("indication", ""),
        "source_rating": result.get("source_rating", ""),
        "overall_outcome": result.get("overall_outcome", ""),
        "comparator_names": result.get("comparator_names", ""),
        "benefit_extent": result.get("benefit_extent", ""),
        "evidence_certainty": result.get("evidence_certainty", ""),
        "endpoint_summary": result.get("endpoint_summary", ""),
        "nice_comment": result.get("nice_comment", ""),
        "decision_date": str(result.get("decision_date", "")),
        "source_url": result.get("source_url", ""),
        "score": result.get("@search.score", 0),
        "reranker_score": result.get("@search.reranker_score", 0),
    }


def retrieve(query: str, filters: dict | None = None, top_k: int = 8) -> list[dict]:
    """Hybrid search: vector + keyword + semantic reranking.

    Args:
        query: Natural language query (DE or EN)
        filters: Optional dict with agency_id, overall_outcome, etc.
        top_k: Number of results to return

    Returns:
        List of context dicts, sorted by relevance.
    """
    client = _get_search_client()
    query_vector = get_embedding(query)

    odata_filter = _build_filter(filters) if filters else None

    results = client.search(
        search_text=query,
        vector_queries=[
            VectorizedQuery(
                vector=query_vector,
                k_nearest_neighbors=50,
                fields="content_vector",
            ),
        ],
        filter=odata_filter,
        query_type="semantic",
        semantic_configuration_name=config.SEMANTIC_CONFIG,
        top=top_k,
        select=[
            "id", "agency_id", "drug_inn", "drug_brand",
            "indication", "source_rating", "overall_outcome",
            "comparator_names", "benefit_extent", "evidence_certainty",
            "endpoint_summary", "nice_comment", "decision_date", "source_url",
        ],
    )

    context = [_format_context(r) for r in results]
    log.info("Retrieved %d results for query: %s", len(context), query[:80])
    return context
