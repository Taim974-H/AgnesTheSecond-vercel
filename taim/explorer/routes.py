"""
Explorer – Flask Blueprint
============================
All data-explorer API endpoints: tables, overview, graph, ingredients, suppliers.
"""

from flask import Blueprint, jsonify, request, send_from_directory
from sqlalchemy import create_engine, inspect, text
import os
import re

explorer_bp = Blueprint('explorer', __name__, url_prefix='/explorer')

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../hackathon-tumai/db.sqlite'))
engine = create_engine(f'sqlite:///{DB_PATH}')

TABLES = {
    'BOM',
    'BOM_Component',
    'Company',
    'Product',
    'Supplier',
    'Supplier_Product',
    'Supplier_Rating',
    'Procurement_History',
    'Price_Benchmark',
}


def humanize_sku(sku):
    """Turn 'RM-C2-soy-lecithin-cc38c49d' into 'Soy Lecithin'."""
    if not sku:
        return sku
    cleaned = re.sub(r'^(FG|RM)-[A-Za-z0-9]+-', '', sku)
    cleaned = re.sub(r'-[0-9a-f]{6,}$', '', cleaned)
    cleaned = cleaned.replace('-', ' ').strip()
    return cleaned.title() if cleaned else sku


def fetch_all(query, params=None):
    with engine.connect() as conn:
        result = conn.execute(text(query), params or {})
        return [dict(row) for row in result.mappings().all()]


def fetch_one(query, params=None):
    with engine.connect() as conn:
        result = conn.execute(text(query), params or {})
        row = result.mappings().first()
        return dict(row) if row else None


def _base_name(sku):
    """Extract the canonical ingredient name from an SKU for grouping."""
    if not sku:
        return ''
    cleaned = re.sub(r'^(FG|RM)-[A-Za-z0-9]+-', '', sku)
    cleaned = re.sub(r'-[0-9a-f]{6,}$', '', cleaned)
    return cleaned.lower()


# ── Serve UI ──────────────────────────────────────────────────────

@explorer_bp.route('/')
def serve_index():
    return send_from_directory(os.path.dirname(__file__), 'index.html')


# ── Tables ────────────────────────────────────────────────────────

@explorer_bp.route('/api/tables')
def get_tables():
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    return jsonify({'tables': tables})


@explorer_bp.route('/api/table/<table_name>')
def get_table_data(table_name):
    if table_name not in TABLES:
        return jsonify({'error': 'Unknown table'}), 404

    limit = min(max(request.args.get('limit', default=25, type=int), 1), 100)
    offset = max(request.args.get('offset', default=0, type=int), 0)

    inspector = inspect(engine)
    columns = [col['name'] for col in inspector.get_columns(table_name)]

    where_clauses = []
    params = {'limit': limit, 'offset': offset}
    for col in columns:
        val = request.args.get(f'filter_{col}', '').strip()
        if val:
            safe_key = f'f_{col}'
            where_clauses.append(f'CAST("{col}" AS TEXT) LIKE :{safe_key}')
            params[safe_key] = f'%{val}%'

    where_sql = (' WHERE ' + ' AND '.join(where_clauses)) if where_clauses else ''

    total = fetch_one(f'SELECT COUNT(*) AS count FROM "{table_name}"{where_sql}', params)['count']
    rows = fetch_all(
        f'SELECT * FROM "{table_name}"{where_sql} LIMIT :limit OFFSET :offset',
        params,
    )

    return jsonify({
        'columns': columns,
        'rows': rows,
        'pagination': {
            'limit': limit,
            'offset': offset,
            'total': total,
        },
    })


# ── Overview ──────────────────────────────────────────────────────

