# AgnesTheSecond — AI-powered Supply Chain Intelligence Platform

Agnes is a full-stack decision-support system for CPG (Consumer Packaged Goods) supply chain teams. It analyzes raw material sourcing, supplier quality, procurement cost efficiency, substitution opportunities, and consolidation strategies across a multi-company network.

Built for the TUM.ai x Spherecast Makeathon 2026.

---

## Table of Contents

- [Architecture](#architecture)
- [Pages & Navigation](#pages--navigation)
- [Database Schema](#database-schema)
  - [Original Tables](#original-tables-from-hackathon-dataset)
  - [Mock Procurement Tables](#mock-procurement-tables-generated)
- [Mock Data Generation](#mock-data-generation)
  - [Supplier Ratings](#supplier-ratings)
  - [Price Benchmarks](#price-benchmarks)
  - [Procurement History](#procurement-history)
- [AgnesTheSecond Analysis Engine](#agnesthesecond-analysis-engine)
  - [Ingredient Profiling](#1-ingredient-profiling)
  - [Substitution Detection](#2-substitution-detection)
  - [Consolidation Analysis](#3-consolidation-analysis)
  - [Risk Assessment](#4-risk-assessment)
  - [Recommendations](#5-recommendations)
- [Chat Agent](#chat-agent)
- [Explorer](#explorer)
- [API Reference](#api-reference)
- [Setup & Running](#setup--running)
- [Tech Stack](#tech-stack)

---

## Architecture

```
taim/
├── app.py                    # Flask entry point, registers 3 blueprints
├── generate_mock_data.py     # One-time script to populate procurement mock data
├── chat/
│   ├── routes.py             # Blueprint at / — main landing page
│   ├── agent.py              # OpenAI function-calling agent (gpt-4o-mini)
│   └── main.html             # Chat UI with reasoning step display
├── explorer/
│   ├── routes.py             # Blueprint at /explorer/ — data exploration APIs
│   └── index.html            # 5-tab SPA (Ingredients, Suppliers, Graph, Procurement, Tables)
├── insights/
│   ├── routes.py             # Blueprint at /agnes/ — analysis API endpoints
│   ├── agnes_engine.py       # Core analysis engine (~1200 lines)
│   └── agnes.html            # 5-tab analysis dashboard UI
└── README.md                 # You are here
```

The app uses **3 Flask Blueprints**:

| Blueprint | URL Prefix | Purpose |
|-----------|-----------|---------|
| `chat_bp` | `/` | Main landing page — AI chat agent |
| `explorer_bp` | `/explorer/` | Interactive data explorer |
| `agnes_bp` | `/agnes/` | AI-generated insights & recommendations |

All three pages share a consistent top navigation bar: **Chat | Explorer | Insights**.

---

## Pages & Navigation

### 1. Chat (`/`)
An OpenAI-powered conversational agent that can query the database, analyze BOMs, find substitutes, and answer natural-language supply chain questions. Shows reasoning steps (SQL queries, tool calls) alongside the AI's response.

### 2. Explorer (`/explorer/`)
A 5-tab interactive data browser:
- **Ingredients** — Search & browse all 357 unique ingredients. Click any ingredient to see suppliers (with quality scores, certifications, procurement stats), market pricing, and which companies use it.
- **Suppliers** — Browse all 40 suppliers. Click any supplier to see their quality scorecard, certification list, risk tier, and procurement history summary.
- **Graph** — Interactive vis-network graph visualization of the supply chain (companies → products → suppliers).
- **Procurement** — Dedicated procurement analytics tab with overview stats, top cost-savings opportunities, and supplier performance rankings.
- **Tables** — Raw database table browser with filtering, pagination, and CSV-style exploration.

### 3. Insights (`/agnes/`)
AgnesTheSecond analysis dashboard with 5 tabs:
- **Recommendations** — Prioritized action items (consolidation, risk mitigation, substitution, cost optimization).
- **Consolidation** — Cross-company ingredient consolidation opportunities with cost analysis.
- **Substitutions** — Variant and functional ingredient substitution groups.
- **Risks** — Supply chain risk register (single-source, supplier concentration, critical ingredients, supplier quality, price volatility).
- **Ingredient Intel** — Deep dive into any ingredient's profile, suppliers, and procurement data.

---

## Database Schema

The SQLite database lives at `../hackathon-tumai/db.sqlite` (relative to `taim/`).

### Original Tables (from hackathon dataset)

| Table | Description | Rows |
|-------|-------------|------|
| `Company` | CPG companies in the network | 61 |
| `Product` | SKUs — finished goods (FG) and raw materials (RM) | 1,025 (149 FG + 876 RM) |
| `BOM` | Bill of Materials — one per finished good | 149 |
| `BOM_Component` | Links BOMs to their raw material components | ~1,200 |
| `Supplier` | Raw material suppliers | 40 |
| `Supplier_Product` | Which suppliers can supply which products | ~2,000 |

**SKU format:**
- Finished goods: `FG-{BrandName}-{id}` (e.g. `FG-NutriCore-1`)
- Raw materials: `RM-C{companyId}-{ingredient-name}-{hexhash}` (e.g. `RM-C12-vitamin-c-ascorbic-acid-a3f2c1`)

To extract the human-readable ingredient name from a raw material SKU: strip the `RM-C##-` prefix and the trailing `-hexhash`, then replace hyphens with spaces and title-case.

### Mock Procurement Tables (generated)

These 3 tables were added by `generate_mock_data.py` to provide realistic procurement, pricing, and quality data for analysis.

#### `Supplier_Rating` — 40 rows (one per supplier)

Quality, compliance, and reliability assessments for each supplier.

| Column | Type | Description |
|--------|------|-------------|
| `SupplierId` | INTEGER PK | FK → Supplier.Id |
| `QualityScore` | REAL (0-100) | Product quality assessment. Higher = better. Generated from gaussian distribution centered at 82-90 depending on supplier portfolio size. |
| `ComplianceScore` | REAL (0-100) | Regulatory/GMP compliance score. Higher = better. Generated centered at 85-90. |
| `ReliabilityScore` | REAL (0-100) | Delivery reliability score. Higher = better. Generated centered at 88-93. |
| `LeadTimeDays` | INTEGER | Typical order-to-delivery time in days (5-90). Larger suppliers tend to be faster. |
| `MinOrderQty` | INTEGER | Minimum order quantity in kg. One of: 50, 100, 250, 500, 1000, 2500, 5000. |
| `Certifications` | TEXT | Comma-separated certification list. GMP is almost always included. Others: ISO-9001, ISO-22000, FSSC-22000, organic, non-GMO, kosher, halal, NSF, USP-verified. |
| `LastAuditDate` | TEXT | ISO date of last quality audit (within past 18 months). |
| `RiskTier` | TEXT | `'low'`, `'medium'`, or `'high'`. Computed from composite score: (Quality×0.35 + Compliance×0.35 + Reliability×0.30). ≥88 → low, ≥75 → medium, <75 → high. |

**Key statistics:**
- Average quality score: ~84.9
- Risk tier distribution: ~20 low, ~15 medium, ~5 high
- Average lead time: ~22 days
- Most common certifications: GMP (all), ISO-9001, non-GMO, kosher

#### `Price_Benchmark` — 357 rows (one per unique ingredient)

Market reference prices per ingredient, used to compare actual procurement costs against market norms.

| Column | Type | Description |
|--------|------|-------------|
| `BaseName` | TEXT PK | Canonical ingredient name in lowercase-hyphenated form (e.g. `vitamin-c-ascorbic-acid`). Matches the base name extracted from SKUs. |
| `AvgMarketPrice` | REAL | Average market price in $/kg. |
| `MinPrice` | REAL | Minimum observed market price in $/kg. |
| `MaxPrice` | REAL | Maximum observed market price in $/kg. |
| `PriceVolatility` | REAL (0-1) | Price volatility coefficient. 0.05 = very stable, 0.35 = highly volatile. Ingredients with volatility ≥ 0.25 are flagged as price-volatile risks. |
| `LastUpdated` | TEXT | ISO date when the benchmark was last updated (set to 2026-04-01). |

**Price ranges by functional category:**

| Category | $/kg Range | Examples |
|----------|-----------|----------|
| Protein & Amino Acids | $15-60 | Whey protein, collagen, BCAAs |
| Sweeteners | $2-20 | Stevia, erythritol, maltodextrin |
| Emulsifiers | $8-35 | Soy lecithin, sunflower lecithin |
| Vitamins | $40-250 | Vitamin C, D3, B12, tocopherols |
| Minerals | $5-45 | Calcium, magnesium, zinc, iron |
| Fiber & Prebiotics | $4-18 | Inulin, psyllium, tapioca fiber |
| Fats & Oils | $3-15 | MCT oil, coconut oil, safflower |
| Flavors & Extracts | $20-120 | Vanilla, chocolate, cinnamon |
| Thickeners & Stabilizers | $6-30 | Xanthan gum, gelatin, pectin |
| Preservatives | $8-40 | Sorbate, benzoate, rosemary extract |
| Acids | $3-15 | Citric acid, malic acid, lactic acid |
| Colors & Colorants | $25-150 | Annatto, beet extract, caramel color |
| Botanicals & Nutraceuticals | $30-180 | Ashwagandha, CoQ10, lutein |
| Capsules & Coatings | $10-50 | HPMC capsules, softgel, gelatin capsule |
| Excipients & Fillers | $2-12 | Microcrystalline cellulose, silica |
| Probiotics & Cultures | $80-350 | Lactobacillus, bifidobacterium |
| Salts & Electrolytes | $1-10 | Sodium chloride, potassium chloride |

#### `Procurement_History` — ~8,127 rows

Two years of historical purchase orders (April 2024 – April 2026) for every supplier-product-company combination.

| Column | Type | Description |
|--------|------|-------------|
| `Id` | INTEGER PK | Auto-incrementing order ID |
| `SupplierId` | INTEGER FK | FK → Supplier.Id |
| `ProductId` | INTEGER FK | FK → Product.Id (the raw material purchased) |
| `CompanyId` | INTEGER FK | FK → Company.Id (the buying company) |
| `OrderDate` | TEXT | ISO date when order was placed |
| `DeliveryDate` | TEXT | ISO date when order was delivered |
| `Quantity` | REAL | Order quantity in kg |
| `UnitPrice` | REAL | Price per kg in USD |
| `TotalCost` | REAL | Quantity × UnitPrice |
| `Currency` | TEXT | Always `'USD'` |
| `OnTime` | INTEGER | 1 = delivered on time, 0 = late. Probability correlated with supplier's ReliabilityScore. |
| `QualityPassRate` | REAL (0-100) | Incoming quality inspection pass rate for this order. Correlated with supplier's QualityScore. |

**Key statistics:**
- Total procurement spend: ~$1.52 billion
- Total orders: 8,127
- Average on-time delivery rate: 91.9%
- Average quality pass rate: 90.3%
- Orders per supplier-product-company link: 2-8 (random)
- Quantities based on supplier MOQ × multiplier (1x, 2x, 3x, or 5x)

**Pricing logic:**
- Base price comes from the ingredient's Price_Benchmark
- Supplier-specific adjustment: higher quality suppliers charge a slight premium (`(qualityScore - 80) / 100 × 10%`)
- Random price variation within the ingredient's volatility range
- Floor set at 50% of base price to prevent unrealistic lows

---

## Mock Data Generation

The `generate_mock_data.py` script populates the 3 procurement tables above. It uses `random.seed(42)` for full reproducibility.

### How it works:

1. **Loads existing data** — reads all Company, Product, BOM, BOM_Component, Supplier, and Supplier_Product rows.

2. **Generates Supplier Ratings** — For each of the 40 suppliers:
   - Computes a "size factor" based on how many products the supplier offers (portfolio breadth).
   - Quality/compliance/reliability scores are drawn from gaussian distributions, with larger suppliers scoring slightly higher.
   - Certifications: GMP is always included. Additional certs selected randomly, count correlated with supplier size.
   - Risk tier computed from weighted composite score.

3. **Generates Price Benchmarks** — For each of the 357 unique ingredient base names:
   - Categorizes the ingredient into one of 18 functional categories using keyword matching.
   - Selects a price range from the category lookup table.
   - Generates avg/min/max prices and a volatility coefficient.

4. **Generates Procurement History** — For each (supplier, product) link:
   - Determines which companies use this product (via BOM analysis).
   - Generates 2-8 orders per company over the 2-year window.
   - Pricing includes base price ± volatility ± quality premium.
   - Delivery timing based on supplier's lead time with gaussian noise.
   - On-time flag probabilistic based on reliability score.
   - Quality pass rate probabilistic based on quality score.

### Running it:

```bash
cd taim
python generate_mock_data.py
```

Only needs to be run once. It will drop and recreate the tables if they already exist.

---

## AgnesTheSecond Analysis Engine

The core engine (`insights/agnes_engine.py`, ~1200 lines) runs a complete analysis pipeline when the Insights page is first loaded. The engine is a lazy-initialized singleton — it analyzes once and caches results.

### Pipeline:

```
load_data() → profile_ingredients() → detect_substitution_groups()
    → analyze_consolidation() → assess_risks() → generate_recommendations()
```

### 1. Ingredient Profiling

For every unique ingredient (identified by the base name extracted from SKUs), the engine builds a comprehensive profile:

- **Functional category** — Assigned via keyword matching against 18 categories (protein, sweetener, emulsifier, vitamin, mineral, fiber, fat/oil, flavor, thickener/stabilizer, preservative, acid, color, botanical, capsule/coating, excipient, probiotic, salt/electrolyte, other).
- **Allergen detection** — Scans ingredient names for allergen markers (soy, dairy, gluten, tree nut, egg, bovine, fish).
- **Quality flags** — Detects organic, non-GMO, vegan, natural, artificial, grass-fed.
- **Company count** — How many companies in the network use this ingredient.
- **Supplier count** — How many suppliers can provide this ingredient.
- **Single-source count** — How many product SKUs have only 1 linked supplier.
- **Pricing profile** — Market benchmark comparison, per-supplier pricing breakdown, total procurement spend, price vs market differential.

### 2. Substitution Detection

Three levels of substitution are detected:

| Level | Name | Logic | Confidence | Example |
|-------|------|-------|-----------|---------|
| **Direct** | Same ingredient, different companies | Same base name → trivially grouped | 1.0 | Vitamin C by Company A = Vitamin C by Company B |
| **Variant** | Same root, different modifier | Jaccard similarity ≥ 0.4 on name tokens, same functional category | 0.75 | `soy-lecithin` ↔ `sunflower-lecithin`, `organic-stevia` ↔ `stevia-extract` |
| **Functional** | Same category, different ingredient | Sub-clustered within category by shared semantic root tokens | 0.50 | `erythritol` ↔ `stevia` (both sweeteners), `xanthan-gum` ↔ `gellan-gum` (both thickeners) |

The engine uses Jaccard similarity on ingredient name tokens (with modifier words like "organic", "natural", etc. stripped) to identify variant groups. Within functional groups, it further sub-clusters by finding the longest shared significant token as a semantic root.

### 3. Consolidation Analysis

Identifies opportunities to consolidate purchasing across companies:

- **Trigger**: An ingredient must be used by ≥2 companies from ≥2 different suppliers.
- **Logic**: For each qualifying ingredient, the engine finds the "best" supplier (the one already serving the most companies) and computes which companies could switch.
- **Cost analysis**: If procurement data is available, computes:
  - Current total spend across all suppliers
  - Estimated consolidated spend if all volume moves to the best supplier
  - Dollar savings and percentage savings
  - Quality/compliance/reliability scores of the recommended supplier
- **Evidence trail**: Each opportunity includes human-readable evidence statements documenting the rationale.
- **Impact score**: `companyCount × currentSupplierCount` — prioritizes high-reach, high-fragmentation opportunities.

### 4. Risk Assessment

Five types of supply chain risks are assessed:

#### a) Single-Source Risk (`single_source`) — Severity: HIGH
- **Condition**: Ingredient has exactly 1 supplier AND is used by ≥2 companies.
- **Why it matters**: If that supplier has a disruption (factory fire, quality issue, regulatory action), multiple companies lose their supply simultaneously.
- **Example**: "Astaxanthin is supplied by only Prinova USA, affecting 12 companies and 12 products."

#### b) Supplier Concentration Risk (`supplier_concentration`) — Severity: HIGH/MEDIUM
- **Condition**: A single supplier is the sole source for ≥3 different ingredients.
- **Why it matters**: Over-dependence on one supplier across multiple ingredients creates correlated risk.
- **Severity**: HIGH if sole-source for ≥10 ingredients, MEDIUM if ≥3.
- **Example**: "Prinova USA is the sole supplier for 15 ingredients. Loss of this supplier would create critical shortages."

#### c) Critical Ingredient Risk (`critical_ingredient`) — Severity: MEDIUM
- **Condition**: Ingredient serves ≥5 companies but has ≤2 suppliers, with a demand/supply ratio ≥4x.
- **Why it matters**: High-demand ingredients with thin supplier bases are vulnerable to capacity constraints.
- **Example**: "Vitamin C serves 12 companies but has only 2 suppliers. Demand/supply ratio: 6.0x."

#### d) Supplier Quality Risk (`supplier_quality`) — Severity: HIGH/MEDIUM
- **Condition**: Supplier has a quality score <80 OR is classified as high-risk tier.
- **Why it matters**: Low-quality suppliers increase the probability of batch rejections, recalls, and compliance issues.
- **Severity**: HIGH if quality score <70, MEDIUM if <80.
- **Example**: "BulkSupplements has a quality score of 73/100 and compliance score of 78/100, classified as high-risk."

#### e) Price Volatility Risk (`price_volatility`) — Severity: MEDIUM
- **Condition**: Ingredient has price volatility ≥0.25 (25%) AND is used by ≥3 companies.
- **Why it matters**: High price swings make cost forecasting unreliable and can erode margins.
- **Example**: "Lutein has 32% price volatility affecting 8 companies. Avg price: $142.50/kg."

### 5. Recommendations

Four types of prioritized, evidence-backed recommendations:

#### a) Consolidation Recommendations (`consolidation`)
- Derived from the top 20 consolidation opportunities.
- Priority: HIGH if ≥5 companies affected, MEDIUM if ≥3, LOW otherwise.
- Confidence: 0.80 if recommended supplier already covers >50% of companies, 0.60 otherwise.
- Includes cost impact (estimated savings) and evidence trail.

#### b) Risk Mitigation Recommendations (`risk_mitigation`)
- One recommendation per single-source risk.
- Always HIGH priority.
- Suggests qualifying a second supplier.
- Lists potential alternative suppliers found via substitution group analysis.
- Includes caveats: GMP qualification, formulation testing, lead time assessment.

#### c) Substitution Recommendations (`substitution`)
- Derived from variant substitution groups with ≥3 total companies.
- MEDIUM priority.
- Suggests standardizing on the most widely used variant.
- Notes allergen considerations and regulatory review needs.

#### d) Cost Optimization Recommendations (`cost_optimization`)
- **Condition**: Price spread ≥15% across suppliers for the same ingredient, AND the cheapest supplier has quality ≥75 AND compliance ≥75.
- MEDIUM priority if spread ≥25%, LOW otherwise.
- Estimates savings conservatively (50% of volume shift).
- Includes per-supplier price comparison, market benchmark, quality scores, and on-time rates.

**Overall recommendation stats (typical run): ~54 total** — 20 consolidation, 18 risk mitigation, 6 substitution, 10 cost optimization.

---

## Chat Agent

The chat module (`chat/agent.py`) is an OpenAI function-calling agent using **gpt-4o-mini** (temperature 0.3).

### Tools available to the agent:

| Tool | Description |
|------|-------------|
| `execute_sql` | Execute any read-only SELECT query against the database. Returns up to 50 rows. |
| `find_substitutes` | Find substitute ingredients via BOM co-occurrence and name similarity. Returns variant and functional candidates. |
| `analyze_bom` | Analyze a finished good's Bill of Materials. Lists all components, their suppliers (with quality ratings and procurement pricing), market benchmarks, and single-source flags. |

### How it works:
1. User sends a natural-language question.
2. The agent receives a detailed system prompt describing the full schema (all 9 tables), domain knowledge, substitution logic, and cost/quality analysis rules.
3. The agent decides which tools to call (may chain multiple tools in sequence).
4. Each tool execution is logged as a "reasoning step" visible in the UI.
5. After gathering data, the agent synthesizes a markdown-formatted answer.
6. Supports multi-turn conversation with history (last 10 exchanges).

### Example questions it handles:
- "What are the top 5 most common ingredients across all companies?"
- "Which suppliers have the highest quality scores?"
- "What substitutes exist for soy lecithin?"
- "Analyze the BOM for company NutriCore"
- "What are the cheapest suppliers for vitamin C?"
- "Show me single-source risk ingredients"

---

## Explorer

The Explorer (`explorer/routes.py`) provides REST APIs for interactive data exploration.

### Procurement Analytics Tab

The Procurement tab provides three dedicated API endpoints:

**Overview Stats:**
- Total procurement spend (~$1.52B)
- Total order count (8,127)
- Active supplier count (40)
- Average on-time delivery rate (91.9%)
- Average quality pass rate (90.3%)
- Supplier risk tier distribution (low/medium/high)
- Top 10 suppliers by spend
- Top 10 ingredients by spend

**Cost Savings Explorer:**
- Ranks all 296 ingredients by estimated savings potential
- For each ingredient, compares prices across all suppliers
- Highlights the cheapest qualified supplier vs. most expensive
- Shows price spread percentage, shiftable spend, and estimated dollar savings
- Includes per-supplier breakdown: avg price, quality score, compliance, risk tier, certifications
- Filterable by minimum savings amount and minimum spread percentage
- Sortable by savings, spread, or ingredient name

**Supplier Rankings:**
- All 40 suppliers ranked by total procurement spend
- Quality score, compliance score, reliability score for each
- On-time delivery rate from actual procurement history
- Risk tier badge (low/medium/high)
- Number of products and companies served

---

## API Reference

### Chat

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | Chat UI page |
| POST | `/api/chat` | Send message to Agnes agent. Body: `{message, history?, api_key?}` |

### Explorer

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/explorer/` | Explorer UI page |
| GET | `/explorer/api/tables` | List all database tables |
| GET | `/explorer/api/table/:name` | Paginated table data with filtering |
| GET | `/explorer/api/overview` | High-level database stats |
| GET | `/explorer/api/ingredients` | All ingredients with supplier/company counts |
| GET | `/explorer/api/ingredient/:name` | Deep ingredient detail (suppliers, ratings, pricing, BOMs) |
| GET | `/explorer/api/suppliers-list` | All suppliers with product counts |
| GET | `/explorer/api/supplier/:id` | Deep supplier detail (products, rating scorecard, procurement) |
| GET | `/explorer/api/graph` | Vis-network graph data (nodes + edges) |
| GET | `/explorer/api/procurement/overview` | Procurement stats, risk dist, top spend |
| GET | `/explorer/api/procurement/savings` | Cost savings opportunities per ingredient |
| GET | `/explorer/api/procurement/suppliers` | Supplier performance rankings |

### Insights (Agnes Engine)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/agnes/` | Insights dashboard UI |
| GET | `/agnes/api/analysis` | Full analysis results (summary, all data) |
| GET | `/agnes/api/recommendations` | Filtered/paginated recommendations |
| GET | `/agnes/api/consolidation` | Consolidation opportunities |
| GET | `/agnes/api/substitutions` | Substitution groups |
| GET | `/agnes/api/risks` | Risk register |
| GET | `/agnes/api/ingredient/:name` | Single ingredient deep analysis |

---

## Setup & Running

### Prerequisites
- Python 3.10+
- An OpenAI API key (for the Chat agent — set `OPENAI_API_KEY` env var)

### Install dependencies

```bash
# From repo root
python -m venv .venv

# Windows
.venv\Scripts\activate

# Install packages
pip install flask flask-cors sqlalchemy networkx python-docx openai
```

### Generate mock procurement data (one-time)

```bash
cd AgnesTheSecond/taim
python generate_mock_data.py
```

### Run the app

```bash
cd AgnesTheSecond/taim
python app.py
```

The app starts at **http://localhost:5000**:
- `/` — Chat (main landing page)
- `/explorer/` — Data Explorer
- `/agnes/` — AgnesTheSecond Insights

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENAI_API_KEY` | For Chat only | OpenAI API key. Can also be passed per-request in the chat body. |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, Flask, SQLAlchemy, SQLite |
| AI/LLM | OpenAI gpt-4o-mini (function-calling agent) |
| Analysis | Custom Python engine (networkx, difflib) |
| Frontend | Vanilla HTML/CSS/JS, vis-network 9.1.2 |
| Fonts | Google Fonts — Space Grotesk (headings) + Sora (body) |
| Theme | Dark theme (#0a0a0f background, cyan/purple accent palette) |

---

## Data Summary at a Glance

| Metric | Value |
|--------|-------|
| Companies | 61 |
| Products (total) | 1,025 |
| Finished Goods | 149 |
| Raw Materials | 876 |
| Unique Ingredients | 357 |
| Suppliers | 40 |
| Bills of Materials | 149 |
| Supplier Ratings | 40 |
| Price Benchmarks | 357 |
| Procurement Orders | 8,127 |
| Total Procurement Spend | ~$1.52 billion |
| Average On-Time Rate | 91.9% |
| Average Quality Pass Rate | 90.3% |
| Risk Types Tracked | 5 |
| Recommendation Types | 4 |
| Functional Categories | 18 |
| Allergen Types Detected | 7 |
