import pytest
from app.services.enquete_title_parser import parse_enquete_title


def test_structured_with_spaces():
    """Formato estruturado com espaços entre = e valor."""
    title = (
        "📝 *ITEM=* BLUSAS\n💰 *VALOR=$* 26\n🔖 *TECIDO=* MODAL\n"
        "📏 *TAMANHOS=* M G GG\n📍  *CATEGORIA=* IMPORTADO"
    )
    result = parse_enquete_title(title)
    assert result["item"] == "BLUSAS"
    assert result["tecido"] == "MODAL"
    assert result["valor"] == 26.0
    assert result["tamanho"] == "M G GG"
    assert result["categoria"] == "IMPORTADO"


def test_structured_without_spaces():
    """Formato estruturado sem espaços entre = e valor."""
    title = "📝 *ITEM=*BLUSA\n💰 *VALOR=$*36\n🔖 *TECIDO=*COTON ALGODÃO"
    result = parse_enquete_title(title)
    assert result["item"] == "BLUSA"
    assert result["tecido"] == "COTON ALGODÃO"
    assert result["valor"] == 36.0


def test_valor_with_comma():
    """Valor com vírgula deve ser tratado como decimal."""
    title = "💰 *VALOR=$* 26,50\n📝 *ITEM=* REGATA"
    result = parse_enquete_title(title)
    assert result["valor"] == 26.5
    assert result["item"] == "REGATA"


def test_short_format():
    """Formato curto: extrai preço e usa resto como item."""
    title = "$15 Canelado em Ribana TAM U"
    result = parse_enquete_title(title)
    assert result["item"] == "CANELADO EM RIBANA TAM U"
    assert result["valor"] == 15.0
    assert result["tecido"] is None


def test_garbage_title():
    """Título sem padrão reconhecível retorna tudo None."""
    title = "Teste do sistema"
    result = parse_enquete_title(title)
    assert result["item"] is None
    assert result["tecido"] is None
    assert result["valor"] is None


def test_empty_string():
    """String vazia retorna tudo None."""
    result = parse_enquete_title("")
    assert result["item"] is None
    assert result["tecido"] is None
    assert result["valor"] is None
