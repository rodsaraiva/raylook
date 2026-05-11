"""Testes para confirmed_packages_service.

Cobre: _build_package_item, _fetch_approved_packages, _fetch_package_by_uuid,
load_confirmed_packages, get_confirmed_package (UUID, legacy, fallback, domain disabled),
save_confirmed_packages (no-op), add_confirmed_package (no-op),
remove_confirmed_package (no-op), merge_confirmed_into_metrics.
"""
import sys
import types
from datetime import datetime, timedelta

import pytest

from app.services import confirmed_packages_service as svc
from metrics import processors


@pytest.fixture
def mock_metrics_supabase_clients():
    """Injeta um módulo stub para metrics.supabase_clients que contém
    resolve_supabase_package_id (ausente no módulo real).
    Restaura o módulo original ao fim do teste.
    """
    original = sys.modules.get("metrics.supabase_clients")
    stub = types.ModuleType("metrics.supabase_clients")
    stub.resolve_supabase_package_id = lambda pkg_id: None
    sys.modules["metrics.supabase_clients"] = stub
    yield stub
    if original is None:
        sys.modules.pop("metrics.supabase_clients", None)
    else:
        sys.modules["metrics.supabase_clients"] = original


# ---------------------------------------------------------------------------
# Helpers de fixture
# ---------------------------------------------------------------------------

def _row_approved(
    pkg_id="aaaaaaaa-0001-0001-0001-000000000001",
    poll_id="poll-001",
    title="Roupa Vermelha",
    seq=2,
    qty=10,
    drive_file_id="file-abc",
    status="approved",
    votes=None,
):
    """Monta uma row no formato que PostgREST retornaria (com embeds)."""
    if votes is None:
        votes = [
            {"qty": 6, "cliente": {"nome": "Ana", "celular": "5511999990001"}},
            {"qty": 4, "cliente": {"nome": "Bia", "celular": "5511999990002"}},
        ]
    return {
        "id": pkg_id,
        "sequence_no": seq,
        "total_qty": qty,
        "status": status,
        "opened_at": "2026-04-01T10:00:00Z",
        "closed_at": "2026-04-06T12:00:00Z",
        "approved_at": "2026-04-06T13:00:00Z",
        "cancelled_at": None,
        "updated_at": "2026-04-06T13:00:00Z",
        "custom_title": title,
        "tag": "promo",
        "pdf_status": None,
        "pdf_file_name": None,
        "pdf_sent_at": None,
        "pdf_attempts": 0,
        "confirmed_by": "gerente1",
        "cancelled_by": None,
        "enquete": {
            "titulo": title,
            "external_poll_id": poll_id,
            "chat_id": "chat-001",
            "created_at_provider": "2026-04-01T10:00:00Z",
            "drive_file_id": drive_file_id,
            "produto": {"drive_file_id": "prod-file-001"},
        },
        "pacote_clientes": votes,
    }


def _base_metrics():
    return {
        "votos": {
            "packages": {
                "open": [],
                "closed_today": [
                    {"id": "legacy-closed"},
                    {"id": "db-approved", "source_package_id": "pkg-db"},
                ],
                "confirmed_today": [
                    {
                        "id": "db-approved",
                        "source_package_id": "pkg-db",
                        "confirmed_at": "2026-04-06T13:42:29+00:00",
                    }
                ],
                "rejected_today": [],
            },
            "packages_summary_confirmed": {
                "today": 1,
                "yesterday": 0,
                "last_7_days": [1, 0, 0, 0, 0, 0, 0],
                "avg_7_days": 0.14,
                "same_weekday_last_week": 0,
            },
        }
    }


# ---------------------------------------------------------------------------
# _build_package_item
# ---------------------------------------------------------------------------

