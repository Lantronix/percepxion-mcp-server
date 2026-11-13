# src/percepxion_mcp/providers/azure.py
import json
import os
from urllib.parse import urlparse

from . import CredentialProvider, Credentials

try:
    from azure.core.exceptions import (  # type: ignore
        AzureError,
        ClientAuthenticationError,
        HttpResponseError,
        ResourceNotFoundError,
    )
    from azure.identity import DefaultAzureCredential  # type: ignore
    from azure.keyvault.secrets import SecretClient  # type: ignore
except ImportError:
    SecretClient = None  # type: ignore
    DefaultAzureCredential = None  # type: ignore
    AzureError = ClientAuthenticationError = HttpResponseError = ResourceNotFoundError = None  # type: ignore


class AzureKeyVaultProvider(CredentialProvider):
    """Read Percepxion credentials from an Azure Key Vault secret.

    Env vars:
        AZURE_KEYVAULT_URL          Vault URL, e.g. https://my-vault.vault.azure.net
        AZURE_KEYVAULT_SECRET_NAME  Secret holding a JSON object with "username" and
                                    "password" (default: percepxion-credentials)

    Authentication uses DefaultAzureCredential: managed identity, workload identity,
    AZURE_CLIENT_ID / AZURE_TENANT_ID / AZURE_CLIENT_SECRET, or an ``az login``
    session. The identity needs the "Key Vault Secrets User" role (or a get-secret
    access policy) on the vault. The secret value never appears in error messages.
    """

    def __init__(self) -> None:
        if SecretClient is None or DefaultAzureCredential is None:
            raise RuntimeError(
                "azure-identity and azure-keyvault-secrets are required for the 'azure' provider. "
                "Install with: pip install percepxion-mcp-server[azure]"
            )
        url = (os.getenv("AZURE_KEYVAULT_URL") or "").strip()
        if not url:
            raise RuntimeError("AZURE_KEYVAULT_URL must be set when using the 'azure' provider.")
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise RuntimeError(
                f"AZURE_KEYVAULT_URL must be an https URL like https://my-vault.vault.azure.net, got {url!r}"
            )
        self.vault_url = url
        self.secret_name = (os.getenv("AZURE_KEYVAULT_SECRET_NAME") or "percepxion-credentials").strip()
        self._client = SecretClient(vault_url=url, credential=DefaultAzureCredential())

    def get_credentials(self) -> Credentials:
        where = f"secret {self.secret_name!r} in {self.vault_url}"
        try:
            secret = self._client.get_secret(self.secret_name)
        except ResourceNotFoundError as exc:
            raise RuntimeError(
                f"Azure Key Vault: {where} not found. Check AZURE_KEYVAULT_SECRET_NAME."
            ) from exc
        except ClientAuthenticationError as exc:
            raise RuntimeError(
                "Azure Key Vault: no usable Azure identity. Configure managed identity, "
                "AZURE_CLIENT_ID/AZURE_TENANT_ID/AZURE_CLIENT_SECRET, or run 'az login'."
            ) from exc
        except HttpResponseError as exc:
            if getattr(exc, "status_code", None) == 403:
                raise RuntimeError(
                    f"Azure Key Vault: access denied to {where}. Grant the identity the "
                    "'Key Vault Secrets User' role or a get-secret access policy."
                ) from exc
            raise RuntimeError(
                f"Azure Key Vault request failed for {where} (HTTP {getattr(exc, 'status_code', '?')})"
            ) from exc
        except AzureError as exc:
            raise RuntimeError(
                f"Azure Key Vault: can't reach {self.vault_url} ({type(exc).__name__}). "
                "Check AZURE_KEYVAULT_URL, DNS and network access to the vault."
            ) from exc

        try:
            data = json.loads(secret.value or "")
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"Azure Key Vault {where} must hold a JSON object with 'username' and 'password'"
            ) from exc
        if not isinstance(data, dict) or not data.get("username") or not data.get("password"):
            raise RuntimeError(
                f"Azure Key Vault {where} must hold a JSON object with 'username' and 'password'"
            )
        return {"username": str(data["username"]), "password": str(data["password"])}
