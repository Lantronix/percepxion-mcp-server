# Changelog

## 0.4.2 - 2026-06-18

### Added
- `get_port_telemetry(device_id, port_number, tenant_id)` tool: queries `/v1/telemetry/stat/view` and returns filtered telemetry for a single port, managed-device hostname, model, serial, IP, OS version, uptime, CPU/memory/flash usage, and associated scripts. Eliminates the full-payload fetch + manual JSON parse required when only one port's data is needed.

### Changed
- `get_security_telemetry` docstring rewritten to accurately describe what the tool returns: full per-port `dp_info` records with up to 20 managed-device fields, console manager info, firmware state, network probes, and audit/syslog records. The previous one-liner ("Retrieve telemetry statistics useful for security analysis") gave no signal this was the canonical source for port-level and managed-device inventory, causing callers to miss it and report false "no managed devices" results.
- `list_device_ports` docstring updated with an explicit NOTE that the tool returns port connection state only and does not include managed-device attachment details. Redirects callers to `get_security_telemetry` or `get_port_telemetry` for managed-device identity.

## 0.4.1 - 2026-06-16

### Fixed
- `get_job_group`: payload key changed from `id` to `job_group_id`; the Percepxion API requires the longer key and returned a `VALIDATION_ERROR` with the old one
- `list_device_ports`: endpoint corrected from `/v1/device/port/search` (404 in production) to `/v3/port/search`; payload key changed from `device_id` to `search_string` to match the actual API contract discovered via live network capture

## 0.4.0 - 2026-05-29

### Added
- CLI command policy (`cli_policy.py`), read-only by default; configurable deny list, permit list, write mode, and YOLO mode via env vars (`PERCEPXION_CLI_WRITE_ENABLED`, `PERCEPXION_CLI_YOLO`, `PERCEPXION_CLI_MAX_LENGTH`, `PERCEPXION_CLI_DENY_COMMANDS`, `PERCEPXION_CLI_PERMIT_COMMANDS`)
- Credential provider system (`providers/`), pluggable backends for env vars (default), HashiCorp Vault, and AWS Secrets Manager; selected via `PERCEPXION_CREDENTIAL_PROVIDER`
- `reconfigure_credentials` tool, switch credential provider at runtime without restarting the server
- 9 new API tools: `list_smart_groups`, `delete_smart_group`, `get_job_group`, `reboot_device`, `get_device_config`, `list_firmware_content`, `list_templates`, `delete_template`, `list_device_ports`
- `PERCEPXION_DEFAULT_TENANT_ID` env var documented in env table
- Test suite, 56 tests covering CLI policy, credential providers, session/client helpers, and tool integration (pytest + pytest-httpserver)
- `CLAUDE.md`, guided onboarding for Claude Code users
- `config/setup-instructions.md`, step-by-step setup for all OS and credential provider combinations

### Changed
- Default `PERCEPXION_API_URL` is now `https://api.percepxion.ai/api` (production). The sandbox (`api.gopercepxion.ai`) is documented as a user-configurable option.
- `send_direct_cli_command` validates commands against CLI policy before dispatching; read-only by default
- `login_with_env` reads the active credential provider dynamically (supports runtime switching via `reconfigure_credentials`)
- Firmware path check uses `Path.is_relative_to()` instead of string prefix matching (fixes a path traversal bypass)
- Removed deprecated tool aliases `automate_smart_group` and `send_cli_command`
- `pyproject.toml`, dependencies pinned with upper bounds; `[dev]` and `[aws]` optional extras added
- Codebase split into focused modules: `config.py` (env vars), `client.py` (HTTP session and helpers), `server.py` (tool definitions only)

## 0.3.0 - 2026-03-23

### Added
- `list_tenants` tool, list organizations visible to the current user; needed to discover `tenant_id` values for scoped operations
- `create_smart_group` tool, canonical replacement for `automate_smart_group` with clearer naming and expanded docstring
- `PERCEPXION_FIRMWARE_DIR` env var, when set, restricts `update_firmware_by_smart_group` to files in the specified directory
- CLI command audit logging, `send_direct_cli_command` now logs device ID and command string to stderr for audit purposes

### Changed
- `automate_smart_group` is now a deprecated alias for `create_smart_group`; existing workflows continue to function
- `send_cli_command` docstring updated to mark it as deprecated in favor of `send_direct_cli_command`
- `update_firmware_by_smart_group` uses `Path.resolve()` to normalize paths before directory restriction check
- `docs/tools.md`, updated job tracking examples to reflect timestamp-based job names; added deprecated aliases table; added firmware compliance + update workflow example
- README rewritten, added Percepxion product context, expanded security section, added Claude Code connection instructions, restructured tool reference tables, added troubleshooting table

## 0.2.0 - 2026-03-23

### Added
- Stderr logging via Python `logging` module, auth events, API errors, and request failures are now visible when debugging
- Unix timestamp suffix on CLI, config, and syslog job names to prevent name collisions when multiple jobs target the same device

### Changed
- Dependency versions pinned in `requirements.txt`: `fastmcp>=3.1.0,<4.0`, `requests>=2.32.0,<3.0`, `python-dotenv>=1.2.0,<2.0`

## 0.1.0 - 2026-03-04

- Initial repo packaging of the Percepxion FastMCP server PoC
