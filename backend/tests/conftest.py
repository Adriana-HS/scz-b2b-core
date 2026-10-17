"""Telemetría en memoria para toda la corrida (#19).

OpenTelemetry admite un solo TracerProvider/MeterProvider por proceso, así que se configura
una vez, al inicio, con exportadores en memoria. `configure_telemetry` es idempotente: el
lifespan de la api y el main del worker no lo reemplazan durante las pruebas.
"""

import logging
from collections.abc import Callable, Iterator

import pytest
from opentelemetry.sdk.metrics.export import (
    HistogramDataPoint,
    InMemoryMetricReader,
    NumberDataPoint,
)
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.core import telemetry

SPANS = InMemorySpanExporter()
METRICS = InMemoryMetricReader()
telemetry.configure_telemetry(
    "scz-b2b-core-test", "test", None, span_processors=[SimpleSpanProcessor(SPANS)], metric_readers=[METRICS]
)

type MetricValue = Callable[..., float]


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    SPANS.clear()
    yield SPANS
    SPANS.clear()


def _metric_value(name: str, **attributes: str) -> float:
    """Suma de los contadores (o cantidad de muestras de un histograma) cuyo atributo coincide."""
    data = METRICS.get_metrics_data()
    total = 0.0
    for resource in data.resource_metrics if data else []:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name != name:
                    continue
                for point in metric.data.data_points:
                    point_attributes = point.attributes or {}
                    if any(point_attributes.get(k) != v for k, v in attributes.items()):
                        continue
                    if isinstance(point, HistogramDataPoint):
                        total += point.count
                    elif isinstance(point, NumberDataPoint):
                        total += point.value
    return total


@pytest.fixture
def metric() -> MetricValue:
    """`metric("jobs_dead", kind="T")`: valor acumulado. Las pruebas comparan antes y después."""
    return _metric_value


@pytest.fixture(autouse=True)
def _restore_root_logging() -> Iterator[None]:
    """create_app() configura logs JSON en el lifespan; cada prueba deja el logging como lo encontró."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
