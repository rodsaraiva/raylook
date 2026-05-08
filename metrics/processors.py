from datetime import datetime, timedelta
from collections import defaultdict
from datetime import timezone
from typing import Dict, List, Any, Optional, Tuple
import json
import logging
import os
from zoneinfo import ZoneInfo


logger = logging.getLogger("raylook.metrics.processors")


def build_drive_image_url(drive_id: Any) -> Optional[str]:
    if not drive_id:
        return None
    return f"/files/{drive_id}"


def resolve_enquete_drive_file_id(
    enquete: Optional[Dict[str, Any]] = None,
    produto: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """F-061: imagem vive na enquete (por post do WhatsApp).
    Fallback pro produto mantém dados legado funcionando.
    """
    if isinstance(enquete, dict):
        fid = str(enquete.get("drive_file_id") or "").strip()
        if fid:
            return fid
    if isinstance(produto, dict):
        fid = str(produto.get("drive_file_id") or "").strip()
        if fid:
            return fid
    return None


def _app_timezone():
    tz_name = str(os.getenv("TZ", "America/Sao_Paulo") or "America/Sao_Paulo").strip()
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return timezone.utc


def _to_local_naive(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(_app_timezone()).replace(tzinfo=None)

def parse_timestamp(ts_str: Any) -> Optional[datetime]:
    """Parse timestamp string to datetime."""
    if not ts_str:
        return None
    if isinstance(ts_str, datetime):
        return _to_local_naive(ts_str)
    try:
        ts_val = float(ts_str)
        if ts_val > 10000000000:
            ts_val = ts_val / 1000
        return datetime.fromtimestamp(ts_val)
    except Exception:
        try:
            dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
            return _to_local_naive(dt)
        except Exception:
            return None


def get_date_range(now: Optional[datetime] = None) -> Dict[str, datetime]:
    if now is None:
        now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)
    yesterday_end = today_start
    week_start = today_start - timedelta(days=7)  # rolling 7d (legacy)
    day24h_start = now - timedelta(hours=24)

    # F-044: novos cortes pra "até a mesma hora" e "semana até o mesmo ponto".
    # elapsed_today = quanto tempo se passou desde 00:00 de hoje
    elapsed_today = now - today_start
    # Até mesma hora de ontem = yesterday_start + elapsed_today
    yesterday_same_hour = yesterday_start + elapsed_today
    # Início da semana atual (últimos 7 dias rolling, hoje inclusive)
    this_week_start = today_start - timedelta(days=6)  # hoje + 6 dias atrás = 7 dias
    # Ponto equivalente na semana passada = this_week_start - 7 dias, mas cortado na mesma hora agora
    last_week_same_point_end = now - timedelta(days=7)
    last_week_same_point_start = this_week_start - timedelta(days=7)

    return {
        "now": now,
        "today_start": today_start,
        "yesterday_start": yesterday_start,
        "yesterday_end": yesterday_end,
        "yesterday_same_hour": yesterday_same_hour,
        "week_start": week_start,
        "this_week_start": this_week_start,
        "last_week_same_point_start": last_week_same_point_start,
        "last_week_same_point_end": last_week_same_point_end,
        "day24h_start": day24h_start,
        "elapsed_today": elapsed_today,
    }


def get_packages_closed_cutoff() -> Optional[datetime]:
    raw = str(os.getenv("METRICS_MIN_DATE", "") or "").strip()
    if not raw:
        return None
    return parse_timestamp(raw)

def preserve_package_metadata(new_data: dict, old_data: dict):
    """
    Preserva thumbnails, status de PDF e dados financeiros de pacotes existentes no novo conjunto de dados.
    Usa a URL da imagem como chave primÃ¡ria para thumbnails e o ID do pacote/telefone para outros metadados.
    """
    if not old_data or not new_data:
        return

    # 1. Mapear metadados de pacotes existentes
    old_pkgs_map = {} # { pkg_id: pkg_data }
    thumb_map = {}    # { image_url: image_thumb }

    def collect_from_section(section_data):
        if not isinstance(section_data, list): return
        for p in section_data:
            pkg_id = p.get("id")
            img = p.get("image")
            thumb = p.get("image_thumb")
            
            if pkg_id:
                old_pkgs_map[pkg_id] = p
            if img and thumb:
                thumb_map[img] = thumb

    votos_old = old_data.get("votos", {})
    if isinstance(votos_old, dict):
        packages_old = votos_old.get("packages", {})
        if isinstance(packages_old, dict):
            for section in packages_old.values():
                collect_from_section(section)

    # 2. Aplicar ao novo conjunto de dados
    votos_new = new_data.get("votos", {})
    if not isinstance(votos_new, dict): return
    packages_new = votos_new.get("packages", {})
    if not isinstance(packages_new, dict): return
    
    for section_name, pkg_list in packages_new.items():
        if not isinstance(pkg_list, list): continue
        for p in pkg_list:
            pkg_id = p.get("id")
            img = p.get("image")
            
            # Restaurar thumbnail por URL da imagem (independente do ID do pacote)
            if img in thumb_map and not p.get("image_thumb"):
                p["image_thumb"] = thumb_map[img]
            
            # Restaurar metadados por ID do pacote
            if pkg_id and pkg_id in old_pkgs_map:
                old_p = old_pkgs_map[pkg_id]
                # Preservar campos de PDF
                for field in ["pdf_status", "pdf_sent_at", "pdf_file_name", "pdf_attempts"]:
                    if field in old_p and field not in p:
                        p[field] = old_p[field]
                
                # Preservar metadados de votos (Asaas/Pagamentos)
                old_votes = {v.get("phone"): v for v in old_p.get("votes", []) if v.get("phone")}
                for new_v in p.get("votes", []):
                    phone = new_v.get("phone")
                    if phone and phone in old_votes:
                        old_v = old_votes[phone]
                        fields_to_preserve = [
                            "asaas_customer_id", "asaas_payment_id", "asaas_payment_status", 
                            "financial_details", "asaas_payment_payload", "asaas_customer_status",
                            "asaas_customer_attempts", "asaas_payment_attempts", "asaas_payment_send_result"
                        ]
                        for field in fields_to_preserve:
                            if field in old_v and field not in new_v:
                                new_v[field] = old_v[field]

def analyze_enquetes(enquetes: List[Dict[str, Any]], dates: Dict[str, datetime]) -> Dict[str, Any]:
    # F-048: active_now = enquetes com status='open' E criadas nas últimas 72h.
    # Antes contava tudo open (incluindo enquetes zumbi de semanas atrás); o
    # filtro 72h descarta essas e deixa só as de fato ativas.
    cutoff_72h = dates["now"] - timedelta(hours=72)
    def _is_active_72h(e: Dict[str, Any]) -> bool:
        if str(e.get("status") or "open").strip().lower() != "open":
            return False
        ts = parse_timestamp(e.get("createdAtTs", e.get("field_171")))
        return bool(ts and ts >= cutoff_72h)
    active_now = sum(1 for e in enquetes if _is_active_72h(e))
    metrics = {"today": 0, "yesterday": 0, "last_7_days": [0] * 7, "total": len(enquetes)}
    for e in enquetes:
        ts = parse_timestamp(e.get("createdAtTs", e.get("field_171")))
        if not ts:
            continue
        if ts >= dates["today_start"]:
            metrics["today"] += 1
        elif ts >= dates["yesterday_start"] and ts < dates["yesterday_end"]:
            metrics["yesterday"] += 1
        if ts >= dates["week_start"] and ts < dates["today_start"]:
            days_ago = (dates["today_start"] - ts).days
            if 0 <= days_ago < 7:
                metrics["last_7_days"][days_ago] += 1
    # Build explicit last 7 days counts (yesterday..7 days ago)
    last7 = []
    for i in range(1, 8):
        day_start = dates["today_start"] - timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        cnt = 0
        for e in enquetes:
            ts = parse_timestamp(e.get("createdAtTs", e.get("field_171")))
            if not ts:
                continue
            if ts >= day_start and ts < day_end:
                cnt += 1
        last7.append(cnt)

    # Build enquetes with images for today/week
    for e in enquetes:
        drive_id = e.get("driveFileId", e.get("field_200"))
        if drive_id:
            poll_id = e.get("pollId", e.get("field_169"))
            if poll_id:
                # This helps analyze_votos if it re-checks enquetes
                pass 

    avg_7_days = sum(last7) / 7 if last7 else 0
    diff_yesterday = metrics["today"] - metrics["yesterday"]
    diff_avg = metrics["today"] - avg_7_days
    pct_yesterday = (diff_yesterday / metrics["yesterday"] * 100) if metrics["yesterday"] > 0 else 0
    pct_avg = (diff_avg / avg_7_days * 100) if avg_7_days > 0 else 0
    same_weekday_last_week = last7[6] if len(last7) >= 7 else 0

    return {
        "today": metrics["today"],
        "yesterday": metrics["yesterday"],
        "diff_yesterday": diff_yesterday,
        "pct_yesterday": pct_yesterday,
        "avg_7_days": avg_7_days,
        "diff_avg": diff_avg,
        "pct_avg": pct_avg,
        "last_7_days": last7,
        "same_weekday_last_week": same_weekday_last_week,
        # Card "Enquetes Ativas" (status='open' agora)
        "active_now": active_now,
    }


class VoteProcessor:
    def __init__(self):
        self.poll_votes = defaultdict(lambda: defaultdict(list))
        self.closed_packages = defaultdict(list)
        self.waitlist = defaultdict(list)

    def process_vote(self, vote: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        poll_id = vote.get("pollId", vote.get("field_158"))
        voter_phone = vote.get("voterPhone", vote.get("field_160"))
        try:
            qty = int(vote.get("qty", vote.get("field_164", "0")))
        except Exception:
            qty = 0
        vote["parsed_qty"] = qty
        if qty > 0:
            self.poll_votes[poll_id][voter_phone] = [vote]
            return "added", vote
        else:
            if self.poll_votes[poll_id][voter_phone]:
                removed = self.poll_votes[poll_id][voter_phone].pop()
                return "removed", removed
            else:
                return "ignored", vote

    def calculate_packages(self, limit: int = 24):
        for poll_id, voters in self.poll_votes.items():
            all_active_votes = []
            for phone, votes in voters.items():
                all_active_votes.extend(votes)
            def _sort_key(vote_item: Dict[str, Any]):
                # prioritize larger quantities first, then earlier timestamps
                qty_candidate = vote_item.get("parsed_qty", vote_item.get("qty", vote_item.get("field_164", 0)))
                try:
                    qty_val = int(qty_candidate)
                except Exception:
                    qty_val = 0
                ts = parse_timestamp(vote_item.get("timestamp", vote_item.get("field_166"))) or datetime.min
                # negative qty for descending order, timestamp ascending
                return (-qty_val, ts)

            all_active_votes.sort(key=_sort_key)
            pending_votes = all_active_votes[:]
            while True:
                package, remaining = self._find_subset_sum(pending_votes, limit)
                if package:
                    self.closed_packages[poll_id].append(package)
                    pending_votes = remaining
                else:
                    break
            self.waitlist[poll_id] = pending_votes

    def _find_subset_sum(self, votes: List[Dict[str, Any]], target: int):
        def backtrack(index, current_sum, current_subset):
            if current_sum == target:
                return current_subset
            if current_sum > target or index >= len(votes):
                return None
            vote = votes[index]
            qty = vote["parsed_qty"]
            if current_sum + qty <= target:
                res = backtrack(index + 1, current_sum + qty, current_subset + [vote])
                if res:
                    return res
            return backtrack(index + 1, current_sum, current_subset)

        subset = backtrack(0, 0, [])
        if subset:
            subset_ids = set(id(v) for v in subset)
            remaining = [v for v in votes if id(v) not in subset_ids]
            return subset, remaining
        return None, votes


def analyze_votos(
    votos: List[Dict[str, Any]],
    dates: Dict[str, datetime],
    enquetes_map: Dict[str, Any],
    enquetes_created: Optional[Dict[str, datetime]] = None,
) -> Dict[str, Any]:
    from app.services.customer_service import load_customers, save_customers

    processor = VoteProcessor()
    closed_cutoff = get_packages_closed_cutoff()

    def _clean_phone(phone: Any) -> str:
        if not phone:
            return ""
        return "".join(filter(str.isdigit, str(phone)))

    raw_customers = load_customers()
    all_customers = {
        _clean_phone(phone): name
        for phone, name in raw_customers.items()
        if _clean_phone(phone)
    }
    new_customers_found: Dict[str, str] = {}

    def _is_useful_name(name: Any) -> bool:
        if not name:
            return False
        normalized = str(name).strip()
        if not normalized:
            return False
        if normalized.lower() in {"desconhecido", "unknown", "voter", "voto", "cliente"}:
            return False
        if len(normalized) <= 2 and not any(char.isalnum() for char in normalized):
            return False
        return True

    metrics = {
        "today": 0,
        "yesterday": 0,
        "last_7_days": [0] * 7,
        "removed_today": 0,
        "removed_yesterday": 0,
        "by_poll_today": defaultdict(lambda: {"title": "", "qty": 0}),
        "by_poll_week": defaultdict(lambda: {"title": "", "qty": 0}),
        "by_customer_today": defaultdict(lambda: {"name": "", "qty": 0, "phone": ""}),
        "by_customer_week": defaultdict(lambda: {"name": "", "qty": 0, "phone": ""}),
        "by_hour": defaultdict(int),
        "packages": {"open": [], "closed_today": [], "closed_week": [], "confirmed_today": []},
    }

    def _extract_title_from_raw(raw_str: Any) -> Optional[str]:
        if not raw_str:
            return None
        try:
            raw_data = json.loads(raw_str)
            if not isinstance(raw_data, dict):
                return None
            # direct poll
            if "poll" in raw_data and isinstance(raw_data["poll"], dict):
                return raw_data["poll"].get("title")
            # body.poll
            body = raw_data.get("body")
            if isinstance(body, dict):
                if "poll" in body and isinstance(body["poll"], dict):
                    return body["poll"].get("title")
                # messages_updates path
                msgs = body.get("messages_updates")
                if isinstance(msgs, list) and msgs:
                    msg = msgs[0]
                    if isinstance(msg, dict):
                        if "poll" in msg and isinstance(msg["poll"], dict):
                            return msg["poll"].get("title")
                        for key in ("after_update", "before_update"):
                            upd = msg.get(key)
                            if isinstance(upd, dict) and "poll" in upd and isinstance(upd["poll"], dict):
                                return upd["poll"].get("title")
            return None
        except Exception as exc:
            # don't raise here, but log for observability if available
            try:
                import logging

                logging.getLogger("raylook.processors").exception("Failed to extract title from rawJson")
            except Exception:
                pass
            return None

    def _first_pass(votos_list: List[Dict[str, Any]]) -> Dict[str, str]:
        # operate on a local copy and return updates to caller explicitly
        local_map: Dict[str, str] = dict(enquetes_map)
        sorted_votos = sorted(votos_list, key=lambda x: x.get("id", 0))
        for v in sorted_votos:
            poll_id = v.get("pollId", v.get("field_158"))
            if poll_id and (poll_id not in local_map or not local_map[poll_id]):
                t = _extract_title_from_raw(v.get("rawJson"))
                if t:
                    local_map[poll_id] = t
            voter_phone = v.get("voterPhone", v.get("field_160"))
            voter_name = v.get("voterName", v.get("field_161"))
            phone_str = _clean_phone(voter_phone)
            if phone_str and _is_useful_name(voter_name) and not _is_useful_name(all_customers.get(phone_str)):
                new_customers_found[phone_str] = str(voter_name).strip()
            status, _ = processor.process_vote(v)
            ts = parse_timestamp(v.get("timestamp", v.get("field_166")))
            if not ts:
                continue
            if ts >= dates["today_start"]:
                if status in ("removed", "ignored"):
                    metrics["removed_today"] += 1
                elif status == "added":
                    metrics["today"] += 1
            elif ts >= dates["yesterday_start"] and ts < dates["yesterday_end"]:
                if status in ("removed", "ignored"):
                    metrics["removed_yesterday"] += 1
                elif status == "added":
                    metrics["yesterday"] += 1
            if ts >= dates["week_start"] and ts < dates["today_start"]:
                days_ago = (dates["today_start"] - ts).days
                if 0 <= days_ago < 7:
                    metrics["last_7_days"][days_ago] += 1
        return local_map

    def _aggregate_from_processor():
        for poll_voters in processor.poll_votes.values():
            for vote_list in poll_voters.values():
                for v in vote_list:
                    ts = parse_timestamp(v.get("timestamp", v.get("field_166")))
                    if not ts:
                        continue
                    qty = v["parsed_qty"]
                    poll_id = v.get("pollId", v.get("field_158"))
                    poll_info = enquetes_map.get(poll_id, {})
                    poll_title = poll_info.get("title", poll_id) if isinstance(poll_info, dict) else poll_info
                    voter_phone = v.get("voterPhone", v.get("field_160"))
                    phone_str = _clean_phone(voter_phone)
                    customer_name = (
                        all_customers.get(phone_str)
                        or new_customers_found.get(phone_str)
                        or v.get("voterName", v.get("field_161"))
                    )
                    if ts >= dates["day24h_start"]:
                        # parse_timestamp já retorna datetime em BRT (naive)
                        metrics["by_hour"][ts.hour] += 1
                    if ts >= dates["today_start"]:
                        metrics["by_poll_today"][poll_id]["title"] = poll_title
                        metrics["by_poll_today"][poll_id]["qty"] += qty
                        metrics["by_customer_today"][voter_phone]["name"] = customer_name
                        metrics["by_customer_today"][voter_phone]["phone"] = voter_phone
                        metrics["by_customer_today"][voter_phone]["qty"] += qty
                    if ts >= dates["week_start"]:
                        metrics["by_poll_week"][poll_id]["title"] = poll_title
                        metrics["by_poll_week"][poll_id]["qty"] += qty
                        metrics["by_customer_week"][voter_phone]["name"] = customer_name
                        metrics["by_customer_week"][voter_phone]["phone"] = voter_phone
                        metrics["by_customer_week"][voter_phone]["qty"] += qty

    def _process_packages():
        for poll_id, packages in processor.closed_packages.items():
            poll_info = enquetes_map.get(poll_id, {})
            poll_title = poll_info.get("title", poll_id) if isinstance(poll_info, dict) else poll_info
            drive_id = poll_info.get("drive_file_id") if isinstance(poll_info, dict) else None
            image_url = build_drive_image_url(drive_id)
            poll_created_ts = enquetes_created.get(poll_id) if enquetes_created else None

            for i, pkg_votes in enumerate(packages):
                last_ts = max((parse_timestamp(v.get("timestamp", v.get("field_166"))) or datetime.min) for v in pkg_votes)
                pkg_data = {
                    "id": f"{poll_id}_{i}",
                    "poll_title": poll_title,
                    "image": image_url,
                    "qty": sum(v["parsed_qty"] for v in pkg_votes),
                    "status": "closed",
                    "opened_at": poll_created_ts.isoformat() if poll_created_ts else None,
                    "closed_at": last_ts.isoformat() if last_ts != datetime.min else None,
                    "votes": [
                        {
                            "name": (
                                all_customers.get(_clean_phone(v.get("voterPhone", v.get("field_160", ""))))
                                or new_customers_found.get(_clean_phone(v.get("voterPhone", v.get("field_160", ""))))
                                or v.get("voterName", v.get("field_161", "Desconhecido"))
                            ),
                            "phone": v.get("voterPhone", v.get("field_160", "")),
                            "qty": v["parsed_qty"],
                        }
                        for v in pkg_votes
                    ],
                }
                if not poll_created_ts:
                    continue

                if closed_cutoff is not None:
                    if last_ts >= closed_cutoff:
                        metrics["packages"]["closed_today"].append(pkg_data)
                elif last_ts >= dates["today_start"] and poll_created_ts >= dates["yesterday_start"]:
                    metrics["packages"]["closed_today"].append(pkg_data)
                if last_ts >= dates["week_start"]:
                    metrics["packages"]["closed_week"].append(pkg_data)

        for poll_id, wait_votes in processor.waitlist.items():
            if not wait_votes:
                continue
            poll_info = enquetes_map.get(poll_id, {})
            poll_title = poll_info.get("title", poll_id) if isinstance(poll_info, dict) else poll_info
            drive_id = poll_info.get("drive_file_id") if isinstance(poll_info, dict) else None
            image_url = build_drive_image_url(drive_id)

            poll_created_ts = enquetes_created.get(poll_id) if enquetes_created else None
            # Pacotes em Aberto: apenas os abertos hoje
            if poll_created_ts and poll_created_ts >= dates["today_start"]:
                metrics["packages"]["open"].append(
                    {
                        "poll_id": poll_id,
                        "poll_title": poll_title,
                        "image": image_url,
                        "qty": sum(v["parsed_qty"] for v in wait_votes),
                        "opened_at": poll_created_ts.isoformat() if poll_created_ts else None,
                        "votes": [
                            {
                                "name": (
                                    all_customers.get(_clean_phone(v.get("voterPhone", v.get("field_160", ""))))
                                    or new_customers_found.get(_clean_phone(v.get("voterPhone", v.get("field_160", ""))))
                                    or v.get("voterName", v.get("field_161", "Desconhecido"))
                                ),
                                "phone": v.get("voterPhone", v.get("field_160", "")),
                                "qty": v["parsed_qty"],
                            }
                            for v in wait_votes
                        ],
                    }
                )

    # run pipeline and apply extracted titles back to caller-provided map
    local_updates = _first_pass(votos)
    # update caller's enquetes_map explicitly
    enquetes_map.update(local_updates)
    processor.calculate_packages()
    _aggregate_from_processor()

    avg_7_days = sum(metrics["last_7_days"]) / 7 if metrics["last_7_days"] else 0
    diff_yesterday = metrics["today"] - metrics["yesterday"]
    diff_avg = metrics["today"] - avg_7_days
    pct_yesterday = (diff_yesterday / metrics["yesterday"] * 100) if metrics["yesterday"] > 0 else 0
    pct_avg = (diff_avg / avg_7_days * 100) if avg_7_days > 0 else 0
    diff_removed = metrics["removed_today"] - metrics["removed_yesterday"]
    pct_removed = (diff_removed / metrics["removed_yesterday"] * 100) if metrics["removed_yesterday"] > 0 else 0

    _process_packages()

    if new_customers_found:
        try:
            latest_customers = load_customers()
            to_update = {
                phone: name
                for phone, name in new_customers_found.items()
                if latest_customers.get(phone) != name and len(phone) >= 8 and name
            }
            if to_update:
                latest_customers.update(to_update)
                save_customers(latest_customers)
                logger.info("Auto-capturados %d nomes de clientes durante o processamento de votos.", len(to_update))
        except Exception as exc:
            logger.error("Falha ao salvar clientes auto-capturados: %s", exc)

    # Build votes last-7-days (yesterday .. 7 days ago)
    votes_last7 = []
    for i in range(1, 8):
        day_start = dates["today_start"] - timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        cnt = 0
        for v in votos:
            ts = parse_timestamp(v.get("timestamp", v.get("field_166")))
            if not ts:
                continue
            if ts >= day_start and ts < day_end:
                try:
                    qty = int(v.get("qty", v.get("field_164", "0")))
                except Exception:
                    qty = 0
                if qty > 0:
                    cnt += 1
        votes_last7.append(cnt)

    votes_same_weekday_last_week = votes_last7[6] if len(votes_last7) >= 7 else 0
    votes_avg_7_days = sum(votes_last7) / 7 if votes_last7 else 0

    # F-044: métricas "até a mesma hora" e "semana até o mesmo ponto"
    def _count_votes_in_range(start_dt: datetime, end_dt: datetime) -> int:
        """Conta votos cujo timestamp está em [start_dt, end_dt) e qty > 0."""
        cnt = 0
        for v in votos:
            ts = parse_timestamp(v.get("timestamp", v.get("field_166")))
            if not ts:
                continue
            if ts < start_dt or ts >= end_dt:
                continue
            try:
                qty_val = int(v.get("qty", v.get("field_164", "0")))
            except Exception:
                qty_val = 0
            if qty_val > 0:
                cnt += 1
        return cnt

    now_ts = dates["now"]
    elapsed_today = dates["elapsed_today"]

    # Votos HOJE até agora (mesma métrica que metrics["today"], mas reconta
    # a partir do timestamp para ser consistente com os recortes de hora)
    votes_today_so_far = _count_votes_in_range(dates["today_start"], now_ts)

    # Votos ONTEM até mesma hora (00:00 de ontem até yesterday_same_hour)
    votes_yesterday_until_same_hour = _count_votes_in_range(
        dates["yesterday_start"], dates["yesterday_same_hour"]
    )

    # Média dos últimos 7 dias cortados na mesma hora do dia
    # (não inclui hoje, inclui ontem, anteontem, ..., 7 dias atrás)
    daily_until_same_hour = []
    for i in range(1, 8):
        day_start = dates["today_start"] - timedelta(days=i)
        day_end_at_hour = day_start + elapsed_today
        daily_until_same_hour.append(_count_votes_in_range(day_start, day_end_at_hour))
    votes_avg_7d_until_same_hour = (
        sum(daily_until_same_hour) / 7 if daily_until_same_hour else 0
    )
    # 7 dias atrás = mesmo dia da semana passada até a mesma hora
    votes_same_weekday_last_week_same_hour = (
        daily_until_same_hour[6] if len(daily_until_same_hour) >= 7 else 0
    )

    # Votos SEMANA até agora (últimos 7 dias rolling incluindo hoje)
    votes_week_to_date = _count_votes_in_range(dates["this_week_start"], now_ts)

    # Votos SEMANA PASSADA até o mesmo ponto (7 dias antes até now-7d)
    votes_last_week_same_point = _count_votes_in_range(
        dates["last_week_same_point_start"], dates["last_week_same_point_end"]
    )

    # Média das últimas 4 semanas equivalentes (same rolling window, -1w, -2w, -3w, -4w)
    weekly_buckets = []
    for i in range(1, 5):
        wk_start = dates["this_week_start"] - timedelta(days=7 * i)
        wk_end = now_ts - timedelta(days=7 * i)
        weekly_buckets.append(_count_votes_in_range(wk_start, wk_end))
    votes_avg_4_weeks = sum(weekly_buckets) / 4 if weekly_buckets else 0

    def _safe_pct(current, baseline):
        # F-044: retorna None quando não há dados pra comparar. O frontend
        # (setSmallDiff) mostra "—" quando recebe null, deixando claro que
        # é "sem dados ainda" em vez de "0% = empate".
        if not baseline or baseline == 0:
            return None
        return ((current - baseline) / baseline) * 100

    def _round_or_none(val, ndigits=1):
        return None if val is None else round(val, ndigits)

    pct_vs_yesterday_same_hour = _safe_pct(
        votes_today_so_far, votes_yesterday_until_same_hour
    )
    pct_vs_avg_7d_same_hour = _safe_pct(
        votes_today_so_far, votes_avg_7d_until_same_hour
    )
    # F-044: "vs semana passada" compara HOJE até agora com o MESMO DIA
    # da semana passada até a mesma hora (7 dias atrás exatos).
    pct_vs_last_week_same_weekday = _safe_pct(
        votes_today_so_far, votes_same_weekday_last_week_same_hour
    )
    pct_vs_last_week_same_point = _safe_pct(
        votes_week_to_date, votes_last_week_same_point
    )
    # F-044: "vs média mensal" — média dos 4 dias equivalentes nas 4 semanas
    # anteriores (ex: hoje é qua → média das 4 quartas-feiras anteriores até
    # a mesma hora). Mais significativo que média de 4 semanas inteiras,
    # porque compara sempre o mesmo ponto da semana.
    monthly_same_weekday_cuts = []
    for i in range(1, 5):
        ref_day_start = dates["today_start"] - timedelta(days=7 * i)
        ref_day_end = ref_day_start + elapsed_today
        monthly_same_weekday_cuts.append(_count_votes_in_range(ref_day_start, ref_day_end))
    votes_avg_monthly_same_weekday = (
        sum(monthly_same_weekday_cuts) / 4 if monthly_same_weekday_cuts else 0
    )
    pct_vs_monthly_avg = _safe_pct(votes_today_so_far, votes_avg_monthly_same_weekday)

    pct_vs_avg_4_weeks = _safe_pct(votes_week_to_date, votes_avg_4_weeks)

    # Build closed packages last-7-days counts
    closed_last7 = []
    for i in range(1, 8):
        day_start = dates["today_start"] - timedelta(days=i)
        day_end = day_start + timedelta(days=1)
        cnt = 0
        for pkg in metrics["packages"].get("closed_week", []):
            closed_ts = parse_timestamp(pkg.get("closed_at"))
            if not closed_ts:
                continue
            if closed_ts >= day_start and closed_ts < day_end:
                cnt += 1
        closed_last7.append(cnt)

    closed_same_weekday_last_week = closed_last7[6] if len(closed_last7) >= 7 else 0
    closed_avg_7_days = sum(closed_last7) / 7 if closed_last7 else 0

    return {
        "today": metrics["today"],
        "yesterday": metrics["yesterday"],
        "diff_yesterday": diff_yesterday,
        "pct_yesterday": pct_yesterday,
        "avg_7_days": avg_7_days,
        "diff_avg": diff_avg,
        "pct_avg": pct_avg,
        "removed_today": metrics["removed_today"],
        "removed_yesterday": metrics["removed_yesterday"],
        "diff_removed": diff_removed,
        "pct_removed": pct_removed,
        "by_poll_today": dict(metrics["by_poll_today"]),
        "by_poll_week": dict(metrics["by_poll_week"]),
        "by_customer_today": dict(metrics["by_customer_today"]),
        "by_customer_week": dict(metrics["by_customer_week"]),
        "by_hour": dict(metrics["by_hour"]),
        "packages": metrics["packages"],
        "last_7_days": votes_last7,
        "same_weekday_last_week": votes_same_weekday_last_week,
        # F-044: métricas hora-a-hora pro card único "Votos Hoje"
        "today_so_far": votes_today_so_far,
        "yesterday_until_same_hour": votes_yesterday_until_same_hour,
        "same_weekday_last_week_same_hour": votes_same_weekday_last_week_same_hour,
        "avg_monthly_same_weekday": round(votes_avg_monthly_same_weekday, 1),
        "pct_vs_yesterday_same_hour": _round_or_none(pct_vs_yesterday_same_hour, 1),
        "pct_vs_last_week_same_weekday": _round_or_none(pct_vs_last_week_same_weekday, 1),
        "pct_vs_monthly_avg": _round_or_none(pct_vs_monthly_avg, 1),
        # Métricas antigas ainda expostas pra compatibilidade
        "avg_7d_until_same_hour": round(votes_avg_7d_until_same_hour, 1),
        "pct_vs_avg_7d_same_hour": _round_or_none(pct_vs_avg_7d_same_hour, 1),
        "week_to_date": votes_week_to_date,
        "last_week_same_point": votes_last_week_same_point,
        "avg_4_weeks": round(votes_avg_4_weeks, 1),
        "pct_vs_last_week_same_point": _round_or_none(pct_vs_last_week_same_point, 1),
        "pct_vs_avg_4_weeks": _round_or_none(pct_vs_avg_4_weeks, 1),
        "packages_summary": {
            "today": len(metrics["packages"].get("closed_today", [])),
            "yesterday": sum(closed_last7[0:1]) if closed_last7 else 0,
            "avg_7_days": closed_avg_7_days,
            "last_7_days": closed_last7,
            "same_weekday_last_week": closed_same_weekday_last_week,
        },
    }


