# Changelog

## 1.0.0 - 2026-07-13

### Added
- Organization-name lookup: `organization_id`/`tenant_id` params across all tools now accept either a UUID or an exact (case-insensitive) organization display name. Names are resolved by scanning visible devices' embedded `tenant[]` info via `POST /v3/device/search` (bounded pagination, a few pages max) since there's no dedicated organization-lookup endpoint. Resolution is hard-scoped to the authenticated user's own login-derived RBAC permissions (`session.permitted_organization_ids`, captured from `user.group[].tenant_id` in the `/v2/user/login` response): a name match for an organization outside that set is rejected, not returned, this is a real permission boundary, not just a UX filter. A UUID-shaped value always skips name resolution entirely (no extra API call, pure passthrough, unchanged from prior behavior). Zero matches or ambiguous (2+) matches raise a clear error naming the problem instead of guessing.

### Fixed
- `list_organizations`/`list_tenants` were calling `POST /v1/tenant/search`, an endpoint that does not exist server-side (`400 VALIDATION_FAILED`, "Route defined in Swagger specification but there is no defined post operation"). Both tools always failed in production; this was masked by fully-mocked tests. Reimplemented using `session.permitted_organization_ids` (login-derived, authoritative) for the ID list, with best-effort display names resolved from visible devices via `/v3/device/search`. An organization with zero visible devices now appears with a known `organization_id` but `name: null`, that's a documented limitation, not a bug: there is no other endpoint that exposes organization display names.
- CLI policy: `check_command` now rejects commands containing embedded `\r`/`\n` before normalization, closing a deny-list bypass (e.g. `"show version\nreload\nwrite erase"` previously normalized to a single string that passed the read-prefix check and matched no deny-list entry).
- Audit logging added to 9 destructive tools that previously had none: `import_and_assign_devices`, `unassign_devices`, `remove_device_from_platform`, `delete_smart_group`, `update_device_config`, `clone_device_config`, `reboot_device`, `update_firmware_by_smart_group`, `delete_template`.
- `_resolve_tenant`/`_resolve_organization` now logs a warning when it silently falls back to the configured default organization scope, instead of failing silently.
- `get_devices_by_organization` was not routing its `organization_id`/`tenant_id` value through `_resolve_organization`, so it never got the name-resolution (or default-scope-fallback) behavior applied to every other tool. Fixed for consistency with the rest of the tool surface.

### Changed
- Renamed "tenant" terminology to "organization" across the tool surface to match Percepxion's actual product hierarchy (Project > Portal > Organization). Every tool that previously accepted `tenant_id` now accepts `organization_id` as the primary parameter; `tenant_id` still works as a deprecated alias (organization_id wins if both are set). `PERCEPXION_DEFAULT_ORGANIZATION_ID` is the new primary env var; `PERCEPXION_DEFAULT_TENANT_ID` keeps working as a legacy fallback. `list_tenants` is now a deprecated alias for the new `list_organizations` tool. The outgoing Percepxion API field (payload key `tenant_id`) is unchanged, that's an external API constraint.

## 0.4.4 - 2026-06-30

### Added
- CyberArk Central Credential Provider (CCP) backend (`providers/cyberark.py`). Fetches Percepxion admin credentials from the CyberArk AIM Web Service REST API (`GET /AIMWebService/api/Accounts`) at login time. Required env vars: `CYBERARK_URL`, `CYBERARK_APP_ID`, `CYBERARK_SAFE`, `CYBERARK_OBJECT`. Optional mTLS support via `CYBERARK_CERT_PATH` and `CYBERARK_KEY_PATH`. Set `CYBERARK_VERIFY_SSL=false` to skip server cert verification in lab environments.
- `reconfigure_credentials` tool now accepts `'cyberark'` as a valid provider.

## 0.4.3 - 2026-06-18

### Added
- Configurable transport mode via `MCP_TRANSPORT` env var (`stdio` default, `sse` for HTTP deployments). `MCP_HOST` (default `0.0.0.0`) and `MCP_PORT` (default `8765`) set the SSE bind address and port. Existing stdio users (Claude Code, Claude Desktop) are unaffected.
- `EXPOSE 8765` in Dockerfile.

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
