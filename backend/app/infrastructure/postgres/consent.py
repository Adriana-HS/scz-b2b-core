"""Repositorio de consentimiento (migración 17). El estado y la evidencia los escribe una sola
función de la BD, en la misma transacción, con el tenant de la UnitOfWork."""

from typing import TYPE_CHECKING
from uuid import UUID

from app.domain.consent import ConsentKind, ConsentSource
from app.infrastructure.postgres.errors import translate_errors

if TYPE_CHECKING:
    from app.infrastructure.postgres.unit_of_work import Session


class PostgresConsentRepository:
    def __init__(self, session: "Session") -> None:
        self._session = session

    async def record(
        self, customer_id: UUID, kind: ConsentKind, source: ConsentSource, evidence: str | None
    ) -> None:
        connection = self._session.connection()
        with translate_errors():
            await connection.execute(
                "SELECT private.record_consent($1, $2, $3, $4)",
                customer_id,
                kind.value,
                source.value,
                evidence,
            )
