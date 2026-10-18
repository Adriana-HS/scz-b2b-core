"""PDF de cotización con reportlab (#26, SYSTEM_DESIGN §4: pdf/reportlab_renderer.py).

* Solo formatea: las cifras vienen de la BD (QuoteDocument). Formato boliviano: 1.252,00.
* Fechas en la zona horaria del negocio (organizations.timezone).
* Texto del catálogo escapado: un "<b>" en un nombre se muestra tal cual, no es marcado.
* Modo invariante: el mismo documento da los mismos bytes (snapshot en las pruebas).
* Fuentes estándar de PDF (Helvetica, WinAnsi): cubren el español sin incrustar archivos.
"""

import io
from datetime import datetime
from decimal import Decimal
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Flowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.domain.catalog import TaxMode
from app.domain.quote_document import QuoteDocument

_BRAND = colors.HexColor("#1F3A5F")
_STYLES = getSampleStyleSheet()
_TITLE = ParagraphStyle("Titulo", parent=_STYLES["Title"], fontSize=16, textColor=_BRAND, spaceAfter=2 * mm)
_BODY = ParagraphStyle("Cuerpo", parent=_STYLES["BodyText"], fontSize=9, leading=12)
_SMALL = ParagraphStyle("Nota", parent=_BODY, fontSize=8, leading=10, textColor=colors.HexColor("#555555"))
_CELL = ParagraphStyle("Celda", parent=_BODY, fontSize=8.5, leading=10.5)
_CELL_RIGHT = ParagraphStyle("CeldaDerecha", parent=_CELL, alignment=TA_RIGHT)


def _money(value: Decimal) -> str:
    """1252.00 -> "1.252,00". Solo formato: no redondea más allá de los 2 decimales de la BD."""
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _rate(value: Decimal) -> str:
    """11.6800 -> "11,68": el TC sin ceros de más (hasta 4 decimales, como en la BD)."""
    text = f"{value:.4f}".rstrip("0")
    text = text + "00" if text.endswith(".") else text
    whole, _, decimals = text.partition(".")
    return f"{whole},{decimals.ljust(2, '0')}"


def _percent(value: Decimal) -> str:
    return f"{value.normalize():f}".replace(".", ",")


def _when(moment: datetime, timezone: str) -> str:
    return moment.astimezone(ZoneInfo(timezone)).strftime("%d/%m/%Y %H:%M")


def _p(text: str, style: ParagraphStyle = _BODY) -> Paragraph:
    return Paragraph(escape(text), style)


class ReportlabQuoteRenderer:
    def render(self, document: QuoteDocument) -> bytes:
        buffer = io.BytesIO()
        title = f"Cotización {document.reference_code}"
        pdf = SimpleDocTemplate(
            buffer, pagesize=LETTER, title=title, author=document.business_name, subject=title,
            creator="SCZ-B2B-Core", leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
            bottomMargin=16 * mm, invariant=1,
        )  # fmt: skip
        pdf.build(self._story(document))
        return buffer.getvalue()

    def _story(self, doc: QuoteDocument) -> list[Flowable]:
        issuer = doc.legal_name or doc.business_name
        story: list[Flowable] = [_p(issuer, ParagraphStyle("Emisor", parent=_BODY, fontSize=11, leading=14))]
        if doc.nit:
            story.append(_p(f"NIT {doc.nit}"))
        story += [
            Spacer(1, 4 * mm),
            _p(f"COTIZACIÓN {doc.reference_code}", _TITLE),
            _p(f"Fecha: {_when(doc.issued_at, doc.timezone)}"),
            _p(f"Válida hasta: {_when(doc.valid_until, doc.timezone)}"),
        ]
        if doc.customer_name:
            story.append(_p(f"Cliente: {doc.customer_name}"))
        story += [
            Spacer(1, 5 * mm),
            self._lines(doc),
            Spacer(1, 4 * mm),
            self._totals(doc),
            Spacer(1, 5 * mm),
        ]
        story += [_p(note, _SMALL) for note in self._notes(doc)]
        return story

    @staticmethod
    def _lines(doc: QuoteDocument) -> Table:
        header = ["Código", "Descripción", "Cant.", "P. unit. (Bs)", "Importe (Bs)"]
        rows: list[list[Flowable]] = [[_p(h, _CELL) for h in header]]
        rows += [
            [_p(line.sku, _CELL), _p(line.name, _CELL), _p(str(line.quantity), _CELL_RIGHT),
             _p(_money(line.unit_price_bs), _CELL_RIGHT), _p(_money(line.line_total_bs), _CELL_RIGHT)]
            for line in doc.lines
        ]  # fmt: skip
        table = Table(rows, colWidths=[28 * mm, 76 * mm, 14 * mm, 28 * mm, 28 * mm], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF5")),
            ("LINEBELOW", (0, 0), (-1, 0), 0.6, _BRAND),
            ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#CCCCCC")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))  # fmt: skip
        return table

    @staticmethod
    def _totals(doc: QuoteDocument) -> Table:
        iva = f"IVA ({_percent(doc.iva_percent)} %)"
        if doc.tax_mode is TaxMode.IVA_INCLUIDO:
            iva += " incluido"
        rows = [
            ["Subtotal", _money(doc.subtotal_bs)],
            [iva, _money(doc.iva_bs)],
            ["Total (Bs)", _money(doc.total_bs)],
        ]
        if doc.deposit_required_bs > 0:
            rows.append(["Seña requerida (Bs)", _money(doc.deposit_required_bs)])
        table = Table(
            [[_p(a, _CELL), _p(b, _CELL_RIGHT)] for a, b in rows],
            colWidths=[48 * mm, 28 * mm],
            hAlign="RIGHT",
        )
        table.setStyle(TableStyle([
            ("LINEABOVE", (0, 2), (-1, 2), 0.6, _BRAND),
            ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
        ]))  # fmt: skip
        return table

    @staticmethod
    def _notes(doc: QuoteDocument) -> list[str]:
        notes = ["Precios en bolivianos (Bs)."]
        if doc.applied_fx_rate is not None:
            notes.append(f"Tipo de cambio aplicado: {_rate(doc.applied_fx_rate)} Bs/USD.")
        notes += [
            "El stock queda reservado hasta la fecha de validez de esta cotización.",
            "Este documento es una cotización y no es una factura.",
        ]
        return notes
