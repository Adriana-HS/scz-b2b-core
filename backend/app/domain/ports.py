"""Puertos del dominio (docs/architecture/principios-y-patrones.md §2.1, ADR-0011).

Todo acceso a datos ocurre dentro de una UnitOfWork: una transacción con el tenant y el
actor ya fijados en la BD. Los repositorios no existen fuera de ella.

Los puertos de IA son pequeños y por capacidad (segregación de interfaces): el agente
depende de ChatModel, no de un proveedor. Sus errores son AiError con código estable.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from types import TracebackType
from typing import Any, Protocol, Self
from uuid import UUID

from app.domain.agent import TurnState
from app.domain.ai import ChatMessage, LlmCall, ModelResponse, ReceiptExtraction, ToolSpec
from app.domain.catalog import CatalogMatch, CatalogSearch, ItemDetails
from app.domain.consent import ConsentKind, ConsentSource
from app.domain.jobs import Job, QueueStats
from app.domain.messaging import InboundMessage, WhatsAppChannel
from app.domain.orders import Quote, QuoteRequest
from app.domain.outbound import OutboundKind, OutboundRequest, PendingSend, StatusUpdate
from app.domain.quote_document import QuoteDelivery, QuoteDocument
from app.domain.tenancy import TenantContext


class CatalogRepository(Protocol):
    async def search(self, criteria: CatalogSearch) -> list[CatalogMatch]: ...

    async def get_details(self, item_id: UUID) -> ItemDetails | None:
        """Ficha de un ítem activo del tenant, o None si no existe para este negocio."""
        ...


class ConversationRepository(Protocol):
    async def request_handoff(self, conversation_id: UUID, reason: str, summary: str) -> None:
        """Modo HUMAN con motivo y resumen para el equipo (RF-04). NOT_FOUND si no es del tenant."""
        ...

    async def load_turn(self, conversation_id: UUID, history_limit: int) -> TurnState | None:
        """Estado del turno: negocio, modo, últimos mensajes y pendientes. None si no es del tenant."""
        ...

    async def mark_processed(self, conversation_id: UUID, message_ids: Sequence[int]) -> None: ...

    async def record_privacy_notice(self, customer_id: UUID) -> None:
        """customers.privacy_notice_at = ahora, solo la primera vez (#29, RF-03)."""
        ...

    async def add_bot_reply(self, conversation_id: UUID, text: str) -> None:
        """Guarda la respuesta del bot en el historial (la envía el outbox)."""
        ...


class VerticalPolicy(Protocol):
    """Lo que cambia por rubro (§7.3): el prompt de sistema, las herramientas habilitadas y el
    aviso de IA del primer contacto (#29)."""

    @property
    def enabled_tools(self) -> tuple[str, ...]: ...

    def system_prompt(self, business_name: str) -> str: ...

    def ai_notice(self, business_name: str, approved: str | None) -> str: ...


class OrderRepository(Protocol):
    async def create_quote(self, request: QuoteRequest) -> Quote: ...

    async def request_quote_document(self, order_id: UUID, conversation_id: UUID) -> None:
        """Encola SEND_QUOTE_PDF en la misma transacción que la cotización (#26)."""
        ...

    async def quote_delivery(self, order_id: UUID) -> QuoteDelivery | None:
        """La cotización con sus líneas y el canal por el que sale, o None si no es del tenant."""
        ...

    async def count_for_customer(self, customer_id: UUID) -> int: ...


class InboundRepository(Protocol):
    """Persistencia de lo que entra por el webhook (ADR-0005): todo en la misma transacción."""

    async def record_event(self, message: InboundMessage, phone_number_id: str) -> bool:
        """Registra el evento. False si ya existía (Meta lo reenvió): no hay que hacer nada más."""
        ...

    async def upsert_customer(self, wa_id: str, profile_name: str | None, seen_at: datetime) -> UUID: ...

    async def open_conversation(self, customer_id: UUID, seen_at: datetime, channel_id: UUID) -> UUID:
        """La conversación abierta del cliente (una sola por cliente), o una nueva. Guarda el
        número por el que escribió: la respuesta sale por ese mismo número."""
        ...

    async def add_message(self, conversation_id: UUID, message: InboundMessage) -> None: ...

    async def enqueue_conversation(self, conversation_id: UUID) -> None:
        """Encola (o pospone, para agrupar ráfagas) el turno del agente para esa conversación."""
        ...


class OutboundRepository(Protocol):
    """Outbox (ADR-0005): nada sale a Meta sin pasar por aquí."""

    async def enqueue(self, request: OutboundRequest) -> int | None:
        """Encola el mensaje y su trabajo SEND_OUTBOUND en la misma transacción.
        None si la idempotency_key ya existía (no se envía dos veces)."""
        ...

    async def lock_for_send(self, outbound_id: int) -> PendingSend | None: ...

    async def mark_sending(self, outbound_id: int) -> None: ...

    async def mark_sent(self, outbound_id: int, wa_message_id: str) -> None: ...

    async def mark_retry(self, outbound_id: int, error: str) -> None: ...

    async def mark_failed(self, outbound_id: int, reason: str) -> None: ...

    async def apply_status(self, update: StatusUpdate) -> bool:
        """Avanza el estado si corresponde (sent < delivered < read). False si no cambió nada."""
        ...


class ConsentRepository(Protocol):
    """Estado actual + evidencia append-only, en la misma transacción (migración 17)."""

    async def record(
        self, customer_id: UUID, kind: ConsentKind, source: ConsentSource, evidence: str | None
    ) -> None: ...


class LlmCallRepository(Protocol):
    """private.llm_calls: costo y resultado de cada llamada a la IA, en el tenant de la transacción."""

    async def add(self, call: LlmCall) -> None: ...


class ChatModel(Protocol):
    """Un turno del modelo de chat. Lanza TransientAiError, PermanentAiError o AiUnavailableError."""

    async def run_turn(
        self, system: str, history: Sequence[ChatMessage], tools: Sequence[ToolSpec], *, timeout_s: float
    ) -> ModelResponse: ...


class VisionExtractor(Protocol):
    """Lee los campos de un comprobante QR. El resultado es evidencia, nunca confirma un pago."""

    async def extract_receipt(self, image: bytes, *, timeout_s: float) -> ReceiptExtraction: ...


class Embedder(Protocol):
    """Un vector por texto, con la dimensión de catalog_items.embedding (ADR-0003)."""

    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


class SpeechToText(Protocol):
    async def transcribe(self, audio: bytes, mime: str) -> str: ...


class LlmCallRecorder(Protocol):
    """Deja el registro de una llamada a la IA (lo usa el decorador de costo)."""

    async def record(self, call: LlmCall) -> None: ...


class QuoteRenderer(Protocol):
    """Genera el PDF de una cotización. Sincrónico: el caso de uso lo corre en un thread pool."""

    def render(self, document: QuoteDocument) -> bytes: ...


class MediaUploader(Protocol):
    """Sube un archivo a Meta y devuelve su media_id. Lanza TransientSendError o PermanentSendError."""

    async def upload_media(
        self, phone_number_id: str, token: str, content: bytes, mime_type: str, filename: str
    ) -> str: ...


class MessageSender(Protocol):
    """Envío a la WhatsApp Cloud API. Lanza TransientSendError o PermanentSendError."""

    async def send(
        self, phone_number_id: str, token: str, to: str, kind: OutboundKind, payload: dict[str, Any]
    ) -> str: ...


class SecretStore(Protocol):
    """Resuelve el NOMBRE de un secreto (p. ej. whatsapp_channels.access_token_ref) a su valor."""

    def get(self, name: str) -> str | None: ...


class ChannelResolver(Protocol):
    """La única lectura previa a conocer el tenant (ADR-0002 §6): phone_number_id -> negocio."""

    async def resolve(self, phone_number_id: str) -> WhatsAppChannel | None: ...


class JobQueue(Protocol):
    """Cola durable (ADR-0005). complete/fail devuelven el estado nuevo, o None si este
    worker ya no tiene el lease (otro lo reclamó): fencing contra el doble procesamiento."""

    async def claim(self, worker_id: str, limit: int, kinds: Sequence[str]) -> list[Job]: ...

    async def complete(self, job_id: int, worker_id: str) -> str | None: ...

    async def fail(self, job_id: int, worker_id: str, error: str, *, retry: bool) -> str | None: ...


class QueueMaintenance(Protocol):
    """Tareas periódicas que el worker corre si la BD no tiene pg_cron habilitado."""

    async def requeue_stale(self, lease: timedelta) -> tuple[int, int]:
        """(reintentados, pasados a DEAD) entre los trabajos con el lease vencido."""
        ...

    async def expire_stale_orders(self) -> int: ...

    async def purge_retention(self) -> None: ...

    async def queue_stats(self, kinds: Sequence[str]) -> list[QueueStats]:
        """Estado de la cola por tipo, solo de los tipos pedidos (los que el worker procesa)."""
        ...

    async def fx_stale_tenants(self) -> int: ...


class UnitOfWork(Protocol):
    """Una transacción con tenant y actor. Confirma al salir sin error; si no, revierte."""

    @property
    def context(self) -> TenantContext: ...

    @property
    def catalog(self) -> CatalogRepository: ...

    @property
    def orders(self) -> OrderRepository: ...

    @property
    def inbound(self) -> InboundRepository: ...

    @property
    def outbound(self) -> OutboundRepository: ...

    @property
    def consent(self) -> ConsentRepository: ...

    @property
    def llm_calls(self) -> LlmCallRepository: ...

    @property
    def conversations(self) -> ConversationRepository: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...


class UnitOfWorkFactory(Protocol):
    """Lo que reciben los casos de uso: una forma de abrir unidades de trabajo por tenant."""

    def __call__(self, context: TenantContext) -> UnitOfWork: ...
