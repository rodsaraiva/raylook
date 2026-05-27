-- F-065: corrige numeração de friendly_id quando há gaps na sequência do dia.
--
-- A versão anterior usava `count(*) + 1`, o que falha sempre que a sequência
-- do dia tem buracos ou não começa em 001 (cenário gerado pelo backfill
-- F063b quando rodou com pacotes pré-existentes). Resultado: colisão no
-- unique constraint `pacotes_friendly_id_uq` e pacotes recém-fechados
-- ficavam sem código (exceção engolida no Python).
--
-- Agora extrai o maior número já usado no dia e soma 1, ficando robusto a
-- gaps. Também faz backfill dos órfãos do período afetado usando a função
-- corrigida (idempotente — só toca em friendly_id NULL).

BEGIN;

CREATE OR REPLACE FUNCTION assign_pacote_friendly_id(
    p_pacote_id text,
    p_ddmm text
) RETURNS text LANGUAGE plpgsql AS $$
DECLARE
    v_existing text;
    v_seq int;
    v_friendly text;
    v_found boolean;
BEGIN
    SELECT friendly_id, true INTO v_existing, v_found
      FROM pacotes WHERE id = p_pacote_id;
    IF NOT v_found THEN
        RETURN NULL;
    END IF;
    IF v_existing IS NOT NULL THEN
        RETURN v_existing;
    END IF;

    PERFORM pg_advisory_xact_lock(hashtextextended('friendly_id_' || p_ddmm, 0));

    SELECT COALESCE(MAX(substring(friendly_id FROM '^PAC(\d+)/')::int), 0) + 1
      INTO v_seq
      FROM pacotes
     WHERE friendly_id LIKE 'PAC%/' || p_ddmm;

    v_friendly := 'PAC' || lpad(v_seq::text, 3, '0') || '/' || p_ddmm;

    UPDATE pacotes SET friendly_id = v_friendly, updated_at = now()
     WHERE id = p_pacote_id;

    RETURN v_friendly;
END;
$$;

-- Backfill: pacotes closed/approved/cancelled que ficaram sem código por
-- causa do bug. Usa a função corrigida pra reaproveitar a lógica de
-- advisory lock e numeração baseada em MAX.
DO $$
DECLARE
    r record;
    v_ddmm text;
BEGIN
    FOR r IN
        SELECT id,
               coalesce(closed_at, cancelled_at, created_at) AS evt_at
          FROM pacotes
         WHERE friendly_id IS NULL
           AND status IN ('closed', 'approved', 'cancelled')
         ORDER BY evt_at, id
    LOOP
        v_ddmm := to_char(
            (r.evt_at AT TIME ZONE 'America/Sao_Paulo')::date,
            'DDMM'
        );
        PERFORM assign_pacote_friendly_id(r.id, v_ddmm);
    END LOOP;
END $$;

COMMIT;
