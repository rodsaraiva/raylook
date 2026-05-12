-- F-062: status 'written_off' em pagamentos.
-- Permite write-off manual de cobranças que o cliente abandonou
-- sem confundir com 'cancelled' (que é cancelamento ativo).

BEGIN;

ALTER TABLE pagamentos DROP CONSTRAINT IF EXISTS pagamentos_status_check;
ALTER TABLE pagamentos ADD CONSTRAINT pagamentos_status_check
  CHECK (status IN ('created','sent','paid','failed','cancelled','written_off'));

ALTER TABLE pagamentos ADD COLUMN IF NOT EXISTS written_off_at TIMESTAMPTZ;
ALTER TABLE pagamentos ADD COLUMN IF NOT EXISTS written_off_reason TEXT;

CREATE INDEX IF NOT EXISTS pagamentos_written_off_at_idx
  ON pagamentos (written_off_at)
  WHERE written_off_at IS NOT NULL;

COMMIT;
