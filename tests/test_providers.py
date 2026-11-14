# tests/test_providers.py
import json
import pytest
from unittest.mock import patch, MagicMock
from percepxion_mcp.providers import get_provider, CredentialProvider


# --- factory ---

def test_get_provider_env_returns_env_provider():
    from percepxion_mcp.providers.env import EnvProvider
    p = get_provider("env")
    assert isinstance(p, EnvProvider)


def test_get_provider_unknown_raises():
    with pytest.raises(ValueError, match="Unknown credential provider"):
        get_provider("bogus")


# --- EnvProvider ---

def test_env_provider_returns_credentials():
    from percepxion_mcp.providers.env import EnvProvider
    with patch.dict("os.environ", {
        "PERCEPXION_USERNAME": "testuser",
        "PERCEPXION_PASSWORD": "testpass",
    }):
        p = EnvProvider()
        creds = p.get_credentials()
        assert creds["username"] == "testuser"
        assert creds["password"] == "testpass"


def test_env_provider_raises_when_missing():
    from percepxion_mcp.providers.env import EnvProvider
    with patch.dict("os.environ", {}, clear=True):
        p = EnvProvider()
        with pytest.raises(RuntimeError, match="PERCEPXION_USERNAME"):
            p.get_credentials()


# --- VaultProvider ---

def test_vault_provider_reads_from_vault(httpserver):
    from percepxion_mcp.providers.vault import VaultProvider
    httpserver.expect_request(
        "/v1/secret/data/percepxion",
        method="GET",
    ).respond_with_json({
        "data": {"data": {"username": "vaultuser", "password": "vaultpass"}}
    })

    with patch.dict("os.environ", {
        "VAULT_ADDR": httpserver.url_for(""),
        "VAULT_TOKEN": "test-token",
        "VAULT_SECRET_PATH": "secret/data/percepxion",
    }):
        p = VaultProvider()
        creds = p.get_credentials()
        assert creds["username"] == "vaultuser"
        assert creds["password"] == "vaultpass"


def test_vault_provider_raises_on_missing_env():
    from percepxion_mcp.providers.vault import VaultProvider
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(KeyError):
            VaultProvider()


# --- AwsProvider ---

def test_aws_provider_reads_from_secrets_manager():
    from percepxion_mcp.providers.aws import AwsProvider
    mock_client = MagicMock()
    mock_client.get_secret_value.return_value = {
        "SecretString": json.dumps({"username": "awsuser", "password": "awspass"})
    }
    with patch.dict("os.environ", {
        "AWS_SECRET_NAME": "percepxion/credentials",
        "AWS_REGION": "us-east-1",
    }):
        with patch("percepxion_mcp.providers.aws.boto3") as mock_boto3:
            mock_boto3.client.return_value = mock_client
            p = AwsProvider()
            creds = p.get_credentials()
            assert creds["username"] == "awsuser"
            assert creds["password"] == "awspass"


# --- _FILE variables (env provider) ---

def test_env_provider_reads_password_file(tmp_path):
    from percepxion_mcp.providers.env import EnvProvider
    secret = tmp_path / "pw"
    secret.write_text("filepass\nignored second line\n")
    with patch.dict("os.environ", {
        "PERCEPXION_USERNAME": "fileuser",
        "PERCEPXION_PASSWORD_FILE": str(secret),
    }, clear=True):
        creds = EnvProvider().get_credentials()
    assert creds == {"username": "fileuser", "password": "filepass"}


def test_env_provider_reads_username_file(tmp_path):
    from percepxion_mcp.providers.env import EnvProvider
    user = tmp_path / "user"
    user.write_text("fileuser\r\n")
    with patch.dict("os.environ", {
        "PERCEPXION_USERNAME_FILE": str(user),
        "PERCEPXION_PASSWORD": "p",
    }, clear=True):
        assert EnvProvider().get_credentials()["username"] == "fileuser"


