# Alana — Glossário de domínio

> Termos do negócio. Manter curto e preciso.

| Termo | Definição |
|---|---|
| **Enquete** | Uma postagem de produto no WhatsApp (geralmente foto de uma peça de roupa + título com preço, ex: `"Blusa linda PMG $45,00"`). Clientes respondem votando. Representada em `enquetes` (1 linha por poll do WhatsApp). |
| **Alternativa** | Opção da enquete. No contexto Alana, são as quantidades (`0`, `3`, `6`, `9`, `12` peças). Em `enquete_alternativas`. |
| **Voto** | Resposta de 1 cliente a 1 enquete, com a quantidade escolhida. Upsert por `(enquete_id, cliente_id)` — se o cliente muda o voto, sobrescreve. Em `votos`. `qty=0` significa "cancelei meu voto". |
| **Voto evento** | Audit trail append-only de cada mudança de voto (`vote` ou `remove`). Em `votos_eventos`. |
| **Pacote** | Grupo de votos que juntos somam exatamente **24 peças** (= `capacidade_total`). Quando a enquete acumula 24 qty, o sistema "fecha" um pacote e começa a montar o próximo. Em `pacotes`. Identificado por `(enquete_id, sequence_no)`. |
| **sequence_no** | Número do pacote dentro da enquete, começando em 0. Convenção: `sequence_no=0` é o slot "aberto" (sobras); os fechados seguem 1, 2, 3... |
| **Pacote cliente** | 1 linha por cliente dentro de um pacote fechado, com preço unitário, comissão, total. Em `pacote_clientes`. Materializado apenas quando o pacote fecha — pacotes `open` não têm `pacote_clientes`. |
| **Confirmar pacote** | Ação do operador no dashboard que transiciona o pacote `closed → approved`. Dispara geração de vendas, criação de PIX no Asaas e envio de PDF de etiqueta ao estoque. Endpoint `POST /api/packages/{id}/confirm`. |
| **Venda** | Registro financeiro derivado de `pacote_clientes` quando o pacote é aprovado. Em `vendas`. 1 venda por `pacote_cliente`. |
| **Pagamento** | Cobrança PIX criada no Asaas pra uma venda. 1:1 com venda (via `pagamentos.venda_id UNIQUE`). |
| **Produto** | Peça de roupa linkada à enquete. Título + preço. Em `produtos`. Relação com enquete é redundante (também aparece em `pacote_clientes.produto_id` e `vendas.produto_id`). |
| **PMG** | "Pequeno / Médio / Grande" — padrão de tamanhos. Aparece nos títulos das enquetes. |
| **Comissão** | Sempre `13%` em `vendas` (tem CHECK constraint). Em `pacote_clientes.commission_percent` pode variar. |
| **Estoque** | Número de WhatsApp que recebe os PDFs de etiqueta (`ESTOQUE_PHONE_NUMBER`). |
| **Webhook inbox** | Fila de ingestão dos eventos do WhatsApp. UNIQUE por `event_key` pra idempotência. Estados: `received → processed | failed`. |
| **rebuild_for_poll** | Função central (`whatsapp_domain_service.py:341`) que, dado uma enquete, olha todos os votos `in` + `qty>0`, aplica o algoritmo de soma de subconjuntos, fecha pacotes quando achar subconjuntos de 24, e deixa sobras no pacote `open`. |
| **WHAPI** | Um dos providers de webhook do WhatsApp (externo). Alternativa ao Evolution API. |
| **Evolution API** | Self-hosted WhatsApp HTTP API rodando no VPS, usado pra **enviar** mensagens (PDFs, links PIX). |
| **Baileys** | Biblioteca Node pra WhatsApp. Usada pelo `/root/baileys-poll-listener/` (receptor alternativo de votos). |
| **Asaas** | Gateway de pagamentos brasileiro. Gera cobranças PIX e notifica quando pagas. |
| **Chat ID** | ID do grupo/chat do WhatsApp onde a enquete foi postada. `OFFICIAL_GROUP_CHAT_ID` vs `TEST_GROUP_CHAT_ID`. |
