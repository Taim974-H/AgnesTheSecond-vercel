"""
AgnesTheSecond Analysis Engine
==============================
Core analysis module for identifying substitutable ingredients,
consolidation opportunities, and sourcing recommendations across
a CPG supply chain database.

Prioritization Framework
------------------------
Every consolidation opportunity is scored across 5 explicit dimensions
that balance leverage against concentration risk:

  0.35 × consolidation_benefit     — how much buying leverage is gained
  0.25 × evidence_confidence       — how well-supported the recommendation is
  0.20 × compliance_fit            — quality + compliance fit of the target supplier
  0.10 × supplier_diversification  — network resilience AFTER consolidation
  0.10 × switching_feasibility     — how actionable the swap is in practice

The `supplier_diversification` dimension is the anti-monopoly guard:
when the network has ≤2 suppliers for an ingredient, collapsing to one
creates single-source risk, and the framework **downgrades** the grade
from "safe_to_consolidate" to "review_required" even when the raw
weighted score would otherwise clear the threshold.

Agnes doesn't minimize suppliers. It finds the optimal consolidation
point — enough leverage to negotiate, enough diversification to absorb
a supplier disruption.
"""

import sqlite3
import re
import math
from collections import defaultdict
from difflib import SequenceMatcher

# ────────────────────────────────────────────────────────────
#  Prioritization Framework constants
# ────────────────────────────────────────────────────────────

PRIORITIZATION_WEIGHTS = {
    'consolidation_benefit': 0.35,
    'evidence_confidence': 0.25,
    'compliance_fit': 0.20,
    'supplier_diversification': 0.10,
    'switching_feasibility': 0.10,
}
GRADE_SAFE_THRESHOLD = 0.70
GRADE_REJECT_THRESHOLD = 0.30
DIVERSIFICATION_FLOOR = 0.30  # monopoly veto fires below this

# Modifier tokens stripped when computing functional root of ingredient names
MODIFIER_TOKENS = {
    'organic', 'natural', 'artificial', 'non', 'gmo', 'pure',
    'vegan', 'vegetable', 'grass', 'fed', 'from', 'concentrate',
    'processed', 'with', 'alkali', 'dl', 'l', 'd', 'alpha',
}

# ── Scoring weights for risk mitigation recommendations ──
RISK_WEIGHTS = {
    'impact_severity': 0.30,
    'evidence_strength': 0.25,
    'mitigation_feasibility': 0.20,
    'exposure_breadth': 0.15,
    'urgency': 0.10,
}

# ── Scoring weights for substitution recommendations ──
SUBSTITUTION_WEIGHTS = {
    'similarity_score': 0.30,
    'evidence_strength': 0.20,
    'compliance_compatibility': 0.20,
    'network_benefit': 0.15,
    'switching_feasibility': 0.15,
}

# ── Scoring weights for cost optimization recommendations ──
COST_WEIGHTS = {
    'savings_magnitude': 0.30,
    'evidence_strength': 0.20,
    'quality_assurance': 0.25,
    'supplier_reliability': 0.15,
    'implementation_ease': 0.10,
}

# Ordered dimension labels per recommendation type (key → display label)
DIMENSION_LABELS = {
    'consolidation': [
        ['consolidation_benefit', 'Consolidation leverage'],
        ['evidence_confidence', 'Evidence confidence'],
        ['compliance_fit', 'Compliance fit'],
        ['supplier_diversification', 'Supplier diversification'],
        ['switching_feasibility', 'Switching feasibility'],
    ],
    'risk_mitigation': [
        ['impact_severity', 'Impact severity'],
        ['evidence_strength', 'Evidence strength'],
        ['mitigation_feasibility', 'Mitigation feasibility'],
        ['exposure_breadth', 'Exposure breadth'],
        ['urgency', 'Urgency'],
    ],
    'substitution': [
        ['similarity_score', 'Ingredient similarity'],
        ['evidence_strength', 'Evidence strength'],
        ['compliance_compatibility', 'Compliance compatibility'],
        ['network_benefit', 'Network benefit'],
        ['switching_feasibility', 'Switching feasibility'],
    ],
    'cost_optimization': [
        ['savings_magnitude', 'Savings magnitude'],
        ['evidence_strength', 'Evidence strength'],
        ['quality_assurance', 'Quality assurance'],
        ['supplier_reliability', 'Supplier reliability'],
        ['implementation_ease', 'Implementation ease'],
    ],
}

# ────────────────────────────────────────────────────────────
#  Ingredient Knowledge Base
# ────────────────────────────────────────────────────────────

FUNCTIONAL_CATEGORIES = {
    'protein': {
        'keywords': ['protein', 'collagen', 'peptide', 'casein', 'whey', 'bcaa',
                     'l-leucine', 'l-isoleucine', 'l-valine', 'leucine', 'amino-acid'],
        'label': 'Protein & Amino Acids',
    },
    'sweetener': {
        'keywords': ['sugar', 'stevia', 'monk-fruit', 'erythritol', 'sucralose',
                     'sucrose', 'fructose', 'dextrose', 'maltodextrin', 'sorbitol',
                     'agave', 'coconut-sugar', 'cane-sugar', 'rebaudioside',
                     'acesulfame', 'tapioca-syrup', 'polydextrose', 'inulin'],
        'label': 'Sweeteners & Carbohydrates',
    },
    'emulsifier': {
        'keywords': ['lecithin', 'polysorbate', 'glyceride', 'acetoglyceride'],
        'label': 'Emulsifiers',
    },
    'vitamin': {
        'keywords': ['vitamin', 'ascorbic', 'retinol', 'tocopherol', 'thiamin',
                     'thiamine', 'riboflavin', 'niacin', 'niacinamide', 'nicotinamide',
                     'folate', 'folic', 'biotin', 'cobalamin', 'cyanocobalamin',
                     'methylcobalamin', 'pantothen', 'pyridoxine', 'cholecalciferol',
                     'phytonadione', 'menaquinone', 'retinyl', 'ascorbyl',
                     'beta-carotene', 'b-vitamins', 'd-alpha-tocopheryl',
                     'dl-alpha-tocopheryl', 'tocopherols'],
        'label': 'Vitamins',
    },
    'mineral': {
        'keywords': ['calcium', 'magnesium', 'zinc', 'iron', 'selenium', 'chromium',
                     'copper', 'manganese', 'potassium', 'phosphorus', 'iodine',
                     'iodide', 'boron', 'molybdenum', 'vanadium', 'sodium-selenite',
                     'sodium-molybdate', 'ferrous', 'cupric', 'trace-mineral',
                     'concentrace'],
        'label': 'Minerals',
    },
    'fiber': {
        'keywords': ['fiber', 'fibre', 'inulin', 'psyllium', 'prebiotic',
                     'fructooligosaccharide', 'tapioca-fiber'],
        'label': 'Fiber & Prebiotics',
    },
    'fat_oil': {
        'keywords': ['oil', 'mct', 'coconut-mct', 'medium-chain-triglyceride',
                     'safflower', 'soybean-oil', 'corn-oil', 'palm-oil', 'olive-oil',
                     'sunflower-oil'],
        'label': 'Fats & Oils',
    },
    'flavor': {
        'keywords': ['flavor', 'flavour', 'vanilla', 'chocolate', 'cocoa',
                     'cinnamon', 'cherry', 'strawberry', 'peach', 'tangerine',
                     'lemon', 'passionfruit', 'orange-flavor', 'ginger'],
        'label': 'Flavors & Extracts',
    },
    'thickener_stabilizer': {
        'keywords': ['gum', 'starch', 'pectin', 'agar', 'carrageenan', 'gelatin',
                     'gellan', 'xanthan', 'cellulose-gum', 'cellulose-gel',
                     'acacia', 'gum-arabic', 'gum-acacia', 'gummy-base'],
        'label': 'Thickeners & Stabilizers',
    },
    'preservative': {
        'keywords': ['benzoate', 'sorbate', 'sorbic-acid', 'bht',
                     'rosemary-extract'],
        'label': 'Preservatives & Antioxidants',
    },
    'acid': {
        'keywords': ['citric-acid', 'malic-acid', 'lactic-acid', 'tartaric-acid',
                     'stearic-acid', 'dl-tartaric'],
        'label': 'Acids',
    },
    'color': {
        'keywords': ['color', 'lake', 'caramel', 'annatto', 'turmeric',
                     'beet-extract', 'beet-juice', 'titanium-dioxide',
                     'blue-2', 'red-40', 'yellow-6', 'fd-and-c'],
        'label': 'Colors & Colorants',
    },
    'botanical': {
        'keywords': ['extract', 'herb', 'botanical', 'rhodiola', 'ashwagandha',
                     'green-tea', 'grape-seed', 'pomegranate', 'alfalfa',
                     'black-pepper', 'astaxanthin', 'lutein', 'lycopene',
                     'zeaxanthin', 'resveratrol', 'bioflavonoid', 'hesperidin',
                     'rutin', 'coenzyme', 'coq10', 'epicor', 'taurine'],
        'label': 'Botanicals & Nutraceuticals',
    },
    'capsule_coating': {
        'keywords': ['capsule', 'coating', 'softgel', 'hypromellose', 'hpmc',
                     'hydroxypropyl', 'pharmaceutical-glaze', 'lac-resin',
                     'carnauba-wax', 'zein', 'polyvinyl', 'polyethylene',
                     'croscarmellose', 'sodium-starch-glycolate', 'plantgel',
                     'vegan-capsule', 'gelatin-capsule', 'vegetarian-capsule'],
        'label': 'Capsules & Coatings',
    },
    'excipient': {
        'keywords': ['microcrystalline-cellulose', 'cellulose', 'silica',
                     'silicon-dioxide', 'stearic-acid', 'magnesium-stearate',
                     'talc', 'dicalcium-phosphate', 'tricalcium-phosphate',
                     'rice-flour', 'rice-powder', 'rice-bran', 'modified-cellulose',
                     'modified-food-starch', 'sodium-alginate', 'potassium-alginate'],
        'label': 'Excipients & Fillers',
    },
    'probiotic': {
        'keywords': ['probiotic', 'bifidobacterium', 'lactobacillus', 'ferment',
                     'cultured'],
        'label': 'Probiotics & Cultures',
    },
    'salt_electrolyte': {
        'keywords': ['salt', 'sodium-chloride', 'himalayan', 'sea-salt',
                     'kalahari', 'chloride', 'dipotassium-phosphate',
                     'potassium-chloride', 'sodium-citrate'],
        'label': 'Salts & Electrolytes',
    },
}

