"""
Agnes Chat – Flask Blueprint
=============================
Main landing page with OpenAI-powered natural language query interface.
"""

from flask import Blueprint, jsonify, request, send_from_directory
import os
from .agent import run_agent

chat_bp = Blueprint('chat', __name__, url_prefix='/')

API_KEY = os.environ.get('OPEN_AI_API', '') or os.environ.get('OPENAI_API_KEY', '')


@chat_bp.route('/')
def index():
    return send_from_directory(os.path.dirname(__file__), 'main.html')


@chat_bp.route('/api/chat', methods=['POST'])
def chat():
    data = request.get_json(force=True)
    user_message = data.get('message', '').strip()
    history = data.get('history', [])

    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    if not API_KEY:
        return jsonify({"error": "No API key configured. Set OPEN_AI_API environment variable."}), 500

    # Build conversation history (only keep last 10 exchanges)
    conv_history = []
    for h in history[-20:]:
        role = h.get('role')
        content = h.get('content', '')
        if role in ('user', 'assistant') and content:
            conv_history.append({"role": role, "content": content})

    result = run_agent(user_message, conv_history, api_key=API_KEY)
    return jsonify({"reply": result["reply"], "steps": result["steps"]})
