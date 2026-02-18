# Market Access Intelligence

> **Language:** English | [Deutsch](README.de.md)

**Cross-country Health Technology Assessment (HTA) analysis powered by AI.**

Compare drug approval decisions between G-BA (Germany) and NICE (UK) through interactive analytics, side-by-side drug comparisons, and an AI-powered knowledge base chat.

---

## The Problem

Market Access teams in pharma spend weeks manually comparing HTA decisions across countries. G-BA and NICE evaluate the same drugs but use fundamentally different frameworks (clinical benefit vs. cost-effectiveness), making cross-country analysis complex and time-consuming.

Studies show only 54-72% concordance between agencies for the same drugs - understanding *why* decisions diverge is critical for market access strategy.

## The Solution

Market Access Intelligence automates the entire workflow:

- **2,300+ assessments** from G-BA (998) and NICE (1,312) in a unified database
- **Cross-country matching** linking the same drug across agencies by INN
- **RAG-powered chat** that answers questions using assessment data + PDF decision documents
- **PDF analysis** of G-BA "Tragende Gruende" (decision rationale) for clinical detail beyond metadata

## Features

### Analytics Dashboard
- KPI overview: assessment counts, matched drugs, concordance rate
- Outcome distribution (positive/restricted/negative) by agency
- Concordance matrix (G-BA vs NICE)
- Assessment timeline by year

### Drug Comparison
- Side-by-side comparison for any matched drug
- All assessments per agency with indication, rating, outcome, date
- Direct links to source documents

### RAG Chat (AI Knowledge Base)
- Ask questions in German or English
- Hybrid search: vector similarity + keyword matching + semantic reranking
- SQL routing for aggregate queries ("all orphan drugs", "how many")
- PDF-backed answers citing clinical endpoints and decision rationale
- Source citations with links to original documents

**Example queries:**
- *"Compare G-BA and NICE decisions for pembrolizumab"*
- *"Welche Orphan Drugs wurden 2024 negativ bewertet?"*
- *"What mortality endpoints were evaluated for nivolumab in NSCLC?"*

---

## Architecture

```
                  Data Sources                    Processing              Frontend
              +-----------------+
              |   G-BA XML      |---+
              | (POST -> S3)    |   |      +-------------+
              +-----------------+   +----->|  Importer   |---+
              +-----------------+   |      | (UPSERT)    |   |
              |  NICE Excel     |---+      +-------------+   |    +------------+
              | (HTTP GET)      |                             +--->| Azure SQL  |
              +-----------------+                             |    | Database   |
              +-----------------+          +-------------+    |    +-----+------+
              |  G-BA PDFs      |--------->| PDF Parser  |----+          |
              | (Tragende       |          | + Chunker   |          +----+----+
              |  Gruende)       |          +-------------+          |         |
              +-----------------+                              +---+---+ +---+---+
                                                               |Embedder| |SQL    |
                                                               |256-dim | |Routing|
                                                               +---+---+ +---+---+
                                                                   |         |
                                                               +---+---+     |
                                                               | Azure  |    |
                                                               |   AI   |    |
                                                               | Search |    |
                                                               +---+---+    |
                                                                   |         |
                                                               +---+---------+--+
                                                               |    RAG Chat    |
                                                               |  (GPT-4o)     |
                                                               +-------+-------+
                                                                       |
                                                               +-------+-------+
                                                               |   Streamlit   |
                                                               |   Dashboard   |
                                                               +---------------+
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Database | Azure SQL (Serverless) |
| Search | Azure AI Search (hybrid: vector + BM25 + semantic) |
| Embeddings | Azure OpenAI `text-embedding-3-small` (256-dim) |
| Chat | Azure OpenAI `GPT-4o` |
| PDF Parsing | pdfplumber |
| Frontend | Streamlit |
| Language | Python 3.11+ |

---

## Data Sources

| Source | Agency | Records | Content |
|--------|--------|---------|---------|
| [G-BA XML](https://ais.g-ba.de) | G-BA (Germany) | 998 assessments | Benefit ratings, subgroups, endpoints, comparators |
| [NICE Excel](https://www.nice.org.uk) | NICE (UK) | 1,312 appraisals | Recommendations, categorisations, comments |
| G-BA PDFs | G-BA (Germany) | 2,376 available | "Tragende Gruende" decision rationale documents |

### Harmonization

G-BA rates **clinical added benefit** (Zusatznutzen). NICE evaluates **cost-effectiveness** (ICER threshold). These are fundamentally different measures.

We preserve original ratings and only harmonize to a ternary outcome for cross-country comparison:

| | positive | restricted | negative |
|---|---|---|---|
| **G-BA** | erheblich, betraechtlich, gering, nicht quantifizierbar, gilt als belegt | nicht quantifizierbar (insufficient evidence) | ist nicht belegt, geringerer Nutzen |
| **NICE** | Recommended (incl. CDF, IMF) | Optimised, Only in research | Not recommended |

---

## Setup

### Prerequisites

- Python 3.11+
- Azure SQL Database
- Azure AI Search service
- Azure OpenAI deployment (text-embedding-3-small + GPT-4o)

### Installation

```bash
git clone https://github.com/leelesemann-sys/market-access-intelligence.git
cd market-access-intelligence
pip install -r requirements.txt
```

### Configuration

Copy the environment template and fill in your Azure credentials:

```bash
cp backend/.env.example backend/.env
```

Required variables:
```
HTA_SQL_SERVER=your-server.database.windows.net
HTA_SQL_DATABASE=your_db
HTA_SQL_USER=your_user
HTA_SQL_PASSWORD=your_password
HTA_SEARCH_ENDPOINT=https://your-search.search.windows.net
HTA_SEARCH_KEY=your_search_admin_key
HTA_OPENAI_ENDPOINT=https://your-region.api.cognitive.microsoft.com/
HTA_OPENAI_KEY=your_openai_key
```

### Database Setup

Deploy the schema to your Azure SQL database:
```bash
# Apply schema.sql and schema_phase1d.sql to your database
```

### Run the Pipeline

```bash
# Import G-BA + NICE data
python backend/run_pipeline.py --source all

# Build search index (embed + index)
python backend/run_pipeline.py --index

# Process G-BA PDF documents (Tragende Gruende)
python backend/run_pipeline.py --pdfs --pdf-limit 10   # test with 10
python backend/run_pipeline.py --pdfs --skip-scrape     # full run (use cached URLs)
```

### Start the Dashboard

```bash
streamlit run backend/app.py
```

---

## Project Structure

```
market-access-intelligence/
├── backend/
│   ├── sources/          # Data source adapters (G-BA XML, NICE Excel)
│   ├── pipeline/         # ETL: import, embed, index, PDF processing
│   ├── chat/             # RAG: hybrid retrieval + GPT-4o response
│   ├── app.py            # Streamlit dashboard (3 tabs)
│   ├── run_pipeline.py   # CLI orchestration
│   ├── config.py         # Central configuration
│   └── db.py             # Database connection
├── sql/                  # Database schema
├── docs/                 # Architecture documentation
└── requirements.txt
```

---

## License

This project is licensed under the [MIT License](LICENSE).

G-BA data is subject to [G-BA usage terms](https://www.g-ba.de). NICE data is publicly available under [NICE terms](https://www.nice.org.uk/terms-and-conditions).
