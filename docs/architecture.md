# HTA Intelligence — Architektur & Projektstatus

> Aktualisiert: 2026-02-18 | Status: Phase 1a–1d abgeschlossen, MVP funktionsfähig

## Überblick

Healthcare Market Access Teams brauchen systematische Vergleiche von Nutzenbewertungs-Entscheidungen verschiedener HTA-Agenturen. Studien zeigen 54–72% Konkordanz zwischen G-BA, NICE und HAS (Matched-Pairs-Studie 2021). Eine LLM-gestützte Knowledge Base mit Analytics + RAG-Chat automatisiert das.

**Aktueller Stand:**
- **998 G-BA** Bewertungen (seit 2011) + **1.312 NICE** Technology Appraisals importiert
- **Hybrid RAG** (Vector + BM25 + Semantic Reranking) mit SQL-Fallback für Aggregate
- **PDF-Verarbeitung** für G-BA Tragende Gründe (Beschlussbegründungen)
- **Streamlit Dashboard** mit 3 Tabs: Analytics, Drug Comparison, RAG Chat

---

## 1. Azure-Ressourcen

| Ressource | Name | Tier | Status |
|-----------|------|------|--------|
| Resource Group | `rg-hta-intelligence` | — | GermanyWestCentral |
| SQL Server | `hta-intelligence-sql.database.windows.net` | Serverless Gen5 1vCore | Online |
| SQL Database | `hta_intelligence_db` | Auto-Pause 60min | 998 G-BA + 1.312 NICE |
| AI Search | `vergaberadar-search` (shared) | Free (50 MB, 3 Indizes) | Index `hta-intelligence-v1` aktiv |
| OpenAI | `vergaberadar-openai` (shared) | text-embedding-3-small, 256-dim | Verfügbar |

**Endpoints:**
- SQL: `hta-intelligence-sql.database.windows.net` (User: `htaadmin`)
- AI Search: `https://vergaberadar-search.search.windows.net`
- OpenAI: `https://germanywestcentral.api.cognitive.microsoft.com/` (regional Endpoint!)
- Firewall: dev-machine (92.208.100.241), AllowAllAzure (0.0.0.0)
- Geschätzte Kosten: ~€5–15/Monat (SQL Serverless)

---

## 2. Projektstruktur

```
C:\Projects\hta-intelligence\
├── backend/
│   ├── config.py              ✅ Zentrale Konfiguration (env + Streamlit secrets)
│   ├── db.py                  ✅ SQLAlchemy-Wrapper (pyodbc lokal, pymssql Cloud)
│   ├── app.py                 ✅ Streamlit Dashboard (3 Tabs)
│   ├── run_pipeline.py        ✅ CLI-Orchestrierung (--source, --index, --pdfs)
│   ├── .env                   🔒 Credentials (lokal, gitignored)
│   ├── .env.example           ✅ Template
│   │
│   ├── sources/
│   │   ├── base_source.py     ✅ Abstract HTASource + Dataclasses
│   │   ├── gba.py             ✅ G-BA XML Parser (POST→S3→XML)
│   │   └── nice.py            ✅ NICE Excel Parser (Storyblok URL)
│   │
│   ├── pipeline/
│   │   ├── importer.py        ✅ AssessmentRecord → SQL (UPSERT)
│   │   ├── denormalizer.py    ✅ SQL → search_documents
│   │   ├── embedder.py        ✅ Azure OpenAI Embeddings (256-dim)
│   │   ├── indexer.py         ✅ Azure AI Search Push + Schema
│   │   ├── pdf_scraper.py     ✅ G-BA Seiten scrapen → PDF URLs
│   │   ├── pdf_parser.py      ✅ PDF Download + pdfplumber Extraktion
│   │   ├── chunker.py         ✅ Semantisches Chunking (700 Tokens, 100 Overlap)
│   │   └── pdf_pipeline.py    ✅ PDF-Pipeline Orchestrierung
│   │
│   └── chat/
│       ├── retriever.py       ✅ Hybrid Search + SQL-Routing
│       ├── sql_retriever.py   ✅ SQL-Fallback für Aggregate/Listings
│       └── responder.py       ✅ GPT-4o RAG mit Quellenzitaten
│
├── sql/
│   ├── schema.sql             ✅ 7 Tabellen + 2 Views
│   └── schema_phase1d.sql     ✅ pdf_chunks + Erweiterungen
│
├── data/
│   ├── downloads/             (gitignored)
│   │   ├── gba_beschluss_info.xml      # G-BA Vollexport (24.6 MB)
│   │   ├── nice_ta_recommendations.xlsx # NICE TA Excel (206 KB)
│   │   └── pdfs/gba/                   # Gecachte PDFs (~110 Dateien)
│   └── reference/
│       └── gba_samples/                # Test-XMLs
│
├── docs/
│   ├── architecture.md        ✅ Dieses Dokument
│   └── pre-mortem-analysis.md ✅ Risikoanalyse
│
├── tests/                     ⬜ Noch leer
├── requirements.txt           ✅ 14 Dependencies
└── .gitignore                 ✅
```

