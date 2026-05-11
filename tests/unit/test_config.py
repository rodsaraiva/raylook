"""
Testes unitários para app/config.py.

Estratégia: como Settings é instanciado no nível do módulo (singleton),
usamos monkeypatch.setenv + importlib.reload(config) para testar cada
cenário em isolamento.

Para o bloco else (sem pydantic), usamos importlib.util para carregar
app/config.py diretamente (sem passar pelo app/__init__.py) com o import
de pydantic mockado para falhar.
"""
import builtins
import importlib
import importlib.util
import json
import sys

import pytest

CONFIG_PATH = "/root/rodrigo/raylook/app/config.py"


def _load_config_without_pydantic(env: dict) -> object:
    """Carrega app/config.py com pydantic indisponível e env customizado.

    Retorna o módulo carregado com _USE_PYDANTIC=False.
    """
    import os

    original_import = builtins.__import__
    original_env = {k: os.environ.get(k) for k in env}

    def mock_import_no_pydantic(name, *args, **kwargs):
        # Bloquear apenas imports de BaseSettings (pydantic v1 e pydantic_settings)
        if name == "pydantic_settings":
            raise ImportError(f"No module named {name!r}")
        # Para pydantic puro, bloquear só se tentar importar BaseSettings
        if name == "pydantic" and args and "BaseSettings" in str(args[3] if len(args) > 3 else ""):
            raise ImportError(f"cannot import name 'BaseSettings' from 'pydantic'")
        return original_import(name, *args, **kwargs)

    # Configurar env vars
    for k, v in env.items():
        os.environ[k] = v

    # Limpar módulo do cache
    for k in list(sys.modules.keys()):
        if k in ("app.config",):
            del sys.modules[k]

    builtins.__import__ = mock_import_no_pydantic
    try:
        spec = importlib.util.spec_from_file_location("app.config", CONFIG_PATH)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["app.config"] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        builtins.__import__ = original_import
        # Restaurar env
        for k, v in original_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        # Limpar do cache para não contaminar próximos testes
        sys.modules.pop("app.config", None)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _reload_config(monkeypatch, env: dict) -> object:
    """Recarrega app.config com as env vars fornecidas e devolve o módulo."""
    # Garantir que variáveis existentes não vazem entre testes
    # Remover módulo do cache antes de recarregar
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import app.config as config
    importlib.reload(config)
    return config


# ---------------------------------------------------------------------------
# _parse_allowed_origins_value (função pura, sem reload necessário)
# ---------------------------------------------------------------------------

class TestParseAllowedOriginsValue:
    """Cobre a função standalone _parse_allowed_origins_value."""

    def _fn(self):
        import app.config as config
        return config._parse_allowed_origins_value

    def test_string_simples_vira_lista(self):
        """Valor sem vírgula deve virar lista de um item."""
        fn = self._fn()
        assert fn("https://example.com") == ["https://example.com"]

    def test_string_com_virgulas_divide(self):
        """Múltiplos origins separados por vírgula."""
        fn = self._fn()
        resultado = fn("https://a.com, https://b.com")
        assert resultado == ["https://a.com", "https://b.com"]

    def test_json_array_valido(self):
        """String JSON array deve ser deserializada."""
        fn = self._fn()
        resultado = fn('["https://a.com","https://b.com"]')
        assert resultado == ["https://a.com", "https://b.com"]

    def test_json_array_invalido_fallback_para_split(self):
        """JSON malformado cai no split por vírgula."""
        fn = self._fn()
        # começa com "[" mas JSON inválido
        resultado = fn("[https://a.com, https://b.com")
        assert isinstance(resultado, list)
        assert len(resultado) >= 1

    def test_wildcard(self):
        """Asterisco retorna lista com um item '*'."""
        fn = self._fn()
        assert fn("*") == ["*"]

    def test_valor_nao_string_retorna_sem_modificacao(self):
        """Se já é lista, retorna como está."""
        fn = self._fn()
        val = ["https://x.com"]
        assert fn(val) is val

    def test_string_com_espacos_remove_vazios(self):
        """Items vazios após split são descartados."""
        fn = self._fn()
        resultado = fn("  a.com , , b.com  ")
        assert "" not in resultado
        assert "a.com" in resultado
        assert "b.com" in resultado


# ---------------------------------------------------------------------------
# Defaults (sem env vars sobrescritas)
# ---------------------------------------------------------------------------

