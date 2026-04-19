"""
Sourcing – Flask Blueprint
===========================
Product sourcing page: enter a product + ingredients, get supplier map.
"""

from flask import Blueprint, jsonify, request, send_from_directory
import os
from .engine import source_ingredients, get_all_known_ingredients

sourcing_bp = Blueprint('sourcing', __name__, url_prefix='/sourcing')


@sourcing_bp.route('/')
def index():
    return send_from_directory(os.path.dirname(__file__), 'index.html')


@sourcing_bp.route('/api/source', methods=['POST'])
def source():
    """
    POST { "product": "My Protein Bar", "ingredients": ["whey protein", "cocoa", "stevia"] }
    Returns full sourcing report.
    """
    data = request.get_json(force=True)
    ingredients = data.get('ingredients', [])
    if not ingredients:
        return jsonify({'error': 'No ingredients provided.'}), 400

    report = source_ingredients(ingredients)
    report['product'] = data.get('product', 'Unnamed Product')
    return jsonify(report)


@sourcing_bp.route('/api/ingredients')
def known_ingredients():
    """Return all known ingredient names for autocomplete."""
    return jsonify(get_all_known_ingredients())
