# HTA Intelligence — Architekturplan (v2, Post-Pre-Mortem)

> Aktualisiert: 2026-02-17 | Status: Azure-Ressourcen provisioniert, Schema deployt

## Context

Healthcare Market Access Teams brauchen systematische Vergleiche von Nutzenbewertungs-Entscheidungen verschiedener HTA-Agenturen. Studien zeigen 54–72% Konkordanz zwischen G-BA, NICE und HAS (Matched-Pairs-Studie 2021). Eine LLM-gestützte Knowledge Base mit Analytics + RAG-Chat automatisiert das.

---

## 1. Azure-Ressourcen (provisioniert)

| Ressource | Name | Tier | Status |
|-----------|------|------|--------|
| Resource Group | `rg-hta-intelligence` | — | GermanyWestCentral |
| SQL Server | `hta-intelligence-sql.database.windows.net` | Serverless Gen5 1vCore | Online |
| SQL Database | `hta_intelligence_db` | Auto-Pause 60min | Schema deployt |
| AI Search | `vergaberadar-search` (shared) | Free (50 MB, 3 Indizes) | Index `hta-intelligence-v1` geplant |
| OpenAI | `vergaberadar-openai` (shared) | text-embedding-3-small, 256-dim | Verfügbar |

**Firewall:** dev-machine (92.208.100.241), AllowAllAzure (0.0.0.0)
**Geschätzte Kosten:** ~€5–15/Monat (SQL Serverless)

---

## 2. Projektstruktur (erstellt)

```
C:\Projects\hta-intelligence\
├── backend/
│   ├── config.py              ✅ Zentrale Konfiguration
│   ├── db.py                  ✅ SQLAlchemy-Wrapper (getestet)
│   ├── .env                   ✅ Credentials
│   ├── sources/
│   │   ├── base_source.py     ✅ Abstract HTASource + Dataclasses
│   │   ├── gba.py             ⬜ Phase 1a: XML-Parser
│   │   └── nice.py            ⬜ Phase 1b: Excel-Parser
│   ├── pipeline/
│   │   ├── importer.py        ⬜ Source → SQL
│   │   ├── harmonizer.py      ⬜ Source-Ratings → overall_outcome
│   │   ├── denormalizer.py    ⬜ SQL → search_documents
│   │   ├── embedder.py        ⬜ Phase 1c: Azure OpenAI
│   │   └── indexer.py         ⬜ Phase 1c: Azure AI Search
│   ├── chat/
│   │   ├── retriever.py       ⬜ Phase 1c: Hybrid Search
│   │   └── responder.py       ⬜ Phase 1c: GPT-4o RAG
│   ├── run_pipeline.py        ⬜ Orchestrierung
│   └── app.py                 ⬜ Streamlit Frontend
├── sql/
│   └── schema.sql             ✅ 7 Tabellen + 2 Views deployt
├── data/
│   ├── reference/             ⬜ ATC-Codes, ICD→Therapiegebiet
│   └── downloads/             (gitignored)
├── tests/
├── docs/
│   └── architecture.md        ✅ Dieses Dokument
├── requirements.txt           ✅
└── .gitignore                 ✅
```

---

## 3. Datenquellen (validiert durch Pre-Mortem-Tests)

### 3.1 G-BA (Deutschland) — XML

| Eigenschaft | Wert |
|-------------|------|
| **URL** | POST `https://ais.g-ba.de/aktuelle-version` mit `nutzungsbedingungenAkzeptiert=1` |
| **Format** | XML (24,6 MB), XSD-Schema vom 01.04.2023 |
| **Update** | 1. und 15. jedes Monats (komplett, kein Delta) |
| **Abdeckung** | Alle aktuell gültigen Beschlüsse seit 2011 |
| **Storage** | AWS S3 (signierte URL, 600s gültig) |
| **Auth** | Keine (nur Nutzungsbedingungen akzeptieren) |

**Verfügbare Felder (alle 11 bestätigt):**
- `NAME_WS_BEW` + `NAME_ASK` → INN / Wirkstoffname
- `NAME_HN` → Handelsname
- `ATC_CODE` → ATC (7-stellig, Pattern A00AA00)
- `AWG` / `AWG_BESCHLUSS` / `AWG_KURZ` → Indikation (voll/Beschluss/kurz)
- `ZN_A` → Zusatznutzen (10 Enum-Werte, nicht 6!)
- `ZN_W` → Aussagesicherheit (Beleg/Hinweis/Anhaltspunkt, optional)
- `NAME_ZVT_BEST` → Vergleichstherapie + ATC/ASK
- `ID_PAT_GR` + `NAME_PAT_GR` → Patientengruppen
- `ZSF_EP_MORT/MORB/LEBQ/UE` → Endpunkte (Pfeil-Symbole + Text)
- `DATUM_BE_VOM` / `DATUM_BE_BIS` → Beschlussdatum
- `ID_BE_AKZ` → Verfahrens-ID

