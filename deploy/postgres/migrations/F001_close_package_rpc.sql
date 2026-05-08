-- F-001: RPC close_package (transacional, com advisory lock).
-- Port da migration original do projeto Alana, adaptada pro schema do raylook
-- (ids como TEXT em vez de uuid).

CREATE OR REPLACE FUNCTION close_package(
    p_enquete_id text,
    p_produto_id text,
    p_votes jsonb,
    p_opened_at timestamptz,
    p_closed_at timestamptz,
    p_capacidade_total int DEFAULT 24,
    p_total_qty int DEFAULT 24
) RETURNS jsonb LANGUAGE plpgsql AS $$
DECLARE
    v_pacote_id text;
    v_sequence_no int;
    v_vote jsonb;
    v_participants_count int;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(p_enquete_id, 0));

    SELECT coalesce(max(sequence_no), 0) + 1
      INTO v_sequence_no
      FROM pacotes
     WHERE enquete_id = p_enquete_id
       AND sequence_no > 0;

    v_participants_count := jsonb_array_length(p_votes);

    IF v_participants_count = 0 THEN
        RETURN jsonb_build_object('status', 'no_votes', 'pacote_id', null);
    END IF;

    INSERT INTO pacotes (
        id, enquete_id, sequence_no, capacidade_total, total_qty,
        participants_count, status, opened_at, closed_at, created_at, updated_at
    ) VALUES (
        gen_random_uuid()::text,
        p_enquete_id, v_sequence_no, p_capacidade_total, p_total_qty,
        v_participants_count, 'closed', p_opened_at, p_closed_at, now(), now()
    )
    RETURNING id INTO v_pacote_id;

    FOR v_vote IN SELECT * FROM jsonb_array_elements(p_votes) LOOP
        INSERT INTO pacote_clientes (
            id, pacote_id, cliente_id, voto_id, produto_id, qty,
            unit_price, subtotal, commission_percent, commission_amount,
            total_amount, status, created_at, updated_at
        ) VALUES (
            gen_random_uuid()::text,
            v_pacote_id,
            v_vote->>'cliente_id',
            v_vote->>'vote_id',
            p_produto_id,
            (v_vote->>'qty')::int,
            (v_vote->>'unit_price')::numeric,
            (v_vote->>'subtotal')::numeric,
            (v_vote->>'commission_percent')::numeric,
            (v_vote->>'commission_amount')::numeric,
            (v_vote->>'total_amount')::numeric,
            'closed',
            now(), now()
        )
        ON CONFLICT (pacote_id, cliente_id) DO UPDATE
        SET qty = EXCLUDED.qty,
            voto_id = EXCLUDED.voto_id,
            unit_price = EXCLUDED.unit_price,
            subtotal = EXCLUDED.subtotal,
            commission_percent = EXCLUDED.commission_percent,
            commission_amount = EXCLUDED.commission_amount,
            total_amount = EXCLUDED.total_amount,
            updated_at = now();
    END LOOP;

    UPDATE votos
       SET status = 'in', updated_at = now()
     WHERE id = ANY (
        SELECT (v->>'vote_id') FROM jsonb_array_elements(p_votes) v
     );

    RETURN jsonb_build_object(
        'status', 'ok',
        'pacote_id', v_pacote_id,
        'sequence_no', v_sequence_no,
        'participants_count', v_participants_count
    );
END;
$$;

GRANT EXECUTE ON FUNCTION close_package(text, text, jsonb, timestamptz, timestamptz, int, int) TO raylook_api;