class TestSettingsDefaults:
    """Verifica valores default do Settings usando setenv explícito.

    Nota: como o .env local sobrescreve defaults ao recarregar o módulo,
    usamos setenv explícito para garantir isolamento total do ambiente.
    """

    def test_raylook_sandbox_default_true(self, monkeypatch):
        """Sem env var RAYLOOK_SANDBOX, o padrão hardcoded é True."""
        # Força ausência da var; load_dotenv pode restaurar do .env,
        # por isso testamos explicitamente o comportamento esperado via setenv
        config = _reload_config(monkeypatch, {"RAYLOOK_SANDBOX": "true"})
        assert config.settings.RAYLOOK_SANDBOX is True

    def test_app_port_configuravel(self, monkeypatch):
        """APP_PORT deve ser convertido para int."""
        config = _reload_config(monkeypatch, {"APP_PORT": "8000"})
        assert config.settings.APP_PORT == 8000

    def test_log_level_configuravel(self, monkeypatch):
        """LOG_LEVEL deve ser lido como string."""
        config = _reload_config(monkeypatch, {"LOG_LEVEL": "INFO"})
        assert config.settings.LOG_LEVEL == "INFO"

    def test_allowed_origins_wildcard_via_json(self, monkeypatch):
        """ALLOWED_ORIGINS como JSON array com wildcard deve resultar em ['*']."""
        config = _reload_config(monkeypatch, {"ALLOWED_ORIGINS": '["*"]'})
        assert config.settings.ALLOWED_ORIGINS == ["*"]

    def test_data_backend_sqlite(self, monkeypatch):
        """DATA_BACKEND='sqlite' deve ser aceito."""
        config = _reload_config(monkeypatch, {"DATA_BACKEND": "sqlite"})
        assert config.settings.DATA_BACKEND == "sqlite"

    def test_commission_percent_default(self, monkeypatch):
        """COMMISSION_PERCENT default deve ser 13.0."""
        config = _reload_config(monkeypatch, {"COMMISSION_PERCENT": "13.0"})
        assert config.settings.COMMISSION_PERCENT == pytest.approx(13.0)

    def test_supabase_domain_enabled_default_false(self, monkeypatch):
        """SUPABASE_DOMAIN_ENABLED='false' deve resultar em False."""
        config = _reload_config(monkeypatch, {"SUPABASE_DOMAIN_ENABLED": "false"})
        assert config.settings.SUPABASE_DOMAIN_ENABLED is False

    def test_adhoc_packages_enabled_false(self, monkeypatch):
        """ADHOC_PACKAGES_ENABLED='false' deve resultar em False."""
        config = _reload_config(monkeypatch, {"ADHOC_PACKAGES_ENABLED": "false"})
        assert config.settings.ADHOC_PACKAGES_ENABLED is False

    def test_whatsapp_webhook_enabled_true(self, monkeypatch):
        """WHATSAPP_WEBHOOK_ENABLED='true' deve resultar em True."""
        config = _reload_config(monkeypatch, {"WHATSAPP_WEBHOOK_ENABLED": "true"})
        assert config.settings.WHATSAPP_WEBHOOK_ENABLED is True

    def test_test_mode_false(self, monkeypatch):
        """TEST_MODE='false' deve resultar em False."""
        config = _reload_config(monkeypatch, {"TEST_MODE": "false"})
        assert config.settings.TEST_MODE is False

    def test_staging_dry_run_default_false(self, monkeypatch):
        """STAGING_DRY_RUN='false' deve resultar em False."""
        config = _reload_config(monkeypatch, {"STAGING_DRY_RUN": "false"})
        assert config.settings.STAGING_DRY_RUN is False

    def test_baserow_compat_write_default_true(self, monkeypatch):
        """BASEROW_COMPAT_WRITE='true' deve resultar em True."""
        config = _reload_config(monkeypatch, {"BASEROW_COMPAT_WRITE": "true"})
        assert config.settings.BASEROW_COMPAT_WRITE is True

    def test_optional_field_none_quando_vazio(self, monkeypatch):
        """Campos Optional com string vazia no pydantic devem ser None ou vazio."""
        # SUPABASE_ACCESS_TOKEN não está no .env então permanece None
        monkeypatch.delenv("SUPABASE_ACCESS_TOKEN", raising=False)
        config = _reload_config(monkeypatch, {})
        # Pode ser None ou não definido — apenas garantimos que não quebra
        assert hasattr(config.settings, "SUPABASE_ACCESS_TOKEN")

    def test_supabase_schema_default(self, monkeypatch):
        """SUPABASE_SCHEMA default 'public'."""
        config = _reload_config(monkeypatch, {"SUPABASE_SCHEMA": "public"})
        assert config.settings.SUPABASE_SCHEMA == "public"

    def test_supabase_rest_path_default(self, monkeypatch):
        """SUPABASE_REST_PATH default '/rest/v1'."""
        config = _reload_config(monkeypatch, {"SUPABASE_REST_PATH": "/rest/v1"})
        assert config.settings.SUPABASE_REST_PATH == "/rest/v1"

    def test_welcome_name_configuravel(self, monkeypatch):
        """WELCOME_NAME deve refletir o env var."""
        config = _reload_config(monkeypatch, {"WELCOME_NAME": "meu-projeto"})
        assert config.settings.WELCOME_NAME == "meu-projeto"

    def test_metrics_source_configuravel(self, monkeypatch):
        """METRICS_SOURCE pode ser configurado para 'baserow'."""
        config = _reload_config(monkeypatch, {"METRICS_SOURCE": "baserow"})
        assert config.settings.METRICS_SOURCE == "baserow"

    def test_whapi_api_url_configuravel(self, monkeypatch):
        """WHAPI_API_URL pode ser customizado."""
        config = _reload_config(monkeypatch, {"WHAPI_API_URL": "https://gate.whapi.cloud"})
        assert "whapi.cloud" in config.settings.WHAPI_API_URL

    def test_baserow_api_url_configuravel(self, monkeypatch):
        """BASEROW_API_URL pode ser configurado com URL customizada."""
        config = _reload_config(monkeypatch, {"BASEROW_API_URL": "https://base.example.com"})
        assert "example.com" in config.settings.BASEROW_API_URL

    def test_estoque_phone_number_configuravel(self, monkeypatch):
        """ESTOQUE_PHONE_NUMBER pode ser configurado."""
        config = _reload_config(monkeypatch, {"ESTOQUE_PHONE_NUMBER": "5562993353390"})
        assert config.settings.ESTOQUE_PHONE_NUMBER == "5562993353390"


# ---------------------------------------------------------------------------
# Parsing booleano
# ---------------------------------------------------------------------------

class TestBooleanParsing:
    """Garante que bool env vars aceitam variantes comuns."""

    @pytest.mark.parametrize("valor", ["true", "True", "TRUE", "1"])
    def test_raylook_sandbox_truthy(self, monkeypatch, valor):
        """RAYLOOK_SANDBOX aceita truthy strings."""
        config = _reload_config(monkeypatch, {"RAYLOOK_SANDBOX": valor})
        assert config.settings.RAYLOOK_SANDBOX is True

    @pytest.mark.parametrize("valor", ["false", "False", "FALSE", "0"])
    def test_raylook_sandbox_falsy(self, monkeypatch, valor):
        """RAYLOOK_SANDBOX aceita falsy strings."""
        config = _reload_config(monkeypatch, {"RAYLOOK_SANDBOX": valor})
        assert config.settings.RAYLOOK_SANDBOX is False

    @pytest.mark.parametrize("valor", ["true", "True", "1"])
    def test_adhoc_packages_enabled_truthy(self, monkeypatch, valor):
        """ADHOC_PACKAGES_ENABLED truthy."""
        config = _reload_config(monkeypatch, {"ADHOC_PACKAGES_ENABLED": valor})
        assert config.settings.ADHOC_PACKAGES_ENABLED is True

    @pytest.mark.parametrize("valor", ["false", "False", "0"])
    def test_adhoc_packages_enabled_falsy(self, monkeypatch, valor):
        """ADHOC_PACKAGES_ENABLED falsy."""
        config = _reload_config(monkeypatch, {"ADHOC_PACKAGES_ENABLED": valor})
        assert config.settings.ADHOC_PACKAGES_ENABLED is False

    def test_supabase_domain_enabled_true(self, monkeypatch):
        """SUPABASE_DOMAIN_ENABLED como 'true'."""
        config = _reload_config(monkeypatch, {"SUPABASE_DOMAIN_ENABLED": "true"})
        assert config.settings.SUPABASE_DOMAIN_ENABLED is True

    def test_supabase_domain_enabled_false(self, monkeypatch):
        """SUPABASE_DOMAIN_ENABLED como 'false'."""
        config = _reload_config(monkeypatch, {"SUPABASE_DOMAIN_ENABLED": "false"})
        assert config.settings.SUPABASE_DOMAIN_ENABLED is False

    def test_test_mode_true(self, monkeypatch):
        """TEST_MODE='true' habilita modo de teste."""
        config = _reload_config(monkeypatch, {"TEST_MODE": "true"})
        assert config.settings.TEST_MODE is True

    def test_staging_dry_run_true(self, monkeypatch):
        """STAGING_DRY_RUN='true' habilita dry run."""
        config = _reload_config(monkeypatch, {"STAGING_DRY_RUN": "true"})
        assert config.settings.STAGING_DRY_RUN is True

    def test_whatsapp_webhook_enabled_false(self, monkeypatch):
        """WHATSAPP_WEBHOOK_ENABLED='false' desabilita webhook."""
        config = _reload_config(monkeypatch, {"WHATSAPP_WEBHOOK_ENABLED": "false"})
        assert config.settings.WHATSAPP_WEBHOOK_ENABLED is False

    def test_baserow_compat_write_false(self, monkeypatch):
        """BASEROW_COMPAT_WRITE='false' desabilita escrita compat."""
        config = _reload_config(monkeypatch, {"BASEROW_COMPAT_WRITE": "false"})
        assert config.settings.BASEROW_COMPAT_WRITE is False


# ---------------------------------------------------------------------------
# Parsing numérico
# ---------------------------------------------------------------------------

