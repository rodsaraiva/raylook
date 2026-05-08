"""Confirmed packages service — agora 100% Postgres (F-051).

Histórico: antes lia/escrevia de `confirmed_packages.json`. Esse JSON era
redundante — `pacotes` (status='approved') + `enquetes` + `pacote_clientes` +
`clientes` + `produtos` tinham exatamente os mesmos dados, mas normalizados.
O JSON só adicionava divergência e não era auditado pelo F-046/F-049.

Agora: toda leitura vem do Postgres via SupabaseRestClient. Os callers não
mudam de assinatura — mesmas funções públicas, mesmos retornos.

Funções de escrita (`add_confirmed_package`, `save_confirmed_packages`,
`remove_confirmed_package`) viraram no-ops: o Postgres já é atualizado
pelos callers antes de chamar essas funções. O JSON atuava como "segundo
write" redundante.
"""
import logging
from typing import Any, Dict, List, Optional

from app.services.supabase_service import SupabaseRestClient, supabase_domain_enabled

logger = logging.getLogger("raylook.confirmed_packages_service")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_package_item(row: Dict[str, Any]) -> Dict[str, Any]:
    """Transforma uma row de PostgREST (com embeds) no shape que o dash espera."""
    from metrics.processors import build_drive_image_url, resolve_enquete_drive_file_id

    enquete = row.get("enquete") or {}
    produto = enquete.get("produto") or {}
    poll_id = str(enquete.get("external_poll_id") or "").strip()

    seq_no = row.get("sequence_no")
    try:
        legacy_seq = max(int(seq_no) - 1, 0)
    except Exception:
        legacy_seq = 0
    package_uuid = str(row.get("id") or "")
    legacy_id = f"{poll_id}_{legacy_seq}" if poll_id else package_uuid

    # Votes (pacote_clientes join clientes)
    pc_rows = row.get("pacote_clientes") or []
    if not isinstance(pc_rows, list):
        pc_rows = [pc_rows] if pc_rows else []
    votes = []
    for pc in pc_rows:
        if not isinstance(pc, dict):
            continue
        cliente = pc.get("cliente") or {}
        votes.append({
            "name": cliente.get("nome") or "",
            "phone": cliente.get("celular") or "",
            "qty": int(pc.get("qty") or 0),
        })
    votes.sort(key=lambda v: v.get("qty", 0), reverse=True)

    opened_at = row.get("opened_at") or enquete.get("created_at_provider")

    return {
        "id": legacy_id,
        "source_package_id": package_uuid,
        "poll_id": poll_id,
        "poll_title": row.get("custom_title") or enquete.get("titulo") or poll_id,
        "chat_id": enquete.get("chat_id"),
        "image": build_drive_image_url(resolve_enquete_drive_file_id(enquete, produto)),
        "qty": int(row.get("total_qty") or 0),
        "opened_at": opened_at,
        "closed_at": row.get("closed_at"),
        "confirmed_at": row.get("approved_at"),
        "cancelled_at": row.get("cancelled_at"),
        "status": str(row.get("status") or "approved"),
        "tag": row.get("tag"),
        "pdf_status": row.get("pdf_status"),
        "pdf_file_name": row.get("pdf_file_name"),
        "pdf_sent_at": row.get("pdf_sent_at"),
        "pdf_attempts": int(row.get("pdf_attempts") or 0),
        "confirmed_by": row.get("confirmed_by"),
        "cancelled_by": row.get("cancelled_by"),
        "votes": votes,
    }


_APPROVED_SELECT = (
    "id,sequence_no,total_qty,status,"
    "opened_at,closed_at,approved_at,cancelled_at,updated_at,"
    "custom_title,tag,pdf_status,pdf_file_name,pdf_sent_at,pdf_attempts,"
    "confirmed_by,cancelled_by,"
    "enquete:enquete_id(titulo,external_poll_id,chat_id,created_at_provider,"
    "drive_file_id,"
    "produto:produto_id(drive_file_id)),"
    "pacote_clientes(qty,cliente:cliente_id(nome,celular))"
)


def _fetch_approved_packages(limit: int = 500) -> List[Dict[str, Any]]:
    """Busca pacotes approved do Postgres com todos os embeds necessários."""
    if not supabase_domain_enabled():
        return []
    sb = SupabaseRestClient.from_settings()
    try:
        rows = sb.select(
            "pacotes",
            columns=_APPROVED_SELECT,
            filters=[("status", "eq", "approved")],
            order="approved_at.desc",
            limit=limit,
        )
        return rows if isinstance(rows, list) else []
    except Exception:
        logger.exception("_fetch_approved_packages falhou")
        return []


