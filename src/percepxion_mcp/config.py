# src/percepxion_mcp/config.py
import os

from dotenv import load_dotenv

load_dotenv()

API_BASE_URL: str = os.getenv("PERCEPXION_API_URL", "https://api.percepxion.ai/api").rstrip("/")
USERNAME: str | None = os.getenv("PERCEPXION_USERNAME")
PASSWORD: str | None = os.getenv("PERCEPXION_PASSWORD")
REQUEST_TIMEOUT: int = int(os.getenv("PERCEPXION_REQUEST_TIMEOUT", "45"))
FIRMWARE_DIR: str | None = os.getenv("PERCEPXION_FIRMWARE_DIR")
DEFAULT_TENANT_ID: str | None = os.getenv("PERCEPXION_DEFAULT_TENANT_ID")
CREDENTIAL_PROVIDER: str = os.getenv("PERCEPXION_CREDENTIAL_PROVIDER", "env")
