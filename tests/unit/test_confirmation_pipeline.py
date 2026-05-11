"""Testes para confirmation_pipeline.

Cobre: _clean_charge_item_name, _resolve_charge_rows,
run_post_confirmation_effects (moved vazio, pdf já enviado, persist flags,
exceção no finance_lock, pdf worker sucesso/falha).
"""
import asyncio
import sys
import types

import pytest

from app.services import confirmation_pipeline as svc


@pytest.fixture
def stub_pdf_modules():
    """Injeta stubs para módulos que dependem de xhtml2pdf (não instalado no sandbox)."""
    stubs = {}

    # estoque.pdf_builder
    estoque_pkg = sys.modules.get("estoque") or types.ModuleType("estoque")
    pdf_builder_mod = types.ModuleType("estoque.pdf_builder")
    pdf_builder_mod.build_pdf = lambda snapshot, pct: b"%PDF-fake"
    sys.modules.setdefault("estoque", estoque_pkg)
    sys.modules["estoque.pdf_builder"] = pdf_builder_mod
    stubs["estoque.pdf_builder"] = pdf_builder_mod

    # finance.utils
    finance_pkg = sys.modules.get("finance") or types.ModuleType("finance")
    finance_utils_mod = types.ModuleType("finance.utils")
    finance_utils_mod.get_pdf_filename_by_id = lambda pkg_id: "pacote_test.pdf"
    sys.modules.setdefault("finance", finance_pkg)
    sys.modules["finance.utils"] = finance_utils_mod
    stubs["finance.utils"] = finance_utils_mod

    yield stubs

    for key in list(stubs):
        sys.modules.pop(key, None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _moved_base(**overrides):
    data = {
        "id": "legacy-pkg-1",
        "source_package_id": "source-pkg-1",
        "poll_title": "[teste] Roupa Vermelha - 10 reais PMG",
        "votes": [
            {"phone": "5511999990001", "name": "Ana", "qty": 12},
            {"phone": "5511999990002", "name": "Bia", "qty": 12},
        ],
        "image": "https://example.com/image.png",
    }
    data.update(overrides)
    return data


def _run_sync(coro):
    """Executa uma coroutine com asyncio.run, drenando tasks pendentes."""
    async def _wrapper():
        await coro
        # Drena tasks criadas (PDF worker) sem aguardar resultado real
        tasks = [t for t in asyncio.all_tasks() if not t.done() and t != asyncio.current_task()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(_wrapper())


# ---------------------------------------------------------------------------
# Testes do teste original (mantido para compatibilidade)
# ---------------------------------------------------------------------------

def test_run_post_confirmation_effects_resolves_postgres_charges(monkeypatch):
    """Pós-confirmação resolve as charges no Postgres (sem enfileirar WhatsApp,
    que foi removido). Garante que FinanceManager legada não é usada quando
    a fonte do Postgres retorna charges."""
    moved = {
        "id": "legacy-pkg-1",
        "source_package_id": "source-pkg-1",
        "poll_title": "[teste] Roupa Vermelha - 10 reais PMG",
        "votes": [
            {"phone": "5511999990001", "name": "Ana", "qty": 12},
            {"phone": "5511999990002", "name": "Bia", "qty": 12},
        ],
        "pdf_status": "sent",
        "image": "https://example.com/image.png",
    }
    list_calls = []

    async def _run():
        await svc.run_post_confirmation_effects(
            moved,
            "source-pkg-1",
            metrics_data_to_save=None,
            persist_confirmed_package=False,
        )

    def _list_charges(package_id):
        list_calls.append(package_id)
        return [
            {
                "id": "db-charge-1",
                "package_id": package_id,
                "customer_name": "Ana",
                "customer_phone": "5511999990001",
                "quantity": 12,
                "subtotal": 120.0,
                "commission_percent": 13.0,
                "poll_title": moved["poll_title"],
                "image": moved["image"],
            }
        ]

    monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: True)
    monkeypatch.setattr("app.services.finance_service.list_package_charges", _list_charges)
    monkeypatch.setattr("app.services.confirmed_packages_service.add_confirmed_package", lambda *_args, **_kwargs: None)

    class _UnexpectedFinanceManager:
        def __init__(self, *args, **kwargs):
            raise AssertionError("FinanceManager legada nao deveria ser usada quando ha charge no Postgres")

    monkeypatch.setattr(svc, "FinanceManager", _UnexpectedFinanceManager)

    asyncio.run(_run())

    assert list_calls == ["source-pkg-1"]


# ---------------------------------------------------------------------------
# _clean_charge_item_name
# ---------------------------------------------------------------------------

class TestCleanChargeItemName:
    def test_remove_preco_reais(self):
        """Remove preço no formato R$ X,XX."""
        assert svc._clean_charge_item_name("Blusa R$ 45,00") == "Blusa"

    def test_remove_preco_cifrao(self):
        """Remove preço no formato $X.XX."""
        assert svc._clean_charge_item_name("Calça $30.00 linda") == "Calça linda"

    def test_remove_emojis(self):
        """Remove emojis da lista."""
        assert svc._clean_charge_item_name("🔥 Promoção 🎯") == "Promoção"

    def test_colapsa_espacos(self):
        """Múltiplos espaços são colapsados em um."""
        assert svc._clean_charge_item_name("Item   com   espacos") == "Item com espacos"

    def test_valor_vazio_retorna_peca(self):
        """String vazia retorna 'Peca'."""
        assert svc._clean_charge_item_name("") == "Peca"

    def test_valor_none_retorna_peca(self):
        """None retorna 'Peca'."""
        assert svc._clean_charge_item_name(None) == "Peca"

    def test_apenas_preco_retorna_peca(self):
        """String com só o preço retorna 'Peca'."""
        assert svc._clean_charge_item_name("R$ 99,99") == "Peca"

    def test_texto_limpo_passa_intacto(self):
        """Texto sem preço/emoji fica intacto."""
        assert svc._clean_charge_item_name("Vestido Floral") == "Vestido Floral"

    def test_remove_numero_sem_cifrao(self):
        """Remove número inteiro solto (sem símbolo de moeda)."""
        result = svc._clean_charge_item_name("Blusa 10 peças")
        # O número deve ser removido
        assert "10" not in result

    def test_todos_emojis_suportados(self):
        """Todos os emojis na lista são removidos."""
        assert svc._clean_charge_item_name("💕✨✅💰👇 Item") == "Item"


# ---------------------------------------------------------------------------
# _resolve_charge_rows
# ---------------------------------------------------------------------------

class TestResolveChargeRows:
    def test_usa_postgres_quando_disponivel(self, monkeypatch):
        """_resolve_charge_rows usa list_package_charges quando supabase habilitado."""
        calls = []
        monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: True)
        monkeypatch.setattr(
            "app.services.finance_service.list_package_charges",
            lambda pkg_id: calls.append(pkg_id) or [{"id": "charge-1"}],
        )
        result = svc._resolve_charge_rows({"source_package_id": "uuid-1"}, "uuid-1")
        assert calls == ["uuid-1"]
        assert result == [{"id": "charge-1"}]

    def test_fallback_finance_manager_quando_postgres_vazio(self, monkeypatch):
        """Quando Postgres retorna charges vazias, usa FinanceManager como fallback."""
        register_calls = []

        class FakeFinanceManager:
            def __init__(self):
                pass
            def register_package_confirmation(self, moved):
                register_calls.append(moved)
                return [{"id": "fm-charge"}]

        monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: True)
        monkeypatch.setattr("app.services.finance_service.list_package_charges", lambda pkg_id: [])
        monkeypatch.setattr(svc, "FinanceManager", FakeFinanceManager)

        moved = {"source_package_id": "uuid-2"}
        result = svc._resolve_charge_rows(moved, "uuid-2")
        assert register_calls == [moved]
        assert result == [{"id": "fm-charge"}]

    def test_sem_finance_manager_e_postgres_vazio(self, monkeypatch):
        """Sem FinanceManager e Postgres vazio, retorna []."""
        monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: True)
        monkeypatch.setattr("app.services.finance_service.list_package_charges", lambda pkg_id: [])
        monkeypatch.setattr(svc, "FinanceManager", None)
        result = svc._resolve_charge_rows({"source_package_id": "uuid-3"}, "uuid-3")
        assert result == []

    def test_domain_disabled_usa_finance_manager(self, monkeypatch):
        """Quando supabase_domain_enabled=False, pula Postgres e usa FinanceManager."""
        register_calls = []

        class FakeFinanceManager:
            def __init__(self):
                pass
            def register_package_confirmation(self, moved):
                register_calls.append(True)
                return [{"id": "fm-charge-2"}]

        monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: False)
        monkeypatch.setattr(svc, "FinanceManager", FakeFinanceManager)
        result = svc._resolve_charge_rows({"source_package_id": "uuid-4"}, "uuid-4")
        assert register_calls == [True]
        assert result == [{"id": "fm-charge-2"}]

    def test_sem_source_package_id_usa_pkg_id(self, monkeypatch):
        """Quando moved não tem source_package_id, usa pkg_id como fallback."""
        calls = []
        monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: True)
        monkeypatch.setattr(
            "app.services.finance_service.list_package_charges",
            lambda pkg_id: calls.append(pkg_id) or [{"id": "c"}],
        )
        svc._resolve_charge_rows({}, "fallback-pkg-id")
        assert calls == ["fallback-pkg-id"]


