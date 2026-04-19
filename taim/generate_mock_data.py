"""
Generate Mock Procurement Data
==============================
Adds realistic historical procurement, supplier ratings, and price benchmarks
to the existing supply chain database. Run once to populate.

New tables:
  - Supplier_Rating: quality/compliance/reliability scores, certs, lead times
  - Procurement_History: 2 years of order history with prices and delivery data
  - Price_Benchmark: market price references per ingredient
"""

import sqlite3
import random
import re
import os
import math
from datetime import datetime, timedelta
from collections import defaultdict

DB_PATH = os.path.join(os.path.dirname(__file__), '../hackathon-tumai/db.sqlite')
DB_PATH = os.path.abspath(DB_PATH)

random.seed(42)  # reproducible

# ── Category-based price ranges (USD/kg) ──────────────────────────
CATEGORY_PRICES = {
    'protein':              (15, 60),
    'sweetener':            (2, 20),
    'emulsifier':           (8, 35),
    'vitamin':              (40, 250),
    'mineral':              (5, 45),
    'fiber':                (4, 18),
    'fat_oil':              (3, 15),
    'flavor':               (20, 120),
    'thickener_stabilizer': (6, 30),
    'preservative':         (8, 40),
    'acid':                 (3, 15),
    'color':                (25, 150),
    'botanical':            (30, 180),
    'capsule_coating':      (10, 50),
    'excipient':            (2, 12),
    'probiotic':            (80, 350),
    'salt_electrolyte':     (1, 10),
    'other':                (5, 40),
}

FUNCTIONAL_CATEGORIES = {
    'protein': ['protein', 'collagen', 'peptide', 'casein', 'whey', 'bcaa',
                'l-leucine', 'l-isoleucine', 'l-valine', 'leucine', 'amino-acid'],
    'sweetener': ['sugar', 'stevia', 'monk-fruit', 'erythritol', 'sucralose',
                  'sucrose', 'fructose', 'dextrose', 'maltodextrin', 'sorbitol',
                  'agave', 'coconut-sugar', 'cane-sugar', 'rebaudioside',
                  'acesulfame', 'tapioca-syrup', 'polydextrose', 'inulin'],
    'emulsifier': ['lecithin', 'polysorbate', 'glyceride', 'acetoglyceride'],
    'vitamin': ['vitamin', 'ascorbic', 'retinol', 'tocopherol', 'thiamin',
                'riboflavin', 'niacin', 'niacinamide', 'folate', 'folic',
                'biotin', 'cobalamin', 'cyanocobalamin', 'pantothen',
                'pyridoxine', 'cholecalciferol', 'phytonadione', 'menaquinone',
                'retinyl', 'ascorbyl', 'beta-carotene', 'tocopherols'],
    'mineral': ['calcium', 'magnesium', 'zinc', 'iron', 'selenium', 'chromium',
                'copper', 'manganese', 'potassium', 'phosphorus', 'iodine',
                'boron', 'molybdenum', 'ferrous', 'cupric', 'trace-mineral'],
    'fiber': ['fiber', 'fibre', 'inulin', 'psyllium', 'prebiotic',
              'fructooligosaccharide', 'tapioca-fiber'],
    'fat_oil': ['oil', 'mct', 'coconut-mct', 'medium-chain-triglyceride',
                'safflower', 'soybean-oil', 'corn-oil', 'palm-oil'],
    'flavor': ['flavor', 'flavour', 'vanilla', 'chocolate', 'cocoa',
               'cinnamon', 'cherry', 'strawberry', 'peach', 'lemon', 'ginger'],
    'thickener_stabilizer': ['gum', 'starch', 'pectin', 'agar', 'carrageenan',
                             'gelatin', 'gellan', 'xanthan', 'cellulose-gum',
                             'acacia', 'gum-arabic'],
    'preservative': ['benzoate', 'sorbate', 'sorbic-acid', 'bht',
                     'rosemary-extract'],
    'acid': ['citric-acid', 'malic-acid', 'lactic-acid', 'tartaric-acid',
             'stearic-acid'],
    'color': ['color', 'lake', 'caramel', 'annatto', 'turmeric',
              'beet-extract', 'titanium-dioxide', 'blue-2', 'red-40'],
    'botanical': ['extract', 'herb', 'rhodiola', 'ashwagandha', 'green-tea',
                  'grape-seed', 'pomegranate', 'astaxanthin', 'lutein',
                  'lycopene', 'resveratrol', 'coenzyme', 'coq10', 'taurine'],
    'capsule_coating': ['capsule', 'coating', 'softgel', 'hypromellose',
                        'hpmc', 'hydroxypropyl', 'pharmaceutical-glaze',
                        'carnauba-wax', 'zein', 'croscarmellose', 'plantgel'],
    'excipient': ['microcrystalline-cellulose', 'cellulose', 'silica',
                  'silicon-dioxide', 'magnesium-stearate', 'talc',
                  'dicalcium-phosphate', 'tricalcium-phosphate', 'rice-flour',
                  'rice-powder', 'modified-cellulose', 'sodium-alginate'],
    'probiotic': ['probiotic', 'bifidobacterium', 'lactobacillus', 'ferment'],
    'salt_electrolyte': ['salt', 'sodium-chloride', 'himalayan', 'sea-salt',
                         'chloride', 'dipotassium-phosphate',
                         'potassium-chloride', 'sodium-citrate'],
}

