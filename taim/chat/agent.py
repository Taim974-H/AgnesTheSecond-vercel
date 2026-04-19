"""
AgnesTheSecond Agent
=====================
OpenAI-powered agent that answers natural-language supply chain questions
by querying the SQLite database, finding substitutes via BOM analysis,
and synthesising actionable answers.
"""

import sqlite3
import os
import re
import json
from openai import OpenAI
from sourcing.engine import source_ingredients

DB_PATH = os.path.join(
    os.path.dirname(__file__), '../../hackathon-tumai/db.sqlite'
)
DB_PATH = os.path.abspath(DB_PATH)

SYSTEM_PROMPT = """You are Agnes, an expert AI supply-chain analyst for a CPG (Consumer Packaged Goods) company network.

You have access to a SQLite database with this schema:

  Company(Id INTEGER PK, Name TEXT, Email TEXT)
  Product(Id INTEGER PK, SKU TEXT, CompanyId INTEGER FK→Company, Type TEXT CHECK('finished-good','raw-material'))
  BOM(Id INTEGER PK, ProducedProductId INTEGER FK→Product)  -- one BOM per finished good
  BOM_Component(BOMId INTEGER FK→BOM, ConsumedProductId INTEGER FK→Product)  -- raw materials in a BOM
  Supplier(Id INTEGER PK, Name TEXT, Email TEXT)
  Supplier_Product(SupplierId FK→Supplier, ProductId FK→Product)  -- which suppliers can supply which products

  -- Procurement & quality data (historical mock data for analysis):
  Supplier_Rating(SupplierId PK, QualityScore REAL 0-100, ComplianceScore REAL 0-100, ReliabilityScore REAL 0-100, LeadTimeDays INT, MinOrderQty INT, Certifications TEXT comma-separated, LastAuditDate TEXT, RiskTier TEXT 'low'|'medium'|'high')
  Procurement_History(Id PK, SupplierId FK, ProductId FK, CompanyId FK, OrderDate TEXT, DeliveryDate TEXT, Quantity REAL, UnitPrice REAL $/kg, TotalCost REAL, Currency TEXT, OnTime INT 0|1, QualityPassRate REAL 0-100)
  Price_Benchmark(BaseName TEXT PK, AvgMarketPrice REAL $/kg, MinPrice REAL, MaxPrice REAL, PriceVolatility REAL 0-1, LastUpdated TEXT)

Key domain facts:
- Product SKUs encode ingredient names: RM-C{companyId}-{ingredient-name}-{hex}  or  FG-{brand}-{id}
- To get the human-readable ingredient name from a raw-material SKU, strip the RM-C##- prefix and the trailing -hexhash, then replace hyphens with spaces and title-case.
- 61 companies, 1025 products (149 finished goods, 876 raw materials), 357 unique ingredients, 40 suppliers.
- Each finished good has exactly one BOM. Each BOM lists its raw-material components.
- Supplier_Product links suppliers to raw-material Products.
- Procurement_History contains ~8000 historical orders over 2 years with pricing and delivery data.
- Supplier_Rating contains quality, compliance, and reliability scores for all 40 suppliers, plus certifications.
- Price_Benchmark has market reference prices per ingredient (by BaseName which is the lowercase hyphenated form).

Substitution logic (IMPORTANT):
- Two raw materials are VARIANT substitutes if they share the same core ingredient name (e.g. "vitamin-c-ascorbic-acid" vs "vitamin-c-sodium-ascorbate" — both are vitamin C).
- Two raw materials are FUNCTIONAL substitutes if they appear in the same functional category (e.g. both are thickeners, both are sweeteners, both are preservatives) even if they have different names.
- BOM co-occurrence is a strong signal: if two raw materials frequently appear in the same types of finished goods, they likely serve the same functional role.
- When suggesting substitutes, always note caveats: formulation testing needed, allergen differences, regulatory considerations.

Cost & Quality analysis:
- A sourcing recommendation is only valid if quality and compliance constraints are still met.
- Compare supplier prices against market benchmarks (Price_Benchmark table).
- Factor in supplier quality scores, compliance scores, on-time delivery rates, and certifications.
- When recommending supplier switches, verify the new supplier has adequate certifications (GMP at minimum).
- Flag cost savings opportunities when price spreads exceed 15% across suppliers for the same ingredient.

CRITICAL — Querying procurement, price, and quality data:
- To find suppliers for an ingredient and their PRICES, you MUST join through Product:
    SELECT s.Name, AVG(ph.UnitPrice) as avg_price, SUM(ph.TotalCost) as total_spend,
           COUNT(*) as orders
    FROM Procurement_History ph
    JOIN Supplier s ON s.Id = ph.SupplierId
    JOIN Product p ON p.Id = ph.ProductId
    WHERE p.Type = 'raw-material' AND p.SKU LIKE '%ingredient-name%'
    GROUP BY s.Id, s.Name
- To get supplier quality/compliance ratings alongside prices:
    SELECT s.Name, sr.QualityScore, sr.ComplianceScore, sr.ReliabilityScore,
           sr.RiskTier, sr.Certifications, sr.LeadTimeDays,
           AVG(ph.UnitPrice) as avg_price, AVG(ph.QualityPassRate) as quality_pass
    FROM Supplier_Product sp
    JOIN Supplier s ON s.Id = sp.SupplierId
    JOIN Product p ON p.Id = sp.ProductId
    LEFT JOIN Supplier_Rating sr ON sr.SupplierId = s.Id
    LEFT JOIN Procurement_History ph ON ph.SupplierId = s.Id AND ph.ProductId = p.Id
    WHERE p.SKU LIKE '%ingredient-name%'
    GROUP BY s.Id
- To compare against market benchmark:
    SELECT pb.BaseName, pb.AvgMarketPrice, pb.MinPrice, pb.MaxPrice, pb.PriceVolatility
    FROM Price_Benchmark pb WHERE pb.BaseName LIKE '%ingredient-name%'
- Remember: ingredient names in SKUs use hyphens (e.g. 'vitamin-d', 'soy-lecithin', 'ascorbic-acid').
  Use LIKE '%vitamin-d%' (not 'vitamin d' with a space) when searching by SKU.
- ALWAYS query Procurement_History when the user asks about prices, costs, or spending. Do NOT say you lack pricing data.
- ALWAYS query Supplier_Rating when the user asks about quality, compliance, reliability, or certifications.
- IMPORTANT: Only include data the user actually asked for. If the user asks only about prices, do NOT include quality scores, compliance, or reliability data unless they ask for it. Answer precisely what was asked.

When answering questions:
1. Use the execute_sql tool to query the database. Write clean SQL.
2. Use find_substitutes tool to find potential replacements for ingredients.
3. Use analyze_bom to inspect a product's bill of materials.
4. Use source_product when the user wants to create/source a new product and provides a list of ingredients — this returns a complete deterministic sourcing report with all supplier details, pricing, quality, and risk flags.
5. Always ground your answers in actual data from tools — never fabricate numbers.
6. Be specific: name companies, suppliers, ingredient names, counts, prices, quality scores.
7. When relevant, mention risks (single-source, concentration, quality), costs, and opportunities (consolidation, substitution, savings).
8. Format your answers in clear Markdown with headers, bullet points, and tables where helpful.
9. If a query returns no results or you're uncertain, say so honestly.

SCOPE RESTRICTION (CRITICAL):
- You are ONLY allowed to answer questions related to supply chain, procurement, sourcing, ingredients, suppliers, products, BOMs, costs, pricing, quality, compliance, logistics, and our business data.
- If the user asks about anything outside this scope (e.g. general knowledge, coding, weather, politics, entertainment, personal advice, math homework, etc.), politely decline and redirect them back to supply chain topics.
- Example refusal: "I'm Agnes, your supply chain analyst — I can only help with questions about our suppliers, ingredients, procurement, costs, and product data. What would you like to know about your supply chain?"
- Even if the user insists or rephrases, stay within your domain. Do not answer off-topic questions.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "execute_sql",
            "description": "Execute a read-only SQL SELECT query against the supply chain database. Returns up to 50 rows. Use this to look up companies, products, BOMs, suppliers, ingredient counts, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "A SQL SELECT statement. Must be read-only (no INSERT/UPDATE/DELETE). Use LIMIT to cap results."
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_substitutes",
            "description": "Find potential substitute ingredients for a given ingredient name. Searches by BOM co-occurrence (ingredients that appear alongside the target in finished goods), same functional category (name-based heuristic), and variant detection (similar names). Returns ranked candidates with reasoning.",
            "parameters": {
                "type": "object",
                "properties": {
                    "ingredient_name": {
                        "type": "string",
                        "description": "The ingredient name or partial name to find substitutes for (e.g. 'glycerin', 'vitamin c', 'soy lecithin')."
                    }
                },
                "required": ["ingredient_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_bom",
            "description": "Analyze a finished-good product's Bill of Materials: lists all raw-material components, their suppliers, and flags single-source risks. Can search by product SKU fragment or company name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "search_term": {
                        "type": "string",
                        "description": "A product SKU fragment, product name, or company name to find and analyze the BOM for."
                    }
                },
                "required": ["search_term"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "source_product",
            "description": "Given a product concept and its ingredients, find all matching suppliers from the database with pricing, quality scores, certifications, lead times, and risk flags for each ingredient. Use this when the user says they want to make a new product and lists ingredients, or asks 'who can supply X, Y, and Z'. Returns a full deterministic sourcing report.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_name": {
                        "type": "string",
                        "description": "Name of the product being sourced (e.g. 'Premium Protein Bar')."
                    },
                    "ingredients": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of ingredient names to find suppliers for (e.g. ['whey protein', 'cocoa', 'stevia'])."
                    }
                },
                "required": ["ingredients"]
            }
        }
    }
]


def _base_name(sku):
    """Extract canonical ingredient name from SKU."""
    if not sku:
        return ''
    cleaned = re.sub(r'^(FG|RM)-[A-Za-z0-9]+-', '', sku)
    cleaned = re.sub(r'-[0-9a-f]{6,}$', '', cleaned, flags=re.IGNORECASE)
    return cleaned.lower().strip('-')


def _humanize(name):
    return name.replace('-', ' ').strip().title()


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def tool_execute_sql(query):
    """Execute a read-only SQL query and return results."""
    q = query.strip()
    # Safety: only allow SELECT
    if not q.upper().startswith('SELECT'):
        return {"error": "Only SELECT queries are allowed."}
    try:
        conn = _get_conn()
        cursor = conn.execute(q)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchmany(50)
        result = [dict(zip(columns, row)) for row in rows]
        total = len(result)
        # Check if there are more
        extra = cursor.fetchone()
        if extra:
            total_count = conn.execute(
                f"SELECT COUNT(*) FROM ({q})"
            ).fetchone()[0]
            conn.close()
            return {"columns": columns, "rows": result, "total": total_count, "truncated": True}
        conn.close()
        return {"columns": columns, "rows": result, "total": total, "truncated": False}
    except Exception as e:
        return {"error": str(e)}


def tool_find_substitutes(ingredient_name):
    """Find substitutes for an ingredient using BOM co-occurrence and name similarity."""
    search = ingredient_name.lower().replace(' ', '-')
    conn = _get_conn()

    # 1. Find all product IDs matching this ingredient
    all_rm = conn.execute(
        "SELECT Id, SKU, CompanyId FROM Product WHERE Type = 'raw-material'"
    ).fetchall()

    matching_ids = []
    for r in all_rm:
        bn = _base_name(r['SKU'])
        if search in bn or bn in search:
            matching_ids.append(r['Id'])

    if not matching_ids:
        conn.close()
        return {"error": f"No ingredient found matching '{ingredient_name}'."}

    target_base = _base_name(
        conn.execute("SELECT SKU FROM Product WHERE Id = ?", (matching_ids[0],)).fetchone()['SKU']
    )

    # 2. Find BOMs that use this ingredient
    ph = ','.join('?' * len(matching_ids))
    bom_rows = conn.execute(f"""
        SELECT DISTINCT bc.BOMId
        FROM BOM_Component bc
        WHERE bc.ConsumedProductId IN ({ph})
    """, matching_ids).fetchall()
    bom_ids = [r['BOMId'] for r in bom_rows]

    # 3. Find all other ingredients that appear in these same BOMs (co-occurrence)
    co_occurring = {}
    if bom_ids:
        bph = ','.join('?' * len(bom_ids))
        co_rows = conn.execute(f"""
            SELECT bc.ConsumedProductId, p.SKU, COUNT(DISTINCT bc.BOMId) as bom_count
            FROM BOM_Component bc
            JOIN Product p ON p.Id = bc.ConsumedProductId
            WHERE bc.BOMId IN ({bph})
              AND bc.ConsumedProductId NOT IN ({ph})
            GROUP BY bc.ConsumedProductId
            ORDER BY bom_count DESC
        """, bom_ids + matching_ids).fetchall()

        for r in co_rows:
            bn = _base_name(r['SKU'])
            if bn not in co_occurring:
                co_occurring[bn] = {
                    'name': _humanize(bn),
                    'bom_overlap': r['bom_count'],
                    'product_ids': []
                }
            co_occurring[bn]['product_ids'].append(r['ConsumedProductId'])
            co_occurring[bn]['bom_overlap'] = max(co_occurring[bn]['bom_overlap'], r['bom_count'])

    # 4. Find name-similar ingredients (variant substitutes)
    target_tokens = set(target_base.replace('-', ' ').split())
    variants = []
    functional = []

    all_base_names = {}
    for r in all_rm:
        bn = _base_name(r['SKU'])
        if bn and bn != target_base:
            if bn not in all_base_names:
                all_base_names[bn] = []
            all_base_names[bn].append(r['Id'])

    for bn, pids in all_base_names.items():
        tokens = set(bn.replace('-', ' ').split())
        overlap = target_tokens & tokens
        jaccard = len(overlap) / len(target_tokens | tokens) if (target_tokens | tokens) else 0

        if jaccard >= 0.3:
            # Get suppliers for this ingredient
            suppliers = conn.execute(f"""
                SELECT DISTINCT s.Name
                FROM Supplier_Product sp
                JOIN Supplier s ON s.Id = sp.SupplierId
                WHERE sp.ProductId IN ({','.join('?' * len(pids))})
            """, pids).fetchall()

            # Count companies using it
            companies = conn.execute(f"""
                SELECT COUNT(DISTINCT CompanyId) as cnt
                FROM Product WHERE Id IN ({','.join('?' * len(pids))})
            """, pids).fetchone()

            variants.append({
                'name': _humanize(bn),
                'baseName': bn,
                'similarity': round(jaccard, 2),
                'supplierNames': [s['Name'] for s in suppliers],
                'companyCount': companies['cnt'],
                'inSameBoms': bn in co_occurring,
            })

    variants.sort(key=lambda x: (-x['similarity'], -x['companyCount']))

    # 5. BOM co-occurrence candidates (functional substitutes)
    # Ingredients that appear in many of the same BOMs but aren't name-similar
    variant_names = {v['baseName'] for v in variants}
    for bn, info in sorted(co_occurring.items(), key=lambda x: -x[1]['bom_overlap']):
        if bn in variant_names:
            continue
        pids = info['product_ids']
        suppliers = conn.execute(f"""
            SELECT DISTINCT s.Name
            FROM Supplier_Product sp
            JOIN Supplier s ON s.Id = sp.SupplierId
            WHERE sp.ProductId IN ({','.join('?' * len(pids))})
        """, pids).fetchall()

        companies = conn.execute(f"""
            SELECT COUNT(DISTINCT CompanyId) as cnt
            FROM Product WHERE Id IN ({','.join('?' * len(pids))})
        """, pids).fetchone()

        functional.append({
            'name': info['name'],
            'baseName': bn,
            'bomOverlap': info['bom_overlap'],
            'totalBoms': len(bom_ids),
            'supplierNames': [s['Name'] for s in suppliers],
            'companyCount': companies['cnt'],
        })

    functional.sort(key=lambda x: (-x['bomOverlap'], -x['companyCount']))

    # Get suppliers for the target ingredient
    target_suppliers = conn.execute(f"""
        SELECT DISTINCT s.Name
        FROM Supplier_Product sp
        JOIN Supplier s ON s.Id = sp.SupplierId
        WHERE sp.ProductId IN ({ph})
    """, matching_ids).fetchall()

    target_companies = conn.execute(f"""
        SELECT COUNT(DISTINCT CompanyId) as cnt
        FROM Product WHERE Id IN ({ph})
    """, matching_ids).fetchone()

    conn.close()

    return {
        "target": {
            "name": _humanize(target_base),
            "baseName": target_base,
            "supplierNames": [s['Name'] for s in target_suppliers],
            "companyCount": target_companies['cnt'],
            "bomCount": len(bom_ids),
        },
        "variants": variants[:15],
        "functional_candidates": functional[:20],
    }


def tool_analyze_bom(search_term):
    """Analyze a product's BOM."""
    conn = _get_conn()
    search = f'%{search_term}%'

    # Try to find a finished good matching the search
    product = conn.execute("""
        SELECT p.Id, p.SKU, p.CompanyId, c.Name as CompanyName
        FROM Product p
        JOIN Company c ON c.Id = p.CompanyId
        WHERE p.Type = 'finished-good'
          AND (p.SKU LIKE ? OR c.Name LIKE ?)
        LIMIT 1
    """, (search, search)).fetchone()

    if not product:
        conn.close()
        return {"error": f"No finished good found matching '{search_term}'."}

    bom = conn.execute(
        "SELECT Id FROM BOM WHERE ProducedProductId = ?", (product['Id'],)
    ).fetchone()

    if not bom:
        conn.close()
        return {"error": f"No BOM found for product {product['SKU']}."}

    # Get components
    components = conn.execute("""
        SELECT p.Id, p.SKU
        FROM BOM_Component bc
        JOIN Product p ON p.Id = bc.ConsumedProductId
        WHERE bc.BOMId = ?
        ORDER BY p.SKU
    """, (bom['Id'],)).fetchall()

    result_components = []
    for comp in components:
        bn = _base_name(comp['SKU'])
        # Get suppliers with ratings
        suppliers = conn.execute("""
            SELECT s.Id, s.Name FROM Supplier_Product sp
            JOIN Supplier s ON s.Id = sp.SupplierId
            WHERE sp.ProductId = ?
        """, (comp['Id'],)).fetchall()

        sup_details = []
        for s in suppliers:
            rating = conn.execute("""
                SELECT QualityScore, ComplianceScore, ReliabilityScore,
                       RiskTier, Certifications, LeadTimeDays
                FROM Supplier_Rating WHERE SupplierId = ?
            """, (s['Id'],)).fetchone()
            # Avg price for this supplier-product combo
            price_row = conn.execute("""
                SELECT AVG(UnitPrice) as avg_price, SUM(TotalCost) as total_spend,
                       COUNT(*) as order_count, AVG(OnTime)*100 as on_time_pct
                FROM Procurement_History
                WHERE SupplierId = ? AND ProductId = ?
            """, (s['Id'], comp['Id'])).fetchone()
            sd = {'id': s['Id'], 'name': s['Name']}
            if rating:
                sd['qualityScore'] = rating['QualityScore']
                sd['complianceScore'] = rating['ComplianceScore']
                sd['riskTier'] = rating['RiskTier']
                sd['certifications'] = rating['Certifications']
                sd['leadTimeDays'] = rating['LeadTimeDays']
            if price_row and price_row['avg_price']:
                sd['avgPrice'] = round(price_row['avg_price'], 2)
                sd['totalSpend'] = round(price_row['total_spend'], 2)
                sd['onTimeRate'] = round(price_row['on_time_pct'], 1)
            sup_details.append(sd)

        # Get market benchmark
        benchmark = conn.execute(
            "SELECT AvgMarketPrice FROM Price_Benchmark WHERE BaseName = ?",
            (bn,)
        ).fetchone()

        comp_entry = {
            'name': _humanize(bn),
            'baseName': bn,
            'sku': comp['SKU'],
            'supplierCount': len(suppliers),
            'suppliers': sup_details,
            'singleSource': len(suppliers) == 1,
        }
        if benchmark:
            comp_entry['marketPrice'] = benchmark['AvgMarketPrice']
        result_components.append(comp_entry)

    single_source_count = sum(1 for c in result_components if c['singleSource'])

    conn.close()
    return {
        "product": {
            "id": product['Id'],
            "sku": product['SKU'],
            "company": product['CompanyName'],
        },
        "componentCount": len(result_components),
        "singleSourceCount": single_source_count,
        "components": result_components,
    }


