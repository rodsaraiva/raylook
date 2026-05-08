"""F-050: termômetro de vendas do card "Pacotes Confirmados (72h)".

Objetivo: mostrar um indicador visual do ritmo atual de fechamento de
pacotes (**closed**, não approved) comparado à **média histórica completa**
de closed/dia (desde o primeiro registro no banco).

Por que `closed` e não `approved`:
    approved é disparado em lote pelo cliente (confirma vários
    acumulados de uma vez), o que distorceria a amostra.

Por que **histórico completo** e não "últimos 30 dias":
    o usuário pediu explicitamente. 30d é um recorte arbitrário e recente;
    a média geral acumulada é mais estável e reflete o "ritmo real do
    negócio" ao longo do tempo.

Por que janela de **3 horas** (não 72h):
    o usuário quer sensibilidade "agora". 3h é volátil por natureza
    (madrugada = 0, pico = muitos), o que é aceitável pra indicador de
    temperatura.

Tiers (ratio = ritmo_atual / média_histórica):
    < 50%   → frio 🥶
    50-85%  → morno 🌤️
    85-115% → quente 🔥
    > 115%  → pelando 🌋

Cache: `CACHE_TTL_HOURS` em `app_runtime_state` pra evitar flicker visual.
O usuário pode forçar recompute via POST /api/metrics/temperature/refresh.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("raylook.services.sales_temperature")

RUNTIME_KEY = "sales_temperature"
CACHE_TTL_HOURS = 3
SAMPLE_WINDOW_HOURS = 3  # "agora" = últimas 3 horas


def _classify(ratio_pct: float) -> Dict[str, str]:
    if ratio_pct < 50:
        return {"label": "frio", "emoji": "🥶", "tone": "cold"}
    if ratio_pct < 85:
        return {"label": "morno", "emoji": "🌤️", "tone": "warm"}
    if ratio_pct < 115:
        return {"label": "quente", "emoji": "🔥", "tone": "hot"}
    return {"label": "pelando", "emoji": "🌋", "tone": "blazing"}


def _count_closed_since(sb, cutoff: datetime) -> int:
    """Conta pacotes com closed_at >= cutoff."""
    from urllib.parse import quote
    try:
        resp = sb._request(
            "GET",
            f"/rest/v1/pacotes?closed_at=gte.{quote(cutoff.isoformat(), safe='')}&select=id",
            extra_headers={"Prefer": "count=exact"},
        )
        cr = resp.headers.get("content-range", "")
        if "/" in cr:
            total = cr.split("/")[-1]
            if total != "*":
                return int(total)
        rows = resp.json() if resp.text else []
        return len(rows) if isinstance(rows, list) else 0
    except Exception as exc:
        logger.warning("_count_closed_since(%s) falhou: %s", cutoff, exc)
        return 0


def _compute_smart_daily_avg(sb) -> Dict[str, Any]:
    """Média inteligente de fechados/dia considerando:

    1. Dia da semana (segunda fecha mais que domingo)
    2. Dados recentes pesam mais (últimas 4 semanas)
    3. Conforme mais dados entram, fica mais preciso

    Retorna a média pro DIA DA SEMANA ATUAL usando as últimas 4 semanas.
    Se não tem 4 semanas do mesmo dia, usa tudo que tem.
    """
    try:
        resp = sb._request(
            "GET",
            "/rest/v1/pacotes?closed_at=not.is.null&select=closed_at&order=closed_at.asc",
        )
        rows = resp.json() if resp.text else []
        if not rows:
            return {"avg": 0.0, "avg_int": 0, "days_counted": 0, "method": "empty"}
    except Exception as exc:
        logger.warning("_compute_smart_daily_avg falhou: %s", exc)
        return {"avg": 0.0, "avg_int": 0, "days_counted": 0, "method": "error"}

    from collections import defaultdict

    now = datetime.now(timezone.utc)
    today_dow = now.weekday()  # 0=seg, 6=dom
    cutoff_28d = now - timedelta(days=28)

    # Contar fechamentos por (dia_da_semana, data)
    daily_by_dow: Dict[int, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    all_daily: Dict[str, int] = defaultdict(int)

    for r in rows:
        ts_str = r.get("closed_at")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
        except Exception:
            continue
        day_key = ts.strftime("%Y-%m-%d")
        dow = ts.weekday()
        daily_by_dow[dow][day_key] += 1
        all_daily[day_key] += 1

    # Média pro dia da semana atual (últimas 4 semanas)
    same_dow_days = daily_by_dow.get(today_dow, {})
    recent_same_dow = {d: c for d, c in same_dow_days.items()
                       if datetime.fromisoformat(d).replace(tzinfo=timezone.utc) >= cutoff_28d}

    if recent_same_dow:
        avg_raw = sum(recent_same_dow.values()) / len(recent_same_dow)
        method = f"same_dow_28d ({len(recent_same_dow)} dias)"
    elif same_dow_days:
        avg_raw = sum(same_dow_days.values()) / len(same_dow_days)
        method = f"same_dow_all ({len(same_dow_days)} dias)"
    else:
        # Fallback: média geral
        total_days = len(all_daily)
        total_closed = sum(all_daily.values())
        avg_raw = total_closed / max(1, total_days)
        method = f"geral ({total_days} dias)"

    # Também calcular média geral pra referência
    total_days = len(all_daily)
    total_closed = sum(all_daily.values())
    avg_geral = total_closed / max(1, total_days)

    dow_names = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]

    return {
        "avg_raw": round(avg_raw, 2),
        "avg": int(round(avg_raw)),
        "avg_geral": int(round(avg_geral)),
        "days_counted": len(recent_same_dow) if recent_same_dow else len(same_dow_days),
        "method": method,
        "dow_name": dow_names[today_dow],
        "total_days": total_days,
        "total_closed": total_closed,
    }


def _compute_historical_avg_same_window(sb, window_start_hour: int, window_end_hour: int) -> Dict[str, Any]:
    """Média histórica de fechados na MESMA janela de horário em todos os dias.

    Ex: se agora é 13:30 UTC e a janela é 3h, compara com a média de
    fechamentos entre 10:30-13:30 UTC em todos os dias do histórico.
    Isso elimina o viés de horário (madrugada vs pico da tarde).
    """
    from urllib.parse import quote
    try:
        # Buscar todos os fechamentos com closed_at
        # PostgREST não suporta extract(hour), então fazemos no Python
        resp = sb._request(
            "GET",
            "/rest/v1/pacotes?closed_at=not.is.null&select=closed_at&order=closed_at.asc",
            extra_headers={"Prefer": "count=exact"},
        )
        rows = resp.json() if resp.text else []
        if not rows:
            return {"avg": 0.0, "avg_int": 0, "days_with_data": 0}

        from collections import defaultdict
        daily_counts = defaultdict(int)
        all_days = set()

        for r in rows:
            ts_str = r.get("closed_at")
            if not ts_str:
                continue
            try:
                ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
            except Exception:
                continue
            day_key = ts.strftime("%Y-%m-%d")
            all_days.add(day_key)
            hour = ts.hour
            # Verificar se está na mesma janela de horário
            if window_start_hour <= window_end_hour:
                in_window = window_start_hour <= hour < window_end_hour
            else:  # janela que cruza meia-noite
                in_window = hour >= window_start_hour or hour < window_end_hour
            if in_window:
                daily_counts[day_key] += 1

        if not all_days:
            return {"avg": 0.0, "avg_int": 0, "days_with_data": 0}

        # Média: soma de fechamentos na janela / total de dias no histórico
        total_in_window = sum(daily_counts.values())
        total_days = len(all_days)
        avg_raw = total_in_window / total_days if total_days > 0 else 0

        return {
            "avg": round(avg_raw, 2),
            "avg_int": int(round(avg_raw)),
            "days_with_data": total_days,
            "total_in_window": total_in_window,
            "window": f"{window_start_hour:02d}:00-{window_end_hour:02d}:00 UTC",
        }
    except Exception as exc:
        logger.warning("_compute_historical_avg_same_window falhou: %s", exc)
        return {"avg": 0.0, "avg_int": 0, "days_with_data": 0}


def compute_temperature_now() -> Dict[str, Any]:
    """Calcula o termômetro AO VIVO (sem cache). Retorna dict serializável.

    Compara fechamentos nas últimas N horas contra a média histórica
    da MESMA janela de horário em todos os dias. Assim, 8 fechados
    entre 10h-13h é comparado com a média de 10h-13h (não com a média
    diária total, que incluiria madrugada com 0 fechamentos).
    """
    from app.services.supabase_service import SupabaseRestClient

    sb = SupabaseRestClient.from_settings()

    now = datetime.now(timezone.utc)
    cutoff_window = now - timedelta(hours=SAMPLE_WINDOW_HOURS)
    closed_last_window = _count_closed_since(sb, cutoff_window)

    # Janela de horário: de (now - 3h) até now, em horas UTC
    window_start_hour = cutoff_window.hour
    window_end_hour = now.hour + 1  # +1 pra incluir a hora atual
    if window_end_hour > 23:
        window_end_hour = 24

    hist_window = _compute_historical_avg_same_window(sb, window_start_hour, window_end_hour)
    hist_daily = _compute_smart_daily_avg(sb)

    avg_same_window = float(hist_window.get("avg") or 0)

    if avg_same_window > 0:
        ratio_pct = (closed_last_window / avg_same_window) * 100
    else:
        ratio_pct = 0.0

    cls = _classify(ratio_pct)

    return {
        "label": cls["label"],
        "emoji": cls["emoji"],
        "tone": cls["tone"],
        "closed_in_window": closed_last_window,
        "avg_same_window": avg_same_window,
        "avg_same_window_int": hist_window.get("avg_int", 0),
        "window_hours": f"{window_start_hour:02d}-{window_end_hour:02d} UTC",
        "days_with_data": hist_window.get("days_with_data", 0),
        "ratio_pct": round(ratio_pct, 1),
        "historical_avg_per_day": float(hist_daily.get("avg_raw") or 0),
        "historical_avg_geral": hist_daily.get("avg_geral", 0),
        "historical_days_span": hist_daily.get("total_days", 0),
        "dow_name": hist_daily.get("dow_name", ""),
        "smart_method": hist_daily.get("method", ""),
        "sample_window_hours": SAMPLE_WINDOW_HOURS,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "ttl_hours": CACHE_TTL_HOURS,
    }


def _is_cache_fresh(cached: Optional[Dict[str, Any]]) -> bool:
    if not cached:
        return False
    ts = cached.get("computed_at")
    if not ts:
        return False
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return False
    age = datetime.now(timezone.utc) - dt
    return age < timedelta(hours=CACHE_TTL_HOURS)


def get_temperature(force_refresh: bool = False) -> Dict[str, Any]:
    """Lê do cache se fresco, senão recomputa e persiste."""
    from app.services.runtime_state_service import (
        load_runtime_state,
        save_runtime_state,
        runtime_state_enabled,
    )

    if not force_refresh and runtime_state_enabled():
        try:
            cached = load_runtime_state(RUNTIME_KEY)
            if _is_cache_fresh(cached):
                return cached or {}
        except Exception:
            logger.exception("sales_temperature: falha ao ler cache")

    fresh = compute_temperature_now()
    try:
        if runtime_state_enabled():
            save_runtime_state(RUNTIME_KEY, fresh)
    except Exception:
        logger.exception("sales_temperature: falha ao gravar cache")
    return fresh


def compute_confirmed_extras(sb=None) -> Dict[str, Any]:
    """Extras usados pelo card (consistentes com o termômetro):
    - `daily_avg_closed_historic`: média histórica de closed/dia INTEIRA
      (valor cheio), mesma baseline do termômetro — 1 fonte da verdade.
    - `closed_72h_still_closed`: pacotes que fecharam nas últimas 72h E ainda
      estão com status='closed' (não confirmados). Escopo 72h casa com o
      tema do card "Pacotes Confirmados (72h)".
    - `approved_72h_unpaid`: pacotes approved nas últimas 72h cujo pagamento
      não está pago.

    Não cacheia — leve.
    """
    from urllib.parse import quote
    from app.services.supabase_service import SupabaseRestClient

    sb = sb or SupabaseRestClient.from_settings()
    now = datetime.now(timezone.utc)
    cutoff_72h = now - timedelta(hours=72)
    cutoff_72h_q = quote(cutoff_72h.isoformat(), safe="")

    def _count(path: str) -> int:
        try:
            resp = sb._request("GET", path, extra_headers={"Prefer": "count=exact"})
            cr = resp.headers.get("content-range", "")
            if "/" in cr:
                total = cr.split("/")[-1]
                if total != "*":
                    return int(total)
            rows = resp.json() if resp.text else []
            return len(rows) if isinstance(rows, list) else 0
        except Exception as exc:
            logger.warning("compute_confirmed_extras count: %s", exc)
            return 0

    # Baseline histórica (mesma usada pelo termômetro)
    hist = _compute_smart_daily_avg(sb)

    # Escopo 72h: pacotes que fecharam nesse período e continuam no estado closed
    closed_72h_still_closed = _count(
        f"/rest/v1/pacotes?status=eq.closed&closed_at=gte.{cutoff_72h_q}&select=id"
    )

    # Escopo 72h: pacotes approved nesse período cujo pagamento não é paid
    approved_72h_unpaid = 0
    try:
        resp = sb._request(
            "GET",
            (
                "/rest/v1/pacotes"
                f"?status=eq.approved&approved_at=gte.{cutoff_72h_q}"
                "&select=id,vendas(pagamentos(status))"
            ),
        )
        rows = resp.json() if resp.text else []
        for r in rows or []:
            vendas = r.get("vendas") or []
            if not isinstance(vendas, list):
                vendas = [vendas]
            has_unpaid = False
            for v in vendas:
                pagamentos = (v or {}).get("pagamentos") or []
                if not isinstance(pagamentos, list):
                    pagamentos = [pagamentos]
                for pg in pagamentos:
                    st = str((pg or {}).get("status") or "").lower()
                    if st in ("created", "sent", "failed", "pending"):
                        has_unpaid = True
                        break
                if has_unpaid:
                    break
            if has_unpaid:
                approved_72h_unpaid += 1
    except Exception as exc:
        logger.warning("approved_72h_unpaid embed falhou: %s", exc)

    return {
        # Média histórica em valor cheio (int) pro display, raw pra auditoria
        "daily_avg_closed_historic": hist.get("avg", 0),
        "daily_avg_closed_historic_raw": hist.get("avg_raw", 0.0),
        "daily_avg_geral": hist.get("avg_geral", 0),
        "historical_days_span": hist.get("total_days", 0),
        "historical_total_closed": hist.get("total_closed", 0),
        "dow_name": hist.get("dow_name", ""),
        # Escopo 72h que casa com o tema do card
        "closed_72h_still_closed": closed_72h_still_closed,
        "approved_72h_unpaid": approved_72h_unpaid,
    }
