-- F-063: ID amigável dos pacotes (PAC{NNN}/{DDMM}).
-- Atribuído quando o pacote é fechado; sequência reseta por dia.
-- Mantém id (UUID) e sequence_no (interno por enquete) intactos.

BEGIN;

ALTER TABLE pacotes ADD COLUMN IF NOT EXISTS friendly_id text;

CREATE UNIQUE INDEX IF NOT EXISTS pacotes_friendly_id_uq
  ON pacotes (friendly_id)
  WHERE friendly_id IS NOT NULL;

-- RPC atômica: idempotente + advisory_lock por dia evita 2 pacotes
-- ganharem o mesmo número quando fecham simultaneamente.
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

    SELECT count(*) + 1 INTO v_seq
      FROM pacotes
     WHERE friendly_id LIKE 'PAC%/' || p_ddmm;

    v_friendly := 'PAC' || lpad(v_seq::text, 3, '0') || '/' || p_ddmm;

    UPDATE pacotes SET friendly_id = v_friendly, updated_at = now()
     WHERE id = p_pacote_id;

    RETURN v_friendly;
END;
$$;

GRANT EXECUTE ON FUNCTION assign_pacote_friendly_id(text, text) TO raylook_api;

COMMIT;