class TestBuildPackageItem:
    def test_shape_basico(self):
        """Campos obrigatórios do shape de saída estão presentes."""
        row = _row_approved()
        item = svc._build_package_item(row)
        assert item["id"] == "poll-001_1"  # seq 2 → legacy 1
        assert item["source_package_id"] == "aaaaaaaa-0001-0001-0001-000000000001"
        assert item["poll_id"] == "poll-001"
        assert item["qty"] == 10
        assert item["status"] == "approved"

    def test_votes_ordenados_desc(self):
        """Votos devem vir ordenados por qty decrescente."""
        row = _row_approved()
        item = svc._build_package_item(row)
        qtys = [v["qty"] for v in item["votes"]]
        assert qtys == sorted(qtys, reverse=True)

    def test_votes_shape(self):
        """Cada voto tem name, phone e qty."""
        row = _row_approved()
        item = svc._build_package_item(row)
        for v in item["votes"]:
            assert "name" in v
            assert "phone" in v
            assert "qty" in v

    def test_image_via_enquete_drive_file_id(self):
        """Imagem é montada a partir do drive_file_id da enquete."""
        row = _row_approved(drive_file_id="drive-img-001")
        item = svc._build_package_item(row)
        assert item["image"] == "/files/drive-img-001"

    def test_image_none_sem_drive_file(self):
        """Sem drive_file_id na enquete nem no produto → image é None."""
        row = _row_approved(drive_file_id="")
        row["enquete"]["produto"] = {}
        item = svc._build_package_item(row)
        assert item["image"] is None

    def test_legacy_id_sem_poll_id(self):
        """Sem poll_id, legacy_id cai para o UUID do pacote."""
        row = _row_approved()
        row["enquete"]["external_poll_id"] = ""
        item = svc._build_package_item(row)
        assert item["id"] == "aaaaaaaa-0001-0001-0001-000000000001"

    def test_seq_no_invalido(self):
        """sequence_no inválido usa 0 como fallback."""
        row = _row_approved(seq="nao-um-numero")
        item = svc._build_package_item(row)
        assert item["id"] == "poll-001_0"

    def test_seq_zero_fica_zero(self):
        """sequence_no = 0 ou 1 → legacy 0 (max(int-1, 0))."""
        row = _row_approved(seq=1)
        item = svc._build_package_item(row)
        assert item["id"] == "poll-001_0"

    def test_opened_at_fallback_enquete(self):
        """opened_at usa created_at_provider da enquete se row.opened_at for None."""
        row = _row_approved()
        row["opened_at"] = None
        item = svc._build_package_item(row)
        assert item["opened_at"] == "2026-04-01T10:00:00Z"

    def test_pacote_clientes_nao_lista(self):
        """pacote_clientes como dict único (não lista) não deve quebrar."""
        row = _row_approved(
            votes={"qty": 3, "cliente": {"nome": "Carlos", "celular": "5511000000001"}}
        )
        item = svc._build_package_item(row)
        assert len(item["votes"]) == 1
        assert item["votes"][0]["name"] == "Carlos"

    def test_pacote_clientes_vazio(self):
        """pacote_clientes vazio → votes = []."""
        row = _row_approved(votes=[])
        item = svc._build_package_item(row)
        assert item["votes"] == []

    def test_pacote_clientes_none(self):
        """pacote_clientes None → votes = []."""
        row = _row_approved(votes=None)
        row["pacote_clientes"] = None
        item = svc._build_package_item(row)
        assert item["votes"] == []

    def test_custom_title_tem_prioridade(self):
        """custom_title tem precedência sobre enquete.titulo."""
        row = _row_approved(title="Meu Título Custom")
        row["enquete"]["titulo"] = "Título da enquete"
        item = svc._build_package_item(row)
        assert item["poll_title"] == "Meu Título Custom"

    def test_campos_pdf(self):
        """Campos de PDF são passados corretamente."""
        row = _row_approved()
        row["pdf_status"] = "sent"
        row["pdf_file_name"] = "pacote_001.pdf"
        row["pdf_sent_at"] = "2026-04-06T14:00:00Z"
        row["pdf_attempts"] = 2
        item = svc._build_package_item(row)
        assert item["pdf_status"] == "sent"
        assert item["pdf_file_name"] == "pacote_001.pdf"
        assert item["pdf_attempts"] == 2


# ---------------------------------------------------------------------------
# _fetch_approved_packages / _fetch_package_by_uuid
# ---------------------------------------------------------------------------

