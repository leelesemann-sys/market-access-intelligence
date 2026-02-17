# Pre-Mortem Analyse — HTA Intelligence

> Erstellt: 2026-02-17 | Methodik: Systematische Risikoanalyse vor Implementierungsstart

## Zusammenfassung

Vor dem Start der Implementierung wurde eine Pre-Mortem-Analyse durchgeführt: "Was kann schiefgehen?" — sowohl beim Initial Build als auch bei laufenden Updates. 8 Risiken wurden identifiziert, 2 davon durch empirische Tests vollständig eliminiert.

---

## Risiko 1: NICE API-Zugang 🔴→✅ ELIMINIERT

### Hypothese
Der Plan sah die NICE Syndication REST-API als Primärquelle vor. Risiko: Zugang erfordert Lizenzantrag, dauert Wochen, kostet Geld für internationale Nutzer.

### Test durchgeführt

**API-Test (api.nice.org.uk):**
- `GET /services/guidance` → **401 Unauthorized**
- `GET /services/guidance?type=TA` → **401 Unauthorized**
- Zugang erfordert: Excel-Formular ausfüllen → Email an `syndication@nice.org.uk` → monatliche Prüfung durch internes Panel
- Lizenztypen: Full (4 Jahre), Pilot (1 Jahr), Metadata (4 Jahre), Test (3 Monate)
- **Kostenlos nur für UK-Nutzung**, Gebühren für internationale Nutzung (Beträge nicht öffentlich)

**Ergebnis: API ist kein gangbarer Weg für Phase 1.**

### Alternative gefunden: NICE Excel (frei verfügbar)

| Eigenschaft | Wert |
|-------------|------|
| **URL** | `https://a.storyblok.com/f/243782/x/2cb3eacc3b/ta-recommendations.xlsx` |
| **Auth** | Keine — direkter HTTP GET, kein Login |
| **Dateigröße** | 206 KB |
| **Records** | 1.475 Empfehlungen aus 1.126 Technology Appraisals |
| **Zeitraum** | 1999/00 bis 2025/26 (>25 Jahre) |

**9 Spalten:**
1. `Rec no.` — Laufende Nummer (1–1473)
2. `TA ID` — z.B. "TA001", "TA1125"
3. `Year of Publication` — UK-Finanzjahr, z.B. "2024/25"
4. `STA/MTA process` — "STA", "MTA", "STA (review)"
5. `Technology` — Wirkstoff/Technologie-Name
6. `Technology type` — "Pharmaceutical", "Medical device", "Other therapeutic therapies"
7. `Indication` — Erkrankung/Indikation
8. `Categorisation (for specific recommendation)` — Empfehlung
9. `Comment` — Freitext

**12 Empfehlungs-Kategorien (mit Case-Inkonsistenzen):**

| Kategorie | Anzahl | Anmerkung |
|-----------|--------|-----------|
| Recommended | 635 | |
| Optimised | 411 | |
| Terminated Appraisal - non submission | 163 | Kein Dossier eingereicht |
| Not recommended | 137 | Kleinschreibung |
| Recommended (CDF) | 47 | Cancer Drugs Fund |
| Not Recommended | 34 | Großschreibung — Inkonsistenz! |
| Only in research | 22 | Kleinschreibung |
| Optimised (CDF) | 13 | |
| Only in Research | 8 | Großschreibung — Inkonsistenz! |
| recommended | 2 | Alles klein — Inkonsistenz! |
| Optimised (IMF) | 2 | Innovative Medicines Fund |
| Recommended (IMF) | 1 | |

**Bonus: NICE JSON-Listing (Monitoring)**
`https://www.nice.org.uk/guidance/published?ndt=Guidance&ngt=Technology%20appraisal%20guidance&ps=9999` liefert alle 884 publizierten TAs als eingebettetes `__NEXT_DATA__`-JSON (Titel, Datum, URL) — ohne Auth. Nutzbar zum Erkennen neuer TAs.

### Planänderung
- **Excel als Primärquelle** für Phase 1b (statt API)
- API-Antrag parallel stellen für spätere Phasen (Volltexte, ERG-Reports)
- Case-Normalisierung im Importer einbauen

---

## Risiko 2: G-BA XML Feldumfang 🔴→✅ ELIMINIERT

