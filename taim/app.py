import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask
from flask_cors import CORS

# Ensure taim/ is on sys.path so bare imports work from any CWD
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

# Load .env from project root (one level up from taim/)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

app = Flask(__name__)
CORS(app)

@app.route("/health")
def health():
    return {"status": "ok", "message": "Agnes backend running"}

# Register Chat blueprint (main landing page at /)
from chat.routes import chat_bp
app.register_blueprint(chat_bp)

# Register Explorer blueprint (serves at /explorer/)
from explorer.routes import explorer_bp
app.register_blueprint(explorer_bp)

# Register Agnes Insights blueprint (serves at /agnes/)
from insights.routes import agnes_bp
app.register_blueprint(agnes_bp)

# Register Voice Cube blueprint (serves at /cube/)
from cube.routes import cube_bp
app.register_blueprint(cube_bp)

# Register Orders blueprint (serves at /orders/) — conversation persistence +
# LLM-drafted purchase-order PDFs.
from orders.routes import orders_bp
app.register_blueprint(orders_bp)

# Register Product Sourcing blueprint (serves at /sourcing/)
from sourcing.routes import sourcing_bp
app.register_blueprint(sourcing_bp)

if __name__ == '__main__':
    # Local dev only — production uses gunicorn (see Procfile).
    port = int(os.environ.get('PORT', 5050))
    app.run(debug=True, threaded=True, host='0.0.0.0', port=port)
