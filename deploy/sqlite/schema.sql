-- Schema SQLite do Raylook (dev).
-- Tradução fiel do schema Postgres (deploy/postgres/schema.sql)
-- + colunas adicionadas em migrations F031, F039, F036, F046, F061, portal_auth, cleanup_lid_phantoms, confirmed_edit_nullable_fk.
--
-- Regras de tradução:
--   uuid            → TEXT (gerado em Python com uuid.uuid4())
--   timestamptz     → TEXT (ISO8601 UTC: "2026-04-20T12:34:56+00:00")
--   numeric         → REAL
--   jsonb           → TEXT (JSON-encoded)
--   boolean         → INTEGER (0/1)
--   ENUMs           → TEXT + CHECK
--   gen_random_uuid() → default gerado pelo client (SQLite não tem)
--   funções PL/pgSQL → reimplementadas em Python (ver sqlite_service.py)

PRAGMA foreign_keys = ON;

-- ============================================================
-- produtos
-- ============================================================
CREATE TABLE IF NOT EXISTS produtos (
    id TEXT PRIMARY KEY,
    nome TEXT NOT NULL,
    descricao TEXT,
    tamanho TEXT,
    valor_unitario REAL NOT NULL DEFAULT 0 CHECK (valor_unitario >= 0),
    drive_folder_id TEXT,
    drive_file_id TEXT,
    image_message_id TEXT,
    source TEXT NOT NULL DEFAULT 'whapi',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- ============================================================
-- clientes
-- ============================================================
CREATE TABLE IF NOT EXISTS clientes (
    id TEXT PRIMARY KEY,
    nome TEXT NOT NULL,
    celular TEXT NOT NULL,
    email TEXT,
    nome_loja TEXT,
    cpf_cnpj TEXT,
    password_hash TEXT,
    session_token TEXT,
    session_expires_at TEXT,
    reset_token TEXT,
    reset_token_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS clientes_celular_key ON clientes (celular);

-- ============================================================
-- enquetes
-- ============================================================
CREATE TABLE IF NOT EXISTS enquetes (
    id TEXT PRIMARY KEY,
    external_poll_id TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT 'unknown'
        CHECK (provider IN ('whapi', 'evolution', 'unknown')),
    chat_id TEXT,
    produto_id TEXT REFERENCES produtos(id),
    titulo TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'closed', 'cancelled')),
    source TEXT NOT NULL DEFAULT 'whapi',
    drive_folder_id TEXT,
    drive_file_id TEXT,
    image_message_id TEXT,
    created_at_provider TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    fornecedor TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS enquetes_external_poll_id_key ON enquetes (external_poll_id);

-- ============================================================
-- enquete_alternativas
-- ============================================================
CREATE TABLE IF NOT EXISTS enquete_alternativas (
    id TEXT PRIMARY KEY,
    enquete_id TEXT NOT NULL REFERENCES enquetes(id),
    option_external_id TEXT,
    label TEXT NOT NULL,
    qty INTEGER NOT NULL CHECK (qty IN (3, 6, 9, 12)),
    position INTEGER NOT NULL DEFAULT 0
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
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL CHECK (provider IN ('whapi', 'evolution', 'unknown')),
    event_kind TEXT NOT NULL,
    event_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    received_at TEXT NOT NULL,
    processed_at TEXT,
    status TEXT NOT NULL DEFAULT 'received'
        CHECK (status IN ('received', 'processed', 'failed', 'duplicate')),
    error TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS webhook_inbox_event_key_key ON webhook_inbox (event_key);
CREATE INDEX IF NOT EXISTS webhook_inbox_provider_status_idx ON webhook_inbox (provider, status);
CREATE INDEX IF NOT EXISTS webhook_inbox_received_at_idx ON webhook_inbox (received_at DESC);

-- ============================================================
-- votos  (qty check relaxado pela migration F031)
-- ============================================================
CREATE TABLE IF NOT EXISTS votos (
    id TEXT PRIMARY KEY,
    enquete_id TEXT NOT NULL REFERENCES enquetes(id),
    cliente_id TEXT NOT NULL REFERENCES clientes(id),
    alternativa_id TEXT REFERENCES enquete_alternativas(id),
    qty INTEGER NOT NULL CHECK (qty >= 0 AND qty <= 24 AND qty % 3 = 0),
    status TEXT NOT NULL DEFAULT 'out' CHECK (status IN ('in', 'out', 'wait')),
    synthetic INTEGER NOT NULL DEFAULT 0 CHECK (synthetic IN (0, 1)),
    voted_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS votos_enquete_id_cliente_id_key
    ON votos (enquete_id, cliente_id);
CREATE INDEX IF NOT EXISTS votos_enquete_cliente_idx ON votos (enquete_id, cliente_id);
CREATE INDEX IF NOT EXISTS votos_status_enquete_idx ON votos (status, enquete_id);

-- ============================================================
-- votos_eventos
-- ============================================================
CREATE TABLE IF NOT EXISTS votos_eventos (
    id TEXT PRIMARY KEY,
    enquete_id TEXT NOT NULL REFERENCES enquetes(id),
    cliente_id TEXT NOT NULL REFERENCES clientes(id),
    alternativa_id TEXT REFERENCES enquete_alternativas(id),
    qty INTEGER NOT NULL CHECK (qty >= 0 AND qty <= 24 AND qty % 3 = 0),
    action TEXT NOT NULL CHECK (action IN ('vote', 'remove', 'sync')),
    occurred_at TEXT NOT NULL,
    raw_event_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS votos_eventos_enquete_occurred_idx
    ON votos_eventos (enquete_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS votos_eventos_cliente_occurred_idx
    ON votos_eventos (cliente_id, occurred_at DESC);

-- ============================================================
-- pacotes
-- ============================================================
CREATE TABLE IF NOT EXISTS pacotes (
    id TEXT PRIMARY KEY,
    enquete_id TEXT NOT NULL REFERENCES enquetes(id),
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 0),
    capacidade_total INTEGER NOT NULL DEFAULT 24 CHECK (capacidade_total > 0),
    total_qty INTEGER NOT NULL DEFAULT 0,
    participants_count INTEGER NOT NULL DEFAULT 0 CHECK (participants_count >= 0),
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'closed', 'approved', 'cancelled')),
    opened_at TEXT,
    closed_at TEXT,
    approved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    tag TEXT,
    pdf_status TEXT,
    pdf_file_name TEXT,
    pdf_sent_at TEXT,
    pdf_attempts INTEGER NOT NULL DEFAULT 0,
    confirmed_by TEXT,
    cancelled_at TEXT,
    cancelled_by TEXT,
    created_via TEXT NOT NULL DEFAULT 'poll',
    -- shipped_at: quando o pacote foi despachado pro cliente (6º estado do fluxo
    -- "enviado"). Preenchido pela UI quando o operador marca como enviado.
    shipped_at TEXT,
    shipped_by TEXT,
    custom_title TEXT,
    fornecedor TEXT,
    -- payment_validated_at: gate manual entre "pago" (todos pagamentos paid) e
    -- "pendente" (liberado pra estoque separar). Operador valida o pagamento.
    payment_validated_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS pacotes_enquete_id_sequence_no_key
    ON pacotes (enquete_id, sequence_no);
CREATE INDEX IF NOT EXISTS pacotes_enquete_status_idx ON pacotes (enquete_id, status);
CREATE INDEX IF NOT EXISTS pacotes_status_updated_idx ON pacotes (status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_pacotes_cancelled_at
    ON pacotes (cancelled_at DESC)
    WHERE cancelled_at IS NOT NULL;

-- ============================================================
-- pacote_clientes
-- ============================================================
CREATE TABLE IF NOT EXISTS pacote_clientes (
    id TEXT PRIMARY KEY,
    pacote_id TEXT NOT NULL REFERENCES pacotes(id),
    cliente_id TEXT NOT NULL REFERENCES clientes(id),
    voto_id TEXT NOT NULL REFERENCES votos(id),
    produto_id TEXT NOT NULL REFERENCES produtos(id),
    qty INTEGER NOT NULL CHECK (qty > 0),
    unit_price REAL NOT NULL DEFAULT 0 CHECK (unit_price >= 0),
    subtotal REAL NOT NULL DEFAULT 0 CHECK (subtotal >= 0),
    commission_percent REAL NOT NULL DEFAULT 0
        CHECK (commission_percent >= 0 AND commission_percent <= 100),
    commission_amount REAL NOT NULL DEFAULT 0 CHECK (commission_amount >= 0),
    total_amount REAL NOT NULL DEFAULT 0 CHECK (total_amount >= 0),
    status TEXT NOT NULL DEFAULT 'closed',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    -- Granularidade fina: cada cliente avança individualmente nas fases finais.
    payment_validated_at TEXT,
    pdf_sent_at TEXT,
    shipped_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS pacote_clientes_pacote_id_cliente_id_key
    ON pacote_clientes (pacote_id, cliente_id);
CREATE INDEX IF NOT EXISTS pacote_clientes_pacote_idx ON pacote_clientes (pacote_id);
CREATE INDEX IF NOT EXISTS pacote_clientes_cliente_idx ON pacote_clientes (cliente_id);

-- ============================================================
-- vendas  (pacote_cliente_id nullable via migration_confirmed_edit_nullable_fk)
-- ============================================================
CREATE TABLE IF NOT EXISTS vendas (
    id TEXT PRIMARY KEY,
    pacote_id TEXT NOT NULL REFERENCES pacotes(id),
    cliente_id TEXT NOT NULL REFERENCES clientes(id),
    produto_id TEXT NOT NULL REFERENCES produtos(id),
    pacote_cliente_id TEXT REFERENCES pacote_clientes(id) ON DELETE SET NULL,
    qty INTEGER NOT NULL CHECK (qty > 0),
    unit_price REAL NOT NULL DEFAULT 0 CHECK (unit_price >= 0),
    subtotal REAL NOT NULL DEFAULT 0 CHECK (subtotal >= 0),
    commission_percent REAL NOT NULL DEFAULT 0
        CHECK (commission_percent >= 0 AND commission_percent <= 100),
    commission_amount REAL NOT NULL DEFAULT 0 CHECK (commission_amount >= 0),
    total_amount REAL NOT NULL DEFAULT 0 CHECK (total_amount >= 0),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'cancelled')),
    sold_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS vendas_pacote_id_cliente_id_key
    ON vendas (pacote_id, cliente_id);
CREATE INDEX IF NOT EXISTS vendas_pacote_status_idx ON vendas (pacote_id, status);
CREATE INDEX IF NOT EXISTS vendas_cliente_status_idx ON vendas (cliente_id, status);

-- ============================================================
-- pagamentos (status inclui 'written_off' via migration_F062_pagamento_written_off)
-- ============================================================
CREATE TABLE IF NOT EXISTS pagamentos (
    id TEXT PRIMARY KEY,
    venda_id TEXT NOT NULL REFERENCES vendas(id),
    provider TEXT NOT NULL DEFAULT 'asaas'
        CHECK (provider IN ('asaas', 'mercadopago')),
    provider_customer_id TEXT,
    provider_payment_id TEXT,
    payment_link TEXT,
    pix_payload TEXT,
    due_date TEXT,
    paid_at TEXT,
    status TEXT NOT NULL DEFAULT 'created'
        CHECK (status IN ('created', 'sent', 'paid', 'failed', 'cancelled', 'written_off')),
    written_off_at TEXT,
    written_off_reason TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS pagamentos_venda_id_key ON pagamentos (venda_id);
CREATE UNIQUE INDEX IF NOT EXISTS pagamentos_provider_payment_id_key
    ON pagamentos (provider_payment_id)
    WHERE provider_payment_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS pagamentos_provider_id_idx ON pagamentos (provider_payment_id);
CREATE INDEX IF NOT EXISTS pagamentos_status_venda_idx ON pagamentos (status, venda_id);

-- ============================================================
-- metrics_hourly_snapshots (F-045) — snapshots horários de KPIs.
-- Em dev fica vazia; o worker de snapshot vai popular aos poucos.
-- ============================================================
CREATE TABLE IF NOT EXISTS metrics_hourly_snapshots (
    hour_bucket TEXT PRIMARY KEY,
    active_enquetes_open INTEGER NOT NULL DEFAULT 0,
    active_enquetes_total INTEGER NOT NULL DEFAULT 0,
    closed_packages_on_active_enquetes INTEGER NOT NULL DEFAULT 0,
    enquetes_active_72h INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- ============================================================
-- legacy_charges (histórico de cobranças MercadoPago pré-refactor;
--                 callers já tratam ausência, mas criamos vazia pra evitar
--                 warnings constantes no log de dev)
-- ============================================================
CREATE TABLE IF NOT EXISTS legacy_charges (
    id TEXT PRIMARY KEY,
    package_id TEXT,
    mercadopago_id TEXT,
    poll_title TEXT,
    customer_name TEXT,
    customer_phone TEXT,
    item_price REAL DEFAULT 0,
    quantity INTEGER DEFAULT 0,
    subtotal REAL DEFAULT 0,
    commission_percent REAL DEFAULT 13,
    commission_amount REAL DEFAULT 0,
    total_amount REAL DEFAULT 0,
    status TEXT,
    image TEXT,
    source TEXT,
    created_at TEXT,
    confirmed_at TEXT,
    sent_at TEXT,
    paid_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS legacy_charges_status_idx ON legacy_charges (status);
CREATE INDEX IF NOT EXISTS legacy_charges_customer_phone_idx ON legacy_charges (customer_phone);

-- ============================================================
-- app_runtime_state (key-value JSON genérico)
-- ============================================================
CREATE TABLE IF NOT EXISTS app_runtime_state (
    key TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_app_runtime_state_updated_at
    ON app_runtime_state (updated_at DESC);

-- ============================================================
-- drive_files (storage local de imagens — substitui Google Drive)
-- ============================================================
-- Cada linha representa uma "pasta" (is_folder=1) ou "arquivo" (is_folder=0).
-- Bytes ficam em data/images/<parent_id>/<id>.<ext>. Pastas só servem como
-- agrupamento lógico — não criam diretório vazio em disco.
CREATE TABLE IF NOT EXISTS drive_files (
    id TEXT PRIMARY KEY,
    parent_id TEXT,
    name TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    ext TEXT,
    is_folder INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_drive_files_parent ON drive_files (parent_id, deleted);
CREATE INDEX IF NOT EXISTS idx_drive_files_name ON drive_files (name, deleted);
CREATE INDEX IF NOT EXISTS idx_drive_files_created ON drive_files (created_at DESC);
