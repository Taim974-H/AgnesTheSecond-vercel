"""
Vercel serverless entry point.
Adds the `taim/` directory to sys.path so bare imports
(e.g. `from chat.routes import chat_bp`) resolve correctly,
then exposes the Flask app as `app` for the Vercel Python runtime.
"""

import sys
import os

# Ensure taim/ is on the import path
_TAIM_DIR = os.path.join(os.path.dirname(__file__), '..', 'taim')
_TAIM_DIR = os.path.abspath(_TAIM_DIR)
if _TAIM_DIR not in sys.path:
    sys.path.insert(0, _TAIM_DIR)

# Now import the Flask application
from app import app  # noqa: E402

# Vercel expects `app` at module level — already satisfied.
