"""Minimal reportlab PDF template for a purchase order.

One-page layout:

  PURCHASE ORDER                          PO-YYYY-NNNNNN · <date>

  From                         To
  <Buyer>                      <Supplier>

  [ table: # | Product | Unit | Qty | Unit price | Line total ]

  Grand total                  $X,XXX.XX

  Notes: ...
  Warnings: ...
  Drafted by AgnesTheSecond — review and sign before sending.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# Keep the palette minimal — black text on white paper, one accent colour
# matching the app's teal.
ACCENT = colors.HexColor("#00d4aa")
BORDER = colors.HexColor("#d0d7de")
MUTED  = colors.HexColor("#64748b")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            name="poTitle",
            parent=base["Title"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=26,
            textColor=colors.black,
            spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            name="poSubtitle",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10,
            textColor=MUTED,
            spaceAfter=14,
        ),
        "section": ParagraphStyle(
            name="poSection",
            parent=base["Normal"],
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=MUTED,
            leading=12,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            name="poBody",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=10.5,
            textColor=colors.black,
            leading=14,
        ),
        "footer": ParagraphStyle(
            name="poFooter",
            parent=base["Normal"],
            fontName="Helvetica-Oblique",
            fontSize=8.5,
            textColor=MUTED,
            leading=11,
        ),
        "warn": ParagraphStyle(
            name="poWarn",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            textColor=colors.HexColor("#b45309"),
            leading=12,
        ),
    }


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"${value:,.2f}"


def _fmt_qty(value: float) -> str:
    return f"{value:g}"


def build_pdf(
    out_path: str,
    *,
    po_number: str,
    draft: dict[str, Any],
) -> str:
    """Write the PO to ``out_path`` and return that path."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    styles = _styles()

    doc = SimpleDocTemplate(
        out_path,
        pagesize=A4,
        leftMargin=22 * mm,
        rightMargin=22 * mm,
        topMargin=22 * mm,
        bottomMargin=20 * mm,
        title=f"Purchase Order {po_number}",
        author="AgnesTheSecond",
    )

    story: list[Any] = []

    # ── Header ────────────────────────────────────────────────────────────
    story.append(Paragraph("PURCHASE ORDER", styles["title"]))
    story.append(
        Paragraph(
            f"{po_number} &middot; {datetime.now().strftime('%Y-%m-%d')}",
            styles["subtitle"],
        )
    )

    # ── Parties block (two columns) ───────────────────────────────────────
    buyer_name = draft.get("company_name") or "—"
    supplier_name = draft.get("supplier_name") or "—"
    delivery_date = draft.get("delivery_date") or "—"

    parties = Table(
        [
            [
                Paragraph("FROM (BUYER)", styles["section"]),
                Paragraph("TO (SUPPLIER)", styles["section"]),
                Paragraph("REQUESTED DELIVERY", styles["section"]),
            ],
            [
                Paragraph(buyer_name, styles["body"]),
                Paragraph(supplier_name, styles["body"]),
                Paragraph(delivery_date, styles["body"]),
            ],
        ],
        colWidths=[60 * mm, 60 * mm, 46 * mm],
        hAlign="LEFT",
    )
    parties.setStyle(
        TableStyle(
            [
                ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
                ("TOPPADDING", (0, 1), (-1, 1), 0),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(parties)
    story.append(Spacer(1, 12))

    # ── Line-items table ──────────────────────────────────────────────────
    header = ["#", "Product", "Unit", "Quantity", "Unit price", "Line total"]
    rows: list[list[Any]] = [header]
    items = draft.get("items") or []
    for idx, item in enumerate(items, start=1):
        rows.append(
            [
                str(idx),
                Paragraph(
                    (item.get("product_name") or "—")
                    + (" <font color='#94a3b8'>(unmatched)</font>"
                       if not item.get("resolved") else ""),
                    styles["body"],
                ),
                item.get("unit") or "kg",
                _fmt_qty(float(item.get("quantity") or 0)),
                _fmt_money(item.get("unit_price")),
                _fmt_money(item.get("line_total")),
            ]
        )
    if not items:
        rows.append([
            "—",
            Paragraph("No line items extracted from the conversation yet.", styles["body"]),
            "",
            "",
            "",
            "",
        ])

    line_table = Table(
        rows,
        colWidths=[10 * mm, 78 * mm, 16 * mm, 18 * mm, 20 * mm, 24 * mm],
        repeatRows=1,
        hAlign="LEFT",
    )
    line_table.setStyle(
        TableStyle(
            [
                # Header row
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
                ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 8.5),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                ("TOPPADDING", (0, 0), (-1, 0), 6),
                # Body
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, -1), 10),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 1), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
                ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
                ("ALIGN", (2, 0), (2, -1), "CENTER"),
                # Grid
                ("LINEBELOW", (0, 0), (-1, 0), 0.75, BORDER),
                ("LINEBELOW", (0, 1), (-1, -2), 0.25, BORDER),
                ("LINEBELOW", (0, -1), (-1, -1), 0.5, BORDER),
            ]
        )
    )
    story.append(line_table)
    story.append(Spacer(1, 6))

    # Grand total — right-justified under the Unit price / Line total columns
    # of the line-items table. Tight label+amount pair, no dead space between.
    grand_total = draft.get("grand_total")
    total_table = Table(
        [["Grand total", _fmt_money(grand_total)]],
        colWidths=[20 * mm, 24 * mm],
        hAlign="RIGHT",
    )
    total_table.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (0, 0), "RIGHT"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 11.5),
                ("TEXTCOLOR", (1, 0), (1, 0), ACCENT),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    story.append(total_table)
    story.append(Spacer(1, 14))

    # ── Notes / warnings ──────────────────────────────────────────────────
    notes = draft.get("notes")
    if notes:
        story.append(Paragraph("NOTES", styles["section"]))
        story.append(Paragraph(str(notes).replace("\n", "<br/>"), styles["body"]))
        story.append(Spacer(1, 10))

    warnings = draft.get("warnings") or []
    if warnings:
        story.append(Paragraph("REVIEW BEFORE SENDING", styles["section"]))
        for w in warnings:
            story.append(Paragraph(f"• {w}", styles["warn"]))
        story.append(Spacer(1, 10))

    # ── Footer ────────────────────────────────────────────────────────────
    story.append(Spacer(1, 16))
    story.append(
        Paragraph(
            "Drafted by AgnesTheSecond from a conversational transcript. "
            "Review all fields and sign before sending to the supplier. "
            "Prices (when shown) are market-benchmark references — confirm "
            "with the supplier before commitment.",
            styles["footer"],
        )
    )

    doc.build(story)
    return out_path
