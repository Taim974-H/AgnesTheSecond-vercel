# Context — AgnesTheSecond (taim)

A full-stack AI supply-chain intelligence platform for CPG companies, built for the TUM.ai × Spherecast Makeathon 2026. This file is a condensed, code-grounded overview of every file in `taim/`, intended as onboarding context for an LLM or new engineer.

---

## 1. Project Layout

```
taim/
├── app.py                      # Flask entrypoint — registers 4 blueprints
├── generate_mock_data.py       # One-time script: adds Supplier_Rating, Price_Benchmark, Procurement_History tables
├── extract_docx.py             # Utility: extracts plaintext from the original hackathon .docx briefs
├── README.md                   # Full human-readable product/architecture documentation (~540 lines)
│
├── chat/                       # Blueprint "/" — main landing page, conversational AI agent
│   ├── __init__.py             # empty
│   ├── routes.py               # Flask routes: GET /, POST /api/chat
│   ├── agent.py                # OpenAI function-calling agent (gpt-4o-mini), ~600 lines
│   └── main.html               # Chat UI (902 lines) — shows reasoning steps next to replies
│
├── explorer/                   # Blueprint "/explorer/" — interactive data browser
│   ├── __init__.py             # empty
│   ├── routes.py               # 12 REST endpoints for tables/ingredients/suppliers/graph/procurement (~1050 lines)
│   └── index.html              # 5-tab SPA (1764 lines): Ingredients, Suppliers, Graph, Procurement, Tables
│
├── insights/                   # Blueprint "/agnes/" — AI-generated insights dashboard
│   ├── __init__.py             # empty
│   ├── routes.py               # 8 REST endpoints wrapping AgnesEngine (lazy singleton)
│   ├── agnes_engine.py         # Core analysis engine (~1500 lines): profiling, substitution, consolidation, risk, recs
│   └── agnes.html              # Insights dashboard UI (1030 lines): 5 tabs for recs/consolidation/subs/risks/ingredient intel
│
└── cube/                       # Blueprint "/cube/" — voice-interactive 3D cube agent
    ├── __init__.py             # empty
    ├── routes.py               # Voice preprocessing + /api/voice-chat endpoint (wraps chat.agent with voice_mode=True)
    └── index.html              # 3D cube voice UI (1112 lines)
```

The SQLite database lives outside `taim/` at `../hackathon-tumai/db.sqlite` (accessed via absolute path from each module).

---

## 2. `app.py` — Flask Entry Point

24 lines. Creates the `Flask` app, enables `flask_cors`, and registers 4 blueprints:

| Blueprint | URL prefix | Module |
|-----------|-----------|--------|
| `chat_bp` | `/` | `chat.routes` |
| `explorer_bp` | `/explorer/` | `explorer.routes` |
| `agnes_bp` | `/agnes/` | `insights.routes` |
| `cube_bp` | `/cube/` | `cube.routes` |

Runs with `debug=True, threaded=True` on port 5000.

---

## 3. `generate_mock_data.py` — One-Time Data Generator

~450 lines. Uses `random.seed(42)` for reproducibility. Populates 3 procurement tables:

- **`Supplier_Rating`** (40 rows, one per supplier): QualityScore/ComplianceScore/ReliabilityScore (Gaussian-distributed, higher for larger suppliers), LeadTimeDays, MinOrderQty, Certifications (GMP always present + random selection from {ISO-9001, ISO-22000, FSSC-22000, organic, non-GMO, kosher, halal, NSF, USP-verified}), LastAuditDate, RiskTier computed from composite score: Quality×0.35 + Compliance×0.35 + Reliability×0.30 (≥88 low, ≥75 medium, <75 high).
- **`Price_Benchmark`** (357 rows, one per unique ingredient base name): AvgMarketPrice, MinPrice, MaxPrice, PriceVolatility (0.05–0.35), via category lookup across 18 functional categories (protein, sweetener, emulsifier, vitamin, mineral, fiber, fat_oil, flavor, thickener_stabilizer, preservative, acid, color, botanical, capsule_coating, excipient, probiotic, salt_electrolyte, other) with price ranges like vitamin $40–250/kg, probiotic $80–350/kg, salt_electrolyte $1–10/kg.
- **`Procurement_History`** (~8,127 rows): 2 years of orders (Apr 2024 – Apr 2026). For each (supplier, product) link, finds which companies use the RM via BOM analysis, generates 2–8 orders/company. Unit price = base ± volatility ± quality premium (higher-quality suppliers charge slightly more). OnTime and QualityPassRate are probabilistic based on supplier ratings. Floor at 50% of base price.