---

## 3. Datenquellen

### 3.1 G-BA (Deutschland) — XML

| Eigenschaft | Wert |
|-------------|------|
| URL | POST `https://ais.g-ba.de/aktuelle-version` mit `nutzungsbedingungenAkzeptiert=1` |
| Format | XML (24,6 MB), XSD-Schema vom 01.04.2023 |
| Update | 1. und 15. jedes Monats (Vollexport, kein Delta) |
| Abdeckung | Alle aktuell gültigen Beschlüsse seit 2011 |
| Storage | AWS S3 (signierte URL, 600s gültig) |
| Importiert | **998 Assessments** |

**ZN_A → `overall_outcome` Mapping (10 Werte):**

| ZN_A | Outcome |
|------|---------|
| `erheblich`, `betraechtlich`, `gering` | `positive` |
| `nicht quantifizierbar` (beide Varianten) | `positive` |
| `gilt als belegt` (Orphan) | `positive` |
| `nicht quantifizierbar, weil die erforderlichen Nachweise...` | `restricted` |
| `ist nicht belegt`, `gilt als nicht belegt`, `geringerer Nutzen` | `negative` |

### 3.2 NICE (UK) — Excel

| Eigenschaft | Wert |
|-------------|------|
| URL | `https://a.storyblok.com/f/243782/x/2cb3eacc3b/ta-recommendations.xlsx` |
| Format | Excel (206 KB), 9 Spalten |
| Abdeckung | 1999/00 bis 2025/26 |
| Auth | Keine (direkter Download) |
| Importiert | **1.312 Technology Appraisals** |

**Categorisation → `overall_outcome` Mapping:**

| NICE Original | Outcome |
|---------------|---------|
| Recommended, Recommended (CDF/IMF) | `positive` |
| Optimised, Optimised (CDF/IMF), Only in research | `restricted` |
| Not recommended | `negative` |

---

## 4. Datenbankschema

### Tabellen (8)

| Tabelle | Zweck | Records |
|---------|-------|---------|
| `agencies` | Referenz: G-BA, NICE | 2 |
| `drugs` | Wirkstoff-Stammdaten (INN, Brand, ATC) | ~1.800 |
| `assessments` | Master: 1 Zeile pro Bewertung | ~2.310 |
| `assessment_outcomes` | G-BA Subgruppen-Ergebnisse | ~2.500 |
| `comparators` | Vergleichstherapien | ~3.000 |
| `documents` | PDF-Metadaten + URLs | ~28.900 |
| `pdf_chunks` | Gechunkte PDF-Texte | ~4.800 |
| `search_documents` | Denormalisiert für RAG | ~7.100 |

### Views (2)

- `v_benefit_distribution` — Aggregierte Bewertungsverteilung nach Agentur/Jahr/Therapiegebiet
- `v_matched_drug_pairs` — Cross-Country-Paare mit Concordance-Flag

