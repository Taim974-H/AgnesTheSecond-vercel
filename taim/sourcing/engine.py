"""
Sourcing Engine
===============
Deterministic ingredient-to-supplier matching engine.
Given a list of ingredient names, fuzzy-matches them to the DB
and returns comprehensive supplier data per ingredient.
"""

import os
import re
import sqlite3
from difflib import SequenceMatcher

DB_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '../../hackathon-tumai/db.sqlite')
)


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _base_name(sku):
    if not sku:
        return ''
    cleaned = re.sub(r'^(FG|RM)-[A-Za-z0-9]+-', '', sku)
    cleaned = re.sub(r'-[0-9a-f]{6,}$', '', cleaned, flags=re.IGNORECASE)
    return cleaned.lower().strip('-')


def _humanize(name):
    return name.replace('-', ' ').strip().title()


# ── Build index of all known base-names ───────────────────────────

def _build_ingredient_index():
    """Return {base_name: [product_id, ...]} for all raw materials."""
    conn = _conn()
    rows = conn.execute(
        "SELECT Id, SKU FROM Product WHERE Type = 'raw-material'"
    ).fetchall()
    conn.close()

    index = {}
    for r in rows:
        bn = _base_name(r['SKU'])
        if bn:
            index.setdefault(bn, []).append(r['Id'])
    return index


def _fuzzy_match(query, candidates, threshold=0.45):
    """
    Match a user-provided ingredient name to known base-names.
    Returns list of (base_name, score) sorted by score descending.
    """
    q = query.lower().replace(' ', '-').strip('-')

    scored = []
    for bn in candidates:
        # Exact substring match
        if q in bn or bn in q:
            scored.append((bn, 1.0))
            continue
        # Token overlap
        q_tokens = set(q.split('-'))
        bn_tokens = set(bn.split('-'))
        if q_tokens & bn_tokens:
            jaccard = len(q_tokens & bn_tokens) / len(q_tokens | bn_tokens)
            if jaccard >= threshold:
                scored.append((bn, jaccard))
                continue
        # Sequence similarity fallback
        ratio = SequenceMatcher(None, q, bn).ratio()
        if ratio >= threshold:
            scored.append((bn, ratio))

    scored.sort(key=lambda x: -x[1])
    return scored


# ── Core: source ingredients ──────────────────────────────────────

