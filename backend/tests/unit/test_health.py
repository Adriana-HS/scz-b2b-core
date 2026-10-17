from fastapi.testclient import TestClient

from app import __version__
from app.core.config import Settings
from app.main import create_app


def _client(environment: str) -> TestClient:
    return TestClient(create_app(Settings(_env_file=None, environment=environment)))


def test_health_responde_ok_con_la_version() -> None:
    response = _client("test").get("/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_en_produccion_no_se_publica_la_documentacion_de_la_api() -> None:
    client = TestClient(
        create_app(
            Settings(
                _env_file=None,
                environment="production",
                database_url="postgresql://app_backend@localhost/postgres",
                meta_app_secret="secreto-de-prueba",
                meta_webhook_verify_token="token-de-prueba",
                meta_graph_api_version="v23.0",
            )
        )
    )

    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_en_local_la_documentacion_esta_disponible() -> None:
    assert _client("local").get("/openapi.json").status_code == 200
