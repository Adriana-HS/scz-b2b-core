"""Aplicación: casos de uso, orquestador del agente y guardrails.

Depende solo del dominio y de sus puertos (Protocol). Los adaptadores concretos se inyectan
por constructor desde el composition root (app.main y app.worker).
"""
