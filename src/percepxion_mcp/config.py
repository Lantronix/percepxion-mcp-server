# src/percepxion_mcp/config.py
import os

from dotenv import load_dotenv

load_dotenv()

API_BASE_URL: str = os.getenv("PERCEPXION_API_URL", "https://api.percepxion.ai/api").rstrip("/")
USERNAME: str | None = os.getenv("PERCEPXION_USERNAME")
PASSWORD: str | None = os.getenv("PERCEPXION_PASSWORD")
API_TOKEN: str | None = os.getenv("PERCEPXION_API_TOKEN")  # experimental: proposed C-API-12 token auth
REQUEST_TIMEOUT: int = int(os.getenv("PERCEPXION_REQUEST_TIMEOUT", "45"))
FIRMWARE_DIR: str | None = os.getenv("PERCEPXION_FIRMWARE_DIR")

# Percepxion's product hierarchy is Project > Portal > Organization; "tenant" is
# legacy/internal terminology that predates that naming. PERCEPXION_DEFAULT_ORGANIZATION_ID
# is the primary env var. PERCEPXION_DEFAULT_TENANT_ID is kept as a backward-compatible
# alias: if both are set, the new name wins; if only the old one is set, it's used as-is.
DEFAULT_ORGANIZATION_ID: str | None = os.getenv("PERCEPXION_DEFAULT_ORGANIZATION_ID") or os.getenv(
    "PERCEPXION_DEFAULT_TENANT_ID"
)
DEFAULT_TENANT_ID: str | None = DEFAULT_ORGANIZATION_ID  # deprecated alias, do not remove
CREDENTIAL_PROVIDER: str = os.getenv("PERCEPXION_CREDENTIAL_PROVIDER", "env")
# stdio (default, local clients), http (Streamable HTTP at /mcp), sse (legacy
# HTTP+SSE at /sse, with messages under /messages/) or both (http and sse in one
# process, for remote clients old and new).
MCP_TRANSPORT: str = os.getenv("MCP_TRANSPORT", "stdio").strip().lower()
# Streamable HTTP keeps a session per client (Mcp-Session-Id) by default, so run
# one replica. MCP_STATELESS_HTTP=true answers each request on its own instead.
MCP_STATELESS_HTTP: bool = os.getenv("MCP_STATELESS_HTTP", "false").strip().lower() in ("1", "true", "yes")
# Optional comma-separated Host and Origin allowlists for the HTTP transports.
# Empty keeps FastMCP's default: checked only when bound to a loopback address.
MCP_ALLOWED_HOSTS: list[str] = [h.strip() for h in os.getenv("MCP_ALLOWED_HOSTS", "").split(",") if h.strip()]
MCP_ALLOWED_ORIGINS: list[str] = [o.strip() for o in os.getenv("MCP_ALLOWED_ORIGINS", "").split(",") if o.strip()]
MCP_HOST: str = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT: int = int(os.getenv("MCP_PORT", "8765"))
