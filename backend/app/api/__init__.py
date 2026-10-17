"""Interfaz HTTP (adaptador de entrada): webhooks, callbacks y endpoints del dashboard.

No hace trabajo pesado: valida, persiste y encola (ADR-0005). Responde en <= 250 ms (Q-PERF-1).
"""