def test_env_provider_rejects_value_and_file_together(tmp_path):
    from percepxion_mcp.providers.env import EnvProvider
    secret = tmp_path / "pw"
    secret.write_text("filepass")
    with patch.dict("os.environ", {
        "PERCEPXION_USERNAME": "u",
        "PERCEPXION_PASSWORD": "envpass",
        "PERCEPXION_PASSWORD_FILE": str(secret),
    }, clear=True):
        with pytest.raises(RuntimeError, match="not both"):
            EnvProvider().get_credentials()


def test_env_provider_unreadable_file_names_variable(tmp_path):
    from percepxion_mcp.providers.env import EnvProvider
    with patch.dict("os.environ", {
        "PERCEPXION_USERNAME": "u",
        "PERCEPXION_PASSWORD_FILE": str(tmp_path / "missing"),
    }, clear=True):
        with pytest.raises(RuntimeError, match="PERCEPXION_PASSWORD_FILE can't be read"):
            EnvProvider().get_credentials()


def test_env_provider_empty_file_counts_as_missing(tmp_path):
    from percepxion_mcp.providers.env import EnvProvider
    secret = tmp_path / "pw"
    secret.write_text("\n")
    with patch.dict("os.environ", {
        "PERCEPXION_USERNAME": "u",
        "PERCEPXION_PASSWORD_FILE": str(secret),
    }, clear=True):
        with pytest.raises(RuntimeError, match="must be set"):
            EnvProvider().get_credentials()


# --- CommandProvider ---

def _py(code: str) -> str:
    import shlex
    import sys
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


def test_get_provider_command_returns_command_provider():
    from percepxion_mcp.providers.command import CommandProvider
    with patch.dict("os.environ", {"PERCEPXION_CREDENTIAL_COMMAND": "true"}, clear=True):
        assert isinstance(get_provider("command"), CommandProvider)


def test_command_provider_requires_command():
    from percepxion_mcp.providers.command import CommandProvider
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(RuntimeError, match="PERCEPXION_CREDENTIAL_COMMAND"):
            CommandProvider()


def test_command_provider_rejects_bad_timeout():
    from percepxion_mcp.providers.command import CommandProvider
    with patch.dict("os.environ", {
        "PERCEPXION_CREDENTIAL_COMMAND": "true",
        "PERCEPXION_CREDENTIAL_COMMAND_TIMEOUT": "soon",
    }, clear=True):
        with pytest.raises(RuntimeError, match="must be a number"):
            CommandProvider()


def test_command_provider_parses_json():
    from percepxion_mcp.providers.command import CommandProvider
    cmd = _py('import json; print(json.dumps({"username": "cmduser", "password": "cmdpass"}))')
    with patch.dict("os.environ", {"PERCEPXION_CREDENTIAL_COMMAND": cmd}, clear=True):
        creds = CommandProvider().get_credentials()
    assert creds == {"username": "cmduser", "password": "cmdpass"}


def test_command_provider_bare_password_uses_env_username():
    from percepxion_mcp.providers.command import CommandProvider
    cmd = _py('print("barepass")')
    with patch.dict("os.environ", {
        "PERCEPXION_CREDENTIAL_COMMAND": cmd,
        "PERCEPXION_USERNAME": "envuser",
    }, clear=True):
        creds = CommandProvider().get_credentials()
    assert creds == {"username": "envuser", "password": "barepass"}


def test_command_provider_bare_password_needs_username():
    from percepxion_mcp.providers.command import CommandProvider
    with patch.dict("os.environ", {"PERCEPXION_CREDENTIAL_COMMAND": _py('print("x")')}, clear=True):
        with pytest.raises(RuntimeError, match="PERCEPXION_USERNAME"):
            CommandProvider().get_credentials()


def test_command_provider_json_missing_keys():
    from percepxion_mcp.providers.command import CommandProvider
    cmd = _py('print(\'{"username": "u"}\')')
    with patch.dict("os.environ", {"PERCEPXION_CREDENTIAL_COMMAND": cmd}, clear=True):
        with pytest.raises(RuntimeError, match="'username' and 'password'"):
            CommandProvider().get_credentials()


