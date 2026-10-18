# Runbook: cuota diaria de IA de un negocio

**Alertas:** `LlmBudgetNearLimit` (ticket, 80 % del tope) y `LlmBudgetExceeded` (page, 100 %), además del log `ALERTA cuota_ia_agotada` del worker.
**Decisión:** RF-51 y §7.4.4. Tope diario igual para todos los negocios: `AGENT_DAILY_BUDGET_USD` (US$ 0,75, decidido por AHS el 18-oct-2026). El día es el de Bolivia (UTC-4).

## Síntoma

* **80 %:** el bot sigue respondiendo normal; solo es un aviso.
* **100 %:** el bot ya no llama a la IA. Responde una plantilla ("le atenderá una persona del equipo") y pasa la conversación a `HUMAN` con motivo `OTHER`. Las demás conversaciones del negocio reciben la misma plantilla en su próximo mensaje.

## Diagnóstico

```sql
-- Costo de IA de hoy por negocio (día de Bolivia)
SELECT organization_id, round(sum(cost_usd), 4) AS usd_hoy, count(*) AS llamadas
  FROM private.llm_calls
 WHERE created_at >= (date_trunc('day', now() AT TIME ZONE 'America/La_Paz') AT TIME ZONE 'America/La_Paz')
 GROUP BY organization_id ORDER BY usd_hoy DESC LIMIT 20;

-- ¿Una conversación concentra el gasto? (abuso o un bucle)
SELECT conversation_id, count(*) AS llamadas, round(sum(cost_usd), 4) AS usd
  FROM private.llm_calls
 WHERE organization_id = '<org>' AND created_at > now() - interval '1 day'
 GROUP BY conversation_id ORDER BY usd DESC LIMIT 10;
```

* **Uso real alto** (muchas conversaciones distintas): el negocio creció; el tope le queda chico.
* **Una sola conversación:** posible abuso. El límite por cliente (`AGENT_CUSTOMER_MESSAGES_PER_MINUTE`, `AGENT_CUSTOMER_TURNS_PER_HOUR`) debería frenarlo; si no alcanzó, revisar esa conversación.
* **Costo por llamada fuera de lo normal:** revisar `LLM_MODEL` y los precios `LLM_PRICE_*` (un modelo más caro o un precio mal cargado).

## Acción

* El negocio sigue atendido por personas del equipo: no es urgente para el cliente final, pero sí para el negocio.
* Subir el tope es una **decisión de presupuesto de AHS**: cambiar `AGENT_DAILY_BUDGET_USD` en el despliegue y reiniciar el worker.
* Ante abuso: bloquear al cliente desde el panel (cuando exista) o dejar la conversación en `HUMAN`.

## Verificación

Al día siguiente (00:00 de Bolivia) el contador vuelve a cero y el bot responde solo en las conversaciones que sigan en `BOT`. Las que pasaron a `HUMAN` las devuelve el equipo.
