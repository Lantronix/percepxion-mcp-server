> **This repository has been archived.** The project has moved to the official Lantronix organization:
> **[github.com/Lantronix/percepxion-mcp-server](https://github.com/Lantronix/percepxion-mcp-server)**

# Percepxion MCP Server

A Python [FastMCP](https://github.com/jlowin/fastmcp) server that exposes the [Percepxion](https://gopercepxion.ai) REST API as MCP tools. Connect it to Claude Desktop, Claude Code, or any MCP-compatible client to manage out-of-band infrastructure through natural language.

## What is Percepxion?

Percepxion is a SaaS platform for out-of-band (OOB) network device management. It connects to console servers, serial port aggregators, and remote access devices to provide fleet-wide visibility, configuration management, firmware updates, CLI access, and compliance reporting, independent of the primary network path.

This MCP server gives AI assistants direct access to Percepxion's management capabilities.

---

## Use cases

- Inventory discovery: find all devices in an organization, filter by model or firmware version
- Remote CLI execution: run commands on a device and retrieve output through Percepxion
- Config management: push individual property changes or clone a full config from a reference device
- Firmware compliance: compare fleet firmware versions against a target and identify non-compliant devices
- Firmware updates: upload firmware and target a Smart Group for coordinated rollout
- Log retrieval: pull syslogs or access logs from devices on demand
- Audit investigation: search platform audit records by user, time range, or action
- Tenant management: list organizations and scope operations to specific tenants

---

## How it works

The server runs locally and communicates with the Percepxion API over HTTPS. Authentication uses username/password, the server exchanges these for session tokens and holds them in memory for the lifetime of the process.

Many Percepxion operations are asynchronous. Tools that trigger device actions (CLI commands, config pushes, firmware updates, syslog requests) create a Percepxion job group and return the job record. Use `search_job_groups` to poll job status and retrieve results.

**Response envelope, all tools return this structure:**

```json
{ "ok": true, "data": { ... }, "status_code": 200 }
```

```json
{ "ok": false, "error": "...", "status_code": 401, "details": { ... } }
```

---

## Prerequisites

- Python 3.11 or later (3.12 recommended)
- Network access to your Percepxion API base URL
- A Percepxion username and password with appropriate permissions

---

## Quick start

### Linux or WSL

```bash
git clone https://github.com/keelhaulin/percepxion-MCP-Server.git
cd percepxion-MCP-Server

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .

cp .env.example .env
# Edit .env, set PERCEPXION_USERNAME, PERCEPXION_PASSWORD, PERCEPXION_API_URL
```

Test the server starts:

```bash
python percepxion_mcp.py
```

The server blocks and waits for an MCP client connection. Connect a client, then call `login_with_env` to authenticate.

### Docker

```bash
docker build -t percepxion-mcp-server .
docker run --rm -it --env-file .env percepxion-mcp-server
```

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `PERCEPXION_USERNAME` | Yes |, | Percepxion login username |
| `PERCEPXION_PASSWORD` | Yes |, | Percepxion login password |
| `PERCEPXION_API_URL` | Yes | `https://api.gopercepxion.ai/api` | Percepxion API base URL |
| `PERCEPXION_REQUEST_TIMEOUT` | No | `45` | HTTP timeout in seconds. Raise for large log downloads or slow links. |
| `PERCEPXION_FIRMWARE_DIR` | No |, | If set, firmware uploads are restricted to files in this directory. Recommended for shared or automated deployments. |

Keep `.env` out of version control. The repo includes `.env.example` as a starting point.

### Credential Providers

By default, the server reads credentials from environment variables in `.env`. You can also store credentials in HashiCorp Vault or AWS Secrets Manager.

Set `PERCEPXION_CREDENTIAL_PROVIDER` to one of:

- `env` (default): Read from `PERCEPXION_USERNAME` and `PERCEPXION_PASSWORD` env vars
- `vault`: Fetch credentials from HashiCorp Vault
- `aws`: Fetch credentials from AWS Secrets Manager

Full setup details in [`config/setup-instructions.md`](config/setup-instructions.md).

---

## Connect an MCP client

### Claude Code (recommended)

Add the server to your Claude Code MCP configuration:

```bash
claude mcp add percepxion -- /path/to/percepxion-MCP-Server/.venv/bin/python /path/to/percepxion-MCP-Server/percepxion_mcp.py
```

Or add manually to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "percepxion": {
      "command": "/path/to/percepxion-MCP-Server/.venv/bin/python",
      "args": ["/path/to/percepxion-MCP-Server/percepxion_mcp.py"],
      "env": { "PYTHONUNBUFFERED": "1" }
    }
  }
}
```

### Claude Desktop (Linux or macOS)

Copy `config/claude_desktop_config.example.json` and fill in your paths. The file goes in:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

### Claude Desktop (Windows calling WSL)

Use `config/claude_desktop_config.wsl_windows.example.json`. Replace `<PATH_TO_REPO_IN_WSL>` with the WSL path to this repo (e.g. `/home/youruser/percepxion-MCP-Server`).

### First connection check

Once connected, verify the integration:

1. Call `login_with_env`
2. Call `get_device_list` with `search_query: "*"`

If `get_device_list` returns devices, the server is working.

---

## Tool reference

Full reference in [`docs/tools.md`](docs/tools.md). Quick summary below.

**Session must be established with `login_with_env` before calling any other tool.**

### Authentication

| Tool | Description |
|---|---|
| `login_with_env` | Authenticate using credentials from `.env`. Call once per session. |

### Tenant management

| Tool | Description |
|---|---|
| `list_tenants` | List organizations visible to the current user. Use to discover `tenant_id` values. |

### Device inventory

| Tool | Description |
|---|---|
| `get_device_list` | Search and paginate the device inventory. Accepts `tenant_id` for org scoping. |
| `get_device_details` | Get full device properties by `device_id` or `serial_num`. |
| `get_devices_by_organization` | List all devices in a specific tenant (convenience wrapper for `get_device_list`). |

### Device lifecycle

| Tool | Description |
|---|---|
| `import_and_assign_devices` | Assign devices to a tenant. Processes each device individually and reports per-device results. |
| `unassign_devices` | Remove one or more devices from a tenant. |
| `remove_device_from_platform` | Convenience wrapper to remove a single device. |

### Smart Groups

| Tool | Description |
|---|---|
| `create_smart_group` | Create a Smart Group using a filter query or explicit device ID list. Used to target bulk firmware and config operations. |
| `list_smart_groups` | List Smart Groups by name filter. Returns all Smart Groups visible to your account. |
| `delete_smart_group` | Delete a Smart Group by ID. Removes the targeting group but does not affect devices. |

### CLI commands

| Tool | Description | Async? |
|---|---|---|
| `send_direct_cli_command` | Send a CLI command to one device. Read-only by default; configure policy in `.env` for write commands. Commands are audit-logged to stderr. | Yes, use `search_job_groups` |

### Device configuration

| Tool | Description | Async? |
|---|---|---|
| `update_device_config` | Save config properties and optionally apply them immediately. | Yes if `apply_now=True` |
| `get_device_config` | Retrieve current telemetry config properties before modifying them. | No |
| `clone_device_config` | Copy config groups from a source device to a target device via a template. | Yes, use `search_job_groups` |
| `list_templates` | List config templates saved in Percepxion. | No |
| `delete_template` | Delete a config template by ID. | No |

### Firmware management

| Tool | Description | Async? |
|---|---|---|
| `get_device_firmware_status` | Get firmware version and state for one device. | No |
| `firmware_compliance_report` | Compare fleet firmware against an expected version. Returns compliant, non-compliant, and unknown device lists. | No |
| `update_firmware_by_smart_group` | Upload a firmware file and apply it to devices in one or more Smart Groups. Firmware file must be on the server host. | Yes, use `search_job_groups` |
| `list_firmware_content` | List firmware packages already uploaded to Percepxion storage. | No |

### Device operations

| Tool | Description | Async? |
|---|---|---|
| `reboot_device` | Reboot a device via Percepxion. | Yes, use `search_job_groups` |
| `list_device_ports` | List all serial and device ports on a device. | No |

### Logging

| Tool | Description | Async? |
|---|---|---|
| `request_device_syslog_upload` | Trigger devices to upload syslogs to Percepxion storage. | Yes, use `search_job_groups` |
| `get_device_syslogs` | Query syslog content already uploaded to Percepxion. | No |
| `query_device_access_log` | Paginated query of device access log entries. | No |
| `download_device_access_log` | Download complete access log content for one device. | No |

### Security and audit

| Tool | Description |
|---|---|
| `get_security_telemetry` | Retrieve security-relevant telemetry statistics for a device. |
| `investigate_audit_logs` | Search platform audit records by user, time range, or keyword. |
| `investigate_user_audit_logs` | Search user records with last recorded audit action per user. |

### Job tracking

| Tool | Description |
|---|---|
| `search_job_groups` | Poll async job status. Use after any tool that returns a job group record. |
| `get_job_group` | Get full job output and results by job group ID. |

### Credentials

| Tool | Description |
|---|---|
| `reconfigure_credentials` | Switch credential provider at runtime (env, vault, or aws). |

### Job tracking workflow

When a tool creates a job, it returns a job group record immediately. The device operation continues asynchronously. To get results:

```
1. Call the action tool (e.g. send_direct_cli_command)
   → Returns: { "ok": true, "data": { "job_group_id": "...", "name": "CLI_abc_1742900000" } }