# ── Voice mode addendum ───────────────────────────────────────────

VOICE_ADDENDUM = """

=== VOICE CONVERSATION MODE ===
You are currently in a VOICE conversation. The user is speaking to you aloud and will hear your reply read aloud via text-to-speech. Adapt your behaviour:

RESPONSE STYLE:
- Keep replies SHORT and conversational — 2 to 4 sentences max for a normal answer.
- NEVER use markdown: no tables, no headers (#), no bullet lists, no bold/italic, no code blocks.
- Speak in natural conversational style as if you're having a face-to-face conversation.
- If the user speaks in a language other than English, reply in that same language.
- Use transition phrases: "So…", "Alright,", "Here's the thing…", "Good question —"
- Round numbers for speech: say "about 8 thousand orders" not "8,127 orders".
- For prices say "around 12 dollars per kilo" not "$12.34/kg".

LEADING QUESTIONS (CRITICAL):
- When your tool calls return a LOT of data (>5 items, multiple categories, complex results), do NOT dump it all at once.
- Instead, first give a brief summary (one sentence), then ASK the user what they want to dive into.
- Examples:
  "I found 12 suppliers for that ingredient, with prices ranging from 8 to 23 dollars per kilo. Want me to focus on the cheapest options, the highest quality ones, or give you the full picture?"
  "There are 3 single-source risks in that product's BOM. Should I go through each one, or just highlight the most critical?"
  "I can see cost savings opportunities across 5 ingredients. Want me to start with the biggest savings, or cover them all?"
- If the user says "all details" or "everything" or "tell me all", THEN give the full answer — but still conversationally, not as a data dump.
- If the query is simple (a count, a yes/no, a single fact), just answer directly — no need to ask.

PROCUREMENT & PRICING DATA (USE THESE):
- You HAVE access to real procurement data. Use execute_sql to query it.
- Supplier_Rating has quality scores, compliance scores, reliability scores, certifications, and risk tiers for ALL 40 suppliers.
- Procurement_History has ~8000 real orders with UnitPrice ($/kg), TotalCost, OnTime delivery flags, and QualityPassRate.
- Price_Benchmark has market reference prices (AvgMarketPrice, MinPrice, MaxPrice) per ingredient BaseName.
- For price comparisons: query Procurement_History grouped by SupplierId with AVG(UnitPrice) and compare against Price_Benchmark.
- For quality comparisons: query Supplier_Rating for scores, join with Procurement_History for AVG(QualityPassRate) and on-time rates.
- For cost savings: find ingredients where MAX(UnitPrice)/MIN(UnitPrice) across suppliers exceeds 1.15 (15% spread).
- ALWAYS query these tables when the user asks about prices, costs, quality, ratings, delivery, or supplier performance. Do not say "I don't have that data".

TRANSCRIPTION CONTEXT:
- The user's message comes from speech-to-text and may have transcription artifacts.
- A structured version of their intent is provided; use the "intent", "keywords", and "entities" fields to understand what they actually mean.
- If the raw transcription is ambiguous, use the structured intent. If still unclear, ask a short clarifying question.
"""


