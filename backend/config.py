"""Central configuration for Market Access Intelligence.

All Azure credentials and service references in one place.
Values are read from environment variables (.env locally)
or Streamlit secrets (on Streamlit Cloud).
"""

import os
from pathlib import Path


def _get(key: str, default: str = "") -> str:
    """Read config value from env vars or Streamlit secrets."""
    # 1. Environment variable (works locally with .env)
    val = os.environ.get(key)
    if val:
        return val
    # 2. Streamlit secrets (works on Streamlit Cloud)
    try:
        import streamlit as st
        return st.secrets.get(key, default)
    except Exception:
        return default


# Load .env file if present (local development)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# Azure SQL
SQL_SERVER = _get("HTA_SQL_SERVER")
SQL_DATABASE = _get("HTA_SQL_DATABASE")
SQL_USER = _get("HTA_SQL_USER")
SQL_PASSWORD = _get("HTA_SQL_PASSWORD")

# Azure AI Search (shared with vergaberadar-search)
SEARCH_ENDPOINT = _get("HTA_SEARCH_ENDPOINT")
SEARCH_KEY = _get("HTA_SEARCH_KEY")
INDEX_NAME = "hta-intelligence-v1"
SEMANTIC_CONFIG = "hta-semantic-config"

# Azure OpenAI (shared with vergaberadar-openai)
OPENAI_ENDPOINT = _get("HTA_OPENAI_ENDPOINT")
OPENAI_KEY = _get("HTA_OPENAI_KEY")
OPENAI_EMBEDDING_DEPLOYMENT = "text-embedding-3-small"
OPENAI_EMBEDDING_DIMENSIONS = 256
OPENAI_CHAT_DEPLOYMENT = "gpt-4o"

# NICE API (optional)
NICE_API_KEY = _get("NICE_API_KEY")

# Data source URLs
GBA_XML_URL = "https://ais.g-ba.de/aktuelle-version"
NICE_EXCEL_URL = "https://a.storyblok.com/f/243782/x/2cb3eacc3b/ta-recommendations.xlsx"
NICE_GUIDANCE_LISTING_URL = (
    "https://www.nice.org.uk/guidance/published"
    "?ndt=Guidance&ngt=Technology%20appraisal%20guidance&ps=9999"
)