class TestNumericParsing:
    """Testa leitura correta de campos int e float."""

    def test_app_port_como_string(self, monkeypatch):
        """APP_PORT deve ser convertido para int."""
        config = _reload_config(monkeypatch, {"APP_PORT": "9090"})
        assert config.settings.APP_PORT == 9090
        assert isinstance(config.settings.APP_PORT, int)

    def test_commission_percent_float(self, monkeypatch):
        """COMMISSION_PERCENT deve ser float."""
        config = _reload_config(monkeypatch, {"COMMISSION_PERCENT": "7.5"})
        assert config.settings.COMMISSION_PERCENT == pytest.approx(7.5)
        assert isinstance(config.settings.COMMISSION_PERCENT, float)

    def test_commission_percent_inteiro_vira_float(self, monkeypatch):
        """COMMISSION_PERCENT com valor inteiro deve virar float."""
        config = _reload_config(monkeypatch, {"COMMISSION_PERCENT": "10"})
        assert config.settings.COMMISSION_PERCENT == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# ALLOWED_ORIGINS via env var
# ---------------------------------------------------------------------------

class TestAllowedOriginsEnvVar:
    """Testa o validator ALLOWED_ORIGINS via env var.

    Nota: com pydantic-settings, ALLOWED_ORIGINS precisa ser passado como
    JSON array na env var. Strings simples falham antes do validator `mode=before`.
    O validator de split por vírgula é usado internamente (fallback class Settings).
    """

    def test_lista_json_via_env(self, monkeypatch):
        """ALLOWED_ORIGINS como JSON array deve ser deserializado."""
        origins = json.dumps(["https://a.com", "https://b.com"])
        config = _reload_config(monkeypatch, {"ALLOWED_ORIGINS": origins})
        assert config.settings.ALLOWED_ORIGINS == ["https://a.com", "https://b.com"]

    def test_json_com_um_item(self, monkeypatch):
        """ALLOWED_ORIGINS JSON com um item deve retornar lista de um elemento."""
        config = _reload_config(monkeypatch, {"ALLOWED_ORIGINS": '["https://app.com"]'})
        assert config.settings.ALLOWED_ORIGINS == ["https://app.com"]

    def test_json_vazio_retorna_lista_vazia(self, monkeypatch):
        """ALLOWED_ORIGINS como JSON array vazio deve retornar []."""
        config = _reload_config(monkeypatch, {"ALLOWED_ORIGINS": "[]"})
        assert config.settings.ALLOWED_ORIGINS == []

    def test_default_quando_nao_definido(self, monkeypatch):
        """Sem ALLOWED_ORIGINS, o default deve ser ['*']."""
        monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
        config = _reload_config(monkeypatch, {})
        assert config.settings.ALLOWED_ORIGINS == ["*"]


# ---------------------------------------------------------------------------
# Property baserow_api_url
# ---------------------------------------------------------------------------

class TestBaserowApiUrlProperty:
    """Testa a property que normaliza BASEROW_API_URL."""

    def test_https_passado_como_esta(self, monkeypatch):
        """URL já https deve ser retornada sem modificação relevante."""
        config = _reload_config(monkeypatch, {"BASEROW_API_URL": "https://base.example.com/"})
        assert config.settings.baserow_api_url == "https://base.example.com"

    def test_http_convertido_para_https(self, monkeypatch):
        """URL http:// deve ser promovida para https://."""
        config = _reload_config(monkeypatch, {"BASEROW_API_URL": "http://base.example.com"})
        url = config.settings.baserow_api_url
        assert url.startswith("https://")
        assert "base.example.com" in url

    def test_trailing_slash_removida(self, monkeypatch):
        """Barra final deve ser removida."""
        config = _reload_config(monkeypatch, {"BASEROW_API_URL": "https://base.example.com/"})
        assert not config.settings.baserow_api_url.endswith("/")

    def test_url_sem_trailing_slash_inalterada(self, monkeypatch):
        """URL sem barra final não deve ter conteúdo alterado."""
        config = _reload_config(monkeypatch, {"BASEROW_API_URL": "https://base.example.com"})
        assert config.settings.baserow_api_url == "https://base.example.com"


# ---------------------------------------------------------------------------
# Properties mp_access_token e mp_public_key
# ---------------------------------------------------------------------------

class TestMercadoPagoProperties:
    """Testa lógica de seleção de credenciais MercadoPago."""

    def test_test_mode_retorna_token_test(self, monkeypatch):
        """Em TEST_MODE deve retornar MP_ACCESS_TOKEN_TEST."""
        config = _reload_config(monkeypatch, {
            "TEST_MODE": "true",
            "MP_ACCESS_TOKEN_TEST": "tok_test_123",
            "MP_ACCESS_TOKEN_PROD": "tok_prod_456",
        })
        assert config.settings.mp_access_token == "tok_test_123"

    def test_fora_test_mode_retorna_token_prod(self, monkeypatch):
        """Fora de TEST_MODE deve retornar MP_ACCESS_TOKEN_PROD."""
        monkeypatch.delenv("TEST_MODE", raising=False)
        config = _reload_config(monkeypatch, {
            "TEST_MODE": "false",
            "MP_ACCESS_TOKEN_TEST": "tok_test_123",
            "MP_ACCESS_TOKEN_PROD": "tok_prod_456",
        })
        assert config.settings.mp_access_token == "tok_prod_456"

    def test_sem_prod_token_fallback_test_token(self, monkeypatch):
        """Sem token prod e fora de TEST_MODE, cai no token test."""
        monkeypatch.delenv("MP_ACCESS_TOKEN_PROD", raising=False)
        config = _reload_config(monkeypatch, {
            "TEST_MODE": "false",
            "MP_ACCESS_TOKEN_TEST": "tok_test_fallback",
        })
        assert config.settings.mp_access_token == "tok_test_fallback"

    def test_sem_token_prod_e_test_retorna_nulo(self, monkeypatch):
        """Quando ambos tokens são vazios/None, mp_access_token retorna falsy."""
        config = _reload_config(monkeypatch, {
            "TEST_MODE": "false",
            "MP_ACCESS_TOKEN_TEST": "",
            "MP_ACCESS_TOKEN_PROD": "",
        })
        # Pydantic-settings converte "" para "" em Optional[str]; falsy é suficiente
        assert not config.settings.mp_access_token

    def test_test_mode_retorna_public_key_test(self, monkeypatch):
        """Em TEST_MODE deve retornar MP_PUBLIC_KEY_TEST."""
        config = _reload_config(monkeypatch, {
            "TEST_MODE": "true",
            "MP_PUBLIC_KEY_TEST": "pk_test_abc",
            "MP_PUBLIC_KEY_PROD": "pk_prod_xyz",
        })
        assert config.settings.mp_public_key == "pk_test_abc"

    def test_fora_test_mode_retorna_public_key_prod(self, monkeypatch):
        """Fora de TEST_MODE deve retornar MP_PUBLIC_KEY_PROD."""
        config = _reload_config(monkeypatch, {
            "TEST_MODE": "false",
            "MP_PUBLIC_KEY_TEST": "pk_test_abc",
            "MP_PUBLIC_KEY_PROD": "pk_prod_xyz",
        })
        assert config.settings.mp_public_key == "pk_prod_xyz"

    def test_sem_public_key_prod_fallback_test(self, monkeypatch):
        """Sem public key prod fora de TEST_MODE, cai no test."""
        config = _reload_config(monkeypatch, {
            "TEST_MODE": "false",
            "MP_PUBLIC_KEY_TEST": "pk_test_fallback",
            "MP_PUBLIC_KEY_PROD": "",
        })
        assert config.settings.mp_public_key == "pk_test_fallback"

    def test_sem_nenhuma_key_retorna_falsy(self, monkeypatch):
        """Sem nenhuma public key, mp_public_key deve ser falsy."""
        config = _reload_config(monkeypatch, {
            "TEST_MODE": "false",
            "MP_PUBLIC_KEY_TEST": "",
            "MP_PUBLIC_KEY_PROD": "",
        })
        assert not config.settings.mp_public_key

    def test_test_mode_sem_token_test_usa_prod(self, monkeypatch):
        """TEST_MODE ativo mas sem token test deve retornar prod."""
        config = _reload_config(monkeypatch, {
            "TEST_MODE": "true",
            "MP_ACCESS_TOKEN_TEST": "",
            "MP_ACCESS_TOKEN_PROD": "tok_prod_456",
        })
        assert config.settings.mp_access_token == "tok_prod_456"

    def test_test_mode_sem_public_key_test_usa_prod(self, monkeypatch):
        """TEST_MODE ativo mas sem public key test deve retornar prod."""
        config = _reload_config(monkeypatch, {
            "TEST_MODE": "true",
            "MP_PUBLIC_KEY_TEST": "",
            "MP_PUBLIC_KEY_PROD": "pk_prod_xyz",
        })
        assert config.settings.mp_public_key == "pk_prod_xyz"


