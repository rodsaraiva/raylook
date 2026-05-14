-- Schema Postgres do Raylook (prod).
-- Port do deploy/sqlite/schema.sql — mantém os mesmos nomes/tipos/constraints
-- pra que o mesmo código (SQLiteRestClient | SupabaseRestClient via PostgREST)
-- funcione contra os dois bancos.
--
-- Convenções:
--   uuid          gerado por gen_random_uuid() (pgcrypto)
--   timestamps    timestamptz com DEFAULT now() em tabelas async; coluna
--                 created_at NOT NULL nas demais (Python preenche)
--   ENUMs         text + CHECK (mais simples de evoluir que enum types)
--   booleans      smallint 0/1 pra paridade com SQLite

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Role da API do PostgREST. Senha vem da env do service postgres.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'raylook_api') THEN
        CREATE ROLE raylook_api NOLOGIN;
    END IF;
END $$;

-- ============================================================
-- produtos
-- ============================================================
CREATE TABLE IF NOT EXISTS produtos (
    id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    nome text NOT NULL,
    descricao text,
    tamanho text,
    valor_unitario numeric NOT NULL DEFAULT 0 CHECK (valor_unitario >= 0),
    drive_folder_id text,
    drive_file_id text,
    image_message_id text,
    source text NOT NULL DEFAULT 'whapi',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- ============================================================
-- clientes
-- ============================================================
CREATE TABLE IF NOT EXISTS clientes (
    id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    nome text NOT NULL,
    celular text NOT NULL,
    email text,
    nome_loja text,
    cpf_cnpj text,
    password_hash text,
    session_token text,
    session_expires_at timestamptz,
    reset_token text,
    reset_token_expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS clientes_celular_key ON clientes (celular);

-- ============================================================
-- enquetes
-- ============================================================
CREATE TABLE IF NOT EXISTS enquetes (
    id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    external_poll_id text NOT NULL,
    provider text NOT NULL DEFAULT 'unknown'
        CHECK (provider IN ('whapi', 'evolution', 'unknown')),
    chat_id text,
    produto_id text REFERENCES produtos(id),
    titulo text NOT NULL,
    status text NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'closed', 'cancelled')),
    source text NOT NULL DEFAULT 'whapi',
    drive_folder_id text,
    drive_file_id text,
    image_message_id text,
    created_at_provider timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    fornecedor text
);
CREATE UNIQUE INDEX IF NOT EXISTS enquetes_external_poll_id_key ON enquetes (external_poll_id);

