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
    # Versión fijada de la Graph API (p. ej. v23.0). Sin ella el worker no envía: no se adivina.
    meta_graph_api_version: str | None = Field(default=None, pattern=r"^v\d{1,3}\.\d{1,2}$")

    # Observabilidad (§10.2): sin endpoint no se exporta; sin DSN no hay Sentry.
    otel_exporter_otlp_endpoint: str | None = Field(default=None, pattern=r"^https?://[^\s]+$")
    sentry_dsn: SecretStr | None = Field(default=None)

    # Worker (ADR-0005, SYSTEM_DESIGN.md §9.2): concurrencia de asyncio, sondeo de la cola y lease.
    worker_concurrency: int = Field(default=16, ge=1, le=256)
    worker_poll_interval_s: float = Field(default=0.25, gt=0, le=10)
    job_timeout_s: float = Field(default=60.0, ge=1, le=900)
    job_lease_s: int = Field(default=300, ge=30, le=3600)

    @model_validator(mode="after")
    def _lease_longer_than_job_timeout(self) -> "Settings":
        """Si el lease venciera antes que el timeout, el reaper le quitaría trabajos vivos a un
        worker sano y otro los procesaría a la vez (doble respuesta al cliente)."""
        if self.job_lease_s <= self.job_timeout_s:
            raise ValueError("job_lease_s debe ser mayor que job_timeout_s")
        return self

    @model_validator(mode="after")
    def _secrets_required_outside_local(self) -> "Settings":
        """Falla cerrado: en staging y producción el proceso no arranca sin sus secretos."""
        if self.environment in ("staging", "production"):
            required = (
                "database_url",
                "meta_app_secret",
                "meta_webhook_verify_token",
                "meta_graph_api_version",
            )
            missing = [name for name in required if getattr(self, name) is None]
            if missing:
                raise ValueError(f"faltan variables obligatorias en {self.environment}: {', '.join(missing)}")
            endpoint = self.otel_exporter_otlp_endpoint
            if endpoint is not None and not endpoint.startswith("https://"):
                # Las trazas llevan IDs de negocio y el encabezado de autenticación del colector
                raise ValueError(f"OTEL_EXPORTER_OTLP_ENDPOINT debe ser https en {self.environment}")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Configuración del proceso, leída una sola vez."""
    return Settings()
