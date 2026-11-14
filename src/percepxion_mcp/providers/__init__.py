# src/percepxion_mcp/providers/__init__.py
import os
from abc import ABC, abstractmethod
from typing import TypedDict


class Credentials(TypedDict):
    username: str
    password: str


class CredentialProvider(ABC):
    @abstractmethod
    def get_credentials(self) -> Credentials: ...


def read_env_secret(name: str) -> str | None:
    """Return env var ``name``, or the first line of the file named by ``name_FILE``.

    The ``_FILE`` form is the Docker and Kubernetes secrets convention. Setting
    both forms is an error, so a stale variable can't silently win.
    """
    value = os.getenv(name)
    path = (os.getenv(f"{name}_FILE") or "").strip()
    if not path:
        return value or None
    if value:
        raise RuntimeError(f"Set {name} or {name}_FILE, not both.")
    try:
        with open(path, encoding="utf-8") as fh:
            first = fh.readline().rstrip("\r\n")
    except OSError as exc:
        raise RuntimeError(f"{name}_FILE can't be read: {exc}") from exc
    return first or None


def get_provider(provider_name: str) -> CredentialProvider:
    if provider_name == "env":
        from .env import EnvProvider
        return EnvProvider()
    if provider_name == "vault":
        from .vault import VaultProvider
        return VaultProvider()
    if provider_name == "aws":
        from .aws import AwsProvider
        return AwsProvider()
    if provider_name == "cyberark":
        from .cyberark import CyberArkProvider
        return CyberArkProvider()
    if provider_name == "azure":
        from .azure import AzureKeyVaultProvider
        return AzureKeyVaultProvider()
    if provider_name == "command":
        from .command import CommandProvider
        return CommandProvider()
    raise ValueError(
        f"Unknown credential provider: '{provider_name}'. Choose: env, vault, aws, azure, cyberark, command"
    )
