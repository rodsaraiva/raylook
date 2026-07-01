-- F067: motivo de cancelamento do pacote, exibido pra cliente no portal.
--
-- Quando a admin cancela um pacote com peças já pagas (gatilho do crédito),
-- a peça volta a aparecer no portal com a tag "Cancelado". cancel_reason
-- guarda a explicação (texto livre) digitada no momento do cancelamento.
--
-- ⚠️ ORDEM DE DEPLOY (obrigatória): rodar ESTA migration ANTES de subir o
-- código. O portal (get_client_orders) passa a pedir `cancel_reason` no embed
-- do PostgREST; se o código subir antes do PostgREST enxergar a coluna, a
-- página de pedidos do cliente quebra pra todos. O NOTIFY abaixo força o
-- PostgREST a recarregar o schema-cache no COMMIT (equivale a reiniciar o
-- serviço raylook_postgrest).

BEGIN;

ALTER TABLE pacotes
    ADD COLUMN IF NOT EXISTS cancel_reason text;

-- Recarrega o schema-cache do PostgREST assim que a coluna existir.
NOTIFY pgrst, 'reload schema';

COMMIT;
