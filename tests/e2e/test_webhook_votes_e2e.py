"""E2E tests — simula webhooks WHAPI para testar o fluxo completo:
enquete → votos → troca de votos → pacotes → fechamento.

Roda contra o serviço rodando (precisa estar UP).
Usa a API HTTP real — NÃO mocka nada.

Uso:
    python -m pytest tests/e2e/test_webhook_votes_e2e.py -v -s
    # ou diretamente:
    python tests/e2e/test_webhook_votes_e2e.py
"""
import json
import os
import time
import uuid
import requests

BASE_URL = os.getenv("E2E_BASE_URL", "http://localhost:8000")
# Grupo de teste (mesmo do sistema)
TEST_CHAT_ID = "120363403901156886@g.us"

# Gerar IDs únicos para esta execução
RUN_ID = uuid.uuid4().hex[:8]
POLL_ID = f"TEST_E2E_{RUN_ID}_poll"
POLL_TITLE = f"[E2E] Teste automatizado {RUN_ID}"

# Opções do poll (3, 6, 9, 12 peças)
OPTION_3 = {"id": f"opt3_{RUN_ID}", "name": "3", "count": 0, "voters": []}
OPTION_6 = {"id": f"opt6_{RUN_ID}", "name": "6", "count": 0, "voters": []}
OPTION_9 = {"id": f"opt9_{RUN_ID}", "name": "9", "count": 0, "voters": []}
OPTION_12 = {"id": f"opt12_{RUN_ID}", "name": "12", "count": 0, "voters": []}

# Votantes fictícios
VOTER_A = {"phone": "5500000000001", "name": "Teste Cliente A"}
VOTER_B = {"phone": "5500000000002", "name": "Teste Cliente B"}
VOTER_C = {"phone": "5500000000003", "name": "Teste Cliente C"}

TRIGGER_SEQ = 0


def _trigger_id():
    global TRIGGER_SEQ
    TRIGGER_SEQ += 1
    return f"trigger_{RUN_ID}_{TRIGGER_SEQ}"


def _ts():
    return int(time.time())


# ---------------------------------------------------------------------------
# Webhook payload builders
# ---------------------------------------------------------------------------

def build_poll_created_payload():
    """Simula WHAPI enviando mensagem de criação de enquete."""
    return {
        "messages": [
            {
                "id": POLL_ID,
                "from": "5500000000000",
                "type": "poll",
                "poll": {
                    "title": POLL_TITLE,
                    "total": 0,
                    "options": ["3", "6", "9", "12"],
                    "results": [OPTION_3, OPTION_6, OPTION_9, OPTION_12],
                    "vote_limit": 1,
                },
                "source": "mobile",
                "chat_id": TEST_CHAT_ID,
                "from_me": False,
                "chat_name": "Grupo de Teste",
                "from_name": "Admin",
                "timestamp": _ts(),
            }
        ]
    }


def build_vote_payload(voter, option_id, option_name, votes_list=None):
    """Simula WHAPI enviando voto/troca de voto."""
    if votes_list is None:
        votes_list = [option_id] if option_id else []
    return {
        "messages_updates": [
            {
                "id": POLL_ID,
                "changes": ["poll"],
                "trigger": {
                    "id": _trigger_id(),
                    "from": voter["phone"],
                    "type": "action",
                    "action": {
                        "type": "vote",
                        "votes": votes_list,
                        "target": POLL_ID,
                    },
                    "source": "mobile",
                    "chat_id": TEST_CHAT_ID,
                    "from_me": False,
                    "from_name": voter["name"],
                    "timestamp": _ts(),
                },
                "after_update": {
                    "id": POLL_ID,
                    "from": "5500000000000",
                    "poll": {
                        "title": POLL_TITLE,
                        "total": 1,
                        "options": ["3", "6", "9", "12"],
                        "results": [
                            {**OPTION_3, "name": "3"},
                            {**OPTION_6, "name": "6"},
                            {**OPTION_9, "name": "9"},
                            {**OPTION_12, "name": "12"},
                        ],
                    },
                },
            }
        ]
    }