def _fetch_package_by_uuid(uuid: str) -> Optional[Dict[str, Any]]:
    """Busca um pacote específico por UUID."""
    if not supabase_domain_enabled():
        return None
    sb = SupabaseRestClient.from_settings()
    try:
        rows = sb.select(
            "pacotes",
            columns=_APPROVED_SELECT,
            filters=[("id", "eq", uuid)],
            limit=1,
        )
        if isinstance(rows, list) and rows:
            return rows[0]
    except Exception:
        logger.exception("_fetch_package_by_uuid(%s) falhou", uuid)
    return None


# ---------------------------------------------------------------------------
# Public API — assinaturas preservadas pra compatibilidade
# ---------------------------------------------------------------------------

def load_confirmed_packages() -> List[Dict[str, Any]]:
    """Carrega todos os pacotes confirmados do Postgres."""
    raw = _fetch_approved_packages()
    return [_build_package_item(r) for r in raw]


def save_confirmed_packages(packages: List[Dict[str, Any]]) -> None:
    """No-op — F-051: dados vivem no Postgres, escritas acontecem nos callers."""
    logger.debug("save_confirmed_packages() chamado — no-op (F-051, dados no Postgres)")


def add_confirmed_package(package: Dict[str, Any]) -> None:
    """No-op — F-051: o caller já atualiza o Postgres antes de chamar isso."""
    logger.debug(
        "add_confirmed_package(%s) chamado — no-op (F-051)",
        package.get("id") or package.get("source_package_id"),
    )


def get_confirmed_package(pkg_id: str) -> Optional[Dict[str, Any]]:
    """Busca um pacote confirmado pelo ID (UUID ou legacy `pollId_seq`)."""
    if not supabase_domain_enabled():
        return None

    # Tenta UUID direto
    import re
    uuid_pattern = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
    )
    if uuid_pattern.match(str(pkg_id).strip()):
        row = _fetch_package_by_uuid(pkg_id)
        if row:
            return _build_package_item(row)
        return None

    # Legacy ID: resolve pra UUID via _resolve_supabase_package_id
    try:
        from metrics.supabase_clients import resolve_supabase_package_id
        uuid = resolve_supabase_package_id(pkg_id)
        if uuid:
            row = _fetch_package_by_uuid(uuid)
            if row:
                return _build_package_item(row)
    except Exception:
        logger.warning("get_confirmed_package: resolve falhou pra %s", pkg_id)

    # Fallback: busca em todos os approved e procura match por legacy_id
    all_pkgs = load_confirmed_packages()
    for p in all_pkgs:
        if p.get("id") == pkg_id or p.get("source_package_id") == pkg_id:
            return p

    return None


def remove_confirmed_package(pkg_id: str) -> Optional[Dict[str, Any]]:
    """No-op — F-051: remoção se dá via UPDATE pacotes SET status='cancelled'
    diretamente no Postgres (o caller já faz isso)."""
    logger.debug("remove_confirmed_package(%s) chamado — no-op (F-051)", pkg_id)
    return None


def merge_confirmed_into_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """F-051: o confirmed_today já é preenchido por fetch_package_lists_for_metrics()
    diretamente do Postgres. Essa função agora só preenche o sumário e remove
    duplicatas entre seções, sem sobrescrever dados do DB com stale JSON.
    """
    from metrics import processors

    pkgs = metrics.setdefault("votos", {}).setdefault("packages", {})
    confirmed_today = pkgs.get("confirmed_today", [])
    if not isinstance(confirmed_today, list):
        confirmed_today = []

    # Remove de closed_today/rejected_today qualquer pacote que já esteja confirmado
    confirmed_ids = set()
    for package in confirmed_today:
        identity = str(
            package.get("source_package_id")
            or package.get("id")
            or package.get("poll_id")
            or ""
        ).strip()
        if identity:
            confirmed_ids.add(identity)

    for section in ["closed_today", "rejected_today"]:
        current_list = pkgs.get(section, [])
        if isinstance(current_list, list):
            pkgs[section] = [
                p for p in current_list
                if isinstance(p, dict) and str(
                    p.get("source_package_id") or p.get("id") or p.get("poll_id") or ""
                ).strip() not in confirmed_ids
            ]

    # Sumário
    try:
        summary = metrics["votos"].setdefault("packages_summary_confirmed", {})
        summary["today"] = len(confirmed_today)
    except Exception as e:
        logger.warning("Erro ao calcular sumário de confirmados: %s", e)

    return metrics