### search_documents Spalten

| Spalte | Typ | Beschreibung |
|--------|-----|-------------|
| `id` | VARCHAR(200) PK | z.B. "gba-2024-01-15-D-001" oder "gba-pdf-2024-01-15-D-001-c3" |
| `agency_id` | VARCHAR(20) | "gba" oder "nice" |
| `drug_inn`, `drug_brand` | NVARCHAR | Wirkstoff |
| `indication` | NVARCHAR(MAX) | Indikationstext |
| `source_rating` | NVARCHAR(500) | Original-Bewertung |
| `overall_outcome` | VARCHAR(20) | positive/restricted/negative |
| `embedding_text` | NVARCHAR(MAX) | Text für Embeddings |
| `content_vector` | — (im Index) | 256-dim Float-Vektor |
| `doc_type` | VARCHAR(50) | "assessment" oder "pdf_chunk" |
| `parent_id` | VARCHAR(200) | Verweis auf Assessment-Dokument (bei Chunks) |
| `section_title` | NVARCHAR(500) | Abschnittsüberschrift (bei Chunks) |
| `document_url` | VARCHAR(1000) | PDF-URL (bei Chunks) |

---

## 5. Pipeline-Architektur

### Import-Pipeline

```
run_pipeline.py --source gba|nice|all [--skip-download]

G-BA XML (POST→S3)  ──→  GBASource.parse()  ──→  AssessmentRecord[]
NICE Excel (HTTP GET) ──→  NICESource.parse() ──→  AssessmentRecord[]
                                                          │
                                                    importer.py
                                                    (UPSERT: drugs, assessments,
                                                     outcomes, comparators)
                                                          │
                                                          ▼
                                                    SQL Database
```

### Index-Pipeline

```
run_pipeline.py --index

SQL (assessments + drugs + outcomes)
          │
    denormalizer.py ──→ search_documents (SQL)
          │
    embedder.py ──→ Azure OpenAI (256-dim)
          │
    indexer.py ──→ Azure AI Search (hta-intelligence-v1)
```

### PDF-Pipeline

```
run_pipeline.py --pdfs [--pdf-limit N] [--skip-scrape]

G-BA Detailseiten (998 URLs)
          │
    pdf_scraper.py ──→ documents (28.900 PDF-URLs)
          │                  ↓ Filter: nur tragende_gruende (~2.376)
    pdf_parser.py ──→ Download + pdfplumber Extraktion
          │
    chunker.py ──→ 700-Token Chunks (100 Overlap)
          │
    pdf_pipeline.py ──→ pdf_chunks + search_documents (SQL)
          │
    embedder.py + indexer.py ──→ Azure AI Search
```

### Chat-Pipeline

```
User Query
    │
    ├──→ _is_aggregate_query? ──→ sql_retriever.py ──→ Direkt-SQL (vollständig)
    │
    ├──→ _is_comparison_query? ──→ sql_retriever.py ──→ SQL (alle Bewertungen)
    │         Fallback: Split Vector Search (G-BA + NICE getrennt)
    │
    └──→ Hybrid Search: Vector + BM25 + Semantic Reranking
                │
          retriever.py ──→ Azure AI Search
                │
          responder.py ──→ GPT-4o (mit Quellenangaben)
                │
          Streamlit Chat UI
```

---

## 6. Azure AI Search Index

**Index:** `hta-intelligence-v1` auf `vergaberadar-search.search.windows.net`

| Metrik | Wert |
|--------|------|
| Dokumente gesamt | **~7.131** |
| Assessment-Dokumente | ~2.310 (G-BA + NICE) |
| PDF-Chunks | ~4.821 (110 Tragende Gründe) |
| Speicherverbrauch | **47,01 / 50 MB** (Free Tier) |
| Freier Speicher | **~3 MB** |
| Vektordimensionen | 256 (text-embedding-3-small) |
| Semantic Config | `hta-semantic-config` |

