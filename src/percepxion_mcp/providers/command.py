# src/percepxion_mcp/providers/command.py
import json
import os
import shlex
import subprocess

from . import CredentialProvider, Credentials, read_env_secret

_STDERR_LIMIT = 200


class CommandProvider(CredentialProvider):
    """Run an operator-configured command and read credentials from its output.

    Works with any secrets tool that has a CLI (1Password ``op``, Bitwarden
    ``bws``, Infisical, Delinea, Keeper, in-house scripts).

    Env vars:
        PERCEPXION_CREDENTIAL_COMMAND          Command line to run. Split with shlex
                                               and run without a shell.
        PERCEPXION_CREDENTIAL_COMMAND_TIMEOUT  Seconds before the command is killed
                                               (default 30).

    Output, either:
        a JSON object with "username" and "password" keys, or
        the password alone on the first line, with the username taken from
        PERCEPXION_USERNAME (or PERCEPXION_USERNAME_FILE).

    The command comes only from the server's environment. No tool argument can
    set or change it. Command output never appears in error messages.
    """

    def __init__(self) -> None:
        raw = (os.getenv("PERCEPXION_CREDENTIAL_COMMAND") or "").strip()
        if not raw:
            raise RuntimeError(
                "PERCEPXION_CREDENTIAL_COMMAND must be set when using the 'command' provider."
            )
        self._argv = shlex.split(raw, posix=os.name != "nt")
        timeout_raw = os.getenv("PERCEPXION_CREDENTIAL_COMMAND_TIMEOUT", "30")
        try:
            self._timeout = float(timeout_raw)
        except ValueError as exc:
            raise RuntimeError(
                f"PERCEPXION_CREDENTIAL_COMMAND_TIMEOUT must be a number, got {timeout_raw!r}"
            ) from exc
        if self._timeout <= 0:
            raise RuntimeError("PERCEPXION_CREDENTIAL_COMMAND_TIMEOUT must be greater than 0")

    def get_credentials(self) -> Credentials:
        name = self._argv[0]
        try:
            proc = subprocess.run(
                self._argv,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(f"Credential command not found: {name}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"Credential command {name} timed out after {self._timeout:g}s"
            ) from exc

        if proc.returncode != 0:
            detail = (proc.stderr or "").strip()[:_STDERR_LIMIT]
            msg = f"Credential command {name} exited with status {proc.returncode}"
            raise RuntimeError(f"{msg}: {detail}" if detail else msg)

        return self._parse(proc.stdout or "", name)

    @staticmethod
    def _parse(stdout: str, name: str) -> Credentials:
        text = stdout.strip()
        if text.startswith("{"):
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Credential command {name} printed invalid JSON") from exc
            username = data.get("username") if isinstance(data, dict) else None
            password = data.get("password") if isinstance(data, dict) else None
            if not username or not password:
                raise RuntimeError(
                    f"Credential command {name} JSON output needs 'username' and 'password' keys"
                )
            return {"username": str(username), "password": str(password)}

        password = text.splitlines()[0] if text else ""
        if not password:
            raise RuntimeError(f"Credential command {name} printed nothing")
        username = read_env_secret("PERCEPXION_USERNAME")
        if not username:
            raise RuntimeError(
                "Credential command printed a bare password, so PERCEPXION_USERNAME "
                "(or PERCEPXION_USERNAME_FILE) must be set."
            )
        return {"username": username, "password": password}
