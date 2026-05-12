from typing import Any, List, Optional
import json
import logging
import os

from dotenv import load_dotenv

# load .env into environment for fallback mode
load_dotenv()

_USE_PYDANTIC = False
try:
    from pydantic import BaseSettings, ConfigDict, Field, field_validator  # type: ignore

    _USE_PYDANTIC = True
except Exception:
    try:
        from pydantic_settings import BaseSettings  # type: ignore
        from pydantic import ConfigDict, Field, field_validator  # type: ignore

        _USE_PYDANTIC = True
    except Exception:
        _USE_PYDANTIC = False


def _parse_allowed_origins_value(value: Any) -> Any:
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("["):
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                pass
        return [item.strip() for item in raw.split(",") if item.strip()]
    return value


if _USE_PYDANTIC:
    class Settings(BaseSettings):
        model_config = ConfigDict(extra="ignore")

        # Sandbox global do raylook. True = nenhuma chamada externa real
        # (Asaas/Resend/etc.) chega na API real; clients logam e retornam dummy.
        # Default True pra que rodar sem .env nunca toque na prod da Alana.
        RAYLOOK_SANDBOX: bool = True

        BASEROW_API_URL: str = "https://base.v4smc.com"
        BASEROW_API_TOKEN: Optional[str] = None
        BASEROW_TABLE_ENQUETES: str = "18"
        BASEROW_TABLE_VOTOS: str = "17"
        BASEROW_TABLE_PAYMENTS: str = "19"
        APP_HOST: str = "0.0.0.0"
        APP_PORT: int = 8000
        LOG_LEVEL: str = "INFO"
        ALLOWED_ORIGINS: List[str] = Field(default_factory=lambda: ["*"])
        WELCOME_NAME: str = "raylook"

        AS_AASAAS_URL: str = os.getenv("AS_AASAAS_URL", "https://api.asaas.com/v3/")
        AS_AASAAS_TOKEN: Optional[str] = None

        ESTOQUE_PHONE_NUMBER: str = "5562993353390"
        COMMISSION_PER_PIECE: float = 5.0

        TEST_MODE: bool = False
        TEST_PHONE_NUMBER: Optional[str] = None
        STAGING_DRY_RUN: bool = False
        BASEROW_COMPAT_WRITE: bool = True

        MP_PUBLIC_KEY_TEST: Optional[str] = None
        MP_ACCESS_TOKEN_TEST: Optional[str] = None
        MP_PUBLIC_KEY_PROD: Optional[str] = None
        MP_ACCESS_TOKEN_PROD: Optional[str] = None

        METRICS_SOURCE: str = "baserow"
        METRICS_MIN_DATE: Optional[str] = None

        SUPABASE_URL: Optional[str] = None
        SUPABASE_ANON_KEY: Optional[str] = None
        SUPABASE_SERVICE_ROLE_KEY: Optional[str] = None
        SUPABASE_ACCESS_TOKEN: Optional[str] = None
        SUPABASE_PROJECT_REF: Optional[str] = None
        SUPABASE_SCHEMA: str = "public"
        SUPABASE_REST_PATH: str = "/rest/v1"
        SUPABASE_DOMAIN_ENABLED: bool = False

        # Backend de persistência. "sqlite" (default) usa data/raylook.db
        # via app.services.sqlite_service; "supabase" usa PostgREST real.
        # Em RAYLOOK_SANDBOX=true qualquer valor é coagido pra "sqlite" no
        # SupabaseRestClient.from_settings().
        DATA_BACKEND: str = "sqlite"

        ADHOC_PACKAGES_ENABLED: bool = False

        WHAPI_TOKEN: Optional[str] = None
        WHAPI_API_URL: str = os.getenv("WHAPI_API_URL") or os.getenv("WHAPI_URL", "https://gate.whapi.cloud")

        WHATSAPP_WEBHOOK_ENABLED: bool = True
        WHATSAPP_WEBHOOK_SECRET: Optional[str] = os.getenv("WHATSAPP_WEBHOOK_SECRET") or os.getenv("WHATSAPP_VERIFY_TOKEN")
        OFFICIAL_GROUP_CHAT_ID: Optional[str] = None
        TEST_GROUP_CHAT_ID: Optional[str] = None
        AUTHORIZED_GROUP_1: Optional[str] = None
        AUTHORIZED_GROUP_2: Optional[str] = None

        @field_validator("ALLOWED_ORIGINS", mode="before")
        @classmethod
        def parse_allowed_origins(cls, value: Any) -> Any:
            return _parse_allowed_origins_value(value)

        @property
        def baserow_api_url(self) -> str:
            url = self.BASEROW_API_URL.rstrip("/")
            if url.startswith("http://"):
                url = "https://" + url[7:]
            return url

        @property
        def mp_access_token(self) -> Optional[str]:
            if self.TEST_MODE and self.MP_ACCESS_TOKEN_TEST:
                return self.MP_ACCESS_TOKEN_TEST
            return self.MP_ACCESS_TOKEN_PROD or self.MP_ACCESS_TOKEN_TEST

        @property
        def mp_public_key(self) -> Optional[str]:
            if self.TEST_MODE and self.MP_PUBLIC_KEY_TEST:
                return self.MP_PUBLIC_KEY_TEST
            return self.MP_PUBLIC_KEY_PROD or self.MP_PUBLIC_KEY_TEST


    settings = Settings()