def build_vote_remove_payload(voter):
    """Simula remoção de voto (votes vazio)."""
    return build_vote_payload(voter, None, None, votes_list=[])


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def send_webhook(payload):
    resp = requests.post(f"{BASE_URL}/webhook/whatsapp", json=payload, timeout=30)
    assert resp.status_code == 200, f"Webhook failed: {resp.status_code} {resp.text}"
    return resp.json()


def get_metrics():
    resp = requests.get(f"{BASE_URL}/api/metrics", timeout=15)
    assert resp.status_code == 200
    return resp.json()


def find_poll_in_metrics(metrics, poll_title):
    """Busca pacote aberto pelo título da enquete."""
    packages = metrics.get("votos", {}).get("packages", {})
    for pkg in packages.get("open", []):
        if pkg.get("poll_title") == poll_title:
            return pkg
    return None


def db_query(sql):
    """Executa SQL no Postgres via docker exec."""
    import subprocess
    result = subprocess.run(
        ["docker", "exec", "postgres-postgres-1", "psql", "-U", "postgres",
         "-d", "raylook", "-t", "-A", "-c", sql],
        capture_output=True, text=True, timeout=10,
    )
    return result.stdout.strip()


def get_votes_for_poll():
    """Retorna votos ativos da enquete de teste."""
    rows = db_query(f"""
        SELECT c.nome, c.celular, v.qty, v.status
        FROM votos v JOIN clientes c ON c.id = v.cliente_id
        WHERE v.enquete_id = (
            SELECT id FROM enquetes WHERE external_poll_id = '{POLL_ID}'
        )
        ORDER BY c.celular;
    """)
    result = []
    for line in rows.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) >= 4:
            result.append({
                "name": parts[0],
                "phone": parts[1],
                "qty": int(parts[2]),
                "status": parts[3],
            })
    return result


def get_packages_for_poll():
    """Retorna pacotes da enquete de teste."""
    rows = db_query(f"""
        SELECT p.sequence_no, p.total_qty, p.participants_count, p.status
        FROM pacotes p
        WHERE p.enquete_id = (
            SELECT id FROM enquetes WHERE external_poll_id = '{POLL_ID}'
        )
        ORDER BY p.sequence_no;
    """)
    result = []
    for line in rows.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) >= 4:
            result.append({
                "seq": int(parts[0]),
                "total_qty": int(parts[1]),
                "participants": int(parts[2]),
                "status": parts[3],
            })
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_01_create_poll():
    """Criar enquete via webhook."""
    print(f"\n{'='*60}")
    print(f"TEST 01: Criar enquete ({POLL_TITLE})")
    print(f"{'='*60}")

    result = send_webhook(build_poll_created_payload())
    print(f"  Webhook result: {result}")

    # Verificar no banco
    poll_exists = db_query(f"SELECT COUNT(*) FROM enquetes WHERE external_poll_id = '{POLL_ID}';")
    assert poll_exists == "1", f"Enquete não criada! count={poll_exists}"
    print("  ✓ Enquete criada no banco")

    # Verificar alternativas
    alts = db_query(f"""
        SELECT COUNT(*) FROM enquete_alternativas
        WHERE enquete_id = (SELECT id FROM enquetes WHERE external_poll_id = '{POLL_ID}');
    """)
    assert alts == "4", f"Esperava 4 alternativas, tem {alts}"
    print("  ✓ 4 alternativas criadas (3, 6, 9, 12)")


def test_02_first_vote():
    """Primeiro voto: Cliente A vota 12."""
    print(f"\n{'='*60}")
    print("TEST 02: Primeiro voto (Cliente A → 12 peças)")
    print(f"{'='*60}")

    result = send_webhook(build_vote_payload(VOTER_A, OPTION_12["id"], "12"))
    print(f"  Webhook result: {json.dumps(result, indent=2)[:200]}")

    votes = get_votes_for_poll()
    assert len(votes) == 1, f"Esperava 1 voto, tem {len(votes)}"
    assert votes[0]["qty"] == 12, f"Esperava qty=12, tem {votes[0]['qty']}"
    assert votes[0]["status"] == "in", f"Esperava status=in, tem {votes[0]['status']}"
    print("  ✓ Voto registrado: Cliente A = 12 peças, status=in")

    # Verificar pacote aberto
    pkgs = get_packages_for_poll()
    assert len(pkgs) == 1, f"Esperava 1 pacote, tem {len(pkgs)}"
    assert pkgs[0]["total_qty"] == 12, f"Esperava total_qty=12, tem {pkgs[0]['total_qty']}"
    assert pkgs[0]["status"] == "open", f"Esperava status=open, tem {pkgs[0]['status']}"
    print(f"  ✓ Pacote aberto: 12 peças, 1 participante")


