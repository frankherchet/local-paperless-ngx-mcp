# Local Paperless-ngx MCP

A local [FastMCP](https://gofastmcp.com/) server for the
[Paperless-ngx REST API](https://docs.paperless-ngx.com/api/). It connects
MCP-capable clients to your own Paperless-ngx instance over `stdio`.

## Features

- Check connectivity, Paperless version, and API version.
- Search documents by simple query, title, Paperless advanced syntax, or
  similarity.
- Retrieve document metadata, OCR text, and optional file checksums.
- List tags, correspondents, document types, storage paths, custom fields,
  saved views, and workflows with pagination.
- Assess organization quality across the archive and find missing metadata.
- Create, rename, and configure organization objects.
- Assign correspondents, document types, and storage paths in batches.
- Add or remove tags without replacing unrelated tags.
- Move documents only to Paperless' recoverable trash and restore them.
- Update selected document metadata and create document notes.
- Configure and verify a default intake workflow for new documents.
- Block permanent document deletion and trash-emptying at the API-client level.

## Requirements

- Python 3.11 or later
- [`uv`](https://docs.astral.sh/uv/)
- A reachable Paperless-ngx instance with an API token

Create the token in Paperless-ngx under **Profile → API Token**.

## Install from GitHub Releases

Install the desired release wheel as a local `uv` tool. Replace the version in
the URL when installing a newer release:

```bash
uv tool install "https://github.com/frankherchet/local-paperless-ngx-mcp/releases/download/v0.8.1/local_paperless_ngx_mcp-0.8.1-py3-none-any.whl"
paperless-ngx-mcp setup
```

The setup wizard asks for the URL and API token without echoing the token,
validates the connection, and saves the configuration only after validation.
Write tools are enabled by default; use `paperless-ngx-mcp setup --read-only`
to keep the server in read-only mode. Permanent document deletion is unavailable
in either mode.

New configurations use REST API v10 by default. Persist another version during
setup when needed:

```bash
paperless-ngx-mcp setup --api-version 10
```

The local JSON configuration file is not encrypted. On macOS and Linux the
server enforces `0600` file permissions; on Windows it is placed in the user's
AppData directory. Show its location and a masked configuration with:

```bash
paperless-ngx-mcp config show
```

To migrate an existing configuration once without changing the source file:

```bash
paperless-ngx-mcp setup --from-env /absolute/path/to/.env
```

To upgrade, install a newer release wheel with `--force`. Before uninstalling,
run `paperless-ngx-mcp config reset` if you also want to remove local
credentials.

## Run the server

After setup, start a stdio MCP server with:

```bash
paperless-ngx-mcp
```

When launched interactively without configuration, the command opens the setup
wizard and exits afterward. When a non-interactive MCP client such as Codex
starts it without configuration, it exits with a clear setup hint instead:
stdin and stdout are reserved for the MCP protocol.

## Develop from the repository

For development rather than installing a release:

```bash
git clone https://github.com/frankherchet/local-paperless-ngx-mcp.git
cd local-paperless-ngx-mcp
uv sync --extra dev
uv run paperless-ngx-mcp setup
uv run fastmcp run fastmcp.json
```

## Configure an MCP client

For a local MCP client such as the Codex app, configure the executable installed
by `uv`. `uv tool dir --bin` prints the parent directory.

```json
{
  "mcpServers": {
    "paperless-ngx": {
      "command": "<UV-TOOL-BIN>/paperless-ngx-mcp",
      "args": []
    }
  }
}
```

The server never reads `.env` files automatically. For CI, containers, or
headless environments, pass `PAPERLESS_URL` and `PAPERLESS_TOKEN` explicitly
as process environment variables. They must always be supplied together and
override the local configuration file. `PAPERLESS_API_VERSION`,
`PAPERLESS_REQUEST_TIMEOUT_MS`, and `PAPERLESS_READ_ONLY` can also be overridden
explicitly.

## Available tools

| Tool | Purpose | Writes to Paperless |
| --- | --- | --- |
| `paperless_status` | Check connectivity, versions, and document count | No |
| `search_documents` | Find documents in four search modes | No |
| `get_document` | Retrieve document metadata, OCR text, and optional file checksums | No |
| `list_metadata` | List organization objects and workflows | No |
| `list_workflows` / `get_workflow` | Paginate or retrieve nested workflows | No |
| `create_workflow` / `update_workflow` | Create or patch nested Paperless workflows | Yes |
| `delete_workflow` | Preview or, after approval, delete a workflow | Optional |
| `configure_default_intake` | Configure the default intake for new documents only | Optional |
| `verify_default_intake` | Validate the default intake configuration without writing | No |
| `get_organization_overview` | Summarize usage, duplicates, and metadata gaps | No |
| `find_documents_missing_metadata` | Find documents missing selected metadata | No |
| `find_documents_by_metadata` | Find documents linked to an organization object | No |
| `create_organization_item` | Create an organization object | Yes |
| `update_organization_item` | Rename or configure an organization object | Yes |
| `bulk_edit_objects` | Use `/api/bulk_edit_objects/` with a preview and safety checks | Optional |
| `set_document_metadata_field` | Set or clear a correspondent, type, or storage path | Yes |
| `modify_document_tags` | Add or remove tags | Yes |
| `list_trashed_documents` | List recoverable trash contents | No |
| `move_documents_to_trash` | Move documents to recoverable trash | Yes |
| `restore_documents_from_trash` | Restore documents from trash | Yes |
| `update_document` | Change supported document fields through REST PATCH | Optional |
| `document_notes` | List or create document notes | Optional |

`update_document` and other write tools require
`PAPERLESS_READ_ONLY=false`. The setup wizard enables this by default; use
`setup --read-only` or a process environment override to force read-only mode.

Even when writes are enabled, the MCP cannot permanently delete documents:

- It does not register a document-delete tool.
- The API client blocks HTTP `DELETE` requests for documents.
- Emptying Paperless trash is explicitly blocked.
- Unsupported bulk methods such as `merge` and `delete_pages` are rejected.
- `move_documents_to_trash` uses only Paperless' recoverable trash.

Workflow objects can be deleted only through `delete_workflow`, which defaults
to `dry_run=true`. This never affects existing documents or trash.

### Default intake for new documents

`configure_default_intake(storage_path_id=18, dry_run=true)` plans an
MCP-owned workflow for all newly added documents. The workflow deliberately has
the stable name `Standard-Eingang – neue Dokumente`; keeping this existing name
prevents upgrades from creating a second intake workflow. It uses the
`Document Added` trigger and assigns the selected storage path. Existing
documents are never changed.

The function is idempotent: it creates only this workflow, or updates only this
workflow when its definition differs from the intended configuration. It leaves
all other workflows unchanged and orders itself after existing storage-path
assignments. Inspect the dry run first, then repeat with `dry_run=false` to
apply it. `verify_default_intake()` checks the name, enabled state, trigger,
storage path, and absence of filters without writing.

### Safe cleanup of organization objects

Unused tags, correspondents, document types, and storage paths can be removed
with the REST-oriented `bulk_edit_objects` tool. It represents
`POST /api/bulk_edit_objects/` using the API fields `objects`, `object_type`,
and `operation`, and applies central safety checks:

1. The default `dry_run=true` checks `document_count`, workflow triggers, and
   workflow actions. For tags, child tags are also treated as references.
2. Show the preview to the user and obtain explicit approval.
3. Repeat the same request with `dry_run=false`; reference checks run again
   immediately before deletion, and the complete request fails if an entry is
   used or no longer exists.

The MCP marks this bulk tool as destructive. For `operation=delete`, it allows
only the four organization-object types above. It cannot delete documents,
custom fields, saved views, or other object types.

`list_metadata(object_type="workflows")` returns full triggers and actions for
manual review. The deletion guard repeats the same workflow-reference checks on
the server. If workflows cannot be read, the check fails and nothing is deleted.

For safe duplicate checking, call `get_document` with
`include_file_metadata=true`. Its `file_metadata` response includes
`original_checksum` and `archive_checksum`. `document_notes` represents GET and
POST operations for `/api/documents/{id}/notes/`; deleting notes is unavailable
because of the global HTTP-`DELETE` safeguard.

### AI-assisted organization review

The analysis tools do not load OCR content. A useful first prompt in an
MCP-capable chat is:

> Review my Paperless organization using `get_organization_overview`. Assess
> tags, correspondents, document types, storage paths, custom fields, and saved
> views. Treat unused entries only as review candidates; do not propose deletions
> yet.

For detailed review, the model can paginate through each object type using
`list_metadata`. `find_documents_missing_metadata` provides compact document
metadata for missing assignments without sending OCR text.

Recommended workflow:

1. Create an analysis and cleanup plan in read-only mode.
2. Have the user approve the proposed target structure.
3. Enable write mode with `paperless-ngx-mcp setup` if needed, then restart the
   MCP server.
4. Apply changes in small batches and verify each one.
5. Preview unused organization-object removal with
   `bulk_edit_objects(dry_run=true)`, obtain confirmation, and repeat the same
   call with `dry_run=false`.

## Development

```bash
uv run ruff format .
uv run ruff check .
uv run mypy
uv run pytest
```

Run every check at once:

```bash
uv run ruff format --check . \
  && uv run ruff check . \
  && uv run mypy \
  && uv run pytest
```

## Security

- The server does not listen on a network port; it uses `stdio` only.
- API tokens are stored in the access-restricted user configuration or in
  explicitly supplied process variables, never in an automatically loaded
  `.env` file.
- The wizard enables writes by default; use `setup --read-only` for read-only
  operation.
- Permanent document deletion and emptying Paperless trash are unavailable even
  when writes are enabled.
- Search results do not include full OCR text; it is returned only by
  `get_document`, subject to a configurable size limit.

See [SECURITY.md](SECURITY.md) for token-storage details and vulnerability
reporting.
