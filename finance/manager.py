import json
import os
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from .utils import extract_price, resolve_unit_price

PAYMENTS_FILE = os.path.join(os.environ.get("DATA_DIR", "data"), "payments.json")

class FinanceManager:
    def __init__(self, file_path: str = PAYMENTS_FILE):
        self.file_path = file_path

    def _load(self) -> Dict[str, Any]:
        if not os.path.exists(self.file_path):
            return {"charges": []}
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {"charges": []}

    def _save(self, data: Dict[str, Any]) -> None:
        # Save atomically
        tmp_path = self.file_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        try:
            os.replace(tmp_path, self.file_path)
        except OSError:
            # fallback for windows if file is locked or other issues
            if os.path.exists(self.file_path):
                os.remove(self.file_path)
            os.rename(tmp_path, self.file_path)

    def register_package_confirmation(self, package: Dict[str, Any], confirmed_by: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Gera cobranças (charges) a partir de um pacote confirmado.
        Retorna lista de objetos das cobranças geradas.
        """
        from app.config import settings
        data = self._load()
        charges_in_data = data.get("charges", [])
        
        poll_title = package.get("poll_title", "")
        valor_col = package.get("valor_col")
        price = resolve_unit_price(poll_title, valor_col)
        commission_per_piece = getattr(settings, "COMMISSION_PER_PIECE", 5.0)

        pkg_id = package.get("id")
        confirmed_at = package.get("confirmed_at") or datetime.now(timezone.utc).isoformat()

        new_charges = []

        # Iterar sobre os votos (compradores) dentro do pacote
        for vote in package.get("votes", []):
            qty = vote.get("qty", 0)
            if qty <= 0:
                continue

            charge_id = str(uuid.uuid4())
            subtotal = price * qty
            commission_amount = round(qty * commission_per_piece, 2)
            total_amount = round(subtotal + commission_amount, 2)

            # Tentar pegar o mercadopago_payment_id se já existir
            mercadopago_id = vote.get("mercadopago_payment_id") or vote.get("asaas_payment_id")

            charge = {
                "id": charge_id,
                "package_id": pkg_id,
                "mercadopago_id": mercadopago_id,  # Novo campo para vincular ao Mercado Pago
                "poll_title": poll_title,
                "customer_name": vote.get("name", "Desconhecido"),
                "customer_phone": vote.get("phone", ""),
                "item_price": price,
                "quantity": qty,
                "subtotal": subtotal,
                "commission_percent": 0,
                "commission_amount": commission_amount,
                "total_amount": total_amount,
                "status": "enviando",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "confirmed_at": confirmed_at,
                "confirmed_by": confirmed_by,
                "image": package.get("image"),
                "image_thumb": package.get("image_thumb")
            }
            
            charges_in_data.append(charge)
            new_charges.append(charge)

        data["charges"] = charges_in_data
        self._save(data)
        return new_charges

    def update_mercadopago_id(self, package_id: str, customer_phone: str, mercadopago_id: str) -> bool:
        """Vincula um ID do Mercado Pago a uma cobrança existente."""
        data = self._load()
        charges = data.get("charges", [])
        updated = False
        for c in charges:
            if c.get("package_id") == package_id and c.get("customer_phone") == customer_phone:
                c["mercadopago_id"] = mercadopago_id
                updated = True
                # Não damos break pois pode haver múltiplos (embora não devesse)
        
        if updated:
            self._save(data)
        return updated

    def sync_paid_status(self, paid_mercadopago_ids: List[str]) -> int:
        """
        Atualiza o status de 'pending' para 'paid' para todas as cobranças 
        cujos mercadopago_id estejam na lista de IDs pagos.
        Retorna o número de cobranças atualizadas.
        """
        if not paid_mercadopago_ids:
            return 0
            
        data = self._load()
        charges = data.get("charges", [])
        updated_count = 0
        
        # Converter para set para busca O(1) e garantir que sejam strings
        paid_set = {str(pid) for pid in paid_mercadopago_ids}
        
        for c in charges:
            if c.get("status") in ("pending", "enviando", "erro no envio"):
                mp_id = str(c.get("mercadopago_id")) if c.get("mercadopago_id") else None
                asaas_id = str(c.get("asaas_id")) if c.get("asaas_id") else None
                
                if (mp_id and mp_id in paid_set) or (asaas_id and asaas_id in paid_set):
                    c["status"] = "paid"
                    c["updated_at"] = datetime.now(timezone.utc).isoformat()
                    c["updated_by"] = "payment_sync_service"
                    updated_count += 1
        
        if updated_count > 0:
            self._save(data)
        return updated_count

    def get_pending_mercadopago_ids(self) -> List[str]:
        """Retorna uma lista de IDs do Mercado Pago que ainda não foram marcados como pagos.

        Inclui "pending", "enviando" e "erro no envio", pois o pagamento pode ter sido aprovado
        no Mercado Pago mesmo que o envio no WhatsApp tenha falhado/estivesse em progresso.
        """
        data = self._load()
        return [
            str(c["mercadopago_id"]) 
            for c in data.get("charges", []) 
            if c.get("status") in ("pending", "enviando", "erro no envio") and c.get("mercadopago_id")
        ]

    def list_charges(self) -> List[Dict[str, Any]]:
        """Retorna todas as cobranças com nomes de clientes sincronizados."""
        data = self._load().get("charges", [])
        try:
            from app.services.customer_service import sync_customer_names
            return sync_customer_names(data)
        except Exception:
            return data

    def update_charge_status(
        self,
        charge_id: str,
        status: str,
        updated_by: Optional[str] = None,
        error_detail: Optional[str] = None,
    ) -> bool:
        """
        Atualiza o status de uma cobrança.
        Retorna True se encontrado e atualizado, False caso contrário.
        """
        data = self._load()
        charges = data.get("charges", [])
        
        updated = False
        for charge in charges:
            if charge.get("id") == charge_id:
                charge["status"] = status
                charge["updated_at"] = datetime.now(timezone.utc).isoformat()
                if updated_by:
                    charge["updated_by"] = updated_by
                if status == "pending":
                    # "pending" representa envio confirmado ao cliente.
                    charge["sent_at"] = datetime.now(timezone.utc).isoformat()
                if error_detail is not None:
                    charge["send_error_detail"] = error_detail
                updated = True
                break
        
        if updated:
            self._save(data)
            
        return updated

    def update_charge_status_by_mercadopago_id(
        self,
        mercadopago_id: str,
        status: str,
        updated_by: Optional[str] = None,
        error_detail: Optional[str] = None,
    ) -> int:
        """Atualiza status para todas as cobranças que coincidirem com o mercadopago_id informado.

        Retorna a quantidade de cobranças atualizadas.
        """
        if not mercadopago_id:
            return 0

        data = self._load()
        charges = data.get("charges", [])
        updated_count = 0
        mp_id_str = str(mercadopago_id)

        for charge in charges:
            if str(charge.get("mercadopago_id")) == mp_id_str:
                charge["status"] = status
                charge["updated_at"] = datetime.now(timezone.utc).isoformat()
                if updated_by:
                    charge["updated_by"] = updated_by
                if status == "pending":
                    # "pending" representa envio confirmado ao cliente.
                    charge["sent_at"] = datetime.now(timezone.utc).isoformat()
                if error_detail is not None:
                    charge["send_error_detail"] = error_detail
                updated_count += 1

        if updated_count > 0:
            self._save(data)

        return updated_count

    def delete_charge(self, charge_id: str) -> bool:
        """
        Remove uma cobrança permanentemente do arquivo.
        Retorna True se encontrado e removido, False caso contrário.
        """
        data = self._load()
        charges = data.get("charges", [])
        
        new_charges = [c for c in charges if c.get("id") != charge_id]
        
        if len(new_charges) < len(charges):
            data["charges"] = new_charges
            self._save(data)
            return True
            
        return False