2. Call search_job_groups with the job name or ID
   → Returns: job status, output, and per-device results
```

Example for a CLI command:

```json
{ "search_string": "CLI_abc", "job_type": "command", "subtype": "cli", "limit": 5 }
```

Job names include a Unix timestamp suffix to avoid collisions when multiple jobs run against the same device.

---

## Security

This server executes operations on production network infrastructure. Treat it accordingly.

**Credentials:**
- Keep `.env` outside of version control. The `.gitignore` in this repo excludes it.
- Set file permissions to `600`: `chmod 600 .env`
- Use a dedicated Percepxion service account with the minimum permissions required for your use case. Do not use an admin account for automated or agent-driven workflows.

**CLI command execution:**
- `send_direct_cli_command` sends arbitrary CLI commands to devices through Percepxion. There is no command allowlist in the server. A session with this MCP tool can reconfigure or reset devices.
- All CLI commands dispatched through the server are logged to stderr with the device ID and command string for audit purposes.
- In production or shared environments, restrict who can start the MCP server process.

**Firmware uploads:**
- `update_firmware_by_smart_group` reads a local file path and uploads it to Percepxion.
- Set `PERCEPXION_FIRMWARE_DIR` to restrict uploads to a specific directory. Without this variable, any file the server process can read can be uploaded.

**Token handling:**
- Auth tokens are stored in memory only and are never written to disk.
- On a 401 response, the session is cleared automatically. Re-run `login_with_env` to restore the session.
- There is no automatic token refresh. Long-running workflows should handle 401 responses and re-authenticate.

**Network:**
- The server communicates with Percepxion over HTTPS only.
- Verify `PERCEPXION_API_URL` points to the correct Percepxion instance before running in any automated context.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "Not authenticated" | `login_with_env` not called or token expired | Call `login_with_env` |
| `401` on any tool | Token expired mid-session | Call `login_with_env` again |
| All calls fail or time out | Wrong `PERCEPXION_API_URL` | Check the URL in `.env` |
| Slow log downloads time out | Default 45s timeout too short | Set `PERCEPXION_REQUEST_TIMEOUT=120` |
| Firmware upload rejected | File outside `PERCEPXION_FIRMWARE_DIR` | Move file to allowed directory or unset the variable |
| Server exits immediately | Python path or venv issue | Run `python percepxion_mcp.py` directly to see the error |

---

## Developer docs

- [`docs/tools.md`](docs/tools.md), full tool reference with API endpoint mapping
- [`docs/adding-new-tools.md`](docs/adding-new-tools.md), conventions for adding tools to this server
- [`docs/claude-example.prompt`](docs/claude-example.prompt), starter system prompt for Claude Desktop sessions

---

## Changelog

See [`CHANGELOG.md`](CHANGELOG.md).

## License

See [`LICENSE`](LICENSE).
