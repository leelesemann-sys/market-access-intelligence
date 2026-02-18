"""LLM response generation with source attribution.

Takes retrieved context documents and generates an answer
using GPT-4o, always citing sources.

Handles two types of context:
1. Vector search results (small set, rich detail)
2. SQL query results (complete listings, tabular)
"""
import logging

from openai import AzureOpenAI

import config

log = logging.getLogger(__name__)

_client = None

SYSTEM_PROMPT = """You are an HTA (Health Technology Assessment) expert assistant.
You answer questions based on the provided assessment documents from G-BA (Germany) and NICE (UK).

The database contains ~998 G-BA assessments (since 2011) and ~1312 NICE technology appraisals.

Rules:
- Answer in the same language as the user's question (German or English)
- Always cite your sources with [Source ID] references
- If the provided context does not contain enough information, say so explicitly
- Never make up assessment results — only report what is in the context
- Be concise but thorough
- Use structured formatting: tables, bullet points, headers — avoid long paragraphs

For COMPARISON queries (drug X at G-BA vs NICE):
- Use a clear side-by-side structure with **G-BA** and **NICE** as headers
- For each agency, list each assessment as a bullet with: indication, rating, outcome, date, source link
- End with a brief 1-2 sentence comparison note (G-BA = clinical added benefit, NICE = cost-effectiveness)
- Do NOT write long explanatory paragraphs — let the structured data speak

Some context may come from PDF documents (G-BA "Tragende Gründe" — decision rationale documents).
These are marked as [PDF Chunk] and contain detailed clinical reasoning, endpoint results, and benefit assessments.
When citing PDF chunks, use: [G-BA {source_id} — Tragende Gründe, {section_title}] with the document URL.

Source format for citations:
- G-BA assessment: [G-BA {source_id}] with link
- G-BA PDF chunk: [G-BA {source_id} — Tragende Gründe, {section}] with document URL
- NICE: [NICE {source_id}] with link
"""

SYSTEM_PROMPT_SQL = """You are an HTA (Health Technology Assessment) expert assistant.
You answer questions based on COMPLETE database query results from the HTA Knowledge Base (G-BA Germany + NICE UK).

The results below are COMPLETE — they contain ALL matching records from the database.
The database contains ~998 G-BA assessments (since 2011) and ~1312 NICE technology appraisals.

Rules:
- Answer in the same language as the user's question (German or English)
- Present ALL results from the data — do not skip or omit any
- For listings: use a clear, structured format (table or numbered list)
- Group results logically (by outcome, by year, by therapeutic area, etc.)
- Include a total count at the top (e.g. "Insgesamt X Treffer")
- Cite source IDs where relevant: [G-BA {source_id}] or [NICE {source_id}]
- When comparing agencies, explain that G-BA evaluates clinical added benefit (Zusatznutzen)
  while NICE evaluates cost-effectiveness
- Never make up data — only report what is in the results
- If the user asks for "alle" (all), make sure EVERY result is shown
"""


def _get_client() -> AzureOpenAI:
    global _client
    if _client is None:
        _client = AzureOpenAI(
            azure_endpoint=config.OPENAI_ENDPOINT,
            api_key=config.OPENAI_KEY,
            api_version="2024-06-01",
        )
    return _client