@explorer_bp.route('/api/overview')
def get_overview():
    counts = fetch_one(
        """
        SELECT
            (SELECT COUNT(*) FROM Company) AS company_count,
            (SELECT COUNT(*) FROM Product) AS product_count,
            (SELECT COUNT(*) FROM Product WHERE Type = 'finished-good') AS finished_good_count,
            (SELECT COUNT(*) FROM Product WHERE Type = 'raw-material') AS raw_material_count,
            (SELECT COUNT(*) FROM BOM) AS bom_count,
            (SELECT COUNT(*) FROM BOM_Component) AS bom_component_count,
            (SELECT COUNT(*) FROM Supplier) AS supplier_count,
            (SELECT COUNT(*) FROM Supplier_Product) AS supplier_product_count,
            (
                SELECT COUNT(*)
                FROM Product p
                WHERE p.Type = 'finished-good'
                  AND NOT EXISTS (SELECT 1 FROM BOM b WHERE b.ProducedProductId = p.Id)
            ) AS finished_goods_without_bom,
            (
                SELECT COUNT(*)
                FROM Product p
                WHERE p.Type = 'raw-material'
                  AND NOT EXISTS (SELECT 1 FROM Supplier_Product sp WHERE sp.ProductId = p.Id)
            ) AS raw_materials_without_supplier,
            (
                SELECT COUNT(*)
                FROM Supplier_Product sp
                JOIN Product p ON p.Id = sp.ProductId
                WHERE p.Type <> 'raw-material'
            ) AS supplier_links_to_non_raw,
            (
                SELECT COUNT(*)
                FROM (
                    SELECT BOMId, COUNT(*) AS component_count
                    FROM BOM_Component
                    GROUP BY BOMId
                    HAVING COUNT(*) < 2
                ) short_boms
            ) AS boms_with_too_few_components
        """
    )

    top_suppliers = fetch_all(
        """
        SELECT s.Name, COUNT(*) AS raw_material_count
        FROM Supplier_Product sp
        JOIN Supplier s ON s.Id = sp.SupplierId
        GROUP BY s.Id, s.Name
        ORDER BY raw_material_count DESC, s.Name ASC
        LIMIT 8
        """
    )

    top_companies = fetch_all(
        """
        SELECT c.Name, COUNT(*) AS finished_good_count
        FROM Product p
        JOIN Company c ON c.Id = p.CompanyId
        WHERE p.Type = 'finished-good'
        GROUP BY c.Id, c.Name
        ORDER BY finished_good_count DESC, c.Name ASC
        LIMIT 8
        """
    )

    return jsonify({
        'counts': counts,
        'constraints': [
            {
                'label': 'Every finished good has a BOM',
                'violations': counts['finished_goods_without_bom'],
            },
            {
                'label': 'Every raw material has at least one supplier',
                'violations': counts['raw_materials_without_supplier'],
            },
            {
                'label': 'Supplier links only point to raw materials',
                'violations': counts['supplier_links_to_non_raw'],
            },
            {
                'label': 'Every BOM has at least two components',
                'violations': counts['boms_with_too_few_components'],
            },
        ],
        'top_suppliers': top_suppliers,
        'top_companies': top_companies,
    })


# ── Graph ─────────────────────────────────────────────────────────

@explorer_bp.route('/api/graph')
def get_graph():
    company_id = request.args.get('companyId', type=int)
    product_id = request.args.get('productId', type=int)

    if product_id is None:
        seed = fetch_one(
            """
            SELECT p.Id AS product_id
            FROM Product p
            WHERE p.Type = 'finished-good'
              AND (:company_id IS NULL OR p.CompanyId = :company_id)
            ORDER BY p.Id ASC
            LIMIT 1
            """,
            {'company_id': company_id},
        )
        if not seed:
            return jsonify({'nodes': [], 'edges': [], 'focus': None})
        product_id = seed['product_id']

    product = fetch_one(
        """
        SELECT p.Id, p.SKU, p.CompanyId, p.Type, c.Name AS CompanyName
        FROM Product p
        JOIN Company c ON c.Id = p.CompanyId
        WHERE p.Id = :product_id
        """,
        {'product_id': product_id},
    )
    if not product:
        return jsonify({'error': 'Unknown product'}), 404

    bom = fetch_one(
        'SELECT Id, ProducedProductId FROM BOM WHERE ProducedProductId = :product_id',
        {'product_id': product_id},
    )
    components = fetch_all(
        """
        SELECT p.Id, p.SKU, p.Type
        FROM BOM_Component bc
        JOIN Product p ON p.Id = bc.ConsumedProductId
        WHERE bc.BOMId = :bom_id
        ORDER BY p.SKU ASC
        """,
        {'bom_id': bom['Id']},
    ) if bom else []

    supplier_links = fetch_all(
        """
        SELECT DISTINCT sp.ProductId, s.Id AS SupplierId, s.Name AS SupplierName
        FROM BOM_Component bc
        JOIN Supplier_Product sp ON sp.ProductId = bc.ConsumedProductId
        JOIN Supplier s ON s.Id = sp.SupplierId
        WHERE bc.BOMId = :bom_id
        ORDER BY s.Name ASC
        """,
        {'bom_id': bom['Id']},
    ) if components else []

    supplier_count_by_component = {}
    suppliers_by_component = {}
    for link in supplier_links:
        product_key = link['ProductId']
        supplier_count_by_component[product_key] = supplier_count_by_component.get(product_key, 0) + 1
        suppliers_by_component.setdefault(product_key, []).append(link)

    nodes = [
        {
            'id': f"company-{product['CompanyId']}",
            'label': product['CompanyName'],
            'type': 'company',
            'subtitle': 'Brand owner',
        },
        {
            'id': f"product-{product['Id']}",
            'label': humanize_sku(product['SKU']),
            'type': 'product',
            'subtitle': product['SKU'],
        },
    ]
    edges = [
        {
            'from': f"company-{product['CompanyId']}",
            'to': f"product-{product['Id']}",
            'label': 'owns',
        }
    ]

    if bom:
        nodes.append({
            'id': f"bom-{bom['Id']}",
            'label': f"BOM {bom['Id']}",
            'type': 'bom',
            'subtitle': f"{len(components)} components",
        })
        edges.append({
            'from': f"product-{product['Id']}",
            'to': f"bom-{bom['Id']}",
            'label': 'defined by',
        })

    for component in components:
        component_id = component['Id']
        nodes.append({
            'id': f'component-{component_id}',
            'label': humanize_sku(component['SKU']),
            'type': 'component',
            'subtitle': f"{supplier_count_by_component.get(component_id, 0)} suppliers",
        })
        edges.append({
            'from': f"bom-{bom['Id']}",
            'to': f'component-{component_id}',
            'label': 'uses',
        })

        component_suppliers = suppliers_by_component.get(component_id, [])[:5]
        for supplier in component_suppliers:
            supplier_node_id = f"supplier-{supplier['SupplierId']}"
            if not any(node['id'] == supplier_node_id for node in nodes):
                nodes.append({
                    'id': supplier_node_id,
                    'label': supplier['SupplierName'],
                    'type': 'supplier',
                    'subtitle': 'Can supply raw material',
                })
            edges.append({
                'from': supplier_node_id,
                'to': f'component-{component_id}',
                'label': 'supplies',
            })

    return jsonify({
        'focus': {
            'productId': product['Id'],
            'companyId': product['CompanyId'],
            'productLabel': product['SKU'],
            'companyLabel': product['CompanyName'],
        },
        'nodes': nodes,
        'edges': edges,
        'componentTable': [
            {
                'name': humanize_sku(component['SKU']),
                'sku': component['SKU'],
                'supplierCount': supplier_count_by_component.get(component['Id'], 0),
            }
            for component in components
        ],
    })