### Hypothese
Die G-BA XML-Datei ("G-BA_Beschluss_Info") ist primär für das Arztinformationssystem (AIS) gedacht. Risiko: Enthält möglicherweise nicht alle für Cross-Country-Vergleiche benötigten Felder (INN, ATC, Vergleichstherapie, Endpunkte).

### Test durchgeführt

**Download-Mechanismus (validiert):**
1. POST an `https://ais.g-ba.de/aktuelle-version` mit `nutzungsbedingungenAkzeptiert=1`
2. Server antwortet mit 302 Redirect
3. Redirect-Ziel: Signierte AWS S3-URL (600 Sekunden gültig)
4. GET auf S3-URL liefert die XML (24,6 MB)

**Struktur-Analyse:**
- Root: `<BE_COLLECTION>` mit `generated`-Timestamp
- 998 `<BE>`-Elemente (Beschlüsse)
- 1.658 Patientengruppen (`<ID_PAT_GR>`) insgesamt
- Durchschnitt: 1,7 Patientengruppen pro Beschluss
- **Alle Werte in `attrib['value']`**, nicht im Element-Text!

### Alle 11 angefragten Felder: VORHANDEN

| Feld | XML-Element | Beispiel |
|------|-------------|---------|
| Wirkstoff (INN) | `WS_BEW/NAME_WS_BEW[@value]` | "Cabazitaxel" |
| Handelsname | `ZUL/NAME_HN[@value]` | "Jevtana" |
| ATC-Code | `WS_INFO_BEW/ATC/ATC_CODE[@value]` | "L01CD04" |
| Indikation | `ZUL/AWG` (Text/HTML) + `AWG_KURZ[@value]` | HTML in CDATA |
| Zusatznutzen | `ZVT_ZN/ZN_A[@value]` | "gering" |
| Vergleichstherapie | `ZVT_BEST/NAME_ZVT_BEST[@value]` | "Best Supportive Care" |
| Patientengruppen | `ID_PAT_GR[@value]` + `NAME_PAT_GR` | Subgruppen-IDs |
| Endpunkte | `ZSF_EP_MORT/EP_MORT_GRAF[@value]` etc. | "&uarr;&uarr;" |
| Aussagesicherheit | `ZVT_ZN/ZN_W[@value]` | "Hinweis" |
| Beschlussdatum | `DATUM_BE_VOM[@value]` | "2012-03-29" |
| Verfahrens-ID | `ID_BE_AKZ[@value]` | "2011-04-15-D-003" |

### Bonus-Felder (nicht erwartet)
- **ICD-10-GM Codes** mit Alpha-IDs → automatisches Therapiegebiet-Mapping möglich
- **Orphan/ATMP/Conditional Approval** Flags (`SOND_ZUL_ORPHAN` etc.)
- **Kombinations-Partner** mit eigenen ATC-Codes (`WS_KOMB`)
- **Tragende Gründe (Zusammenfassung)** bis 4.000 Zeichen (`ZSF_TRG`)
- **PZN** (Pharmazentralnummern) pro Handelsname
- **URL zur G-BA-Detailseite** (`URL[@value]`)

### Benefit-Assessment Skala: 10 Werte (nicht 6!)

| ZN_A Wert | Anzahl | Mapping → `overall_outcome` |
|-----------|--------|------------------------------|
| ist nicht belegt | 1.028 | `negative` |
| beträchtlich | 179 | `positive` |
| gering | 171 | `positive` |
| nicht quantifizierbar | 99 | `positive` |
| nicht quantifizierbar (Datengrundlage) | 90 | `positive` |
| gilt als belegt | 35 | `positive` (Orphan-Sonderfall) |
| gilt als nicht belegt | 26 | `negative` |
| erheblich | 19 | `positive` |
| geringerer Nutzen | 8 | `negative` |
| nicht quantifizierbar (Nachweise) | 3 | `restricted` |

### Evidence Certainty (`ZN_W`): 3 Werte
| Wert | Anzahl | Nur bei positivem Zusatznutzen |
|------|--------|-------------------------------|
| Anhaltspunkt | 363 | Niedrigste Evidenzstufe |
| Hinweis | 154 | Mittlere Evidenzstufe |
| Beleg | 10 | Höchste Evidenzstufe |

