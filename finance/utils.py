import re
import unicodedata
from typing import Optional

def extract_price(text: str) -> Optional[float]:
    """
    Extrai um valor monetário de uma string de forma robusta.
    Suporta formatos como: R$ 45,00, $45, 45.00, 45,00, 45 reais.
    """
    if not text:
        return None

    # 1. Procura por símbolo de moeda (R$ ou $)
    match = re.search(r'(?:R\$|\$)\s*(\d+(?:[.,]\d{1,2})?)', text, re.IGNORECASE)
    if match:
        return _parse_val(match.group(1))
    
    # 2. Procura por número seguido de "reais" ou "real"
    match = re.search(r'(\d+(?:[.,]\d{1,2})?)\s*(?:reais|real)', text, re.IGNORECASE)
    if match:
        return _parse_val(match.group(1))

    # 3. Procura por números soltos com decimais (ex: 45,00 ou 45.00)
    match = re.search(r'\b(\d+[.,]\d{2})\b', text)
    if match:
        return _parse_val(match.group(1))

    return None

def _parse_val(val_str: str) -> Optional[float]:
    try:
        # Normaliza para o formato float (troca vírgula por ponto)
        clean_val = val_str.replace(',', '.')
        return float(clean_val)
    except (ValueError, AttributeError):
        return None

def resolve_unit_price(title: str, valor_col: Optional[str] = None) -> float:
    """
    Resolve o preço unitário baseado no título e na coluna 'valor'.
    O título tem prioridade se ambos forem diferentes.
    Se um for zero/nulo, o outro é considerado.
    """
    title_price = extract_price(title) or 0.0
    column_price = extract_price(valor_col) or 0.0 if valor_col else 0.0

    if title_price > 0:
        return title_price
    return column_price

def sanitize_filename(text: str) -> str:
    """
    Remove acentos, converte para minúsculas e remove caracteres especiais
    para garantir nomes de arquivos seguros para URLs e sistemas de arquivos.
    """
    if not text:
        return "arquivo"
        
    # 1. Normaliza para decompor caracteres acentuados (ex: 'á' -> 'a' + '´')
    text = unicodedata.normalize('NFKD', text)
    # 2. Converte para ASCII, ignorando o que não for possível (como acentos decompostos)
    text = text.encode('ascii', 'ignore').decode('ascii')
    # 3. Converte para minúsculas e remove espaços das extremidades
    text = text.lower().strip()
    # 4. Substitui qualquer caractere não alfanumérico (exceto ponto, hifen e underscore) por underscore
    text = re.sub(r'[^a-z0-9_.-]', '_', text)
    # 5. Remove underscores duplicados
    text = re.sub(r'_+', '_', text)
    # 6. Remove underscores no início ou fim
    text = text.strip('_')
    
    return text or "arquivo"

def get_pdf_filename_by_id(pkg_id: str) -> str:
    """
    Retorna o nome do PDF baseado apenas no ID do pacote.
    Padrão adotado: estoque_{pkg_id}.pdf
    """
    return f"estoque_{pkg_id}.pdf"

