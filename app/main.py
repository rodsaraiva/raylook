"""Compatibility ASGI entrypoint.

Keeps support for running:
`uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
"""

from app import app

__all__ = ["app"]