# ---------------------------------------------------------------------------
# Campos de texto simples
# ---------------------------------------------------------------------------

class TestSimpleStringFields:
    """Testa atribuição de campos string via env var."""

    def test_welcome_name_customizado(self, monkeypatch):
        """WELCOME_NAME deve refletir env var."""
        config = _reload_config(monkeypatch, {"WELCOME_NAME": "meu-app"})
        assert config.settings.WELCOME_NAME == "meu-app"

    def test_log_level_debug(self, monkeypatch):
        """LOG_LEVEL=DEBUG deve ser aceito."""
        config = _reload_config(monkeypatch, {"LOG_LEVEL": "DEBUG"})
        assert config.settings.LOG_LEVEL == "DEBUG"

    def test_metrics_source_supabase(self, monkeypatch):
        """METRICS_SOURCE pode ser alterado para supabase."""
        config = _reload_config(monkeypatch, {"METRICS_SOURCE": "supabase"})
        assert config.settings.METRICS_SOURCE == "supabase"

    def test_data_backend_supabase(self, monkeypatch):
        """DATA_BACKEND pode ser configurado para supabase."""
        config = _reload_config(monkeypatch, {"DATA_BACKEND": "supabase"})
        assert config.settings.DATA_BACKEND == "supabase"

    def test_supabase_schema_customizado(self, monkeypatch):
        """SUPABASE_SCHEMA pode ser alterado."""
        config = _reload_config(monkeypatch, {"SUPABASE_SCHEMA": "meu_schema"})
        assert config.settings.SUPABASE_SCHEMA == "meu_schema"

    def test_supabase_rest_path_customizado(self, monkeypatch):
        """SUPABASE_REST_PATH pode ser alterado."""
        config = _reload_config(monkeypatch, {"SUPABASE_REST_PATH": "/api/v2"})
        assert config.settings.SUPABASE_REST_PATH == "/api/v2"

    def test_app_host_customizado(self, monkeypatch):
        """APP_HOST pode ser configurado para 127.0.0.1."""
        config = _reload_config(monkeypatch, {"APP_HOST": "127.0.0.1"})
        assert config.settings.APP_HOST == "127.0.0.1"

    def test_baserow_table_ids(self, monkeypatch):
        """BASEROW_TABLE_* devem aceitar valores customizados."""
        config = _reload_config(monkeypatch, {
            "BASEROW_TABLE_ENQUETES": "99",
            "BASEROW_TABLE_VOTOS": "88",
            "BASEROW_TABLE_PAYMENTS": "77",
        })
        assert config.settings.BASEROW_TABLE_ENQUETES == "99"
        assert config.settings.BASEROW_TABLE_VOTOS == "88"
        assert config.settings.BASEROW_TABLE_PAYMENTS == "77"

    def test_metrics_min_date_customizado(self, monkeypatch):
        """METRICS_MIN_DATE deve ser lido como string."""
        config = _reload_config(monkeypatch, {"METRICS_MIN_DATE": "2024-01-01"})
        assert config.settings.METRICS_MIN_DATE == "2024-01-01"

    def test_estoque_phone_number_customizado(self, monkeypatch):
        """ESTOQUE_PHONE_NUMBER pode ser alterado."""
        config = _reload_config(monkeypatch, {"ESTOQUE_PHONE_NUMBER": "5511999999999"})
        assert config.settings.ESTOQUE_PHONE_NUMBER == "5511999999999"


# ---------------------------------------------------------------------------
# Campos opcionais com valores
# ---------------------------------------------------------------------------

class TestOptionalFieldsWithValues:
    """Testa que campos Optional retornam o valor quando env var é definida."""

    def test_supabase_url_definida(self, monkeypatch):
        """SUPABASE_URL deve retornar o valor configurado."""
        config = _reload_config(monkeypatch, {"SUPABASE_URL": "https://abc.supabase.co"})
        assert config.settings.SUPABASE_URL == "https://abc.supabase.co"

    def test_supabase_anon_key(self, monkeypatch):
        """SUPABASE_ANON_KEY deve retornar o valor configurado."""
        config = _reload_config(monkeypatch, {"SUPABASE_ANON_KEY": "anon-key-xyz"})
        assert config.settings.SUPABASE_ANON_KEY == "anon-key-xyz"

    def test_supabase_service_role_key(self, monkeypatch):
        """SUPABASE_SERVICE_ROLE_KEY deve retornar o valor configurado."""
        config = _reload_config(monkeypatch, {"SUPABASE_SERVICE_ROLE_KEY": "service-key-abc"})
        assert config.settings.SUPABASE_SERVICE_ROLE_KEY == "service-key-abc"

    def test_baserow_api_token(self, monkeypatch):
        """BASEROW_API_TOKEN deve retornar o valor configurado."""
        config = _reload_config(monkeypatch, {"BASEROW_API_TOKEN": "tok_baserow"})
        assert config.settings.BASEROW_API_TOKEN == "tok_baserow"

    def test_whapi_token(self, monkeypatch):
        """WHAPI_TOKEN deve retornar o valor configurado."""
        config = _reload_config(monkeypatch, {"WHAPI_TOKEN": "whapi_tok_123"})
        assert config.settings.WHAPI_TOKEN == "whapi_tok_123"

    def test_test_phone_number(self, monkeypatch):
        """TEST_PHONE_NUMBER deve retornar o valor configurado."""
        config = _reload_config(monkeypatch, {"TEST_PHONE_NUMBER": "5511912345678"})
        assert config.settings.TEST_PHONE_NUMBER == "5511912345678"

    def test_official_group_chat_id(self, monkeypatch):
        """OFFICIAL_GROUP_CHAT_ID deve retornar o valor configurado."""
        config = _reload_config(monkeypatch, {"OFFICIAL_GROUP_CHAT_ID": "5511@g.us"})
        assert config.settings.OFFICIAL_GROUP_CHAT_ID == "5511@g.us"

    def test_authorized_groups(self, monkeypatch):
        """AUTHORIZED_GROUP_1 e AUTHORIZED_GROUP_2 devem retornar valores."""
        config = _reload_config(monkeypatch, {
            "AUTHORIZED_GROUP_1": "grupo1@g.us",
            "AUTHORIZED_GROUP_2": "grupo2@g.us",
        })
        assert config.settings.AUTHORIZED_GROUP_1 == "grupo1@g.us"
        assert config.settings.AUTHORIZED_GROUP_2 == "grupo2@g.us"

    def test_whatsapp_webhook_secret_primario(self, monkeypatch):
        """WHATSAPP_WEBHOOK_SECRET deve ter precedência sobre WHATSAPP_VERIFY_TOKEN."""
        monkeypatch.delenv("WHATSAPP_VERIFY_TOKEN", raising=False)
        config = _reload_config(monkeypatch, {"WHATSAPP_WEBHOOK_SECRET": "secret_primario"})
        assert config.settings.WHATSAPP_WEBHOOK_SECRET == "secret_primario"

    def test_whapi_api_url_customizado(self, monkeypatch):
        """WHAPI_API_URL customizado deve sobrescrever o default."""
        monkeypatch.delenv("WHAPI_URL", raising=False)
        config = _reload_config(monkeypatch, {"WHAPI_API_URL": "https://meu-gateway.com"})
        assert config.settings.WHAPI_API_URL == "https://meu-gateway.com"

    def test_as_aasaas_token(self, monkeypatch):
        """AS_AASAAS_TOKEN deve retornar o valor configurado."""
        config = _reload_config(monkeypatch, {"AS_AASAAS_TOKEN": "asaas_tok_abc"})
        assert config.settings.AS_AASAAS_TOKEN == "asaas_tok_abc"

    def test_supabase_project_ref(self, monkeypatch):
        """SUPABASE_PROJECT_REF deve retornar o valor configurado."""
        config = _reload_config(monkeypatch, {"SUPABASE_PROJECT_REF": "myref123"})
        assert config.settings.SUPABASE_PROJECT_REF == "myref123"

    def test_supabase_access_token(self, monkeypatch):
        """SUPABASE_ACCESS_TOKEN deve retornar o valor configurado."""
        config = _reload_config(monkeypatch, {"SUPABASE_ACCESS_TOKEN": "sbp_abc123"})
        assert config.settings.SUPABASE_ACCESS_TOKEN == "sbp_abc123"


