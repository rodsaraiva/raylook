import re
from typing import Optional, TypedDict

_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FFFF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U0001F251"
    "]+",
    flags=re.UNICODE,
)


class ParsedEnquete(TypedDict):
    item: Optional[str]
    tecido: Optional[str]
    valor: Optional[float]


def _normalize(text: str) -> str:
    """Remove emojis, asteriscos, underlines e colapsa espaços extras por linha."""
    text = _EMOJI_RE.sub("", text)
    text = re.sub(r"[*_]", "", text)
    # colapsa múltiplos espaços em cada linha
    lines = [re.sub(r" {2,}", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(lines)


def _extract_field(normalized: str, label: str) -> Optional[str]:
    """Extrai valor após LABEL= (case-insensitive) até fim da linha."""
    pattern = re.compile(
        rf"(?i){label}\s*=\s*\$?\s*([^\n]+)"
    )
    m = pattern.search(normalized)
    if not m:
        return None
    val = m.group(1).strip()
    return val if val else None


def _parse_valor(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    # pega apenas o primeiro número (pode ter lixo depois)
    m = re.search(r"(\d+(?:[.,]\d+)?)", raw)
    if not m:
        return None
    num = m.group(1).replace(",", ".")
    try:
        return float(num)
    except ValueError:
        return None


def parse_enquete_title(title: str) -> ParsedEnquete:
    if not title or not title.strip():
        return {"item": None, "tecido": None, "valor": None}

    normalized = _normalize(title)

    item_raw = _extract_field(normalized, "ITEM")
    tecido_raw = _extract_field(normalized, "TECIDO")
    valor_raw = _extract_field(normalized, "VALOR")

    # Formato estruturado: pelo menos ITEM= ou VALOR= encontrado
    if item_raw is not None or valor_raw is not None:
        item = item_raw.upper() if item_raw else None
        tecido = tecido_raw.upper() if tecido_raw else None
        valor = _parse_valor(valor_raw)
        return {"item": item, "tecido": tecido, "valor": valor}

    # Formato curto: tenta extrair preço com $N e resto vira item
    m = re.search(r"\$\s*(\d+(?:[.,]\d+)?)", title)
    if m:
        valor = _parse_valor(m.group(1))
        # Remove o trecho do preço do título pra obter item
        item_text = re.sub(r"\$\s*\d+(?:[.,]\d+)?", "", title).strip()
        item_text = re.sub(r" {2,}", " ", item_text).strip()
        item = item_text.upper() if item_text else None
        return {"item": item, "tecido": None, "valor": valor}

    return {"item": None, "tecido": None, "valor": None}