Also contains helpers `_base_name(sku)` (strips `RM-C##-` prefix and trailing hex hash) and `_categorize(name)` (keyword match across FUNCTIONAL_CATEGORIES dict, score weighted by keyword length).

The script drops and recreates the 3 tables if they exist; prints summary stats and sample rows at the end.

---

## 4. `extract_docx.py` — Utility

20 lines. Uses `python-docx` to print plain text of the two hackathon brief `.docx` files (`README.docx`, `TUM.ai x Spherecast.docx`) located in `../hackathon-tumai/`. Not part of the app runtime.

---

## 5. `chat/` — Conversational AI Agent

### `chat/routes.py`
Trivial blueprint:
- `GET /` — serves `main.html`
- `POST /api/chat` — body `{message, history?, api_key?}`. Trims history to last 20 exchanges (10 user+assistant pairs), forwards to `run_agent()`. Returns `{reply, steps}`.
- Reads `OPENAI_API_KEY` env var as fallback; per-request key takes precedence.

### `chat/agent.py`
OpenAI function-calling agent using **gpt-4o-mini**, temperature 0.3. Lives or dies by its system prompt:

**System prompt (Agnes persona)** documents the full 9-table schema, SKU naming conventions, substitution logic, and critical SQL patterns. Important rules baked in:
- `RM-C{companyId}-{ingredient-name}-{hexhash}` SKU format.
- Variant substitutes share core ingredient name (e.g. `vitamin-c-ascorbic-acid` ↔ `vitamin-c-sodium-ascorbate`). Functional substitutes share a category.
- **MUST** query `Procurement_History`, `Supplier_Rating`, `Price_Benchmark` when user asks about prices/quality/costs — never say "I don't have that data".
- SQL ingredient search uses hyphens, not spaces: `LIKE '%vitamin-d%'` not `'%vitamin d%'`.
- Strict **scope restriction**: only answers supply-chain questions; politely redirects off-topic queries.

**3 tools exposed** to the model:
1. `execute_sql(query)` — only SELECT allowed, returns up to 50 rows plus a truncation flag (computed via wrapping `SELECT COUNT(*) FROM (q)` when more rows exist).
2. `find_substitutes(ingredient_name)` — custom logic:
   - Matches all RM products whose base name contains or is contained in the search.
   - Finds BOMs that use the target ingredient.
   - Computes **co-occurrence**: other RMs appearing in those same BOMs, ranked by BOM overlap count.
   - Computes **Jaccard similarity** on tokenized base names (threshold ≥ 0.3) → variant list, each enriched with supplier names and company count.
   - Co-occurring ingredients that are not already variants become functional candidates.
   - Returns `{target, variants[:15], functional_candidates[:20]}`.
3. `analyze_bom(search_term)` — finds a finished good by SKU/company-name LIKE, walks `BOM` → `BOM_Component` → `Product`, enriches each component with suppliers, per-supplier ratings, procurement averages (avg price, total spend, on-time %), and market benchmark. Flags `singleSource: True` when component has only 1 supplier.

**Agent loop** (`run_agent`):
- Up to `max_iterations = 10` tool-calling rounds.
- Each tool call is logged as a "reasoning step" with `{tool, args, label, result_preview}` for UI display.
- Accepts optional `voice_mode=True` flag — when on, appends `VOICE_ADDENDUM` to the system prompt which forces short conversational replies, no markdown, transition phrases, rounded numbers, and encourages **leading questions** when tool results are large (>5 items).