**Bonus-Felder:** ICD-10-GM Codes, Orphan/ATMP/Conditional Flags, PZN, Tragende-Gründe-Zusammenfassung (4.000 Zeichen), URL zur G-BA-Detailseite

**Benefit-Assessment Enum (`ZN_A`, 10 Werte):**
| Wert | Mapping → `overall_outcome` |
|------|------------------------------|
| `erheblich` | `positive` |
| `betraechtlich` | `positive` |
| `gering` | `positive` |
| `nicht quantifizierbar` | `positive` |
| `nicht quantifizierbar, weil die wissenschaftliche Datengrundlage...` | `positive` |
| `nicht quantifizierbar, weil die erforderlichen Nachweise...` | `restricted` |
| `gilt als belegt` (Orphan) | `positive` |
| `ist nicht belegt` | `negative` |
| `gilt als nicht belegt` | `negative` |
| `geringerer Nutzen` | `negative` |

**Parser-Besonderheiten:**
1. Download: POST → 302 Redirect → GET auf S3 (nicht POST!)
2. HTML in CDATA-Feldern → Stripping mit lxml oder BeautifulSoup
3. Endpunkt-Symbole als HTML-Entities (`&uarr;`, `&darr;`, `&harr;`) → Dekodierung
4. Voller Dump → Hash-basierte Change Detection für Updates

### 3.2 NICE (UK) — Excel + JSON

| Eigenschaft | Wert |
|-------------|------|
| **Excel-URL** | `https://a.storyblok.com/f/243782/x/2cb3eacc3b/ta-recommendations.xlsx` |
| **Format** | Excel (206 KB), 9 Spalten |
| **Records** | 1.475 Empfehlungen aus 1.126 Appraisals |
| **Abdeckung** | 1999/00 bis 2025/26 |
| **Auth** | Keine (direkter Download) |
| **API** | Gesperrt (401, Lizenz erforderlich, Gebühren für international) |

**Excel-Spalten:**
1. `Rec no.` — Laufende Nummer
2. `TA ID` — z.B. "TA1125"
3. `Year of Publication` — UK-Finanzjahr "2024/25"
4. `STA/MTA process` — "STA", "MTA", "STA (review)"
5. `Technology` — Wirkstoff/Technologie-Name
6. `Technology type` — "Pharmaceutical", "Medical device", "Other"
7. `Indication` — Erkrankung/Indikation
8. `Categorisation` — Empfehlung (12 Werte, Case-Inkonsistenzen!)
9. `Comment` — Freitext

**Empfehlungs-Kategorien (normalisiert):**
| NICE Original | Mapping → `overall_outcome` |
|---------------|------------------------------|
| Recommended | `positive` |
| Recommended (CDF) | `positive` |
| Recommended (IMF) | `positive` |
| Optimised | `restricted` |
| Optimised (CDF) | `restricted` |
| Optimised (IMF) | `restricted` |
| Only in research | `restricted` |
| Not recommended | `negative` |
| Terminated Appraisal - non submission | (excluded) |

**Monitoring neuer TAs:** `https://www.nice.org.uk/guidance/published?...&ps=9999` liefert `__NEXT_DATA__` JSON mit allen 884 publizierten TAs (Titel, Datum, URL) — ohne Auth.

---

## 4. Datenbankschema (deployt)

### Tabellen (7)
| Tabelle | Zweck | Schlüsselfelder |
|---------|-------|-----------------|
| `agencies` | Referenz: G-BA, NICE | `agency_id` PK |
| `drugs` | Wirkstoff-Stammdaten | `inn`, `brand_name`, `atc_code` |
| `assessments` | Master: 1 Zeile pro Bewertung | `agency_id` + `source_id` UNIQUE |
| `assessment_outcomes` | G-BA Subgruppen-Ergebnisse | `assessment_id` + `subgroup_id` UNIQUE |
| `comparators` | Vergleichstherapien | FK → assessments/outcomes |
| `documents` | PDF-Links | FK → assessments |
| `search_documents` | Denormalisiert für RAG | `id` PK (z.B. "gba-2024-01-15-D-001") |

### Views (2)
- `v_benefit_distribution` — Aggregierte Bewertungsverteilung nach Agentur/Jahr/Therapiegebiet
- `v_matched_drug_pairs` — Cross-Country-Paare mit Concordance-Flag

---

## 5. Harmonisierungs-Strategie (vereinfacht nach Pre-Mortem)

### Prinzip: Original bewahren, nur ternär vergleichen

G-BA bewertet **klinischen Zusatznutzen** (6→10 Stufen). NICE bewertet **Kosteneffektivität** (ICER-Schwellenwert). Das sind fundamental verschiedene Bewertungsmaße — ein erzwungenes 1:1-Mapping wäre wissenschaftlich fragwürdig.

