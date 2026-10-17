"""Errores del dominio con códigos estables.

Las RPC y los triggers de la BD señalan sus errores con un HINT estable
(`RAISE ... USING HINT = '<CODIGO>'`). El adaptador de Postgres los traduce a estas
excepciones, y la capa de aplicación decide el mensaje al cliente según el código
(SYSTEM_DESIGN.md §7.2). Si se agrega un HINT nuevo en una migración, se agrega aquí.
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    # Reglas de negocio (SQLSTATE P0001)
    ITEM_NOT_FOUND = "ITEM_NOT_FOUND"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    STOCK_CONFLICT = "STOCK_CONFLICT"
    FX_STALE = "FX_STALE"
    SLOT_TAKEN = "SLOT_TAKEN"
    SLOT_IN_PAST = "SLOT_IN_PAST"
    OUTSIDE_BUSINESS_HOURS = "OUTSIDE_BUSINESS_HOURS"
    INVALID_ITEMS = "INVALID_ITEMS"
    INVALID_QUANTITY = "INVALID_QUANTITY"
    DUPLICATE_ITEM = "DUPLICATE_ITEM"
    INVALID_STATE = "INVALID_STATE"
    INVALID_AMOUNT = "INVALID_AMOUNT"
    INVALID_REJECTION = "INVALID_REJECTION"
    EVIDENCE_FSM = "EVIDENCE_FSM"
    EVIDENCE_FROZEN = "EVIDENCE_FROZEN"
    EVIDENCE_FINAL = "EVIDENCE_FINAL"
    EVIDENCE_IMMUTABLE = "EVIDENCE_IMMUTABLE"
    WINDOW_CLOSED = "WINDOW_CLOSED"  # fuera de la ventana de 24 h de WhatsApp: solo plantillas
    CONSENT_REQUIRED = "CONSENT_REQUIRED"  # plantilla sin opt-in o con baja
    # Acceso y actor (SQLSTATE 42501)
    TENANT_REQUIRED = "TENANT_REQUIRED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    MFA_REQUIRED = "MFA_REQUIRED"
    ROLE_REQUIRED = "ROLE_REQUIRED"
    ACTOR_REQUIRED = "ACTOR_REQUIRED"
    NOT_FOUND = "NOT_FOUND"
    PRICE_CHANGE_FORBIDDEN = "PRICE_CHANGE_FORBIDDEN"
    REVIEW_RPC_ONLY = "REVIEW_RPC_ONLY"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    # El motor rechazó la operación con un HINT que este código aún no conoce
    UNMAPPED = "UNMAPPED"


class DomainError(Exception):
    """Base de los errores que el dominio y la aplicación saben manejar."""


class PermanentJobError(DomainError):
    """Un trabajo que no va a funcionar aunque se reintente (payload inválido, entidad borrada).

    Va directo a DEAD, sin gastar reintentos. Cualquier otra excepción se reintenta con backoff.
    """


class SendError(DomainError):
    """Meta no aceptó un mensaje. `code` es estable (HTTP_429, META_131047, NETWORK...);
    el mensaje nunca incluye el token ni el contenido."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TransientSendError(SendError):
    """Vale la pena reintentar: red, 429, 5xx, credencial vencida que operación puede renovar."""


class PermanentSendError(SendError):
    """No se arregla reintentando: ventana cerrada en Meta, destinatario inválido, plantilla rechazada."""


class AiError(DomainError):
    """El proveedor de IA no respondió bien. `code` es estable (HTTP_529, RATE_LIMITED, TIMEOUT...);
    nunca lleva la clave, el prompt ni la respuesta: ahí van mensajes de clientes."""

    def __init__(self, code: str, *, retry_after_s: float | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after_s = retry_after_s


class TransientAiError(AiError):
    """Vale la pena reintentar: red, timeout, 429, 5xx, sobrecarga del proveedor."""


class PermanentAiError(AiError):
    """No se arregla reintentando: request inválido, clave rechazada, modelo inexistente."""


class AiUnavailableError(AiError):
    """El circuito está abierto: el proveedor viene fallando y no se lo llama. El agente degrada
    al instante a una respuesta plantilla y pasa la conversación a una persona (§10.3)."""


class UnitOfWorkNotActiveError(DomainError):
    """Se usó un repositorio fuera de su unidad de trabajo (ADR-0011). Siempre es un bug."""


class CodedError(DomainError):
    """Error con un código estable y, si existe, el detalle que devolvió la BD."""

    def __init__(
        self, code: ErrorCode, message: str, *, detail: str | None = None, raw_hint: str | None = None
    ):
        super().__init__(message)
        self.code = code
        self.detail = detail
        self.raw_hint = raw_hint

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code.value!r}, message={self.args[0]!r})"


class BusinessRuleError(CodedError):
    """La operación viola una regla de negocio (stock, TC vencido, horario tomado, ...)."""


class AccessDeniedError(CodedError):
    """La BD rechazó la operación por tenant, rol, MFA o actor. Falla cerrado."""