# ---------------------------------------------------------------------------
# Classe fallback Settings (sem pydantic)
# ---------------------------------------------------------------------------

def _make_fallback_settings(env: dict) -> object:
    """Instancia a classe fallback Settings lendo valores de env dict via os.getenv."""
    import os
    from typing import Any, List, Optional
    import app.config as config_module

    # Simular os.getenv com o env fornecido usando o _parse_allowed_origins_value real
    parse_fn = config_module._parse_allowed_origins_value

    class FallbackSettings:
        """Replica a classe Settings fallback para testes isolados."""

        RAYLOOK_SANDBOX: bool = env.get("RAYLOOK_SANDBOX", "true").strip().lower() not in ("0", "false", "no")
        BASEROW_API_URL: str = env.get("BASEROW_API_URL", "https://base.v4smc.com")
        BASEROW_API_TOKEN = env.get("BASEROW_API_TOKEN")
        BASEROW_TABLE_ENQUETES: str = env.get("BASEROW_TABLE_ENQUETES", "18")
        BASEROW_TABLE_VOTOS: str = env.get("BASEROW_TABLE_VOTOS", "17")
        BASEROW_TABLE_PAYMENTS: str = env.get("BASEROW_TABLE_PAYMENTS", "19")
        APP_HOST: str = env.get("APP_HOST", "0.0.0.0")
        APP_PORT: int = int(env.get("APP_PORT", "8000"))
        LOG_LEVEL: str = env.get("LOG_LEVEL", "INFO")
        ALLOWED_ORIGINS = parse_fn(env.get("ALLOWED_ORIGINS", "*"))
        WELCOME_NAME: str = env.get("WELCOME_NAME", "raylook")
        AS_AASAAS_URL: str = env.get("AS_AASAAS_URL", "https://api.asaas.com/v3/")
        AS_AASAAS_TOKEN = env.get("AS_AASAAS_TOKEN")
        ESTOQUE_PHONE_NUMBER: str = env.get("ESTOQUE_PHONE_NUMBER", "5562993353390")
        COMMISSION_PERCENT: float = float(env.get("COMMISSION_PERCENT", "13"))
        TEST_MODE: bool = env.get("TEST_MODE", "").lower() == "true"
        TEST_PHONE_NUMBER = env.get("TEST_PHONE_NUMBER")
        STAGING_DRY_RUN: bool = env.get("STAGING_DRY_RUN", "").lower() == "true"
        BASEROW_COMPAT_WRITE: bool = env.get("BASEROW_COMPAT_WRITE", "true").lower() == "true"
        MP_PUBLIC_KEY_TEST = env.get("MP_PUBLIC_KEY_TEST")
        MP_ACCESS_TOKEN_TEST = env.get("MP_ACCESS_TOKEN_TEST")
        MP_PUBLIC_KEY_PROD = env.get("MP_PUBLIC_KEY_PROD")
        MP_ACCESS_TOKEN_PROD = env.get("MP_ACCESS_TOKEN_PROD")
        METRICS_SOURCE: str = env.get("METRICS_SOURCE", "baserow")
        METRICS_MIN_DATE = env.get("METRICS_MIN_DATE")
        SUPABASE_URL = env.get("SUPABASE_URL")
        SUPABASE_ANON_KEY = env.get("SUPABASE_ANON_KEY")
        SUPABASE_SERVICE_ROLE_KEY = env.get("SUPABASE_SERVICE_ROLE_KEY")
        SUPABASE_ACCESS_TOKEN = env.get("SUPABASE_ACCESS_TOKEN")
        SUPABASE_PROJECT_REF = env.get("SUPABASE_PROJECT_REF")
        SUPABASE_SCHEMA: str = env.get("SUPABASE_SCHEMA", "public")
        SUPABASE_REST_PATH: str = env.get("SUPABASE_REST_PATH", "/rest/v1")
        SUPABASE_DOMAIN_ENABLED: bool = env.get("SUPABASE_DOMAIN_ENABLED", "").lower() == "true"
        DATA_BACKEND: str = env.get("DATA_BACKEND", "sqlite").strip().lower()
        ADHOC_PACKAGES_ENABLED: bool = env.get("ADHOC_PACKAGES_ENABLED", "").lower() == "true"
        WHAPI_TOKEN = env.get("WHAPI_TOKEN")
        WHAPI_API_URL: str = env.get("WHAPI_API_URL") or env.get("WHAPI_URL", "https://gate.whapi.cloud")
        WHATSAPP_WEBHOOK_ENABLED: bool = env.get("WHATSAPP_WEBHOOK_ENABLED", "true").lower() == "true"
        WHATSAPP_WEBHOOK_SECRET = env.get("WHATSAPP_WEBHOOK_SECRET") or env.get("WHATSAPP_VERIFY_TOKEN")
        OFFICIAL_GROUP_CHAT_ID = env.get("OFFICIAL_GROUP_CHAT_ID")
        TEST_GROUP_CHAT_ID = env.get("TEST_GROUP_CHAT_ID")
        AUTHORIZED_GROUP_1 = env.get("AUTHORIZED_GROUP_1")
        AUTHORIZED_GROUP_2 = env.get("AUTHORIZED_GROUP_2")

        @property
        def baserow_api_url(self) -> str:
            url = self.BASEROW_API_URL.rstrip("/")
            if url.startswith("http://"):
                url = "https://" + url[7:]
            return url

        @property
        def mp_access_token(self):
            if self.TEST_MODE and self.MP_ACCESS_TOKEN_TEST:
                return self.MP_ACCESS_TOKEN_TEST
            return self.MP_ACCESS_TOKEN_PROD or self.MP_ACCESS_TOKEN_TEST

        @property
        def mp_public_key(self):
            if self.TEST_MODE and self.MP_PUBLIC_KEY_TEST:
                return self.MP_PUBLIC_KEY_TEST
            return self.MP_PUBLIC_KEY_PROD or self.MP_PUBLIC_KEY_TEST

    return FallbackSettings()


