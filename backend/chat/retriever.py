"""Hybrid retrieval: Vector + BM25 + Semantic reranking.

Searches the Azure AI Search index and returns relevant
assessment documents as context for the LLM.

For comparison queries, runs separate searches per agency
to ensure balanced results from both G-BA and NICE.

For aggregate/listing queries ("all orphan drugs", "how many"),
routes to direct SQL queries via sql_retriever.
"""
import logging
import re

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery

import config
from pipeline.embedder import get_embedding

log = logging.getLogger(__name__)

_SELECT_FIELDS = [
    "id", "agency_id", "drug_inn", "drug_brand",
    "indication", "source_rating", "overall_outcome",
    "comparator_names", "benefit_extent", "evidence_certainty",
    "endpoint_summary", "nice_comment", "decision_date", "source_url",
]


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


def _is_comparison_query(query: str) -> bool:
    """Detect if the user is asking for a cross-country comparison."""
    q = query.lower()
    comparison_patterns = [
        r"\bvergleich", r"\bcompare\b", r"\bcompar", r"\bvs\.?\b",
        r"\bgegen\b", r"\bversus\b", r"\bboth\b", r"\bbeide\b",
        r"\bg-ba.{0,10}nice\b", r"\bnice.{0,10}g-ba\b",
        r"\bgermany.{0,10}uk\b", r"\buk.{0,10}germany\b",
        r"\bdeutschland.{0,10}(uk|england)\b",
    ]
    return any(re.search(p, q) for p in comparison_patterns)


def _is_aggregate_query(query: str) -> bool:
    """Detect if the user is asking for a complete listing or count.

    These queries need direct SQL access, not top-k vector search,
    because they expect ALL matching results.
    """
    q = query.lower()
    aggregate_patterns = [
        # "all" / "alle" / "sämtliche" requests
        r"\balle\b", r"\ball\b", r"\bsämtliche\b", r"\bjede[rsnm]?\b",
        # Listing: "list", "welche", "zeig(e) mir", "nenne", "auflistung"
        r"\blist(e|en)?\b", r"\bwelche\b", r"\bzeig", r"\bnenne\b",
        r"\bauflist", r"\bübersicht\b",
        # Counting: "wie viele", "how many", "anzahl", "count"
        r"\bwie\s+viele\b", r"\bhow\s+many\b", r"\banzahl\b", r"\bcount\b",
        # Categorical queries that imply completeness
        r"\borphan.{0,10}(drug|medikament|arzneimittel)", r"\b(drug|medikament)s?.{0,10}orphan",
        # "since/seit" with year (implies listing over time range)
        r"\bseit\s+\d{4}\b", r"\bsince\s+\d{4}\b",
        # "in the last X years" / "in den letzten X Jahren"
        r"\bletzten?\s+\d+\s+(jahr|year)", r"\blast\s+\d+\s+year",
    ]
    return any(re.search(p, q) for p in aggregate_patterns)


def _search(query: str, query_vector: list[float],
            odata_filter: str | None, top_k: int) -> list[dict]:
    """Run a single hybrid search."""
    client = _get_search_client()
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
        select=_SELECT_FIELDS,
    )
    return [_format_context(r) for r in results]


def retrieve(query: str, filters: dict | None = None, top_k: int = 8) -> list[dict]:
    """Hybrid search: vector + keyword + semantic reranking.

    Routes to different strategies based on query type:
    - Aggregate/listing queries → direct SQL (complete results)
    - Comparison queries → split search (balanced G-BA + NICE)
    - Specific queries → normal hybrid search (top-k)

    Returns:
        List of context dicts. For SQL queries, includes a '_source' key
        set to 'sql' and '_sql_description' with the query explanation.
    """
    # 1. Aggregate/listing queries → SQL for complete results
    if _is_aggregate_query(query):
        from chat.sql_retriever import sql_retrieve
        results, description = sql_retrieve(query)
        if results:
            # Tag results as SQL-sourced
            for r in results:
                r["_source"] = "sql"
            if results and not results[0].get("total_count"):
                results[0]["_sql_description"] = description
                results[0]["_sql_total"] = len(results)
            log.info("Aggregate query routed to SQL: %d results for: %s",
                     len(results), query[:80])
            return results

        # SQL failed or returned nothing — fall through to vector search
        log.info("SQL retrieval returned nothing, falling back to vector search")

    # 2. Comparison queries → split search
    query_vector = get_embedding(query)

    if _is_comparison_query(query) and not (filters and filters.get("agency_id")):
        per_agency = max(top_k // 2, 4)
        gba_filter = "agency_id eq 'gba'"
        nice_filter = "agency_id eq 'nice'"

        if filters:
            extra = _build_filter({k: v for k, v in filters.items() if k != "agency_id"})
            if extra:
                gba_filter = f"{gba_filter} and {extra}"
                nice_filter = f"{nice_filter} and {extra}"

        gba_results = _search(query, query_vector, gba_filter, per_agency)
        nice_results = _search(query, query_vector, nice_filter, per_agency)

        # Interleave: GBA, NICE, GBA, NICE...
        context = []
        for pair in zip(gba_results, nice_results):
            context.extend(pair)
        context.extend(gba_results[len(nice_results):])
        context.extend(nice_results[len(gba_results):])

        log.info("Comparison search: %d G-BA + %d NICE for: %s",
                 len(gba_results), len(nice_results), query[:80])
        return context[:top_k]

    # 3. Normal single search
    odata_filter = _build_filter(filters) if filters else None
    context = _search(query, query_vector, odata_filter, top_k)
    log.info("Retrieved %d results for: %s", len(context), query[:80])
    return context