class TestFetchHelpers:
    def test_fetch_approved_retorna_lista(self, monkeypatch):
        """_fetch_approved_packages retorna lista quando supabase habilitado."""
        row = _row_approved()
        fake_sb = type("Fake", (), {
            "select": lambda self, *a, **kw: [row],
        })()
        monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: True)
        monkeypatch.setattr(
            "app.services.confirmed_packages_service.SupabaseRestClient.from_settings",
            staticmethod(lambda: fake_sb),
        )
        result = svc._fetch_approved_packages()
        assert result == [row]

    def test_fetch_approved_domain_disabled(self, monkeypatch):
        """_fetch_approved_packages retorna [] quando domínio desabilitado."""
        monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: False)
        assert svc._fetch_approved_packages() == []

    def test_fetch_approved_exception_retorna_vazio(self, monkeypatch):
        """Exceção no select → retorna [] sem propagar."""
        fake_sb = type("Fake", (), {
            "select": lambda self, *a, **kw: (_ for _ in ()).throw(RuntimeError("DB offline")),
        })()
        monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: True)
        monkeypatch.setattr(
            "app.services.confirmed_packages_service.SupabaseRestClient.from_settings",
            staticmethod(lambda: fake_sb),
        )
        assert svc._fetch_approved_packages() == []

    def test_fetch_approved_retorna_nao_lista(self, monkeypatch):
        """select() retornando não-lista → retorna []."""
        fake_sb = type("Fake", (), {
            "select": lambda self, *a, **kw: {"erro": "nao esperado"},
        })()
        monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: True)
        monkeypatch.setattr(
            "app.services.confirmed_packages_service.SupabaseRestClient.from_settings",
            staticmethod(lambda: fake_sb),
        )
        assert svc._fetch_approved_packages() == []

    def test_fetch_by_uuid_retorna_row(self, monkeypatch):
        """_fetch_package_by_uuid retorna row quando encontrado."""
        row = _row_approved()
        fake_sb = type("Fake", (), {
            "select": lambda self, *a, **kw: [row],
        })()
        monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: True)
        monkeypatch.setattr(
            "app.services.confirmed_packages_service.SupabaseRestClient.from_settings",
            staticmethod(lambda: fake_sb),
        )
        result = svc._fetch_package_by_uuid("aaaaaaaa-0001-0001-0001-000000000001")
        assert result == row

    def test_fetch_by_uuid_domain_disabled(self, monkeypatch):
        """_fetch_package_by_uuid retorna None quando domínio desabilitado."""
        monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: False)
        assert svc._fetch_package_by_uuid("aaaaaaaa-0001-0001-0001-000000000001") is None

    def test_fetch_by_uuid_nao_encontrado(self, monkeypatch):
        """_fetch_package_by_uuid retorna None quando select retorna lista vazia."""
        fake_sb = type("Fake", (), {
            "select": lambda self, *a, **kw: [],
        })()
        monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: True)
        monkeypatch.setattr(
            "app.services.confirmed_packages_service.SupabaseRestClient.from_settings",
            staticmethod(lambda: fake_sb),
        )
        assert svc._fetch_package_by_uuid("aaaaaaaa-0001-0001-0001-000000000001") is None

    def test_fetch_by_uuid_exception(self, monkeypatch):
        """Exceção no select → retorna None sem propagar."""
        fake_sb = type("Fake", (), {
            "select": lambda self, *a, **kw: (_ for _ in ()).throw(RuntimeError("falhou")),
        })()
        monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: True)
        monkeypatch.setattr(
            "app.services.confirmed_packages_service.SupabaseRestClient.from_settings",
            staticmethod(lambda: fake_sb),
        )
        assert svc._fetch_package_by_uuid("aaaaaaaa-0001-0001-0001-000000000001") is None


# ---------------------------------------------------------------------------
# load_confirmed_packages
# ---------------------------------------------------------------------------

class TestLoadConfirmedPackages:
    def test_retorna_lista_de_items(self, monkeypatch):
        """load_confirmed_packages converte rows em itens formatados."""
        row = _row_approved()
        monkeypatch.setattr(svc, "_fetch_approved_packages", lambda limit=500: [row])
        result = svc.load_confirmed_packages()
        assert len(result) == 1
        assert result[0]["source_package_id"] == row["id"]

    def test_retorna_vazio_quando_sem_rows(self, monkeypatch):
        """load_confirmed_packages retorna [] quando não há rows."""
        monkeypatch.setattr(svc, "_fetch_approved_packages", lambda limit=500: [])
        assert svc.load_confirmed_packages() == []


# ---------------------------------------------------------------------------
# get_confirmed_package
# ---------------------------------------------------------------------------

