# Changelog

All notable changes to this project are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Changed the recommended MCP client configuration to launch a pinned release through `uv` with
  a managed Python runtime, avoiding fragile repository-local virtual-environment paths.

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

[Unreleased]: https://github.com/frankherchet/local-paperless-ngx-mcp/compare/v0.9.0...HEAD
[0.9.0]: https://github.com/frankherchet/local-paperless-ngx-mcp/compare/v0.8.5...v0.9.0
[0.8.5]: https://github.com/frankherchet/local-paperless-ngx-mcp/compare/v0.8.4...v0.8.5
[0.8.4]: https://github.com/frankherchet/local-paperless-ngx-mcp/compare/v0.8.3...v0.8.4
[0.8.3]: https://github.com/frankherchet/local-paperless-ngx-mcp/compare/v0.8.2...v0.8.3
[0.8.2]: https://github.com/frankherchet/local-paperless-ngx-mcp/compare/v0.8.1...v0.8.2
[0.8.1]: https://github.com/frankherchet/local-paperless-ngx-mcp/compare/v0.8.0...v0.8.1
[0.8.0]: https://github.com/frankherchet/local-paperless-ngx-mcp/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/frankherchet/local-paperless-ngx-mcp/releases/tag/v0.7.0
