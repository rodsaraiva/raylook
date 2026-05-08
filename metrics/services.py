from datetime import datetime
from typing import Dict, Any, Iterable, Optional
import os
from . import clients, processors, supabase_clients
from app.config import settings
from app.services.supabase_service import supabase_domain_enabled
import logging
import time

logger = logging.getLogger("raylook.services")
try:
    from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
    prometheus_available = True
    METRICS_COUNTER = Counter("generate_metrics_runs_total", "Total generate_metrics runs")
    METRICS_DURATION = Histogram("generate_metrics_duration_seconds", "Duration of generate_metrics in seconds")
except Exception:
    prometheus_available = False
    METRICS_COUNTER = None
    METRICS_DURATION = None


def _rankings_from_approved_packages(hours: int = 24 * 7, since_today: bool = False, statuses=("approved",), date_field: str = "approved_at"):
    """Retorna (ranking_clientes, ranking_enquetes) baseado em peças em pacotes no período.

    Params:
      hours: janela (se since_today=False)
      since_today: usa 00:00 BRT como início (ignora 'hours')
      statuses: lista de status de pacotes a considerar (default: só approved)
      date_field: campo de data pra filtrar (approved_at, closed_at, opened_at)

    ranking_clientes: {phone: {name, phone, qty}}
    ranking_enquetes: {poll_id: {title, image, qty, package_count}}
    """
    empty = ({}, {})
    if not supabase_domain_enabled():
        return empty
    from datetime import datetime, timedelta, timezone
    from app.services.supabase_service import SupabaseRestClient
    sb = SupabaseRestClient.from_settings()

    if since_today:
        # BRT = UTC-3. Início do dia em BRT = 03:00 UTC do dia atual
        now_utc = datetime.now(timezone.utc)
        brt_offset = timedelta(hours=-3)
        now_brt = now_utc + brt_offset
        start_brt = now_brt.replace(hour=0, minute=0, second=0, microsecond=0)
        since = (start_brt - brt_offset).isoformat()
    else:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    # 1) Pacotes no período, com enquete e produto (pra imagem)
    status_filter = ("status", "eq", statuses[0]) if len(statuses) == 1 else ("status", "in", list(statuses))
    pkgs = sb.select_all(
        "pacotes",
        columns=(
            "id,enquete_id,"
            "enquete:enquete_id(id,external_poll_id,titulo,drive_file_id,"
            "produto:produto_id(drive_file_id))"
        ),
        filters=[
            status_filter,
            (date_field, "gte", since),
        ],
    )
    if not pkgs:
        return empty

    # mapa pacote_id -> enquete info (incluindo imagem)
    pkg_to_enquete = {}
    for p in pkgs:
        enq = p.get("enquete") or {}
        produto = enq.get("produto") or {}
        # F-061: imagem da enquete é prioridade sobre a do produto
        drive_id = enq.get("drive_file_id") or produto.get("drive_file_id")
        image_url = f"https://lh3.googleusercontent.com/d/{drive_id}" if drive_id else ""
        pkg_to_enquete[str(p["id"])] = {
            "id": str(enq.get("id") or ""),
            "external_poll_id": str(enq.get("external_poll_id") or ""),
            "title": enq.get("titulo") or "Enquete",
            "image": image_url,
        }

    pkg_ids = list(pkg_to_enquete.keys())

    # 2) pacote_clientes dos pacotes approved
    pc_rows = []
    for i in range(0, len(pkg_ids), 200):
        batch = pkg_ids[i:i+200]
        rows = sb.select_all(
            "pacote_clientes",
            columns="pacote_id,cliente_id,qty,cliente:cliente_id(nome,celular)",
            filters=[("pacote_id", "in", batch)],
        )
        if rows:
            pc_rows.extend(rows)

    # 3) Agrupa por cliente e por enquete
    customers: Dict[str, Dict[str, Any]] = {}
    polls: Dict[str, Dict[str, Any]] = {}
    poll_pkg_counter: Dict[str, set] = {}

    for row in pc_rows:
        qty = int(row.get("qty") or 0)
        if qty <= 0:
            continue
        # Clientes
        cliente = row.get("cliente") or {}
        phone = str(cliente.get("celular") or "").strip()
        if phone:
            name = cliente.get("nome") or "Cliente"
            if phone not in customers:
                customers[phone] = {"name": name, "phone": phone, "qty": 0}
            customers[phone]["qty"] += qty
        # Enquetes
        pkg_id = str(row.get("pacote_id") or "")
        enq_info = pkg_to_enquete.get(pkg_id)
        if enq_info:
            poll_key = enq_info.get("external_poll_id") or enq_info.get("id")
            if not poll_key:
                continue
            if poll_key not in polls:
                polls[poll_key] = {
                    "title": enq_info["title"],
                    "image": enq_info.get("image", ""),
                    "qty": 0,
                    "package_count": 0,
                }
                poll_pkg_counter[poll_key] = set()
            polls[poll_key]["qty"] += qty
            poll_pkg_counter[poll_key].add(pkg_id)

    # Consolidar contagem de pacotes por enquete
    for poll_key, pkg_set in poll_pkg_counter.items():
        polls[poll_key]["package_count"] = len(pkg_set)

    return customers, polls