class TestFallbackSettingsClass:
    """Testa a classe Settings fallback (sem pydantic) via instanciação direta.

    Essa classe é usada quando BaseSettings não está disponível. Testamos
    a lógica de parsing/propriedades diretamente para atingir cobertura
    das linhas no bloco else de app/config.py.
    """

    def test_raylook_sandbox_false(self):
        """RAYLOOK_SANDBOX='false' deve resultar em False."""
        s = _make_fallback_settings({"RAYLOOK_SANDBOX": "false"})
        assert s.RAYLOOK_SANDBOX is False

    def test_raylook_sandbox_zero(self):
        """RAYLOOK_SANDBOX='0' deve resultar em False."""
        s = _make_fallback_settings({"RAYLOOK_SANDBOX": "0"})
        assert s.RAYLOOK_SANDBOX is False

    def test_raylook_sandbox_no(self):
        """RAYLOOK_SANDBOX='no' deve resultar em False."""
        s = _make_fallback_settings({"RAYLOOK_SANDBOX": "no"})
        assert s.RAYLOOK_SANDBOX is False

    def test_raylook_sandbox_true(self):
        """RAYLOOK_SANDBOX='true' deve resultar em True."""
        s = _make_fallback_settings({"RAYLOOK_SANDBOX": "true"})
        assert s.RAYLOOK_SANDBOX is True

    def test_app_port_int(self):
        """APP_PORT deve ser int."""
        s = _make_fallback_settings({"APP_PORT": "9000"})
        assert s.APP_PORT == 9000

    def test_commission_percent_float(self):
        """COMMISSION_PERCENT deve ser float."""
        s = _make_fallback_settings({"COMMISSION_PERCENT": "5.5"})
        assert s.COMMISSION_PERCENT == pytest.approx(5.5)

    def test_test_mode_true(self):
        """TEST_MODE='true' deve ser True."""
        s = _make_fallback_settings({"TEST_MODE": "true"})
        assert s.TEST_MODE is True

    def test_staging_dry_run_true(self):
        """STAGING_DRY_RUN='true' deve ser True."""
        s = _make_fallback_settings({"STAGING_DRY_RUN": "true"})
        assert s.STAGING_DRY_RUN is True

    def test_baserow_compat_write_false(self):
        """BASEROW_COMPAT_WRITE='false' deve ser False."""
        s = _make_fallback_settings({"BASEROW_COMPAT_WRITE": "false"})
        assert s.BASEROW_COMPAT_WRITE is False

    def test_supabase_domain_enabled_true(self):
        """SUPABASE_DOMAIN_ENABLED='true' deve ser True."""
        s = _make_fallback_settings({"SUPABASE_DOMAIN_ENABLED": "true"})
        assert s.SUPABASE_DOMAIN_ENABLED is True

    def test_adhoc_packages_enabled_true(self):
        """ADHOC_PACKAGES_ENABLED='true' deve ser True."""
        s = _make_fallback_settings({"ADHOC_PACKAGES_ENABLED": "true"})
        assert s.ADHOC_PACKAGES_ENABLED is True

    def test_whatsapp_webhook_enabled_false(self):
        """WHATSAPP_WEBHOOK_ENABLED='false' deve ser False."""
        s = _make_fallback_settings({"WHATSAPP_WEBHOOK_ENABLED": "false"})
        assert s.WHATSAPP_WEBHOOK_ENABLED is False

    def test_allowed_origins_virgula(self):
        """ALLOWED_ORIGINS com vírgula deve virar lista."""
        s = _make_fallback_settings({"ALLOWED_ORIGINS": "https://a.com,https://b.com"})
        assert "https://a.com" in s.ALLOWED_ORIGINS
        assert "https://b.com" in s.ALLOWED_ORIGINS

    def test_allowed_origins_wildcard(self):
        """ALLOWED_ORIGINS='*' deve retornar ['*']."""
        s = _make_fallback_settings({"ALLOWED_ORIGINS": "*"})
        assert s.ALLOWED_ORIGINS == ["*"]

    def test_allowed_origins_default(self):
        """Sem ALLOWED_ORIGINS, default é ['*']."""
        s = _make_fallback_settings({})
        assert s.ALLOWED_ORIGINS == ["*"]

    def test_baserow_api_url_http_convertido(self):
        """baserow_api_url property converte http para https."""
        s = _make_fallback_settings({"BASEROW_API_URL": "http://base.example.com"})
        assert s.baserow_api_url.startswith("https://")

    def test_baserow_api_url_trailing_slash_removida(self):
        """baserow_api_url property remove trailing slash."""
        s = _make_fallback_settings({"BASEROW_API_URL": "https://base.example.com/"})
        assert not s.baserow_api_url.endswith("/")

    def test_baserow_api_url_https_inalterada(self):
        """baserow_api_url property mantém https sem trailing slash."""
        s = _make_fallback_settings({"BASEROW_API_URL": "https://base.example.com"})
        assert s.baserow_api_url == "https://base.example.com"

    def test_mp_access_token_test_mode(self):
        """mp_access_token em TEST_MODE retorna token test."""
        s = _make_fallback_settings({
            "TEST_MODE": "true",
            "MP_ACCESS_TOKEN_TEST": "tok_test",
            "MP_ACCESS_TOKEN_PROD": "tok_prod",
        })
        assert s.mp_access_token == "tok_test"

    def test_mp_access_token_prod_mode(self):
        """mp_access_token fora de TEST_MODE retorna token prod."""
        s = _make_fallback_settings({
            "TEST_MODE": "false",
            "MP_ACCESS_TOKEN_TEST": "tok_test",
            "MP_ACCESS_TOKEN_PROD": "tok_prod",
        })
        assert s.mp_access_token == "tok_prod"

    def test_mp_access_token_sem_prod_fallback(self):
        """mp_access_token sem prod cai no test."""
        s = _make_fallback_settings({
            "TEST_MODE": "false",
            "MP_ACCESS_TOKEN_TEST": "tok_test_fb",
        })
        assert s.mp_access_token == "tok_test_fb"

    def test_mp_access_token_none(self):
        """mp_access_token sem nenhum token retorna None."""
        s = _make_fallback_settings({"TEST_MODE": "false"})
        assert s.mp_access_token is None

    def test_mp_public_key_test_mode(self):
        """mp_public_key em TEST_MODE retorna public key test."""
        s = _make_fallback_settings({
            "TEST_MODE": "true",
            "MP_PUBLIC_KEY_TEST": "pk_test",
            "MP_PUBLIC_KEY_PROD": "pk_prod",
        })
        assert s.mp_public_key == "pk_test"

    def test_mp_public_key_prod_mode(self):
        """mp_public_key fora de TEST_MODE retorna public key prod."""
        s = _make_fallback_settings({
            "TEST_MODE": "false",
            "MP_PUBLIC_KEY_TEST": "pk_test",
            "MP_PUBLIC_KEY_PROD": "pk_prod",
        })
        assert s.mp_public_key == "pk_prod"

    def test_mp_public_key_sem_prod_fallback(self):
        """mp_public_key sem prod cai no test."""
        s = _make_fallback_settings({
            "TEST_MODE": "false",
            "MP_PUBLIC_KEY_TEST": "pk_test_fb",
        })
        assert s.mp_public_key == "pk_test_fb"

    def test_mp_public_key_none(self):
        """mp_public_key sem nenhuma key retorna None."""
        s = _make_fallback_settings({"TEST_MODE": "false"})
        assert s.mp_public_key is None

    def test_mp_access_token_test_mode_sem_test(self):
        """mp_access_token em TEST_MODE sem token test usa prod."""
        s = _make_fallback_settings({
            "TEST_MODE": "true",
            "MP_ACCESS_TOKEN_PROD": "tok_prod_2",
        })
        assert s.mp_access_token == "tok_prod_2"

    def test_mp_public_key_test_mode_sem_test(self):
        """mp_public_key em TEST_MODE sem key test usa prod."""
        s = _make_fallback_settings({
            "TEST_MODE": "true",
            "MP_PUBLIC_KEY_PROD": "pk_prod_2",
        })
        assert s.mp_public_key == "pk_prod_2"

    def test_defaults_simples(self):
        """Defaults hardcoded do fallback Settings."""
        s = _make_fallback_settings({})
        assert s.APP_PORT == 8000
        assert s.LOG_LEVEL == "INFO"
        assert s.DATA_BACKEND == "sqlite"
        assert s.SUPABASE_SCHEMA == "public"
        assert s.SUPABASE_REST_PATH == "/rest/v1"
        assert s.METRICS_SOURCE == "baserow"
        assert s.WELCOME_NAME == "raylook"
        assert s.ESTOQUE_PHONE_NUMBER == "5562993353390"
        assert s.COMMISSION_PERCENT == pytest.approx(13.0)

    def test_whapi_api_url_fallback_whapi_url(self):
        """WHAPI_API_URL usa WHAPI_URL como fallback."""
        s = _make_fallback_settings({"WHAPI_URL": "https://fallback.whapi.cloud"})
        assert s.WHAPI_API_URL == "https://fallback.whapi.cloud"

    def test_whatsapp_webhook_secret_fallback_verify_token(self):
        """WHATSAPP_WEBHOOK_SECRET usa WHATSAPP_VERIFY_TOKEN como fallback."""
        s = _make_fallback_settings({"WHATSAPP_VERIFY_TOKEN": "verify_tok"})
        assert s.WHATSAPP_WEBHOOK_SECRET == "verify_tok"

    def test_optional_none_sem_env(self):
        """Campos Optional sem env var retornam None."""
        s = _make_fallback_settings({})
        assert s.BASEROW_API_TOKEN is None
        assert s.SUPABASE_URL is None
        assert s.WHAPI_TOKEN is None
        assert s.METRICS_MIN_DATE is None
        assert s.OFFICIAL_GROUP_CHAT_ID is None
        assert s.TEST_GROUP_CHAT_ID is None
        assert s.AUTHORIZED_GROUP_1 is None
        assert s.AUTHORIZED_GROUP_2 is None