### `chat/main.html`
902-line chat UI with dark theme (`--bg: #0a0a0f`, cyan/purple accents), Space Grotesk + Sora fonts. Features:
- Clear Chat button, API key modal ("key-status" indicator).
- Shared top-nav: Chat | Explorer | Insights | Cube.
- Renders markdown in replies; reasoning steps rendered alongside (tool name + label + previewed JSON result).

---

## 6. `explorer/` — Data Explorer

### `explorer/routes.py`
Uses **SQLAlchemy core** (`create_engine`, `text()`, `inspect()`) rather than raw sqlite3. Allow-listed `TABLES` set prevents arbitrary table access. `humanize_sku()` converts e.g. `RM-C2-soy-lecithin-cc38c49d` → `"Soy Lecithin"`. `_base_name()` is the canonical extraction helper.

**Endpoints:**

| Method + Path | Purpose |
|---|---|
| `GET /explorer/` | Serve `index.html` |
| `GET /api/tables` | List table names via `inspect()` |
| `GET /api/table/<name>` | Paginated table data; dynamic `filter_<col>` query params build a parameterized `LIKE` WHERE clause. Hard limit 1–100 rows per page. |
| `GET /api/overview` | Big composite query: counts of Company/Product/FG/RM/BOM/BOM_Component/Supplier/Supplier_Product plus 4 integrity constraint violation checks (every FG has BOM, every RM has supplier, supplier links point to RMs only, every BOM has ≥2 components). Also top 8 suppliers by RM count and top 8 companies by FG count. |
| `GET /api/graph` | Vis-network graph seeded from a finished good. Builds nodes/edges: company → product → BOM → components → suppliers (limited to 5 suppliers per component). |
| `GET /api/options` | Companies (with FG count) and first 250 FG products (ordered by company) for dropdowns. |
| `GET /api/ingredients` | Grouped by base name. Query params: `minSuppliers`, `minCompanies`, `singleSource`. Returns `{baseName, name, supplierCount, companyCount, productIds, singleSourceProducts}`. Single-source count = products with only 1 linked supplier when ingredient has more than 1 total supplier. |
| `GET /api/ingredient/<base_name>` | Deep detail: enumerates all RM variants of this base name, suppliers (enriched with `Supplier_Rating` + per-supplier procurement summary from `Procurement_History` — orderCount, avgPrice, min/max, totalSpend, onTimeRate, avgQualityPass), companies using it (with products and per-product supplier lists), and benchmark pricing. |
| `GET /api/suppliers` | All suppliers with ingredient/product/company counts + sole-supplier product count. Filters: `minIngredients`, `minCompanies`, `soleSupplier`. |
| `GET /api/supplier/<id>` | Deep supplier detail: ingredients served (each with competitor suppliers flagged), companies, products, rating scorecard, procurement summary. |
| `GET /api/procurement/overview` | Stats: totalSpend, orderCount, supplier/ingredient/company counts, avg on-time, avg QC pass, risk tier distribution, top-10 suppliers by spend, top-10 ingredients by spend (computed by joining product IDs matching each benchmark base name). |
| `GET /api/procurement/savings` | Per-ingredient cost-savings analysis: finds all suppliers with procurement history for that ingredient, computes price spread %, shiftable spend, estimated savings (shiftable volume × price spread). Only includes ingredients with ≥2 suppliers with data. Ranks by `estimatedSavings`. |
| `GET /api/procurement/suppliers` | Ranked list: all 40 suppliers with rating fields + aggregated procurement fields (totalSpend, orderCount, avgUnitPrice, onTimeRate, avgQualityPass, distinct product/company counts) sorted by totalSpend DESC. |

### `explorer/index.html`
1764 lines. 5-tab SPA (Ingredients, Suppliers, Graph, Procurement, Tables). Vanilla JS, vis-network 9.1.2 for graph tab. Dark theme matching the rest of the app.

---

## 7. `insights/` — AgnesTheSecond Analysis Engine

### `insights/agnes_engine.py`
Core engine — **1497 lines**. Class `AgnesEngine(db_path)` with lazy, run-once pipeline. The engine is loaded as a singleton via `_get_engine()` in `routes.py`, protected by a threading lock.

