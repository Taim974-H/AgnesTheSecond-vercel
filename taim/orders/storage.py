"""SQLite persistence for conversations + purchase orders.

Tables are created on first call to ``ensure_schema`` and live alongside the
existing hackathon tables in ``hackathon-tumai/db.sqlite``. Nothing about the
existing schema is modified.

- ``Conversation`` — one row per chat/cube session, keyed by ``session_id``.
- ``ConversationMessage`` — one row per user/assistant turn.
- ``Order`` — one row per generated PO (draft + PDF path).
- ``OrderItem`` — line items on the PO.

Everything is kept minimal: plain sqlite3, no ORM, stable column order.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

_DB_PATH = os.environ.get(
    "ORDERS_DB_PATH",
    os.path.join(os.path.dirname(__file__), "..", "..", "hackathon-tumai", "db.sqlite"),
)
DB_PATH = os.path.abspath(_DB_PATH)


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def ensure_schema() -> None:
    """Idempotently create the orders tables."""
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS Conversation (
                Id INTEGER PRIMARY KEY AUTOINCREMENT,
                SessionId TEXT UNIQUE NOT NULL,
                Source TEXT NOT NULL DEFAULT 'chat',
                StartedAt TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ConversationMessage (
                Id INTEGER PRIMARY KEY AUTOINCREMENT,
                ConversationId INTEGER NOT NULL,
                Role TEXT NOT NULL,
                Content TEXT NOT NULL,
                Metadata TEXT,
                CreatedAt TEXT NOT NULL,
                FOREIGN KEY (ConversationId) REFERENCES Conversation(Id)
            );

            CREATE TABLE IF NOT EXISTS "Order" (
                Id INTEGER PRIMARY KEY AUTOINCREMENT,
                PoNumber TEXT UNIQUE NOT NULL,
                ConversationId INTEGER,
                SupplierId INTEGER,
                SupplierName TEXT,
                CompanyId INTEGER,
                CompanyName TEXT,
                DeliveryDate TEXT,
                Notes TEXT,
                Status TEXT NOT NULL DEFAULT 'draft',
                PdfPath TEXT,
                GrandTotal REAL,
                DraftJson TEXT,
                CreatedAt TEXT NOT NULL,
                FOREIGN KEY (ConversationId) REFERENCES Conversation(Id)
            );

            CREATE TABLE IF NOT EXISTS OrderItem (
                Id INTEGER PRIMARY KEY AUTOINCREMENT,
                OrderId INTEGER NOT NULL,
                ProductId INTEGER,
                ProductName TEXT NOT NULL,
                Unit TEXT,
                Quantity REAL NOT NULL,
                UnitPrice REAL,
                LineTotal REAL,
                FOREIGN KEY (OrderId) REFERENCES "Order"(Id)
            );
            """
        )


def upsert_conversation(session_id: str, source: str = "chat") -> int:
    """Return the Conversation.Id for ``session_id``, creating if missing."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT Id FROM Conversation WHERE SessionId = ?", (session_id,)
        ).fetchone()
        if row:
            return int(row["Id"])
        cur = conn.execute(
            "INSERT INTO Conversation (SessionId, Source, StartedAt) VALUES (?, ?, ?)",
            (session_id, source, _now_iso()),
        )
        return int(cur.lastrowid)


def add_message(
    session_id: str,
    role: str,
    content: str,
    *,
    source: str = "chat",
    metadata: dict[str, Any] | None = None,
) -> int:
    """Append a single message to the conversation. Returns message id."""
    conv_id = upsert_conversation(session_id, source=source)
    meta_json = json.dumps(metadata) if metadata else None
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO ConversationMessage
                (ConversationId, Role, Content, Metadata, CreatedAt)
            VALUES (?, ?, ?, ?, ?)
            """,
            (conv_id, role, content, meta_json, _now_iso()),
        )
        return int(cur.lastrowid)


