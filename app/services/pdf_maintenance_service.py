import os
import json
import logging
from pathlib import Path
from finance.utils import sanitize_filename

logger = logging.getLogger("raylook.pdf_maintenance")

def fix_pdf_filenames_and_metadata():
    """
    Renomeia arquivos PDF no disco para o formato sanitizado e atualiza
    os metadados nos arquivos JSON (confirmed_packages e packages_state).
    """
    pdf_dir = Path("etiquetas_estoque")
    if not pdf_dir.exists():
        logger.info("Diretório de PDFs não encontrado, pulando manutenção.")
        return

    # 1. Renomear arquivos físicos no disco
    for pdf in pdf_dir.glob("*.pdf"):
        new_stem = sanitize_filename(pdf.stem)
        new_name = f"{new_stem}.pdf"
        
        if new_name != pdf.name:
            target = pdf.parent / new_name
            if not target.exists():
                logger.info(f"Renomeando arquivo no disco: {pdf.name} -> {new_name}")
                try:
                    pdf.rename(target)
                except Exception as e:
                    logger.error(f"Erro ao renomear arquivo {pdf.name}: {e}")
            else:
                logger.warning(f"Arquivo sanitizado {new_name} já existe. Removendo duplicata não sanitizada {pdf.name}.")
                try:
                    pdf.unlink()
                except Exception:
                    pass

    # 2. Atualizar arquivos JSON
    json_files = ["data/confirmed_packages.json", "data/packages_state.json"]
    
    for file_rel in json_files:
        json_path = Path(file_rel)
        if not json_path.exists():
            continue
            
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            updated = False
            
            # Caso confirmed_packages.json (Lista)
            if isinstance(data, list):
                for pkg in data:
                    old_name = pkg.get("pdf_file_name")
                    if old_name:
                        new_name = f"{sanitize_filename(Path(old_name).stem)}.pdf"
                        if new_name != old_name:
                            pkg["pdf_file_name"] = new_name
                            updated = True
                            logger.info(f"Atualizando metadados em {json_path.name}: {old_name} -> {new_name}")
            
            # Caso packages_state.json (Dicionário)
            elif isinstance(data, dict):
                for pkg_id, pkg in data.items():
                    old_name = pkg.get("pdf_file_name")
                    if old_name:
                        new_name = f"{sanitize_filename(Path(old_name).stem)}.pdf"
                        if new_name != old_name:
                            pkg["pdf_file_name"] = new_name
                            updated = True
                            logger.info(f"Atualizando metadados em {json_path.name} [{pkg_id}]: {old_name} -> {new_name}")

            if updated:
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                logger.info(f"Arquivo {json_path.name} persistido com nomes sanitizados.")
                
        except Exception as e:
            logger.error(f"Erro ao processar manutenção de metadados em {json_path}: {e}")

def run_maintenance():
    """Wrapper para execução segura."""
    try:
        fix_pdf_filenames_and_metadata()
    except Exception as e:
        logger.error(f"Falha na manutenção de PDFs: {e}")
