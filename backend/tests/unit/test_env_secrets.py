"""EnvSecretStore: una fila de la BD no puede pedir cualquier variable del proceso."""

from app.infrastructure.secret_store.env import EnvSecretStore

ENV = {"WA_TOKEN_IMPORTADORA_A": "token-a", "DATABASE_URL": "postgresql://secreto", "WA_TOKEN_VACIO": ""}


def test_lee_solo_tokens_de_whatsapp() -> None:
    store = EnvSecretStore(ENV)

    assert store.get("WA_TOKEN_IMPORTADORA_A") == "token-a"
    assert store.get("DATABASE_URL") is None  # access_token_ref no puede apuntar a otro secreto
    assert store.get("wa_token_importadora_a") is None
    assert store.get("WA_TOKEN_../x") is None


def test_un_token_vacio_o_ausente_es_none() -> None:
    store = EnvSecretStore(ENV)

    assert store.get("WA_TOKEN_VACIO") is None
    assert store.get("WA_TOKEN_NO_EXISTE") is None
