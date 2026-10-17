# Runbook: fallas de firma del webhook

**Alerta:** `WebhookSignatureFailuresHigh` (ticket): más del 1 % de los POST del webhook con firma HMAC inválida o ausente durante 15 min, con al menos 20 POST en la ventana.
**Requisito:** Q-SEC. Un POST sin firma válida se rechaza con 401 **antes** de leer su contenido.

## Síntoma

Meta (o alguien que se hace pasar por Meta) envía POST que no pasan la verificación `X-Hub-Signature-256`.

## Diagnóstico

* **¿Fallan todos?** Si casi el 100 % falla y los mensajes de los clientes no llegan, es nuestra configuración: el `META_APP_SECRET` del entorno no es el de la app de Meta (por ejemplo, se rotó el secreto en Meta y no en el despliegue).
* **¿Falla una parte?** Con tráfico normal de Meta entrando bien, el resto son POST de terceros: escaneos o intentos de inyectar mensajes falsos. El rechazo ya los frena; el log `webhook: firma inválida o ausente (N bytes)` no guarda contenido.
* Revisa si hubo un despliegue o una rotación de secretos justo antes del inicio de la alerta.

## Acción

* Secreto equivocado: carga el `META_APP_SECRET` correcto (desde el gestor de secretos, nunca por chat ni en el repositorio) y redespliega la api. Meta reintenta los webhooks fallidos durante un tiempo, así que la mayoría de los mensajes llegará solo.
* Tráfico de terceros: no hace falta acción inmediata. Si el volumen afecta la latencia, limita por IP en el proxy o el WAF delante de la api.
* Si sospechas que el secreto se filtró, rótalo en Meta y en el despliegue y sigue el runbook de incidente de seguridad.

## Verificación

La proporción vuelve a ~0 % y `webhook_requests_total` sigue creciendo con los mensajes legítimos.

## Comunicación

Si se perdieron mensajes por un secreto equivocado, avisa a los negocios del período sin recepción.

## Postmortem

Obligatorio si fue un secreto equivocado o filtrado.