def _metrics_min_datetime() -> Optional[datetime]:
    raw = str(getattr(settings, "METRICS_MIN_DATE", "") or "").strip()
    if not raw:
        return None
    parsed = processors.parse_timestamp(raw)
    if parsed is None:
        logger.warning("Ignoring invalid METRICS_MIN_DATE=%s", raw)
        return None
    return parsed


def _filter_rows_since(
    rows: Iterable[Dict[str, Any]],
    *timestamp_keys: str,
) -> list[Dict[str, Any]]:
    floor = _metrics_min_datetime()
    if floor is None:
        return list(rows)

    filtered: list[Dict[str, Any]] = []
    for row in rows:
        ts = None
        for key in timestamp_keys:
            ts = processors.parse_timestamp(row.get(key))
            if ts is not None:
                break
        if ts is None:
            continue
        if ts >= floor:
            filtered.append(row)
    return filtered

def generate_metrics() -> Dict[str, Any]:
    """High level function that fetches rows and returns dashboard data."""
    start = time.time()
    logger.info("generate_metrics: start")
    if prometheus_available and METRICS_COUNTER:
        METRICS_COUNTER.inc()

    source = str(getattr(settings, "METRICS_SOURCE", "baserow") or "baserow").strip().lower()
    if bool(getattr(settings, "TEST_MODE", False)):
        if not supabase_domain_enabled():
            raise RuntimeError("Staging test mode requires Supabase domain enabled.")
        source = "supabase"

    if source == "supabase":
        if not supabase_domain_enabled():
            raise RuntimeError("METRICS_SOURCE=supabase but SUPABASE_DOMAIN_ENABLED is false.")
        data = _generate_metrics_from_supabase()
        elapsed = (time.time() - start)
        if prometheus_available and METRICS_DURATION:
            METRICS_DURATION.observe(elapsed)
        logger.info("generate_metrics[supabase]: done in %dms", int(elapsed * 1000))
        return data

    table_enquetes = os.getenv("BASEROW_TABLE_ENQUETES", settings.BASEROW_TABLE_ENQUETES)
    table_votos = os.getenv("BASEROW_TABLE_VOTOS", settings.BASEROW_TABLE_VOTOS)

    enquetes = _filter_rows_since(
        clients.fetch_all_rows(table_enquetes),
        "createdAtTs",
        "field_171",
    )
    votos = _filter_rows_since(
        clients.fetch_all_rows(table_votos),
        "timestamp",
        "field_166",
    )

    # Build enquetes_map (map poll_id -> title) and created timestamps
    enquetes_map = {}
    enquetes_created = {}
    for e in enquetes:
        poll_id_raw = e.get("pollId", e.get("field_169"))
        poll_id = str(poll_id_raw).strip() if poll_id_raw else ""
        title = e.get("title", e.get("field_173", ""))
        drive_file_id = e.get("driveFileId", e.get("field_200"))
        chat_id_raw = e.get("chatId", e.get("field_170"))
        chat_id = str(chat_id_raw).strip() if chat_id_raw else None

        if poll_id:
            # Map poll_id to a dict containing title and drive_file_id
            enquetes_map[poll_id] = {
                "title": title,
                "drive_file_id": drive_file_id,
                "chat_id": chat_id,
            }
            # capture creation timestamp (if present) for filtering packages
            created_ts = processors.parse_timestamp(e.get("createdAtTs", e.get("field_171")))
            if created_ts:
                enquetes_created[poll_id] = created_ts

    dates = processors.get_date_range()
    enquete_metrics = processors.analyze_enquetes(enquetes, dates)
    # Pass enquetes_created so analyze_votos can filter packages by poll creation time.
    voto_metrics = processors.analyze_votos(votos, dates, enquetes_map, enquetes_created)

    dashboard_data = {
        "generated_at": dates["now"].isoformat(),
        "enquetes": enquete_metrics,
        "votos": voto_metrics,
    }
    elapsed = (time.time() - start)
    elapsed_ms = int(elapsed * 1000)
    if prometheus_available and METRICS_DURATION:
        METRICS_DURATION.observe(elapsed)
    logger.info("generate_metrics: done in %dms enquetes=%d votos=%d", elapsed_ms, len(enquetes), len(votos))
    return dashboard_data


