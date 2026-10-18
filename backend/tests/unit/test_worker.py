import logging
from typing import Any

import pytest

from app.core.config import Settings
from app.worker import main


def test_sin_base_de_datos_el_worker_no_arranca(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="scz.worker")

    assert main(Settings(_env_file=None, environment="test")) == 2
    assert "DATABASE_URL" in caplog.text


def test_el_lease_debe_ser_mayor_que_el_timeout_de_un_trabajo() -> None:
    # Si no, el reaper le quitaría trabajos vivos a un worker sano: doble procesamiento
    with pytest.raises(ValueError, match="job_lease_s"):
        Settings(_env_file=None, environment="test", job_timeout_s=120, job_lease_s=120)


def test_valores_por_defecto_del_worker() -> None:
    settings = Settings(_env_file=None, environment="test")

    assert (settings.worker_concurrency, settings.job_timeout_s, settings.job_lease_s) == (16, 60.0, 300)


async def test_run_worker_exige_base_de_datos() -> None:
    from app.worker import run_worker

    with pytest.raises(ValueError, match="DATABASE_URL"):
        await run_worker(Settings(_env_file=None, environment="test"))


_LLM = {
    "llm_api_key": "sk-ant-clave-de-prueba",
    "llm_model": "claude-haiku-5-5",
    "llm_price_input_usd_per_mtok": "0.10",
    "llm_price_output_usd_per_mtok": "0.50",
    "llm_price_cache_read_usd_per_mtok": "0.01",
    "llm_price_cache_write_usd_per_mtok": "0.125",
}


async def test_con_la_ia_configurada_el_agente_atiende_las_conversaciones() -> None:
    import httpx

    from app.application.agent import ProcessConversation
    from app.domain.jobs import PROCESS_CONVERSATION
    from app.worker import build_handlers

    async with httpx.AsyncClient() as http:
        handlers = build_handlers(Settings(_env_file=None, environment="test", **_LLM), object(), http)  # type: ignore[arg-type]

    assert isinstance(handlers[PROCESS_CONVERSATION], ProcessConversation)


async def test_sin_la_ia_configurada_las_conversaciones_esperan(caplog: pytest.LogCaptureFixture) -> None:
    import httpx

    from app.domain.jobs import PROCESS_CONVERSATION
    from app.worker import build_handlers

    caplog.set_level(logging.WARNING, logger="scz.worker")
    async with httpx.AsyncClient() as http:
        handlers = build_handlers(Settings(_env_file=None, environment="test"), object(), http)  # type: ignore[arg-type]

    assert PROCESS_CONVERSATION not in handlers
    assert "LLM_API_KEY" in caplog.text


@pytest.mark.parametrize(
    ("provider", "model", "adapter"),
    [
        ("anthropic", "claude-haiku-5-5", "AnthropicChatModel"),
        ("gemini", "gemini-3.8-flash", "GeminiChatModel"),
    ],
)
async def test_el_worker_usa_el_adaptador_del_proveedor_configurado(
    provider: str, model: str, adapter: str
) -> None:
    import httpx

    from app.worker import build_chat_adapter

    llm: dict[str, Any] = {**_LLM, "llm_provider": provider, "llm_model": model}
    settings = Settings(_env_file=None, environment="test", **llm)
    async with httpx.AsyncClient() as http:
        name, chat = build_chat_adapter(settings, http)

    assert (name, type(chat).__name__) == (provider, adapter)