def source_ingredients(ingredient_names):
    """
    Given a list of ingredient name strings (free-text),
    return a sourcing report per ingredient with supplier details.
    """
    index = _build_ingredient_index()
    conn = _conn()
    results = []
    supplier_coverage = {}
    single_source = []
    all_matched = 0

    for query in ingredient_names:
        query = query.strip()
        if not query:
            continue

        matches = _fuzzy_match(query, index.keys())

        if not matches:
            results.append({
                'query': query,
                'matched': False,
                'baseName': None,
                'name': query.title(),
                'matches': [],
                'suppliers': [],
                'benchmark': None,
                'companyCount': 0,
                'productCount': 0,
                'riskFlags': ['not_found_in_database'],
            })
            continue

        best_bn, best_score = matches[0]
        product_ids = index[best_bn]
        all_matched += 1

        # --- Suppliers with full details ---
        ph = ','.join('?' * len(product_ids))
        supplier_rows = conn.execute(f"""
            SELECT DISTINCT s.Id, s.Name, s.Email
            FROM Supplier_Product sp
            JOIN Supplier s ON s.Id = sp.SupplierId
            WHERE sp.ProductId IN ({ph})
        """, product_ids).fetchall()

        suppliers = []
        for s in supplier_rows:
            rating = conn.execute("""
                SELECT QualityScore, ComplianceScore, ReliabilityScore,
                       LeadTimeDays, MinOrderQty, Certifications,
                       LastAuditDate, RiskTier
                FROM Supplier_Rating WHERE SupplierId = ?
            """, (s['Id'],)).fetchone()

            proc = conn.execute(f"""
                SELECT AVG(UnitPrice) as avg_price,
                       MIN(UnitPrice) as min_price,
                       MAX(UnitPrice) as max_price,
                       SUM(TotalCost) as total_spend,
                       COUNT(*) as order_count,
                       AVG(CAST(OnTime AS FLOAT)) * 100 as on_time_pct,
                       AVG(QualityPassRate) as avg_quality_pass
                FROM Procurement_History
                WHERE SupplierId = ? AND ProductId IN ({ph})
            """, [s['Id']] + product_ids).fetchone()

            sd = {
                'id': s['Id'],
                'name': s['Name'],
                'email': s['Email'],
            }
            if rating:
                sd['qualityScore'] = round(rating['QualityScore'], 1)
                sd['complianceScore'] = round(rating['ComplianceScore'], 1)
                sd['reliabilityScore'] = round(rating['ReliabilityScore'], 1)
                sd['leadTimeDays'] = rating['LeadTimeDays']
                sd['minOrderQty'] = rating['MinOrderQty']
                sd['certifications'] = (
                    rating['Certifications'].split(',')
                    if rating['Certifications'] else []
                )
                sd['lastAuditDate'] = rating['LastAuditDate']
                sd['riskTier'] = rating['RiskTier']
            if proc and proc['avg_price']:
                sd['avgPrice'] = round(proc['avg_price'], 2)
                sd['minPrice'] = round(proc['min_price'], 2)
                sd['maxPrice'] = round(proc['max_price'], 2)
                sd['totalSpend'] = round(proc['total_spend'], 2)
                sd['orderCount'] = proc['order_count']
                sd['onTimePct'] = (
                    round(proc['on_time_pct'], 1)
                    if proc['on_time_pct'] else None
                )
                sd['avgQualityPass'] = (
                    round(proc['avg_quality_pass'], 1)
                    if proc['avg_quality_pass'] else None
                )

            suppliers.append(sd)

            supplier_coverage.setdefault(s['Name'], [])
            if _humanize(best_bn) not in supplier_coverage[s['Name']]:
                supplier_coverage[s['Name']].append(_humanize(best_bn))

        tier_order = {'low': 0, 'medium': 1, 'high': 2}
        suppliers.sort(key=lambda x: (
            tier_order.get(x.get('riskTier', 'high'), 3),
            x.get('avgPrice', 9999),
        ))

        # --- Benchmark ---
        bench = conn.execute(
            "SELECT AvgMarketPrice, MinPrice, MaxPrice, PriceVolatility, LastUpdated "
            "FROM Price_Benchmark WHERE BaseName = ?",
            (best_bn,)
        ).fetchone()
        benchmark = None
        if bench:
            benchmark = {
                'avgMarketPrice': bench['AvgMarketPrice'],
                'minPrice': bench['MinPrice'],
                'maxPrice': bench['MaxPrice'],
                'priceVolatility': bench['PriceVolatility'],
                'lastUpdated': bench['LastUpdated'],
            }

        comp_count = conn.execute(f"""
            SELECT COUNT(DISTINCT CompanyId) as cnt
            FROM Product WHERE Id IN ({ph})
        """, product_ids).fetchone()['cnt']

        flags = []
        if len(suppliers) == 1:
            flags.append('single_source')
            single_source.append(_humanize(best_bn))
        if len(suppliers) == 0:
            flags.append('no_suppliers')
        if bench and bench['PriceVolatility'] and bench['PriceVolatility'] > 0.25:
            flags.append('high_price_volatility')
        if any(s.get('riskTier') == 'high' for s in suppliers):
            flags.append('has_high_risk_supplier')

        other_matches = [
            {'baseName': bn, 'name': _humanize(bn), 'score': round(sc, 2)}
            for bn, sc in matches[1:6]
        ]

        results.append({
            'query': query,
            'matched': True,
            'matchScore': round(best_score, 2),
            'baseName': best_bn,
            'name': _humanize(best_bn),
            'matches': other_matches,
            'suppliers': suppliers,
            'benchmark': benchmark,
            'companyCount': comp_count,
            'productCount': len(product_ids),
            'riskFlags': flags,
        })

    conn.close()

    # ── Aggregate supplier ranking ────────────────────────────────
    # For each supplier that appeared, compute coverage metrics
    matched_names = [r['name'] for r in results if r['matched']]
    total_matched = len(matched_names)

    ranked_suppliers = {}
    for ing in results:
        if not ing['matched']:
            continue
        for s in ing.get('suppliers', []):
            sid = s['id']
            if sid not in ranked_suppliers:
                ranked_suppliers[sid] = {
                    'id': sid,
                    'name': s['name'],
                    'email': s.get('email'),
                    'riskTier': s.get('riskTier'),
                    'qualityScore': s.get('qualityScore'),
                    'complianceScore': s.get('complianceScore'),
                    'reliabilityScore': s.get('reliabilityScore'),
                    'leadTimeDays': s.get('leadTimeDays'),
                    'certifications': s.get('certifications', []),
                    'covers': [],
                    'avgPrices': {},
                }
            ranked_suppliers[sid]['covers'].append(ing['name'])
            if s.get('avgPrice') is not None:
                ranked_suppliers[sid]['avgPrices'][ing['name']] = s['avgPrice']

    # Sort by coverage count desc, then quality desc
    ranked_list = sorted(
        ranked_suppliers.values(),
        key=lambda x: (-len(x['covers']), -(x.get('qualityScore') or 0)),
    )
    for s in ranked_list:
        s['coverageCount'] = len(s['covers'])
        s['coveragePct'] = round(len(s['covers']) / total_matched * 100, 1) if total_matched else 0

    # ── Optimal supplier combinations ─────────────────────────────
    # Greedy set-cover: pick supplier covering most uncovered ingredients
    combos = _find_supplier_combos(ranked_list, matched_names)

    unmatched = len(results) - all_matched
    summary = {
        'total': len(results),
        'matched': all_matched,
        'unmatched': unmatched,
        'supplierCoverage': supplier_coverage,
        'singleSourceIngredients': list(set(single_source)),
        'rankedSuppliers': ranked_list,
        'optimalCombos': combos,
    }

    return {'ingredients': results, 'summary': summary}


