"""Central configuration for HTA Intelligence.

All Azure credentials and service references in one place.
Values are read from environment variables (set via .env file).
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# Azure SQL
SQL_SERVER = os.environ["HTA_SQL_SERVER"]
SQL_DATABASE = os.environ["HTA_SQL_DATABASE"]
SQL_USER = os.environ["HTA_SQL_USER"]
SQL_PASSWORD = os.environ["HTA_SQL_PASSWORD"]

# Azure AI Search (shared with vergaberadar-search)
SEARCH_ENDPOINT = os.environ["HTA_SEARCH_ENDPOINT"]
SEARCH_KEY = os.environ["HTA_SEARCH_KEY"]
INDEX_NAME = "hta-intelligence-v1"
SEMANTIC_CONFIG = "hta-semantic-config"

# Azure OpenAI (shared with vergaberadar-openai)
OPENAI_ENDPOINT = os.environ["HTA_OPENAI_ENDPOINT"]
OPENAI_KEY = os.environ["HTA_OPENAI_KEY"]
OPENAI_EMBEDDING_DEPLOYMENT = "text-embedding-3-small"
OPENAI_EMBEDDING_DIMENSIONS = 256

# NICE API (optional)
NICE_API_KEY = os.environ.get("NICE_API_KEY", "")

# Data source URLs
GBA_XML_URL = "https://ais.g-ba.de/aktuelle-version"
NICE_EXCEL_URL = "https://a.storyblok.com/f/243782/x/2cb3eacc3b/ta-recommendations.xlsx"
NICE_GUIDANCE_LISTING_URL = (
    "https://www.nice.org.uk/guidance/published"
    "?ndt=Guidance&ngt=Technology%20appraisal%20guidance&ps=9999"
)
