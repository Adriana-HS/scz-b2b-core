"""Fakes de los puertos de IA (#22). Deterministas, sin red ni cuentas.

Los usan las pruebas de los decoradores, las del agente (fase 2) y la suite de contrato, que
verifica que cada fake se comporte como el puerto que imita.
"""

import hashlib
import math
from collections.abc import Sequence
from decimal import Decimal

from app.domain.ai import (
    ChatMessage,
    LlmCall,
    ModelResponse,
    ReceiptExtraction,
    StopReason,
    TokenUsage,
    ToolSpec,
)

EMBEDDING_DIMENSIONS = 1536  # catalog_items.embedding es vector(1536) (ADR-0003)


def text_response(text: str = "Hola, ¿en qué te ayudo?", *, model: str = "fake-model-1") -> ModelResponse:
    return ModelResponse(
        text=text,
        tool_calls=(),
        stop_reason=StopReason.END_TURN,
        usage=TokenUsage(input_tokens=100, output_tokens=20),
        model=model,
    )


class ScriptedChatModel:
    """Devuelve (o lanza) los pasos del guion en orden y guarda cada llamada."""

    def __init__(self, *steps: ModelResponse | Exception) -> None:
        self.steps = list(steps)
        self.calls: list[tuple[str, tuple[ChatMessage, ...], tuple[ToolSpec, ...], float]] = []

    async def run_turn(
        self, system: str, history: Sequence[ChatMessage], tools: Sequence[ToolSpec], *, timeout_s: float
    ) -> ModelResponse:
        self.calls.append((system, tuple(history), tuple(tools), timeout_s))
        step = self.steps.pop(0) if len(self.steps) > 1 else self.steps[0]
        if isinstance(step, Exception):
            raise step
        return step


class FakeEmbedder:
    """Vectores deterministas y normalizados: el mismo texto, el mismo vector."""

    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        vectors = []
        for text in texts:
            seed = hashlib.sha256(text.encode()).digest()
            raw = [((seed[i % 32] + i) % 251) / 251 - 0.5 for i in range(EMBEDDING_DIMENSIONS)]
            norm = math.sqrt(sum(v * v for v in raw)) or 1.0
            vectors.append([v / norm for v in raw])
        return vectors


class FakeSpeechToText:
    def __init__(self, transcript: str = "quiero pastillas de freno") -> None:
        self.transcript = transcript

    async def transcribe(self, audio: bytes, mime: str) -> str:
        if not audio:
            raise ValueError("audio vacío")
        return self.transcript


class FakeVisionExtractor:
    def __init__(self, extraction: ReceiptExtraction | None = None) -> None:
        self.extraction = extraction or ReceiptExtraction(
            amount_bs=Decimal("480.00"), confidence=Decimal("0.95")
        )

    async def extract_receipt(self, image: bytes, *, timeout_s: float) -> ReceiptExtraction:
        if not image:
            raise ValueError("imagen vacía")
        return self.extraction


class RecordingLlmCalls:
    """LlmCallRecorder en memoria; `error` simula que la BD no está."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[LlmCall] = []
        self.error = error

    async def record(self, call: LlmCall) -> None:
        if self.error:
            raise self.error
        self.calls.append(call)
