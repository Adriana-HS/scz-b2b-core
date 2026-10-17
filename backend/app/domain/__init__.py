"""Dominio: entidades, value objects, políticas por rubro, reglas antifraude y puertos.

Puro y sin I/O: solo stdlib y pydantic. No importa ninguna otra capa ni SDKs de terceros
(contrato de import-linter en pyproject.toml). El dinero se representa con Decimal y
nunca se recalcula aquí: la BD es la fuente única del cálculo (ADR-0007).
"""
