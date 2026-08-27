# Percepxion MCP Server

A Python [FastMCP](https://github.com/jlowin/fastmcp) server that exposes the [Percepxion](https://percepxion.ai) REST API as MCP tools. Connect it to Claude Desktop, Claude Code, or any MCP-compatible client to manage out-of-band infrastructure through natural language.

## What is Percepxion?

Percepxion is a SaaS platform for out-of-band (OOB) network device management. It connects to console servers, serial port aggregators, and remote access devices to provide fleet-wide visibility, configuration management, firmware updates, CLI access, and compliance reporting, independent of the primary network path.

This MCP server gives AI assistants direct access to Percepxion's management capabilities.

---

## Use cases

- **Inventory discovery**, find all devices in an organization, filter by model or firmware version
- **Remote CLI execution**, run commands on a device and retrieve output through Percepxion
- **Config management**, push individual property changes or clone a full config from a reference device
- **Firmware compliance**, compare fleet firmware against a target and identify non-compliant devices
- **Firmware updates**, upload firmware and target a Smart Group for coordinated rollout
- **Log retrieval**, pull syslogs or access logs from devices on demand
- **Audit investigation**, search platform audit records by user, time range, or action
- **Organization management**, list organizations and scope operations to a specific one

---

## How it works

The server runs locally and communicates with the Percepxion API over HTTPS. Authentication uses username/password; the server exchanges these for session tokens and holds them in memory for the lifetime of the process.

Many Percepxion operations are asynchronous. Tools that trigger device actions (CLI commands, config pushes, firmware updates, syslog requests) create a Percepxion job group and return the job record. Use `search_job_groups` or `get_job_group` to poll status. Neither of those returns CLI output text, once a CLI command job reaches `"Completed"`, call `get_cli_command_output` for the actual device response.

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
- Network access to your Percepxion API endpoint
- A Percepxion username and password with appropriate permissions

---

## Quick start

### Linux or WSL

```bash
git clone https://github.com/Lantronix/percepxion-mcp-server.git
cd percepxion-mcp-server

python3 -m venv .venv
source .venv/bin/activate
pip install -e .

cp .env.example .env
# Edit .env, set PERCEPXION_USERNAME, PERCEPXION_PASSWORD
# Default API URL is https://api.percepxion.ai/api
# Lantronix employees: use https://api.gopercepxion.ai/api for the internal sandbox
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
| `PERCEPXION_API_URL` | No | `https://api.percepxion.ai/api` | Percepxion API base URL. Use `https://api.gopercepxion.ai/api` for the Lantronix internal sandbox. |
| `PERCEPXION_DEFAULT_ORGANIZATION_ID` | No |, | Default organization ID used when callers omit `organization_id`. Useful for single-organization deployments. |
| `PERCEPXION_DEFAULT_TENANT_ID` | No |, | Deprecated alias for `PERCEPXION_DEFAULT_ORGANIZATION_ID`. Still works; if both are set, the new variable wins. |
| `PERCEPXION_REQUEST_TIMEOUT` | No | `45` | HTTP timeout in seconds. Raise for large log downloads or slow links. |
| `PERCEPXION_FIRMWARE_DIR` | No |, | If set, firmware uploads are restricted to files in this directory. Recommended for shared or automated deployments. |
| `PERCEPXION_CREDENTIAL_PROVIDER` | No | `env` | Credential backend: `env` (default), `vault`, `aws`, or `cyberark`. |

Keep `.env` out of version control. The repo includes `.env.example` as a starting point.

### CLI command policy

`send_direct_cli_command` is **read-only by default**. Only `show`, `get`, `ping`, `traceroute`, and similar read commands are allowed. Configure write access and filtering in `.env`:

| Variable | Default | Description |
|---|---|---|
| `PERCEPXION_CLI_WRITE_ENABLED` | `false` | Set to `true` to allow write commands (`set`, `configure`, etc.). |
| `PERCEPXION_CLI_YOLO` | `false` | Set to `true` to disable all command filtering. Use with extreme caution. |
| `PERCEPXION_CLI_MAX_LENGTH` | `512` | Maximum command length in characters. |
| `PERCEPXION_CLI_DENY_COMMANDS` |, | Comma-separated commands to block in addition to built-in defaults (reload, factory-reset, write erase, etc.). |
| `PERCEPXION_CLI_PERMIT_COMMANDS` |, | Comma-separated explicit allowlist. If set, only matching commands (and their subcommands) are permitted. |

### Credential providers

By default, credentials are read from environment variables. Three additional backends are available:

**HashiCorp Vault:**
```
PERCEPXION_CREDENTIAL_PROVIDER=vault
VAULT_ADDR=https://vault.example.com
VAULT_TOKEN=hvs.XXXX
VAULT_SECRET_PATH=secret/data/percepxion
```

**AWS Secrets Manager:**
```
PERCEPXION_CREDENTIAL_PROVIDER=aws
AWS_SECRET_NAME=percepxion/credentials
AWS_REGION=us-east-1
```

Install the AWS extra: `pip install -e ".[aws]"`

**CyberArk Central Credential Provider (CCP):**

Fetches Percepxion admin credentials from the CyberArk AIM Web Service at login time. No password stored in config files. Recommended for enterprises already running CyberArk.

```
PERCEPXION_CREDENTIAL_PROVIDER=cyberark
CYBERARK_URL=https://cyberark.internal
CYBERARK_APP_ID=PercepxionMCP
CYBERARK_SAFE=PercepxionSafe
CYBERARK_OBJECT=percepxion-admin-account
```

Optional mutual TLS (both vars required to enable):
```
CYBERARK_CERT_PATH=/path/to/client.pem
CYBERARK_KEY_PATH=/path/to/client.key
```

Set `CYBERARK_VERIFY_SSL=false` to skip server cert verification in lab environments. The AppID must be registered in CyberArk with access to the specified safe, and the host running this server must be an allowed machine for that AppID.

Full setup details in [`config/setup-instructions.md`](config/setup-instructions.md).

---

## Connect an MCP client

### Claude Code (recommended)

```bash
claude mcp add percepxion -- /path/to/percepxion-mcp-server/.venv/bin/python /path/to/percepxion-mcp-server/percepxion_mcp.py
```

Or add manually to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "percepxion": {
      "command": "/path/to/.venv/bin/python",
      "args": ["/path/to/percepxion-mcp-server/percepxion_mcp.py"],
      "env": {
        "PYTHONUNBUFFERED": "1",
        "PERCEPXION_USERNAME": "your-email@example.com",
        "PERCEPXION_PASSWORD": "your-password"
      }
    }
  }
}
```

### Claude Desktop (Linux or macOS)

Copy `config/claude_desktop_config.example.json`, fill in your paths, and place it at:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

### Claude Desktop (Windows + WSL)

Use `config/claude_desktop_config.wsl_windows.example.json`. Replace the path placeholder with the WSL path to this repo.

### First connection check

Once connected:

1. Call `login_with_env`
2. Call `get_device_list` with `search_query: "*"`

If `get_device_list` returns devices, the server is working.

---

## Claude skill

[`skills/percepxion-fleet-ops/SKILL.md`](skills/percepxion-fleet-ops/SKILL.md) packages the operational knowledge for this server: OOB-device vs managed-device disambiguation, the async job output pattern, role-based `organization_id` rules, preflight discipline, and safety defaults. With the skill installed, Claude uses the tools correctly on the first try instead of rediscovering these patterns.

Install for all your projects:

```bash
mkdir -p ~/.claude/skills && cp -r skills/percepxion-fleet-ops ~/.claude/skills/
```

Or copy into a single project's `.claude/skills/` directory. The same file can be uploaded to claude.ai as a skill. Keep the skill in sync when tools change: `tests/test_skill_doc.py` fails the suite if tool names drift.

---

## Tool reference

Full reference in [`docs/tools.md`](docs/tools.md). Summary below.

**Call `login_with_env` before any other tool.** The session persists for the lifetime of the process. On a 401 response, call `login_with_env` again.

### Authentication and credentials

| Tool | Description |
|---|---|
| `login_with_env` | Authenticate using the configured credential provider. Call once per session. |
| `reconfigure_credentials` | Switch credential provider at runtime (`env`, `vault`, `aws`, `cyberark`) and clear the current session. |

### Organization management

| Tool | Description |
|---|---|
| `list_organizations` | List organizations you have permission for. Use to discover `organization_id` values. |
| `list_tenants` | Deprecated alias for `list_organizations`, kept for backward compatibility. |

Most tools accept an `organization_id` parameter to scope the call. The older `tenant_id` name is still accepted everywhere as a deprecated alias (see [Environment variables](#environment-variables)).

`organization_id`/`tenant_id` accept either a UUID or an exact (case-insensitive) organization name. Name resolution only works for organizations with at least one visible device (it's derived from device search results, there's no dedicated organization-lookup endpoint), and is always scoped to your own login-derived permissions, a name match for an organization you aren't permitted for is rejected, not returned. If a name matches zero or more than one permitted organization, the tool call fails with a clear error instead of guessing; use the `organization_id` (UUID) directly to disambiguate.

**Project Admin accounts:** Percepxion requires an explicit `organization_id` on job/telemetry/content/Smart-Group/audit calls when the authenticated account is a Project Admin, that role's access spans every organization in the project, so Percepxion can't infer a single default the way it does for `tenant_user`/`tenant_admin` accounts (auto-scoped to their one organization, `organization_id` is optional for those). Omitting it as a Project Admin now raises a clear error instead of a generic API rejection; call `list_organizations` first to find the ID to pass. Device-inventory tools (`get_device_list`, `get_device_details`, `list_device_ports`) don't require it for any role.

### Device inventory

| Tool | Description |
|---|---|
| `get_device_list` | Search and paginate the device inventory. |
| `get_device_details` | Get full device properties by `device_id` or `serial_num`. |
| `get_devices_by_organization` | List all devices in a specific organization. |

### Device lifecycle

| Tool | Description |
|---|---|
| `import_and_assign_devices` | Assign devices to an organization. |
| `unassign_devices` | Remove one or more devices from an organization. |
| `remove_device_from_platform` | Remove a single device (convenience wrapper). |

### Smart Groups

| Tool | Description |
|---|---|
| `create_smart_group` | Create a Smart Group using a filter query or device ID list. Used to target bulk operations. |
| `list_smart_groups` | List Smart Groups by name. |
| `delete_smart_group` | Delete a Smart Group by ID. |

### CLI commands

| Tool | Description | Async? |
|---|---|---|
| `send_direct_cli_command` | Send a CLI command to one device. Read-only by default (see CLI policy above). Commands are audit-logged. | Yes, use `get_job_group` |
| `get_cli_command_output` | Retrieve the actual CLI output text for a completed `send_direct_cli_command` job. Poll `search_job_groups`/`get_job_group` for status first; calling before the job completes returns `total_results: 0`, not an error. | No, call after the job completes |

### Device configuration

| Tool | Description | Async? |
|---|---|---|
| `get_device_config` | Read current telemetry config before modifying it. | No |
| `update_device_config` | Save config properties and optionally apply them immediately. | Yes if `apply_now=True` |
| `clone_device_config` | Copy config from a source device to a target device via a template. | Yes, use `get_job_group` |
| `list_templates` | List saved config templates. | No |
| `delete_template` | Delete a config template by ID. | No |

### Device operations

| Tool | Description | Async? |
|---|---|---|
| `reboot_device` | Reboot a device via Percepxion. | Yes, use `get_job_group` |
| `list_device_ports` | List serial and device ports on a device. Returns port names, numbers, and connection state. Does not include managed-device identity, use `get_security_telemetry` or `get_port_telemetry` for hostname, model, serial, and OS version. | No |

### Firmware management

| Tool | Description | Async? |
|---|---|---|
| `get_device_firmware_status` | Get firmware version and state for one device. | No |
| `firmware_compliance_report` | Compare fleet firmware against an expected version. | No |
| `list_firmware_content` | List firmware packages already uploaded to Percepxion storage. | No |
| `update_firmware_by_smart_group` | Upload firmware and apply to devices in one or more Smart Groups. | Yes, use `get_job_group` |

### Logging

| Tool | Description | Async? |
|---|---|---|
| `request_device_syslog_upload` | Trigger devices to upload syslogs to Percepxion storage. | Yes, use `get_job_group` |
| `get_device_syslogs` | Query syslog files already uploaded. | No |
| `query_device_access_log` | Paginated query of device access log entries. | No |
| `download_device_access_log` | Download complete access log for one device. | No |

### Security and audit

| Tool | Description |
|---|---|
| `get_security_telemetry` | Retrieve full device and per-port telemetry. Source of truth for managed-device inventory: returns per-port `dp_info` records with hostname, model, serial, IP, OS version, uptime, and CPU/memory/flash usage for every attached device. Also includes console manager info, firmware state, network probes, and audit records. |
| `get_port_telemetry` | Retrieve telemetry for a single port. Returns a structured managed-device object for that port only, faster and cheaper than `get_security_telemetry` when only one port is needed. |
| `investigate_audit_logs` | Search platform audit records by user, time range, or keyword. |
| `investigate_user_audit_logs` | Search user records with last recorded audit action per user. |

### Job tracking

| Tool | Description |
|---|---|
| `search_job_groups` | Search and poll async job status by name prefix. |
| `get_job_group` | Get full job output and results by job group ID. |
| `get_job_results_by_device` | Per-device result rollup for a multi-device job (e.g. a Smart Group firmware push or a CLI command sent to several devices at once). Use `get_cli_command_output` instead when you already know the single device you want output for. |

### Async job workflow

When a tool creates a job, it returns a job group record immediately:

```
1. Call the action tool (e.g. send_direct_cli_command)
   → Returns: { "ok": true, "data": { "id": "jg-abc123", "name": "CLI_dev001_1748000000" } }

