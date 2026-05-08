"""Compat shim — em sandbox, GoogleDriveClient é só um alias pro LocalImageStorage.

Existe pra que call-sites antigos (`from integrations.google_drive import
GoogleDriveClient`) continuem funcionando sem renomear. O módulo do Drive
real foi removido junto com a dependência google-api-python-client.
"""
from integrations.local_storage import LocalImageStorage as GoogleDriveClient

__all__ = ["GoogleDriveClient"]