def test_command_provider_failure_keeps_output_out_of_error(caplog):
    import logging
    from percepxion_mcp.providers.command import CommandProvider
    cmd = _py('import sys; print("leaked-stdout"); sys.stderr.write("leaked-stderr"); sys.exit(3)')
    with patch.dict("os.environ", {"PERCEPXION_CREDENTIAL_COMMAND": cmd}, clear=True):
        with caplog.at_level(logging.WARNING, logger="percepxion_mcp.providers.command"):
            with pytest.raises(RuntimeError) as exc:
                CommandProvider().get_credentials()
    msg = str(exc.value)
    assert "status 3" in msg
    assert "leaked-stdout" not in msg and "leaked-stderr" not in msg
    # stderr reaches the server log only, never the tool result
    assert "leaked-stderr" in caplog.text
    assert "leaked-stdout" not in caplog.text


def test_command_provider_invalid_json_hides_output():
    from percepxion_mcp.providers.command import CommandProvider
    cmd = _py('print("{not json leaked-secret")')
    with patch.dict("os.environ", {"PERCEPXION_CREDENTIAL_COMMAND": cmd}, clear=True):
        with pytest.raises(RuntimeError) as exc:
            CommandProvider().get_credentials()
    assert "invalid JSON" in str(exc.value)
    assert "leaked-secret" not in str(exc.value)


def test_command_provider_not_found():
    from percepxion_mcp.providers.command import CommandProvider
    with patch.dict("os.environ", {"PERCEPXION_CREDENTIAL_COMMAND": "/nonexistent/secrets-cli get"}, clear=True):
        with pytest.raises(RuntimeError, match="not found"):
            CommandProvider().get_credentials()


def test_command_provider_timeout():
    from percepxion_mcp.providers.command import CommandProvider
    with patch.dict("os.environ", {
        "PERCEPXION_CREDENTIAL_COMMAND": _py("import time; time.sleep(5)"),
        "PERCEPXION_CREDENTIAL_COMMAND_TIMEOUT": "0.5",
    }, clear=True):
        with pytest.raises(RuntimeError, match="timed out"):
            CommandProvider().get_credentials()


def test_command_provider_does_not_use_a_shell():
    from percepxion_mcp.providers.command import CommandProvider
    # With no shell, "$HOME" and ";" reach the program as literal arguments.
    cmd = _py('import sys, json; print(json.dumps({"username": sys.argv[1], "password": sys.argv[2]}))') + ' "$HOME" ";"'
    with patch.dict("os.environ", {"PERCEPXION_CREDENTIAL_COMMAND": cmd, "HOME": "/h"}, clear=True):
        creds = CommandProvider().get_credentials()
    assert creds == {"username": "$HOME", "password": ";"}


def test_reconfigure_credentials_accepts_command():
    from percepxion_mcp.server import reconfigure_credentials
    fn = getattr(reconfigure_credentials, "fn", reconfigure_credentials)
    with patch.dict("os.environ", {"PERCEPXION_CREDENTIAL_COMMAND": "true"}, clear=False):
        result = fn("command")
        import os
        assert os.environ["PERCEPXION_CREDENTIAL_PROVIDER"] == "command"
    assert "error" not in str(result).lower()


# --- AzureKeyVaultProvider ---
# CI doesn't install the Azure SDK, so these tests patch the module's SDK names.

class _AzBase(Exception):
    pass


class _AzErr(_AzBase):
    def __init__(self, msg="", status_code=None):
        super().__init__(msg)
        self.status_code = status_code


class _AzNotFound(_AzErr):
    pass


class _AzAuth(_AzErr):
    pass