def _find_supplier_combos(ranked_suppliers, all_ingredients):
    """
    Find up to 3 good supplier combinations that cover all matched ingredients.
    Uses greedy set-cover with different starting priorities.
    """
    if not all_ingredients or not ranked_suppliers:
        return []

    all_set = set(all_ingredients)
    combos = []

    # Strategy 1: Greedy by coverage (most ingredients first)
    combo1 = _greedy_cover(ranked_suppliers, all_set, key=lambda s: -len(s['covers']))
    if combo1:
        combos.append({
            'strategy': 'Maximum Coverage',
            'description': 'Fewest suppliers to cover all ingredients',
            'suppliers': combo1,
            'supplierCount': len(combo1),
        })

    # Strategy 2: Greedy by quality (highest quality first)
    combo2 = _greedy_cover(ranked_suppliers, all_set, key=lambda s: -(s.get('qualityScore') or 0))
    if combo2 and [s['name'] for s in combo2] != [s['name'] for s in (combo1 or [])]:
        combos.append({
            'strategy': 'Highest Quality',
            'description': 'Prioritizes suppliers with best quality scores',
            'suppliers': combo2,
            'supplierCount': len(combo2),
        })

    # Strategy 3: Greedy by lowest price
    def avg_price_key(s):
        prices = list(s.get('avgPrices', {}).values())
        return sum(prices) / len(prices) if prices else 9999
    combo3 = _greedy_cover(ranked_suppliers, all_set, key=avg_price_key)
    if combo3 and [s['name'] for s in combo3] != [s['name'] for s in (combo1 or [])]:
        combos.append({
            'strategy': 'Lowest Cost',
            'description': 'Prioritizes suppliers with lowest average prices',
            'suppliers': combo3,
            'supplierCount': len(combo3),
        })

    return combos


def _greedy_cover(suppliers, all_ingredients, key):
    """Greedy set-cover: pick supplier covering most uncovered, break ties by key."""
    remaining = set(all_ingredients)
    chosen = []
    available = list(suppliers)

    while remaining and available:
        # Filter to those that cover at least one remaining ingredient
        useful = [s for s in available if set(s['covers']) & remaining]
        if not useful:
            break
        # Sort by how many remaining they cover (desc), then by the key
        useful.sort(key=lambda s: (-len(set(s['covers']) & remaining), key(s)))
        pick = useful[0]
        covered = set(pick['covers']) & remaining
        chosen.append({
            'name': pick['name'],
            'id': pick['id'],
            'riskTier': pick.get('riskTier'),
            'qualityScore': pick.get('qualityScore'),
            'covers': sorted(covered),
            'coverageCount': len(covered),
        })
        remaining -= covered
        available.remove(pick)

    # Note uncovered ingredients
    if remaining:
        for combo in chosen:
            pass  # still return partial
    return chosen if chosen else None


def get_all_known_ingredients():
    """Return list of all known ingredient base-names for autocomplete."""
    index = _build_ingredient_index()
    return sorted([
        {'baseName': bn, 'name': _humanize(bn), 'productCount': len(pids)}
        for bn, pids in index.items()
    ], key=lambda x: x['name'])
