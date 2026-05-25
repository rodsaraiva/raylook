-- F-064: backfill de pacote_clientes.shipped_at e pdf_sent_at a partir
-- de pacotes.shipped_at / pacotes.pdf_sent_at. Necessário pra que as
-- seções Separado/Enviado do dashboard, agora granulares por cliente,
-- mostrem corretamente pacotes históricos despachados antes da feature.
-- Idempotente: só atualiza linhas onde o campo do pc é NULL.

BEGIN;

UPDATE pacote_clientes pc
   SET shipped_at = p.shipped_at
  FROM pacotes p
 WHERE pc.pacote_id = p.id
   AND p.shipped_at IS NOT NULL
   AND pc.shipped_at IS NULL;

UPDATE pacote_clientes pc
   SET pdf_sent_at = p.pdf_sent_at
  FROM pacotes p
 WHERE pc.pacote_id = p.id
   AND p.pdf_sent_at IS NOT NULL
   AND pc.pdf_sent_at IS NULL;

COMMIT;