2. Call get_job_group (or search_job_groups) with the id to poll status
   → Status reaches "Completed" or "Failed"

3. For CLI commands specifically, call get_cli_command_output with the same job_group_id and device_id
   → Returns: the actual output text the device sent back
```

Job names include a Unix timestamp suffix to avoid collisions when multiple jobs run against the same device.

---

## Security

This server executes operations on network infrastructure. Treat it accordingly.

**Credentials:**
- Keep `.env` out of version control (it's in `.gitignore`).
- Set file permissions: `chmod 600 .env`
- Use a dedicated Percepxion service account with minimum required permissions. Do not use an admin account for automated workflows.
- For team environments, use the Vault or AWS Secrets Manager providers instead of plaintext `.env` files.

**CLI command policy:**
- `send_direct_cli_command` is **read-only by default**. Only recognized read commands (`show`, `get`, `ping`, etc.) pass through.
- A built-in deny list blocks destructive operations (`reload`, `factory-reset`, `write erase`, `erase startup-config`, etc.) even when write mode is enabled.
- Set `PERCEPXION_CLI_WRITE_ENABLED=true` to allow write commands. All dispatched commands are logged to stderr with device ID and command string.
- Set `PERCEPXION_CLI_DENY_COMMANDS` to add custom blocked commands. Set `PERCEPXION_CLI_PERMIT_COMMANDS` for an explicit allowlist.
- `PERCEPXION_CLI_YOLO=true` disables all filtering. Use only in trusted, isolated environments.

**Firmware uploads:**
- `update_firmware_by_smart_group` reads a local file path and uploads it to Percepxion.
- Set `PERCEPXION_FIRMWARE_DIR` to restrict uploads to a specific directory. Without this, any file the server process can read can be uploaded.

**Token handling:**
- Auth tokens are stored in memory only and are never written to disk.
- On a 401 response, the session is cleared automatically. Call `login_with_env` again to restore.
- There is no automatic token refresh. Long-running workflows should handle 401 responses and re-authenticate.

**Network:**
- The server communicates with Percepxion over HTTPS only.
- The default endpoint is `api.percepxion.ai`. The Lantronix internal sandbox is `api.gopercepxion.ai`. Verify `PERCEPXION_API_URL` before running in any automated context.

**Organization-name resolution:**
- `organization_id`/`tenant_id` accept a name as a convenience, but resolution is hard-scoped to the authenticated session: candidate organizations come only from `session.permitted_organization_ids`, populated from your own `/v2/user/login` response (`user.group[].tenant_id`) at login time.
- A device-derived name match for an organization outside that permitted set is rejected, not returned. This is a real permission boundary, not just a UX filter, it prevents organization-name lookup from being used to discover or probe organizations you aren't already RBAC-entitled to.
- A UUID-shaped `organization_id`/`tenant_id` always skips name resolution entirely (no extra API call), identical to prior behavior.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "Not authenticated" | `login_with_env` not called or token expired | Call `login_with_env` |
| `401` on any tool | Token expired mid-session | Call `login_with_env` again |
| All calls fail or time out | Wrong `PERCEPXION_API_URL` | Check `.env`, production is `api.percepxion.ai`, sandbox is `api.gopercepxion.ai` |
| Slow log downloads time out | Default 45s timeout too short | Set `PERCEPXION_REQUEST_TIMEOUT=120` |
| Firmware upload rejected | File outside `PERCEPXION_FIRMWARE_DIR` | Move file to allowed directory or unset the variable |
| "Write commands are disabled" | CLI policy in read-only mode | Set `PERCEPXION_CLI_WRITE_ENABLED=true` in `.env` |
| "Command is in the deny list" | Built-in deny list blocks the command | Set `PERCEPXION_CLI_YOLO=true` to bypass (use with caution) |
| Server exits immediately | Python path or venv issue | Run `python percepxion_mcp.py` directly to see the error |
| "No permitted organizations available" when resolving an organization name | Login response had no `user.group` entries, or `login_with_env` wasn't called | Call `login_with_env` first; confirm the account has organization group membership in Percepxion |
| Organization name resolves to "0 matches" | The org has no visible devices (name resolution is device-derived), or the name doesn't match any organization you're permitted for | Use `list_organizations` to find the `organization_id` (UUID) directly and pass that instead |
| Organization name resolves to "ambiguous, multiple matches" | Two or more permitted organizations share the same name | Use `list_organizations` to disambiguate and pass the `organization_id` (UUID) directly |
| "`organization_id` is required for this call when authenticated as project_admin" | Project Admin account, `organization_id` not supplied on a job/telemetry/content/Smart-Group/audit call | Pass `organization_id` explicitly; use `list_organizations` to find it |
| `get_cli_command_output` returns `total_results: 0` | Job hasn't reached "Completed" yet, or the job isn't a CLI command job | Poll `get_job_group`/`search_job_groups` until status is "Completed", then retry |

---

## Contributing

The project uses a feature branch workflow:

```bash
git checkout -b feat/your-feature
# make changes
git push -u origin feat/your-feature
# open a pull request to main
```

Run the test suite before submitting:

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

See [`docs/adding-new-tools.md`](docs/adding-new-tools.md) for conventions on adding new tools to the server.

---

## Developer docs

- [`docs/tools.md`](docs/tools.md), full tool reference with API endpoint mapping
- [`docs/adding-new-tools.md`](docs/adding-new-tools.md), conventions for adding tools to this server
- [`config/setup-instructions.md`](config/setup-instructions.md), detailed setup for all OS and credential provider combinations
- [`docs/claude-example.prompt`](docs/claude-example.prompt), starter system prompt for Claude Desktop sessions

---

## Changelog

See [`CHANGELOG.md`](CHANGELOG.md).

## License

See [`LICENSE`](LICENSE).