# ── Options ───────────────────────────────────────────────────────

@explorer_bp.route('/api/options')
def get_options():
    companies = fetch_all(
        """
        SELECT c.Id, c.Name, COUNT(*) AS finishedGoodCount
        FROM Company c
        JOIN Product p ON p.CompanyId = c.Id
        WHERE p.Type = 'finished-good'
        GROUP BY c.Id, c.Name
        ORDER BY c.Name ASC
        """
    )
    products = fetch_all(
        """
        SELECT p.Id, p.SKU, c.Name AS CompanyName
        FROM Product p
        JOIN Company c ON c.Id = p.CompanyId
        WHERE p.Type = 'finished-good'
        ORDER BY c.Name ASC, p.SKU ASC
        LIMIT 250
        """
    )
    return jsonify({'companies': companies, 'products': products})


# ── Ingredients ───────────────────────────────────────────────────

@explorer_bp.route('/api/ingredients')
def get_ingredients():
    """Return ingredients grouped by base name, with supplier/company/product counts."""
    min_suppliers = request.args.get('minSuppliers', default=2, type=int)
    min_companies = request.args.get('minCompanies', default=1, type=int)
    single_source = request.args.get('singleSource', default='', type=str) == '1'

    rows = fetch_all(
        """
        SELECT
            p.Id AS product_id,
            p.SKU AS sku,
            p.CompanyId AS company_id,
            c.Name AS company_name,
            s.Id AS supplier_id,
            s.Name AS supplier_name
        FROM Product p
        JOIN Supplier_Product sp ON sp.ProductId = p.Id
        JOIN Supplier s ON s.Id = sp.SupplierId
        JOIN Company c ON c.Id = p.CompanyId
        WHERE p.Type = 'raw-material'
        ORDER BY p.SKU
        """
    )

    product_supplier_counts = {}
    for r in rows:
        pid = r['product_id']
        product_supplier_counts[pid] = product_supplier_counts.get(pid, 0) + 1

    groups = {}
    for r in rows:
        bn = _base_name(r['sku'])
        if bn not in groups:
            groups[bn] = {
                'product_ids': set(),
                'suppliers': {},
                'companies': {},
            }
        g = groups[bn]
        g['product_ids'].add(r['product_id'])
        g['suppliers'][r['supplier_id']] = r['supplier_name']
        g['companies'][r['company_id']] = r['company_name']

    result = []
    for bn, g in groups.items():
        sup_count = len(g['suppliers'])
        co_count = len(g['companies'])
        if sup_count < min_suppliers:
            continue
        if co_count < min_companies:
            continue
        total_suppliers = sup_count
        single_source_count = sum(
            1 for pid in g['product_ids']
            if product_supplier_counts.get(pid, 0) == 1 and total_suppliers > 1
        )
        if single_source and single_source_count == 0:
            continue
        result.append({
            'baseName': bn,
            'name': humanize_sku('RM-X-' + bn + '-000000'),
            'supplierCount': sup_count,
            'companyCount': co_count,
            'productIds': sorted(g['product_ids']),
            'singleSourceProducts': single_source_count,
        })
    result.sort(key=lambda x: (-x['companyCount'], -x['supplierCount'], x['name']))
    return jsonify({'ingredients': result})


