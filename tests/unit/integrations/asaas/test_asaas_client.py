"""Unit tests for Asaas client helpers."""
import pytest

from integrations.asaas.client import _sanitize_name


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Anne Carissa", "Anne Carissa"),
        ("João da Silva", "João da Silva"),
        ("Maria 123", "Maria"),
        ("João 🌸 da Silva", "João da Silva"),
        ("   José   da   Silva   ", "José da Silva"),
        ("Ana-Maria O'Brien", "Ana Maria O Brien"),
        ("Maria\nFulana", "Maria Fulana"),
        # fallbacks: vazio depois da limpeza vira "Cliente"
        (".", "Cliente"),
        ("🩷", "Cliente"),
        ("🩷🩷🩷", "Cliente"),
        ('"!?@#', "Cliente"),
        ("   ", "Cliente"),
        ("", "Cliente"),
        (None, "Cliente"),
    ],
)
def test_sanitize_name(raw, expected):
    assert _sanitize_name(raw) == expected


def test_sanitize_name_respects_max_length():
    assert _sanitize_name("A" * 200, max_length=50) == "A" * 50
