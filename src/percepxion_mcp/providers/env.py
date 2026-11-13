# src/percepxion_mcp/providers/env.py
from . import CredentialProvider, Credentials, read_env_secret


class EnvProvider(CredentialProvider):
    """PERCEPXION_USERNAME and PERCEPXION_PASSWORD, each also readable from a
    file named by PERCEPXION_USERNAME_FILE / PERCEPXION_PASSWORD_FILE."""

    def get_credentials(self) -> Credentials:
        username = read_env_secret("PERCEPXION_USERNAME")
        password = read_env_secret("PERCEPXION_PASSWORD")
        if not username or not password:
            raise RuntimeError(
                "PERCEPXION_USERNAME and PERCEPXION_PASSWORD (or their _FILE forms) "
                "must be set when using the 'env' provider."
            )
        return {"username": username, "password": password}
