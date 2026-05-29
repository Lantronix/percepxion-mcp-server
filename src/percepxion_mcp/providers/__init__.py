# src/percepxion_mcp/providers/__init__.py
from abc import ABC, abstractmethod
from typing import TypedDict


class Credentials(TypedDict):
    username: str
    password: str


class CredentialProvider(ABC):
    @abstractmethod
    def get_credentials(self) -> Credentials: ...


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
    raise ValueError(f"Unknown credential provider: '{provider_name}'. Choose: env, vault, aws")
