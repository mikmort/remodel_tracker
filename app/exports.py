from __future__ import annotations

import csv
import io
from html import escape
from pathlib import Path

import reportlab
from reportlab.lib import colors
from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def safe_cell(value) -> str:
    value = "" if value is None else str(value)
    # Spreadsheet applications must not execute a note or model number as a formula.
    if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


def selection_csv(model: dict) -> bytes:
    fields = [
        "id",
        "category",
        "room",
        "item",
        "product",
        "model",
        "finish",
        "size",
        "quantity",
        "selectionState",
        "price",
        "priceBasis",
        "purchaseState",
        "purchaser",
        "selectionBasis",
    ]
    return rows_csv(fields, model["selections"])


def door_csv(model: dict) -> bytes:
    fields = [
        "id",
        "floor",
        "location",
        "lever",
        "function",
        "finish",
        "rosette",
        "hinges",
        "note",
        "confirmation",
    ]
    return rows_csv(fields, model.get("doorHardware", {}).get("schedule", []))


def rows_csv(fields: list[str], rows: list[dict]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=[*fields, "ownerEditBasis", "sources"])
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                **{field: safe_cell(row.get(field, "")) for field in fields},
                "ownerEditBasis": safe_cell(row.get("ownerEdit", {}).get("basis", "")),
                "sources": safe_cell("; ".join(link["url"] for link in row.get("links", []))),
            }
        )
    return output.getvalue().encode("utf-8-sig")


def door_pdf(model: dict, revision: int) -> bytes:
    stream = io.BytesIO()
    document = SimpleDocTemplate(
        stream,
        pagesize=landscape(A3),
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title="Current door hardware schedule",
    )
    styles = getSampleStyleSheet()
    if "ProjectSans" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(
            TTFont("ProjectSans", str(Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"))
        )
    small = styles["BodyText"].clone("Small", fontName="ProjectSans", fontSize=8, leading=10)

    def paragraph(value):
        return Paragraph(escape(str(value or "")).replace("\n", "<br/>"), small)

    fields = ["id", "location", "lever", "function", "finish", "rosette", "hinges", "confirmation"]
    rows = [[paragraph(field.title()) for field in fields]]
    for row in model.get("doorHardware", {}).get("schedule", []):
        cells = [paragraph(row.get(field, "")) for field in fields]
        if row.get("note"):
            cells[1] = paragraph(row.get("location", "") + "\n" + row["note"])
        rows.append(cells)
    table = Table(
        rows,
        colWidths=[24 * mm, 57 * mm, 66 * mm, 49 * mm, 37 * mm, 52 * mm, 74 * mm, 30 * mm],
        repeatRows=1,
        hAlign="LEFT",
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#aaaaaa")),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    document.build(
        [
            Paragraph("Current door hardware schedule", styles["Title"]),
            paragraph(
                f"App revision {revision}. Owner choices only; not a purchase order or installation takeoff."
            ),
            paragraph(
                "This export contains current rows. Refer to preserved source plans for opening geometry. "
                "Component quantities and the original Excel pivot require reconciliation after hardware edits."
            ),
            Spacer(1, 6 * mm),
            table,
        ]
    )
    return stream.getvalue()