class TestGetConfirmedPackage:
    def test_por_uuid_encontrado(self, monkeypatch):
        """get_confirmed_package localiza pacote por UUID direto."""
        row = _row_approved()
        monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: True)
        monkeypatch.setattr(svc, "_fetch_package_by_uuid", lambda uuid: row)
        result = svc.get_confirmed_package("aaaaaaaa-0001-0001-0001-000000000001")
        assert result is not None
        assert result["source_package_id"] == row["id"]

    def test_por_uuid_nao_encontrado(self, monkeypatch):
        """get_confirmed_package retorna None quando UUID não existe."""
        monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: True)
        monkeypatch.setattr(svc, "_fetch_package_by_uuid", lambda uuid: None)
        assert svc.get_confirmed_package("aaaaaaaa-0001-0001-0001-000000000001") is None

    def test_domain_disabled_retorna_none(self, monkeypatch):
        """get_confirmed_package retorna None com supabase_domain_enabled=False."""
        monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: False)
        assert svc.get_confirmed_package("poll-001_0") is None

    def test_legacy_id_via_resolve(self, monkeypatch, mock_metrics_supabase_clients):
        """get_confirmed_package usa resolve_supabase_package_id para legacy IDs."""
        row = _row_approved()
        target_uuid = "aaaaaaaa-0001-0001-0001-000000000001"
        monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: True)
        mock_metrics_supabase_clients.resolve_supabase_package_id = lambda pkg_id: target_uuid
        monkeypatch.setattr(svc, "_fetch_package_by_uuid", lambda uuid: row)
        result = svc.get_confirmed_package("poll-001_1")
        assert result is not None
        assert result["source_package_id"] == target_uuid

    def test_legacy_id_resolve_retorna_none_vai_para_fallback(self, monkeypatch, mock_metrics_supabase_clients):
        """Quando resolve retorna None, cai no fallback de busca em todos os approved."""
        row = _row_approved()
        built_item = svc._build_package_item(row)
        monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: True)
        mock_metrics_supabase_clients.resolve_supabase_package_id = lambda pkg_id: None
        monkeypatch.setattr(svc, "load_confirmed_packages", lambda: [built_item])
        # id do item é "poll-001_1" (seq=2 → legacy=1)
        result = svc.get_confirmed_package("poll-001_1")
        assert result is not None
        assert result["source_package_id"] == "aaaaaaaa-0001-0001-0001-000000000001"

    def test_legacy_id_resolve_exception_vai_para_fallback(self, monkeypatch, mock_metrics_supabase_clients):
        """Exceção em resolve_supabase_package_id não propaga — usa fallback."""
        row = _row_approved()
        built_item = svc._build_package_item(row)
        monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: True)

        def _raise(pkg_id):
            raise RuntimeError("falhou")

        mock_metrics_supabase_clients.resolve_supabase_package_id = _raise
        monkeypatch.setattr(svc, "load_confirmed_packages", lambda: [built_item])
        result = svc.get_confirmed_package("poll-001_1")
        assert result is not None

    def test_fallback_por_source_package_id(self, monkeypatch, mock_metrics_supabase_clients):
        """Fallback aceita match por source_package_id quando resolve retorna None."""
        uuid = "aaaaaaaa-0001-0001-0001-000000000001"
        item = {"id": "poll-001_1", "source_package_id": uuid}
        monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: True)
        mock_metrics_supabase_clients.resolve_supabase_package_id = lambda pkg_id: None
        monkeypatch.setattr(svc, "load_confirmed_packages", lambda: [item])
        monkeypatch.setattr(svc, "_fetch_package_by_uuid", lambda u: None)
        # legacy id matcha source_package_id
        result = svc.get_confirmed_package("poll-001_1")
        assert result is not None
        assert result["source_package_id"] == uuid

    def test_nao_encontrado_retorna_none(self, monkeypatch, mock_metrics_supabase_clients):
        """get_confirmed_package retorna None quando nenhuma estratégia encontra."""
        monkeypatch.setattr("app.services.supabase_service.supabase_domain_enabled", lambda: True)
        mock_metrics_supabase_clients.resolve_supabase_package_id = lambda pkg_id: None
        monkeypatch.setattr(svc, "load_confirmed_packages", lambda: [])
        assert svc.get_confirmed_package("id-inexistente") is None


# ---------------------------------------------------------------------------
# No-ops: save, add, remove
# ---------------------------------------------------------------------------

class TestNoOps:
    def test_save_confirmed_packages_nao_levanta(self):
        """save_confirmed_packages é no-op e não levanta exceção."""
        svc.save_confirmed_packages([{"id": "x"}])  # deve silenciar

    def test_add_confirmed_package_nao_levanta(self):
        """add_confirmed_package é no-op e não levanta exceção."""
        svc.add_confirmed_package({"id": "x", "source_package_id": "uuid-x"})

    def test_add_confirmed_package_sem_id(self):
        """add_confirmed_package não quebra mesmo sem campo id."""
        svc.add_confirmed_package({})

    def test_remove_confirmed_package_retorna_none(self):
        """remove_confirmed_package é no-op e retorna None."""
        assert svc.remove_confirmed_package("pkg-123") is None