def _generate_metrics_from_supabase() -> Dict[str, Any]:
    enquetes = _filter_rows_since(
        supabase_clients.fetch_enquetes_for_metrics(),
        "createdAtTs",
        "created_at_provider",
        "created_at",
    )
    votos = _filter_rows_since(
        supabase_clients.fetch_votos_for_metrics(),
        "timestamp",
        "voted_at",
        "updated_at",
    )
    normalized_enquetes = []
    enquetes_map: Dict[str, Dict[str, Any]] = {}
    enquetes_created: Dict[str, Any] = {}

    for e in enquetes:
        poll_id = str(e.get("pollId", e.get("external_poll_id")) or "").strip()
        if not poll_id:
            continue
        created_at = e.get("createdAtTs", e.get("created_at_provider") or e.get("created_at"))
        item = {
            "pollId": poll_id,
            "title": e.get("title") or e.get("titulo") or poll_id,
            "chatId": e.get("chatId", e.get("chat_id")),
            "createdAtTs": created_at,
            "driveFileId": e.get("driveFileId"),
            "status": str(e.get("status") or "open").strip().lower(),
        }
        normalized_enquetes.append(item)
        enquetes_map[poll_id] = {
            "title": item["title"],
            "drive_file_id": item["driveFileId"],
            "chat_id": item["chatId"],
        }
        created_ts = processors.parse_timestamp(created_at)
        if created_ts:
            enquetes_created[poll_id] = created_ts

    normalized_votos = []
    for v in votos:
        poll_id = str(v.get("pollId") or "").strip()
        if not poll_id:
            continue
        normalized_votos.append(
            {
                "pollId": poll_id,
                "voterPhone": v.get("voterPhone"),
                "voterName": v.get("voterName"),
                "qty": str(v.get("qty") or 0),
                "timestamp": v.get("timestamp") or v.get("voted_at"),
                "rawJson": v.get("rawJson"),
            }
        )

    dates = processors.get_date_range()
    enquete_metrics = processors.analyze_enquetes(normalized_enquetes, dates)
    voto_metrics = processors.analyze_votos(normalized_votos, dates, enquetes_map, enquetes_created)
    package_payload = supabase_clients.fetch_package_lists_for_metrics()
    if isinstance(package_payload, dict):
        packages = package_payload.get("packages")
        if isinstance(packages, dict):
            processor_packages = voto_metrics.setdefault("packages", {})
            if isinstance(processor_packages, dict):
                processor_packages.clear()
                for section, values in packages.items():
                    if isinstance(values, list):
                        processor_packages[section] = values
        confirmed_summary = package_payload.get("packages_summary_confirmed")
        if isinstance(confirmed_summary, dict):
            voto_metrics["packages_summary_confirmed"] = confirmed_summary

    # F-050: termômetro de vendas + média diária + aguardando confirmação/pagamento
    try:
        from app.services.sales_temperature_service import (
            get_temperature,
            compute_confirmed_extras,
        )
        summary = voto_metrics.setdefault("packages_summary_confirmed", {})
        if isinstance(summary, dict):
            summary["sales_temperature"] = get_temperature(force_refresh=False)
            summary.update(compute_confirmed_extras())
    except Exception:
        logger.exception("Falha ao anexar sales_temperature ao payload")

    # Rankings baseados em PACOTES CONFIRMADOS (approved).
    # Muito mais significativo que votos (antes contava até votos de teste
    # ou votos de enquetes que nunca fecharam pacote).
    try:
        # Clientes (Hoje/Semana): baseado em pacotes CONFIRMADOS (venda real)
        customers_today, _ = _rankings_from_approved_packages(since_today=True)
        customers_week, _ = _rankings_from_approved_packages(hours=24 * 7)
        # Top Enquetes: janela 72h, baseado em pacotes FECHADOS (closed ou approved)
        # — mostra quais produtos estão gerando mais demanda, mesmo sem confirmação
        _, polls_72h = _rankings_from_approved_packages(
            hours=72,
            statuses=("closed", "approved"),
            date_field="closed_at",
        )
        voto_metrics["by_customer_today"] = customers_today
        voto_metrics["by_customer_week"] = customers_week
        voto_metrics["by_poll_today"] = polls_72h
        voto_metrics["by_poll_week"] = polls_72h
    except Exception:
        logger.exception("Falha ao calcular rankings por pacotes confirmados")

    # F-047 + F-048: enriquecer enquete_metrics com (1) contagem de pacotes
    # fechados nas enquetes ativas (status=open E criadas ≤72h), (2) comparativos
    # "vs ontem" e "vs média 7 dias" lidos do snapshot horário
    # (metrics_hourly_snapshots.enquetes_active_72h).
    from datetime import timedelta as _td
    _cutoff_72h = dates["now"] - _td(hours=72)
    def _pollid_active_72h(e):
        if str(e.get("status") or "").strip().lower() != "open":
            return False
        ts = processors.parse_timestamp(e.get("createdAtTs"))
        return bool(ts and ts >= _cutoff_72h)
    active_monitored_pollids = [
        e.get("pollId") for e in normalized_enquetes
        if _pollid_active_72h(e) and e.get("pollId")
    ]
    try:
        _enrich_enquetes_from_snapshots(enquete_metrics, dates["now"], active_monitored_pollids)
    except Exception:
        logger.exception("Falha ao enriquecer enquete_metrics via snapshots")

    return {
        "generated_at": dates["now"].isoformat(),
        "enquetes": enquete_metrics,
        "votos": voto_metrics,
    }