else:
    logging.getLogger("raylook").warning(
        "Pydantic BaseSettings not available; using fallback Settings (env vars only)."
    )

    class Settings:
        RAYLOOK_SANDBOX: bool = os.getenv("RAYLOOK_SANDBOX", "true").strip().lower() not in ("0", "false", "no")

        BASEROW_API_URL: str = os.getenv("BASEROW_API_URL", "https://base.v4smc.com")
        BASEROW_API_TOKEN: Optional[str] = os.getenv("BASEROW_API_TOKEN")
        BASEROW_TABLE_ENQUETES: str = os.getenv("BASEROW_TABLE_ENQUETES", "18")
        BASEROW_TABLE_VOTOS: str = os.getenv("BASEROW_TABLE_VOTOS", "17")
        BASEROW_TABLE_PAYMENTS: str = os.getenv("BASEROW_TABLE_PAYMENTS", "19")
        APP_HOST: str = os.getenv("APP_HOST", "0.0.0.0")
        APP_PORT: int = int(os.getenv("APP_PORT", "8000"))
        LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
        ALLOWED_ORIGINS: List[str] = _parse_allowed_origins_value(os.getenv("ALLOWED_ORIGINS", "*"))
        WELCOME_NAME: str = os.getenv("WELCOME_NAME", "raylook")

        AS_AASAAS_URL: str = os.getenv("AS_AASAAS_URL", "https://api.asaas.com/v3/")
        AS_AASAAS_TOKEN: Optional[str] = os.getenv("AS_AASAAS_TOKEN")

        ESTOQUE_PHONE_NUMBER: str = os.getenv("ESTOQUE_PHONE_NUMBER", "5562993353390")
        COMMISSION_PER_PIECE: float = float(os.getenv("COMMISSION_PER_PIECE", "5"))

        TEST_MODE: bool = os.getenv("TEST_MODE", "").lower() == "true"
        TEST_PHONE_NUMBER: Optional[str] = os.getenv("TEST_PHONE_NUMBER")
        STAGING_DRY_RUN: bool = os.getenv("STAGING_DRY_RUN", "").lower() == "true"
        BASEROW_COMPAT_WRITE: bool = os.getenv("BASEROW_COMPAT_WRITE", "true").lower() == "true"

        MP_PUBLIC_KEY_TEST: Optional[str] = os.getenv("MP_PUBLIC_KEY_TEST")
        MP_ACCESS_TOKEN_TEST: Optional[str] = os.getenv("MP_ACCESS_TOKEN_TEST")
        MP_PUBLIC_KEY_PROD: Optional[str] = os.getenv("MP_PUBLIC_KEY_PROD")
        MP_ACCESS_TOKEN_PROD: Optional[str] = os.getenv("MP_ACCESS_TOKEN_PROD")

        METRICS_SOURCE: str = os.getenv("METRICS_SOURCE", "baserow")
        METRICS_MIN_DATE: Optional[str] = os.getenv("METRICS_MIN_DATE")

        SUPABASE_URL: Optional[str] = os.getenv("SUPABASE_URL")
        SUPABASE_ANON_KEY: Optional[str] = os.getenv("SUPABASE_ANON_KEY")
        SUPABASE_SERVICE_ROLE_KEY: Optional[str] = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        SUPABASE_ACCESS_TOKEN: Optional[str] = os.getenv("SUPABASE_ACCESS_TOKEN")
        SUPABASE_PROJECT_REF: Optional[str] = os.getenv("SUPABASE_PROJECT_REF")
        SUPABASE_SCHEMA: str = os.getenv("SUPABASE_SCHEMA", "public")
        SUPABASE_REST_PATH: str = os.getenv("SUPABASE_REST_PATH", "/rest/v1")
        SUPABASE_DOMAIN_ENABLED: bool = os.getenv("SUPABASE_DOMAIN_ENABLED", "").lower() == "true"

        DATA_BACKEND: str = os.getenv("DATA_BACKEND", "sqlite").strip().lower()

        ADHOC_PACKAGES_ENABLED: bool = os.getenv("ADHOC_PACKAGES_ENABLED", "").lower() == "true"

        WHAPI_TOKEN: Optional[str] = os.getenv("WHAPI_TOKEN")
        WHAPI_API_URL: str = os.getenv("WHAPI_API_URL") or os.getenv("WHAPI_URL", "https://gate.whapi.cloud")

        WHATSAPP_WEBHOOK_ENABLED: bool = os.getenv("WHATSAPP_WEBHOOK_ENABLED", "true").lower() == "true"
        WHATSAPP_WEBHOOK_SECRET: Optional[str] = os.getenv("WHATSAPP_WEBHOOK_SECRET") or os.getenv("WHATSAPP_VERIFY_TOKEN")
        OFFICIAL_GROUP_CHAT_ID: Optional[str] = os.getenv("OFFICIAL_GROUP_CHAT_ID")
        TEST_GROUP_CHAT_ID: Optional[str] = os.getenv("TEST_GROUP_CHAT_ID")
        AUTHORIZED_GROUP_1: Optional[str] = os.getenv("AUTHORIZED_GROUP_1")
        AUTHORIZED_GROUP_2: Optional[str] = os.getenv("AUTHORIZED_GROUP_2")

        @property
        def baserow_api_url(self) -> str:
            url = self.BASEROW_API_URL.rstrip("/")
            if url.startswith("http://"):
                url = "https://" + url[7:]
            return url

        @property
        def mp_access_token(self) -> Optional[str]:
            if self.TEST_MODE and self.MP_ACCESS_TOKEN_TEST:
                return self.MP_ACCESS_TOKEN_TEST
            return self.MP_ACCESS_TOKEN_PROD or self.MP_ACCESS_TOKEN_TEST

        @property
        def mp_public_key(self) -> Optional[str]:
            if self.TEST_MODE and self.MP_PUBLIC_KEY_TEST:
                return self.MP_PUBLIC_KEY_TEST
            return self.MP_PUBLIC_KEY_PROD or self.MP_PUBLIC_KEY_TEST


    settings = Settings()
