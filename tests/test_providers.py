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
