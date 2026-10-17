"""Configuración por entorno (12-Factor). Los secretos nunca tienen valor por defecto."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]


class Settings(BaseSettings):
    """Variables de entorno del backend (ver .env.example en la raíz)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Environment = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Rol app_backend vía Supavisor en modo transacción (ADR-0002, ADR-0011). Nunca la service role.
    database_url: SecretStr | None = Field(default=None)
    # WhatsApp Cloud API: App Secret (firma HMAC de cada webhook) y token de verificación de la suscripción
    meta_app_secret: SecretStr | None = Field(default=None)
    meta_webhook_verify_token: SecretStr | None = Field(default=None)

    @model_validator(mode="after")
    def _secrets_required_outside_local(self) -> "Settings":
        """Falla cerrado: en staging y producción el proceso no arranca sin sus secretos."""
        if self.environment in ("staging", "production"):
            required = ("database_url", "meta_app_secret", "meta_webhook_verify_token")
            missing = [name for name in required if getattr(self, name) is None]
            if missing:
                raise ValueError(f"faltan variables obligatorias en {self.environment}: {', '.join(missing)}")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Configuración del proceso, leída una sola vez."""
    return Settings()