### Beschlusstypen (`REG_NB`): 3 Typen
| Wert | Anzahl |
|------|--------|
| Beschluss_reg (regulär) | 839 |
| Beschluss_orph (Orphan Drug) | 147 |
| Beschluss_antib (Reserve-Antibiotika) | 12 |

### Parser-Besonderheiten (dokumentiert für Wartung)
1. **Werte in Attributen:** `element.get('value')`, NICHT `element.text`
2. **HTML in CDATA:** Felder wie `AWG`, `NAME_PAT_GR`, `ZN_TEXT` enthalten `<div><strong>...` → HTML-Stripping nötig
3. **Endpunkt-Symbole:** HTML-Entities wie `&uarr;&uarr;` → Dekodierung zu `↑↑`
4. **Voller Dump:** Keine Delta-API. 24,6 MB pro Download → Hash-basierte Change Detection
5. **Encoding:** XML deklariert UTF-8, aber enthält Windows-1252-Zeichen (ä→ae in manchen Feldern)
6. **S3-URL:** Signiert, 600 Sekunden gültig → Download muss sofort nach POST erfolgen

### Planänderung
- Benefit-Skala im Schema auf 10 Werte erweitert
- ICD-10-Codes für automatisches Therapiegebiet-Mapping eingeplant
- `indication_icd10` Spalte auf `NVARCHAR(MAX)` erweitert (manche Wirkstoffe haben 50+ ICD-Codes)

---

## Risiken 3–8: Zusammenfassung

| # | Risiko | Schwere | Status | Mitigation |
|---|--------|---------|--------|------------|
| 3 | **Harmonisierung fragwürdig** — G-BA bewertet Zusatznutzen, NICE bewertet Kosteneffektivität. Kein 1:1-Mapping möglich. | Hoch | Mitigiert | Nur ternäre Vergleichsdimension (positive/restricted/negative). Original-Ratings immer bewahren. Im Dashboard klar kommunizieren. |
| 4 | **Drug Matching fehleranfällig** — NICE Excel hat keinen ATC-Code. INN-Schreibvarianten, Kombinationspräparate, Indikations-Matching. | Mittel | Mitigiert | Multi-Level-Matching (INN exact → Fuzzy → Manual Override) mit Confidence-Score. Manuelles Override-CSV für Ausnahmen. |
| 5 | **Pipeline-Updates fragil** — G-BA XML = Vollständiger Dump, kein Delta. NICE Excel ohne Changelog. Schema-Änderungen können Parser brechen. | Mittel | Offen | Hash-basierte Change Detection, XSD-Validierung, UPSERT-Pattern, Monitoring-Alerts. |
| 6 | **Azure Free Tier Limits** — AI Search Free Tier nur 50 MB. PDF-Volltexte (Phase 1d) sprengen das Budget. SQL Serverless Cold-Start. | Niedrig | Mitigiert | Zwei-Index-Strategie (Assessments + Documents). Storage-Monitoring ab Tag 1. |
| 7 | **RAG-Chat-Qualität** — Halluzinationen bei Fachfragen, fehlende Brand↔INN-Synonyme, gemischtsprachige Queries. | Mittel | Offen | Strenge Source-Attribution, Synonym-Tabelle, zweisprachige Embeddings, Disclaimer im UI. |
| 8 | **Scope Creep Phase 2** — Jedes neue Land verdoppelt Harmonisierungs-Komplexität. | Niedrig | Mitigiert | Source-Adapter-Pattern strikt einhalten. Minimale Harmonisierung. Neue Länder nur nach explizitem Bedarf. |

---

## Backfill-Ergebnisse (Phase 1a, verifiziert)

Die G-BA-Daten wurden nach der Risikoanalyse erfolgreich importiert:

| Tabelle | Records |
|---------|---------|
| `drugs` | 485 einzigartige Wirkstoffe |
| `assessments` | 998 Beschlüsse (2012–2026) |
| `assessment_outcomes` | 1.658 Subgruppen-Bewertungen |
| `comparators` | 1.275 Vergleichstherapien |

**Outcome-Verteilung:**
- Negative (Zusatznutzen nicht belegt): 515 (52%)
- Positive: 338 (34%)
- Restricted (gemischte Subgruppen): 145 (15%)

Diese Verteilung ist konsistent mit der publizierten Literatur (~60% "nicht belegt" laut Ärzteblatt 2023).
