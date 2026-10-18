-- ============================================================================
-- 21 · Sinónimos por rubro en la búsqueda, sin embeddings por ahora (#23, ADR-0020)
--
--   a. public.search_synonyms: grupos de palabras que el cliente usa para lo mismo
--      ("zapata" = "pastilla", "calza" = "resina"), por rubro. Son datos de la plataforma,
--      como tax_regimes: sin organization_id, solo lectura para app_backend y authenticated;
--      se cambian por migración. Términos en minúsculas y sin acentos (CHECK).
--   b. private.search_tsquery(rubro, consulta): la consulta de texto completo (todas las palabras,
--      como websearch_to_tsquery) con cada palabra expandida a sus sinónimos, en singular o
--      plural (se comparan los lexemas del stemmer español). Los operadores de tsquery que
--      escriba el cliente no se interpretan.
--   c. public.search_catalog: usa search_tsquery. El resto (SKU, difusa, vector y RRF, ADR-0003)
--      no cambia. Una frase con palabras que ningún ítem tiene ("necesito ... para mi auto") la
--      sigue rescatando la búsqueda difusa por nombre (lo mide el eval).
--   d. Sin embeddings (decisión de AHS, 18-oct-2026): ya no se encolan trabajos EMBED_ITEM, que
--      nadie procesa, y se descartan los que quedaron en cola. El recuperador vectorial queda
--      inactivo hasta que se elija un modelo (se reactiva con un ADR nuevo).
-- Pruebas: supabase/tests/database/19_search_synonyms.test.sql y el eval
--          backend/tests/integration/test_search_eval_db.py (MRR@5).
-- ============================================================================

-- a. Sinónimos --------------------------------------------------------------------------------
CREATE TABLE public.search_synonyms (
    id         integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    vertical   public.business_vertical NOT NULL,
    group_key  text NOT NULL CHECK (group_key ~ '^[a-z0-9_]{1,40}$'),
    term       text NOT NULL CHECK (term ~ '^[a-z0-9]+( [a-z0-9]+)*$' AND length(term) <= 60),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (vertical, term)
);
CREATE INDEX idx_search_synonyms_group ON public.search_synonyms (vertical, group_key);
COMMENT ON TABLE public.search_synonyms IS
    'Sinónimos por rubro para la búsqueda (#23, ADR-0020). Datos de la plataforma: se cambian por migración.';

ALTER TABLE public.search_synonyms ENABLE ROW LEVEL SECURITY;
CREATE POLICY search_synonyms_read ON public.search_synonyms FOR SELECT TO app_backend, authenticated
    USING (true);
REVOKE ALL ON public.search_synonyms FROM PUBLIC, anon, authenticated, app_backend;
GRANT SELECT ON public.search_synonyms TO app_backend, authenticated;

-- Lista inicial: la revisa AHS (español de Santa Cruz). Un término por fila, sin acentos.
INSERT INTO public.search_synonyms (vertical, group_key, term) VALUES
    ('IMPORTADORA_REPUESTOS', 'pastilla_freno', 'pastilla'),
    ('IMPORTADORA_REPUESTOS', 'pastilla_freno', 'zapata'),
    ('IMPORTADORA_REPUESTOS', 'pastilla_freno', 'balata'),
    ('IMPORTADORA_REPUESTOS', 'amortiguador', 'amortiguador'),
    ('IMPORTADORA_REPUESTOS', 'amortiguador', 'shock'),
    ('IMPORTADORA_REPUESTOS', 'amortiguador', 'shocks'),
    ('IMPORTADORA_REPUESTOS', 'correa', 'correa'),
    ('IMPORTADORA_REPUESTOS', 'correa', 'faja'),
    ('IMPORTADORA_REPUESTOS', 'correa', 'banda'),
    ('IMPORTADORA_REPUESTOS', 'embrague', 'embrague'),
    ('IMPORTADORA_REPUESTOS', 'embrague', 'clutch'),
    ('IMPORTADORA_REPUESTOS', 'embrague', 'cloche'),
    ('IMPORTADORA_REPUESTOS', 'foco', 'foco'),
    ('IMPORTADORA_REPUESTOS', 'foco', 'ampolleta'),
    ('IMPORTADORA_REPUESTOS', 'foco', 'bombilla'),
    ('IMPORTADORA_REPUESTOS', 'foco', 'bombillo'),
    ('IMPORTADORA_REPUESTOS', 'refrigerante', 'refrigerante'),
    ('IMPORTADORA_REPUESTOS', 'refrigerante', 'anticongelante'),
    ('IMPORTADORA_REPUESTOS', 'refrigerante', 'coolant'),
    ('IMPORTADORA_REPUESTOS', 'junta', 'junta'),
    ('IMPORTADORA_REPUESTOS', 'junta', 'empaquetadura'),
    ('IMPORTADORA_REPUESTOS', 'junta', 'empaque'),
    ('IMPORTADORA_REPUESTOS', 'arranque', 'arranque'),
    ('IMPORTADORA_REPUESTOS', 'arranque', 'burro'),
    ('IMPORTADORA_REPUESTOS', 'buje', 'buje'),
    ('IMPORTADORA_REPUESTOS', 'buje', 'bocina'),
    ('IMPORTADORA_REPUESTOS', 'disco_freno', 'disco'),
    ('IMPORTADORA_REPUESTOS', 'disco_freno', 'rotor'),
    ('IMPORTADORA_REPUESTOS', 'rodamiento', 'rodamiento'),
    ('IMPORTADORA_REPUESTOS', 'rodamiento', 'ruleman'),
    ('IMPORTADORA_REPUESTOS', 'rodamiento', 'balero'),
    ('IMPORTADORA_REPUESTOS', 'llanta', 'llanta'),
    ('IMPORTADORA_REPUESTOS', 'llanta', 'neumatico'),
    ('IMPORTADORA_REPUESTOS', 'llanta', 'goma'),
    ('IMPORTADORA_REPUESTOS', 'llanta', 'caucho'),
    ('IMPORTADORA_REPUESTOS', 'bateria', 'bateria'),
    ('IMPORTADORA_REPUESTOS', 'bateria', 'acumulador'),
    ('IMPORTADORA_REPUESTOS', 'aceite', 'aceite'),
    ('IMPORTADORA_REPUESTOS', 'aceite', 'lubricante'),
    ('CLINICA_CONSULTORIO', 'limpieza', 'limpieza'),
    ('CLINICA_CONSULTORIO', 'limpieza', 'profilaxis'),
    ('CLINICA_CONSULTORIO', 'limpieza', 'destartraje'),
    ('CLINICA_CONSULTORIO', 'diente', 'diente'),
    ('CLINICA_CONSULTORIO', 'diente', 'dientes'),
    ('CLINICA_CONSULTORIO', 'diente', 'dental'),
    ('CLINICA_CONSULTORIO', 'diente', 'dentadura'),
    ('CLINICA_CONSULTORIO', 'extraccion', 'extraccion'),
    ('CLINICA_CONSULTORIO', 'extraccion', 'exodoncia'),
    ('CLINICA_CONSULTORIO', 'extraccion', 'sacar'),
    ('CLINICA_CONSULTORIO', 'molar', 'molar'),
    ('CLINICA_CONSULTORIO', 'molar', 'muela'),
    ('CLINICA_CONSULTORIO', 'endodoncia', 'endodoncia'),
    ('CLINICA_CONSULTORIO', 'endodoncia', 'conducto'),
    ('CLINICA_CONSULTORIO', 'endodoncia', 'nervio'),
    ('CLINICA_CONSULTORIO', 'restauracion', 'restauracion'),
    ('CLINICA_CONSULTORIO', 'restauracion', 'resina'),
    ('CLINICA_CONSULTORIO', 'restauracion', 'calza'),
    ('CLINICA_CONSULTORIO', 'restauracion', 'tapadura'),
    ('CLINICA_CONSULTORIO', 'restauracion', 'empaste'),
    ('CLINICA_CONSULTORIO', 'restauracion', 'obturacion'),
    ('CLINICA_CONSULTORIO', 'ortodoncia', 'ortodoncia'),
    ('CLINICA_CONSULTORIO', 'ortodoncia', 'brackets'),
    ('CLINICA_CONSULTORIO', 'ortodoncia', 'frenillos'),
    ('CLINICA_CONSULTORIO', 'ortodoncia', 'frenos'),
    ('CLINICA_CONSULTORIO', 'radiografia', 'radiografia'),
    ('CLINICA_CONSULTORIO', 'radiografia', 'rx'),
    ('CLINICA_CONSULTORIO', 'radiografia', 'placa'),
    ('CLINICA_CONSULTORIO', 'consulta', 'consulta'),
    ('CLINICA_CONSULTORIO', 'consulta', 'chequeo'),
    ('CLINICA_CONSULTORIO', 'consulta', 'revision'),
    ('CLINICA_CONSULTORIO', 'consulta', 'evaluacion'),
    ('CLINICA_CONSULTORIO', 'urgencia', 'urgencia'),
    ('CLINICA_CONSULTORIO', 'urgencia', 'emergencia'),
    ('CLINICA_CONSULTORIO', 'blanqueamiento', 'blanqueamiento'),
    ('CLINICA_CONSULTORIO', 'blanqueamiento', 'blanquear'),
    ('CLINICA_CONSULTORIO', 'blanqueamiento', 'aclaramiento');

-- b. Consulta de texto con sinónimos ----------------------------------------------------------
CREATE OR REPLACE FUNCTION private.search_tsquery(
    p_vertical public.business_vertical,
    p_query    text
)
RETURNS tsquery
LANGUAGE plpgsql STABLE
SET search_path = ''
AS $$
DECLARE
    v_word   text;
    v_lex    tsquery;
    v_part   tsquery;
    v_syn    text;
    v_result tsquery;
BEGIN
    FOR v_word IN
        SELECT DISTINCT w
        FROM regexp_split_to_table(lower(private.f_unaccent(left(coalesce(p_query, ''), 200))), '[^a-z0-9]+') AS w
        WHERE w <> ''
    LOOP
        v_lex := plainto_tsquery('spanish'::regconfig, v_word);
        CONTINUE WHEN numnode(v_lex) = 0;          -- palabra vacía: de, la, para...
        v_part := v_lex;
        FOR v_syn IN
            SELECT s2.term
            FROM public.search_synonyms s1
            JOIN public.search_synonyms s2
              ON s2.vertical = s1.vertical AND s2.group_key = s1.group_key AND s2.term <> s1.term
            WHERE s1.vertical = p_vertical
              AND plainto_tsquery('spanish'::regconfig, s1.term)::text = v_lex::text
            ORDER BY s2.term
        LOOP
            v_part := v_part || phraseto_tsquery('spanish'::regconfig, v_syn);
        END LOOP;
        v_result := CASE WHEN v_result IS NULL THEN v_part ELSE v_result && v_part END;
    END LOOP;
    RETURN v_result;                               -- NULL: no hay palabras útiles
END
$$;
REVOKE ALL ON FUNCTION private.search_tsquery(public.business_vertical, text) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION private.search_tsquery(public.business_vertical, text)
    TO app_backend, authenticated;

-- c. La búsqueda usa los sinónimos ------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.search_catalog(
    p_query            text,
    p_query_embedding  extensions.vector(1536) DEFAULT NULL,
    p_limit            integer DEFAULT 8,
    p_category         text DEFAULT NULL
)
RETURNS TABLE (
    item_id          uuid,
    sku              text,
    name             text,
    description      text,
    item_type        public.item_type,
    stock_available  integer,
    duration_minutes integer,
    unit_price_bs    numeric(12,2),
    price_status     text,              -- OK | FX_MISSING | FX_STALE
    applied_fx_rate  numeric(10,4),
    tax_mode         public.tax_mode,   -- el bot dice "IVA incluido" o "+ IVA" según esto
    score            double precision,
    matched_by       text[]
)
LANGUAGE plpgsql STABLE
SET search_path = ''
AS $$
DECLARE
    v_org   uuid := private.backend_org_id();
    v_fx    record;
    v_q     text := left(btrim(coalesce(p_query, '')), 200);
    v_qn    text;
    v_tsq   tsquery;
    v_vertical public.business_vertical;
    v_k     constant integer := 60;
    v_pool  constant integer := 40;
BEGIN
    IF v_org IS NULL THEN
        RAISE EXCEPTION 'Tenant no definido' USING ERRCODE = '42501', HINT = 'TENANT_REQUIRED';
    END IF;
    p_limit := least(greatest(coalesce(p_limit, 8), 1), 20);
    v_qn    := upper(regexp_replace(v_q, '[^A-Za-z0-9]', '', 'g'));
    SELECT o.vertical INTO v_vertical FROM public.organizations o WHERE o.id = v_org;
    v_tsq := private.search_tsquery(v_vertical, v_q);  -- todas las palabras, cada una con sus sinónimos
    SELECT * INTO v_fx FROM private.current_fx(v_org);

    RETURN QUERY
    WITH base AS (
        SELECT ci.* FROM public.catalog_items ci
        WHERE ci.organization_id = v_org AND ci.is_active
          AND (p_category IS NULL OR ci.category = p_category)
    ),
    fts AS (
        SELECT b.id, row_number() OVER (ORDER BY ts_rank_cd(b.search_text, v_tsq) DESC) AS r
        FROM base b WHERE numnode(v_tsq) > 0 AND b.search_text @@ v_tsq
        ORDER BY r LIMIT v_pool
    ),
    sku AS (
        SELECT b.id, row_number() OVER (
                   ORDER BY (b.sku_normalized = v_qn) DESC,
                            extensions.similarity(b.sku_normalized, v_qn) DESC) AS r
        FROM base b
        WHERE length(v_qn) >= 3
          AND (b.sku_normalized = v_qn OR extensions.similarity(b.sku_normalized, v_qn) >= 0.45)
        ORDER BY r LIMIT v_pool
    ),
    fuzzy AS (
        SELECT b.id, row_number() OVER (
                   ORDER BY extensions.word_similarity(private.f_unaccent(lower(v_q)),
                                                       private.f_unaccent(lower(b.name))) DESC) AS r
        FROM base b
        WHERE length(v_q) >= 3
          AND extensions.word_similarity(private.f_unaccent(lower(v_q)),
                                         private.f_unaccent(lower(b.name))) >= 0.35
        ORDER BY r LIMIT v_pool
    ),
    vec AS (
        SELECT b.id, row_number() OVER (
                   ORDER BY b.embedding OPERATOR(extensions.<=>) p_query_embedding) AS r
        FROM base b
        WHERE p_query_embedding IS NOT NULL AND b.embedding IS NOT NULL
        ORDER BY r LIMIT v_pool
    ),
    hits AS (
        SELECT id, r, 'FTS'::text AS src FROM fts
        UNION ALL SELECT id, r, 'SKU'    FROM sku
        UNION ALL SELECT id, r, 'FUZZY'  FROM fuzzy
        UNION ALL SELECT id, r, 'VECTOR' FROM vec
    ),
    fused AS (
        SELECT h.id,
               sum(1.0 / (v_k + h.r))
                 + CASE WHEN bool_or(h.src = 'SKU' AND h.r = 1)
                         AND EXISTS (SELECT 1 FROM base x WHERE x.id = h.id AND x.sku_normalized = v_qn)
                        THEN 1.0 ELSE 0 END          AS s,   -- SKU exacto siempre primero
               array_agg(DISTINCT h.src ORDER BY h.src) AS srcs
        FROM hits h GROUP BY h.id
    ),
    reserved AS (
        SELECT r.catalog_item_id, sum(r.quantity)::integer AS qty
        FROM public.stock_reservations r
        WHERE r.organization_id = v_org AND r.released_at IS NULL
          AND r.committed_at IS NULL
        GROUP BY r.catalog_item_id
    )
    SELECT b.id, b.sku, b.name, b.description, b.item_type,
           CASE WHEN b.item_type = 'PRODUCT'
                THEN greatest(b.stock_on_hand - coalesce(rs.qty, 0), 0) END,
           b.duration_minutes,
           CASE WHEN b.price_currency = 'BOB' THEN private.unit_price_bs(b.price_currency, b.list_price, NULL)
                WHEN v_fx.rate IS NULL OR v_fx.is_stale THEN NULL
                ELSE private.unit_price_bs(b.price_currency, b.list_price, v_fx.rate) END,
           CASE WHEN b.price_currency = 'BOB' THEN 'OK'
                WHEN v_fx.rate IS NULL THEN 'FX_MISSING'
                WHEN v_fx.is_stale THEN 'FX_STALE'
                ELSE 'OK' END,
           CASE WHEN b.price_currency = 'USD' THEN v_fx.rate END,
           (private.tax_regime_at(now())).mode,
           f.s::double precision,
           f.srcs
    FROM fused f
    JOIN base b ON b.id = f.id
    LEFT JOIN reserved rs ON rs.catalog_item_id = b.id
    ORDER BY f.s DESC, b.name
    LIMIT p_limit;
END
$$;

-- d. Sin embeddings por ahora -----------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_catalog_enqueue_embedding ON public.catalog_items;
DROP FUNCTION IF EXISTS private.tg_catalog_enqueue_embedding();
DELETE FROM private.jobs WHERE kind = 'EMBED_ITEM' AND status = 'QUEUED';