# ---------------------------------------------------------------------------
# merge_confirmed_into_metrics
# ---------------------------------------------------------------------------

class TestMergeConfirmedIntoMetrics:
    def test_preserves_existing_postgres_packages(self, monkeypatch):
        """F-051: merge não injeta pacotes do JSON — apenas remove duplicatas e atualiza sumário."""
        now = datetime(2026, 4, 6, 12, 0, 0)
        fake_dates = {
            "now": now,
            "today_start": now.replace(hour=0, minute=0, second=0, microsecond=0),
            "yesterday_start": now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1),
            "yesterday_end": now.replace(hour=0, minute=0, second=0, microsecond=0),
            "week_start": now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=7),
            "day24h_start": now - timedelta(hours=24),
        }
        monkeypatch.setattr(processors, "get_date_range", lambda: fake_dates)

        merged = svc.merge_confirmed_into_metrics(_base_metrics())
        confirmed = merged["votos"]["packages"]["confirmed_today"]
        assert [row["source_package_id"] for row in confirmed] == ["pkg-db"]
        assert all(row.get("id") != "db-approved" for row in merged["votos"]["packages"]["closed_today"])
        assert merged["votos"]["packages_summary_confirmed"]["today"] == 1

    def test_cria_chaves_ausentes(self):
        """merge funciona com metrics sem as chaves votos/packages."""
        result = svc.merge_confirmed_into_metrics({})
        assert "votos" in result
        assert "packages" in result["votos"]
        assert result["votos"]["packages_summary_confirmed"]["today"] == 0

    def test_confirmed_today_nao_lista(self):
        """confirmed_today não sendo lista → tratado como []."""
        metrics = {
            "votos": {
                "packages": {"confirmed_today": "invalido"},
            }
        }
        result = svc.merge_confirmed_into_metrics(metrics)
        assert result["votos"]["packages_summary_confirmed"]["today"] == 0

    def test_remove_duplicatas_de_rejected_today(self):
        """Pacotes em confirmed_today são removidos de rejected_today também."""
        metrics = {
            "votos": {
                "packages": {
                    "confirmed_today": [{"id": "rej-pkg", "source_package_id": "uuid-rej"}],
                    "rejected_today": [
                        {"id": "rej-pkg", "source_package_id": "uuid-rej"},
                        {"id": "outro", "source_package_id": "uuid-outro"},
                    ],
                    "closed_today": [],
                }
            }
        }
        result = svc.merge_confirmed_into_metrics(metrics)
        rejected = result["votos"]["packages"]["rejected_today"]
        assert all(r.get("source_package_id") != "uuid-rej" for r in rejected)
        assert any(r.get("source_package_id") == "uuid-outro" for r in rejected)

    def test_multiplos_confirmados_sumario(self):
        """Sumário reflete a quantidade correta de confirmed_today."""
        metrics = {
            "votos": {
                "packages": {
                    "confirmed_today": [
                        {"id": "p1", "source_package_id": "uuid-1"},
                        {"id": "p2", "source_package_id": "uuid-2"},
                        {"id": "p3", "source_package_id": "uuid-3"},
                    ],
                    "closed_today": [],
                    "rejected_today": [],
                }
            }
        }
        result = svc.merge_confirmed_into_metrics(metrics)
        assert result["votos"]["packages_summary_confirmed"]["today"] == 3

    def test_identity_via_poll_id(self):
        """Remoção de duplicatas funciona quando a identidade vem de poll_id."""
        metrics = {
            "votos": {
                "packages": {
                    "confirmed_today": [{"poll_id": "poll-x"}],
                    "closed_today": [{"poll_id": "poll-x"}, {"id": "outro"}],
                    "rejected_today": [],
                }
            }
        }
        result = svc.merge_confirmed_into_metrics(metrics)
        closed = result["votos"]["packages"]["closed_today"]
        assert all(r.get("poll_id") != "poll-x" for r in closed)

    def test_nao_remove_sem_identity(self):
        """Pacotes sem id/source_package_id/poll_id não são removidos por engano."""
        metrics = {
            "votos": {
                "packages": {
                    "confirmed_today": [{"id": "c1", "source_package_id": "uuid-c1"}],
                    "closed_today": [{"id": "sem-identidade"}],
                    "rejected_today": [],
                }
            }
        }
        result = svc.merge_confirmed_into_metrics(metrics)
        # "sem-identidade" não deve ser removido
        assert any(r.get("id") == "sem-identidade" for r in result["votos"]["packages"]["closed_today"])