**Felder im Index:**
- Searchable: `embedding_text`, `section_title` (de.microsoft Analyzer)
- Filterable: `agency_id`, `overall_outcome`, `decision_year`, `doc_type`, `parent_id`
- Vector: `content_vector` (256-dim, cosine)
- Sortable: `decision_date`, `chunk_index`

**Storage-Budget:**
- Free Tier: 50 MB (kein Upgrade ohne Kostensteigerung auf ~€73/Mo)
- Aktuell: 47 MB belegt → **nur noch ~150 weitere Chunks möglich**
- Empfehlung: Therapeutische Gebiete priorisieren statt alle PDFs

---

## 7. CLI-Befehle

```bash
# Datenquellen importieren
python run_pipeline.py --source gba              # G-BA XML → SQL
python run_pipeline.py --source nice             # NICE Excel → SQL
python run_pipeline.py --source all              # Beide
python run_pipeline.py --source gba --skip-download  # Cached XML nutzen

# Search Index aufbauen
python run_pipeline.py --index                   # Denormalize + Embed + Index
python run_pipeline.py --source all --index      # Import + Index

# PDF-Verarbeitung (Tragende Gründe)
python run_pipeline.py --pdfs                    # Alle: Scrape + Download + Chunk + Index
python run_pipeline.py --pdfs --pdf-limit 10     # Test: nur 10 PDFs
python run_pipeline.py --pdfs --skip-scrape      # Vorhandene URLs nutzen

# Dashboard starten
streamlit run backend/app.py
```

---

## 8. Phasenplan & Status

### Phase 1a — G-BA Import ✅ ABGESCHLOSSEN
- Azure-Ressourcen provisioniert
- Schema deployt (7 Tabellen + 2 Views)
- XML-Parser (`sources/gba.py`): POST→S3, 11 Felder + Bonus
- Importer: UPSERT in drugs, assessments, outcomes, comparators
- 998 G-BA Assessments importiert
- Harmonisierung: 10 ZN_A Werte → ternäres `overall_outcome`

### Phase 1b — NICE + Cross-Country ✅ ABGESCHLOSSEN
- Excel-Parser (`sources/nice.py`): Case-Normalisierung
- 1.312 NICE Technology Appraisals importiert
- Drug-Matching via INN (case-insensitive)
- Streamlit Dashboard: Analytics, Drug Comparison

### Phase 1c — RAG/Chat ✅ ABGESCHLOSSEN
- Denormalization → Embeddings (256-dim) → AI Search Index
- Hybrid Search: Vector + BM25 + Semantic Reranking
- SQL-Routing für Aggregate/Listings ("alle Orphan-Drugs", "wie viele")
- Comparison-Query-Detection für G-BA vs NICE Vergleiche
- GPT-4o Response Generation mit Quellenangaben
- Streamlit Chat-Tab mit Disclaimer

### Phase 1d — PDF-Verarbeitung ✅ ABGESCHLOSSEN (Code)
- PDF URL Scraper: 988 G-BA Seiten → 28.935 PDF-Links, 2.376 Tragende Gründe
- PDF Parser: pdfplumber Extraktion + Section Detection
- Chunker: 700 Tokens, 100 Overlap, Metadata-Prefix
- Pipeline-Orchestrierung: Scrape → Download → Parse → Chunk → SQL → Embed → Index
- **110 PDFs verarbeitet** → 4.821 Chunks im Index, 0 Fehler
- **Storage-Limit erreicht**: 47/50 MB → weitere Verarbeitung blockiert

### Phase 1d — Offene Punkte
- [ ] Therapeutische Gebiete priorisieren (Onkologie, Immunologie, etc.)
- [ ] Selektive Indizierung statt vollständiger Verarbeitung
- [ ] Ggf. alte/irrelevante Chunks ersetzen statt hinzufügen

