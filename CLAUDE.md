# Percepxion MCP Server

This repo is the Percepxion MCP server. When a user opens this project, detect their OS and guide them through setup to connect this server to their MCP client.

## Guided Setup

When a user asks for help setting up, follow these steps in order:

1. **Detect OS**: Ask if they are on macOS/Linux, Windows (native), or Windows with WSL.
2. **Check credentials**: Ask which environment they are connecting to:
   - Production (api.percepxion.ai), default
   - Sandbox/lab (api.gopercepxion.ai), Lantronix internal
3. **Select credential provider**: Ask which credential provider they want:
   - `env` (default): credentials in .env file
   - `vault`: HashiCorp Vault, ask for VAULT_ADDR, VAULT_TOKEN, VAULT_SECRET_PATH
   - `aws`: AWS Secrets Manager, ask for AWS_SECRET_NAME, AWS_REGION
4. **Create .env**: Copy `.env.example` to `.env` and populate with their values.
5. **Install dependencies**: Run `pip install -e .` in the repo directory.
6. **Add to MCP client config**: Use the appropriate template from `config/` for their client and OS. For Claude Desktop, add the JSON block to `claude_desktop_config.json`.
7. **Verify**: Ask them to restart their MCP client, then call `login_with_env` to confirm.

## Key Files

- `src/percepxion_mcp/server.py`, all MCP tool definitions
- `src/percepxion_mcp/client.py`, HTTP session and helpers
- `src/percepxion_mcp/config.py`, environment variable reads
- `src/percepxion_mcp/cli_policy.py`, CLI command policy (read-only default)
- `src/percepxion_mcp/providers/`, credential providers (env/vault/aws)
- `config/`, example MCP client config files
- `config/setup-instructions.md`, detailed setup reference
- `docs/tools.md`, full tool reference
- `docs/adding-new-tools.md`, guide for adding new tools

## API Endpoints

- Production: https://api.percepxion.ai/api
- Sandbox (Lantronix internal lab): https://api.gopercepxion.ai/api

## CLI Command Policy

By default, `send_direct_cli_command` only allows read-only commands (show, get, ping, etc.).
To enable write commands, set `PERCEPXION_CLI_WRITE_ENABLED=true`.
To disable all filtering (use with caution), set `PERCEPXION_CLI_YOLO=true`.

## Running Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```