**Lösung:**
- `source_rating` → Original-Wortlaut, immer unverändert
- `benefit_extent` → G-BA-spezifisch (10 Enum-Werte), nur in `assessment_outcomes`
- `overall_outcome` → **Ternäre Vergleichsdimension** (positive/restricted/negative)
- Im Dashboard klar kommunizieren: "G-BA bewertet Zusatznutzen, NICE bewertet Kosteneffektivität"

### Drug Matching

NICE Excel enthält **keinen ATC-Code**. Matching-Strategie:
1. INN-Vergleich: `NAME_WS_BEW` (G-BA) ↔ `Technology` (NICE), case-insensitive
2. Fuzzy-Match mit Levenshtein für Schreibvarianten
3. Manuelles Override-File (`data/reference/drug_mapping.csv`) für Ausnahmen
4. Confidence-Score: `exact` > `fuzzy` > `manual`

---

## 6. Pipeline-Architektur

```
run_pipeline.py --source gba|nice|all

┌─────────────┐     ┌──────────┐     ┌────────────┐     ┌──────────────┐
│  G-BA XML   │────→│          │────→│            │────→│              │
│  (POST→S3)  │     │ Importer │     │ Harmonizer │     │ Denormalizer │
└─────────────┘     │          │     │            │     │              │
┌─────────────┐     │ (Record →│     │ (source →  │     │ (SQL →       │
│ NICE Excel  │────→│  SQL)    │────→│  ternary)  │────→│  search_doc) │
│  (HTTP GET) │     │          │     │            │     │              │
└─────────────┘     └──────────┘     └────────────┘     └──────┬───────┘
                                                               │
                    ┌──────────┐     ┌────────────┐            │
                    │ vergabe- │←────│ vergabe-   │←───────────┘
                    │ radar-   │     │ radar-     │
                    │ search   │     │ openai     │
                    │ (Index:  │     │ (embed-    │
                    │ hta-v1)  │     │  dings)    │
                    └──────────┘     └────────────┘
```

---

## 7. Phasenplan (aktualisiert)

### Phase 1a — G-BA MVP ← AKTUELL
1. ~~Azure-Ressourcen provisionieren~~ ✅
2. ~~Schema deployen~~ ✅
3. `sources/gba.py` — XML-Download (POST→S3) + Parser
4. `pipeline/importer.py` — AssessmentRecord → SQL
5. `pipeline/harmonizer.py` — ZN_A → overall_outcome
6. Backfill: Alle G-BA-Beschlüsse importieren
7. Basis-Dashboard in Streamlit

### Phase 1b — NICE + Cross-Country
1. `sources/nice.py` — Excel-Download + Parser (NICHT API!)
2. Case-Normalisierung der Categorisation-Werte
3. NICE-Daten importieren (1.475 Recommendations)
4. Drug-Matching: INN-basiert + Fuzzy + Manual Override
5. Dashboard: Vergleichs-Charts, Matched Pairs, Concordance

### Phase 1c — RAG/Chat
1. `pipeline/denormalizer.py` + `embedder.py` + `indexer.py`
2. Index `hta-intelligence-v1` in vergaberadar-search erstellen
3. `chat/retriever.py` + `chat/responder.py`
4. Streamlit Chat-Tab mit Quellenangaben + Disclaimer

### Phase 1d — PDF-Verarbeitung
1. IQWiG-Berichte und NICE FADs herunterladen
2. PDF-Parsing → Chunks → Embeddings
3. Zweiter Index `hta-documents-v1` für Volltexte

### Phase 2 — Expansion
- Frankreich (HAS CSV), USA (openFDA + ICER), Australien (PBAC)

---

## 8. Pre-Mortem Risiken & Mitigierungen

| # | Risiko | Status | Mitigation |
|---|--------|--------|------------|
| 1 | NICE API gesperrt | **Eliminiert** | Excel als Primärquelle (frei, 1.475 Records) |
| 2 | G-BA XML Feldumfang | **Eliminiert** | Alle 11 Felder + Bonus (ICD-10, Orphan-Flag) vorhanden |
| 3 | Harmonisierung fragwürdig | **Mitigiert** | Nur ternäre Vergleichsdimension, Originale bewahrt |
| 4 | Drug Matching fehleranfällig | **Mitigiert** | INN + Fuzzy + Manual Override + Confidence-Score |
| 5 | Pipeline-Updates fragil | Offen | Hash-basierte Change Detection, UPSERT, XSD-Validierung |
| 6 | Azure Free Tier Limits | **Mitigiert** | Shared Search Service, 2-Index-Strategie |
| 7 | RAG-Qualität | Offen | Synonyme, zweisprachige Embeddings, Disclaimer |
| 8 | Scope Creep | **Mitigiert** | Minimale Harmonisierung, explizite Erweiterungskriterien |
