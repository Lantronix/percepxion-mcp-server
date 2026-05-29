# src/percepxion_mcp/providers/env.py
import os
from . import CredentialProvider, Credentials


class EnvProvider(CredentialProvider):
    def get_credentials(self) -> Credentials:
        username = os.getenv("PERCEPXION_USERNAME")
        password = os.getenv("PERCEPXION_PASSWORD")
        if not username or not password:
            raise RuntimeError(
                "PERCEPXION_USERNAME and PERCEPXION_PASSWORD must be set when using the 'env' provider."
            )
        return {"username": username, "password": password}
