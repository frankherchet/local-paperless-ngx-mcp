# Changelog

All notable changes to this project are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Added `paperless-ngx-mcp doctor` to verify the configured authenticated Paperless
  connection, API version, and visible document count without writing to Paperless or
  printing credentials. Exit codes distinguish connection and configuration failures.
- Added a shared Claude Code and OMP marketplace catalog and plugin manifest. Both clients
  install the same MCP launch configuration and inbox skill as Codex. Plugin version: 0.2.6.

## [0.11.1] - 2026-11-23

Version 0.11.0 was tagged but not published because the Windows CI gate failed.

### Added

- Added combinable created-date, required/excluded tag, correspondent, document type, and
  storage-path filters to `search_documents`. They work with the existing search modes and
  custom-field filters.
- Added `preview_document` and `download_document` for archive or original files, including
  optional version selection. Both save a new local file and return its path, type, and size
  without embedding document content in MCP responses.
- Added `import_document` and `get_document_import_status`. Import uploads one selected local
  file without removing it and reports a new document ID only when the Paperless intake task
  confirms it. Pending jobs can be checked without uploading again.

### Changed

- Bumped the Codex plugin to 0.2.5 and pinned it to the MCP 0.11.1 wheel.

### Fixed

- Fixed Windows release checks by accessing the POSIX user ID only on non-Windows platforms.
- Replaced the Windows PowerShell permission check with a native Windows ACL check
  to avoid startup timeouts while keeping file transfers restricted to private paths.

### Security

- Local file transfers are disabled unless `PAPERLESS_TRANSFER_DIR` points to a trusted,
  private directory. Uploads reject path traversal and links and respect Paperless read-only
  mode. Preview and download do not change Paperless but create local files.

## [0.10.0] - 2026-11-23

### Added

- Added a GitHub-distributed Codex plugin marketplace with an inbox-sorting skill that reuses
  existing metadata, applies only confident document changes, and leaves uncertain documents for
  review.
- Added `custom_field_query` to document search, including relative date filters on Paperless-ngx
  3.4 and later, and `modify_document_custom_fields` to set or remove selected field assignments
  on multiple documents. Edits respect read-only mode. Paperless-ngx 3.5.1 can retain old
  reciprocal links when replacing or clearing document-link values; remove the field before
  reassigning links.

### Changed

- Changed the Codex inbox skill to derive the intake storage path from the configured standard
  intake workflow instead of requiring the instance-specific `00 Eingang/Zu prüfen` name. When
  the workflow is absent or ambiguous, the skill now asks the user to select a plausible path.
- Changed the recommended MCP client configuration to launch a pinned release through `uv` with
  a managed Python runtime, avoiding fragile repository-local virtual-environment paths.
- Changed the bundled Codex plugin to use the same resilient, release-pinned MCP launch.
- Bumped the Codex plugin to 0.2.3 and pinned it to the MCP 0.10.0 wheel.
- Moved trash and OCR reprocessing to dedicated Paperless-ngx 3.x endpoints, replacing deprecated
  document bulk methods. API v10 remains the default.

### Fixed

- Made the Codex inbox skill confirm queued metadata writes before moving documents out of the
  intake path and confirm each move before reporting it as complete. Failed or unconfirmed jobs
  are reported separately.

### Security

- Limited the inbox skill to document metadata updates; it never authorizes trash, deletion,
  organization changes, workflow changes, or OCR reprocessing.
- Restricted dedicated trash and OCR requests to explicit document IDs, without an archive-wide
  selector or OCR overrides.

## [0.9.0] - 2026-08-25

### Added

- Added read-only MCP tools for Paperless document suggestions, archive statistics, and compact
  task-queue overviews.
- Added mail rules to the generic metadata reader without exposing mail-account credentials.
- Added compact and full detail modes, exact item lookup, and name filtering to `list_metadata`.

### Changed

- Reduced AI context usage across document search, history, tasks, metadata, workflows, and
  organization analysis by returning compact, paginated payloads by default.
- Normalized history and task responses across Paperless REST API v9 and v10.
- Preserved structured MCP results while replacing duplicate text serialization with a short
  marker for clients that support `structuredContent`.
- Empty simple document searches now return recent documents instead of an empty result.
- GitHub releases now publish these curated changelog notes instead of sparse generated summaries.

### Security

- Kept permanent document deletion and trash-emptying unavailable through MCP.
- Kept metadata deletion fail-closed when documents, workflows, mail rules, or saved views may
  still reference an object.

## [0.8.5] - 2026-08-23

### Changed

- Split default-intake configuration, metadata cleanup, organization analysis, and mutation safety
  into focused modules with smaller public interfaces.
- Simplified the Paperless client and centralized mutation-policy enforcement.
- Strengthened cross-platform CI, release artifact checks, and secret scanning.

### Security

- Made metadata reference checks fail closed and preserved the permanent document deletion ban.

## [0.8.4] - 2026-08-23

### Added

- Added read-only tools for individual task status and active Paperless queue inspection.

## [0.8.3] - 2026-08-23

### Added

- Added read-only access to document history through `get_document_history`.

## [0.8.2] - 2026-08-22

### Added

- Added safe document reprocessing with Paperless's configured OCR mode.

### Security

- Centralized mutation checks, closed raw-write bypasses, and protected metadata deletion with
  workflow, mail-rule, and saved-view reference checks.

## [0.8.1] - 2026-07-30

### Changed

- Translated the repository documentation and security guidance to English.

## [0.8.0] - 2026-07-30

### Added

- Added persistent Paperless REST API version selection to the setup command.

## [0.7.0] - 2026-07-30

### Added

- Added GitHub release distribution with wheel, source archive, and checksums.
- Added the cross-platform setup and configuration CLI with secure token input, connection
  validation, environment import, masked display, and reset support.
- Added configuration-file permission checks and non-interactive startup behavior for MCP clients.

[Unreleased]: https://github.com/frankherchet/local-paperless-ngx-mcp/compare/v0.11.1...HEAD
[0.11.1]: https://github.com/frankherchet/local-paperless-ngx-mcp/compare/v0.10.0...v0.11.1
[0.10.0]: https://github.com/frankherchet/local-paperless-ngx-mcp/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/frankherchet/local-paperless-ngx-mcp/compare/v0.8.5...v0.9.0
[0.8.5]: https://github.com/frankherchet/local-paperless-ngx-mcp/compare/v0.8.4...v0.8.5
[0.8.4]: https://github.com/frankherchet/local-paperless-ngx-mcp/compare/v0.8.3...v0.8.4
[0.8.3]: https://github.com/frankherchet/local-paperless-ngx-mcp/compare/v0.8.2...v0.8.3
[0.8.2]: https://github.com/frankherchet/local-paperless-ngx-mcp/compare/v0.8.1...v0.8.2
[0.8.1]: https://github.com/frankherchet/local-paperless-ngx-mcp/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/frankherchet/local-paperless-ngx-mcp/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/frankherchet/local-paperless-ngx-mcp/releases/tag/v0.7.0