-- ============================================================
-- enquete_alternativas
-- ============================================================
CREATE TABLE IF NOT EXISTS enquete_alternativas (
    id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    enquete_id text NOT NULL REFERENCES enquetes(id),
    option_external_id text,
    label text NOT NULL,
    qty integer NOT NULL CHECK (qty IN (3, 4, 6, 8, 9, 12, 16, 20, 24)),
    position integer NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS enquete_alternativas_enquete_qty_ux
    ON enquete_alternativas (enquete_id, qty);
CREATE UNIQUE INDEX IF NOT EXISTS enquete_alternativas_enquete_option_ux
    ON enquete_alternativas (enquete_id, option_external_id)
    WHERE option_external_id IS NOT NULL;

-- ============================================================
-- webhook_inbox
-- ============================================================
CREATE TABLE IF NOT EXISTS webhook_inbox (
    id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    provider text NOT NULL CHECK (provider IN ('whapi', 'evolution', 'unknown')),
    event_kind text NOT NULL,
    event_key text NOT NULL,
    payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    received_at timestamptz NOT NULL DEFAULT now(),
    processed_at timestamptz,
    status text NOT NULL DEFAULT 'received'
        CHECK (status IN ('received', 'processed', 'failed', 'duplicate')),
    error text
);
CREATE UNIQUE INDEX IF NOT EXISTS webhook_inbox_event_key_key ON webhook_inbox (event_key);
CREATE INDEX IF NOT EXISTS webhook_inbox_provider_status_idx ON webhook_inbox (provider, status);
CREATE INDEX IF NOT EXISTS webhook_inbox_received_at_idx ON webhook_inbox (received_at DESC);

-- ============================================================
-- votos
-- ============================================================
CREATE TABLE IF NOT EXISTS votos (
    id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    enquete_id text NOT NULL REFERENCES enquetes(id),
    cliente_id text NOT NULL REFERENCES clientes(id),
    alternativa_id text REFERENCES enquete_alternativas(id),
    qty integer NOT NULL CHECK (qty IN (0, 3, 4, 6, 8, 9, 12, 16, 20, 24)),
    status text NOT NULL DEFAULT 'out' CHECK (status IN ('in', 'out', 'wait')),
    synthetic smallint NOT NULL DEFAULT 0 CHECK (synthetic IN (0, 1)),
    voted_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS votos_enquete_id_cliente_id_key
    ON votos (enquete_id, cliente_id);
CREATE INDEX IF NOT EXISTS votos_enquete_cliente_idx ON votos (enquete_id, cliente_id);
CREATE INDEX IF NOT EXISTS votos_status_enquete_idx ON votos (status, enquete_id);

-- ============================================================
-- votos_eventos
-- ============================================================
CREATE TABLE IF NOT EXISTS votos_eventos (
    id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    enquete_id text NOT NULL REFERENCES enquetes(id),
    cliente_id text NOT NULL REFERENCES clientes(id),
    alternativa_id text REFERENCES enquete_alternativas(id),
    qty integer NOT NULL CHECK (qty IN (0, 3, 4, 6, 8, 9, 12, 16, 20, 24)),
    action text NOT NULL CHECK (action IN ('vote', 'remove', 'sync')),
    occurred_at timestamptz NOT NULL DEFAULT now(),
    raw_event_id text,
    payload_json jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS votos_eventos_enquete_occurred_idx
    ON votos_eventos (enquete_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS votos_eventos_cliente_occurred_idx
    ON votos_eventos (cliente_id, occurred_at DESC);

-- ============================================================
-- pacotes
-- ============================================================
CREATE TABLE IF NOT EXISTS pacotes (
    id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    enquete_id text NOT NULL REFERENCES enquetes(id),
    sequence_no integer NOT NULL CHECK (sequence_no >= 0),
    capacidade_total integer NOT NULL DEFAULT 24 CHECK (capacidade_total > 0),
    total_qty integer NOT NULL DEFAULT 0,
    participants_count integer NOT NULL DEFAULT 0 CHECK (participants_count >= 0),
    status text NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'closed', 'approved', 'cancelled')),
    opened_at timestamptz,
    closed_at timestamptz,
    approved_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    tag text,
    pdf_status text,
    pdf_file_name text,
    pdf_sent_at timestamptz,
    pdf_attempts integer NOT NULL DEFAULT 0,
    confirmed_by text,
    cancelled_at timestamptz,
    cancelled_by text,
    created_via text NOT NULL DEFAULT 'poll',
    shipped_at timestamptz,
    shipped_by text,
    custom_title text,
    fornecedor text,
    payment_validated_at timestamptz,
    pending_reasons jsonb,
    pending_observations text
);
CREATE UNIQUE INDEX IF NOT EXISTS pacotes_enquete_id_sequence_no_key
    ON pacotes (enquete_id, sequence_no);
CREATE INDEX IF NOT EXISTS pacotes_enquete_status_idx ON pacotes (enquete_id, status);
CREATE INDEX IF NOT EXISTS pacotes_status_updated_idx ON pacotes (status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pacotes_cancelled_at
    ON pacotes (cancelled_at DESC) WHERE cancelled_at IS NOT NULL;

-- ============================================================
-- pacote_clientes (granularidade fina nas fases finais)
-- ============================================================
CREATE TABLE IF NOT EXISTS pacote_clientes (
    id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    pacote_id text NOT NULL REFERENCES pacotes(id),
    cliente_id text NOT NULL REFERENCES clientes(id),
    voto_id text NOT NULL REFERENCES votos(id),
    produto_id text NOT NULL REFERENCES produtos(id),
    qty integer NOT NULL CHECK (qty > 0),
    unit_price numeric NOT NULL DEFAULT 0 CHECK (unit_price >= 0),
    subtotal numeric NOT NULL DEFAULT 0 CHECK (subtotal >= 0),
    commission_percent numeric NOT NULL DEFAULT 0
        CHECK (commission_percent >= 0 AND commission_percent <= 100),
    commission_amount numeric NOT NULL DEFAULT 0 CHECK (commission_amount >= 0),
    total_amount numeric NOT NULL DEFAULT 0 CHECK (total_amount >= 0),
    status text NOT NULL DEFAULT 'closed',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    payment_validated_at timestamptz,
    pdf_sent_at timestamptz,
    shipped_at timestamptz
);
CREATE UNIQUE INDEX IF NOT EXISTS pacote_clientes_pacote_id_cliente_id_key
    ON pacote_clientes (pacote_id, cliente_id);
CREATE INDEX IF NOT EXISTS pacote_clientes_pacote_idx ON pacote_clientes (pacote_id);
CREATE INDEX IF NOT EXISTS pacote_clientes_cliente_idx ON pacote_clientes (cliente_id);

-- ============================================================
-- vendas
-- ============================================================
CREATE TABLE IF NOT EXISTS vendas (
    id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    pacote_id text NOT NULL REFERENCES pacotes(id),
    cliente_id text NOT NULL REFERENCES clientes(id),
    produto_id text NOT NULL REFERENCES produtos(id),
    pacote_cliente_id text REFERENCES pacote_clientes(id) ON DELETE SET NULL,
    qty integer NOT NULL CHECK (qty > 0),
    unit_price numeric NOT NULL DEFAULT 0 CHECK (unit_price >= 0),
    subtotal numeric NOT NULL DEFAULT 0 CHECK (subtotal >= 0),
    commission_percent numeric NOT NULL DEFAULT 0
        CHECK (commission_percent >= 0 AND commission_percent <= 100),
    commission_amount numeric NOT NULL DEFAULT 0 CHECK (commission_amount >= 0),
    total_amount numeric NOT NULL DEFAULT 0 CHECK (total_amount >= 0),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'cancelled')),
    sold_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS vendas_pacote_id_cliente_id_key
    ON vendas (pacote_id, cliente_id);
CREATE INDEX IF NOT EXISTS vendas_pacote_status_idx ON vendas (pacote_id, status);
CREATE INDEX IF NOT EXISTS vendas_cliente_status_idx ON vendas (cliente_id, status);

-- ============================================================
-- pagamentos
-- ============================================================
CREATE TABLE IF NOT EXISTS pagamentos (
    id text PRIMARY KEY DEFAULT gen_random_uuid()::text,
    venda_id text NOT NULL REFERENCES vendas(id),
    provider text NOT NULL DEFAULT 'asaas'
        CHECK (provider IN ('asaas', 'mercadopago')),
    provider_customer_id text,
    provider_payment_id text,
    payment_link text,
    pix_payload text,
    due_date timestamptz,
    paid_at timestamptz,
    status text NOT NULL DEFAULT 'created'
        CHECK (status IN ('created', 'sent', 'paid', 'failed', 'cancelled')),
    payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS pagamentos_venda_id_key ON pagamentos (venda_id);
CREATE UNIQUE INDEX IF NOT EXISTS pagamentos_provider_payment_id_key
    ON pagamentos (provider_payment_id) WHERE provider_payment_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS pagamentos_provider_id_idx ON pagamentos (provider_payment_id);
CREATE INDEX IF NOT EXISTS pagamentos_status_venda_idx ON pagamentos (status, venda_id);

-- ============================================================
-- metrics_hourly_snapshots
-- ============================================================
CREATE TABLE IF NOT EXISTS metrics_hourly_snapshots (
    hour_bucket text PRIMARY KEY,
    active_enquetes_open integer NOT NULL DEFAULT 0,
    active_enquetes_total integer NOT NULL DEFAULT 0,
    closed_packages_on_active_enquetes integer NOT NULL DEFAULT 0,
    enquetes_active_72h integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- ============================================================
-- legacy_charges (vazia em prod limpa — deixada pra compatibilidade)
-- ============================================================
CREATE TABLE IF NOT EXISTS legacy_charges (
    id text PRIMARY KEY,
    package_id text,
    mercadopago_id text,
    poll_title text,
    customer_name text,
    customer_phone text,
    item_price numeric DEFAULT 0,
    quantity integer DEFAULT 0,
    subtotal numeric DEFAULT 0,
    commission_percent numeric DEFAULT 13,
    commission_amount numeric DEFAULT 0,
    total_amount numeric DEFAULT 0,
    status text,
    image text,
    source text,
    created_at timestamptz,
    confirmed_at timestamptz,
    sent_at timestamptz,
    paid_at timestamptz,
    updated_at timestamptz
);
CREATE INDEX IF NOT EXISTS legacy_charges_status_idx ON legacy_charges (status);
CREATE INDEX IF NOT EXISTS legacy_charges_customer_phone_idx ON legacy_charges (customer_phone);

-- ============================================================
-- app_runtime_state
-- ============================================================
CREATE TABLE IF NOT EXISTS app_runtime_state (
    key text PRIMARY KEY,
    payload_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_app_runtime_state_updated_at
    ON app_runtime_state (updated_at DESC);

-- ============================================================
-- drive_files (LocalImageStorage — substitui Google Drive)
-- ============================================================
CREATE TABLE IF NOT EXISTS drive_files (
    id text PRIMARY KEY,
    parent_id text,
    name text NOT NULL,
    mime_type text NOT NULL,
    ext text,
    is_folder smallint NOT NULL DEFAULT 0,
    deleted smallint NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_drive_files_parent ON drive_files (parent_id, deleted);
CREATE INDEX IF NOT EXISTS idx_drive_files_name ON drive_files (name, deleted);
CREATE INDEX IF NOT EXISTS idx_drive_files_created ON drive_files (created_at DESC);

-- ============================================================
-- Grants pra raylook_api (role do PostgREST)
-- ============================================================
GRANT USAGE ON SCHEMA public TO raylook_api;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO raylook_api;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO raylook_api;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO raylook_api;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO raylook_api;