# ── Agent runner ──────────────────────────────────────────────────

TOOL_DISPATCH = {
    "execute_sql": lambda args: tool_execute_sql(args["query"]),
    "find_substitutes": lambda args: tool_find_substitutes(args["ingredient_name"]),
    "analyze_bom": lambda args: tool_analyze_bom(args["search_term"]),
    "source_product": lambda args: source_ingredients(args["ingredients"]),
}


def _tool_label(name, args):
    """Human-readable label for a tool call."""
    if name == "execute_sql":
        return f"SQL: {args.get('query', '')[:120]}"
    if name == "find_substitutes":
        return f"Finding substitutes for \"{args.get('ingredient_name', '')}\""
    if name == "analyze_bom":
        return f"Analyzing BOM for \"{args.get('search_term', '')}\""
    if name == "source_product":
        ings = args.get('ingredients', [])
        return f"Sourcing suppliers for {len(ings)} ingredient{'s' if len(ings) != 1 else ''}"
    return name


def run_agent(user_message, conversation_history=None, api_key=None, voice_mode=False):
    """
    Run the Agnes agent on a user message.
    Returns dict: {"reply": str, "steps": [{"tool", "args", "label", "result_preview"}]}
    """
    if not api_key:
        return {"reply": "Error: OpenAI API key not configured. Set OPENAI_API_KEY environment variable.", "steps": []}

    client = OpenAI(api_key=api_key)
    steps = []

    system = SYSTEM_PROMPT + VOICE_ADDENDUM if voice_mode else SYSTEM_PROMPT
    messages = [{"role": "system", "content": system}]
    if conversation_history:
        messages.extend(conversation_history)
    messages.append({"role": "user", "content": user_message})

    # Agentic loop: keep calling until we get a final text response
    max_iterations = 10
    for _ in range(max_iterations):
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=TOOLS,
            temperature=0.3,
        )

        choice = response.choices[0]

        if choice.finish_reason == "tool_calls":
            # Execute each tool call
            messages.append(choice.message)
            for tool_call in choice.message.tool_calls:
                fn_name = tool_call.function.name
                try:
                    fn_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    fn_args = {}

                if fn_name in TOOL_DISPATCH:
                    result = TOOL_DISPATCH[fn_name](fn_args)
                else:
                    result = {"error": f"Unknown tool: {fn_name}"}

                result_json = json.dumps(result, default=str)

                # Send full result to frontend for proper formatting
                steps.append({
                    "tool": fn_name,
                    "args": fn_args,
                    "label": _tool_label(fn_name, fn_args),
                    "result_preview": result_json,
                })

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_json,
                })
        else:
            # Final text response
            reply = choice.message.content or "I wasn't able to generate a response."
            return {"reply": reply, "steps": steps}

    return {"reply": "I reached the maximum number of reasoning steps. Please try a simpler question.", "steps": steps}