def get_messages(session_id: str) -> list[dict[str, Any]]:
    """Return all messages for a session in chronological order."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT m.Role, m.Content, m.Metadata, m.CreatedAt
              FROM ConversationMessage m
              JOIN Conversation c ON m.ConversationId = c.Id
             WHERE c.SessionId = ?
             ORDER BY m.Id ASC
            """,
            (session_id,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "role": r["Role"],
                "content": r["Content"],
                "metadata": json.loads(r["Metadata"]) if r["Metadata"] else None,
                "created_at": r["CreatedAt"],
            }
        )
    return out


def export_session_json(session_id: str) -> dict[str, Any]:
    """Return the full conversation as a JSON-ready dict."""
    with _connect() as conn:
        conv = conn.execute(
            "SELECT * FROM Conversation WHERE SessionId = ?", (session_id,)
        ).fetchone()
    if not conv:
        return {"session_id": session_id, "messages": []}
    return {
        "session_id": session_id,
        "source": conv["Source"],
        "started_at": conv["StartedAt"],
        "messages": get_messages(session_id),
    }


def create_order(
    *,
    session_id: str | None,
    po_number: str,
    draft: dict[str, Any],
    pdf_path: str,
    grand_total: float | None,
) -> int:
    """Persist a generated order + its line items."""
    conv_id = upsert_conversation(session_id) if session_id else None
    items = draft.get("items") or []
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO "Order"
                (PoNumber, ConversationId, SupplierId, SupplierName, CompanyId,
                 CompanyName, DeliveryDate, Notes, Status, PdfPath, GrandTotal,
                 DraftJson, CreatedAt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?)
            """,
            (
                po_number,
                conv_id,
                draft.get("supplier_id"),
                draft.get("supplier_name"),
                draft.get("company_id"),
                draft.get("company_name"),
                draft.get("delivery_date"),
                draft.get("notes"),
                pdf_path,
                grand_total,
                json.dumps(draft),
                _now_iso(),
            ),
        )
        order_id = int(cur.lastrowid)
        for item in items:
            conn.execute(
                """
                INSERT INTO OrderItem
                    (OrderId, ProductId, ProductName, Unit, Quantity,
                     UnitPrice, LineTotal)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    item.get("product_id"),
                    item.get("product_name", ""),
                    item.get("unit"),
                    float(item.get("quantity") or 0),
                    (None if item.get("unit_price") in (None, "") else float(item["unit_price"])),
                    (None if item.get("line_total") in (None, "") else float(item["line_total"])),
                ),
            )
        return order_id


def get_order(order_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        order = conn.execute(
            'SELECT * FROM "Order" WHERE Id = ?', (order_id,)
        ).fetchone()
        if not order:
            return None
        items = conn.execute(
            "SELECT * FROM OrderItem WHERE OrderId = ? ORDER BY Id", (order_id,)
        ).fetchall()
    return {
        "id": int(order["Id"]),
        "po_number": order["PoNumber"],
        "supplier_id": order["SupplierId"],
        "supplier_name": order["SupplierName"],
        "company_id": order["CompanyId"],
        "company_name": order["CompanyName"],
        "delivery_date": order["DeliveryDate"],
        "notes": order["Notes"],
        "status": order["Status"],
        "pdf_path": order["PdfPath"],
        "grand_total": order["GrandTotal"],
        "draft_json": json.loads(order["DraftJson"]) if order["DraftJson"] else None,
        "created_at": order["CreatedAt"],
        "items": [dict(r) for r in items],
    }


def next_po_number() -> str:
    """Yield a sequential PO number like PO-2026-000017."""
    year = datetime.now().year
    with _connect() as conn:
        row = conn.execute(
            'SELECT COUNT(*) AS n FROM "Order" WHERE PoNumber LIKE ?',
            (f"PO-{year}-%",),
        ).fetchone()
        n = int(row["n"]) + 1
    return f"PO-{year}-{n:06d}"
