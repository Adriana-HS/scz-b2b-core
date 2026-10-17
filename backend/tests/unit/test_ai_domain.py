"""Tipos del dominio de IA (#22): mensajes, herramientas, uso de tokens, costo y modelo fijado."""

from decimal import Decimal

import pytest

from app.domain.ai import (
    ChatMessage,
    ModelPricing,
    Role,
    TokenUsage,
    ToolCall,
    ToolResult,
    ToolSpec,
    validate_model_id,
)

PRICING = ModelPricing(
    input_usd_per_mtok=Decimal("3"),
    output_usd_per_mtok=Decimal("15"),
    cache_read_usd_per_mtok=Decimal("0.30"),
    cache_write_usd_per_mtok=Decimal("3.75"),
)


def test_el_costo_es_exacto_en_decimal() -> None:
    usage = TokenUsage(input_tokens=1200, output_tokens=350, cache_read_tokens=8000, cache_write_tokens=100)

    # 1200*3 + 350*15 + 8000*0.30 + 100*3.75 = 3600 + 5250 + 2400 + 375 = 11625 por millón
    assert PRICING.cost(usage) == Decimal("0.011625")


def test_el_costo_se_redondea_a_seis_decimales_como_la_columna() -> None:
    """private.llm_calls.cost_usd es numeric(12,6)."""
    assert PRICING.cost(TokenUsage(input_tokens=1)) == Decimal("0.000003")
    assert ModelPricing(Decimal("0.25"), Decimal("0"), Decimal("0"), Decimal("0")).cost(
        TokenUsage(input_tokens=3)
    ) == Decimal("0.000001")  # 0.00000075 redondea hacia arriba


def test_sin_uso_no_hay_costo() -> None:
    assert PRICING.cost(TokenUsage()) == Decimal("0")


@pytest.mark.parametrize("field", ["input_usd_per_mtok", "output_usd_per_mtok"])
def test_un_precio_negativo_se_rechaza(field: str) -> None:
    values = {
        "input_usd_per_mtok": Decimal("1"),
        "output_usd_per_mtok": Decimal("1"),
        "cache_read_usd_per_mtok": Decimal("0"),
        "cache_write_usd_per_mtok": Decimal("0"),
        field: Decimal("-1"),
    }
    with pytest.raises(ValueError, match="precio"):
        ModelPricing(**values)


def test_tokens_negativos_se_rechazan() -> None:
    with pytest.raises(ValueError, match="tokens"):
        TokenUsage(input_tokens=-1)


@pytest.mark.parametrize("model", ["claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"])
def test_un_modelo_con_identificador_exacto_se_acepta(model: str) -> None:
    assert validate_model_id(model) == model


@pytest.mark.parametrize(
    "model", ["", "claude-3-5-sonnet-latest", "latest", "Claude Sonnet", "claude/../x", "x" * 100]
)
def test_un_alias_o_un_identificador_raro_se_rechaza(model: str) -> None:
    """Un alias (-latest) cambia de modelo sin aviso: cambia el costo y las respuestas."""
    with pytest.raises(ValueError, match="modelo"):
        validate_model_id(model)


@pytest.mark.parametrize("name", ["search_catalog", "create-quote", "a" * 64])
def test_nombres_de_herramienta_validos(name: str) -> None:
    assert ToolSpec(name=name, description="x", input_schema={"type": "object"}).name == name


@pytest.mark.parametrize("name", ["", "con espacio", "a" * 65, "ñandú"])
def test_nombres_de_herramienta_invalidos(name: str) -> None:
    with pytest.raises(ValueError, match="herramienta"):
        ToolSpec(name=name, description="x", input_schema={"type": "object"})


def test_el_esquema_de_una_herramienta_es_un_objeto() -> None:
    with pytest.raises(ValueError, match="esquema"):
        ToolSpec(name="x", description="x", input_schema={"type": "string"})


def test_un_mensaje_del_cliente_lleva_texto_o_resultados_de_herramientas() -> None:
    assert ChatMessage.user("hola").text == "hola"
    results = ChatMessage.from_tool_results(ToolResult(tool_call_id="t1", content='{"ok": true}'))
    assert results.role is Role.USER and results.tool_results[0].tool_call_id == "t1"
    with pytest.raises(ValueError, match="vacío"):
        ChatMessage(role=Role.USER)


def test_solo_el_asistente_pide_herramientas() -> None:
    call = ToolCall(id="t1", name="search_catalog", arguments={"query": "freno"})

    assert ChatMessage.assistant("busco", (call,)).tool_calls == (call,)
    with pytest.raises(ValueError, match="asistente"):
        ChatMessage(role=Role.USER, text="x", tool_calls=(call,))
    with pytest.raises(ValueError, match="cliente"):
        ChatMessage(role=Role.ASSISTANT, tool_results=(ToolResult("t1", "x"),))
