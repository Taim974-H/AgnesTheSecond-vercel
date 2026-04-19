"""
AgnesTheSecond Voice Cube – Flask Blueprint
============================================
Voice-interactive 3D cube agent using ElevenLabs for TTS/STT
and the existing OpenAI chat agent for intelligence.
"""

import re
import json
from datetime import datetime
from flask import Blueprint, Response, jsonify, request, send_from_directory
import os
import requests as http_requests
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

API_KEY = os.environ.get('OPEN_AI_API', '') or os.environ.get('OPENAI_API_KEY', '')
ELEVEN_KEY = os.environ.get('ELEVEN_LAB_API', '')

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

    if not API_KEY:
        return jsonify({"error": "No API key configured. Set OPEN_AI_API environment variable."}), 500

    conv_history = []
    for h in history[-20:]:
        role = h.get('role')
        content = h.get('content', '')
        if role in ('user', 'assistant') and content:
            conv_history.append({"role": role, "content": content})

    # Pre-process the spoken transcription into a structured message
    enhanced_message = _preprocess_transcription(user_message)

    result = run_agent(enhanced_message, conv_history, api_key=API_KEY, voice_mode=True)

    log = _load_session_log()
    log.append({"role": "user", "content": user_message, "timestamp": datetime.utcnow().isoformat() + "Z"})
    log.append({"role": "assistant", "content": result["reply"], "timestamp": datetime.utcnow().isoformat() + "Z"})
    _save_session_log(log)

    return jsonify({"reply": result["reply"], "steps": result["steps"]})


@cube_bp.route('/api/tts', methods=['POST'])
def tts_proxy():
    """Server-side proxy for ElevenLabs TTS so the API key stays secret."""
    if not ELEVEN_KEY:
        return jsonify({"error": "ELEVEN_LAB_API not configured"}), 500

    data = request.get_json(force=True)
    text = (data.get('text') or '').strip()
    if not text:
        return jsonify({"error": "text required"}), 400

    voice_id = data.get('voice_id', '21m00Tcm4TlvDq8ikWAM')
    # Truncate to ElevenLabs limit
    if len(text) > 2500:
        text = text[:2500] + '...'

    try:
        resp = http_requests.post(
            f'https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream',
            headers={
                'Content-Type': 'application/json',
                'xi-api-key': ELEVEN_KEY,
            },
            json={
                'text': text,
                'model_id': 'eleven_multilingual_v2',
                'voice_settings': {
                    'stability': 0.5,
                    'similarity_boost': 0.75,
                    'style': 0.3,
                    'use_speaker_boost': True,
                },
            },
            stream=True,
            timeout=30,
        )
        if resp.status_code != 200:
            return jsonify({"error": "ElevenLabs error", "status": resp.status_code}), 502

        return Response(
            resp.iter_content(chunk_size=4096),
            content_type=resp.headers.get('Content-Type', 'audio/mpeg'),
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 502
