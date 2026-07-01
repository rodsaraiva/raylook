-- F067: motivo de cancelamento do pacote, exibido pra cliente no portal.
--
-- Quando a admin cancela um pacote com peças já pagas (gatilho do crédito),
-- a peça volta a aparecer no portal com a tag "Cancelado". cancel_reason
-- guarda a explicação (texto livre) digitada no momento do cancelamento.

BEGIN;

ALTER TABLE pacotes
    ADD COLUMN IF NOT EXISTS cancel_reason text;

COMMIT;
