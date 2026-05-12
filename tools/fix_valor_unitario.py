"""
Corrige produtos.valor_unitario = 5.0 (capturado errado da ASSESSORIA)
e recalcula pacote_clientes com os valores reais.

Uso:
    python3 tools/fix_valor_unitario.py           # dry-run (só mostra)
    python3 tools/fix_valor_unitario.py --apply   # aplica no banco
"""
import sys
import os
import psycopg2

# Adiciona raiz do projeto ao path para importar finance.utils
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from finance.utils import extract_price

COMMISSION_PER_PIECE = 5.0
DRY_RUN = "--apply" not in sys.argv

DB_HOST = os.getenv("DB_HOST", "10.0.3.3")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "raylook")
DB_USER = os.getenv("DB_USER", "raylook_owner")
DB_PASS = os.getenv("DB_PASS", "809a9787befa23a73322a12164bb99d4925b632c1292cc9a")


def recalc(unit_price: float, qty: int) -> dict:
    subtotal = round(unit_price * qty, 2)
    commission_amount = round(qty * COMMISSION_PER_PIECE, 2)
    total_amount = round(subtotal + commission_amount, 2)
    return {"subtotal": subtotal, "commission_amount": commission_amount, "total_amount": total_amount}


def main():
    conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS)
    cur = conn.cursor()

    # --- Passo 1: identificar produtos a corrigir ---
    cur.execute("SELECT id, nome, valor_unitario FROM produtos WHERE valor_unitario = 5.0")
    rows = cur.fetchall()

    product_updates: list[tuple] = []  # (id, nome_trecho, old_price, new_price)
    for prod_id, nome, old_price in rows:
        new_price = extract_price(nome or "")
        if new_price and new_price != old_price and new_price > 0:
            product_updates.append((prod_id, (nome or "")[:60].replace("\n", " "), old_price, new_price))

    print(f"\n{'[DRY RUN] ' if DRY_RUN else ''}Produtos a corrigir: {len(product_updates)} de {len(rows)}\n")
    for prod_id, nome_trecho, old, new in product_updates[:20]:
        print(f"  {prod_id[:8]}…  {old:6.2f} → {new:6.2f}  [{nome_trecho}]")
    if len(product_updates) > 20:
        print(f"  … e mais {len(product_updates) - 20} produtos")

    sem_preco = len(rows) - len(product_updates)
    if sem_preco:
        print(f"\n  {sem_preco} produto(s) com valor_unitario=5.0 sem preço extraível no nome — mantidos.")

    # --- Passo 2: identificar pacote_clientes afetados ---
    affected_ids = [r[0] for r in product_updates]
    if affected_ids:
        placeholders = ",".join(["%s"] * len(affected_ids))
        cur.execute(f"""
            SELECT pc.id, pc.qty, pc.unit_price, pc.subtotal, pc.commission_amount, pc.total_amount,
                   p.id as produto_id
            FROM pacote_clientes pc
            JOIN pacotes pak ON pak.id = pc.pacote_id
            JOIN enquetes e ON e.id = pak.enquete_id
            JOIN produtos p ON p.id = e.produto_id
            WHERE p.id IN ({placeholders})
        """, affected_ids)
        pc_rows = cur.fetchall()

        # Mapeia produto_id → novo preço
        price_map = {r[0]: r[3] for r in product_updates}

        pc_updates = []
        for pc_id, qty, old_unit, old_sub, old_comm, old_total, prod_id in pc_rows:
            new_unit = price_map.get(prod_id)
            if new_unit is None:
                continue
            fin = recalc(new_unit, qty)
            pc_updates.append((pc_id, qty, old_unit, old_total, new_unit, fin["total_amount"], fin))

        print(f"\n{'[DRY RUN] ' if DRY_RUN else ''}pacote_clientes a recalcular: {len(pc_updates)}\n")
        for pc_id, qty, old_unit, old_total, new_unit, new_total, _ in pc_updates[:10]:
            print(f"  pc {pc_id[:8]}…  qty={qty}  {old_unit:.2f}×{qty}=old_total={old_total:.2f}"
                  f"  →  {new_unit:.2f}×{qty}=new_total={new_total:.2f}")
        if len(pc_updates) > 10:
            print(f"  … e mais {len(pc_updates) - 10} registros")
    else:
        pc_updates = []
        print("\nNenhum pacote_clientes afetado.")

    if DRY_RUN:
        print("\n[DRY RUN] Nenhuma alteração aplicada. Use --apply para efetivar.")
        conn.close()
        return

    # --- Aplicar ---
    print("\nAplicando dentro de transação...")
    cur.execute("BEGIN")
    try:
        for prod_id, _, _, new_price in product_updates:
            cur.execute("UPDATE produtos SET valor_unitario = %s WHERE id = %s", (new_price, prod_id))

        for pc_id, _, _, _, new_unit, _, fin in pc_updates:
            cur.execute("""
                UPDATE pacote_clientes
                SET unit_price = %s, subtotal = %s, commission_amount = %s, total_amount = %s
                WHERE id = %s
            """, (new_unit, fin["subtotal"], fin["commission_amount"], fin["total_amount"], pc_id))

        conn.commit()
        print(f"\nOK — {len(product_updates)} produtos e {len(pc_updates)} pacote_clientes atualizados.")
    except Exception as exc:
        conn.rollback()
        print(f"\nERRO — rollback executado: {exc}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