ALLERGEN_MARKERS = {
    'soy': ['soy', 'soja', 'soybean'],
    'dairy': ['whey', 'casein', 'milk', 'lactose', 'dairy'],
    'gluten': ['wheat', 'gluten', 'barley', 'rye'],
    'tree_nut': ['almond', 'cashew', 'walnut', 'pecan', 'hazelnut', 'coconut'],
    'egg': ['egg', 'albumin'],
    'bovine': ['bovine', 'bone-gelatin'],
    'fish': ['fish', 'cod', 'salmon'],
}

QUALITY_FLAGS = {
    'organic': ['organic'],
    'non_gmo': ['non-gmo'],
    'vegan': ['vegan', 'vegetable', 'plant'],
    'natural': ['natural'],
    'artificial': ['artificial'],
    'grass_fed': ['grass-fed'],
}


# ────────────────────────────────────────────────────────────
#  Helper Functions
# ────────────────────────────────────────────────────────────

def _base_name(sku):
    """Extract canonical ingredient name from SKU."""
    s = re.sub(r'^RM-C\d+-', '', sku)
    s = re.sub(r'-[0-9a-f]{6,}$', '', s, flags=re.IGNORECASE)
    return s.lower().strip('-')


def _humanize(name):
    """Turn base name into readable form."""
    return name.replace('-', ' ').strip().title()


def _tokenize(name):
    """Split ingredient name into meaningful word tokens."""
    return set(name.lower().replace('-', ' ').split())


def _jaccard(set_a, set_b):
    """Jaccard similarity between two sets."""
    if not set_a or not set_b:
        return 0.0
    inter = set_a & set_b
    union = set_a | set_b
    return len(inter) / len(union)


# ────────────────────────────────────────────────────────────
#  Core Engine
# ────────────────────────────────────────────────────────────

