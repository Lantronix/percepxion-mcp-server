# Adding new tools

This server maps Percepxion REST APIs into MCP tools.
A tool is a Python function decorated with `@mcp.tool()`.

This guide covers the conventions used in this repo.

## Design goals

- Keep tool signatures small and predictable.
- Return JSON-serializable data.
- Use one response envelope for all tools.
- Support organization scoping when the API supports it.

## Where tools live

All tools live in `src/percepxion_mcp/server.py`.
The file already includes helper functions:

- `_api_post()` wraps HTTP calls and returns the response envelope.
- `_require_login()` blocks calls when tokens are missing.
- `PercepxionSession` stores `x-mystq-token` and `x-csrf-token`.

## Workflow for adding a tool

### 1. Pick the API endpoint

Start with the Percepxion API spec and platform docs.
Write down:

- Path
- Request body
- Auth headers required
- Field limits and paging

### 2. Decide the tool name

Use verb-first names.
Match the API behavior.

Examples:

- `get_...` reads data.
- `update_...` changes state.
- `request_...` triggers a job.
- `investigate_...` searches logs.

### 3. Add the function

Add the tool under the closest category in the file.
Use `@mcp.tool()` and a clear docstring.

Template:

```python
from typing import Any

@mcp.tool()
def get_organizations(limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """List organizations visible to the current user."""

    payload = {
        "search_string": "*",
        "limit": min(max(1, limit), 1000),
        "offset": max(0, offset),
        "sort": "name",
        "order": "asc",
    }

    return _api_post("/v1/tenant/search", json_body=payload)
```

### 4. Add organization scoping when it fits

Many Percepxion endpoints accept a `tenant_id` field (that's the API's own field name,
an external constraint we don't control). On the MCP server side, expose it to callers
as `organization_id: str | None = None` (Percepxion's real product terminology is
Project > Portal > Organization), and also accept the older `tenant_id: str | None = None`
param name for backward compatibility. Resolve the effective value with
`_effective_org_id(organization_id, tenant_id)` and pass the result through
`_resolve_organization(...)` before adding it to the payload under the `tenant_id` key.

### 5. Keep paging rules consistent

Use the same caps used in existing tools.

- `limit` min 1
- `limit` max 1000
- `offset` min 0

### 6. Handle job-style endpoints

Percepxion runs many actions as job groups.
A tool that creates a job group should:

- Build a job group payload
- Call `POST /v1/job/jobgroup/create`
- Return the job group response

Pattern:

```python
@mcp.tool()
def reboot_device(device_id: str, name: str | None = None) -> dict[str, Any]:
    """Request a reboot via a job group."""

    job_name = name or f"Reboot_{device_id[:12]}"
    payload = {
        "name": job_name,
        "description": "Reboot requested via MCP",
        "type": "command",
        "subtype": "action",
        "op_code": "execute",
        "operation": "reboot",
        "device_id": [device_id],
        "enable": True,
    }

    return _api_post("/v1/job/jobgroup/create", json_body=payload)
```

Then document the follow up call:

- `search_job_groups` with `search_string` set to the job name

### 7. Multipart upload tools

The firmware tool shows the pattern used for uploads.
Use `content_type_json=False` and pass `files` plus `form_data`.

Keep file-path parameters generic.
Avoid local absolute paths in docs.

### 8. Update documentation

Update these docs when you add tools:

- `README.md` tool catalog section
- `docs/tools.md` summary table
- `skills/percepxion-fleet-ops/SKILL.md` if the change affects usage patterns, parameters, or safety behavior. `tests/test_skill_doc.py` fails when tool names in the skill drift from `server.py`, but behavior text is on you: update it in the same PR.

### 9. Smoke test

Run a basic import test.

```bash
python -m py_compile src/percepxion_mcp/server.py
```

Then start the server and call the new tool.

## Tool quality checklist

- Docstring states what the tool does.
- Parameters have defaults and simple types.
- Return value uses `_ok()` or `_err()`.
- Tool accepts `organization_id` (with `tenant_id` accepted as a deprecated alias) when the API supports it.
- Tool limits `limit` and clamps `offset`.
- Job tools document the follow up search.
