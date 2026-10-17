"""Adaptadores de salida: implementan los puertos del dominio.

Postgres (asyncpg + UnitOfWork, ADR-0011), WhatsApp Cloud API, LLM, Storage, PDF.
Cada proveedor es una capa anticorrupción: sus modelos no entran al dominio.
"""