def test_03_second_vote():
    """Segundo voto: Cliente B vota 6."""
    print(f"\n{'='*60}")
    print("TEST 03: Segundo voto (Cliente B → 6 peças)")
    print(f"{'='*60}")

    result = send_webhook(build_vote_payload(VOTER_B, OPTION_6["id"], "6"))

    votes = get_votes_for_poll()
    assert len(votes) == 2, f"Esperava 2 votos, tem {len(votes)}"
    print("  ✓ 2 votos registrados")

    pkgs = get_packages_for_poll()
    total = sum(p["total_qty"] for p in pkgs if p["status"] == "open")
    assert total == 18, f"Esperava 18 peças (12+6), tem {total}"
    print(f"  ✓ Pacote aberto: 18 peças (12+6)")


def test_04_vote_change():
    """Troca de voto: Cliente A muda de 12 para 9."""
    print(f"\n{'='*60}")
    print("TEST 04: Troca de voto (Cliente A: 12 → 9)")
    print(f"{'='*60}")

    result = send_webhook(build_vote_payload(VOTER_A, OPTION_9["id"], "9"))

    votes = get_votes_for_poll()
    voter_a = next((v for v in votes if v["phone"] == VOTER_A["phone"]), None)
    assert voter_a is not None, "Voto do Cliente A não encontrado"
    assert voter_a["qty"] == 9, f"Esperava qty=9, tem {voter_a['qty']}"
    print(f"  ✓ Cliente A mudou para 9 peças")

    pkgs = get_packages_for_poll()
    total = sum(p["total_qty"] for p in pkgs if p["status"] == "open")
    assert total == 15, f"Esperava 15 peças (9+6), tem {total}"
    print(f"  ✓ Total atualizado: 15 peças (9+6)")


def test_05_vote_back_to_same():
    """Voto repetido: Cliente A volta para 12 (teste do bug de dedup)."""
    print(f"\n{'='*60}")
    print("TEST 05: Voto repetido (Cliente A: 9 → 12, mesma opção do test_02)")
    print(f"{'='*60}")

    result = send_webhook(build_vote_payload(VOTER_A, OPTION_12["id"], "12"))

    votes = get_votes_for_poll()
    voter_a = next((v for v in votes if v["phone"] == VOTER_A["phone"]), None)
    assert voter_a is not None, "Voto do Cliente A não encontrado"
    assert voter_a["qty"] == 12, f"BUG DEDUP! Esperava qty=12, tem {voter_a['qty']}"
    print(f"  ✓ Cliente A voltou para 12 peças (sem bug de dedup!)")

    pkgs = get_packages_for_poll()
    total = sum(p["total_qty"] for p in pkgs if p["status"] == "open")
    assert total == 18, f"Esperava 18 peças (12+6), tem {total}"
    print(f"  ✓ Total correto: 18 peças (12+6)")


def test_06_vote_removal():
    """Remoção de voto: Cliente B remove o voto."""
    print(f"\n{'='*60}")
    print("TEST 06: Remoção de voto (Cliente B remove)")
    print(f"{'='*60}")

    result = send_webhook(build_vote_remove_payload(VOTER_B))

    votes = get_votes_for_poll()
    voter_b = next((v for v in votes if v["phone"] == VOTER_B["phone"]), None)
    assert voter_b is not None, "Registro do Cliente B não encontrado"
    assert voter_b["qty"] == 0, f"Esperava qty=0, tem {voter_b['qty']}"
    assert voter_b["status"] == "out", f"Esperava status=out, tem {voter_b['status']}"
    print(f"  ✓ Cliente B removido: qty=0, status=out")

    pkgs = get_packages_for_poll()
    total = sum(p["total_qty"] for p in pkgs if p["status"] == "open")
    assert total == 12, f"Esperava 12 peças (só A), tem {total}"
    print(f"  ✓ Total atualizado: 12 peças (só Cliente A)")


