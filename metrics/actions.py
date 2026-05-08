from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional

try:
    from finance.manager import FinanceManager
except ImportError:
    FinanceManager = None


class PackageAction(ABC):
    def __init__(self, pkg_id: str, user: Optional[str] = None):
        self.pkg_id = pkg_id
        self.user = user

    @abstractmethod
    def execute(self, metrics: dict) -> dict:
        """Execute the action against the metrics dict and return the modified metrics."""
        raise NotImplementedError()


class ConfirmAction(PackageAction):
    def __init__(self, pkg_id: str, user: Optional[str] = None):
        super().__init__(pkg_id, user)
        self.confirmed_pkg = None

    def execute(self, metrics: dict) -> dict:
        pkgs = metrics.setdefault("votos", {}).setdefault("packages", {})
        closed = pkgs.setdefault("closed_today", [])
        idx = next((i for i, p in enumerate(closed) if p.get("id") == self.pkg_id), None)
        if idx is None:
            raise KeyError("not_found")
        pkg = closed.pop(idx)
        pkg["status"] = "confirmed"
        pkg.pop("rejected", None)
        # set confirmed timestamp
        try:
            pkg["confirmed_at"] = datetime.now(timezone.utc).isoformat()
        except Exception:
            pkg["confirmed_at"] = datetime.utcnow().isoformat()
        if self.user:
            pkg["confirmed_by"] = self.user
        
        # Armazenamos o pacote confirmado no objeto da ação para que o chamador possa salvar separadamente
        self.confirmed_pkg = pkg
        
        # REMOVIDO: Não adicionamos mais ao confirmed_today do dicionário de métricas principal
        # Os pacotes confirmados agora vivem em app/services/confirmed_packages_service.py
        
        pkgs["closed_today"] = closed
        # Limpar confirmed_today do dashboard_metrics se existir para não crescer indefinidamente
        pkgs["confirmed_today"] = []
        return metrics


class RejectAction(PackageAction):
    def __init__(self, pkg_id: str, user: Optional[str] = None):
        super().__init__(pkg_id, user)
        self.rejected_pkg = None

    def execute(self, metrics: dict) -> dict:
        pkgs = metrics.setdefault("votos", {}).setdefault("packages", {})
        closed = pkgs.setdefault("closed_today", [])
        
        idx = next((i for i, p in enumerate(closed) if p.get("id") == self.pkg_id), None)
        if idx is None:
            raise KeyError("not_found")
        
        pkg = closed.pop(idx)
        pkg["status"] = "rejected"
        pkg["rejected"] = True
        try:
            pkg["rejected_at"] = datetime.now(timezone.utc).isoformat()
        except Exception:
            pkg["rejected_at"] = datetime.utcnow().isoformat()
        if self.user:
            pkg["rejected_by"] = self.user
            
        # Armazenamos o pacote rejeitado para que o chamador possa salvar separadamente
        self.rejected_pkg = pkg
        
        # REMOVIDO: Não adicionamos mais ao rejected_today do dicionário de métricas principal
        # Os pacotes rejeitados agora vivem em app/services/rejected_packages_service.py
        
        pkgs["closed_today"] = closed
        # Limpar rejected_today do dashboard_metrics se existir para não crescer indefinidamente
        pkgs["rejected_today"] = []
        return metrics


class RevertAction(PackageAction):
    def execute(self, metrics: dict) -> dict:
        # Nota: De acordo com a nova regra de negócio, uma vez confirmado, o pacote não volta.
        # Como os pacotes confirmados agora vivem em um arquivo separado, esta ação
        # não encontrará o pacote no dicionário de métricas principal.
        raise RuntimeError("revert_not_allowed")