def _format_context_for_llm(docs: list[dict]) -> str:
    """Format vector search results as structured context for the LLM."""
    parts = []
    for i, doc in enumerate(docs, 1):
        agency = doc.get("agency_id", "").upper()
        is_pdf = doc.get("doc_type") == "pdf_chunk"

        if is_pdf:
            # PDF chunk: show section content with metadata
            lines = [f"--- PDF Chunk {i} [{agency}] ---"]
            lines.append(f"ID: {doc.get('id', '')}")
            if doc.get("drug_inn"):
                lines.append(f"Drug: {doc['drug_inn']}")
            if doc.get("section_title"):
                lines.append(f"Section: {doc['section_title']}")
            if doc.get("parent_id"):
                lines.append(f"Parent assessment: {doc['parent_id']}")
            if doc.get("embedding_text"):
                # embedding_text has the prefix + full chunk text
                lines.append(f"Content:\n{doc['embedding_text']}")
            elif doc.get("indication"):
                lines.append(f"Content: {doc['indication']}")
            if doc.get("document_url"):
                lines.append(f"PDF URL: {doc['document_url']}")
        else:
            # Assessment metadata document
            lines = [f"--- Document {i} [{agency}] ---"]
            lines.append(f"ID: {doc.get('id', '')}")
            if doc.get("drug_inn"):
                brand = doc.get("drug_brand", "")
                if brand and brand != doc["drug_inn"]:
                    lines.append(f"Drug: {doc['drug_inn']} ({brand})")
                else:
                    lines.append(f"Drug: {doc['drug_inn']}")
            if doc.get("indication"):
                lines.append(f"Indication: {doc['indication'][:300]}")
            if doc.get("source_rating"):
                lines.append(f"Rating: {doc['source_rating']}")
            if doc.get("overall_outcome"):
                lines.append(f"Outcome: {doc['overall_outcome']}")
            if doc.get("comparator_names"):
                lines.append(f"Comparators: {doc['comparator_names'][:200]}")
            if doc.get("benefit_extent"):
                lines.append(f"Benefit extent: {doc['benefit_extent']}")
            if doc.get("evidence_certainty"):
                lines.append(f"Evidence: {doc['evidence_certainty']}")
            if doc.get("endpoint_summary"):
                lines.append(f"Endpoints: {doc['endpoint_summary']}")
            if doc.get("nice_comment"):
                lines.append(f"Comment: {doc['nice_comment'][:300]}")
            if doc.get("decision_date"):
                lines.append(f"Decision date: {doc['decision_date']}")
            if doc.get("source_url"):
                lines.append(f"Source: {doc['source_url']}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _format_sql_context_for_llm(docs: list[dict]) -> str:
    """Format SQL query results as compact tabular context for the LLM."""
    if not docs:
        return ""

    # Handle count-only results
    if docs[0].get("total_count") is not None:
        return f"Total count: {docs[0]['total_count']}"

    description = docs[0].get("_sql_description", "")
    total = docs[0].get("_sql_total", len(docs))
    header = f"Database query: {description}\nTotal results: {total}\n\n"

    lines = []
    for i, doc in enumerate(docs, 1):
        agency = doc.get("agency_id", "").upper()
        inn = doc.get("drug_inn", "?")
        brand = doc.get("drug_brand", "")
        rating = doc.get("source_rating", "")
        outcome = doc.get("overall_outcome", "")
        date = doc.get("decision_date", "")
        src_id = doc.get("source_id", "")
        indication = doc.get("indication_short", "")
        orphan = " [ORPHAN]" if doc.get("orphan_drug") else ""
        url = doc.get("source_url", "")

        drug_str = f"{inn} ({brand})" if brand and brand != inn else inn
        line = f"{i}. [{agency} {src_id}] {drug_str}{orphan} | {rating} | {outcome} | {date}"
        if indication:
            line += f" | {indication[:120]}"
        if url:
            line += f" | {url}"
        lines.append(line)

    return header + "\n".join(lines)


def respond(query: str, context: list[dict], model: str = "gpt-4o") -> str:
    """Generate a response using GPT-4o with retrieved context.

    Automatically detects whether context came from SQL (aggregate) or
    vector search, and uses appropriate formatting and system prompt.

    Args:
        query: User's question
        context: List of retrieved document dicts
        model: Azure OpenAI deployment name

    Returns:
        LLM response string with source citations
    """
    if not context:
        return ("Ich konnte keine relevanten Bewertungen zu dieser Frage finden. "
                "Bitte versuchen Sie eine andere Formulierung oder prüfen Sie die Filter.")

    # Detect SQL vs vector search results
    is_sql = any(doc.get("_source") == "sql" for doc in context)

    if is_sql:
        context_text = _format_sql_context_for_llm(context)
        system_prompt = SYSTEM_PROMPT_SQL
        # More tokens for large listings
        max_tokens = min(4000, 500 + len(context) * 40)
    else:
        context_text = _format_context_for_llm(context)
        system_prompt = SYSTEM_PROMPT
        max_tokens = 2000

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Context:\n{context_text}\n\nQuestion: {query}"},
    ]

    client = _get_client()
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.3,
        max_tokens=max_tokens,
    )

    answer = response.choices[0].message.content
    log.info("Generated response (%d tokens, sql=%s) for: %s",
             response.usage.completion_tokens, is_sql, query[:80])
    return answer
