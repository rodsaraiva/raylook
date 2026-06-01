-- Migration: tabela de créditos (ledger) — sistema de crédito por cancelamento.
-- Idempotente e transacional. Banco Postgres dedicado do raylook (stack raylook_*).
--
-- Contexto: quando um pacote pago é cancelado, o valor pago vira crédito na
-- plataforma (lançamento 'credit'); o uso do crédito em compras gera 'debit'
-- (pending até a confirmação do pagamento, ou confirmed na cobertura total).
-- Saldo = SUM(credit confirmed) - SUM(debit confirmed).
--
-- Aplicar no Postgres de produção do raylook ANTES de subir a imagem nova.
-- Como reverter (se necessário, sem dados a preservar): DROP TABLE creditos;

BEGIN;

CREATE TABLE IF NOT EXISTS creditos (
    id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    cliente_id text NOT NULL REFERENCES clientes(id),
    tipo text NOT NULL CHECK (tipo IN ('credit', 'debit')),
    status text NOT NULL DEFAULT 'confirmed'
        CHECK (status IN ('pending', 'confirmed')),
    valor numeric NOT NULL CHECK (valor > 0),
    pacote_id text REFERENCES pacotes(id),
    venda_id text REFERENCES vendas(id),
    pagamento_id text REFERENCES pagamentos(id),
    asaas_payment_id text,
    descricao text,
    created_by text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_creditos_cliente ON creditos(cliente_id);
CREATE INDEX IF NOT EXISTS idx_creditos_pagamento ON creditos(pagamento_id);
CREATE INDEX IF NOT EXISTS idx_creditos_asaas ON creditos(asaas_payment_id);

COMMIT;

-- Verificação pós-migration (deve retornar 'creditos'):
--   SELECT to_regclass('public.creditos');
-- Idempotência: re-executar este arquivo é seguro (IF NOT EXISTS em tabela e índices).