### Phase 2 — Expansion (geplant)
- Frankreich (HAS CSV), USA (openFDA + ICER), Australien (PBAC)
- Paid Tier AI Search oder alternative Architektur

---

## 9. Therapeutische Gebiete (Analyse)

`therapeutic_area` ist NULL für alle G-BA-Assessments (Feld nicht im XML). Keyword-basierte Klassifikation auf `indication`:

| Therapiegebiet | Assessments | Wirkstoffe |
|----------------|-------------|------------|
| Onkologie | 426 | ~200 |
| Sonstige | 157 | ~120 |
| Infektiologie | 92 | ~55 |
| Immunologie/Rheuma | 86 | ~50 |
| Neurologie | 44 | ~30 |
| Stoffwechsel/Endo | 43 | ~30 |
| Seltene Erkrankungen | 42 | ~35 |
| Haematologie | 40 | ~30 |
| Pneumologie | 39 | ~20 |
| Kardiologie | 29 | ~20 |

**Empfehlung MVP:** 2–3 Gebiete vollständig mit Tragenden Gründen bestücken (z.B. Onkologie + Immunologie).

---

## 10. Harmonisierungs-Strategie

### Prinzip: Original bewahren, nur ternär vergleichen

G-BA bewertet **klinischen Zusatznutzen** (10 Stufen). NICE bewertet **Kosteneffektivität** (ICER-Schwellenwert). Fundamental verschiedene Bewertungsmaße — kein 1:1-Mapping möglich.

**Lösung:**
- `source_rating` → Original-Wortlaut (immer unverändert)
- `benefit_extent` → G-BA-spezifisch (10 Enum-Werte, nur in `assessment_outcomes`)
- `overall_outcome` → Ternäre Vergleichsdimension (`positive`/`restricted`/`negative`)
- Dashboard kommuniziert Unterschied: "G-BA = Zusatznutzen, NICE = Kosteneffektivität"

### Drug Matching

NICE Excel enthält keinen ATC-Code. Matching über:
1. INN-Vergleich: `NAME_WS_BEW` (G-BA) ↔ `Technology` (NICE), case-insensitive
2. Fuzzy-Match mit Levenshtein für Schreibvarianten
3. Manuelles Override-File für Ausnahmen
4. Confidence-Score: `exact` > `fuzzy` > `manual`

---

## 11. Risiken & Mitigierungen

| # | Risiko | Status | Mitigation |
|---|--------|--------|------------|
| 1 | NICE API gesperrt | **Eliminiert** | Excel als Primärquelle |
| 2 | G-BA XML Feldumfang | **Eliminiert** | Alle 11 Felder + Bonus vorhanden |
| 3 | Harmonisierung fragwürdig | **Mitigiert** | Nur ternäre Vergleichsdimension |
| 4 | Drug Matching fehleranfällig | **Mitigiert** | INN + Fuzzy + Manual Override |
| 5 | Azure Free Tier Limits | **Eingetreten** | 47/50 MB belegt; selektive Indizierung nötig |
| 6 | RAG-Qualität | **Mitigiert** | SQL-Routing für Aggregate, Hybrid Search |
| 7 | G-BA blockiert Scraping | **Mitigiert** | 1 req/sec, URLs in DB gecacht |
| 8 | PDF-Text unlesbar | **Mitigiert** | pdfplumber; 0 Fehler bei 110 PDFs |

---

## 12. Dependencies

```
python-dotenv          # .env Dateien
sqlalchemy             # ORM + Query Builder
pymssql                # Azure SQL (Cloud-kompatibel)
pandas                 # DataFrames (NICE Excel)
openpyxl               # Excel-Dateien
requests               # HTTP Downloads
lxml                   # XML Parsing + Scraping
pdfplumber             # PDF Text-Extraktion
openai                 # Azure OpenAI Client
azure-search-documents # Azure AI Search SDK
azure-core             # Azure SDK Base
streamlit>=1.45.0      # Web Dashboard
altair<6               # Visualisierung
plotly                  # Interaktive Charts
```