**Knowledge bases (constants):**
- `FUNCTIONAL_CATEGORIES`: 18-category dict, each with keyword list and display label.
- `ALLERGEN_MARKERS`: 7 allergen classes (soy, dairy, gluten, tree_nut, egg, bovine, fish) with marker words.
- `QUALITY_FLAGS`: organic, non_gmo, vegan, natural, artificial, grass_fed.
- `PRIORITIZATION_WEIGHTS`: the 5-dimension framework for scoring consolidation opportunities:
  - `consolidation_benefit`: 0.35 — coverage ratio (60%) + fragmentation relief (40%)
  - `evidence_confidence`: 0.25 — data quality (procurement history + ratings + no single-source gaps)
  - `compliance_fit`: 0.20 — best supplier's quality + compliance (caps at 0.35 if either <70)
  - `supplier_diversification`: 0.10 — network resilience AFTER consolidation (monopoly guard; 0 if only 1 global supplier, 0.25 if 2, 0.55 if 3, scales up otherwise)
  - `switching_feasibility`: 0.10 — reliability score + lead time + existing coverage
- Grade thresholds: `GRADE_SAFE_THRESHOLD=0.70`, `GRADE_REJECT_THRESHOLD=0.30`, `DIVERSIFICATION_FLOOR=0.30`. Anti-monopoly veto: if final score ≥0.70 but diversification <0.30, the grade is downgraded from `safe_to_consolidate` to `review_required` with a `concentrationRiskDowngrade` flag.

**Pipeline (`run_full_analysis`):**
1. **`load_data()`** — loads Company, Product, BOM, BOM_Component, Supplier, Supplier_Product, Supplier_Rating, Price_Benchmark, Procurement_History. Builds indices: `rm_products`, `fg_products`, `base_name_index` (base_name → [product_ids]), `product_suppliers`, `supplier_to_products`, `fg_components`, `rm_to_fgs`.
2. **`profile_ingredients()`** — for each unique base name, builds `IngredientProfile` with category, allergens, quality flags, company/supplier/product/FG counts, singleSourceCount, and full pricing breakdown via `_get_ingredient_pricing()` (joins procurement history, computes per-supplier avg/min/max price, total spend, on-time %, quality pass rate, enriched with rating fields).
3. **`detect_substitution_groups()`** — two levels:
   - **Variant clusters**: Jaccard similarity ≥0.4 on tokenized base names with ≥1 shared token, requiring same functional category. Confidence 0.75.
   - **Functional sub-clusters**: within-category grouping via `_subcluster_category()` which picks the longest significant token (after stripping modifiers like `organic`, `natural`, `dl`, `alpha`, etc.) as a semantic root. Singletons then merge into any subcluster with Jaccard ≥0.25. Confidence 0.50.
4. **`analyze_consolidation()`** — for every ingredient with ≥2 companies and ≥2 distinct suppliers:
   - Builds `company_supplier_map: company_id → {supplier_ids}`.
   - Picks "best" supplier = the one covering the most companies already.
   - Computes `_consolidation_cost_analysis()`: extrapolates consolidated spend = total_qty × best_supplier_avg_price, savings and savings %.
   - Calls `_compute_prioritization_dimensions()` to produce the 5-dimension scores + grade + downgrade flag.
   - Generates evidence trail via `_build_consolidation_evidence()` with human-readable sentences.
   - Sorts opportunities by `finalScore` (with `impactScore = companyCount × supplierCount` as tiebreaker).
5. **`assess_risks()`** — 5 risk types:
   - `single_source` (HIGH): 1 supplier AND ≥2 companies.
   - `supplier_concentration` (HIGH if ≥10 sole ingredients else MEDIUM): supplier is sole source for ≥3 ingredients.
   - `critical_ingredient` (MEDIUM): ≥5 companies, ≤2 suppliers, demand/supply ratio ≥4x.
   - `supplier_quality` (HIGH if QualityScore <70, else MEDIUM): RiskTier=='high' OR QualityScore <80.
   - `price_volatility` (MEDIUM): volatility ≥0.25 AND ≥3 companies.
   - Sorted by severity then company impact.
