-- F-063 (parte 2): backfill de friendly_id pra pacotes históricos.
-- Agrupa por dia (timezone America/Sao_Paulo) usando o melhor timestamp
-- disponível (closed_at quando existir, senão created_at) e enumera por
-- ordem cronológica dentro do dia. Roda 1x; é idempotente (só atualiza
-- linhas com friendly_id NULL).

BEGIN;

WITH base AS (
    SELECT
        id,
        coalesce(closed_at, created_at) AS evt_at,
        to_char(
            (coalesce(closed_at, created_at) AT TIME ZONE 'America/Sao_Paulo')::date,
            'DDMM'
        ) AS ddmm
      FROM pacotes
     WHERE friendly_id IS NULL
       AND coalesce(closed_at, created_at) IS NOT NULL
),
numbered AS (
    SELECT
        id,
        ddmm,
        row_number() OVER (PARTITION BY ddmm ORDER BY evt_at, id) AS seq
      FROM base
)
UPDATE pacotes p
   SET friendly_id = 'PAC' || lpad(n.seq::text, 3, '0') || '/' || n.ddmm,
       updated_at = now()
  FROM numbered n
 WHERE p.id = n.id;

COMMIT;