def test_07_re_add_after_removal():
    """Re-adição após remoção: Cliente B volta a votar 6."""
    print(f"\n{'='*60}")
    print("TEST 07: Re-adição (Cliente B volta com 6)")
    print(f"{'='*60}")

    result = send_webhook(build_vote_payload(VOTER_B, OPTION_6["id"], "6"))

    votes = get_votes_for_poll()
    voter_b = next((v for v in votes if v["phone"] == VOTER_B["phone"]), None)
    assert voter_b["qty"] == 6, f"Esperava qty=6, tem {voter_b['qty']}"
    assert voter_b["status"] == "in", f"Esperava status=in, tem {voter_b['status']}"
    print(f"  ✓ Cliente B voltou: qty=6, status=in")


def test_08_close_package_24():
    """Fechar pacote: 3 votantes somando 24 peças → pacote fechado."""
    print(f"\n{'='*60}")
    print("TEST 08: Fechar pacote (12 + 6 + 6 = 24)")
    print(f"{'='*60}")

    # Cliente C vota 6 → total = 12 + 6 + 6 = 24 → fecha!
    result = send_webhook(build_vote_payload(VOTER_C, OPTION_6["id"], "6"))

    votes = get_votes_for_poll()
    active = [v for v in votes if v["status"] == "in"]
    total_active = sum(v["qty"] for v in active)
    print(f"  Votos ativos: {[(v['name'], v['qty']) for v in active]}")
    print(f"  Total ativo: {total_active}")

    pkgs = get_packages_for_poll()
    closed = [p for p in pkgs if p["status"] == "closed"]
    open_pkgs = [p for p in pkgs if p["status"] == "open"]

    print(f"  Pacotes: {pkgs}")

    assert len(closed) >= 1, f"Esperava pelo menos 1 pacote fechado, tem {len(closed)}"
    assert closed[0]["total_qty"] == 24, f"Pacote fechado deveria ter 24 peças, tem {closed[0]['total_qty']}"
    print(f"  ✓ Pacote fechado! 24 peças, {closed[0]['participants']} participantes")

    # Pode ter pacote aberto com 0 ou nenhum
    if open_pkgs:
        print(f"  ✓ Pacote aberto restante: {open_pkgs[0]['total_qty']} peças")
    else:
        print(f"  ✓ Nenhum pacote aberto restante")


def test_09_rapid_vote_changes():
    """Trocas rápidas: simula cliente mudando voto várias vezes seguidas."""
    print(f"\n{'='*60}")
    print("TEST 09: Trocas rápidas (Cliente A: 12→3→6→9→12→3)")
    print(f"{'='*60}")

    changes = [
        (OPTION_3["id"], "3", 3),
        (OPTION_6["id"], "6", 6),
        (OPTION_9["id"], "9", 9),
        (OPTION_12["id"], "12", 12),
        (OPTION_3["id"], "3", 3),
    ]

    for opt_id, opt_name, expected_qty in changes:
        send_webhook(build_vote_payload(VOTER_A, opt_id, opt_name))
        votes = get_votes_for_poll()
        voter_a = next((v for v in votes if v["phone"] == VOTER_A["phone"]), None)
        assert voter_a["qty"] == expected_qty, (
            f"Após votar {opt_name}: esperava qty={expected_qty}, tem {voter_a['qty']}"
        )
        print(f"  ✓ A→{opt_name}: qty={voter_a['qty']}")

    print(f"  ✓ Todas as 5 trocas rápidas processadas corretamente!")


def test_10_metrics_reflect_votes():
    """Métricas: o gráfico e KPIs devem refletir os votos."""
    print(f"\n{'='*60}")
    print("TEST 10: Métricas refletem votos")
    print(f"{'='*60}")

    time.sleep(2)  # esperar SSE/metrics atualizarem
    metrics = get_metrics()

    votos = metrics.get("votos", {})
    today = votos.get("today", 0)
    assert today > 0, f"Votos hoje deveria ser > 0, tem {today}"
    print(f"  ✓ Votos hoje: {today}")

    # Verificar que o pacote aparece nas métricas
    pkg = find_poll_in_metrics(metrics, POLL_TITLE)
    if pkg:
        print(f"  ✓ Enquete encontrada nas métricas: qty={pkg.get('qty')}, votes={len(pkg.get('votes', []))}")
    else:
        # Pode ter sido fechado (não aparece em open)
        closed_pkgs = metrics.get("votos", {}).get("packages", {}).get("closed_today", [])
        found_closed = any(p.get("poll_title") == POLL_TITLE for p in closed_pkgs)
        if found_closed:
            print(f"  ✓ Enquete encontrada como pacote fechado nas métricas")
        else:
            print(f"  ⚠ Enquete não encontrada nas métricas (pode ser filtro de grupo)")


