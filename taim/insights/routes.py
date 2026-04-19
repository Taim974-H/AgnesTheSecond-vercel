"""
Agnes Solution – Flask Blueprint
=================================
API endpoints for the AgnesTheSecond Analysis engine.
"""

from flask import Blueprint, jsonify, request, send_from_directory
import os
from .agnes_engine import AgnesEngine

agnes_bp = Blueprint('agnes', __name__, url_prefix='/agnes')

# Lazy-initialized engine singleton
_engine = None
_engine_lock = None

try:
    import threading
    _engine_lock = threading.Lock()
except ImportError:
    pass


def _get_engine():
    global _engine
    if _engine is not None:
        return _engine
    if _engine_lock:
        with _engine_lock:
            if _engine is not None:
                return _engine
            return _init_engine()
    return _init_engine()


def _init_engine():
    global _engine
    db_path = os.path.join(
        os.path.dirname(__file__), '../../hackathon-tumai/db.sqlite'
    )
    _engine = AgnesEngine(os.path.abspath(db_path))
    _engine.run_full_analysis()
    return _engine


@agnes_bp.route('/')
def agnes_ui():
    """Serve the Agnes decision-support UI."""
    response = send_from_directory(os.path.dirname(__file__), 'agnes.html')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


@agnes_bp.route('/api/analysis')
def full_analysis():
    """Return the complete analysis summary."""
    engine = _get_engine()
    results = engine.get_results()
    return jsonify({
        'summary': results['summary'],
        'recommendations': results['recommendations'],
        'risks': results['risks'],
        'substitutionGroupCount': len(results['substitutionGroups']),
        'consolidationOpportunityCount': len(results['consolidationOpportunities']),
    })


@agnes_bp.route('/api/recommendations')
def get_recommendations():
    """Return all recommendations with optional filtering."""
    engine = _get_engine()
    results = engine.get_results()
    recs = results['recommendations']

    rec_type = request.args.get('type')  # consolidation, risk_mitigation, substitution
    priority = request.args.get('priority')  # high, medium, low

    if rec_type:
        recs = [r for r in recs if r['type'] == rec_type]
    if priority:
        recs = [r for r in recs if r['priority'] == priority]

    return jsonify({'recommendations': recs, 'total': len(recs)})


@agnes_bp.route('/api/substitutions')
def get_substitutions():
    """Return substitution groups."""
    engine = _get_engine()
    results = engine.get_results()
    groups = results['substitutionGroups']

    sub_type = request.args.get('type')  # variant, functional
    if sub_type:
        groups = [g for g in groups if g['type'] == sub_type]

    return jsonify({'groups': groups, 'total': len(groups)})


@agnes_bp.route('/api/consolidation')
def get_consolidation():
    """Return consolidation opportunities."""
    engine = _get_engine()
    results = engine.get_results()
    opps = results['consolidationOpportunities']

    min_companies = int(request.args.get('minCompanies', 2))
    opps = [o for o in opps if o['companyCount'] >= min_companies]

    return jsonify({'opportunities': opps, 'total': len(opps)})


@agnes_bp.route('/api/risks')
def get_risks():
    """Return risk items."""
    engine = _get_engine()
    results = engine.get_results()
    risks = results['risks']

    severity = request.args.get('severity')
    risk_type = request.args.get('type')
    if severity:
        risks = [r for r in risks if r['severity'] == severity]
    if risk_type:
        risks = [r for r in risks if r['type'] == risk_type]

    return jsonify({'risks': risks, 'total': len(risks)})


@agnes_bp.route('/api/ingredients')
def get_ingredients():
    """Return ingredient profiles with optional filtering."""
    engine = _get_engine()
    results = engine.get_results()
    ingredients = list(results['ingredients'].values())

    category = request.args.get('category')
    q = request.args.get('q', '').lower()

    if category:
        ingredients = [i for i in ingredients if i['category'] == category]
    if q:
        ingredients = [i for i in ingredients
                       if q in i['name'].lower() or q in i['baseName'].lower()]

    ingredients.sort(key=lambda x: (-x['companyCount'], x['name']))
    return jsonify({'ingredients': ingredients, 'total': len(ingredients)})


@agnes_bp.route('/api/ingredient/<base_name>')
def get_ingredient_detail(base_name):
    """Deep dive analysis for a single ingredient."""
    engine = _get_engine()
    result = engine.get_ingredient_analysis(base_name)
    if not result:
        return jsonify({'error': 'Ingredient not found'}), 404
    return jsonify(result)


@agnes_bp.route('/api/categories')
def get_categories():
    """Return ingredient category distribution."""
    engine = _get_engine()
    results = engine.get_results()
    return jsonify({'categories': results['summary']['categoryDistribution']})


@agnes_bp.route('/api/reload', methods=['POST'])
def reload_analysis():
    """Force re-run analysis (e.g., after data changes)."""
    global _engine
    _engine = None
    engine = _get_engine()
    return jsonify({'status': 'ok', 'summary': engine.get_results()['summary']})
