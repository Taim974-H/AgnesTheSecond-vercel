"""
AgnesTheSecond Voice Cube – Flask Blueprint
============================================
Voice-interactive 3D cube agent using ElevenLabs for TTS/STT
and the existing OpenAI chat agent for intelligence.
"""

import re
import json
from datetime import datetime
from flask import Blueprint, jsonify, request, send_from_directory
import os
from chat.agent import run_agent

# Use /tmp on Vercel (read-only FS); fall back to local dir otherwise
_TMP = os.environ.get('VERCEL', '')
SESSION_LOG_PATH = os.path.join('/tmp', 'cube_session_log.json') if _TMP else os.path.join(os.path.dirname(__file__), 'session_log.json')


def _load_session_log():
    if os.path.exists(SESSION_LOG_PATH):
        with open(SESSION_LOG_PATH, 'r') as f:
            return json.load(f)
    return []


def _save_session_log(log):
    with open(SESSION_LOG_PATH, 'w') as f:
        json.dump(log, f, indent=2)

cube_bp = Blueprint('cube', __name__, url_prefix='/cube')

API_KEY = os.environ.get('OPENAI_API_KEY', '')

# ── Keyword / intent extraction for voice transcriptions ─────────

# Domain keywords grouped by category
_KEYWORD_CATEGORIES = {
    'cost':      ['cost', 'price', 'spend', 'expensive', 'cheap', 'saving',
                  'savings', 'budget', 'procurement', 'benchmark', 'dollar'],
    'quality':   ['quality', 'score', 'compliance', 'rating', 'audit',
                  'certification', 'gmp', 'iso', 'reliable', 'reliability'],
    'risk':      ['risk', 'single source', 'concentration', 'vulnerability',
                  'disruption', 'critical', 'dependency', 'shortage'],
    'supplier':  ['supplier', 'vendor', 'source', 'sourcing', 'lead time',
                  'delivery', 'on time', 'on-time'],
    'ingredient':['ingredient', 'raw material', 'material', 'component',
                  'substitute', 'replacement', 'alternative', 'bom',
                  'bill of material', 'formulation'],
    'company':   ['company', 'brand', 'manufacturer', 'producer'],
    'overview':  ['overview', 'summary', 'how many', 'total', 'count',
                  'list', 'show me', 'tell me about', 'what is', 'who'],
}

_INTENT_PATTERNS = [
    (r'\b(?:substitute|replace|alternative|swap|switch)\b', 'find_substitutes'),
    (r'\b(?:bom|bill of material|recipe|formulation|composition)\b', 'analyze_bom'),
    (r'\b(?:compar|cheapest|most expensive|cheapest|price range)\b', 'compare_costs'),
    (r'\b(?:risk|single.?source|vulnerable|critical)\b', 'assess_risk'),
    (r'\b(?:how many|count|total|number of)\b', 'count_query'),
    (r'\b(?:who suppli|which supplier|supplier for|sourced from)\b', 'supplier_lookup'),
    (r'\b(?:save|saving|consolidat|optimi)\b', 'find_savings'),
]


def _preprocess_transcription(raw_text):
    """
    Structure a raw voice transcription into a richer message
    with extracted keywords, detected intent, and named entities.
    """
    lower = raw_text.lower()

    # 1. Extract matching keyword categories
    matched_categories = []
    matched_keywords = []
    for cat, words in _KEYWORD_CATEGORIES.items():
        for w in words:
            if w in lower:
                if cat not in matched_categories:
                    matched_categories.append(cat)
                matched_keywords.append(w)

    # 2. Detect intent from patterns
    intents = []
    for pattern, intent in _INTENT_PATTERNS:
        if re.search(pattern, lower):
            intents.append(intent)

    # 3. Extract entities: look for things in quotes or capitalized proper nouns
    entities = []
    # Quoted strings
    for m in re.finditer(r'["\']([^"\']+)["\']', raw_text):
        entities.append(m.group(1))
    # Capitalized multi-word proper nouns (likely company/supplier names)
    for m in re.finditer(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', raw_text):
        entities.append(m.group(1))

    # 4. Build structured message
    structured = {
        'raw_transcription': raw_text,
        'keywords': matched_keywords[:10],
        'categories': matched_categories,
        'intent': intents[:3] if intents else ['general_question'],
        'entities': entities[:5],
    }

    # Format as enhanced user message
    parts = [raw_text]
    if matched_keywords or intents or entities:
        parts.append('\n\n[Voice context — structured from transcription]')
        if intents:
            parts.append(f'Intent: {", ".join(intents)}')
        if matched_categories:
            parts.append(f'Topics: {", ".join(matched_categories)}')
        if matched_keywords:
            parts.append(f'Keywords: {", ".join(matched_keywords[:8])}')
        if entities:
            parts.append(f'Entities mentioned: {", ".join(entities)}')

    return '\n'.join(parts)


@cube_bp.route('/')
def cube_ui():
    response = send_from_directory(os.path.dirname(__file__), 'index.html')
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


@cube_bp.route('/api/voice-chat', methods=['POST'])
def voice_chat():
    """Voice-optimised agent endpoint: preprocesses transcription and uses voice_mode."""
    data = request.get_json(force=True)
    user_message = data.get('message', '').strip()
    history = data.get('history', [])

    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    key = data.get('api_key', '') or API_KEY
    if not key:
        return jsonify({"error": "No API key. Set OPENAI_API_KEY or pass api_key in body."}), 400

    conv_history = []
    for h in history[-20:]:
        role = h.get('role')
        content = h.get('content', '')
        if role in ('user', 'assistant') and content:
            conv_history.append({"role": role, "content": content})

    # Pre-process the spoken transcription into a structured message
    enhanced_message = _preprocess_transcription(user_message)

    result = run_agent(enhanced_message, conv_history, api_key=key, voice_mode=True)

    log = _load_session_log()
    log.append({"role": "user", "content": user_message, "timestamp": datetime.utcnow().isoformat() + "Z"})
    log.append({"role": "assistant", "content": result["reply"], "timestamp": datetime.utcnow().isoformat() + "Z"})
    _save_session_log(log)

    return jsonify({"reply": result["reply"], "steps": result["steps"]})
