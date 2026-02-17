"""Azure AI Search index: create and populate.

Creates the hta-intelligence-v1 index with Vector + BM25 + Semantic config
and pushes documents with embeddings.
"""
import logging
from datetime import datetime

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)

import config
import db

log = logging.getLogger(__name__)


def create_index():
    """Create or update the hta-intelligence-v1 index (idempotent)."""
    client = SearchIndexClient(
        endpoint=config.SEARCH_ENDPOINT,
        credential=AzureKeyCredential(config.SEARCH_KEY),
    )

    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True, filterable=True),

        # Searchable text fields (BM25)
        SearchableField(name="drug_inn", type=SearchFieldDataType.String, analyzer_name="en.microsoft"),
        SearchableField(name="drug_brand", type=SearchFieldDataType.String, analyzer_name="en.microsoft"),
        SearchableField(name="indication", type=SearchFieldDataType.String, analyzer_name="en.microsoft"),
        SearchableField(name="source_rating", type=SearchFieldDataType.String, analyzer_name="en.microsoft"),
        SearchableField(name="comparator_names", type=SearchFieldDataType.String, analyzer_name="en.microsoft"),
        SearchableField(name="embedding_text", type=SearchFieldDataType.String, analyzer_name="en.microsoft"),

        # Filterable / facetable metadata
        SimpleField(name="agency_id", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SimpleField(name="country_code", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SimpleField(name="therapeutic_area", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SimpleField(name="overall_outcome", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SimpleField(name="decision_year", type=SearchFieldDataType.Int32, filterable=True, facetable=True, sortable=True),
        SimpleField(name="atc_code", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SimpleField(name="benefit_extent", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SimpleField(name="evidence_certainty", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SimpleField(name="nice_process", type=SearchFieldDataType.String, filterable=True, facetable=True),

        # Non-searchable metadata
        SimpleField(name="decision_date", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
        SimpleField(name="source_url", type=SearchFieldDataType.String),
        SimpleField(name="endpoint_summary", type=SearchFieldDataType.String),
        SimpleField(name="nice_comment", type=SearchFieldDataType.String),

        # Vector field
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=config.OPENAI_EMBEDDING_DIMENSIONS,
            vector_search_profile_name="hta-vector-profile",
        ),
    ]

    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="hta-hnsw")],
        profiles=[
            VectorSearchProfile(
                name="hta-vector-profile",
                algorithm_configuration_name="hta-hnsw",
            ),
        ],
    )

    semantic_config = SemanticConfiguration(
        name=config.SEMANTIC_CONFIG,
        prioritized_fields=SemanticPrioritizedFields(
            title_field=SemanticField(field_name="drug_inn"),
            content_fields=[
                SemanticField(field_name="indication"),
                SemanticField(field_name="embedding_text"),
            ],
            keywords_fields=[
                SemanticField(field_name="therapeutic_area"),
                SemanticField(field_name="agency_id"),
                SemanticField(field_name="overall_outcome"),
            ],
        ),
    )

    index = SearchIndex(
        name=config.INDEX_NAME,
        fields=fields,
        vector_search=vector_search,
        semantic_search=SemanticSearch(configurations=[semantic_config]),
    )

    result = client.create_or_update_index(index)
    log.info("Index '%s' created/updated", result.name)
    return result


def _format_doc(doc: dict) -> dict:
    """Format a document for Azure Search upload."""
    search_doc = {
        "id": doc["id"],
        "drug_inn": doc.get("drug_inn"),
        "drug_brand": doc.get("drug_brand"),
        "indication": doc.get("indication"),
        "source_rating": doc.get("source_rating"),
        "comparator_names": doc.get("comparator_names"),
        "embedding_text": doc.get("embedding_text"),
        "agency_id": doc.get("agency_id"),
        "country_code": doc.get("country_code"),
        "therapeutic_area": doc.get("therapeutic_area"),
        "overall_outcome": doc.get("overall_outcome"),
        "atc_code": doc.get("atc_code"),
        "benefit_extent": doc.get("benefit_extent"),
        "evidence_certainty": doc.get("evidence_certainty"),
        "nice_process": doc.get("nice_process"),
        "endpoint_summary": doc.get("endpoint_summary"),
        "nice_comment": doc.get("nice_comment"),
        "source_url": doc.get("source_url"),
        "content_vector": doc.get("content_vector"),
    }

    # Format decision_year
    dy = doc.get("decision_year")
    search_doc["decision_year"] = int(dy) if dy is not None else None

    # Format decision_date (ISO 8601)
    dd = doc.get("decision_date")
    if dd is not None:
        if isinstance(dd, datetime):
            search_doc["decision_date"] = dd.isoformat() + "Z"
        elif hasattr(dd, "isoformat"):
            search_doc["decision_date"] = dd.isoformat() + "T00:00:00Z"
        elif isinstance(dd, str) and dd:
            search_doc["decision_date"] = dd
        else:
            search_doc["decision_date"] = None
    else:
        search_doc["decision_date"] = None

    return search_doc


def upload_documents(docs: list[dict], batch_size: int = 500) -> int:
    """Push documents to Azure AI Search.

    Returns count of successfully uploaded documents.
    """
    client = SearchClient(
        endpoint=config.SEARCH_ENDPOINT,
        index_name=config.INDEX_NAME,
        credential=AzureKeyCredential(config.SEARCH_KEY),
    )

    total = 0
    for i in range(0, len(docs), batch_size):
        batch = docs[i:i + batch_size]
        formatted = [_format_doc(d) for d in batch]

        try:
            result = client.upload_documents(documents=formatted)
            succeeded = sum(1 for r in result if r.succeeded)
            failed = sum(1 for r in result if not r.succeeded)
            total += succeeded
            if failed > 0:
                log.warning("Batch %d: %d failed", i, failed)
                for r in result:
                    if not r.succeeded:
                        log.warning("  Doc %s: %s", r.key, r.error_message)
                        break
        except Exception as e:
            log.error("Upload error at batch %d: %s", i, e)

    log.info("Search index: %d / %d documents uploaded", total, len(docs))
    return total


def mark_indexed(doc_ids: list[str]):
    """Mark documents as successfully indexed in SQL."""
    engine = db.get_engine()
    raw_conn = engine.raw_connection()
    cursor = raw_conn.cursor()
    for doc_id in doc_ids:
        cursor.execute(
            "UPDATE search_documents SET indexed_at = GETDATE() WHERE id = ?",
            doc_id,
        )
    raw_conn.commit()
    cursor.close()
    raw_conn.close()