# ---------------------------------------------------------------------------
# run_post_confirmation_effects — caminhos principais
# ---------------------------------------------------------------------------

class TestRunPostConfirmationEffects:
    def _patch_base(self, monkeypatch):
        """Patches mínimos para o fluxo normal rodar sem I/O."""
        monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: True)
        monkeypatch.setattr("app.services.finance_service.list_package_charges", lambda pkg_id: [])
        monkeypatch.setattr(svc, "FinanceManager", None)
        monkeypatch.setattr(
            "app.services.confirmed_packages_service.add_confirmed_package",
            lambda *a, **kw: None,
        )
        # persist_metrics é importado no nível de módulo como alias local
        monkeypatch.setattr(svc, "persist_metrics", lambda *a, **kw: None)

    def test_moved_vazio_nao_executa(self, monkeypatch):
        """moved={} → retorna imediatamente sem efeitos."""
        add_calls = []
        monkeypatch.setattr(
            "app.services.confirmed_packages_service.add_confirmed_package",
            lambda *a, **kw: add_calls.append(True),
        )
        asyncio.run(svc.run_post_confirmation_effects({}, "pkg-x"))
        assert add_calls == []

    def test_moved_none_nao_executa(self, monkeypatch):
        """moved=None → retorna imediatamente."""
        add_calls = []
        monkeypatch.setattr(
            "app.services.confirmed_packages_service.add_confirmed_package",
            lambda *a, **kw: add_calls.append(True),
        )
        asyncio.run(svc.run_post_confirmation_effects(None, "pkg-x"))
        assert add_calls == []

    def test_pdf_ja_enviado_nao_seta_defaults(self, monkeypatch):
        """Pacote com pdf_status='sent' não sobrescreve defaults."""
        self._patch_base(monkeypatch)
        moved = _moved_base(pdf_status="sent")
        _run_sync(svc.run_post_confirmation_effects(moved, "source-pkg-1", persist_confirmed_package=False))
        # pdf_status não deve ser alterado para 'queued'
        assert moved["pdf_status"] == "sent"

    def test_sem_pdf_status_seta_queued(self, monkeypatch):
        """Pacote sem pdf_status recebe default 'queued'."""
        self._patch_base(monkeypatch)
        moved = _moved_base()  # sem pdf_status
        _run_sync(svc.run_post_confirmation_effects(moved, "source-pkg-1", persist_confirmed_package=False))
        assert moved["pdf_status"] == "queued"
        assert moved["pdf_attempts"] == 0
        assert moved.get("pdf_file_name") is None

    def test_persist_confirmed_package_true_chama_add(self, monkeypatch):
        """persist_confirmed_package=True chama add_confirmed_package."""
        self._patch_base(monkeypatch)
        add_calls = []
        monkeypatch.setattr(
            "app.services.confirmed_packages_service.add_confirmed_package",
            lambda pkg: add_calls.append(pkg),
        )
        moved = _moved_base(pdf_status="sent")
        _run_sync(
            svc.run_post_confirmation_effects(moved, "source-pkg-1", persist_confirmed_package=True)
        )
        assert len(add_calls) == 1

    def test_persist_confirmed_package_false_nao_chama_add(self, monkeypatch):
        """persist_confirmed_package=False não chama add_confirmed_package."""
        self._patch_base(monkeypatch)
        add_calls = []
        monkeypatch.setattr(
            "app.services.confirmed_packages_service.add_confirmed_package",
            lambda pkg: add_calls.append(pkg),
        )
        moved = _moved_base(pdf_status="sent")
        _run_sync(
            svc.run_post_confirmation_effects(moved, "source-pkg-1", persist_confirmed_package=False)
        )
        assert add_calls == []

    def test_metrics_data_none_nao_chama_persist(self, monkeypatch):
        """metrics_data_to_save=None → persist_metrics não é chamado."""
        self._patch_base(monkeypatch)
        persist_calls = []
        monkeypatch.setattr(svc, "persist_metrics", lambda data: persist_calls.append(data))
        moved = _moved_base(pdf_status="sent")
        _run_sync(
            svc.run_post_confirmation_effects(
                moved, "source-pkg-1",
                metrics_data_to_save=None,
                persist_confirmed_package=False,
            )
        )
        assert persist_calls == []

    def test_metrics_data_fornecido_chama_persist(self, monkeypatch):
        """metrics_data_to_save fornecido → persist_metrics é chamado."""
        self._patch_base(monkeypatch)
        persist_calls = []
        monkeypatch.setattr(svc, "persist_metrics", lambda data: persist_calls.append(data))
        moved = _moved_base(pdf_status="sent")
        metrics = {"votos": {}}
        _run_sync(
            svc.run_post_confirmation_effects(
                moved, "source-pkg-1",
                metrics_data_to_save=metrics,
                persist_confirmed_package=False,
            )
        )
        assert persist_calls == [metrics]

    def test_finance_exception_nao_propaga(self, monkeypatch):
        """Exceção no bloco finance_lock é capturada e não propaga."""
        monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: True)
        monkeypatch.setattr(
            "app.services.finance_service.list_package_charges",
            lambda pkg_id: (_ for _ in ()).throw(RuntimeError("DB caiu")),
        )
        monkeypatch.setattr(svc, "FinanceManager", None)
        monkeypatch.setattr(
            "app.services.confirmed_packages_service.add_confirmed_package",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(svc, "persist_metrics", lambda *a, **kw: None)
        moved = _moved_base(pdf_status="sent")
        # não deve levantar
        _run_sync(
            svc.run_post_confirmation_effects(moved, "source-pkg-1", persist_confirmed_package=False)
        )


# ---------------------------------------------------------------------------
# PDF worker — sucesso e falha
# ---------------------------------------------------------------------------

class TestPdfWorker:
    """Testa os dois ramos internos do _pdf_worker via run_post_confirmation_effects."""

    def _patch_no_finance(self, monkeypatch):
        monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: True)
        monkeypatch.setattr("app.services.finance_service.list_package_charges", lambda pkg_id: [])
        monkeypatch.setattr(svc, "FinanceManager", None)
        monkeypatch.setattr(
            "app.services.confirmed_packages_service.add_confirmed_package",
            lambda *a, **kw: None,
        )

    def _patch_common_pdf(self, monkeypatch, stub_pdf_modules, tmp_path, build_pdf_fn=None):
        """Aplica patches comuns para testes do PDF worker."""
        if build_pdf_fn is not None:
            stub_pdf_modules["estoque.pdf_builder"].build_pdf = build_pdf_fn
        stub_pdf_modules["finance.utils"].get_pdf_filename_by_id = lambda pkg_id: "pacote_test.pdf"
        monkeypatch.setattr("app.services.package_state_service.update_package_state", lambda *a, **kw: None)
        monkeypatch.setattr("app.config.settings", type("S", (), {"COMMISSION_PERCENT": 13.0})())
        monkeypatch.chdir(tmp_path)

    def test_pdf_worker_sucesso_grava_arquivo_e_persiste(self, monkeypatch, tmp_path, stub_pdf_modules):
        """PDF worker com sucesso grava arquivo em disco e persiste métricas."""
        self._patch_no_finance(monkeypatch)
        self._patch_common_pdf(monkeypatch, stub_pdf_modules, tmp_path, build_pdf_fn=lambda snap, pct: b"%PDF-fake")

        persist_calls = []
        update_calls = []
        monkeypatch.setattr(svc, "persist_metrics", lambda data: persist_calls.append(data))
        monkeypatch.setattr(
            "app.services.package_state_service.update_package_state",
            lambda pkg_id, updates: update_calls.append(updates),
        )

        metrics = {"votos": {}}
        moved = _moved_base()  # sem pdf_status → receberá 'queued'

        _run_sync(
            svc.run_post_confirmation_effects(
                moved, "source-pkg-1",
                metrics_data_to_save=metrics,
                persist_confirmed_package=False,
            )
        )
        # Worker fez update_package_state com pdf_status='sent'
        assert any(u.get("pdf_status") == "sent" for u in update_calls)
        # persist_metrics chamado: uma vez direto + uma pelo worker
        assert len(persist_calls) >= 2
        # Arquivo foi criado em disco
        pdf_path = tmp_path / "etiquetas_estoque" / "pacote_test.pdf"
        assert pdf_path.exists()

    def test_pdf_worker_falha_persiste_metricas(self, monkeypatch, tmp_path, stub_pdf_modules):
        """PDF worker que lança exceção persiste métricas com pdf_status='failed'."""
        self._patch_no_finance(monkeypatch)

        def _raise_pdf(snap, pct):
            raise RuntimeError("PDF gerou erro")

        self._patch_common_pdf(monkeypatch, stub_pdf_modules, tmp_path, build_pdf_fn=_raise_pdf)
        monkeypatch.setattr("app.services.package_state_service.update_package_state", lambda *a, **kw: None)

        persist_calls = []
        monkeypatch.setattr(svc, "persist_metrics", lambda data: persist_calls.append(data))

        metrics = {"votos": {}}
        moved = _moved_base()  # sem pdf_status → receberá 'queued'

        _run_sync(
            svc.run_post_confirmation_effects(
                moved, "source-pkg-1",
                metrics_data_to_save=metrics,
                persist_confirmed_package=False,
            )
        )
        # persist_metrics chamado pelo worker em caso de falha
        # (uma vez direto por metrics_data_to_save + uma no except do worker)
        assert len(persist_calls) >= 2

    def test_pdf_worker_update_state_exception_nao_propaga(self, monkeypatch, tmp_path, stub_pdf_modules):
        """Exceção em update_package_state dentro do worker não propaga; PDF é gerado."""
        self._patch_no_finance(monkeypatch)
        stub_pdf_modules["estoque.pdf_builder"].build_pdf = lambda snap, pct: b"%PDF-fake"
        stub_pdf_modules["finance.utils"].get_pdf_filename_by_id = lambda pkg_id: "pacote_test2.pdf"

        def _raise_update(pkg_id, updates):
            raise RuntimeError("state svc offline")

        monkeypatch.setattr("app.services.package_state_service.update_package_state", _raise_update)
        monkeypatch.setattr("app.config.settings", type("S", (), {"COMMISSION_PERCENT": 13.0})())
        monkeypatch.setattr(svc, "persist_metrics", lambda *a, **kw: None)
        monkeypatch.chdir(tmp_path)

        moved = _moved_base()
        # Não deve levantar
        _run_sync(
            svc.run_post_confirmation_effects(
                moved, "source-pkg-1",
                metrics_data_to_save=None,
                persist_confirmed_package=False,
            )
        )
        # PDF foi salvo mesmo com update_package_state falhando
        pdf_path = tmp_path / "etiquetas_estoque" / "pacote_test2.pdf"
        assert pdf_path.exists()
