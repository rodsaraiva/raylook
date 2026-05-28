-- F066: senha temporária pra fluxo "esqueci minha senha"
--
-- temp_password_hash + temp_password_expires_at: senha aleatória de 30min
-- gerada quando o cliente clica em "esqueci minha senha". Login aceita
-- password_hash (senha real) OU temp_password_hash (se não expirou).
-- must_change_password: setada true no login com a temp; força modal de
-- troca de senha no portal e é zerada quando a senha é trocada.

BEGIN;

ALTER TABLE clientes
    ADD COLUMN IF NOT EXISTS temp_password_hash text,
    ADD COLUMN IF NOT EXISTS temp_password_expires_at timestamptz,
    ADD COLUMN IF NOT EXISTS must_change_password boolean NOT NULL DEFAULT false;

COMMIT;