@explorer_bp.route('/api/ingredient/<path:base_name>')
def get_ingredient_detail(base_name):
    """Return full detail for an ingredient identified by its base name."""
    all_rm = fetch_all(
        "SELECT Id, SKU, CompanyId FROM Product WHERE Type = 'raw-material'"
    )
    matching_ids = [r['Id'] for r in all_rm if _base_name(r['SKU']) == base_name]
    if not matching_ids:
        return jsonify({'error': 'Unknown ingredient'}), 404

    placeholders = ','.join(str(i) for i in matching_ids)

    suppliers = fetch_all(f"""
        SELECT DISTINCT s.Id, s.Name
        FROM Supplier_Product sp
        JOIN Supplier s ON s.Id = sp.SupplierId
        WHERE sp.ProductId IN ({placeholders})
        ORDER BY s.Name ASC
    """)

    supplier_map_rows = fetch_all(f"""
        SELECT sp.ProductId, s.Id AS supplier_id, s.Name AS supplier_name
        FROM Supplier_Product sp
        JOIN Supplier s ON s.Id = sp.SupplierId
        WHERE sp.ProductId IN ({placeholders})
        ORDER BY s.Name ASC
    """)
    rm_suppliers = {}
    for row in supplier_map_rows:
        rm_suppliers.setdefault(row['ProductId'], []).append({
            'id': row['supplier_id'],
            'name': row['supplier_name'],
        })

    usages = fetch_all(f"""
        SELECT DISTINCT
            c.Id   AS company_id,
            c.Name AS company_name,
            fg.Id  AS product_id,
            fg.SKU AS product_sku,
            b.Id   AS bom_id,
            bc.ConsumedProductId AS rm_product_id
        FROM BOM_Component bc
        JOIN BOM b  ON b.Id = bc.BOMId
        JOIN Product fg ON fg.Id = b.ProducedProductId
        JOIN Company c  ON c.Id = fg.CompanyId
        WHERE bc.ConsumedProductId IN ({placeholders})
        ORDER BY c.Name ASC, fg.SKU ASC
    """)

    companies = {}
    for u in usages:
        cid = u['company_id']
        if cid not in companies:
            companies[cid] = {'id': cid, 'name': u['company_name'], 'products': []}
        companies[cid]['products'].append({
            'productId': u['product_id'],
            'productName': humanize_sku(u['product_sku']),
            'productSku': u['product_sku'],
            'bomId': u['bom_id'],
            'suppliers': rm_suppliers.get(u['rm_product_id'], []),
        })

    display_name = humanize_sku('RM-X-' + base_name + '-000000')

    # Fetch supplier ratings for all suppliers of this ingredient
    supplier_ids = [s['Id'] for s in suppliers]
    supplier_ratings = {}
    if supplier_ids:
        sr_ph = ','.join(str(i) for i in supplier_ids)
        sr_rows = fetch_all(f"""
            SELECT SupplierId, QualityScore, ComplianceScore, ReliabilityScore,
                   LeadTimeDays, MinOrderQty, Certifications, RiskTier, LastAuditDate
            FROM Supplier_Rating WHERE SupplierId IN ({sr_ph})
        """)
        for sr in sr_rows:
            supplier_ratings[sr['SupplierId']] = {
                'qualityScore': sr['QualityScore'],
                'complianceScore': sr['ComplianceScore'],
                'reliabilityScore': sr['ReliabilityScore'],
                'leadTimeDays': sr['LeadTimeDays'],
                'minOrderQty': sr['MinOrderQty'],
                'certifications': (sr['Certifications'] or '').split(','),
                'riskTier': sr['RiskTier'],
                'lastAuditDate': sr['LastAuditDate'],
            }

    # Fetch price benchmark
    benchmark = fetch_one(
        "SELECT AvgMarketPrice, MinPrice, MaxPrice, PriceVolatility FROM Price_Benchmark WHERE BaseName = :bn",
        {'bn': base_name}
    )

    # Fetch procurement summary per supplier
    procurement_by_supplier = {}
    if supplier_ids and matching_ids:
        sp_ph = ','.join(str(i) for i in supplier_ids)
        mp_ph = ','.join(str(i) for i in matching_ids)
        proc_rows = fetch_all(f"""
            SELECT SupplierId,
                   COUNT(*) AS orderCount,
                   ROUND(AVG(UnitPrice), 2) AS avgPrice,
                   ROUND(MIN(UnitPrice), 2) AS minPrice,
                   ROUND(MAX(UnitPrice), 2) AS maxPrice,
                   ROUND(SUM(TotalCost), 0) AS totalSpend,
                   ROUND(AVG(OnTime) * 100, 1) AS onTimeRate,
                   ROUND(AVG(QualityPassRate), 1) AS avgQualityPass
            FROM Procurement_History
            WHERE SupplierId IN ({sp_ph}) AND ProductId IN ({mp_ph})
            GROUP BY SupplierId
        """)
        for pr in proc_rows:
            procurement_by_supplier[pr['SupplierId']] = {
                'orderCount': pr['orderCount'],
                'avgPrice': pr['avgPrice'],
                'minPrice': pr['minPrice'],
                'maxPrice': pr['maxPrice'],
                'totalSpend': pr['totalSpend'],
                'onTimeRate': pr['onTimeRate'],
                'avgQualityPass': pr['avgQualityPass'],
            }

    # Enrich supplier list with ratings and procurement data
    enriched_suppliers = []
    for s in suppliers:
        entry = {'Id': s['Id'], 'Name': s['Name']}
        if s['Id'] in supplier_ratings:
            entry['rating'] = supplier_ratings[s['Id']]
        if s['Id'] in procurement_by_supplier:
            entry['procurement'] = procurement_by_supplier[s['Id']]
        enriched_suppliers.append(entry)

    return jsonify({
        'ingredient': {
            'baseName': base_name,
            'name': display_name,
            'variantCount': len(matching_ids),
        },
        'suppliers': enriched_suppliers,
        'companies': list(companies.values()),
        'benchmark': {
            'avgMarketPrice': benchmark['AvgMarketPrice'],
            'minPrice': benchmark['MinPrice'],
            'maxPrice': benchmark['MaxPrice'],
            'priceVolatility': benchmark['PriceVolatility'],
        } if benchmark else None,
    })


