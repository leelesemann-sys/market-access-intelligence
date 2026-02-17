"""LLM response generation with source attribution.

Takes retrieved context documents and generates an answer
using GPT-4o, always citing sources.
"""
import logging

from openai import AzureOpenAI

import config

log = logging.getLogger(__name__)

_client = None

SYSTEM_PROMPT = """You are an HTA (Health Technology Assessment) expert assistant.
You answer questions based on the provided assessment documents from G-BA (Germany) and NICE (UK).

Rules:
- Answer in the same language as the user's question (German or English)
- Always cite your sources with [Source ID] references
- When comparing agencies, explain that G-BA evaluates clinical added benefit (Zusatznutzen)
  while NICE evaluates cost-effectiveness — they measure different things
- If the provided context does not contain enough information, say so explicitly
- Never make up assessment results — only report what is in the context
- Be concise but thorough

Source format for citations:
- G-BA: [G-BA {source_id}] with link
- NICE: [NICE {source_id}] with link
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
    """Format retrieved documents as structured context for the LLM."""
    parts = []
    for i, doc in enumerate(docs, 1):
        agency = doc.get("agency_id", "").upper()
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


def respond(query: str, context: list[dict], model: str = "gpt-4o") -> str:
    """Generate a response using GPT-4o with retrieved context.

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

    context_text = _format_context_for_llm(context)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Context:\n{context_text}\n\nQuestion: {query}"},
    ]

    client = _get_client()
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0.3,
        max_tokens=2000,
    )

    answer = response.choices[0].message.content
    log.info("Generated response (%d tokens) for: %s",
             response.usage.completion_tokens, query[:80])
    return answer
