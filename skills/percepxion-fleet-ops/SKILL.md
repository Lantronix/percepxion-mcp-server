---
name: percepxion-fleet-ops
description: "Operate Lantronix out-of-band (OOB) infrastructure through the Percepxion MCP server: device inventory, serial port and managed-device inspection, CLI diagnostics on SLC/EMG console servers, firmware compliance and rollout, config management, security auditing, and evidence collection for incident response. Use this skill whenever the user mentions Percepxion, Lantronix console servers (SLC9000, SLC8000, EMG), out-of-band or OOB management, serial consoles, firmware compliance for console servers, or wants an AI agent to reach network devices when the production network path is unavailable, even if they don't name a specific tool."
version: 1.2.0
license: MIT
---

# Percepxion Fleet Operations

This skill covers the **percepxion-mcp-server**, which manages Lantronix out-of-band infrastructure fleet-wide through the Percepxion SaaS platform (38 tools). For direct, synchronous access to a single SLC console server over its own REST API, use the companion **slc-mcp-server** and its `slc-device-ops` skill (https://github.com/Lantronix/slc-mcp-server). The capability split table near the end shows which server to route each job to.

Full per-tool parameter reference: `docs/tools.md` in this repository. This skill covers usage patterns, disambiguation, and safety; read `docs/tools.md` when you need exact parameters for a tool not shown here.

**Version requirement:** percepxion-mcp-server v1.2.0 or later. `get_cli_command_output` (retrieve actual CLI output text) and role-aware `organization_id` enforcement arrived in v1.1.0; `set_user_access` (suspend/resume user access) and the session `correlation_id` arrived in v1.2.0. This skill assumes all of them.

---

## Key Terms: OOB Device vs. Managed Device

This skill operates on two distinct device types. Confusing them causes wrong tool calls and unwanted outcomes.

| Term | What it is | Examples | How you reference it |
|------|-----------|---------|---------------------|
| **OOB device** (also: console server) | The Lantronix hardware managed by Percepxion. Has serial ports that cable to managed devices. | SLC9000, SLC8000, EMG7500, EMG8500 | By `device_id` in most MCP tool calls |
| **Managed device** (also: attached device, target device) | The network device whose console port is physically cabled to a serial port on the OOB device. NOT managed by Percepxion directly. | Cisco switch, Juniper router, Palo Alto firewall, a server with a serial console | Via `get_security_telemetry` (full inventory: hostname, model, serial, IP, OS) or `get_port_telemetry` (single port). `list_device_ports` returns port state only, not managed-device identity. |

**Tool routing for port and managed-device queries:**

| Question | Correct tool | Notes |
|----------|-------------|-------|
| What ports does this OOB device have? | `list_device_ports` | Port names, numbers, connection state. Does NOT return managed-device hostname, model, serial, or IP. |
| What managed devices are attached? | `get_security_telemetry` | Source of truth for managed-device inventory. Per-port records: hostname, model, serial, IP, OS version, uptime. |
| What is on a specific port? | `get_port_telemetry` | Single-port filtered view. Cheaper than `get_security_telemetry` for one port. |
| What port is a named managed device on? | `list_device_ports` with the device name as `device_id` | The `device_id` parameter doubles as a search string against the port index. Returns `parent_device_id` and `port_number`. |

**The key distinctions for tool calling:**

- The `device_id` in every tool call is the **OOB device** ID from `get_device_list` or `get_device_details`, never the managed device.
- `send_direct_cli_command` runs commands on the SLC's own management CLI, not on managed devices attached via serial. Valid commands are SLC-native: `show sysstatus`, `show deviceport port N`, `show portstatus`, `diag ping <ip>`, `admin version`. Cisco/Juniper/Arista CLI syntax will not work. Full CLI reference: SLC9000 Users Guide chapter "18: Command Reference" (https://cdn.lantronix.com/wp-content/uploads/pdf/PMD-00347A-SLC9K-UG-release.pdf).
- There is no Percepxion MCP tool that provides an interactive managed-device CLI session over serial. The Percepxion WebUI's device "Console" screen is not one either: it submits a CLI job and polls for results, the same mechanism `send_direct_cli_command` + `get_cli_command_output` expose, so anything that screen can do, this MCP covers. For an interactive terminal session a human SSHes to the SLC and runs `connect direct deviceport N`; that is outside MCP scope, but the MCP can compute the connection details (next section).

**When the user says "run a command on the device", ask:** the OOB console server itself, or a managed device attached to one of its serial ports? If the SLC: `send_direct_cli_command` with SLC syntax. If a managed device: compute the direct SSH connection string for them instead of stopping at "not supported":

1. `list_device_ports(device_id=<managed device name>)` to find `parent_device_id` and `port_number` (flag the port if it shows disconnected or no carrier detect)
2. `get_device_details(device_id=<parent_device_id>)` for the SLC's management IP
3. SSH direct-connect port is **3000 + port number** (port 2 → TCP 3002)
4. Return: `ssh -p <3000+N> <username>@<slc-management-ip>` (they'll be prompted for SLC credentials)

---

## Golden Rules

**Never send CLI commands or push firmware without explicit operator confirmation.** `send_direct_cli_command` reaches live infrastructure through a serial path; a wrong port number targets the wrong device. `update_firmware_by_smart_group` is irreversible while in progress.

**Always call `login_with_env` first.** No other tool succeeds without an active session. Despite the name, it authenticates via whichever backend `PERCEPXION_CREDENTIAL_PROVIDER` selects (env, vault, aws, cyberark); no different call is needed for non-env providers.

**Read before you write.** Confirm the OOB device with `get_device_list`/`get_device_details`, and confirm the target port shows the expected managed device with `get_security_telemetry` or `get_port_telemetry` before any action that touches it.

**Never expose credentials** (`PERCEPXION_USERNAME`, `PERCEPXION_PASSWORD`, `VAULT_TOKEN`, session tokens) in output, logs, or error messages.

---

## Session Correlation ID

`login_with_env` returns a `correlation_id` for the session and logs it. That id is also appended to the audit `description` of MCP-brokered device commands (`send_direct_cli_command`), so a sequence of actions in one authenticated session can be traced in both the MCP server logs and the Percepxion audit trail. Record it in the incident ticket when you open a session.

This is an MCP-side audit aid only. It does not reach the console server's own firmware audit log, so it is not a substitute for a device-side session identifier; correlating cloud activity with the device's local audit record is a platform/firmware capability, not something this server can stamp.

---

## Platform Behavior You Can't Infer From the Tools

Three things about how Percepxion works underneath the tools, so you read results correctly instead of mistaking platform behavior for a tool bug.

- **Events are entirely rule-driven.** Percepxion raises an event only when a configured rule's condition matches. A device going offline, a port dropping, a config drift, none of it generates an event unless a rule watches for it. So a quiet event/audit stream is not evidence of a healthy fleet, and "Percepxion didn't alert on X" only means no rule covered X. When an operator expects an alert that isn't there, check whether a rule exists before concluding nothing happened. Event latency also tracks each device's status-update interval (often ~2 minutes), so a state change shorter than one cycle can produce no event at all.

- **Some device operations are authenticated by the device's own firmware token, not your session.** A handful of low-level device endpoints (direct device config fetch, device-side connect/disconnect) require a token the device firmware generates, which no user session can produce. That is by design and it's why there is no interactive "log in as the device" path, and why device configuration goes through the fleet tools (`get_device_config`, `update_device_config`, `clone_device_config`) rather than a device-credential login. If you find yourself wanting a raw device token to reach an endpoint, stop: the fleet tool is the supported path.

- **Transient `500`s on some telemetry reads are a known platform-side condition, not a malformed call.** If a telemetry or property read returns a 500 while the same-shaped call succeeds elsewhere, treat it as a server-side issue: retry once, and if it persists, surface it to the operator as a platform problem rather than reformatting the request or reporting the tool broken. A `400` that names an expected type IS your request shape, fix that; a bare `500` on a valid shape is theirs.

---

## Setup

Requires Python 3.11+ and network access to the Percepxion API.

```bash
git clone https://github.com/Lantronix/percepxion-mcp-server.git
cd percepxion-mcp-server
pip install -r requirements.txt
```

**Claude Code:**

```bash
claude mcp add percepxion \
  --env PERCEPXION_USERNAME=you@example.com \
  --env PERCEPXION_PASSWORD=yourpassword \
  -- python /path/to/percepxion-mcp-server/percepxion_mcp.py
```

**Claude Desktop:** copy `config/claude_desktop_config.example.json` from this repository into your `claude_desktop_config.json` and fill in credentials.

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PERCEPXION_USERNAME` | Yes, when `PERCEPXION_CREDENTIAL_PROVIDER=env` (the default) | | Percepxion login username. Not read by the `vault`, `aws`, or `cyberark` providers. |
| `PERCEPXION_PASSWORD` | Yes, when `PERCEPXION_CREDENTIAL_PROVIDER=env` (the default) | | Percepxion login password. Not read by the `vault`, `aws`, or `cyberark` providers. |
| `PERCEPXION_API_URL` | No | `https://api.percepxion.ai/api` | API base URL. The wrong domain causes silent auth failures. |
| `PERCEPXION_CREDENTIAL_PROVIDER` | No | `env` | Credential backend: `env`, `vault`, `aws`, or `cyberark`. With a non-env provider, set that provider's variables instead of username/password (table below). |
| `PERCEPXION_DEFAULT_ORGANIZATION_ID` | No | | Default organization ID when callers omit `organization_id`. Useful for single-organization deployments. `PERCEPXION_DEFAULT_TENANT_ID` is a deprecated alias. |
| `PERCEPXION_REQUEST_TIMEOUT` | No | `45` | HTTP timeout in seconds. Raise for large log downloads or slow links. |
| `PERCEPXION_FIRMWARE_DIR` | No | | If set, firmware uploads are restricted to files in this directory. Recommended for shared or automated deployments. |

### Credential Providers

Selected by `PERCEPXION_CREDENTIAL_PROVIDER` on the server process:

| Provider | Env vars to set | When to use |
|----------|---------------|-------------|
| `env` (default) | `PERCEPXION_USERNAME` + `PERCEPXION_PASSWORD` | Dev, local, simple deployments |
| `vault` | `VAULT_ADDR`, `VAULT_TOKEN`, `VAULT_SECRET_PATH` | HashiCorp Vault |
| `aws` | `AWS_SECRET_NAME`, `AWS_REGION` | AWS Secrets Manager |
| `cyberark` | `CYBERARK_URL`, `CYBERARK_APP_ID`, `CYBERARK_SAFE`, `CYBERARK_OBJECT` | CyberArk Central Credential Provider (CCP) |

To switch at runtime: `reconfigure_credentials(provider="vault")`, then `login_with_env` again. This changes which secret store the server reads its own Percepxion session credentials from. It is NOT per-device credential rotation.

---

## The Async Job Pattern (read this before any CLI or firmware workflow)

`send_direct_cli_command`, `update_firmware_by_smart_group`, `reboot_device`, and `request_device_syslog_upload` are asynchronous: they return a `job_group_id` immediately and run in the background. The pattern is always:

1. Call the async tool, note the returned `job_group_id`.
2. Poll `get_job_group(job_group_id)` (or `search_job_groups`) until status reaches `"Completed"` or `"Failed"`. On failure, surface the full error to the operator.
3. **`get_job_group` never returns CLI output text**, only status and metadata. For `send_direct_cli_command` jobs, once completed, call `get_cli_command_output(job_group_id, device_id)` for the actual device response text. Calling it before completion returns `total_results: 0`, not an error; retry after a short delay.
4. For a multi-device job (Smart Group firmware push, CLI sent to several devices), `get_job_results_by_device(job_group_id)` returns a per-device result rollup.

Never report an action successful based on job status alone when output text is available; verify the device's own response.

---

## Workflow 1: Session Auth + Device Discovery (required first)

1. `login_with_env` with no parameters. Confirm `"ok": true`.
2. `get_device_list(search_query="*", limit=25)` lists OOB console servers; note device IDs, every later call needs one. Filter with `search_query`.
3. Multi-organization accounts: `list_organizations`, then `get_devices_by_organization(organization_id=...)`. (`list_tenants` and `tenant_id` are deprecated aliases; prefer the organization names.)
4. `get_device_details(device_id=...)` or `get_device_details(serial_num=...)` for hostname, firmware, model, IP, last check-in, status.

**Role rule:** if the authenticated account is a Percepxion **Project Admin**, `organization_id` is *required* on job/telemetry/content/Smart-Group/audit calls (`send_direct_cli_command`, `get_cli_command_output`, `search_job_groups`, `update_device_config`, `reboot_device`, firmware and smart-group tools, audit tools). Their access spans every organization in the project, so the server can't infer one. Omitting it raises a clear error naming the parameter (v1.1.0+; earlier versions surfaced an opaque `400 ACCESS_DENIED: "Invalid access to tenant."`). Tenant Admin / Tenant User accounts are auto-scoped and may omit it. Device-inventory tools (`get_device_list`, `get_device_details`, `list_device_ports`) never require it. If a call fails with either error, check the account's role before assuming a bug, and call `list_organizations` to get the ID.

## Workflow 2: Preflight (run before any automated action)

1. `get_device_details`: status `online`, recent check-in. Offline OOB device means no serial path; stop and alert.
2. `list_device_ports(device_id, limit=100)`: confirm the target port shows `connected`. A `total: 0` result does not mean nothing is attached; telemetry is authoritative.
3. `get_port_telemetry(device_id, port_number)` (or `get_security_telemetry(device_id)` for all ports): confirm the managed device on the target port matches expectations (hostname/model). No device visible means an unplugged cable or powered-off device; warn and stop unless the operator overrides.
4. Optional detail: `send_direct_cli_command` with `show deviceport port N` (async pattern above) for carrier detect, baud rate, byte counters.
5. On operator override, record it in the `description` field of every subsequent call: `"Operator authorized: proceeding despite <reason>"`. The override cannot bypass server-side CLI policy.

## Workflow 3: SLC Console Diagnostics

All commands target the SLC itself via `send_direct_cli_command` + the async pattern. Always fill `description` with the reason (it lands in Percepxion's audit trail). Useful commands: `show sysstatus` (health), `show deviceport port N` (carrier detect, connection state), `show portstatus` (all ports), `diag ping <ip>` (reachability from the SLC's network position, distinguishes in-band failure from total failure). Afterwards capture evidence: `get_device_syslogs(device_id)` and `query_device_access_log(device_id, query="session opened")`.

## Workflow 4: Firmware Compliance and Updates

1. `get_device_firmware_status(device_id)` for one device; `firmware_compliance_report(expected_firmware_version=..., model_filter=..., limit=1000)` fleet-wide (`expected_firmware_version` required).
2. `list_firmware_content()` shows packages already in Percepxion.
3. `create_smart_group(name=..., query="firmware_ver:<old> AND model:<model>", temporary=true)`; a `query` filter OR explicit `device_ids`, not both. Membership re-evaluates at execution time.
4. **Confirm scope with the operator**: group name, member count, current versions, target version, firmware file path.
5. `update_firmware_by_smart_group(firmware_file_path=..., smart_group_ids=[...], content_name=..., version=..., enable=true)`: uploads a local firmware file, so the file must exist on the server host. Async.
6. Track with `get_job_group`; per-device outcomes with `get_job_results_by_device`.
7. `delete_smart_group(smart_group_id)` when done with a temporary group; `list_smart_groups()` shows what exists.

## Workflow 5: Security Audit and Access Investigation

- `get_security_telemetry(device_id)`: telemetry for one OOB device (not fleet-wide).
- `investigate_audit_logs`: no `device_id` parameter; filter by device with `search_string`, by users with `usernames` (list). Dates are `from_date`/`to_date` (`YYYY-MM-DD`); omitted dates mean all history.
- `investigate_user_audit_logs(user_filter=...)`: user records with last-action summaries; no date parameters.
- `set_user_access(usernames, enabled)`: suspend (`enabled=false`) or resume (`enabled=true`) user access in bulk. This is the remediation step after an investigation flags a compromised or departing account: investigate, confirm with the operator, then suspend. Idempotent (users already in the target state are left alone), reports unknown usernames instead of failing, and is suspend/resume only, never create or delete. High blast radius: treat it like a CLI or firmware action and never call it without explicit operator confirmation.
- `download_device_access_log(device_id)` for forensic export / SIEM ingestion; `query_device_access_log(device_id, query=...)` for targeted event search.

## Workflow 6: Configuration Management (OOB devices, not managed devices)

- `get_device_config(device_id)` reads current config.
- `update_device_config`: single change via `property_name` + `new_value`, or several via `items` list; `apply_now=true` (default) saves and immediately creates a config pull job. **Confirm with the operator before applying.**
- `clone_device_config(source_device_id, target_device_id, record_names=[...])`: `record_names` is required; read the source with `get_device_config` first to identify them. Confirm both device IDs with the operator.
- `list_templates()` lists config templates; `delete_template` removes one (confirm first).

## Workflow 7: Device Lifecycle

- `import_and_assign_devices(devices=[{device_id, device_name, serial_num}], organization_id=...)`: all three fields per entry; optional `device_description`. `organization_id` required for Project Admin sessions.
- `reboot_device(device_id, description=...)`: **confirm first**; a reboot drops serial console access to every managed device on that OOB device's ports.
- `remove_device_from_platform(device_id)`: **irreversible, confirm first**. `unassign_devices(device_ids=[...])` unassigns without removing.
- `request_device_syslog_upload(device_id)`: asks the device to upload its syslog buffer (async); retrieve with `get_device_syslogs`.

## Workflow 8: Incident Evidence Loop (for automated remediation)

When an upstream system (monitoring, ticketing, or an orchestration platform such as PagerDuty, ServiceNow, or Itential) reports a device unreachable in-band:

1. `login_with_env` (if not already authenticated), `get_device_list(search_query=<site or device name>)`, `get_device_details`. If the OOB device itself is offline, stop and escalate; there is no path.
2. Run Workflow 2 preflight on the target port.
3. **Before-evidence:** `get_device_syslogs`.
4. Diagnostics via Workflow 3, always carrying the upstream incident ID in `description`.
5. Remediation commands only if the server was started with `PERCEPXION_CLI_WRITE_ENABLED=true`, and only after presenting the command to the operator or orchestrator. Verify via `get_cli_command_output`, not job status. If the incident implicates a user account (compromised or needs offboarding), `set_user_access(usernames, enabled=false)` suspends access, again only after explicit confirmation; resume later with `enabled=true`.
6. **After-evidence:** `investigate_audit_logs(search_string=<device>)`; include the excerpt in the incident record.
7. Report a structured outcome upstream: job group ID, before/after evidence, final status (`remediated` / `diagnosed-only` / `escalate-to-human`).

The before/after evidence steps are non-negotiable, even when the remediation fails. AI-initiated access carries the same audit trail as human access; treat `description` like a change ticket number.

---

## Server-Side CLI Policy

These are environment variables on the MCP server process, not tool parameters. **The AI cannot override them at runtime.**

| Env var | Default | Effect |
|---------|---------|--------|
| `PERCEPXION_CLI_WRITE_ENABLED` | `false` | `true` enables write commands. Read-only (`show`, `get`, `ping`, `traceroute`) is the default. |
| `PERCEPXION_CLI_MAX_LENGTH` | `512` | Maximum command length in characters. |
| `PERCEPXION_CLI_DENY_COMMANDS` | built-in list | Extra comma-separated commands to block. The built-in deny list (`reload`, `factory-reset`, `write erase`, similar) always applies unless YOLO. |
| `PERCEPXION_CLI_PERMIT_COMMANDS` | unset | If set, an explicit allowlist; only these commands and subcommands run. |
| `PERCEPXION_CLI_YOLO` | `false` | `true` disables ALL filtering. Extreme caution. |

Percepxion also supports per-port command filtering configured in the platform UI. A command allowed by server policy can still be blocked there; surface the error and point the operator at the platform's port command filter. Likewise, a permissions error means platform RBAC needs updating, not the MCP call.

---

## Which Server for Which Job

There is no capability overlap by design between this server and slc-mcp-server:

| Capability | percepxion-mcp-server (fleet) | slc-mcp-server (direct) |
|---|---|---|
| Serial port status/config | `list_device_ports` | `get_slc_port`, `get_slc_ports` |
| CLI commands, async job + output fetch | `send_direct_cli_command` + `get_cli_command_output` | - |
| CLI commands, synchronous output in one call | - | `apply_config_commands` |
| Firmware update | `update_firmware_by_smart_group` | `firmware_update`, `get_firmware_update_status` |
| Device config backup | `get_device_config` | `export_config_commands` |
| User/session management | - | `get_sessions`, `terminate_session` |
| Reboot | `reboot_device` (fleet) | `reboot_device` |
| Cellular status | - | `get_cellular_status` |
| Fleet-wide ops (smart groups, templates, compliance) | Yes | - |
| Audit logs | `investigate_audit_logs` | - |

Route through Percepxion when operating at fleet scale, when the agent has no network path to the device, or when audit/compliance evidence is needed. Route through slc-mcp-server when the agent can reach the SLC's management IP and wants synchronous output without the job cycle.
