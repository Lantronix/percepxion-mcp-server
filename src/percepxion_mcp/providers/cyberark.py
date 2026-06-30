import os

import requests

from . import CredentialProvider, Credentials


class CyberArkProvider(CredentialProvider):
    """Fetches Percepxion admin credentials from CyberArk CCP at login time.

    Required env vars:
        CYBERARK_URL      Base URL of the CCP server (e.g. https://cyberark.internal)
        CYBERARK_APP_ID   Registered AppID with access to the safe
        CYBERARK_SAFE     Safe name where the Percepxion account lives
        CYBERARK_OBJECT   Object name for the Percepxion admin account in the safe

    Optional (mTLS -- enabled automatically when both paths are set):
        CYBERARK_CERT_PATH   Path to client certificate (.pem or .crt)
        CYBERARK_KEY_PATH    Path to client private key (.pem)
        CYBERARK_VERIFY_SSL  Set to 'false' to skip server cert verification (lab use only)
    """

    def __init__(self) -> None:
        self._url = os.getenv("CYBERARK_URL", "").rstrip("/")
        self._app_id = os.getenv("CYBERARK_APP_ID", "")
        self._safe = os.getenv("CYBERARK_SAFE", "")
        self._object = os.getenv("CYBERARK_OBJECT", "")
        self._cert_path = os.getenv("CYBERARK_CERT_PATH")
        self._key_path = os.getenv("CYBERARK_KEY_PATH")
        self._verify_ssl = os.getenv("CYBERARK_VERIFY_SSL", "true").lower() != "false"

        missing = [
            k
            for k, v in {
                "CYBERARK_URL": self._url,
                "CYBERARK_APP_ID": self._app_id,
                "CYBERARK_SAFE": self._safe,
                "CYBERARK_OBJECT": self._object,
            }.items()
            if not v
        ]
        if missing:
            raise RuntimeError(f"CyberArk provider requires: {', '.join(missing)}")

    def _cert(self) -> tuple[str, str] | str | None:
        if self._cert_path and self._key_path:
            return (self._cert_path, self._key_path)
        if self._cert_path:
            return self._cert_path
        return None

    def get_credentials(self) -> Credentials:
        params = {
            "AppID": self._app_id,
            "Safe": self._safe,
            "Object": self._object,
        }
        try:
            r = requests.get(
                f"{self._url}/AIMWebService/api/Accounts",
                params=params,
                cert=self._cert(),
                verify=self._verify_ssl,
                timeout=30,
            )
        except requests.exceptions.SSLError as exc:
            raise RuntimeError(f"CyberArk mTLS error: {exc}") from exc
        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(f"CyberArk connection failed: {exc}") from exc

        if r.status_code == 404:
            raise RuntimeError(
                f"CyberArk: no account found for object {self._object!r} in safe {self._safe!r}. "
                "Verify CYBERARK_SAFE and CYBERARK_OBJECT match what is in the vault."
            )
        if r.status_code == 403:
            raise RuntimeError(
                f"CyberArk: access denied for AppID {self._app_id!r}. "
                "Verify the AppID is allowed to access this safe and is registered on this host."
            )
        r.raise_for_status()

        data = r.json()
        username = data.get("UserName") or data.get("username")
        password = data.get("Content") or data.get("password")

        if not username or not password:
            raise RuntimeError(
                f"CyberArk returned account for {self._object!r} but UserName or Content is missing. "
                f"Got keys: {list(data.keys())}"
            )

        return {"username": username, "password": password}
