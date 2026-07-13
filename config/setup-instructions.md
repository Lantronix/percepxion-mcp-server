# Percepxion MCP Server, Setup Instructions

## Prerequisites

- Python 3.11 or newer
- A Percepxion account (production at percepxion.ai or sandbox at gopercepxion.ai)

## Step 1: Clone and install

```bash
git clone https://github.com/Lantronix/percepxion-mcp-server
cd percepxion-mcp-server
pip install -e .
```

## Step 2: Configure credentials

Copy the example env file:

```bash
cp .env.example .env
```

Edit `.env`:

```
PERCEPXION_API_URL=https://api.percepxion.ai/api
# Use https://api.gopercepxion.ai/api for the Lantronix sandbox environment

PERCEPXION_USERNAME=your-email@example.com
PERCEPXION_PASSWORD=your-password

# Optional: default organization ID if your account has multiple organizations
PERCEPXION_DEFAULT_ORGANIZATION_ID=
# Deprecated alias, still works: PERCEPXION_DEFAULT_TENANT_ID=

# Optional: restrict firmware uploads to a specific directory
PERCEPXION_FIRMWARE_DIR=
```

### Vault provider (optional)

If your team stores credentials in HashiCorp Vault:

```
PERCEPXION_CREDENTIAL_PROVIDER=vault
VAULT_ADDR=https://vault.example.com
VAULT_TOKEN=hvs.XXXX
VAULT_SECRET_PATH=secret/data/percepxion
```

The secret at that path must have `username` and `password` keys.

### AWS Secrets Manager provider (optional)

```
PERCEPXION_CREDENTIAL_PROVIDER=aws
AWS_SECRET_NAME=percepxion/credentials
AWS_REGION=us-east-1
```

Install the extra: `pip install -e ".[aws]"`

The secret value must be a JSON object with `username` and `password` keys.

## Step 3: Add to your MCP client

### Claude Desktop (macOS/Linux)

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`~/.config/Claude/claude_desktop_config.json` (Linux):

```json
{
  "mcpServers": {
    "percepxion": {
      "command": "python",
      "args": ["percepxion_mcp.py"],
      "cwd": "/path/to/percepxion-mcp-server",
      "env": {
        "PYTHONUNBUFFERED": "1",
        "PERCEPXION_API_URL": "https://api.percepxion.ai/api",
        "PERCEPXION_USERNAME": "your-email@example.com",
        "PERCEPXION_PASSWORD": "your-password"
      }
    }
  }
}
```

### Claude Desktop (Windows + WSL)

```json
{
  "mcpServers": {
    "percepxion": {
      "command": "wsl.exe",
      "args": ["bash", "-lc", "cd /path/to/percepxion-mcp-server && .venv/bin/python percepxion_mcp.py"],
      "env": {
        "PYTHONUNBUFFERED": "1",
        "PERCEPXION_API_URL": "https://api.percepxion.ai/api",
        "PERCEPXION_USERNAME": "your-email@example.com",
        "PERCEPXION_PASSWORD": "your-password"
      }
    }
  }
}
```

Replace `/path/to/percepxion-mcp-server` with the actual WSL path.

## Step 4: Verify

Restart your MCP client, then call:

```
login_with_env
```

You should see: `Authenticated successfully.`

## CLI Command Policy

`send_direct_cli_command` is read-only by default. Only `show`, `get`, `ping`, and similar
read commands pass through. To change this, add to your `.env`:

```
# Allow write commands (set, configure, etc.):
PERCEPXION_CLI_WRITE_ENABLED=true

# Add commands to the deny list (always blocked):
PERCEPXION_CLI_DENY_COMMANDS=clear counters,debug ip packet

# Explicit allowlist (if set, only these commands pass through):
PERCEPXION_CLI_PERMIT_COMMANDS=show version,show interfaces,ping

# Disable all filtering (use with extreme caution):
PERCEPXION_CLI_YOLO=true
```

## Troubleshooting

**Auth fails with 401:** Check that `PERCEPXION_API_URL` matches your environment. Production
uses `api.percepxion.ai`, the sandbox uses `api.gopercepxion.ai`.

**"PERCEPXION_USERNAME and PERCEPXION_PASSWORD must be set":** The `.env` file is not being
loaded. Confirm it exists in the repo root and contains the correct variable names.

**"Write commands are disabled":** Set `PERCEPXION_CLI_WRITE_ENABLED=true` in your `.env`
if you need to send write commands via `send_direct_cli_command`.
