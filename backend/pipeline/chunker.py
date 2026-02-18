"""Split PDF text into search-ready chunks.

Chunks are sized for effective embedding (~500-800 tokens)
with metadata prefixes for better retrieval.
"""
import hashlib
import logging

log = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 700
DEFAULT_OVERLAP_TOKENS = 100
CHARS_PER_TOKEN = 4  # rough estimate for German text


def _estimate_tokens(text: str) -> int:
    """Rough token estimate for German text."""
    return len(text) // CHARS_PER_TOKEN


def _split_fixed_window(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    """Split text into fixed-size windows with overlap."""
    max_chars = max_tokens * CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * CHARS_PER_TOKEN
    step = max_chars - overlap_chars

    if len(text) <= max_chars:
        return [text]

    chunks = []
    pos = 0
    while pos < len(text):
        end = pos + max_chars

        # Try to break at a sentence or paragraph boundary
        if end < len(text):
            # Look for paragraph break near the end
            para_break = text.rfind("\n\n", pos + step // 2, end)
            if para_break > pos:
                end = para_break + 2
            else:
                # Look for sentence break
                sentence_break = text.rfind(". ", pos + step // 2, end)
                if sentence_break > pos:
                    end = sentence_break + 2

        chunk = text[pos:end].strip()
        if chunk:
            chunks.append(chunk)

        pos = end - overlap_chars
        if pos >= len(text):
            break

    return chunks


def chunk_document(
    sections: list[dict],
    metadata: dict,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
) -> list[dict]:
    """Create search-ready chunks from parsed PDF sections.

    Args:
        sections: List of {title, text} dicts from pdf_parser.detect_sections()
        metadata: Dict with drug_inn, agency_id, source_id, etc.
        max_tokens: Maximum tokens per chunk
        overlap_tokens: Token overlap between adjacent chunks

    Returns:
        List of chunk dicts ready for storage:
        {chunk_index, section_title, chunk_text, embedding_text, char_count, embedding_hash}
    """
    drug_inn = metadata.get("drug_inn", "Unknown")
    source_id = metadata.get("source_id", "")

    chunks = []
    chunk_idx = 0

    for section in sections:
        title = section["title"]
        text = section["text"]

        if _estimate_tokens(text) <= max_tokens:
            # Section fits in one chunk
            sub_chunks = [text]
        else:
            # Split long section
            sub_chunks = _split_fixed_window(text, max_tokens, overlap_tokens)

        for sub_text in sub_chunks:
            if len(sub_text.strip()) < 50:  # skip tiny fragments
                continue

            # Build embedding text with metadata prefix
            prefix = f"Drug: {drug_inn} | Agency: G-BA | Doc: Tragende Gründe | Section: {title}"
            embedding_text = f"{prefix}\n\n{sub_text}"

            emb_hash = hashlib.sha256(embedding_text.encode("utf-8")).hexdigest()

            chunks.append({
                "chunk_index": chunk_idx,
                "section_title": title,
                "chunk_text": sub_text,
                "embedding_text": embedding_text,
                "char_count": len(sub_text),
                "embedding_hash": emb_hash,
            })
            chunk_idx += 1

    log.debug("Chunked %d sections into %d chunks for %s (%s)",
              len(sections), len(chunks), drug_inn, source_id)
    return chunks