def _azure_patches(secret_value=None, error=None):
    import percepxion_mcp.providers.azure as az
    client = MagicMock()
    if error is not None:
        client.get_secret.side_effect = error
    else:
        client.get_secret.return_value = MagicMock(value=secret_value)
    return client, [
        patch.object(az, "SecretClient", MagicMock(return_value=client)),
        patch.object(az, "DefaultAzureCredential", MagicMock()),
        patch.object(az, "ResourceNotFoundError", _AzNotFound),
        patch.object(az, "ClientAuthenticationError", _AzAuth),
        patch.object(az, "HttpResponseError", _AzErr),
        patch.object(az, "AzureError", _AzBase),
    ]


def _run_azure(env, secret_value=None, error=None):
    from contextlib import ExitStack
    from percepxion_mcp.providers.azure import AzureKeyVaultProvider
    client, patches = _azure_patches(secret_value, error)
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(patch.dict("os.environ", env, clear=True))
        provider = AzureKeyVaultProvider()
        return client, provider.get_credentials()


AZ_ENV = {"AZURE_KEYVAULT_URL": "https://example-vault.vault.azure.net"}


def test_get_provider_azure_returns_azure_provider():
    from contextlib import ExitStack
    from percepxion_mcp.providers.azure import AzureKeyVaultProvider
    _, patches = _azure_patches("{}")
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(patch.dict("os.environ", AZ_ENV, clear=True))
        assert isinstance(get_provider("azure"), AzureKeyVaultProvider)


def test_azure_reads_default_secret():
    client, creds = _run_azure(AZ_ENV, json.dumps({"username": "azuser", "password": "azpass"}))
    assert creds == {"username": "azuser", "password": "azpass"}
    client.get_secret.assert_called_once_with("percepxion-credentials")


def test_azure_custom_secret_name():
    env = dict(AZ_ENV, AZURE_KEYVAULT_SECRET_NAME="px-prod")
    client, _ = _run_azure(env, json.dumps({"username": "u", "password": "p"}))
    client.get_secret.assert_called_once_with("px-prod")


def test_azure_sdk_missing():
    import percepxion_mcp.providers.azure as az
    with patch.object(az, "SecretClient", None), patch.dict("os.environ", AZ_ENV, clear=True):
        with pytest.raises(RuntimeError, match=r"percepxion-mcp-server\[azure\]"):
            az.AzureKeyVaultProvider()


@pytest.mark.parametrize("url", ["", "http://example-vault.vault.azure.net", "example-vault"])
def test_azure_rejects_bad_url(url):
    from contextlib import ExitStack
    from percepxion_mcp.providers.azure import AzureKeyVaultProvider
    _, patches = _azure_patches("{}")
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        stack.enter_context(patch.dict("os.environ", {"AZURE_KEYVAULT_URL": url}, clear=True))
        with pytest.raises(RuntimeError, match="AZURE_KEYVAULT_URL"):
            AzureKeyVaultProvider()


def test_azure_not_found():
    with pytest.raises(RuntimeError, match="not found"):
        _run_azure(AZ_ENV, error=_AzNotFound("nope", 404))


def test_azure_no_identity():
    with pytest.raises(RuntimeError, match="az login"):
        _run_azure(AZ_ENV, error=_AzAuth("no credential"))


def test_azure_access_denied():
    with pytest.raises(RuntimeError, match="Key Vault Secrets User"):
        _run_azure(AZ_ENV, error=_AzErr("forbidden", 403))


def test_azure_other_http_error():
    with pytest.raises(RuntimeError, match="HTTP 500"):
        _run_azure(AZ_ENV, error=_AzErr("boom", 500))


def test_azure_unreachable_vault():
    with pytest.raises(RuntimeError, match="can't reach"):
        _run_azure(AZ_ENV, error=_AzBase("Failed to resolve host"))


@pytest.mark.parametrize("value", ["leaked-secret", '{"username": "u"}', '["leaked-secret"]'])
def test_azure_bad_secret_value_hidden(value):
    with pytest.raises(RuntimeError) as exc:
        _run_azure(AZ_ENV, value)
    assert "'username' and 'password'" in str(exc.value)
    assert "leaked-secret" not in str(exc.value)
