import importlib

from percepxion_mcp import config as config_mod


def _reload_config(monkeypatch):
    # Avoid picking up values from a real local .env file during reload; the
    # env vars set/removed via monkeypatch in each test are the source of truth.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: False)
    return importlib.reload(config_mod)


def test_default_organization_id_env_var_used(monkeypatch):
    monkeypatch.setenv("PERCEPXION_DEFAULT_ORGANIZATION_ID", "org-123")
    monkeypatch.delenv("PERCEPXION_DEFAULT_TENANT_ID", raising=False)
    reloaded = _reload_config(monkeypatch)
    assert reloaded.DEFAULT_ORGANIZATION_ID == "org-123"
    assert reloaded.DEFAULT_TENANT_ID == "org-123"


def test_legacy_default_tenant_id_env_var_still_works(monkeypatch):
    monkeypatch.delenv("PERCEPXION_DEFAULT_ORGANIZATION_ID", raising=False)
    monkeypatch.setenv("PERCEPXION_DEFAULT_TENANT_ID", "tenant-legacy")
    reloaded = _reload_config(monkeypatch)
    assert reloaded.DEFAULT_ORGANIZATION_ID == "tenant-legacy"
    assert reloaded.DEFAULT_TENANT_ID == "tenant-legacy"


def test_new_env_var_takes_precedence_when_both_set(monkeypatch):
    monkeypatch.setenv("PERCEPXION_DEFAULT_ORGANIZATION_ID", "org-new")
    monkeypatch.setenv("PERCEPXION_DEFAULT_TENANT_ID", "tenant-old")
    reloaded = _reload_config(monkeypatch)
    assert reloaded.DEFAULT_ORGANIZATION_ID == "org-new"
    assert reloaded.DEFAULT_TENANT_ID == "org-new"


def test_neither_env_var_set_results_in_none(monkeypatch):
    monkeypatch.delenv("PERCEPXION_DEFAULT_ORGANIZATION_ID", raising=False)
    monkeypatch.delenv("PERCEPXION_DEFAULT_TENANT_ID", raising=False)
    reloaded = _reload_config(monkeypatch)
    assert reloaded.DEFAULT_ORGANIZATION_ID is None
    assert reloaded.DEFAULT_TENANT_ID is None