6. **`generate_recommendations()`** — 4 types:
   - **Consolidation** (top 20 by finalScore): priority derived from grade (`safe_to_consolidate` → HIGH if ≥3 companies else MEDIUM; `review_required` → MEDIUM/LOW; `not_recommended` → LOW). Title and summary differ when `concentrationRiskDowngrade=True` — "Partial consolidation of X — lead with Y, keep a backup".
   - **Risk mitigation** (from every single_source risk): HIGH priority; lists alternative suppliers found via substitution groups; caveats about GMP qualification, formulation testing, lead time.
   - **Substitution** (from variant groups with ≥3 companies): MEDIUM; suggests standardizing on the most widely used variant.
   - **Cost optimization**: MEDIUM if spread ≥25% else LOW. Triggers when spread ≥15% AND cheapest supplier has quality ≥75 AND compliance ≥75. Estimated savings = `(expensive-cheapest)/kg × total_kg × 0.5` (50% volume shift assumption).
   - Final sort: priority HIGH→MEDIUM→LOW, then confidence desc, then companiesAffected desc.

**Per-ingredient deep-dive** (`get_ingredient_analysis`): returns profile + company usage rows + related substitution groups + consolidation opportunity + risks + recommendations.

**Full summary payload** (`get_results`): totals, category distribution, procurement aggregate (totalSpend, orderCount, avgOnTimeRate, supplierRiskDistribution), all ingredients, all groups, all opportunities, all risks, all recommendations.

### `insights/routes.py`
Thin REST wrapper around `AgnesEngine`. Lazy singleton initialization (thread-locked). 8 endpoints:

| Path | Notes |
|---|---|
| `GET /agnes/` | Serve `agnes.html` with `no-cache` headers |
| `GET /api/analysis` | Full summary + recommendations + risks + group/opportunity counts |
| `GET /api/recommendations` | Filters: `type`, `priority` |
| `GET /api/substitutions` | Filter: `type` (variant/functional) |
| `GET /api/consolidation` | Filter: `minCompanies` (default 2) |
| `GET /api/risks` | Filters: `severity`, `type` |
| `GET /api/ingredients` | Filters: `category`, `q` (substring search) |
| `GET /api/ingredient/<base_name>` | Deep dive |
| `GET /api/categories` | Category distribution |
| `POST /api/reload` | Force rebuild the singleton |

### `insights/agnes.html`
1030-line dashboard UI. 5 tabs: Recommendations, Consolidation, Substitutions, Risks, Ingredient Intel.

---

## 8. `cube/` — Voice Cube

### `cube/routes.py`
Voice-interactive agent wrapper. Adds a transcription-preprocessing pipeline before delegating to `chat.agent.run_agent` with `voice_mode=True`.

- **`_KEYWORD_CATEGORIES`** dict groups domain vocabulary into 7 categories (cost, quality, risk, supplier, ingredient, company, overview).
- **`_INTENT_PATTERNS`** — 7 regex → intent mappings (`find_substitutes`, `analyze_bom`, `compare_costs`, `assess_risk`, `count_query`, `supplier_lookup`, `find_savings`).
- **`_preprocess_transcription(raw_text)`** — detects matched keywords/categories/intents, extracts entities (quoted strings + capitalized multi-word proper nouns), and builds an enhanced message that appends a `[Voice context — structured from transcription]` block to the original text so the LLM can disambiguate noisy speech-to-text.
- **`POST /cube/api/voice-chat`** — identical contract to `/api/chat` but runs the preprocessor and forces `voice_mode=True`.
- **`GET /cube/`** — serves `index.html` with `no-cache` headers.

### `cube/index.html`
1112-line voice UI: 3D cube visualization with slightly different palette (`--bg: #0a0f1a`, accent `#6c63ff`, accent2 `#00d4aa`). Same nav shell.

---

## 9. Database Schema (at `../hackathon-tumai/db.sqlite`)

### Original tables (from hackathon dataset)

