-- F-063 (parte 2): backfill de friendly_id pra pacotes históricos.
-- Só pacotes que efetivamente fecharam (closed/approved/cancelled).
-- Pacotes `open` são slots placeholders ainda acumulando votos — não
-- recebem friendly_id até virarem closed. Agrupa por dia (timezone
-- America/Sao_Paulo) e enumera por ordem cronológica. Idempotente:
-- só atualiza linhas com friendly_id NULL.

BEGIN;

WITH base AS (
    SELECT
        id,
        coalesce(closed_at, cancelled_at, created_at) AS evt_at,
        to_char(
            (coalesce(closed_at, cancelled_at, created_at) AT TIME ZONE 'America/Sao_Paulo')::date,
            'DDMM'
        ) AS ddmm
      FROM pacotes
     WHERE friendly_id IS NULL
       AND status IN ('closed', 'approved', 'cancelled')
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
