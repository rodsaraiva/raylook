"""Rejected packages service — no-op (F-051 pattern).

Pacotes rejeitados vivem no Postgres (status='cancelled').
O fetch_package_lists_for_metrics já popula rejected_today.
Este service existia pra manter um JSON legado que não é mais necessário.
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("raylook.rejected_packages_service")


def load_rejected_packages() -> List[Dict[str, Any]]:
    """No-op — dados vêm do Postgres via fetch_package_lists_for_metrics."""
    return []


def save_rejected_packages(packages: List[Dict[str, Any]]) -> None:
    """No-op."""
    pass


def add_rejected_package(package: Dict[str, Any]) -> None:
    """No-op — o caller já atualiza pacotes.status='cancelled' no Postgres."""
    logger.debug("add_rejected_package: no-op (F-051)")


def get_rejected_package(pkg_id: str) -> Optional[Dict[str, Any]]:
    """No-op."""
    return None


def merge_rejected_into_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """No-op — rejected_today já é preenchido pelo fetch_package_lists_for_metrics."""
    return metrics
