# Percepxion MCP tools reference

Quick reference for all tools exposed by the Percepxion MCP server. Designed for scanning and linking from issues or PRs.

## Tool count summary

- Total tools: 33 (31 active + 2 deprecated aliases)
- Source file: `src/percepxion_mcp/server.py`

## Quick usage rules

- Call `login_with_env` once per MCP server process start.
- Many actions run as jobs. These tools return a job group record, not the final device output.
- Use `search_job_groups` to track job progress and results.
- Job names include a Unix timestamp suffix (e.g. `CLI_abc123_1742900000`) to prevent collisions.

## Summary table

| Category | Tool | Returns final data | Primary API endpoint(s) | Follow up |
|---|---|---|---|---|
| Authentication | `login_with_env` | Yes | `POST /v2/user/login` | None |
| Tenant | `list_tenants` | Yes | `POST /v1/tenant/search` | None |
| Inventory | `get_device_list` | Yes | `POST /v3/device/search` | None |
| Inventory | `get_device_details` | Yes | `POST /v3/device/get` | None |
| Inventory | `get_devices_by_organization` | Yes | `POST /v3/device/search` | None |
| Lifecycle | `import_and_assign_devices` | Yes (per-device) | `POST /v3/device/assign` | None |
| Lifecycle | `unassign_devices` | Yes | `POST /v3/device/unassign` | None |
| Lifecycle | `remove_device_from_platform` | Yes | `POST /v3/device/unassign` | None |
| Smart Groups | `create_smart_group` | Yes | `POST /v3/device/smartgroup/create` | None |
| Smart Groups | `list_smart_groups` | Yes | `POST /v3/device/smartgroup/search` | None |
| Smart Groups | `delete_smart_group` | Yes | `POST /v3/device/smartgroup/delete` | None |
| CLI | `send_direct_cli_command` | No | `POST /v1/job/jobgroup/create` | `search_job_groups` |
| Config | `update_device_config` | No | `POST /v1/telemetry/config/save` + `POST /v1/job/jobgroup/create` | `search_job_groups` |
| Config | `clone_device_config` | No | `POST /v1/telemetry/template/create` + `POST /v1/job/jobgroup/create` | `search_job_groups` |
| Config | `get_device_config` | Yes | `POST /v1/telemetry/config/view` | None |
| Config | `list_templates` | Yes | `POST /v1/telemetry/template/search` | None |
| Config | `delete_template` | Yes | `POST /v1/telemetry/template/delete` | None |
| Firmware | `get_device_firmware_status` | Yes | `POST /v3/device/get` | None |
| Firmware | `firmware_compliance_report` | Yes | `POST /v3/device/search` | None |
| Firmware | `update_firmware_by_smart_group` | No | `POST /v3/content/create` (multipart) | `search_job_groups` |
| Firmware | `list_firmware_content` | Yes | `POST /v3/content/search` | None |
| Device Operations | `reboot_device` | No | `POST /v1/job/jobgroup/create` | `search_job_groups` |
| Device Ports | `list_device_ports` | Yes | `POST /v3/device/port/list` | None |
| Logs | `request_device_syslog_upload` | No | `POST /v1/job/jobgroup/create` | `search_job_groups` |
| Logs | `get_device_syslogs` | Yes | `POST /v1/storage/file/content/query` | None |
| Logs | `query_device_access_log` | Yes | `POST /v1/storage/file/devicelog/query-by-id` | None |
| Logs | `download_device_access_log` | Yes | `POST /v1/storage/file/devicelog/download` | None |
| Security | `get_security_telemetry` | Yes | `POST /v1/telemetry/stat/view` | None |
| Audit | `investigate_audit_logs` | Yes | `POST /v1/audit/search` | None |
| Audit | `investigate_user_audit_logs` | Yes | `POST /v1/audit/user/search` | None |
| Jobs | `search_job_groups` | Yes | `POST /v1/job/jobgroup/search` | None |
| Jobs | `get_job_group` | Yes | `POST /v1/job/jobgroup/get` | None |
| Credentials | `reconfigure_credentials` | Yes | Credential provider runtime switch | None |

### Deprecated aliases (still functional, use replacements)

| Deprecated tool | Replacement |
|---|---|
| `send_cli_command` | `send_direct_cli_command` |
| `automate_smart_group` | `create_smart_group` |

## CLI Command Policy

`send_direct_cli_command` applies a security policy to prevent accidental destructive commands. By default, only read-only commands (show, get, ping, etc.) are allowed.

### Policy configuration

Set these environment variables in your `.env` to customize the policy:

```
# Allow write commands (set, configure, reload, etc.):
PERCEPXION_CLI_WRITE_ENABLED=true

# Comma-separated list of commands to always block:
PERCEPXION_CLI_DENY_COMMANDS=clear counters,debug ip packet,reload system

# Comma-separated list of commands to explicitly allow (if set, only these pass through):
PERCEPXION_CLI_PERMIT_COMMANDS=show version,show interfaces,ping,get config

# Disable all filtering (use with extreme caution):
PERCEPXION_CLI_YOLO=true
```

If `PERCEPXION_CLI_YOLO=true`, all commands pass through unfiltered. Use only in trusted environments.

## Job tracking pattern

Several tools create a Percepxion job group and return immediately. The device operation continues asynchronously. Use `search_job_groups` to poll status.

Job names use a Unix timestamp suffix to prevent collisions when multiple jobs target the same device.

### Example: run a CLI command

Step 1, dispatch the command:

```json
{ "device_id": "abc-123", "command": "show version" }
```

Returns a job group record with a name like `CLI_abc-123_1742900000`.

Step 2, search for the job group:

```json
{ "search_string": "CLI_abc-123", "job_type": "command", "subtype": "cli" }
```

### Example: apply a saved config update

Step 1, save and apply:

```json
{ "device_id": "abc-123", "property_name": "ntp_server", "new_value": "10.10.10.10", "apply_now": true }
```

Returns a save result and a job group record with a name like `Config_Update_abc-123_1742900000`.

Step 2, search for the apply job:

```json
{ "search_string": "Config_Update_abc-123", "job_type": "command", "subtype": "config" }
```

### Example: firmware compliance then update

Step 1, identify non-compliant devices:

```json
{ "expected_firmware_version": "9.7.0", "search_query": "*", "model_filter": "console-server" }
```

Step 2, create a Smart Group targeting those devices:

```json
{ "name": "fw-update-batch", "device_ids": ["d-001", "d-002"], "temporary": true }
```

Step 3, upload firmware and target the group:

```json
{
  "firmware_file_path": "/home/user/firmware/device-fw-9.8.0.tgz",
  "smart_group_ids": ["sg-abc"],
  "content_name": "device-fw-9.8.0",
  "version": "9.8.0"
}
```

Step 4, track the update job:

```json
{ "search_string": "device-fw-9.8.0", "job_type": "command" }
```