| Table | Rows | Key columns |
|---|---|---|
| `Company` | 61 | Id PK, Name, Email |
| `Product` | 1,025 (149 FG + 876 RM) | Id PK, SKU, CompanyId FK, Type ∈ {'finished-good','raw-material'} |
| `BOM` | 149 | Id PK, ProducedProductId FK→Product |
| `BOM_Component` | ~1,200 | BOMId FK, ConsumedProductId FK→Product |
| `Supplier` | 40 | Id PK, Name, Email |
| `Supplier_Product` | ~2,000 | SupplierId FK, ProductId FK |

**SKU conventions:**
- Finished goods: `FG-{BrandName}-{id}` (e.g. `FG-NutriCore-1`)
- Raw materials: `RM-C{companyId}-{ingredient-name}-{hexhash}` (e.g. `RM-C12-vitamin-c-ascorbic-acid-a3f2c1`)
- Canonical base name = lowercase hyphenated ingredient form after stripping `RM-C##-` prefix and `-hexhash` suffix.

### Mock procurement tables (from `generate_mock_data.py`)

| Table | Rows | Highlights |
|---|---|---|
| `Supplier_Rating` | 40 | QualityScore/ComplianceScore/ReliabilityScore (0–100), LeadTimeDays, MinOrderQty, Certifications (csv), LastAuditDate, RiskTier∈{low,medium,high} |
| `Price_Benchmark` | 357 | BaseName PK, AvgMarketPrice, MinPrice, MaxPrice, PriceVolatility 0–1, LastUpdated='2026-04-01' |
| `Procurement_History` | ~8,127 | SupplierId/ProductId/CompanyId FKs, OrderDate, DeliveryDate, Quantity (kg), UnitPrice ($/kg), TotalCost, Currency='USD', OnTime (0/1), QualityPassRate 0–100 |

**Aggregate stats:**
- Total procurement spend: ~$1.52 billion
- Average on-time delivery: 91.9%
- Average quality pass rate: 90.3%
- Avg quality score across suppliers: ~84.9
- Risk tier distribution: ~20 low / ~15 medium / ~5 high
- 357 unique ingredients, 18 functional categories, 7 allergen classes detected

---

## 10. Tech Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.10+, Flask, flask-cors, SQLAlchemy, sqlite3 stdlib |
| LLM | OpenAI gpt-4o-mini (function-calling, temperature 0.3) |
| Analysis libs | stdlib only for engine (`collections`, `difflib`, `re`, `math`) |
| Frontend | Vanilla HTML/CSS/JS, vis-network 9.1.2, Google Fonts (Space Grotesk + Sora) |
| Theme | Dark (`#0a0a0f` / `#0a0f1a`), cyan/purple accents |
| External data | `../hackathon-tumai/db.sqlite`, `.docx` briefs |

---

## 11. Running the App

```bash
cd taim
pip install flask flask-cors sqlalchemy networkx python-docx openai
python generate_mock_data.py   # one-time: populates 3 mock tables
export OPENAI_API_KEY=sk-...   # or pass api_key per request
python app.py
```

Routes available at `http://localhost:5000/`:
- `/` — Chat landing page
- `/explorer/` — 5-tab data explorer
- `/agnes/` — Insights dashboard
- `/cube/` — Voice Cube

---

## 12. Key Design Decisions Worth Remembering

1. **Singleton pattern** for `AgnesEngine` — analysis runs once on first `/agnes/api/*` hit, result cached on the module.
2. **Base name as canonical ingredient key** — every module (generate_mock_data, explorer, agnes_engine, chat/agent) re-implements the same `_base_name(sku)` regex: `^RM-C\d+-` prefix + `-[0-9a-f]{6,}$` suffix stripped.
3. **Jaccard-on-tokens** is the ingredient similarity metric everywhere (with modifier-word stripping).
4. **Prioritization Framework with anti-monopoly veto** — Agnes explicitly refuses to recommend full consolidation when the network has ≤2 global suppliers for an ingredient, even when the other 4 dimensions score high.
5. **Scope-restricted agent** — the chat agent refuses non-supply-chain questions; voice mode additionally forces short replies and leading clarification questions.
6. **Mock data is deterministic** — `random.seed(42)` means regenerating the DB produces identical numbers.
7. **Safety on `execute_sql`** — hard-enforced SELECT-only prefix check, 50-row cap, truncation flag.