# ── Suppliers Explorer ────────────────────────────────────────────

@explorer_bp.route('/api/suppliers')
def get_suppliers():
    """Return all suppliers with counts of ingredients, products, and companies."""
    min_ingredients = request.args.get('minIngredients', default=1, type=int)
    min_companies = request.args.get('minCompanies', default=1, type=int)
    sole_only = request.args.get('soleSupplier', default='', type=str) == '1'

    rows = fetch_all("""
        SELECT
            s.Id   AS supplier_id,
            s.Name AS supplier_name,
            p.Id   AS product_id,
            p.SKU  AS sku,
            p.CompanyId AS company_id
        FROM Supplier s
        JOIN Supplier_Product sp ON sp.SupplierId = s.Id
        JOIN Product p ON p.Id = sp.ProductId
        WHERE p.Type = 'raw-material'
        ORDER BY s.Name
    """)

    product_sup_counts = {}
    for r in rows:
        pid = r['product_id']
        product_sup_counts[pid] = product_sup_counts.get(pid, 0) + 1

    groups = {}
    for r in rows:
        sid = r['supplier_id']
        if sid not in groups:
            groups[sid] = {
                'id': sid,
                'name': r['supplier_name'],
                'ingredients': set(),
                'product_ids': set(),
                'company_ids': set(),
                'sole_product_ids': set(),
            }
        g = groups[sid]
        g['product_ids'].add(r['product_id'])
        g['company_ids'].add(r['company_id'])
        bn = _base_name(r['sku'])
        if bn:
            g['ingredients'].add(bn)
        if product_sup_counts.get(r['product_id'], 0) == 1:
            g['sole_product_ids'].add(r['product_id'])

    result = []
    for g in groups.values():
        ic = len(g['ingredients'])
        cc = len(g['company_ids'])
        sole_count = len(g['sole_product_ids'])
        if ic < min_ingredients or cc < min_companies:
            continue
        if sole_only and sole_count == 0:
            continue
        result.append({
            'id': g['id'],
            'name': g['name'],
            'ingredientCount': ic,
            'ingredientNames': [humanize_sku('RM-X-' + bn + '-000000') for bn in sorted(g['ingredients'])],
            'productCount': len(g['product_ids']),
            'companyCount': cc,
            'soleProductCount': sole_count,
        })
    result.sort(key=lambda x: (-x['companyCount'], -x['ingredientCount'], x['name']))
    return jsonify({'suppliers': result})


