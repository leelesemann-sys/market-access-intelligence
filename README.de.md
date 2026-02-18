# Market Access Intelligence

> **Sprache:** [English](README.md) | Deutsch

**Länderübergreifende Health Technology Assessment (HTA) Analyse mit KI-Unterstützung.**

Vergleichen Sie Arzneimittelbewertungen zwischen G-BA (Deutschland) und NICE (UK) durch interaktive Analysen, Wirkstoff-Vergleiche und einen KI-gestützten Knowledge-Base-Chat.

---

## Das Problem

Market-Access-Teams in der Pharmabranche verbringen Wochen damit, HTA-Entscheidungen länderübergreifend manuell zu vergleichen. G-BA und NICE bewerten dieselben Medikamente, verwenden aber grundlegend unterschiedliche Bewertungsrahmen (klinischer Nutzen vs. Kosteneffektivität), was die länderübergreifende Analyse komplex und zeitaufwändig macht.

Studien zeigen nur 54–72 % Übereinstimmung zwischen den Behörden für dieselben Medikamente — zu verstehen, *warum* Entscheidungen voneinander abweichen, ist entscheidend für die Market-Access-Strategie.

## Die Lösung

Market Access Intelligence automatisiert den gesamten Workflow:

- **2.300+ Bewertungen** von G-BA (998) und NICE (1.312) in einer einheitlichen Datenbank
- **Länderübergreifendes Matching** verknüpft denselben Wirkstoff über Behörden hinweg per INN
- **RAG-gestützter Chat** beantwortet Fragen auf Basis von Bewertungsdaten + PDF-Entscheidungsdokumenten
- **PDF-Analyse** der G-BA „Tragenden Gründe" (Entscheidungsbegründungen) für klinische Details über Metadaten hinaus

## Funktionen

### Analytics Dashboard
- KPI-Übersicht: Bewertungsanzahl, gematchte Wirkstoffe, Übereinstimmungsrate
- Ergebnisverteilung (positiv/eingeschränkt/negativ) nach Behörde
- Konkordanzmatrix (G-BA vs. NICE)
- Bewertungs-Zeitverlauf nach Jahr

### Wirkstoff-Vergleich
- Seite-an-Seite-Vergleich für jeden gematchten Wirkstoff
- Alle Bewertungen pro Behörde mit Indikation, Rating, Ergebnis, Datum
- Direktlinks zu Quelldokumenten

### RAG Chat (KI Knowledge Base)
- Fragen auf Deutsch oder Englisch stellen
- Hybridsuche: Vektorähnlichkeit + Keyword-Matching + semantisches Reranking
- SQL-Routing für aggregierte Abfragen („alle Orphan Drugs", „wie viele")
- PDF-basierte Antworten mit Zitaten zu klinischen Endpunkten und Entscheidungsbegründungen
- Quellenangaben mit Links zu Originaldokumenten

**Beispielabfragen:**
- *„Compare G-BA and NICE decisions for pembrolizumab"*
- *„Welche Orphan Drugs wurden 2024 negativ bewertet?"*
- *„What mortality endpoints were evaluated for nivolumab in NSCLC?"*

---

## Architektur

```
                  Datenquellen                     Verarbeitung             Frontend
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

| Schicht | Technologie |
|---------|-------------|
| Datenbank | Azure SQL (Serverless) |
| Suche | Azure AI Search (hybrid: Vektor + BM25 + Semantic) |
| Embeddings | Azure OpenAI `text-embedding-3-small` (256-dim) |
| Chat | Azure OpenAI `GPT-4o` |
| PDF-Parsing | pdfplumber |
| Frontend | Streamlit |
| Sprache | Python 3.11+ |

---

## Datenquellen

| Quelle | Behörde | Datensätze | Inhalt |
|--------|---------|------------|--------|
| [G-BA XML](https://ais.g-ba.de) | G-BA (Deutschland) | 998 Bewertungen | Zusatznutzen-Ratings, Subgruppen, Endpunkte, Komparatoren |
| [NICE Excel](https://www.nice.org.uk) | NICE (UK) | 1.312 Appraisals | Empfehlungen, Kategorisierungen, Kommentare |
| G-BA PDFs | G-BA (Deutschland) | 2.376 verfügbar | „Tragende Gründe" Entscheidungsbegründungen |

### Harmonisierung

Der G-BA bewertet den **klinischen Zusatznutzen**. NICE evaluiert die **Kosteneffektivität** (ICER-Schwelle). Dies sind grundlegend unterschiedliche Maßstäbe.

Wir bewahren die Original-Ratings und harmonisieren nur zu einem ternären Ergebnis für den länderübergreifenden Vergleich:

| | positiv | eingeschränkt | negativ |
|---|---|---|---|
| **G-BA** | erheblich, betraechtlich, gering, nicht quantifizierbar, gilt als belegt | nicht quantifizierbar (unzureichende Evidenz) | ist nicht belegt, geringerer Nutzen |
| **NICE** | Recommended (inkl. CDF, IMF) | Optimised, Only in research | Not recommended |

---

## Einrichtung

### Voraussetzungen

- Python 3.11+
- Azure SQL Database
- Azure AI Search Service
- Azure OpenAI Deployment (text-embedding-3-small + GPT-4o)

### Installation

```bash
git clone https://github.com/leelesemann-sys/market-access-intelligence.git
cd market-access-intelligence
pip install -r requirements.txt
```

### Konfiguration

Kopieren Sie die Umgebungsvorlage und tragen Sie Ihre Azure-Zugangsdaten ein:

```bash
cp backend/.env.example backend/.env
```

Erforderliche Variablen:
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

### Datenbank-Setup

Deployen Sie das Schema auf Ihre Azure SQL Datenbank:
```bash
# schema.sql und schema_phase1d.sql auf Ihre Datenbank anwenden
```

### Pipeline ausführen

```bash
# G-BA + NICE Daten importieren
python backend/run_pipeline.py --source all

# Suchindex aufbauen (Embed + Index)
python backend/run_pipeline.py --index

# G-BA PDF-Dokumente verarbeiten (Tragende Gründe)
python backend/run_pipeline.py --pdfs --pdf-limit 10   # Test mit 10
python backend/run_pipeline.py --pdfs --skip-scrape     # Vollständiger Lauf (gecachte URLs nutzen)
```

### Dashboard starten

```bash
streamlit run backend/app.py
```

---

## Projektstruktur

```
market-access-intelligence/
├── backend/
│   ├── sources/          # Datenquellen-Adapter (G-BA XML, NICE Excel)
│   ├── pipeline/         # ETL: Import, Embed, Index, PDF-Verarbeitung
│   ├── chat/             # RAG: Hybridsuche + GPT-4o Antwort
│   ├── app.py            # Streamlit Dashboard (3 Tabs)
│   ├── run_pipeline.py   # CLI-Orchestrierung
│   ├── config.py         # Zentrale Konfiguration
│   └── db.py             # Datenbankverbindung
├── sql/                  # Datenbankschema
├── docs/                 # Architekturdokumentation
└── requirements.txt
```

---

## Lizenz

Dieses Projekt ist unter der [MIT-Lizenz](LICENSE) lizenziert.

G-BA-Daten unterliegen den [G-BA-Nutzungsbedingungen](https://www.g-ba.de). NICE-Daten sind öffentlich verfügbar unter den [NICE-Nutzungsbedingungen](https://www.nice.org.uk/terms-and-conditions).