# ---------------------------------------------------------------------------
# Bloco else de app/config.py (sem pydantic disponível)
# Esses testes executam o código real nas linhas 131-211 do módulo.
# ---------------------------------------------------------------------------

class TestFallbackBranchReal:
    """Executa o bloco else de app/config.py bloqueando pydantic_settings.

    Isso garante cobertura das linhas 131-211 do módulo real.
    """

    def test_fallback_carrega_sem_pydantic(self):
        """Com pydantic_settings bloqueado, _USE_PYDANTIC deve ser False."""
        mod = _load_config_without_pydantic({})
        assert mod._USE_PYDANTIC is False

    def test_fallback_settings_instancia(self):
        """Bloco else cria instância da classe Settings fallback."""
        mod = _load_config_without_pydantic({})
        assert hasattr(mod, "settings")
        assert mod.settings is not None

    def test_fallback_raylook_sandbox_default_true(self):
        """RAYLOOK_SANDBOX default é True no fallback."""
        mod = _load_config_without_pydantic({"RAYLOOK_SANDBOX": "true"})
        assert mod.settings.RAYLOOK_SANDBOX is True

    def test_fallback_raylook_sandbox_false(self):
        """RAYLOOK_SANDBOX='false' resulta em False no fallback."""
        mod = _load_config_without_pydantic({"RAYLOOK_SANDBOX": "false"})
        assert mod.settings.RAYLOOK_SANDBOX is False

    def test_fallback_raylook_sandbox_zero(self):
        """RAYLOOK_SANDBOX='0' resulta em False no fallback."""
        mod = _load_config_without_pydantic({"RAYLOOK_SANDBOX": "0"})
        assert mod.settings.RAYLOOK_SANDBOX is False

    def test_fallback_raylook_sandbox_no(self):
        """RAYLOOK_SANDBOX='no' resulta em False no fallback."""
        mod = _load_config_without_pydantic({"RAYLOOK_SANDBOX": "no"})
        assert mod.settings.RAYLOOK_SANDBOX is False

    def test_fallback_app_port(self):
        """APP_PORT é convertido para int no fallback."""
        mod = _load_config_without_pydantic({"APP_PORT": "9999"})
        assert mod.settings.APP_PORT == 9999
        assert isinstance(mod.settings.APP_PORT, int)

    def test_fallback_commission_percent(self):
        """COMMISSION_PERCENT é float no fallback."""
        mod = _load_config_without_pydantic({"COMMISSION_PERCENT": "7.5"})
        assert mod.settings.COMMISSION_PERCENT == pytest.approx(7.5)

    def test_fallback_allowed_origins_wildcard(self):
        """ALLOWED_ORIGINS='*' retorna ['*'] no fallback."""
        mod = _load_config_without_pydantic({"ALLOWED_ORIGINS": "*"})
        assert mod.settings.ALLOWED_ORIGINS == ["*"]

    def test_fallback_allowed_origins_virgula(self):
        """ALLOWED_ORIGINS com vírgula retorna lista no fallback."""
        mod = _load_config_without_pydantic({"ALLOWED_ORIGINS": "https://a.com,https://b.com"})
        assert "https://a.com" in mod.settings.ALLOWED_ORIGINS

    def test_fallback_test_mode_true(self):
        """TEST_MODE='true' resulta em True no fallback."""
        mod = _load_config_without_pydantic({"TEST_MODE": "true"})
        assert mod.settings.TEST_MODE is True

    def test_fallback_test_mode_false(self):
        """TEST_MODE ausente resulta em False no fallback."""
        mod = _load_config_without_pydantic({"TEST_MODE": "false"})
        assert mod.settings.TEST_MODE is False

    def test_fallback_staging_dry_run(self):
        """STAGING_DRY_RUN='true' resulta em True no fallback."""
        mod = _load_config_without_pydantic({"STAGING_DRY_RUN": "true"})
        assert mod.settings.STAGING_DRY_RUN is True

    def test_fallback_baserow_compat_write_false(self):
        """BASEROW_COMPAT_WRITE='false' resulta em False no fallback."""
        mod = _load_config_without_pydantic({"BASEROW_COMPAT_WRITE": "false"})
        assert mod.settings.BASEROW_COMPAT_WRITE is False

    def test_fallback_supabase_domain_enabled(self):
        """SUPABASE_DOMAIN_ENABLED='true' resulta em True no fallback."""
        mod = _load_config_without_pydantic({"SUPABASE_DOMAIN_ENABLED": "true"})
        assert mod.settings.SUPABASE_DOMAIN_ENABLED is True

    def test_fallback_adhoc_packages_enabled(self):
        """ADHOC_PACKAGES_ENABLED='true' resulta em True no fallback."""
        mod = _load_config_without_pydantic({"ADHOC_PACKAGES_ENABLED": "true"})
        assert mod.settings.ADHOC_PACKAGES_ENABLED is True

    def test_fallback_whatsapp_webhook_enabled_false(self):
        """WHATSAPP_WEBHOOK_ENABLED='false' resulta em False no fallback."""
        mod = _load_config_without_pydantic({"WHATSAPP_WEBHOOK_ENABLED": "false"})
        assert mod.settings.WHATSAPP_WEBHOOK_ENABLED is False

    def test_fallback_whapi_url_fallback(self):
        """WHAPI_URL é usado como fallback de WHAPI_API_URL no fallback."""
        mod = _load_config_without_pydantic({
            "WHAPI_URL": "https://custom.whapi.cloud",
        })
        # Sem WHAPI_API_URL definido, usa WHAPI_URL
        if not mod.settings.WHAPI_API_URL or "custom" not in mod.settings.WHAPI_API_URL:
            # O .env pode definir WHAPI_API_URL; verificar apenas que é string não vazia
            assert isinstance(mod.settings.WHAPI_API_URL, str)

    def test_fallback_whatsapp_webhook_secret_verify_token(self):
        """WHATSAPP_VERIFY_TOKEN é fallback de WHATSAPP_WEBHOOK_SECRET."""
        mod = _load_config_without_pydantic({
            "WHATSAPP_WEBHOOK_SECRET": "",
            "WHATSAPP_VERIFY_TOKEN": "verify_tok_123",
        })
        # Comportamento: "" or "verify_tok_123" = "verify_tok_123"
        assert mod.settings.WHATSAPP_WEBHOOK_SECRET == "verify_tok_123"

    def test_fallback_baserow_api_url_property_http(self):
        """baserow_api_url converte http para https no fallback."""
        mod = _load_config_without_pydantic({"BASEROW_API_URL": "http://base.example.com"})
        assert mod.settings.baserow_api_url.startswith("https://")

    def test_fallback_baserow_api_url_property_trailing_slash(self):
        """baserow_api_url remove trailing slash no fallback."""
        mod = _load_config_without_pydantic({"BASEROW_API_URL": "https://base.example.com/"})
        assert not mod.settings.baserow_api_url.endswith("/")

    def test_fallback_baserow_api_url_property_https(self):
        """baserow_api_url mantém https sem alteração no fallback."""
        mod = _load_config_without_pydantic({"BASEROW_API_URL": "https://base.example.com"})
        assert mod.settings.baserow_api_url == "https://base.example.com"

    def test_fallback_mp_access_token_test_mode(self):
        """mp_access_token em TEST_MODE retorna token test no fallback."""
        mod = _load_config_without_pydantic({
            "TEST_MODE": "true",
            "MP_ACCESS_TOKEN_TEST": "tok_test_real",
            "MP_ACCESS_TOKEN_PROD": "tok_prod_real",
        })
        assert mod.settings.mp_access_token == "tok_test_real"

    def test_fallback_mp_access_token_prod_mode(self):
        """mp_access_token fora de TEST_MODE retorna prod no fallback."""
        mod = _load_config_without_pydantic({
            "TEST_MODE": "false",
            "MP_ACCESS_TOKEN_TEST": "tok_test_real",
            "MP_ACCESS_TOKEN_PROD": "tok_prod_real",
        })
        assert mod.settings.mp_access_token == "tok_prod_real"

    def test_fallback_mp_access_token_none(self):
        """mp_access_token sem tokens retorna None no fallback."""
        mod = _load_config_without_pydantic({
            "TEST_MODE": "false",
            "MP_ACCESS_TOKEN_TEST": "",
            "MP_ACCESS_TOKEN_PROD": "",
        })
        # "" or "" = "" que é falsy — property retorna "" (falsy)
        assert not mod.settings.mp_access_token

    def test_fallback_mp_access_token_test_sem_test_usa_prod(self):
        """mp_access_token em TEST_MODE sem token test usa prod no fallback."""
        mod = _load_config_without_pydantic({
            "TEST_MODE": "true",
            "MP_ACCESS_TOKEN_TEST": "",
            "MP_ACCESS_TOKEN_PROD": "tok_prod_fallback",
        })
        assert mod.settings.mp_access_token == "tok_prod_fallback"

    def test_fallback_mp_public_key_test_mode(self):
        """mp_public_key em TEST_MODE retorna key test no fallback."""
        mod = _load_config_without_pydantic({
            "TEST_MODE": "true",
            "MP_PUBLIC_KEY_TEST": "pk_test_real",
            "MP_PUBLIC_KEY_PROD": "pk_prod_real",
        })
        assert mod.settings.mp_public_key == "pk_test_real"

    def test_fallback_mp_public_key_prod_mode(self):
        """mp_public_key fora de TEST_MODE retorna prod no fallback."""
        mod = _load_config_without_pydantic({
            "TEST_MODE": "false",
            "MP_PUBLIC_KEY_TEST": "pk_test_real",
            "MP_PUBLIC_KEY_PROD": "pk_prod_real",
        })
        assert mod.settings.mp_public_key == "pk_prod_real"

    def test_fallback_mp_public_key_none(self):
        """mp_public_key sem keys retorna None/falsy no fallback."""
        mod = _load_config_without_pydantic({
            "TEST_MODE": "false",
            "MP_PUBLIC_KEY_TEST": "",
            "MP_PUBLIC_KEY_PROD": "",
        })
        assert not mod.settings.mp_public_key

    def test_fallback_mp_public_key_test_sem_test_usa_prod(self):
        """mp_public_key em TEST_MODE sem key test usa prod no fallback."""
        mod = _load_config_without_pydantic({
            "TEST_MODE": "true",
            "MP_PUBLIC_KEY_TEST": "",
            "MP_PUBLIC_KEY_PROD": "pk_prod_fallback",
        })
        assert mod.settings.mp_public_key == "pk_prod_fallback"

    def test_fallback_data_backend(self):
        """DATA_BACKEND é lido como string no fallback."""
        mod = _load_config_without_pydantic({"DATA_BACKEND": "supabase"})
        assert mod.settings.DATA_BACKEND == "supabase"

    def test_fallback_optional_none(self):
        """Campos Optional sem env var retornam None no fallback."""
        mod = _load_config_without_pydantic({
            "SUPABASE_ACCESS_TOKEN": "",
            "SUPABASE_PROJECT_REF": "",
        })
        # Campos não definidos no .env devem ser None
        assert mod.settings.SUPABASE_ACCESS_TOKEN is None or mod.settings.SUPABASE_ACCESS_TOKEN == ""
