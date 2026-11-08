"""PDF and CSV report generation (local, no cloud services)."""

from __future__ import annotations

import io
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..config import APP_NAME, APP_VERSION, SAFETY_DISCLAIMER
from .incidents import IncidentService

INK = colors.HexColor("#1F2A2E")
MUTED = colors.HexColor("#5C6B70")
TEAL = colors.HexColor("#2F7A73")
BLUE = colors.HexColor("#3D6FA8")
RULE = colors.HexColor("#DCD8CF")
WARM = colors.HexColor("#FAF8F3")


def _get_styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "TitleX", parent=base["Title"], fontName="Helvetica-Bold", fontSize=20,
            textColor=INK, alignment=TA_LEFT, spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "SubX", parent=base["Normal"], fontSize=9.5, textColor=MUTED, leading=13,
        ),
        "h2": ParagraphStyle(
            "H2X", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=12,
            textColor=TEAL, spaceBefore=10, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "BodyX", parent=base["Normal"], fontSize=9, textColor=INK, leading=12.5,
        ),
        "small": ParagraphStyle(
            "SmallX", parent=base["Normal"], fontSize=7.8, textColor=MUTED, leading=10.5,
        ),
        "cell": ParagraphStyle(
            "CellX", parent=base["Normal"], fontSize=8, textColor=INK, leading=10.5,
        ),
        "disclaimer": ParagraphStyle(
            "DiscX", parent=base["Normal"], fontSize=7.8, textColor=colors.HexColor("#8A5A2B"),
            leading=11, backColor=colors.HexColor("#FBF3E6"), borderPadding=6,
        ),
    }


def build_incident_report(
    incidents: Sequence[dict[str, Any]],
    operator: str = "",
    title: str = "CivicWatch incident report",
    include_evidence: bool = True,
) -> bytes:
    styles = _get_styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title=title,
        author=APP_NAME,
        subject="Potential civic violations for human review",
    )
    story: list[Any] = []
    generated = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    story.append(Paragraph(title, styles["title"]))
    story.append(
        Paragraph(
            f"{APP_NAME} v{APP_VERSION} · generated {generated}"
            + (f" · operator: {operator}" if operator else " · local operator")
            + f" · {len(incidents)} incident(s)",
            styles["subtitle"],
        )
    )
    story.append(Spacer(1, 8))
    story.append(Paragraph(SAFETY_DISCLAIMER, styles["disclaimer"]))
    story.append(Spacer(1, 10))

    summary = _summary_table(incidents, styles)
    story.append(Paragraph("Summary", styles["h2"]))
    story.append(summary)
    story.append(Spacer(1, 6))

    story.append(Paragraph("Incident register", styles["h2"]))
    story.append(_register_table(incidents, styles))

    if include_evidence and incidents:
        story.append(PageBreak())
        story.append(Paragraph("Evidence detail", styles["h2"]))
        for index, incident in enumerate(incidents, start=1):
            story.append(_incident_block(index, incident, styles))
            if index != len(incidents):
                story.append(Spacer(1, 6))

    story.append(Spacer(1, 10))
    story.append(
        Paragraph(
            "Automated indicators are advisory. Confirm or dismiss each incident after reviewing "
            "the evidence. CivicWatch does not issue challans or penalties.",
            styles["small"],
        )
    )
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()


def _footer(canvas, doc) -> None:  # pragma: no cover - drawing callback
    canvas.saveState()
    width, _ = A4
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.6)
    canvas.line(16 * mm, 14 * mm, width - 16 * mm, 14 * mm)
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(16 * mm, 9.5 * mm, f"{APP_NAME} · local processing · human review required")
    canvas.drawRightString(width - 16 * mm, 9.5 * mm, f"Page {doc.page}")
    canvas.restoreState()


def _summary_table(incidents: Sequence[dict[str, Any]], styles) -> Table:
    statuses = ["detected", "pending_review", "verified", "dismissed", "forwarded", "resolved"]
    counts = {status: 0 for status in statuses}
    for incident in incidents:
        counts[incident.get("status", "detected")] = counts.get(incident.get("status"), 0) + 1
    header = ["Total", "Awaiting review", "Verified", "Dismissed", "Forwarded", "Resolved"]
    awaiting = counts["detected"] + counts["pending_review"]
    values = [len(incidents), awaiting, counts["verified"], counts["dismissed"],
              counts["forwarded"], counts["resolved"]]
    table = Table([header, values], hAlign="LEFT", colWidths=[28 * mm] * 6)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), WARM),
                ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica"),
                ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("TEXTCOLOR", (0, 1), (-1, 1), INK),
                ("GRID", (0, 0), (-1, -1), 0.4, RULE),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _register_table(incidents: Sequence[dict[str, Any]], styles) -> Table:
    header = ["ID", "Category", "Camera", "Captured (UTC)", "Conf.", "Status"]
    rows: list[list[Any]] = [header]
    for incident in incidents:
        rows.append(
            [
                incident.get("id", ""),
                Paragraph(incident.get("label", ""), styles["cell"]),
                Paragraph(str(incident.get("camera", "")), styles["cell"]),
                str(incident.get("captured_at", "")).replace("T", " ")[:19],
                f"{float(incident.get('confidence') or 0):.2f}",
                str(incident.get("status", "")).replace("_", " "),
            ]
        )
    table = Table(
        rows,
        hAlign="LEFT",
        colWidths=[32 * mm, 46 * mm, 44 * mm, 32 * mm, 12 * mm, 22 * mm],
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), WARM),
                ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
                ("FONTSIZE", (0, 0), (-1, -1), 7.8),
                ("TEXTCOLOR", (0, 1), (-1, -1), INK),
                ("GRID", (0, 0), (-1, -1), 0.4, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _incident_block(index: int, incident: dict[str, Any], styles) -> KeepTogether:
    heading = Paragraph(
        f"{index}. {incident.get('id')} — {incident.get('label')}", styles["h2"]
    )
    details = [
        ["Category", incident.get("label", "")],
        ["Detected class", incident.get("class_name") or "—"],
        ["Confidence", f"{float(incident.get('confidence') or 0):.2f}"],
        ["Captured (UTC)", str(incident.get("captured_at", "")).replace("T", " ")],
        ["Camera", incident.get("camera", "")],
        ["Location", incident.get("location") or "—"],
        ["Zone", incident.get("zone_name") or "—"],
        ["Dwell", f"{incident.get('dwell_seconds'):.1f} s" if incident.get("dwell_seconds") else "—"],
        ["Track ID", str(incident.get("track_id")) if incident.get("track_id") is not None else "—"],
        ["Status", str(incident.get("status", "")).replace("_", " ")],
        ["Severity", incident.get("severity", "")],
    ]
    table = Table(details, hAlign="LEFT", colWidths=[34 * mm, 68 * mm])
    table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
                ("TEXTCOLOR", (1, 0), (1, -1), INK),
                ("LINEBELOW", (0, 0), (-1, -1), 0.3, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
            ]
        )
    )
    elements: list[Any] = [heading, table]
    image_path = incident.get("snapshot_path")
    if image_path and Path(image_path).exists():
        try:
            elements.append(Spacer(1, 4))
            elements.append(Image(str(image_path), width=88 * mm, height=None, kind="proportional"))
            elements.append(Paragraph("Evidence frame captured at detection time.", styles["small"]))
        except Exception:  # pragma: no cover - unreadable image
            pass
    return KeepTogether(elements)


def build_csv(incident_ids: Sequence[str] | None = None) -> str:
    return IncidentService.csv_rows(incident_ids)