@explorer_bp.route('/api/supplier/<int:supplier_id>')
def get_supplier_detail(supplier_id):
    """Return full detail for a supplier: ingredients, companies, products."""
    supplier = fetch_one(
        "SELECT Id, Name FROM Supplier WHERE Id = :id", {'id': supplier_id}
    )
    if not supplier:
        return jsonify({'error': 'Unknown supplier'}), 404

    rows = fetch_all("""
        SELECT
            p.Id AS product_id,
            p.SKU AS sku,
            p.CompanyId AS company_id,
            c.Name AS company_name
        FROM Supplier_Product sp
        JOIN Product p ON p.Id = sp.ProductId
        JOIN Company c ON c.Id = p.CompanyId
        WHERE sp.SupplierId = :sid AND p.Type = 'raw-material'
        ORDER BY c.Name, p.SKU
    """, {'sid': supplier_id})

    product_ids = [r['product_id'] for r in rows]
    competitor_map = {}
    if product_ids:
        placeholders = ','.join(str(i) for i in product_ids)
        comp_rows = fetch_all(f"""
            SELECT sp.ProductId, s.Id AS sid, s.Name AS sname
            FROM Supplier_Product sp
            JOIN Supplier s ON s.Id = sp.SupplierId
            WHERE sp.ProductId IN ({placeholders}) AND sp.SupplierId != :sid
        """, {'sid': supplier_id})
        for cr in comp_rows:
            competitor_map.setdefault(cr['ProductId'], []).append({
                'id': cr['sid'], 'name': cr['sname'],
            })

    all_rm = fetch_all(
        "SELECT Id, SKU FROM Product WHERE Type = 'raw-material'"
    )
    base_name_to_ids = {}
    for r in all_rm:
        bn = _base_name(r['SKU'])
        if bn:
            base_name_to_ids.setdefault(bn, []).append(r['Id'])

    ingredients = {}
    for r in rows:
        bn = _base_name(r['sku'])
        if not bn:
            continue
        if bn not in ingredients:
            all_ids_for_ing = base_name_to_ids.get(bn, [])
            alt_suppliers = []
            if all_ids_for_ing:
                ph = ','.join(str(i) for i in all_ids_for_ing)
                alt_suppliers = fetch_all(f"""
                    SELECT DISTINCT s.Id, s.Name
                    FROM Supplier_Product sp
                    JOIN Supplier s ON s.Id = sp.SupplierId
                    WHERE sp.ProductId IN ({ph}) AND s.Id != :sid
                    ORDER BY s.Name
                """, {'sid': supplier_id})
            ingredients[bn] = {
                'baseName': bn,
                'name': humanize_sku('RM-X-' + bn + '-000000'),
                'companies': {},
                'alternativeSuppliers': [{'id': s['Id'], 'name': s['Name']} for s in alt_suppliers],
            }
        ing = ingredients[bn]
        cid = r['company_id']
        if cid not in ing['companies']:
            ing['companies'][cid] = {
                'id': cid,
                'name': r['company_name'],
                'products': [],
            }
        competitors = competitor_map.get(r['product_id'], [])
        sole_supplier = len(competitors) == 0
        ing['companies'][cid]['products'].append({
            'productId': r['product_id'],
            'productSku': r['sku'],
            'soleSupplier': sole_supplier,
            'competitors': competitors,
        })

    ing_list = []
    for ing in ingredients.values():
        companies = list(ing['companies'].values())
        total_products = sum(len(c['products']) for c in companies)
        sole_count = sum(
            1 for c in companies for p in c['products'] if p['soleSupplier']
        )
        ing_list.append({
            'baseName': ing['baseName'],
            'name': ing['name'],
            'companyCount': len(companies),
            'productCount': total_products,
            'soleSupplierCount': sole_count,
            'companies': companies,
            'alternativeSuppliers': ing['alternativeSuppliers'],
        })
    ing_list.sort(key=lambda x: (-x['companyCount'], -x['productCount'], x['name']))

    # Fetch supplier rating
    rating = fetch_one(
        """SELECT QualityScore, ComplianceScore, ReliabilityScore,
                  LeadTimeDays, MinOrderQty, Certifications, RiskTier, LastAuditDate
           FROM Supplier_Rating WHERE SupplierId = :sid""",
        {'sid': supplier_id}
    )
    rating_data = None
    if rating:
        rating_data = {
            'qualityScore': rating['QualityScore'],
            'complianceScore': rating['ComplianceScore'],
            'reliabilityScore': rating['ReliabilityScore'],
            'leadTimeDays': rating['LeadTimeDays'],
            'minOrderQty': rating['MinOrderQty'],
            'certifications': [c.strip() for c in (rating['Certifications'] or '').split(',') if c.strip()],
            'riskTier': rating['RiskTier'],
            'lastAuditDate': rating['LastAuditDate'],
        }

    # Fetch procurement summary
    proc_summary = fetch_one(
        """SELECT COUNT(*) AS orderCount,
                  ROUND(SUM(TotalCost), 0) AS totalSpend,
                  ROUND(AVG(OnTime) * 100, 1) AS onTimeRate,
                  ROUND(AVG(QualityPassRate), 1) AS avgQualityPass
           FROM Procurement_History WHERE SupplierId = :sid""",
        {'sid': supplier_id}
    )
    procurement_data = None
    if proc_summary and proc_summary['orderCount'] > 0:
        procurement_data = {
            'orderCount': proc_summary['orderCount'],
            'totalSpend': proc_summary['totalSpend'],
            'onTimeRate': proc_summary['onTimeRate'],
            'avgQualityPass': proc_summary['avgQualityPass'],
        }

    return jsonify({
        'supplier': supplier,
        'ingredientCount': len(ing_list),
        'companyCount': len(set(r['company_id'] for r in rows)),
        'productCount': len(product_ids),
        'ingredients': ing_list,
        'rating': rating_data,
        'procurement': procurement_data,
    })


# ── Procurement / Cost Savings ────────────────────────────────────