ALL_CERTS = ['GMP', 'ISO-9001', 'ISO-22000', 'FSSC-22000', 'organic',
             'non-GMO', 'kosher', 'halal', 'NSF', 'USP-verified']


def _base_name(sku):
    s = re.sub(r'^RM-C\d+-', '', sku)
    s = re.sub(r'-[0-9a-f]{6,}$', '', s, flags=re.IGNORECASE)
    return s.lower().strip('-')


def _categorize(name):
    name_lower = name.lower()
    scores = {}
    for cat, keywords in FUNCTIONAL_CATEGORIES.items():
        for kw in keywords:
            if kw in name_lower:
                scores[cat] = scores.get(cat, 0) + len(kw)
    if scores:
        return max(scores, key=scores.get)
    return 'other'


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # ── Check if already populated ──
    existing = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()]
    if 'Supplier_Rating' in existing:
        print("Mock data tables already exist. Dropping and recreating...")
        c.execute("DROP TABLE IF EXISTS Procurement_History")
        c.execute("DROP TABLE IF EXISTS Price_Benchmark")
        c.execute("DROP TABLE IF EXISTS Supplier_Rating")

    # ── Create tables ──
    c.execute("""
        CREATE TABLE Supplier_Rating (
            SupplierId INTEGER PRIMARY KEY REFERENCES Supplier(Id),
            QualityScore REAL,
            ComplianceScore REAL,
            ReliabilityScore REAL,
            LeadTimeDays INTEGER,
            MinOrderQty INTEGER,
            Certifications TEXT,
            LastAuditDate TEXT,
            RiskTier TEXT
        )
    """)

    c.execute("""
        CREATE TABLE Procurement_History (
            Id INTEGER PRIMARY KEY AUTOINCREMENT,
            SupplierId INTEGER REFERENCES Supplier(Id),
            ProductId INTEGER REFERENCES Product(Id),
            CompanyId INTEGER REFERENCES Company(Id),
            OrderDate TEXT,
            DeliveryDate TEXT,
            Quantity REAL,
            UnitPrice REAL,
            TotalCost REAL,
            Currency TEXT DEFAULT 'USD',
            OnTime INTEGER,
            QualityPassRate REAL
        )
    """)

    c.execute("""
        CREATE TABLE Price_Benchmark (
            BaseName TEXT PRIMARY KEY,
            AvgMarketPrice REAL,
            MinPrice REAL,
            MaxPrice REAL,
            PriceVolatility REAL,
            LastUpdated TEXT
        )
    """)

    # ── Load existing data ──
    suppliers = {r['Id']: dict(r) for r in c.execute("SELECT * FROM Supplier")}
    products = {r['Id']: dict(r) for r in c.execute("SELECT * FROM Product")}
    supplier_products = [(r['SupplierId'], r['ProductId'])
                         for r in c.execute("SELECT * FROM Supplier_Product")]
    companies = {r['Id']: dict(r) for r in c.execute("SELECT * FROM Company")}

    # Build indices
    rm_products = {pid: p for pid, p in products.items()
                   if p['Type'] == 'raw-material'}
    base_name_map = {}
    for pid, p in rm_products.items():
        bn = _base_name(p['SKU'])
        if bn:
            base_name_map.setdefault(bn, []).append(pid)

    product_suppliers = defaultdict(list)  # product_id -> [supplier_id]
    supplier_to_products = defaultdict(list)  # supplier_id -> [product_id]
    for sid, pid in supplier_products:
        product_suppliers[pid].append(sid)
        supplier_to_products[sid].append(pid)

    print(f"Loaded: {len(suppliers)} suppliers, {len(rm_products)} raw materials, "
          f"{len(base_name_map)} unique ingredients, {len(supplier_products)} links")

    # ── 1. Generate Supplier Ratings ──
    print("\nGenerating supplier ratings...")
    supplier_profiles = {}
    for sid, s in suppliers.items():
        # Larger suppliers (more products) tend to have better quality systems
        product_count = len(supplier_to_products.get(sid, []))
        size_factor = min(product_count / 100, 1.0)  # 0-1 based on portfolio size

        quality = max(50, min(100, random.gauss(82 + size_factor * 8, 8)))
        compliance = max(55, min(100, random.gauss(85 + size_factor * 5, 7)))
        reliability = max(60, min(100, random.gauss(88 + size_factor * 5, 6)))

        # Lead time: 7-90 days, larger suppliers tend to be faster
        lead_time = max(5, int(random.gauss(28 - size_factor * 10, 12)))

        # MOQ: varies by supplier size
        moq = random.choice([50, 100, 250, 500, 1000, 2500, 5000])

        # Certifications: more for larger/better suppliers
        n_certs = max(1, int(random.gauss(3 + size_factor * 3, 1.5)))
        # GMP is almost always present for pharma/food suppliers
        certs = ['GMP']
        remaining = [c for c in ALL_CERTS if c != 'GMP']
        random.shuffle(remaining)
        certs.extend(remaining[:n_certs - 1])

        # Last audit: within past 18 months
        days_ago = random.randint(30, 540)
        audit_date = (datetime.now() - timedelta(days=days_ago)).strftime('%Y-%m-%d')

        # Risk tier
        composite = (quality * 0.35 + compliance * 0.35 + reliability * 0.3)
        if composite >= 88:
            risk_tier = 'low'
        elif composite >= 75:
            risk_tier = 'medium'
        else:
            risk_tier = 'high'

        supplier_profiles[sid] = {
            'quality': round(quality, 1),
            'compliance': round(compliance, 1),
            'reliability': round(reliability, 1),
            'lead_time': lead_time,
            'moq': moq,
            'certs': ','.join(sorted(certs)),
            'audit_date': audit_date,
            'risk_tier': risk_tier,
        }

        c.execute("""
            INSERT INTO Supplier_Rating
            (SupplierId, QualityScore, ComplianceScore, ReliabilityScore,
             LeadTimeDays, MinOrderQty, Certifications, LastAuditDate, RiskTier)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (sid, round(quality, 1), round(compliance, 1), round(reliability, 1),
              lead_time, moq, ','.join(sorted(certs)), audit_date, risk_tier))

    print(f"  Created {len(supplier_profiles)} supplier ratings")
    risk_dist = defaultdict(int)
    for p in supplier_profiles.values():
        risk_dist[p['risk_tier']] += 1
    print(f"  Risk distribution: {dict(risk_dist)}")

    # ── 2. Generate Price Benchmarks ──
    print("\nGenerating price benchmarks...")
    ingredient_prices = {}  # base_name -> base_price
    for bn in base_name_map:
        cat = _categorize(bn)
        price_range = CATEGORY_PRICES.get(cat, (5, 40))
        base_price = random.uniform(price_range[0], price_range[1])

        # Add some ingredient-specific variation
        volatility = random.uniform(0.05, 0.35)
        min_price = base_price * (1 - volatility)
        max_price = base_price * (1 + volatility)
        avg_price = base_price * random.uniform(0.95, 1.05)

        ingredient_prices[bn] = {
            'avg': round(avg_price, 2),
            'min': round(min_price, 2),
            'max': round(max_price, 2),
            'volatility': round(volatility, 3),
        }

        c.execute("""
            INSERT INTO Price_Benchmark (BaseName, AvgMarketPrice, MinPrice,
                                          MaxPrice, PriceVolatility, LastUpdated)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (bn, round(avg_price, 2), round(min_price, 2), round(max_price, 2),
              round(volatility, 3), '2026-04-01'))

    print(f"  Created {len(ingredient_prices)} price benchmarks")

    # ── 3. Generate Procurement History ──
    print("\nGenerating procurement history...")
    order_count = 0
    start_date = datetime(2024, 4, 1)
    end_date = datetime(2026, 4, 1)
    total_days = (end_date - start_date).days

    # For each supplier-product link, generate orders from the companies
    # that use that product
    bom_components = [(r['BOMId'], r['ConsumedProductId'])
                      for r in c.execute("SELECT * FROM BOM_Component")]
    boms = {r['Id']: r['ProducedProductId']
            for r in c.execute("SELECT Id, ProducedProductId FROM BOM")}

    # product_id -> set of company_ids that use it (via BOMs)
    product_companies = defaultdict(set)
    for bom_id, rm_id in bom_components:
        fg_id = boms.get(bom_id)
        if fg_id and fg_id in products:
            cid = products[fg_id]['CompanyId']
            product_companies[rm_id].add(cid)

    for sid, pid in supplier_products:
        if pid not in rm_products:
            continue

        bn = _base_name(rm_products[pid]['SKU'])
        if bn not in ingredient_prices:
            continue

        base_price = ingredient_prices[bn]['avg']
        vol = ingredient_prices[bn]['volatility']
        sup_profile = supplier_profiles[sid]

        # Companies using this product
        cos = product_companies.get(pid, set())
        if not cos:
            # If no BOM link, use the product's own company
            cos = {rm_products[pid]['CompanyId']}

        for cid in cos:
            # Generate 2-8 orders over 2 years
            n_orders = random.randint(2, 8)
            for _ in range(n_orders):
                # Random order date within range
                order_day = random.randint(0, total_days - 30)
                order_date = start_date + timedelta(days=order_day)

                # Quantity: based on MOQ + some multiple
                qty = sup_profile['moq'] * random.choice([1, 1, 1, 2, 2, 3, 5])

                # Price: base ± volatility, with a supplier-specific markup/discount
                # Better quality suppliers charge slightly more
                quality_premium = (sup_profile['quality'] - 80) / 100 * 0.1
                price_variation = random.gauss(0, vol * 0.5)
                unit_price = base_price * (1 + quality_premium + price_variation)
                unit_price = max(base_price * 0.5, unit_price)  # floor

                total_cost = qty * unit_price

                # Delivery: lead_time ± some variance
                actual_lead = max(1, int(random.gauss(
                    sup_profile['lead_time'],
                    sup_profile['lead_time'] * 0.2
                )))
                delivery_date = order_date + timedelta(days=actual_lead)

                # On-time: correlated with reliability score
                on_time_prob = sup_profile['reliability'] / 100
                on_time = 1 if random.random() < on_time_prob else 0

                # Quality pass rate: correlated with quality score
                qpr = max(70, min(100, random.gauss(
                    sup_profile['quality'] * 0.95 + 5, 3
                )))

                c.execute("""
                    INSERT INTO Procurement_History
                    (SupplierId, ProductId, CompanyId, OrderDate, DeliveryDate,
                     Quantity, UnitPrice, TotalCost, Currency, OnTime, QualityPassRate)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'USD', ?, ?)
                """, (sid, pid, cid,
                      order_date.strftime('%Y-%m-%d'),
                      delivery_date.strftime('%Y-%m-%d'),
                      round(qty, 1), round(unit_price, 2), round(total_cost, 2),
                      on_time, round(qpr, 1)))
                order_count += 1

    conn.commit()
    print(f"  Created {order_count} procurement orders")

    # ── Summary stats ──
    print("\n" + "=" * 60)
    print("MOCK DATA SUMMARY")
    print("=" * 60)
    sr_count = c.execute("SELECT COUNT(*) FROM Supplier_Rating").fetchone()[0]
    pb_count = c.execute("SELECT COUNT(*) FROM Price_Benchmark").fetchone()[0]
    ph_count = c.execute("SELECT COUNT(*) FROM Procurement_History").fetchone()[0]
    total_spend = c.execute("SELECT SUM(TotalCost) FROM Procurement_History").fetchone()[0]
    avg_quality = c.execute("SELECT AVG(QualityScore) FROM Supplier_Rating").fetchone()[0]
    avg_compliance = c.execute("SELECT AVG(ComplianceScore) FROM Supplier_Rating").fetchone()[0]
    avg_reliability = c.execute("SELECT AVG(ReliabilityScore) FROM Supplier_Rating").fetchone()[0]
    on_time_rate = c.execute(
        "SELECT AVG(OnTime) * 100 FROM Procurement_History"
    ).fetchone()[0]
    avg_qpr = c.execute(
        "SELECT AVG(QualityPassRate) FROM Procurement_History"
    ).fetchone()[0]

    print(f"  Supplier_Rating:      {sr_count} rows")
    print(f"  Price_Benchmark:      {pb_count} rows")
    print(f"  Procurement_History:  {ph_count} rows")
    print(f"  Total spend:          ${total_spend:,.0f}")
    print(f"  Avg quality score:    {avg_quality:.1f}")
    print(f"  Avg compliance score: {avg_compliance:.1f}")
    print(f"  Avg reliability:      {avg_reliability:.1f}")
    print(f"  On-time delivery:     {on_time_rate:.1f}%")
    print(f"  Avg QC pass rate:     {avg_qpr:.1f}%")

    # Show a few sample rows
    print("\nSample Supplier Ratings:")
    for r in c.execute("""
        SELECT s.Name, sr.QualityScore, sr.ComplianceScore, sr.ReliabilityScore,
               sr.RiskTier, sr.Certifications
        FROM Supplier_Rating sr JOIN Supplier s ON s.Id = sr.SupplierId
        ORDER BY sr.QualityScore DESC LIMIT 5
    """).fetchall():
        print(f"  {r[0]:25s} Q={r[1]:.0f} C={r[2]:.0f} R={r[3]:.0f} "
              f"Risk={r[4]:6s} Certs={r[5]}")

    print("\nSample Price Benchmarks:")
    for r in c.execute("""
        SELECT BaseName, AvgMarketPrice, MinPrice, MaxPrice, PriceVolatility
        FROM Price_Benchmark ORDER BY AvgMarketPrice DESC LIMIT 5
    """).fetchall():
        print(f"  {r[0]:35s} Avg=${r[1]:7.2f}  Range=${r[2]:.2f}-{r[3]:.2f}  "
              f"Vol={r[4]:.2f}")

    print("\nSample Procurement History:")
    for r in c.execute("""
        SELECT s.Name, p.SKU, c.Name, ph.OrderDate, ph.Quantity,
               ph.UnitPrice, ph.TotalCost, ph.OnTime
        FROM Procurement_History ph
        JOIN Supplier s ON s.Id = ph.SupplierId
        JOIN Product p ON p.Id = ph.ProductId
        JOIN Company c ON c.Id = ph.CompanyId
        ORDER BY ph.TotalCost DESC LIMIT 5
    """).fetchall():
        print(f"  {r[0]:20s} → {r[2]:20s} {r[3]} "
              f"Qty={r[4]:,.0f} @${r[5]:.2f} = ${r[6]:,.0f} "
              f"{'✓' if r[7] else '✗'}")

    conn.close()
    print("\nDone! Mock data generated successfully.")


if __name__ == '__main__':
    main()
