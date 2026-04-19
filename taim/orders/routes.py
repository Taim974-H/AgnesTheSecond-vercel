"""Flask blueprint for the order-placement flow.

Endpoints:
- ``POST /orders/save_message`` — persist a chat/cube message.
- ``POST /orders/generate``     — extract + validate + write PDF.
- ``POST /orders/place/<id>``   — upload PDF to Dify workflow (fire-and-forget).
- ``GET  /orders/pdf/<id>``     — serve the generated PDF inline.
- ``GET  /orders/export/<sid>`` — raw conversation JSON (debug).
- ``GET  /orders/draft/<id>``   — inspect the stored draft (debug).
"""

from __future__ import annotations

import os

import requests as http_requests
from flask import Blueprint, abort, jsonify, request, send_file

from .extractor import OrderExtractionError, build_draft
from .pdf_generator import build_pdf
from .storage import (
    add_message,
    create_order,
    ensure_schema,
    export_session_json,
    get_messages,
    get_order,
    next_po_number,
)

orders_bp = Blueprint("orders", __name__, url_prefix="/orders")

# Use /tmp on Vercel (read-only FS); fall back to local dir otherwise
if os.environ.get('VERCEL', ''):
    PDF_DIR = os.path.join('/tmp', 'order_pdfs')
else:
    PDF_DIR = os.path.join(os.path.dirname(__file__), "pdfs")


def _ensure_pdf_dir() -> None:
    os.makedirs(PDF_DIR, exist_ok=True)


# Make sure the schema exists when the blueprint is imported.
# On Vercel the DB is read-only — skip schema creation (tables must be pre-baked).
if not os.environ.get('VERCEL', ''):
    ensure_schema()
_ensure_pdf_dir()


@orders_bp.route("/save_message", methods=["POST"])
def save_message():
    data = request.get_json(force=True, silent=True) or {}
    session_id = (data.get("session_id") or "").strip()
    role = (data.get("role") or "").strip()
    content = data.get("content") or ""
    source = (data.get("source") or "chat").strip() or "chat"
    metadata = data.get("metadata")
    if not session_id or role not in {"user", "assistant", "system"} or not content:
        return (
            jsonify(
                {
                    "error": "session_id, role∈{user,assistant,system}, and content are required",
                }
            ),
            400,
        )
    message_id = add_message(
        session_id, role=role, content=content, source=source, metadata=metadata
    )
    return jsonify({"ok": True, "message_id": message_id})


@orders_bp.route("/generate", methods=["POST"])
def generate():
    data = request.get_json(force=True, silent=True) or {}
    session_id = (data.get("session_id") or "").strip()
    api_key = (
        os.environ.get("OPEN_AI_API", "")
        or os.environ.get("OPENAI_API_KEY", "")
        or ""
    ).strip()
    if not session_id:
        return jsonify({"error": "session_id required"}), 400
    if not api_key:
        return (
            jsonify(
                {
                    "error": (
                        "No OpenAI API key available. Set OPEN_AI_API "
                        "environment variable."
                    )
                }
            ),
            500,
        )

    messages = get_messages(session_id)
    if not messages:
        return jsonify({"error": "no messages saved for this session yet"}), 400

    try:
        draft = build_draft(messages, api_key=api_key)
    except OrderExtractionError as exc:
        return jsonify({"error": str(exc)}), 502

    po_number = next_po_number()
    safe_po = po_number.replace("/", "-")
    pdf_path = os.path.join(PDF_DIR, f"{safe_po}.pdf")
    build_pdf(pdf_path, po_number=po_number, draft=draft)

    order_id = create_order(
        session_id=session_id,
        po_number=po_number,
        draft=draft,
        pdf_path=pdf_path,
        grand_total=draft.get("grand_total"),
    )

    return jsonify(
        {
            "ok": True,
            "order_id": order_id,
            "po_number": po_number,
            "pdf_url": f"/orders/pdf/{order_id}",
            "draft": draft,
        }
    )


@orders_bp.route("/pdf/<int:order_id>")
def serve_pdf(order_id: int):
    order = get_order(order_id)
    if not order or not order.get("pdf_path"):
        abort(404)
    path = order["pdf_path"]
    if not os.path.isfile(path):
        abort(404)
    return send_file(
        path,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"{order['po_number']}.pdf",
    )


@orders_bp.route("/draft/<int:order_id>")
def serve_draft(order_id: int):
    order = get_order(order_id)
    if not order:
        abort(404)
    return jsonify(order)


@orders_bp.route("/export/<session_id>")
def export_session(session_id: str):
    return jsonify(export_session_json(session_id))


# ── Dify workflow: fire-and-forget ───────────────────────
@orders_bp.route("/place/<int:order_id>", methods=["POST"])
def place_order(order_id: int):
    """Upload the generated PDF to the Dify workflow API."""
    order = get_order(order_id)
    if not order or not order.get("pdf_path"):
        return jsonify({"error": "Order not found"}), 404
    pdf_path = order["pdf_path"]
    if not os.path.isfile(pdf_path):
        return jsonify({"error": "PDF file missing"}), 404

    dify_key = os.environ.get("DIFY_API_KEY", "").strip()
    dify_base = os.environ.get("DIFY_API_BASE", "https://api.dify.ai/v1").strip().rstrip("/")
    if not dify_key:
        return jsonify({"error": "DIFY_API_KEY not configured"}), 500

    headers = {"Authorization": f"Bearer {dify_key}"}

    # Step 1 — upload PDF file to Dify
    try:
        with open(pdf_path, "rb") as f:
            upload_resp = http_requests.post(
                f"{dify_base}/files/upload",
                headers=headers,
                files={"file": (os.path.basename(pdf_path), f, "application/pdf")},
                data={"user": "agnes-order-system"},
                timeout=30,
            )
        upload_resp.raise_for_status()
        file_id = upload_resp.json().get("id")
    except Exception as exc:
        return jsonify({"error": f"Dify file upload failed: {exc}"}), 502

    # Step 2 — run the Dify workflow with the uploaded file
    try:
        run_resp = http_requests.post(
            f"{dify_base}/workflows/run",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "inputs": {
                    "file": [
                        {
                            "type": "document",
                            "transfer_method": "local_file",
                            "upload_file_id": file_id,
                        }
                    ]
                },
                "response_mode": "blocking",
                "user": "agnes-order-system",
            },
            timeout=60,
        )
        run_resp.raise_for_status()
    except Exception as exc:
        return jsonify({"error": f"Dify workflow run failed: {exc}"}), 502

    return jsonify({"ok": True, "po_number": order["po_number"]})
