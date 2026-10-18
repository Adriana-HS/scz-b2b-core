"""private.llm_calls: costo de cada llamada a la IA por tenant (#22, Q-COST-1).

`organization_id` sale de `private.backend_org_id()`, nunca del objeto: la fila va al tenant
de la transacción, y la RLS y la FK compuesta (migración 19) rechazan cualquier mezcla.
"""

from typing import TYPE_CHECKING

from app.domain.ai import LlmCall
from app.infrastructure.postgres.errors import translate_errors

if TYPE_CHECKING:
    from app.infrastructure.postgres.unit_of_work import Session

_INSERT = """
    INSERT INTO private.llm_calls
        (organization_id, conversation_id, purpose, provider, model, input_tokens, output_tokens,
         cached_tokens, cost_usd, latency_ms, tool_calls, outcome, trace_id)
    VALUES (private.backend_org_id(), $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
"""


class PostgresLlmCallRepository:
    def __init__(self, session: "Session") -> None:
        self._session = session

    async def add(self, call: LlmCall) -> None:
        connection = self._session.connection()
        with translate_errors():
            await connection.execute(
                _INSERT,
                call.conversation_id,
                call.purpose.value,
                call.provider,
                call.model,
                call.usage.input_tokens,
                call.usage.output_tokens,
                call.usage.cache_read_tokens,
                call.cost_usd,
                call.latency_ms,
                call.tool_calls,
                call.outcome,
                call.trace_id,
            )
