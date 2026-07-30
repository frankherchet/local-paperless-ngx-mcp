# Security

## Credentials

`paperless-ngx-mcp setup` stores the URL and API token in a local,
user-specific configuration file. On POSIX systems, the server enforces `0600`
permissions for the file and `0700` for its directory. The file is not
encrypted, so use a protected user account and never share the file.

The server does not load `.env` files automatically. Process environment
variables remain available for automated and headless deployments;
`PAPERLESS_URL` and `PAPERLESS_TOKEN` must be supplied together.

The API token is not included in tool responses or CLI output.

## Documents

The server provides no tool for permanently deleting documents, sends no HTTP
`DELETE` request for documents, and cannot empty Paperless trash.

## Reporting vulnerabilities

Do not disclose credentials or security vulnerabilities in public issues.
Report them privately to the repository owner instead.