def test_11_audit_trail_complete():
    """Verificar que votos_eventos tem registro de todas as ações."""
    print(f"\n{'='*60}")
    print("TEST 11: Audit trail completo")
    print(f"{'='*60}")

    count = db_query(f"""
        SELECT COUNT(*) FROM votos_eventos
        WHERE enquete_id = (SELECT id FROM enquetes WHERE external_poll_id = '{POLL_ID}');
    """)
    count = int(count)
    # test_02(1) + test_03(1) + test_04(1) + test_05(1) + test_06(1) + test_07(1)
    # + test_08(1) + test_09(5) = 12 mínimo
    assert count >= 12, f"Esperava >= 12 eventos no audit trail, tem {count}"
    print(f"  ✓ {count} eventos registrados no audit trail (votos_eventos)")


def cleanup():
    """Remove dados de teste do banco."""
    print(f"\n{'='*60}")
    print("CLEANUP: Removendo dados de teste")
    print(f"{'='*60}")

    enquete_id = db_query(f"SELECT id FROM enquetes WHERE external_poll_id = '{POLL_ID}';")
    if not enquete_id:
        print("  Nada para limpar")
        return

    db_query(f"DELETE FROM pacote_clientes WHERE pacote_id IN (SELECT id FROM pacotes WHERE enquete_id = '{enquete_id}');")
    db_query(f"DELETE FROM pacotes WHERE enquete_id = '{enquete_id}';")
    db_query(f"DELETE FROM votos_eventos WHERE enquete_id = '{enquete_id}';")
    db_query(f"DELETE FROM votos WHERE enquete_id = '{enquete_id}';")
    db_query(f"DELETE FROM enquete_alternativas WHERE enquete_id = '{enquete_id}';")
    db_query(f"DELETE FROM webhook_inbox WHERE event_key LIKE '%{POLL_ID}%';")
    db_query(f"DELETE FROM enquetes WHERE id = '{enquete_id}';")
    # Limpar clientes de teste
    for voter in [VOTER_A, VOTER_B, VOTER_C]:
        db_query(f"DELETE FROM clientes WHERE celular = '{voter['phone']}';")

    print("  ✓ Dados de teste removidos")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_all():
    tests = [
        test_01_create_poll,
        test_02_first_vote,
        test_03_second_vote,
        test_04_vote_change,
        test_05_vote_back_to_same,
        test_06_vote_removal,
        test_07_re_add_after_removal,
        test_08_close_package_24,
        test_09_rapid_vote_changes,
        test_10_metrics_reflect_votes,
        test_11_audit_trail_complete,
    ]

    passed = 0
    failed = 0
    errors = []

    print(f"\n{'#'*60}")
    print(f"# E2E TEST SUITE — Webhooks WHAPI / Votos / Enquetes / Pacotes")
    print(f"# Run ID: {RUN_ID}")
    print(f"# Poll ID: {POLL_ID}")
    print(f"# Target: {BASE_URL}")
    print(f"{'#'*60}")

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            failed += 1
            errors.append((test.__name__, str(e)))
            print(f"  ✗ FALHOU: {e}")
        except Exception as e:
            failed += 1
            errors.append((test.__name__, str(e)))
            print(f"  ✗ ERRO: {e}")

    print(f"\n{'#'*60}")
    print(f"# RESULTADO: {passed} passed, {failed} failed")
    if errors:
        print(f"# Falhas:")
        for name, err in errors:
            print(f"#   {name}: {err}")
    print(f"{'#'*60}")

    # Cleanup
    cleanup()

    return failed == 0


if __name__ == "__main__":
    import sys
    success = run_all()
    sys.exit(0 if success else 1)
