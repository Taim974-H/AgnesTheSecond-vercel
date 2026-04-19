"""LLM order-field extractor with deterministic DB validation.

Flow:
1. Take the persisted conversation JSON.
2. Ask gpt-4o-mini (JSON-object mode) to extract a structured draft order.
3. Deterministically resolve the extracted supplier + products against the
   real SQLite tables (Supplier, Product, Supplier_Product, Price_Benchmark,
   Procurement_History) so the final draft carries DB-backed ids and any
   sensible default pricing.

The LLM never invents ids or prices. It only pulls natural-language fields
out of the conversation. All numeric enrichment happens in pure Python.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from difflib import SequenceMatcher
from typing import Any

from openai import OpenAI

from .storage import DB_PATH

EXTRACTION_MODEL = os.environ.get("AGNES_ORDER_MODEL", "gpt-4o-mini")

SYSTEM_PROMPT = """You extract a draft purchase order from a chat transcript.
Return valid JSON ONLY, matching this shape exactly:

{
  "buyer_company_name": string | null,
  "supplier_name": string | null,
  "delivery_date": "YYYY-MM-DD" | null,
  "notes": string | null,
  "items": [
    {"product_name": string, "quantity": number, "unit": string | null, "unit_price": number | null}
  ]
}

Rules:
- Extract only what the user actually said or agreed to. Do NOT invent prices,
  products, or quantities. If a field is absent, use null (or [] for items).
- `product_name` is required for each item; `quantity` defaults to 1 if the
  user didn't specify one. Do not emit items the user never mentioned.
- `notes` captures any special instructions (urgency, packaging, rebates,
  compliance flags).