class AgnesEngine:
    """AgnesTheSecond Analysis Engine.

    Loads a SQLite supply chain database and performs:
    - Ingredient profiling & functional categorization
    - Multi-level substitution detection
    - Cross-company consolidation analysis
    - Supply chain risk assessment
    - Evidence-backed sourcing recommendations
    """

    def __init__(self, db_path):
        self.db_path = db_path
        # Raw data
        self.companies = {}
        self.products = {}
        self.boms = {}
        self.bom_components = []
        self.suppliers = {}
        self.supplier_products = []
        # Mock procurement data
        self.supplier_ratings = {}
        self.price_benchmarks = {}
        self.procurement_history = []
        # Indices
        self.rm_products = {}
        self.fg_products = {}
        self.base_name_index = defaultdict(list)   # base_name -> [product_id]
        self.product_suppliers = defaultdict(list)  # product_id -> [supplier_id]
        self.supplier_to_products = defaultdict(list)
        self.fg_components = defaultdict(list)      # fg_id -> [rm_id]
        self.rm_to_fgs = defaultdict(set)           # rm_id -> {fg_id}
        # Analysis results
        self.ingredient_profiles = {}
        self.substitution_groups = []
        self.consolidation_opportunities = []
        self.risk_items = []
        self.recommendations = []
        self._analysis_done = False

    # ── Data Loading ────────────────────────────

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def load_data(self):
        conn = self._connect()
        self.companies = {r['Id']: dict(r) for r in conn.execute("SELECT * FROM Company")}
        self.products = {r['Id']: dict(r) for r in conn.execute("SELECT * FROM Product")}
        self.boms = {r['Id']: dict(r) for r in conn.execute("SELECT * FROM BOM")}
        self.bom_components = [dict(r) for r in conn.execute("SELECT * FROM BOM_Component")]
        self.suppliers = {r['Id']: dict(r) for r in conn.execute("SELECT * FROM Supplier")}
        self.supplier_products = [dict(r) for r in conn.execute("SELECT * FROM Supplier_Product")]

        # Load procurement mock data (if tables exist)
        try:
            self.supplier_ratings = {
                r['SupplierId']: dict(r)
                for r in conn.execute("SELECT * FROM Supplier_Rating")
            }
        except Exception:
            self.supplier_ratings = {}
        try:
            self.price_benchmarks = {
                r['BaseName']: dict(r)
                for r in conn.execute("SELECT * FROM Price_Benchmark")
            }
        except Exception:
            self.price_benchmarks = {}
        try:
            self.procurement_history = [
                dict(r) for r in conn.execute("SELECT * FROM Procurement_History")
            ]
        except Exception:
            self.procurement_history = []

        conn.close()

        # Build indices
        for pid, p in self.products.items():
            if p['Type'] == 'raw-material':
                self.rm_products[pid] = p
                bn = _base_name(p['SKU'])
                if bn:
                    self.base_name_index[bn].append(pid)
            else:
                self.fg_products[pid] = p

        for sp in self.supplier_products:
            self.product_suppliers[sp['ProductId']].append(sp['SupplierId'])
            self.supplier_to_products[sp['SupplierId']].append(sp['ProductId'])

        for bc in self.bom_components:
            bom = self.boms.get(bc['BOMId'])
            if bom:
                fg_id = bom['ProducedProductId']
                rm_id = bc['ConsumedProductId']
                self.fg_components[fg_id].append(rm_id)
                self.rm_to_fgs[rm_id].add(fg_id)

    # ── Ingredient Profiling ────────────────────

    def _categorize(self, name):
        """Assign functional category using keyword matching."""
        name_lower = name.lower()
        scores = {}
        for cat, info in FUNCTIONAL_CATEGORIES.items():
            for kw in info['keywords']:
                if kw in name_lower:
                    scores[cat] = scores.get(cat, 0) + len(kw)  # weight by keyword length
        if scores:
            best = max(scores, key=scores.get)
            return best, FUNCTIONAL_CATEGORIES[best]['label']
        return 'other', 'Other / Uncategorized'

    def _detect_allergens(self, name):
        """Detect potential allergens from ingredient name."""
        name_lower = name.lower()
        allergens = []
        for allergen, markers in ALLERGEN_MARKERS.items():
            for m in markers:
                if m in name_lower:
                    allergens.append(allergen)
                    break
        return allergens

    def _detect_quality_flags(self, name):
        """Detect quality/certification indicators."""
        name_lower = name.lower()
        flags = []
        for flag, markers in QUALITY_FLAGS.items():
            for m in markers:
                if m in name_lower:
                    flags.append(flag)
                    break
        return flags

    def profile_ingredients(self):
        """Build comprehensive profiles for all unique ingredients."""
        for bn, product_ids in self.base_name_index.items():
            # Companies using this ingredient
            company_ids = set()
            supplier_ids = set()
            fg_ids_using = set()
            for pid in product_ids:
                p = self.products[pid]
                company_ids.add(p['CompanyId'])
                supplier_ids.update(self.product_suppliers.get(pid, []))
                fg_ids_using.update(self.rm_to_fgs.get(pid, set()))

            # Supplier detail
            supplier_info = []
            for sid in supplier_ids:
                # How many products of this ingredient does this supplier serve?
                served = [pid for pid in product_ids if sid in self.product_suppliers.get(pid, [])]
                supplier_info.append({
                    'id': sid,
                    'name': self.suppliers[sid]['Name'],
                    'productCount': len(served),
                    'companyCount': len(set(self.products[pid]['CompanyId'] for pid in served)),
                })
            supplier_info.sort(key=lambda x: -x['productCount'])

            category_id, category_label = self._categorize(bn)
            allergens = self._detect_allergens(bn)
            quality_flags = self._detect_quality_flags(bn)

            # Single-source products
            single_source_count = sum(
                1 for pid in product_ids if len(self.product_suppliers.get(pid, [])) <= 1
            )

            self.ingredient_profiles[bn] = {
                'baseName': bn,
                'name': _humanize(bn),
                'category': category_id,
                'categoryLabel': category_label,
                'allergens': allergens,
                'qualityFlags': quality_flags,
                'companyCount': len(company_ids),
                'companyIds': list(company_ids),
                'supplierCount': len(supplier_ids),
                'suppliers': supplier_info,
                'productCount': len(product_ids),
                'productIds': product_ids,
                'finishedGoodCount': len(fg_ids_using),
                'singleSourceCount': single_source_count,
                'pricing': self._get_ingredient_pricing(bn, product_ids, supplier_ids),
            }

    # ── Substitution Detection ─────────────────

    def _get_ingredient_pricing(self, base_name, product_ids, supplier_ids):
        """Build pricing/procurement summary for an ingredient."""
        benchmark = self.price_benchmarks.get(base_name)
        if not benchmark:
            return None

        # Aggregate procurement history for this ingredient's products
        pid_set = set(product_ids)
        orders = [o for o in self.procurement_history if o['ProductId'] in pid_set]

        if not orders:
            return {
                'benchmark': {
                    'avgMarketPrice': benchmark['AvgMarketPrice'],
                    'minPrice': benchmark['MinPrice'],
                    'maxPrice': benchmark['MaxPrice'],
                    'volatility': benchmark['PriceVolatility'],
                },
                'totalSpend': 0,
                'orderCount': 0,
                'avgUnitPrice': None,
                'supplierPricing': [],
            }

        total_spend = sum(o['TotalCost'] for o in orders)
        total_qty = sum(o['Quantity'] for o in orders)
        avg_price = total_spend / total_qty if total_qty else 0

        # Per-supplier pricing
        supplier_pricing = {}
        for o in orders:
            sid = o['SupplierId']
            if sid not in supplier_pricing:
                supplier_pricing[sid] = {
                    'id': sid,
                    'name': self.suppliers[sid]['Name'],
                    'orders': 0, 'totalSpend': 0, 'totalQty': 0,
                    'prices': [], 'onTimeCount': 0, 'qualityRates': [],
                }
            sp = supplier_pricing[sid]
            sp['orders'] += 1
            sp['totalSpend'] += o['TotalCost']
            sp['totalQty'] += o['Quantity']
            sp['prices'].append(o['UnitPrice'])
            sp['onTimeCount'] += o['OnTime']
            sp['qualityRates'].append(o['QualityPassRate'])

        supplier_pricing_list = []
        for sid, sp in supplier_pricing.items():
            rating = self.supplier_ratings.get(sid, {})
            avg_p = sp['totalSpend'] / sp['totalQty'] if sp['totalQty'] else 0
            supplier_pricing_list.append({
                'id': sid,
                'name': sp['name'],
                'avgPrice': round(avg_p, 2),
                'minPrice': round(min(sp['prices']), 2),
                'maxPrice': round(max(sp['prices']), 2),
                'orderCount': sp['orders'],
                'totalSpend': round(sp['totalSpend'], 2),
                'onTimeRate': round(sp['onTimeCount'] / sp['orders'] * 100, 1),
                'avgQualityPassRate': round(
                    sum(sp['qualityRates']) / len(sp['qualityRates']), 1
                ),
                'qualityScore': rating.get('QualityScore'),
                'complianceScore': rating.get('ComplianceScore'),
                'reliabilityScore': rating.get('ReliabilityScore'),
                'riskTier': rating.get('RiskTier'),
                'certifications': (rating.get('Certifications') or '').split(','),
            })
        supplier_pricing_list.sort(key=lambda x: x['avgPrice'])

        # Price vs benchmark
        price_vs_market = round(
            ((avg_price - benchmark['AvgMarketPrice']) / benchmark['AvgMarketPrice']) * 100, 1
        ) if benchmark['AvgMarketPrice'] else 0

        return {
            'benchmark': {
                'avgMarketPrice': benchmark['AvgMarketPrice'],
                'minPrice': benchmark['MinPrice'],
                'maxPrice': benchmark['MaxPrice'],
                'volatility': benchmark['PriceVolatility'],
            },
            'totalSpend': round(total_spend, 2),
            'orderCount': len(orders),
            'avgUnitPrice': round(avg_price, 2),
            'priceVsMarket': price_vs_market,
            'supplierPricing': supplier_pricing_list,
        }


    def detect_substitution_groups(self):
        """Identify groups of functionally substitutable ingredients.

        Three levels:
        1. DIRECT: Same base ingredient across companies (trivial, already grouped)
        2. VARIANT: Same root ingredient, different modifier
           e.g. soy-lecithin ↔ sunflower-lecithin, organic-stevia ↔ stevia
        3. FUNCTIONAL: Same category, different ingredient
           e.g. different sweeteners, different protein sources
        """
        names = list(self.ingredient_profiles.keys())
        groups = []
        used_in_variant = set()

        # ── Level 2: Variant Detection ──
        # Find ingredients that share a key root token
        root_map = defaultdict(list)  # root_token -> [base_names]
        for bn in names:
            tokens = _tokenize(bn)
            # Remove common modifiers to get the functional root
            modifiers = MODIFIER_TOKENS
            functional_tokens = tokens - modifiers
            # For each significant token, build a reverse index
            for t in functional_tokens:
                if len(t) >= 4:  # skip very short tokens
                    root_map[t].append(bn)

        # Cluster by shared root tokens with high overlap
        variant_clusters = []
        seen = set()
        for bn in names:
            if bn in seen:
                continue
            tokens_a = _tokenize(bn)
            cluster = [bn]
            seen.add(bn)
            for other in names:
                if other in seen:
                    continue
                tokens_b = _tokenize(other)
                sim = _jaccard(tokens_a, tokens_b)
                # High similarity → variant
                if sim >= 0.4 and len(tokens_a & tokens_b) >= 1:
                    # Extra check: same category
                    cat_a = self.ingredient_profiles[bn]['category']
                    cat_b = self.ingredient_profiles[other]['category']
                    if cat_a == cat_b:
                        cluster.append(other)
                        seen.add(other)
            if len(cluster) > 1:
                # Score the cluster
                cats = set(self.ingredient_profiles[c]['category'] for c in cluster)
                total_companies = set()
                total_suppliers = set()
                allergen_diff = set()
                for c in cluster:
                    p = self.ingredient_profiles[c]
                    total_companies.update(p['companyIds'])
                    total_suppliers.update(s['id'] for s in p['suppliers'])
                    for a in p['allergens']:
                        allergen_diff.add(a)
                # ── Compute confidence from data ──
                pair_sims = []
                for i in range(len(cluster)):
                    for j in range(i + 1, len(cluster)):
                        pair_sims.append(_jaccard(_tokenize(cluster[i]),
                                                  _tokenize(cluster[j])))
                avg_sim = sum(pair_sims) / len(pair_sims) if pair_sims else 0.0
                # Supplier overlap between cluster members
                all_sup_ids = set()
                shared_sup = None
                for c in cluster:
                    sids = set(s['id'] for s in self.ingredient_profiles[c]['suppliers'])
                    if shared_sup is None:
                        shared_sup = sids
                    else:
                        shared_sup &= sids
                    all_sup_ids |= sids
                sup_overlap = len(shared_sup or set()) / max(len(all_sup_ids), 1)
                allerg_pen = 0.15 if allergen_diff else 0.0
                variant_conf = self._clamp(
                    avg_sim * 0.6 + 0.25 + sup_overlap * 0.15 - allerg_pen
                )

                variant_clusters.append({
                    'type': 'variant',
                    'members': cluster,
                    'memberNames': [_humanize(c) for c in cluster],
                    'category': self.ingredient_profiles[cluster[0]]['categoryLabel'],
                    'totalCompanies': len(total_companies),
                    'totalSuppliers': len(total_suppliers),
                    'allergenConsiderations': list(allergen_diff),
                    'confidence': round(variant_conf, 4),
                })
                for c in cluster:
                    used_in_variant.add(c)

        # ── Level 3: Functional Groups ──
        # Group remaining ingredients by category
        cat_groups = defaultdict(list)
        for bn, profile in self.ingredient_profiles.items():
            cat_groups[profile['category']].append(bn)

        functional_groups = []
        for cat, members in cat_groups.items():
            if len(members) <= 1 or cat == 'other':
                continue
            # Sub-cluster within category by more specific similarity
            subclusters = self._subcluster_category(members)
            for sc in subclusters:
                if len(sc) <= 1:
                    continue
                total_companies = set()
                total_suppliers = set()
                for c in sc:
                    p = self.ingredient_profiles[c]
                    total_companies.update(p['companyIds'])
                    total_suppliers.update(s['id'] for s in p['suppliers'])
                # ── Computed confidence for functional group ──
                all_sup_f = set()
                shared_sup_f = None
                group_allergens = set()
                for c in sc:
                    p = self.ingredient_profiles[c]
                    sids = set(s['id'] for s in p['suppliers'])
                    if shared_sup_f is None:
                        shared_sup_f = sids
                    else:
                        shared_sup_f &= sids
                    all_sup_f |= sids
                    group_allergens.update(p.get('allergens', []))
                sup_overlap_f = len(shared_sup_f or set()) / max(len(all_sup_f), 1)
                allerg_pen_f = 0.12 * len(group_allergens)
                func_conf = self._clamp(
                    0.30 + 0.05 * min(len(sc), 4)
                    + sup_overlap_f * 0.15 - allerg_pen_f
                )

                functional_groups.append({
                    'type': 'functional',
                    'members': sc,
                    'memberNames': [_humanize(c) for c in sc],
                    'category': FUNCTIONAL_CATEGORIES.get(cat, {}).get('label', cat),
                    'totalCompanies': len(total_companies),
                    'totalSuppliers': len(total_suppliers),
                    'allergenConsiderations': list(group_allergens),
                    'confidence': round(func_conf, 4),
                })

        self.substitution_groups = variant_clusters + functional_groups
        # Sort by impact (total companies)
        self.substitution_groups.sort(key=lambda g: -g['totalCompanies'])

    def _subcluster_category(self, members):
        """Within a category, find meaningful sub-groups."""
        # Use semantic root matching
        subclusters = []
        used = set()

        # First pass: group by shared significant root
        root_groups = defaultdict(list)
        for bn in members:
            tokens = _tokenize(bn)
            modifiers = MODIFIER_TOKENS
            sig_tokens = sorted(tokens - modifiers, key=lambda t: -len(t))
            # Use the longest significant token as root
            root = sig_tokens[0] if sig_tokens else bn
            root_groups[root].append(bn)

        for root, group in root_groups.items():
            if len(group) > 1:
                subclusters.append(group)
                used.update(group)

        # Second pass: merge remaining singletons with similar groups
        for bn in members:
            if bn not in used:
                best_match = None
                best_sim = 0
                tokens_a = _tokenize(bn)
                for sc in subclusters:
                    for member in sc:
                        sim = _jaccard(tokens_a, _tokenize(member))
                        if sim > best_sim:
                            best_sim = sim
                            best_match = sc
                if best_match and best_sim >= 0.25:
                    best_match.append(bn)
                    used.add(bn)

        return subclusters

    # ── Prioritization Framework ────────────────

    @staticmethod
    def _clamp(x):
        return max(0.0, min(1.0, float(x)))

    def _compute_prioritization_dimensions(
        self, profile, company_supplier_map, best_supplier_id, cost_analysis
    ):
        """Compute the 5-dimension prioritization scores for one opportunity.

        All dimensions return a float in [0, 1]; higher is better for the
        recommendation. ``supplier_diversification`` is the monopoly guard —
        it drops fast when the network has very few suppliers for the
        ingredient, regardless of how attractive the numbers look elsewhere.
        """
        total_companies = profile['companyCount']
        covered = sum(
            1 for sids in company_supplier_map.values() if best_supplier_id in sids
        )
        all_suppliers = set()
        for sids in company_supplier_map.values():
            all_suppliers.update(sids)
        current_supplier_count = len(all_suppliers)
        global_supplier_count = profile.get('supplierCount', current_supplier_count)

        # 1) consolidation_benefit: coverage fraction + fragmentation relief
        coverage_ratio = (
            covered / total_companies if total_companies else 0.0
        )
        # Relief from fragmentation: current_supplier_count>=3 is more painful to manage
        fragmentation_relief = self._clamp((current_supplier_count - 1) / 4.0)
        consolidation_benefit = self._clamp(
            0.6 * coverage_ratio + 0.4 * fragmentation_relief
        )

        # 2) evidence_confidence: do we actually have data backing this up?
        # Presence of procurement history + supplier rating = high confidence
        ev = 0.4  # baseline: name/ingredient data exists
        if cost_analysis is not None:
            ev += 0.3  # procurement history backs the cost claim
        best_rating = self.supplier_ratings.get(best_supplier_id)
        if best_rating:
            ev += 0.2  # quality/compliance scores exist
        if profile.get('singleSourceCount', 0) == 0:
            ev += 0.1  # no data gaps in supplier mapping
        evidence_confidence = self._clamp(ev)

        # 3) compliance_fit: best supplier's quality + compliance half-avg.
        # do_not_recommend equivalent: quality<70 → fit near zero.
        if best_rating:
            q = best_rating.get('QualityScore', 0) or 0
            c = best_rating.get('ComplianceScore', 0) or 0
            compliance_fit = self._clamp((q + c) / 200.0)
            if q < 70 or c < 70:
                compliance_fit = min(compliance_fit, 0.35)
        else:
            compliance_fit = 0.5  # neutral when we lack rating data

        # 4) supplier_diversification: NETWORK resilience AFTER consolidation.
        # Key insight: after consolidating 1 supplier serves the network;
        # what matters is how many ALTERNATES remain globally for backup.
        alternates_remaining = max(0, global_supplier_count - 1)
        if global_supplier_count <= 1:
            supplier_diversification = 0.0  # would-be monopoly
        elif global_supplier_count == 2:
            supplier_diversification = 0.25  # one thin backup
        elif global_supplier_count == 3:
            supplier_diversification = 0.55
        else:
            supplier_diversification = self._clamp(alternates_remaining / 4.0 + 0.25)

        # 5) switching_feasibility: reliability + lead time + coverage already there.
        feas = 0.4  # baseline
        if best_rating:
            rel = best_rating.get('ReliabilityScore', 0) or 0
            lead = best_rating.get('LeadTimeDays', 30) or 30
            feas += 0.3 * self._clamp(rel / 100.0)
            feas += 0.15 * self._clamp(1 - (min(lead, 60) / 60.0))
        feas += 0.15 * coverage_ratio  # supplier already serves some companies
        switching_feasibility = self._clamp(feas)

        final_score = self._clamp(
            PRIORITIZATION_WEIGHTS['consolidation_benefit'] * consolidation_benefit
            + PRIORITIZATION_WEIGHTS['evidence_confidence'] * evidence_confidence
            + PRIORITIZATION_WEIGHTS['compliance_fit'] * compliance_fit
            + PRIORITIZATION_WEIGHTS['supplier_diversification'] * supplier_diversification
            + PRIORITIZATION_WEIGHTS['switching_feasibility'] * switching_feasibility
        )

        # Grade mapping with anti-monopoly veto
        if final_score >= GRADE_SAFE_THRESHOLD:
            grade = 'safe_to_consolidate'
        elif final_score <= GRADE_REJECT_THRESHOLD:
            grade = 'not_recommended'
        else:
            grade = 'review_required'

        concentration_risk_downgrade = False
        if (
            grade == 'safe_to_consolidate'
            and supplier_diversification < DIVERSIFICATION_FLOOR
        ):
            grade = 'review_required'
            concentration_risk_downgrade = True

        return {
            'dimensions': {
                'consolidation_benefit': round(consolidation_benefit, 4),
                'evidence_confidence': round(evidence_confidence, 4),
                'compliance_fit': round(compliance_fit, 4),
                'supplier_diversification': round(supplier_diversification, 4),
                'switching_feasibility': round(switching_feasibility, 4),
            },
            'finalScore': round(final_score, 4),
            'grade': grade,
            'concentrationRiskDowngrade': concentration_risk_downgrade,
            'globalSupplierCount': global_supplier_count,
            'alternatesRemaining': alternates_remaining,
        }

    # ── Risk Scoring Dimensions ──────────────────

    def _compute_risk_dimensions(self, risk):
        """Compute 5-dimension scoring for a risk mitigation recommendation.

        Dimensions:
          impact_severity        — magnitude of damage if risk materializes
          evidence_strength      — data backing the risk assessment
          mitigation_feasibility — can we actually address this risk?
          exposure_breadth       — fraction of the network affected
          urgency                — time sensitivity
        """
        total_companies = max(len(self.companies), 1)
        total_ingredients = max(len(self.ingredient_profiles), 1)

        # ── impact_severity ──
        if risk['type'] == 'single_source':
            impact_severity = self._clamp(
                risk['companiesAffected'] / total_companies * 1.5
            )
        elif risk['type'] == 'supplier_concentration':
            impact_severity = self._clamp(
                risk['soleIngredientCount'] / total_ingredients * 3.0
            )
        elif risk['type'] == 'critical_ingredient':
            impact_severity = self._clamp(risk.get('ratio', 4) / 10.0)
        elif risk['type'] == 'supplier_quality':
            impact_severity = self._clamp(
                1.0 - (risk.get('qualityScore', 50) / 100.0)
            )
        elif risk['type'] == 'price_volatility':
            impact_severity = self._clamp(risk.get('volatility', 25) / 50.0)
        else:
            impact_severity = 0.5

        # ── evidence_strength ──
        bn = risk.get('baseName', '')
        profile = self.ingredient_profiles.get(bn) if bn else None

        if risk['type'] == 'supplier_quality':
            evidence_strength = 0.90   # directly measured scores
        elif risk['type'] == 'price_volatility':
            oc = (profile['pricing']['orderCount']
                  if profile and profile.get('pricing') else 0)
            evidence_strength = self._clamp(0.40 + 0.06 * min(oc, 10))
        elif risk['type'] in ('single_source', 'critical_ingredient'):
            ev = 0.55
            if profile and profile.get('pricing'):
                ev += 0.25
            if profile and any(
                s['id'] in self.supplier_ratings
                for s in profile.get('suppliers', [])
            ):
                ev += 0.15
            evidence_strength = self._clamp(ev)
        else:
            evidence_strength = 0.60

        # ── mitigation_feasibility ──
        if risk['type'] == 'single_source':
            alts = self._find_alternative_suppliers(bn)
            mitigation_feasibility = self._clamp(
                0.25 + 0.15 * min(len(alts), 4)
            )
        elif risk['type'] == 'supplier_quality':
            prod_count = risk.get('productsAffected', 0)
            mitigation_feasibility = self._clamp(
                0.50 if prod_count <= 5 else 0.30
            )
        elif risk['type'] == 'price_volatility':
            mitigation_feasibility = 0.55
        else:
            mitigation_feasibility = 0.45

        # ── exposure_breadth ──
        companies_affected = risk.get(
            'companiesAffected', risk.get('companyCount', 0)
        )
        if not companies_affected and risk['type'] == 'supplier_quality':
            sp_prods = self.supplier_to_products.get(
                risk.get('supplierId', -1), []
            )
            cos = set()
            for pid in sp_prods:
                p = self.products.get(pid)
                if p:
                    cos.add(p['CompanyId'])
            companies_affected = len(cos)
        exposure_breadth = self._clamp(companies_affected / total_companies)

        # ── urgency ──
        if risk.get('severity') == 'high':
            urgency = 0.85
        elif risk.get('severity') == 'medium':
            urgency = 0.55
        else:
            urgency = 0.30

        dims = {
            'impact_severity': round(self._clamp(impact_severity), 4),
            'evidence_strength': round(self._clamp(evidence_strength), 4),
            'mitigation_feasibility': round(self._clamp(mitigation_feasibility), 4),
            'exposure_breadth': round(self._clamp(exposure_breadth), 4),
            'urgency': round(self._clamp(urgency), 4),
        }

        final_score = self._clamp(
            sum(RISK_WEIGHTS[k] * dims[k] for k in RISK_WEIGHTS)
        )

        if final_score >= GRADE_SAFE_THRESHOLD:
            grade = 'recommended'
        elif final_score <= GRADE_REJECT_THRESHOLD:
            grade = 'not_recommended'
        else:
            grade = 'review_required'

        return {
            'dimensions': dims,
            'finalScore': round(final_score, 4),
            'grade': grade,
        }

    # ── Substitution Scoring Dimensions ────────

    def _compute_substitution_dimensions(self, group, best_member, best_profile):
        """Compute 5-dimension scoring for a substitution recommendation.

        Dimensions:
          similarity_score          — how similar are the ingredients?
          evidence_strength         — data backing the substitution claim
          compliance_compatibility  — allergen / quality-flag compatibility
          network_benefit           — supply-chain improvement from standardising
          switching_feasibility     — how easy to implement the switch
        """
        total_companies = max(len(self.companies), 1)
        members = group['members']
        member_profiles = [self.ingredient_profiles[m] for m in members]

        # ── similarity_score ──
        if group['type'] == 'variant':
            pair_sims = []
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    pair_sims.append(
                        _jaccard(_tokenize(members[i]), _tokenize(members[j]))
                    )
            avg_sim = sum(pair_sims) / len(pair_sims) if pair_sims else 0.0
            cats = set(p['category'] for p in member_profiles)
            similarity_score = self._clamp(
                avg_sim + (0.10 if len(cats) == 1 else 0.0)
            )
        else:
            similarity_score = self._clamp(
                0.30 + 0.04 * min(len(members), 5)
            )

        # ── evidence_strength ──
        ev = 0.35
        has_pricing = sum(1 for p in member_profiles if p.get('pricing'))
        ev += 0.25 * (has_pricing / len(member_profiles))
        rated_sups = sum(
            1 for p in member_profiles for s in p['suppliers']
            if s['id'] in self.supplier_ratings
        )
        total_sups = max(
            sum(len(p['suppliers']) for p in member_profiles), 1
        )
        ev += 0.20 * (rated_sups / total_sups)
        if group['totalSuppliers'] >= 3:
            ev += 0.10
        if len(members) >= 3:
            ev += 0.10
        evidence_strength = self._clamp(ev)

        # ── compliance_compatibility ──
        allergen_count = len(group.get('allergenConsiderations', []))
        member_flags = [
            set(p.get('qualityFlags', [])) for p in member_profiles
        ]
        if member_flags and any(member_flags):
            all_flags = set()
            for f in member_flags:
                all_flags |= f
            common_flags = member_flags[0].copy()
            for f in member_flags[1:]:
                common_flags &= f
            flag_agreement = (
                len(common_flags) / max(len(all_flags), 1)
            )
        else:
            flag_agreement = 1.0
        compliance_compatibility = self._clamp(
            0.65 + 0.35 * flag_agreement - 0.10 * allergen_count
        )

        # ── network_benefit ──
        network_benefit = self._clamp(
            0.25 * (group['totalCompanies'] / total_companies)
            + 0.35 * self._clamp(group['totalSuppliers'] / 5.0)
            + 0.40 * (best_profile['companyCount']
                       / max(group['totalCompanies'], 1))
        )

        # ── switching_feasibility ──
        best_share = (
            best_profile['companyCount'] / max(group['totalCompanies'], 1)
        )
        type_bonus = 0.15 if group['type'] == 'variant' else 0.0
        switching_feasibility = self._clamp(
            0.30 + 0.50 * best_share + type_bonus
        )

        dims = {
            'similarity_score': round(similarity_score, 4),
            'evidence_strength': round(evidence_strength, 4),
            'compliance_compatibility': round(compliance_compatibility, 4),
            'network_benefit': round(network_benefit, 4),
            'switching_feasibility': round(switching_feasibility, 4),
        }

        final_score = self._clamp(
            sum(SUBSTITUTION_WEIGHTS[k] * dims[k]
                for k in SUBSTITUTION_WEIGHTS)
        )

        if final_score >= GRADE_SAFE_THRESHOLD:
            grade = 'recommended'
        elif final_score <= GRADE_REJECT_THRESHOLD:
            grade = 'not_recommended'
        else:
            grade = 'review_required'

        return {
            'dimensions': dims,
            'finalScore': round(final_score, 4),
            'grade': grade,
        }

    # ── Cost-Optimisation Scoring Dimensions ───

    def _compute_cost_dimensions(self, profile, cheapest, most_expensive,
                                  spread_pct, pricing):
        """Compute 5-dimension scoring for a cost optimisation recommendation.

        Dimensions:
          savings_magnitude    — size of the cost-reduction opportunity
          evidence_strength    — robustness of the price data
          quality_assurance    — does the cheaper supplier maintain quality?
          supplier_reliability — reliable delivery / on-time record
          implementation_ease  — how actionable the switch is
        """
        # ── savings_magnitude ──
        savings_magnitude = self._clamp(spread_pct / 50.0)

        # ── evidence_strength ──
        order_count = pricing.get('orderCount', 0)
        ev = 0.25
        ev += 0.25 * self._clamp(order_count / 20.0)
        ev += 0.15 * (1.0 if cheapest.get('qualityScore') else 0.0)
        ev += 0.15 * (1.0 if pricing.get('benchmark') else 0.0)
        ev += 0.10 * self._clamp(
            len(pricing.get('supplierPricing', [])) / 4.0
        )
        ev += 0.10 * self._clamp(cheapest.get('orderCount', 0) / 5.0)
        evidence_strength = self._clamp(ev)

        # ── quality_assurance ──
        q = (cheapest.get('qualityScore') or 0) / 100.0
        c = (cheapest.get('complianceScore') or 0) / 100.0
        quality_assurance = self._clamp((q + c) / 2.0)

        # ── supplier_reliability ──
        rel = ((cheapest.get('reliabilityScore') or 0) / 100.0
               if cheapest.get('reliabilityScore') else 0.0)
        on_time = cheapest.get('onTimeRate', 0) / 100.0
        supplier_reliability = self._clamp(0.5 * rel + 0.5 * on_time)

        # ── implementation_ease ──
        impl = 0.40
        if cheapest.get('orderCount', 0) >= 3:
            impl += 0.25
        if pricing.get('benchmark'):
            impl += 0.15
        impl += 0.10 * self._clamp(profile['supplierCount'] / 4.0)
        impl += 0.10 * self._clamp(profile['companyCount'] / 5.0)
        implementation_ease = self._clamp(impl)

        dims = {
            'savings_magnitude': round(savings_magnitude, 4),
            'evidence_strength': round(evidence_strength, 4),
            'quality_assurance': round(quality_assurance, 4),
            'supplier_reliability': round(supplier_reliability, 4),
            'implementation_ease': round(implementation_ease, 4),
        }

        final_score = self._clamp(
            sum(COST_WEIGHTS[k] * dims[k] for k in COST_WEIGHTS)
        )

        if final_score >= GRADE_SAFE_THRESHOLD:
            grade = 'recommended'
        elif final_score <= GRADE_REJECT_THRESHOLD:
            grade = 'not_recommended'
        else:
            grade = 'review_required'

        return {
            'dimensions': dims,
            'finalScore': round(final_score, 4),
            'grade': grade,
        }

    # ── Consolidation Analysis ─────────────────

    def analyze_consolidation(self):
        """Identify consolidation opportunities across companies."""
        opportunities = []

        for bn, profile in self.ingredient_profiles.items():
            if profile['companyCount'] < 2:
                continue  # No consolidation possible for single-company ingredients

            # Analyze current sourcing fragmentation
            # For each company, which suppliers do they use for this ingredient?
            company_supplier_map = defaultdict(set)
            for pid in profile['productIds']:
                cid = self.products[pid]['CompanyId']
                for sid in self.product_suppliers.get(pid, []):
                    company_supplier_map[cid].add(sid)

            all_suppliers_used = set()
            for sids in company_supplier_map.values():
                all_suppliers_used.update(sids)

            if len(all_suppliers_used) <= 1:
                # Already consolidated on 1 supplier
                continue

            # Find the "best" supplier (serves most companies already)
            supplier_coverage = {}
            for sid in all_suppliers_used:
                cos = sum(1 for cid, sids in company_supplier_map.items() if sid in sids)
                supplier_coverage[sid] = cos
            best_supplier_id = max(supplier_coverage, key=supplier_coverage.get)
            best_supplier_name = self.suppliers[best_supplier_id]['Name']
            best_coverage = supplier_coverage[best_supplier_id]

            # Companies NOT currently using the best supplier
            non_covered = [cid for cid, sids in company_supplier_map.items()
                           if best_supplier_id not in sids]

            # Impact score: companies × suppliers that could be reduced
            impact = profile['companyCount'] * len(all_suppliers_used)

            # Add cost analysis from procurement data
            cost_analysis = self._consolidation_cost_analysis(
                bn, profile, company_supplier_map, best_supplier_id
            )

            # Prioritization Framework: compute 5-dimension scores + grade
            prioritization = self._compute_prioritization_dimensions(
                profile, company_supplier_map, best_supplier_id, cost_analysis
            )

            opportunities.append({
                'ingredientName': profile['name'],
                'baseName': bn,
                'category': profile['categoryLabel'],
                'companyCount': profile['companyCount'],
                'currentSupplierCount': len(all_suppliers_used),
                'currentSuppliers': [
                    {'id': sid, 'name': self.suppliers[sid]['Name'],
                     'coverage': supplier_coverage[sid]}
                    for sid in sorted(all_suppliers_used,
                                      key=lambda s: -supplier_coverage[s])
                ],
                'recommendedSupplierId': best_supplier_id,
                'recommendedSupplierName': best_supplier_name,
                'currentCoverage': best_coverage,
                'additionalCompanies': len(non_covered),
                'additionalCompanyNames': [
                    self.companies[cid]['Name'] for cid in non_covered
                ],
                'impactScore': impact,
                'costAnalysis': cost_analysis,
                'prioritization': prioritization,
                'finalScore': prioritization['finalScore'],
                'grade': prioritization['grade'],
                'concentrationRiskDowngrade': prioritization[
                    'concentrationRiskDowngrade'
                ],
                'evidence': self._build_consolidation_evidence(
                    bn, profile, company_supplier_map, best_supplier_id,
                    prioritization=prioritization,
                ),
            })

        # Sort by the framework's final score (primary) with impact as tiebreaker,
        # so demo-visible "top" opportunities reflect the prioritization pitch.
        opportunities.sort(key=lambda x: (-x['finalScore'], -x['impactScore']))
        self.consolidation_opportunities = opportunities

    def _consolidation_cost_analysis(self, bn, profile, company_supplier_map,
                                      best_supplier_id):
        """Compute cost savings potential from consolidation."""
        pricing = profile.get('pricing')
        if not pricing or not pricing.get('supplierPricing'):
            return None

        sp_map = {s['id']: s for s in pricing['supplierPricing']}
        best_sp = sp_map.get(best_supplier_id)
        if not best_sp:
            return None

        # Current weighted average price across all suppliers
        current_total_spend = pricing['totalSpend']
        current_avg_price = pricing['avgUnitPrice']

        # If consolidated to best supplier: estimate savings
        best_price = best_sp['avgPrice']
        total_qty = sum(s['totalSpend'] / s['avgPrice']
                        for s in pricing['supplierPricing'] if s['avgPrice'] > 0)
        estimated_consolidated_spend = total_qty * best_price
        savings = current_total_spend - estimated_consolidated_spend
        savings_pct = (savings / current_total_spend * 100) if current_total_spend else 0

        # Quality comparison
        best_quality = best_sp.get('qualityScore', 0)
        best_compliance = best_sp.get('complianceScore', 0)
        best_reliability = best_sp.get('reliabilityScore', 0)
        best_risk = best_sp.get('riskTier', 'unknown')

        return {
            'currentTotalSpend': round(current_total_spend, 2),
            'currentAvgPrice': round(current_avg_price, 2),
            'consolidatedPrice': round(best_price, 2),
            'estimatedSavings': round(savings, 2),
            'savingsPercent': round(savings_pct, 1),
            'bestSupplierQuality': best_quality,
            'bestSupplierCompliance': best_compliance,
            'bestSupplierReliability': best_reliability,
            'bestSupplierRisk': best_risk,
        }

    def _build_consolidation_evidence(self, bn, profile, company_supplier_map,
                                       best_supplier_id, prioritization=None):
        """Build an evidence trail for a consolidation recommendation."""
        best_name = self.suppliers[best_supplier_id]['Name']
        total_cos = profile['companyCount']
        covered = sum(1 for sids in company_supplier_map.values()
                      if best_supplier_id in sids)
        evidence = []
        evidence.append(
            f"{profile['name']} is used by {total_cos} companies, "
            f"creating consolidation potential."
        )
        evidence.append(
            f"{best_name} already serves {covered}/{total_cos} companies "
            f"for this ingredient."
        )
        if prioritization:
            dims = prioritization['dimensions']
            evidence.append(
                f"Prioritization Framework score {prioritization['finalScore']:.2f} "
                f"(grade: {prioritization['grade']}): "
                f"leverage {dims['consolidation_benefit']:.2f}, "
                f"evidence {dims['evidence_confidence']:.2f}, "
                f"compliance {dims['compliance_fit']:.2f}, "
                f"diversification {dims['supplier_diversification']:.2f}, "
                f"switching {dims['switching_feasibility']:.2f}."
            )
            if prioritization['concentrationRiskDowngrade']:
                evidence.append(
                    "⚠️ Concentration-risk veto: the network has ≤2 suppliers "
                    "for this ingredient, so full consolidation would create "
                    "single-source risk. Recommendation downgraded to "
                    "review_required — consolidate MOST volume but keep "
                    "a qualified backup supplier."
                )
        if profile['singleSourceCount'] > 0:
            evidence.append(
                f"⚠️ {profile['singleSourceCount']} product SKU(s) have only "
                f"one linked supplier — consolidation should maintain backup options."
            )
        if profile['allergens']:
            evidence.append(
                f"Allergen considerations: {', '.join(profile['allergens'])}. "
                f"Any substitution must preserve allergen labeling compliance."
            )
        # Add cost evidence if available
        pricing = profile.get('pricing')
        if pricing and pricing.get('totalSpend'):
            evidence.append(
                f"Historical spend on this ingredient: ${pricing['totalSpend']:,.0f} "
                f"across {pricing['orderCount']} orders (avg ${pricing['avgUnitPrice']:.2f}/kg)."
            )
            if pricing.get('priceVsMarket'):
                direction = 'above' if pricing['priceVsMarket'] > 0 else 'below'
                evidence.append(
                    f"Current avg price is {abs(pricing['priceVsMarket']):.1f}% "
                    f"{direction} market benchmark (${pricing['benchmark']['avgMarketPrice']:.2f}/kg)."
                )
        return evidence

    # ── Risk Assessment ────────────────────────

    def assess_risks(self):
        """Identify supply chain risk factors."""
        risks = []

        # 1. Single-source ingredients (only 1 global supplier)
        for bn, profile in self.ingredient_profiles.items():
            if profile['supplierCount'] == 1 and profile['companyCount'] >= 2:
                risks.append({
                    'type': 'single_source',
                    'severity': 'high',
                    'ingredientName': profile['name'],
                    'baseName': bn,
                    'category': profile['categoryLabel'],
                    'supplierName': profile['suppliers'][0]['name'],
                    'companiesAffected': profile['companyCount'],
                    'productsAffected': profile['productCount'],
                    'description': (
                        f"{profile['name']} is supplied by only "
                        f"{profile['suppliers'][0]['name']}, affecting "
                        f"{profile['companyCount']} companies and "
                        f"{profile['productCount']} products."
                    ),
                    'recommendation': (
                        f"Qualify at least one additional supplier for "
                        f"{profile['name']} to mitigate single-source risk."
                    ),
                })

        # 2. Supplier concentration — suppliers that dominate many ingredients
        supplier_dominance = defaultdict(lambda: {'sole_ingredients': [], 'total_ingredients': 0})
        for bn, profile in self.ingredient_profiles.items():
            for s in profile['suppliers']:
                supplier_dominance[s['id']]['total_ingredients'] += 1
                if profile['supplierCount'] == 1:
                    supplier_dominance[s['id']]['sole_ingredients'].append(bn)

        for sid, dom in supplier_dominance.items():
            sole_count = len(dom['sole_ingredients'])
            if sole_count >= 3:
                risks.append({
                    'type': 'supplier_concentration',
                    'severity': 'high' if sole_count >= 10 else 'medium',
                    'supplierName': self.suppliers[sid]['Name'],
                    'supplierId': sid,
                    'soleIngredientCount': sole_count,
                    'totalIngredientCount': dom['total_ingredients'],
                    'soleIngredients': [_humanize(bn) for bn in dom['sole_ingredients'][:10]],
                    'description': (
                        f"{self.suppliers[sid]['Name']} is the sole supplier for "
                        f"{sole_count} ingredients. Loss of this supplier would "
                        f"create critical shortages."
                    ),
                    'recommendation': (
                        f"Develop alternative supplier relationships for the "
                        f"{sole_count} ingredients solely sourced from "
                        f"{self.suppliers[sid]['Name']}."
                    ),
                })

        # 3. Critical ingredient risk — used by many companies with few suppliers
        for bn, profile in self.ingredient_profiles.items():
            if profile['companyCount'] >= 5 and profile['supplierCount'] <= 2:
                ratio = profile['companyCount'] / max(profile['supplierCount'], 1)
                if ratio >= 4:
                    risks.append({
                        'type': 'critical_ingredient',
                        'severity': 'medium',
                        'ingredientName': profile['name'],
                        'baseName': bn,
                        'companyCount': profile['companyCount'],
                        'supplierCount': profile['supplierCount'],
                        'ratio': round(ratio, 1),
                        'description': (
                            f"{profile['name']} serves {profile['companyCount']} companies "
                            f"but has only {profile['supplierCount']} supplier(s). "
                            f"Demand/supply ratio: {ratio:.1f}x"
                        ),
                        'recommendation': (
                            f"Qualify additional suppliers for {profile['name']} "
                            f"to better balance demand across {profile['companyCount']} companies."
                        ),
                    })

        # 4. High-risk supplier quality — suppliers with low quality/compliance scores
        for sid, rating in self.supplier_ratings.items():
            if rating['RiskTier'] == 'high' or rating['QualityScore'] < 80:
                prod_count = len(self.supplier_to_products.get(sid, []))
                if prod_count > 0:
                    risks.append({
                        'type': 'supplier_quality',
                        'severity': 'high' if rating['QualityScore'] < 70 else 'medium',
                        'supplierName': self.suppliers[sid]['Name'],
                        'supplierId': sid,
                        'qualityScore': rating['QualityScore'],
                        'complianceScore': rating['ComplianceScore'],
                        'reliabilityScore': rating['ReliabilityScore'],
                        'riskTier': rating['RiskTier'],
                        'productsAffected': prod_count,
                        'description': (
                            f"{self.suppliers[sid]['Name']} has a quality score of "
                            f"{rating['QualityScore']:.0f}/100 and compliance score of "
                            f"{rating['ComplianceScore']:.0f}/100, classified as "
                            f"{rating['RiskTier']}-risk. Supplies {prod_count} products."
                        ),
                        'recommendation': (
                            f"Conduct quality audit of {self.suppliers[sid]['Name']} "
                            f"and develop contingency sourcing for their "
                            f"{prod_count} supplied products."
                        ),
                    })

        # 5. Price volatility risk — ingredients with high price swings
        for bn, profile in self.ingredient_profiles.items():
            pricing = profile.get('pricing')
            if pricing and pricing.get('benchmark'):
                vol = pricing['benchmark']['volatility']
                if vol >= 0.25 and profile['companyCount'] >= 3:
                    risks.append({
                        'type': 'price_volatility',
                        'severity': 'medium',
                        'ingredientName': profile['name'],
                        'baseName': bn,
                        'volatility': round(vol * 100, 1),
                        'companyCount': profile['companyCount'],
                        'avgPrice': pricing.get('avgUnitPrice', 0),
                        'description': (
                            f"{profile['name']} has {vol*100:.0f}% price volatility "
                            f"affecting {profile['companyCount']} companies. "
                            f"Avg price: ${pricing.get('avgUnitPrice', 0):.2f}/kg."
                        ),
                        'recommendation': (
                            f"Consider long-term contracts or hedging for "
                            f"{profile['name']} to stabilize costs."
                        ),
                    })

        risks.sort(key=lambda r: (0 if r['severity'] == 'high' else 1, -r.get('companiesAffected', 0)))
        self.risk_items = risks

    # ── Recommendations ────────────────────────

    def generate_recommendations(self):
        """Generate prioritized sourcing recommendations with evidence trails."""
        recs = []
        rec_id = 0

        # Recommendation Type 1: Consolidation plays
        # Ranked by the Prioritization Framework's final score, not raw impact.
        for opp in self.consolidation_opportunities[:30]:
            rec_id += 1
            grade = opp.get('grade', 'review_required')
            # Priority uses the framework grade first; falls back to volume heuristic
            if grade == 'not_recommended':
                priority = 'low'
            elif grade == 'safe_to_consolidate':
                priority = 'high' if opp['companyCount'] >= 3 else 'medium'
            else:  # review_required
                priority = 'medium' if opp['companyCount'] >= 5 else 'low'

            downgrade = opp.get('concentrationRiskDowngrade', False)
            if downgrade:
                title = (
                    f"Partial consolidation of {opp['ingredientName']} — "
                    f"lead with {opp['recommendedSupplierName']}, keep a backup"
                )
                summary = (
                    f"{opp['ingredientName']} is purchased by {opp['companyCount']} "
                    f"companies from {opp['currentSupplierCount']} suppliers, but the "
                    f"network has ≤2 global suppliers. Full consolidation would "
                    f"create single-source risk, so Agnes recommends shifting most "
                    f"volume to {opp['recommendedSupplierName']} while qualifying "
                    f"at least one backup — maximum leverage with minimum "
                    f"concentration risk."
                )
            else:
                title = (
                    f"Consolidate {opp['ingredientName']} to "
                    f"{opp['recommendedSupplierName']}"
                )
                summary = (
                    f"{opp['ingredientName']} is purchased by {opp['companyCount']} "
                    f"companies from {opp['currentSupplierCount']} suppliers. "
                    f"The Prioritization Framework scores this "
                    f"{opp['finalScore']:.2f} ({grade}): consolidating to "
                    f"{opp['recommendedSupplierName']} aggregates buying volume "
                    f"while leaving "
                    f"{opp['prioritization']['alternatesRemaining']} supplier(s) "
                    f"as backup across the network."
                )

            recs.append({
                'id': rec_id,
                'type': 'consolidation',
                'priority': priority,
                'grade': grade,
                'finalScore': opp['finalScore'],
                'dimensions': opp['prioritization']['dimensions'],
                'dimensionLabels': DIMENSION_LABELS['consolidation'],
                'concentrationRiskDowngrade': downgrade,
                'title': title,
                'summary': summary,
                'impact': {
                    'companiesAffected': opp['companyCount'],
                    'suppliersReduced': opp['currentSupplierCount'] - 1,
                    'volumeAggregation': (
                        f"{opp['companyCount']} companies → "
                        f"1 primary + {opp['prioritization']['alternatesRemaining']} "
                        f"backup" if downgrade
                        else f"{opp['companyCount']} companies → 1 consolidated order"
                    ),
                },
                'evidence': opp['evidence'],
                'caveats': self._build_caveats(opp['baseName']),
                'confidence': round(
                    opp['prioritization']['dimensions']['evidence_confidence'], 2
                ),
                'baseName': opp['baseName'],
            })

        # Recommendation Type 2: Risk mitigation
        for risk in self.risk_items:
            if risk['type'] in ('single_source', 'supplier_quality'):
                rec_id += 1
                bn = risk.get('baseName', '')
                alt_suppliers = (
                    self._find_alternative_suppliers(bn) if bn else []
                )

                # ── Compute framework dimensions ──
                risk_fw = self._compute_risk_dimensions(risk)
                dims = risk_fw['dimensions']

                # Priority derived from framework grade
                if risk_fw['grade'] == 'recommended':
                    priority = 'high'
                elif risk_fw['grade'] == 'review_required':
                    priority = 'medium'
                else:
                    priority = 'low'

                # Confidence = evidence-strength dimension
                confidence = round(dims['evidence_strength'], 2)

                # ── Rich evidence trail ──
                evidence = [risk['description']]
                if risk['type'] == 'single_source':
                    evidence.append(
                        f"Current sole supplier: {risk['supplierName']}"
                    )
                    evidence.append(
                        f"Impact scope: {risk['companiesAffected']} companies, "
                        f"{risk['productsAffected']} products affected"
                    )
                    if alt_suppliers:
                        evidence.append(
                            f"Alternative suppliers identified from related "
                            f"ingredients: "
                            f"{', '.join(s['name'] for s in alt_suppliers[:3])}"
                        )
                    else:
                        evidence.append(
                            "No alternative suppliers found in current "
                            "database — external sourcing research recommended"
                        )
                elif risk['type'] == 'supplier_quality':
                    evidence.append(
                        f"Quality score: {risk['qualityScore']:.0f}/100, "
                        f"Compliance: {risk['complianceScore']:.0f}/100, "
                        f"Risk tier: {risk['riskTier']}"
                    )
                    evidence.append(
                        f"Products affected: {risk['productsAffected']}"
                    )
                evidence.append(
                    f"Framework score: {risk_fw['finalScore']:.2f} — "
                    f"impact {dims['impact_severity']:.2f}, "
                    f"evidence {dims['evidence_strength']:.2f}, "
                    f"feasibility {dims['mitigation_feasibility']:.2f}, "
                    f"exposure {dims['exposure_breadth']:.2f}, "
                    f"urgency {dims['urgency']:.2f}"
                )

                title = (
                    f"Qualify second supplier for {risk['ingredientName']}"
                    if risk['type'] == 'single_source'
                    else f"Address quality risk: {risk['supplierName']}"
                )

                recs.append({
                    'id': rec_id,
                    'type': 'risk_mitigation',
                    'priority': priority,
                    'grade': risk_fw['grade'],
                    'finalScore': risk_fw['finalScore'],
                    'dimensions': dims,
                    'dimensionLabels': DIMENSION_LABELS['risk_mitigation'],
                    'title': title,
                    'summary': risk['description'],
                    'impact': {
                        'companiesAffected': risk.get(
                            'companiesAffected',
                            risk.get('productsAffected', 0)
                        ),
                        'productsAffected': risk.get('productsAffected', 0),
                        'riskReduction': (
                            'Eliminates single-source dependency'
                            if risk['type'] == 'single_source'
                            else 'Reduces quality-risk exposure'
                        ),
                    },
                    'evidence': evidence,
                    'caveats': [
                        "New supplier must be qualified for GMP compliance",
                        "Formulation testing required before switching",
                        "Lead time and minimum order quantities need assessment",
                    ],
                    'alternativeSuppliers': alt_suppliers[:5],
                    'confidence': confidence,
                    'baseName': bn,
                })

        # Recommendation Type 3: Substitution opportunities
        for group in self.substitution_groups[:30]:
            if group['totalCompanies'] >= 2:
                rec_id += 1
                best_member = max(
                    group['members'],
                    key=lambda m: self.ingredient_profiles[m]['companyCount'],
                )
                best_profile = self.ingredient_profiles[best_member]

                # ── Compute framework dimensions ──
                sub_fw = self._compute_substitution_dimensions(
                    group, best_member, best_profile
                )
                dims = sub_fw['dimensions']

                # Priority from framework
                if sub_fw['grade'] == 'recommended':
                    priority = (
                        'high' if group['totalCompanies'] >= 4 else 'medium'
                    )
                elif sub_fw['grade'] == 'review_required':
                    priority = 'medium'
                else:
                    priority = 'low'

                confidence = round(dims['evidence_strength'], 2)

                # ── Enriched evidence trail ──
                gtype = ('Variant' if group['type'] == 'variant'
                         else 'Functional')
                evidence = [
                    f"{gtype} substitution group: "
                    f"{', '.join(group['memberNames'])}",
                    f"Category: {group['category']}",
                    f"{best_profile['name']} is the most widely used "
                    f"({best_profile['companyCount']} companies, "
                    f"{best_profile['supplierCount']} suppliers)",
                    f"Combined supplier pool: {group['totalSuppliers']} "
                    f"suppliers across {group['totalCompanies']} companies",
                ]
                if group['type'] == 'variant':
                    evidence.append(
                        "Members share high name similarity — likely the "
                        "same ingredient in different forms or specifications"
                    )
                if group.get('allergenConsiderations'):
                    evidence.append(
                        f"Allergen considerations: "
                        f"{', '.join(group['allergenConsiderations'])} "
                        f"— substitution requires allergen-labeling review"
                    )
                evidence.append(
                    f"Framework score: {sub_fw['finalScore']:.2f} — "
                    f"similarity {dims['similarity_score']:.2f}, "
                    f"evidence {dims['evidence_strength']:.2f}, "
                    f"compliance {dims['compliance_compatibility']:.2f}, "
                    f"network {dims['network_benefit']:.2f}, "
                    f"switching {dims['switching_feasibility']:.2f}"
                )

                recs.append({
                    'id': rec_id,
                    'type': 'substitution',
                    'priority': priority,
                    'grade': sub_fw['grade'],
                    'finalScore': sub_fw['finalScore'],
                    'dimensions': dims,
                    'dimensionLabels': DIMENSION_LABELS['substitution'],
                    'title': (
                        f"Standardize on {best_profile['name']} across "
                        f"{'variants' if group['type'] == 'variant' else 'alternatives'}"
                    ),
                    'summary': (
                        f"{len(group['members'])} "
                        f"{'variants' if group['type'] == 'variant' else 'alternatives'} "
                        f"of this ingredient: {', '.join(group['memberNames'])}. "
                        f"Standardizing on {best_profile['name']} could consolidate "
                        f"demand across {group['totalCompanies']} companies. "
                        f"Framework scores this {sub_fw['finalScore']:.2f} "
                        f"({sub_fw['grade'].replace('_', ' ')})."
                    ),
                    'impact': {
                        'companiesAffected': group['totalCompanies'],
                        'variantsReduced': len(group['members']) - 1,
                        'consolidationPotential': (
                            f"{len(group['members'])} variants → 1 standard"
                        ),
                    },
                    'evidence': evidence,
                    'caveats': self._build_substitution_caveats(group),
                    'confidence': confidence,
                    'substitutionGroup': group,
                })

        # Recommendation Type 4: Cost optimization — switch to cheaper supplier
        for bn, profile in self.ingredient_profiles.items():
            pricing = profile.get('pricing')
            if (not pricing or not pricing.get('supplierPricing')
                    or len(pricing['supplierPricing']) < 2):
                continue
            sp_list = pricing['supplierPricing']
            cheapest = sp_list[0]               # already sorted by avgPrice
            most_expensive = sp_list[-1]
            if cheapest['avgPrice'] <= 0 or most_expensive['avgPrice'] <= 0:
                continue
            spread_pct = ((most_expensive['avgPrice'] - cheapest['avgPrice'])
                          / most_expensive['avgPrice'] * 100)
            # Gate: minimum spread and quality / compliance floor
            if spread_pct < 15:
                continue
            if (cheapest.get('qualityScore') or 0) < 75:
                continue
            if (cheapest.get('complianceScore') or 0) < 75:
                continue

            rec_id += 1

            # ── Compute framework dimensions ──
            cost_fw = self._compute_cost_dimensions(
                profile, cheapest, most_expensive, spread_pct, pricing
            )
            dims = cost_fw['dimensions']

            # Priority from framework
            if cost_fw['grade'] == 'recommended':
                priority = 'high' if spread_pct >= 30 else 'medium'
            elif cost_fw['grade'] == 'review_required':
                priority = 'medium'
            else:
                priority = 'low'

            confidence = round(dims['evidence_strength'], 2)

            est_savings = (most_expensive['avgPrice'] - cheapest['avgPrice']) * (
                pricing['totalSpend'] / pricing['avgUnitPrice']
            ) * 0.5  # conservative: 50% volume shift

            # ── Rich evidence trail ──
            evidence = [
                f"Cheapest supplier: {cheapest['name']} at "
                f"${cheapest['avgPrice']:.2f}/kg "
                f"(quality: {cheapest.get('qualityScore', 'N/A')}/100, "
                f"compliance: {cheapest.get('complianceScore', 'N/A')}/100, "
                f"on-time: {cheapest['onTimeRate']:.0f}%, "
                f"based on {cheapest.get('orderCount', 0)} orders)",
                f"Most expensive: {most_expensive['name']} at "
                f"${most_expensive['avgPrice']:.2f}/kg "
                f"({most_expensive.get('orderCount', 0)} orders)",
                f"Price spread: {spread_pct:.1f}% — estimated savings of "
                f"${est_savings:,.0f} on 50% volume shift",
                f"Market benchmark: "
                f"${pricing['benchmark']['avgMarketPrice']:.2f}/kg "
                f"(volatility: "
                f"{pricing['benchmark']['volatility']*100:.0f}%)",
                f"Total historical spend: ${pricing['totalSpend']:,.0f} "
                f"across {pricing['orderCount']} orders",
                f"Framework score: {cost_fw['finalScore']:.2f} — "
                f"savings {dims['savings_magnitude']:.2f}, "
                f"evidence {dims['evidence_strength']:.2f}, "
                f"quality {dims['quality_assurance']:.2f}, "
                f"reliability {dims['supplier_reliability']:.2f}, "
                f"ease {dims['implementation_ease']:.2f}",
            ]

            recs.append({
                'id': rec_id,
                'type': 'cost_optimization',
                'priority': priority,
                'grade': cost_fw['grade'],
                'finalScore': cost_fw['finalScore'],
                'dimensions': dims,
                'dimensionLabels': DIMENSION_LABELS['cost_optimization'],
                'title': (
                    f"Switch {profile['name']} sourcing to "
                    f"{cheapest['name']} for cost savings"
                ),
                'summary': (
                    f"{profile['name']} shows a {spread_pct:.0f}% price "
                    f"spread across suppliers. {cheapest['name']} offers "
                    f"${cheapest['avgPrice']:.2f}/kg vs "
                    f"${most_expensive['avgPrice']:.2f}/kg "
                    f"({most_expensive['name']}). Framework scores this "
                    f"{cost_fw['finalScore']:.2f} "
                    f"({cost_fw['grade'].replace('_', ' ')}), "
                    f"quality {cheapest.get('qualityScore', 'N/A')}/100."
                ),
                'impact': {
                    'companiesAffected': profile['companyCount'],
                    'estimatedSavings': f"${est_savings:,.0f}",
                    'priceReduction': f"{spread_pct:.0f}%",
                },
                'evidence': evidence,
                'caveats': self._build_caveats(bn) + [
                    "Price comparison based on historical procurement data",
                    "Volume discounts may change effective pricing at "
                    "consolidated quantities",
                ],
                'confidence': confidence,
                'baseName': bn,
            })

        recs.sort(key=lambda r: (
            {'high': 0, 'medium': 1, 'low': 2}.get(r['priority'], 3),
            -r.get('confidence', 0),
            -r['impact'].get('companiesAffected', 0),
        ))
        self.recommendations = recs

    def _find_alternative_suppliers(self, base_name):
        """Find suppliers that provide functionally similar ingredients."""
        profile = self.ingredient_profiles.get(base_name)
        if not profile:
            return []
        current_supplier_ids = set(s['id'] for s in profile['suppliers'])

        # Look in substitution groups
        alts = []
        for group in self.substitution_groups:
            if base_name in group['members']:
                for member in group['members']:
                    if member == base_name:
                        continue
                    mp = self.ingredient_profiles[member]
                    for s in mp['suppliers']:
                        if s['id'] not in current_supplier_ids:
                            alts.append({
                                'id': s['id'],
                                'name': s['name'],
                                'viaIngredient': mp['name'],
                                'confidence': group['confidence'],
                            })
        # Deduplicate
        seen = set()
        unique = []
        for a in alts:
            if a['id'] not in seen:
                seen.add(a['id'])
                unique.append(a)
        return unique

    def _build_caveats(self, base_name):
        """Build caveats list for a recommendation."""
        profile = self.ingredient_profiles.get(base_name, {})
        caveats = []
        if profile.get('allergens'):
            caveats.append(
                f"Allergen sensitivity: contains {', '.join(profile['allergens'])}. "
                f"Supplier change must maintain allergen declaration accuracy."
            )
        if 'organic' in profile.get('qualityFlags', []):
            caveats.append("Organic certification: new supplier must have matching organic cert.")
        caveats.append("Supplier qualification and formulation stability testing required.")
        return caveats

    def _build_substitution_caveats(self, group):
        """Build caveats for ingredient substitution."""
        caveats = []
        if group['allergenConsiderations']:
            caveats.append(
                f"Allergen differences: {', '.join(group['allergenConsiderations'])}. "
                f"Substitution may change allergen labeling requirements."
            )
        caveats.append("Formulation testing required — ingredient variants may differ in "
                       "concentration, purity, or processing characteristics.")
        caveats.append("Regulatory review needed if products cross jurisdictional boundaries.")
        return caveats

    # ── Run Full Analysis ──────────────────────

    def run_full_analysis(self):
        """Execute the complete analysis pipeline."""
        if self._analysis_done:
            return self.get_results()

        self.load_data()
        self.profile_ingredients()
        self.detect_substitution_groups()
        self.analyze_consolidation()
        self.assess_risks()
        self.generate_recommendations()
        self._analysis_done = True
        return self.get_results()

    def get_results(self):
        """Return complete analysis results."""
        # Category distribution
        cat_counts = defaultdict(int)
        for p in self.ingredient_profiles.values():
            cat_counts[p['category']] += 1

        # Procurement summary
        total_spend = sum(o['TotalCost'] for o in self.procurement_history)
        avg_on_time = (
            sum(o['OnTime'] for o in self.procurement_history)
            / len(self.procurement_history) * 100
        ) if self.procurement_history else 0
        risk_dist = {'low': 0, 'medium': 0, 'high': 0}
        for r in self.supplier_ratings.values():
            tier = r.get('RiskTier', 'medium')
            risk_dist[tier] = risk_dist.get(tier, 0) + 1

        return {
            'summary': {
                'totalCompanies': len(self.companies),
                'totalProducts': len(self.products),
                'totalFinishedGoods': len(self.fg_products),
                'totalRawMaterials': len(self.rm_products),
                'uniqueIngredients': len(self.ingredient_profiles),
                'totalSuppliers': len(self.suppliers),
                'totalBoms': len(self.boms),
                'substitutionGroupCount': len(self.substitution_groups),
                'consolidationOpportunityCount': len(self.consolidation_opportunities),
                'riskCount': len(self.risk_items),
                'recommendationCount': len(self.recommendations),
                'highPriorityCount': sum(1 for r in self.recommendations if r['priority'] == 'high'),
                'categoryDistribution': [
                    {'category': FUNCTIONAL_CATEGORIES.get(k, {}).get('label', k),
                     'count': v, 'id': k}
                    for k, v in sorted(cat_counts.items(), key=lambda x: -x[1])
                ],
                'procurement': {
                    'totalSpend': round(total_spend, 2),
                    'orderCount': len(self.procurement_history),
                    'avgOnTimeRate': round(avg_on_time, 1),
                    'supplierRiskDistribution': risk_dist,
                },
            },
            'ingredients': self.ingredient_profiles,
            'substitutionGroups': self.substitution_groups,
            'consolidationOpportunities': self.consolidation_opportunities,
            'risks': self.risk_items,
            'recommendations': self.recommendations,
        }

    # ── Per-Ingredient Deep Dive ───────────────

    def get_ingredient_analysis(self, base_name):
        """Get deep analysis for a specific ingredient."""
        if not self._analysis_done:
            self.run_full_analysis()

        profile = self.ingredient_profiles.get(base_name)
        if not profile:
            return None

        # Find related substitution groups
        related_groups = [g for g in self.substitution_groups if base_name in g['members']]

        # Find consolidation opportunity
        consol = next((c for c in self.consolidation_opportunities if c['baseName'] == base_name), None)

        # Find risks
        risks = [r for r in self.risk_items if r.get('baseName') == base_name]

        # Find recommendations
        recs = [r for r in self.recommendations if r.get('baseName') == base_name]

        # Company usage detail
        company_usage = []
        for pid in profile['productIds']:
            p = self.products[pid]
            cid = p['CompanyId']
            suppliers = [self.suppliers[sid]['Name'] for sid in self.product_suppliers.get(pid, [])]
            fgs = [self.products[fgid]['SKU'] for fgid in self.rm_to_fgs.get(pid, set())]
            company_usage.append({
                'companyId': cid,
                'companyName': self.companies[cid]['Name'],
                'productSku': p['SKU'],
                'suppliers': suppliers,
                'usedInFinishedGoods': [_humanize(fg) for fg in fgs[:5]],
            })
        company_usage.sort(key=lambda x: x['companyName'])

        return {
            'profile': profile,
            'companyUsage': company_usage,
            'substitutionGroups': related_groups,
            'consolidation': consol,
            'risks': risks,
            'recommendations': recs,
        }
