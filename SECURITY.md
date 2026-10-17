# Política de seguridad

## Reportar una vulnerabilidad

No abras un *issue* público. Escribe a la responsable del proyecto (ver `README.md`) con:
* descripción y componente afectado;
* pasos para reproducir o prueba de concepto;
* impacto estimado (datos, dinero, disponibilidad).

Confirmamos la recepción en 72 h y acordamos un plazo de corrección según la severidad: crítica ≤ 7 días, alta ≤ 30 días.

## Alcance prioritario

1. Aislamiento entre organizaciones (RLS, Storage, funciones SECURITY DEFINER).
2. Flujo de pagos (evidencias, aprobación, callbacks bancarios).
3. Webhooks de WhatsApp (firma, tamaño, media).
4. Inyección de prompts que cambie precios, estados o datos de otro cliente.

## Controles del diseño

Ver `SYSTEM_DESIGN.md` §8 (modelo de amenazas STRIDE, identidad, secretos, Storage, privacidad) y las pruebas `supabase/tests/database/01_tenant_isolation.test.sql`.

## Reglas para quienes contribuyen

* Nunca subir secretos: `gitleaks` corre en CI y como hook local recomendado.
* Las GitHub Actions se fijan por SHA de commit con la versión en un comentario; nunca por etiqueta (`@v7`). Lo verifica `scripts/ci/check_repo.py`.
* La *service role key* no se usa para consultas de datos (solo en el `StorageAdapter`).
* Toda función `SECURITY DEFINER` fija `SET search_path = ''`.
* Toda tabla nueva lleva `organization_id`, RLS, políticas y prueba pgTAP de aislamiento.
* Cambios en dinero, autenticación o datos personales requieren revisión de seguridad en el PR.