@explorer_bp.route('/api/procurement/overview')
def get_procurement_overview():
    """Return procurement summary stats."""
    summary = fetch_one("""
        SELECT COUNT(*) AS orderCount,
               ROUND(SUM(TotalCost), 0) AS totalSpend,
               ROUND(AVG(UnitPrice), 2) AS avgUnitPrice,
               COUNT(DISTINCT SupplierId) AS supplierCount,
               COUNT(DISTINCT ProductId) AS ingredientCount,
               COUNT(DISTINCT CompanyId) AS companyCount,
               ROUND(AVG(OnTime) * 100, 1) AS avgOnTimeRate,
               ROUND(AVG(QualityPassRate), 1) AS avgQualityPass,
               MIN(OrderDate) AS firstOrder,
               MAX(OrderDate) AS lastOrder
        FROM Procurement_History
    """)
    risk_dist = fetch_all("""
        SELECT RiskTier, COUNT(*) AS count
        FROM Supplier_Rating GROUP BY RiskTier ORDER BY count DESC
    """)
    top_spend_suppliers = fetch_all("""
        SELECT s.Name, ROUND(SUM(ph.TotalCost), 0) AS totalSpend,
               COUNT(*) AS orderCount,
               ROUND(AVG(ph.OnTime) * 100, 1) AS onTimeRate,
               sr.QualityScore, sr.ComplianceScore, sr.RiskTier
        FROM Procurement_History ph
        JOIN Supplier s ON s.Id = ph.SupplierId
        LEFT JOIN Supplier_Rating sr ON sr.SupplierId = s.Id
        GROUP BY ph.SupplierId
        ORDER BY totalSpend DESC
        LIMIT 10
    """)
    top_spend_ingredients = fetch_all("""
        SELECT pb.BaseName,
               pb.AvgMarketPrice AS marketPrice,
               pb.PriceVolatility AS volatility
        FROM Price_Benchmark pb
        ORDER BY pb.BaseName
    """)
    # Build baseName -> product ids mapping
    all_rm = fetch_all(
        "SELECT Id, SKU FROM Product WHERE Type = 'raw-material'"
    )
    bn_to_pids = {}
    for r in all_rm:
        bn = _base_name(r['SKU'])
        if bn:
            bn_to_pids.setdefault(bn, []).append(r['Id'])

    # Get spend per ingredient from procurement history
    ingr_spend_list = []
    for bm in top_spend_ingredients:
        bn = bm['BaseName']
        pids = bn_to_pids.get(bn, [])
        if not pids:
            continue
        ph = ','.join(str(i) for i in pids)
        row = fetch_one(f"""
            SELECT ROUND(SUM(TotalCost), 0) AS totalSpend,
                   ROUND(AVG(UnitPrice), 2) AS avgPrice,
                   COUNT(DISTINCT SupplierId) AS supplierCount
            FROM Procurement_History
            WHERE ProductId IN ({ph})
        """)
        if row and row['totalSpend']:
            ingr_spend_list.append({
                'baseName': bn,
                'name': humanize_sku('RM-X-' + bn + '-000000'),
                'totalSpend': row['totalSpend'],
                'avgPrice': row['avgPrice'],
                'marketPrice': bm['marketPrice'],
                'volatility': bm['volatility'],
                'supplierCount': row['supplierCount'],
            })
    ingr_spend_list.sort(key=lambda x: -x['totalSpend'])
    ingr_spend_top = ingr_spend_list[:10]

    return jsonify({
        'summary': summary,
        'riskDistribution': risk_dist,
        'topSpendSuppliers': top_spend_suppliers,
        'topSpendIngredients': ingr_spend_top,
    })


