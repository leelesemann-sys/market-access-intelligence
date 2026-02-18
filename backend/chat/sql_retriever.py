"""SQL-based retrieval for aggregate/listing queries.

When users ask for "all orphan drugs" or "how many oncology assessments",
vector search (top-k) is insufficient. This module runs direct SQL queries
against the assessments + drugs tables to get complete result sets.

Uses GPT-4o to translate natural language into a SQL WHERE clause,
then executes a safe parameterized query.
"""
import json
import logging
import re

from openai import AzureOpenAI

import config
import db

log = logging.getLogger(__name__)

_client = None

# Maximum rows to return from SQL (safety limit)
MAX_SQL_ROWS = 200

# Schema description for GPT-4o (minimal, just what it needs)
_SCHEMA_PROMPT = """You are a SQL WHERE clause generator for an HTA (Health Technology Assessment) database.

Available tables and columns:
- assessments (a): agency_id (VARCHAR: 'gba' or 'nice'), source_id, assessment_type, therapeutic_area,
  indication (NVARCHAR(MAX)), indication_short (NVARCHAR(255)), decision_date (DATE),
  source_rating (NVARCHAR), overall_outcome (VARCHAR: 'positive','restricted','negative'),
  source_url, process_type
- drugs (d): inn (NVARCHAR), brand_name (NVARCHAR), atc_code (VARCHAR), orphan_drug (BIT: 0 or 1)

The query always uses: FROM assessments a JOIN drugs d ON a.drug_id = d.drug_id

Key domain knowledge:
- G-BA (Germany): agency_id = 'gba'. Rates "Zusatznutzen" (added benefit). source_rating contains German terms.
- NICE (UK): agency_id = 'nice'. Rates cost-effectiveness. source_rating contains English terms.
- overall_outcome is harmonized: 'positive' = benefit confirmed, 'restricted' = partial, 'negative' = no benefit
- orphan_drug = 1 means orphan drug designation
- "Zusatznutzen" (added benefit) maps to overall_outcome IN ('positive', 'restricted')
- "kein Zusatznutzen" (no added benefit) maps to overall_outcome = 'negative'
- therapeutic_area values include: 'oncology', 'cardiology', 'neurology', 'immunology', etc.
- Use LOWER() for case-insensitive string comparisons
- For drug name searches: use LOWER(d.inn) LIKE '%drugname%' (partial match, case-insensitive)
- For date filters: decision_date >= 'YYYY-MM-DD' format
- "last 2 years" means decision_date >= '2024-01-01' (current year is 2026)
- "last 3 years" means decision_date >= '2023-01-01'
- "seit 2024" means decision_date >= '2024-01-01'
- For comparison queries ("compare X at G-BA vs NICE"): filter by drug name only, do NOT filter by agency.
  Return all assessments from both agencies so they can be compared side-by-side.

Respond ONLY with a JSON object:
{
  "where_clause": "SQL WHERE clause (without the WHERE keyword)",
  "order_by": "column(s) to order by, e.g. 'a.decision_date DESC'",
  "description": "Brief description of what this query finds",
  "needs_count_only": false
}

If you cannot generate a valid WHERE clause, return:
{"where_clause": null, "description": "reason", "needs_count_only": false}

IMPORTANT: Only use columns listed above. Do NOT use subqueries, UNION, or any DML.
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


def _generate_where_clause(query: str) -> dict | None:
    """Use GPT-4o to convert a natural language query into a SQL WHERE clause."""
    client = _get_client()
    response = client.chat.completions.create(
        model=config.OPENAI_CHAT_DEPLOYMENT,
        messages=[
            {"role": "system", "content": _SCHEMA_PROMPT},
            {"role": "user", "content": query},
        ],
        temperature=0,
        max_tokens=500,
    )

    raw = response.choices[0].message.content.strip()

    # Extract JSON from response (handle markdown code blocks)
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not json_match:
        log.warning("GPT-4o did not return valid JSON: %s", raw[:200])
        return None

    try:
        result = json.loads(json_match.group())
    except json.JSONDecodeError:
        log.warning("Failed to parse JSON from GPT-4o: %s", raw[:200])
        return None

    if not result.get("where_clause"):
        log.info("GPT-4o could not generate WHERE clause: %s", result.get("description"))
        return None

    return result


def _sanitize_where(clause: str) -> str | None:
    """Basic safety check on the generated WHERE clause."""
    forbidden = [";", "--", "/*", "*/", "DROP", "DELETE", "UPDATE", "INSERT",
                 "EXEC", "EXECUTE", "xp_", "sp_", "INTO", "TRUNCATE", "ALTER",
                 "CREATE", "GRANT", "DENY"]
    upper = clause.upper()
    for f in forbidden:
        if f.upper() in upper:
            log.warning("Blocked forbidden token '%s' in WHERE clause: %s", f, clause)
            return None
    return clause


def sql_retrieve(query: str) -> tuple[list[dict], str]:
    """Run a SQL query against the database based on natural language.

    Returns:
        (results, description) where results is a list of dicts
        and description explains what was queried.
    """
    parsed = _generate_where_clause(query)
    if not parsed:
        return [], "Could not generate SQL query for this question."

    where = _sanitize_where(parsed["where_clause"])
    if not where:
        return [], "Query was blocked for safety reasons."

    order_by = parsed.get("order_by", "a.decision_date DESC")
    description = parsed.get("description", "")
    count_only = parsed.get("needs_count_only", False)

    if count_only:
        sql = f"""
            SELECT COUNT(*) AS total
            FROM assessments a
            JOIN drugs d ON a.drug_id = d.drug_id
            WHERE {where}
        """
        log.info("SQL count query: WHERE %s", where)
        rows = db.fetch_all(sql)
        return [{"total_count": rows[0][0]}], description

    # Full listing query
    sql = f"""
        SELECT TOP {MAX_SQL_ROWS}
            d.inn, d.brand_name, d.orphan_drug, d.atc_code,
            a.agency_id, a.source_id, a.source_rating, a.overall_outcome,
            a.indication_short, a.decision_date, a.therapeutic_area,
            a.source_url
        FROM assessments a
        JOIN drugs d ON a.drug_id = d.drug_id
        WHERE {where}
        ORDER BY {order_by}
    """

    log.info("SQL listing query: WHERE %s ORDER BY %s", where, order_by)

    try:
        rows = db.fetch_all(sql)
    except Exception as e:
        log.error("SQL query failed: %s — %s", sql[:300], e)
        return [], f"SQL query failed: {e}"

    results = []
    for r in rows:
        results.append({
            "drug_inn": r[0] or "",
            "drug_brand": r[1] or "",
            "orphan_drug": bool(r[2]),
            "atc_code": r[3] or "",
            "agency_id": r[4] or "",
            "source_id": r[5] or "",
            "source_rating": r[6] or "",
            "overall_outcome": r[7] or "",
            "indication_short": r[8] or "",
            "decision_date": str(r[9]) if r[9] else "",
            "therapeutic_area": r[10] or "",
            "source_url": r[11] or "",
        })

    log.info("SQL returned %d rows for: %s", len(results), query[:80])
    return results, description
