"""PDF de cotización (#26, RF-20, SYSTEM_DESIGN §8.3): contenido, formato boliviano y snapshot.

Las cifras las calculó la BD (create_quote): el PDF solo las formatea. El snapshot es el PDF
completo, byte a byte (reportlab en modo invariante: mismo documento, mismos bytes). Si un cambio
de diseño es intencional, se regenera con SCZ_UPDATE_SNAPSHOTS=1 y se revisa el PDF a mano.
"""

import io
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pypdf import PdfReader

from app.domain.catalog import TaxMode
from app.domain.quote_document import QuoteDocument, QuoteDocumentLine
from app.infrastructure.pdf.reportlab_renderer import ReportlabQuoteRenderer

SNAPSHOTS = Path(__file__).resolve().parents[1] / "snapshots"

DOC = QuoteDocument(
    business_name="Importadora de Ejemplo",
    legal_name="Importadora de Ejemplo S.R.L.",
    nit="1234567019",
    reference_code="COT-2026-000123",
    issued_at=datetime(2026, 10, 18, 14, 30, tzinfo=UTC),  # 10:30 en Santa Cruz
    valid_until=datetime(2026, 10, 19, 14, 30, tzinfo=UTC),
    timezone="America/La_Paz",
    customer_name="Juan Pérez",
    lines=(
        QuoteDocumentLine("PF-001", "Pastillas de freno delanteras (Hilux 2016-2024)", 2, Decimal("480.00"),
                          Decimal("960.00")),
        QuoteDocumentLine("FA-210", "Filtro de aceite", 1, Decimal("292.00"), Decimal("292.00")),
    ),
    subtotal_bs=Decimal("1252.00"),
    iva_bs=Decimal("162.76"),
    total_bs=Decimal("1252.00"),
    deposit_required_bs=Decimal("626.00"),
    applied_fx_rate=Decimal("11.6800"),
    tax_mode=TaxMode.IVA_INCLUIDO,
    iva_percent=Decimal("13.00"),
)  # fmt: skip


def _text(pdf: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf))
    return "\n".join(page.extract_text() for page in reader.pages)


def _render(doc: QuoteDocument = DOC) -> bytes:
    return ReportlabQuoteRenderer().render(doc)


def _with(**changes: object) -> QuoteDocument:
    from dataclasses import replace

    return replace(DOC, **changes)  # type: ignore[arg-type]


# --- Contenido ------------------------------------------------------------------------------------
def test_es_un_pdf_de_una_pagina_con_titulo() -> None:
    pdf = _render()

    assert pdf.startswith(b"%PDF-")
    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) == 1
    assert reader.metadata is not None and reader.metadata.title == "Cotización COT-2026-000123"


def test_muestra_negocio_cliente_referencia_y_vigencia_en_hora_de_bolivia() -> None:
    text = _text(_render())

    for expected in (
        "Importadora de Ejemplo S.R.L.", "NIT 1234567019", "COTIZACIÓN COT-2026-000123", "Juan Pérez",
        "18/10/2026 10:30", "Válida hasta: 19/10/2026 10:30",
    ):  # fmt: skip
        assert expected in text, expected


def test_las_cifras_van_en_formato_boliviano_tal_como_las_calculo_la_bd() -> None:
    text = _text(_render())

    for expected in ("PF-001", "480,00", "960,00", "292,00", "1.252,00", "162,76", "626,00"):
        assert expected in text, expected


def test_muestra_el_tipo_de_cambio_aplicado() -> None:
    assert "Tipo de cambio aplicado: 11,68 Bs/USD" in _text(_render())


def test_sin_items_en_dolares_no_muestra_tipo_de_cambio() -> None:
    assert "Tipo de cambio" not in _text(_render(_with(applied_fx_rate=None)))


@pytest.mark.parametrize(
    ("mode", "expected"),
    [(TaxMode.IVA_INCLUIDO, "IVA (13 %) incluido"), (TaxMode.IVA_ADICIONAL, "IVA (13 %)")],
)
def test_explica_el_iva_segun_el_regimen(mode: TaxMode, expected: str) -> None:
    text = _text(_render(_with(tax_mode=mode)))

    assert expected in text
    assert ("incluido" in text) is (mode is TaxMode.IVA_INCLUIDO)


def test_la_sena_solo_aparece_si_el_negocio_la_pide() -> None:
    assert "Seña requerida" in _text(_render())
    assert "Seña" not in _text(_render(_with(deposit_required_bs=Decimal("0.00"))))


def test_aclara_que_no_es_una_factura() -> None:
    """ADR-0014: la plataforma no factura en el MVP; una cotización no reemplaza la factura."""
    assert "no es una factura" in _text(_render())


def test_sin_nit_ni_razon_social_usa_el_nombre_comercial() -> None:
    text = _text(_render(_with(legal_name=None, nit=None)))

    assert "Importadora de Ejemplo" in text and "NIT" not in text


def test_una_cotizacion_larga_pasa_a_mas_paginas_sin_perder_lineas() -> None:
    lines = tuple(
        QuoteDocumentLine(f"SKU-{n:03d}", f"Repuesto número {n}", 1, Decimal("10.00"), Decimal("10.00"))
        for n in range(50)  # el máximo de líneas de create_quote
    )
    pdf = _render(_with(lines=lines))

    text = _text(pdf)
    assert len(PdfReader(io.BytesIO(pdf)).pages) >= 2
    assert all(f"SKU-{n:03d}" in text for n in range(50))


def test_el_texto_del_catalogo_no_rompe_el_pdf() -> None:
    """Los nombres vienen del catálogo del negocio: se escapan, no se interpretan como marcado."""
    line = QuoteDocumentLine("X<1>", "Junta <b>tapa</b> & sello", 1, Decimal("5.00"), Decimal("5.00"))

    text = _text(_render(_with(lines=(line,))))

    assert "Junta <b>tapa</b> & sello" in text


# --- Nombre de archivo y determinismo -------------------------------------------------------------
def test_el_nombre_del_archivo_lleva_la_referencia() -> None:
    assert DOC.filename == "Cotizacion-COT-2026-000123.pdf"
    assert _with(reference_code="COT/../x y").filename == "Cotizacion-COT-x-y.pdf"


def test_el_mismo_documento_da_los_mismos_bytes() -> None:
    assert _render() == _render()


def test_snapshot_del_pdf() -> None:
    golden = SNAPSHOTS / "cotizacion_iva_incluido.pdf"
    pdf = _render()
    if os.environ.get("SCZ_UPDATE_SNAPSHOTS") == "1":
        golden.parent.mkdir(exist_ok=True)
        golden.write_bytes(pdf)
    assert golden.exists(), "falta el snapshot: correr con SCZ_UPDATE_SNAPSHOTS=1 y revisar el PDF"
    assert pdf == golden.read_bytes(), "el PDF cambió: si es intencional, regenerar el snapshot y revisarlo"
