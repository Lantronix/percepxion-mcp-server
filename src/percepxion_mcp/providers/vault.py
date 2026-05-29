# src/percepxion_mcp/providers/vault.py
import os
import requests
from . import CredentialProvider, Credentials


class VaultProvider(CredentialProvider):
    """Read credentials from HashiCorp Vault KV v2 secrets engine."""

    def __init__(self) -> None:
        self.addr = os.environ["VAULT_ADDR"]
        self.token = os.environ["VAULT_TOKEN"]
        self.path = os.getenv("VAULT_SECRET_PATH", "secret/data/percepxion")

    def get_credentials(self) -> Credentials:
        url = f"{self.addr.rstrip('/')}/v1/{self.path}"
        resp = requests.get(
            url,
            headers={"X-Vault-Token": self.token},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()["data"]["data"]
        return {"username": data["username"], "password": data["password"]}