- Output JSON only. No prose, no markdown fences.
"""


class OrderExtractionError(RuntimeError):
    """Raised when the LLM output cannot be parsed or is unusable."""


# ── LLM call ───────────────────────────────────────────────────────────────


def _transcript_text(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for m in messages:
        role = m.get("role", "?")
        content = (m.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"[{role.upper()}] {content}")
    return "\n\n".join(lines)


def llm_extract(messages: list[dict[str, Any]], *, api_key: str) -> dict[str, Any]:
    if not messages:
        raise OrderExtractionError("conversation is empty")
    if not api_key:
        raise OrderExtractionError("missing OpenAI API key")
    client = OpenAI(api_key=api_key)
    prompt = _transcript_text(messages)
    resp = client.chat.completions.create(
        model=EXTRACTION_MODEL,
        response_format={"type": "json_object"},
        temperature=0.0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Transcript:\n\n{prompt}"},
        ],
    )
    raw = resp.choices[0].message.content or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OrderExtractionError(f"LLM returned non-JSON: {raw[:200]}") from exc
    if not isinstance(data, dict):
        raise OrderExtractionError(f"LLM returned non-object: {type(data).__name__}")
    return data


# ── DB validation ──────────────────────────────────────────────────────────


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _normalize(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def resolve_supplier(name: str | None) -> tuple[int | None, str | None]:
    if not name:
        return None, None
    with _connect() as conn:
        rows = conn.execute("SELECT Id, Name FROM Supplier").fetchall()
    if not rows:
        return None, name
    scored = sorted(
        rows, key=lambda r: _similarity(name, r["Name"]), reverse=True
    )
    best = scored[0]
    if _similarity(name, best["Name"]) >= 0.55:
        return int(best["Id"]), best["Name"]
    return None, name


def resolve_company(name: str | None) -> tuple[int | None, str | None]:
    if not name:
        return None, None
    with _connect() as conn:
        rows = conn.execute("SELECT Id, Name FROM Company").fetchall()
    if not rows:
        return None, name
    scored = sorted(rows, key=lambda r: _similarity(name, r["Name"]), reverse=True)
    best = scored[0]
    if _similarity(name, best["Name"]) >= 0.55:
        return int(best["Id"]), best["Name"]
    return None, name


def _strip_sku_prefix(sku: str) -> str:
    """RM-C12-vitamin-c-ascorbic-acid-a3f2c1 → vitamin c ascorbic acid."""
    parts = sku.split("-")
    if len(parts) >= 4 and parts[0] in ("RM", "FG"):
        core = parts[2:-1] if parts[0] == "RM" else parts[2:]
        return " ".join(core).strip() or sku
    return sku


def resolve_product(name: str, supplier_id: int | None) -> tuple[int | None, str]:
    """Match a natural-language product name to a raw-material Product row.

    When ``supplier_id`` is known, restricts the search to products that
    supplier can actually supply (per Supplier_Product). Falls back to all
    raw materials if nothing matches.
    """
    if not name:
        return None, name
    with _connect() as conn:
        if supplier_id is not None:
            rows = conn.execute(
                """
                SELECT p.Id, p.SKU, p.Type FROM Product p
                JOIN Supplier_Product sp ON sp.ProductId = p.Id
                WHERE sp.SupplierId = ? AND p.Type = 'raw-material'
                """,
                (supplier_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT Id, SKU, Type FROM Product WHERE Type = 'raw-material'"
            ).fetchall()
    if not rows:
        return None, name
    scored = sorted(
        rows,
        key=lambda r: _similarity(name, _strip_sku_prefix(r["SKU"])),
        reverse=True,
    )
    best = scored[0]
    if _similarity(name, _strip_sku_prefix(best["SKU"])) >= 0.45:
        return int(best["Id"]), _strip_sku_prefix(best["SKU"])
    return None, name


def _benchmark_price(product_name: str) -> float | None:
    """Look up a market-benchmark avg $/kg for an ingredient name."""
    norm = _normalize(product_name).replace(" ", "-")
    if not norm:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT AvgMarketPrice FROM Price_Benchmark WHERE BaseName = ?",
            (norm,),
        ).fetchone()
        if row:
            return float(row["AvgMarketPrice"])
        # fuzzy fallback
        rows = conn.execute("SELECT BaseName, AvgMarketPrice FROM Price_Benchmark").fetchall()
    if not rows:
        return None
    scored = sorted(rows, key=lambda r: _similarity(norm, r["BaseName"]), reverse=True)
    best = scored[0]
    if _similarity(norm, best["BaseName"]) >= 0.7:
        return float(best["AvgMarketPrice"])
    return None


# ── Public entry point ─────────────────────────────────────────────────────


def build_draft(
    messages: list[dict[str, Any]], *, api_key: str
) -> dict[str, Any]:
    """Run the LLM + deterministic resolver and return the final draft.

    Output shape mirrors what the PDF generator + storage layer expect:

    {
      "company_id": int | None, "company_name": str | None,
      "supplier_id": int | None, "supplier_name": str | None,
      "delivery_date": str | None, "notes": str | None,
      "items": [
        {product_id, product_name, unit, quantity, unit_price, line_total, resolved}
      ],
      "grand_total": float | None,
      "warnings": list[str],
    }
    """
    raw = llm_extract(messages, api_key=api_key)
    warnings: list[str] = []

    company_id, company_name = resolve_company(raw.get("buyer_company_name"))
    if raw.get("buyer_company_name") and company_id is None:
        warnings.append(
            f"Buyer company '{raw['buyer_company_name']}' not found in DB — "
            "please verify before sending."
        )

    supplier_id, supplier_name = resolve_supplier(raw.get("supplier_name"))
    if raw.get("supplier_name") and supplier_id is None:
        warnings.append(
            f"Supplier '{raw['supplier_name']}' not found in DB — "
            "please verify before sending."
        )

    items_out: list[dict[str, Any]] = []
    grand_total = 0.0
    any_price = False
    for item in (raw.get("items") or []):
        name = (item.get("product_name") or "").strip()
        if not name:
            continue
        qty = item.get("quantity")
        try:
            qty_f = float(qty) if qty is not None else 1.0
        except (TypeError, ValueError):
            qty_f = 1.0
        product_id, resolved_name = resolve_product(name, supplier_id)
        if product_id is None:
            warnings.append(
                f"Product '{name}' not matched to the catalogue — "
                "entered as free text."
            )
        unit_price = item.get("unit_price")
        if unit_price in (None, ""):
            unit_price = _benchmark_price(resolved_name or name)
        try:
            unit_price_f = float(unit_price) if unit_price is not None else None
        except (TypeError, ValueError):
            unit_price_f = None
        line_total = (
            round(unit_price_f * qty_f, 2) if unit_price_f is not None else None
        )
        if line_total is not None:
            grand_total += line_total
            any_price = True
        items_out.append(
            {
                "product_id": product_id,
                "product_name": resolved_name or name,
                "unit": item.get("unit") or "kg",
                "quantity": qty_f,
                "unit_price": unit_price_f,
                "line_total": line_total,
                "resolved": product_id is not None,
            }
        )

    if not items_out:
        warnings.append("No line items extracted — the conversation doesn't contain a concrete order yet.")

    return {
        "company_id": company_id,
        "company_name": company_name,
        "supplier_id": supplier_id,
        "supplier_name": supplier_name,
        "delivery_date": raw.get("delivery_date"),
        "notes": raw.get("notes"),
        "items": items_out,
        "grand_total": round(grand_total, 2) if any_price else None,
        "warnings": warnings,
    }