@explorer_bp.route('/api/procurement/savings')
def get_cost_savings():
    """Return top cost-saving opportunities: ingredients with biggest supplier price spreads."""
    # For each ingredient in Price_Benchmark, find all suppliers with pricing,
    # compute the spread, and rank by potential savings.
    all_rm = fetch_all(
        "SELECT Id, SKU, CompanyId FROM Product WHERE Type = 'raw-material'"
    )
    # Build baseName -> product_ids
    bn_map = {}
    for r in all_rm:
        bn = _base_name(r['SKU'])
        if bn:
            bn_map.setdefault(bn, []).append(r['Id'])

    benchmarks = fetch_all("SELECT * FROM Price_Benchmark ORDER BY BaseName")
    savings = []

    for bm in benchmarks:
        bn = bm['BaseName']
        pids = bn_map.get(bn, [])
        if not pids:
            continue
        ph = ','.join(str(i) for i in pids)
        # Get per-supplier pricing for this ingredient
        sup_pricing = fetch_all(f"""
            SELECT ph.SupplierId, s.Name AS supplierName,
                   ROUND(AVG(ph.UnitPrice), 2) AS avgPrice,
                   ROUND(MIN(ph.UnitPrice), 2) AS minPrice,
                   ROUND(MAX(ph.UnitPrice), 2) AS maxPrice,
                   ROUND(SUM(ph.TotalCost), 0) AS totalSpend,
                   SUM(ph.Quantity) AS totalQty,
                   COUNT(*) AS orderCount,
                   ROUND(AVG(ph.OnTime) * 100, 1) AS onTimeRate,
                   ROUND(AVG(ph.QualityPassRate), 1) AS avgQualityPass,
                   sr.QualityScore, sr.ComplianceScore,
                   sr.ReliabilityScore, sr.RiskTier, sr.Certifications
            FROM Procurement_History ph
            JOIN Supplier s ON s.Id = ph.SupplierId
            LEFT JOIN Supplier_Rating sr ON sr.SupplierId = ph.SupplierId
            WHERE ph.ProductId IN ({ph})
            GROUP BY ph.SupplierId
            ORDER BY avgPrice ASC
        """)
        if len(sup_pricing) < 2:
            continue
        cheapest = sup_pricing[0]
        most_expensive = sup_pricing[-1]
        if cheapest['avgPrice'] <= 0 or most_expensive['avgPrice'] <= 0:
            continue
        spread = most_expensive['avgPrice'] - cheapest['avgPrice']
        spread_pct = spread / most_expensive['avgPrice'] * 100
        # Estimate total volume (kg) bought at higher prices
        # that could shift to cheapest supplier
        shiftable_spend = sum(
            s['totalSpend'] for s in sup_pricing[1:]
        )
        shiftable_qty = sum(
            s['totalQty'] for s in sup_pricing[1:]
        )
        est_savings = shiftable_qty * spread if shiftable_qty else 0
        # Company count
        co = fetch_one(f"""
            SELECT COUNT(DISTINCT CompanyId) AS cnt
            FROM Product WHERE Id IN ({ph})
        """)
        savings.append({
            'baseName': bn,
            'name': humanize_sku('RM-X-' + bn + '-000000'),
            'marketPrice': bm['AvgMarketPrice'],
            'volatility': bm['PriceVolatility'],
            'cheapestSupplier': cheapest['supplierName'],
            'cheapestPrice': cheapest['avgPrice'],
            'cheapestQuality': cheapest['QualityScore'],
            'cheapestCompliance': cheapest['ComplianceScore'],
            'cheapestReliability': cheapest['ReliabilityScore'],
            'cheapestRisk': cheapest['RiskTier'],
            'cheapestCerts': cheapest['Certifications'],
            'cheapestOnTime': cheapest['onTimeRate'],
            'expensiveSupplier': most_expensive['supplierName'],
            'expensivePrice': most_expensive['avgPrice'],
            'spreadPct': round(spread_pct, 1),
            'spreadAbs': round(spread, 2),
            'estimatedSavings': round(est_savings, 0),
            'shiftableSpend': round(shiftable_spend, 0),
            'totalSpend': round(sum(s['totalSpend'] for s in sup_pricing), 0),
            'supplierCount': len(sup_pricing),
            'companyCount': co['cnt'] if co else 0,
            'suppliers': [
                {
                    'name': s['supplierName'],
                    'avgPrice': s['avgPrice'],
                    'totalSpend': s['totalSpend'],
                    'orderCount': s['orderCount'],
                    'onTimeRate': s['onTimeRate'],
                    'qualityScore': s['QualityScore'],
                    'complianceScore': s['ComplianceScore'],
                    'reliabilityScore': s['ReliabilityScore'],
                    'riskTier': s['RiskTier'],
                    'certs': (s['Certifications'] or '').split(','),
                }
                for s in sup_pricing
            ],
        })

    savings.sort(key=lambda x: -x['estimatedSavings'])
    return jsonify({'savings': savings})


@explorer_bp.route('/api/procurement/suppliers')
def get_supplier_rankings():
    """Return supplier rankings by quality, spend, and value."""
    rows = fetch_all("""
        SELECT s.Id, s.Name,
               sr.QualityScore, sr.ComplianceScore, sr.ReliabilityScore,
               sr.RiskTier, sr.Certifications, sr.LeadTimeDays,
               ROUND(SUM(ph.TotalCost), 0) AS totalSpend,
               COUNT(ph.Id) AS orderCount,
               ROUND(AVG(ph.UnitPrice), 2) AS avgUnitPrice,
               ROUND(AVG(ph.OnTime) * 100, 1) AS onTimeRate,
               ROUND(AVG(ph.QualityPassRate), 1) AS avgQualityPass,
               COUNT(DISTINCT ph.ProductId) AS productCount,
               COUNT(DISTINCT ph.CompanyId) AS companyCount
        FROM Supplier s
        LEFT JOIN Supplier_Rating sr ON sr.SupplierId = s.Id
        LEFT JOIN Procurement_History ph ON ph.SupplierId = s.Id
        GROUP BY s.Id
        ORDER BY totalSpend DESC
    """)
    return jsonify({'suppliers': rows})
