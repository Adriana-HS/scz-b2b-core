"""Chequeo de vida del proceso api (uptime check externo, Q-REL-2)."""

from fastapi import APIRouter
from pydantic import BaseModel

from app import __version__

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str


@router.get("/health")
def health() -> HealthResponse:
    """Responde sin tocar la BD: mide que el proceso esté vivo, no sus dependencias."""
    return HealthResponse(status="ok", version=__version__)
