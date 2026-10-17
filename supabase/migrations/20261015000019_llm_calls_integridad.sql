-- ============================================================================
-- 19 · Integridad de private.llm_calls (#22, Q-COST-1)
--
-- llm_calls guarda el costo de cada llamada al proveedor de IA por tenant (lo escribe el
-- decorador CostMeteredChatModel). Se completa lo que faltaba para la regla multi-tenant:
--   a. FK compuesta (organization_id, conversation_id) -> conversations: una llamada nunca
--      apunta a una conversación de otro negocio. Con su índice (lo exige la prueba 05).
--   b. CHECK de rangos: tokens, costo y latencia no negativos.
-- La tabla está vacía en todos los entornos (el agente llega con #26), así que las
-- restricciones se validan al crearlas.
-- Pruebas: supabase/tests/database/16_llm_calls.test.sql
-- ============================================================================

ALTER TABLE private.llm_calls
    ADD CONSTRAINT llm_calls_conversation_fk
        FOREIGN KEY (organization_id, conversation_id)
        REFERENCES public.conversations (organization_id, id) ON DELETE RESTRICT,
    ADD CONSTRAINT llm_calls_tokens_check
        CHECK (input_tokens >= 0 AND output_tokens >= 0 AND cached_tokens >= 0 AND tool_calls >= 0),
    ADD CONSTRAINT llm_calls_cost_check CHECK (cost_usd >= 0),
    ADD CONSTRAINT llm_calls_latency_check CHECK (latency_ms IS NULL OR latency_ms >= 0);

CREATE INDEX idx_llm_calls_conversation ON private.llm_calls (organization_id, conversation_id);