def _enrich_enquetes_from_snapshots(
    enquete_metrics: Dict[str, Any],
    now_dt,
    open_monitored_pollids: Optional[list] = None,
) -> None:
    """Lê `metrics_hourly_snapshots` e popula:
      - `enquete_metrics['closed_packages_on_active']` (pacotes closed/approved em enquetes ativas do grupo monitorado)
      - `enquete_metrics['pct_vs_yesterday']` (active_now vs snapshot de ontem mesma hora)
      - `enquete_metrics['pct_vs_7d_avg']`    (active_now vs média últimos 7 dias mesma hora)
    Se o snapshot histórico ainda não tiver dados suficientes, deixa em None.

    `open_monitored_pollids` = external_poll_id das enquetes open no grupo monitorado.
    Se None, considera todas (modo legado).
    """
    from datetime import timedelta
    from app.services.supabase_service import SupabaseRestClient

    sb = SupabaseRestClient.from_settings()
    hour_now = now_dt.replace(minute=0, second=0, microsecond=0)
    active_now = int(enquete_metrics.get("active_now") or 0)

    # 1) contagem viva de pacotes fechados em enquetes ativas (filtrado pelo grupo monitorado)
    try:
        if open_monitored_pollids:
            # resolve external_poll_id → id (uuid)
            pollids_csv = ",".join(str(p) for p in open_monitored_pollids)
            id_resp = sb._request(
                "GET",
                f"/rest/v1/enquetes?status=eq.open&external_poll_id=in.({pollids_csv})&select=id",
            )
            id_rows = id_resp.json() if id_resp.text else []
        else:
            id_resp = sb._request("GET", "/rest/v1/enquetes?status=eq.open&select=id")
            id_rows = id_resp.json() if id_resp.text else []

        open_uuids = [r.get("id") for r in (id_rows or []) if r.get("id")]
        if open_uuids:
            ids_csv = ",".join(open_uuids)
            resp = sb._request(
                "GET",
                f"/rest/v1/pacotes?status=in.(closed,approved)&enquete_id=in.({ids_csv})&select=id",
                extra_headers={"Prefer": "count=exact"},
            )
            cr = resp.headers.get("content-range", "")
            closed_count = 0
            if "/" in cr:
                total = cr.split("/")[-1]
                if total != "*":
                    closed_count = int(total)
            enquete_metrics["closed_packages_on_active"] = closed_count
        else:
            enquete_metrics["closed_packages_on_active"] = 0
    except Exception as exc:
        logger.warning("closed_packages_on_active: %s", exc)
        enquete_metrics["closed_packages_on_active"] = None

    # 2) comparativos via snapshot
    def _snapshot_open_at(bucket) -> Optional[int]:
        # F-048: usa enquetes_active_72h (casa com a definição nova do card).
        # PostgREST: `+` em query param é interpretado como espaço, precisa encode.
        from urllib.parse import quote as _q
        try:
            resp = sb._request(
                "GET",
                f"/rest/v1/metrics_hourly_snapshots?hour_bucket=eq.{_q(bucket.isoformat(), safe='')}&select=enquetes_active_72h",
            )
            rows = resp.json() if resp.text else []
            if rows and isinstance(rows, list):
                return int(rows[0].get("enquetes_active_72h") or 0)
        except Exception:
            pass
        return None

    def _safe_pct(cur: int, base: Optional[int]) -> Optional[float]:
        if base is None or base <= 0:
            return None
        return round(((cur - base) / base) * 100.0, 2)

    yesterday_bucket = hour_now - timedelta(days=1)
    yesterday_open = _snapshot_open_at(yesterday_bucket)
    enquete_metrics["pct_vs_yesterday"] = _safe_pct(active_now, yesterday_open)

    last7 = []
    for d in range(1, 8):
        val = _snapshot_open_at(hour_now - timedelta(days=d))
        if val is not None:
            last7.append(val)
    avg_7d = (sum(last7) / len(last7)) if last7 else None
    enquete_metrics["pct_vs_7d_avg"] = _safe_pct(active_now, int(avg_7d) if avg_7d else None)


        
