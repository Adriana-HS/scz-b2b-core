"""Resolución de canal: la única lectura antes de conocer el tenant (ADR-0002 §6, §8.2).

`private.resolve_whatsapp_channel` es SECURITY DEFINER con `search_path` fijo y solo
devuelve canales activos de negocios activos. Se ejecuta en una transacción corta y
propia, sin `app.current_org_id`: no ve ni escribe nada más.
"""

import asyncpg

from app.domain.messaging import WhatsAppChannel
from app.infrastructure.postgres.errors import translate_errors

_RESOLVE = "SELECT organization_id, channel_id, access_token_ref FROM private.resolve_whatsapp_channel($1)"


class PostgresChannelResolver:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def resolve(self, phone_number_id: str) -> WhatsAppChannel | None:
        with translate_errors():
            row = await self._pool.fetchrow(_RESOLVE, phone_number_id)
        if row is None:
            return None
        return WhatsAppChannel(
            organization_id=row["organization_id"],
            channel_id=row["channel_id"],
            access_token_ref=row["access_token_ref"],
        )
