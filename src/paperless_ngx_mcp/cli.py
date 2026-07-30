"""Command-line setup and stdio launch entry point."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from paperless_ngx_mcp import __version__
from paperless_ngx_mcp.client import PaperlessClient
from paperless_ngx_mcp.config import (
    CREDENTIAL_ENV_NAMES,
    ConfigurationError,
    Settings,
    config_path,
    get_settings,
    load_config_values,
    load_env_file,
    make_settings,
    masked_config_values,
    reset_config,
    write_config,
)
from paperless_ngx_mcp.server import mcp


def main(argv: Sequence[str] | None = None) -> None:
    """Run the user-facing CLI and return an appropriate process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.version:
        print(__version__)
        return

    command = args.command or "serve"
    try:
        if command == "setup":
            _run_setup(args)
        elif command == "config":
            _run_config(args)
        else:
            _run_server()
    except ConfigurationError as error:
        print(f"paperless-ngx-mcp: {error}", file=sys.stderr)
        raise SystemExit(2) from error
    except KeyboardInterrupt:
        raise SystemExit(130) from None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local Paperless-ngx MCP server")
    parser.add_argument(
        "--version", action="store_true", help="Show the installed version and exit"
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("serve", help="Run the MCP server over stdio")

    setup = subparsers.add_parser("setup", help="Configure Paperless access interactively")
    setup.add_argument("--read-only", action="store_true", help="Disable all Paperless write tools")
    setup.add_argument(
        "--from-env",
        type=Path,
        metavar="PATH",
        help="Import URL, token, and options from an explicitly selected .env file",
    )

    config = subparsers.add_parser("config", help="Inspect or remove local configuration")
    config_subparsers = config.add_subparsers(dest="config_command", required=True)
    config_subparsers.add_parser("show", help="Show the local configuration with a masked token")
    reset = config_subparsers.add_parser("reset", help="Remove the local configuration")
    reset.add_argument(
        "--yes", action="store_true", help="Confirm removal without an interactive prompt"
    )
    return parser


def _run_server() -> None:
    try:
        get_settings()
    except ConfigurationError:
        missing_local_config = not config_path().exists()
        has_credential_environment = any(name in os.environ for name in CREDENTIAL_ENV_NAMES)
        if (
            missing_local_config
            and not has_credential_environment
            and sys.stdin.isatty()
            and sys.stdout.isatty()
        ):
            print("Paperless is not configured yet. Starting setup…")
            _run_setup(argparse.Namespace(read_only=False, from_env=None))
            print("Setup completed. Start the server again from your MCP client.")
            return
        raise
    mcp.run(transport="stdio", show_banner=False)


def _run_setup(
    args: argparse.Namespace,
    *,
    input_func: Callable[[str], str] = input,
    secret_input: Callable[[str], str] = getpass.getpass,
    verify: Callable[[Settings], dict[str, Any]] | None = None,
) -> None:
    imported = load_env_file(args.from_env) if args.from_env is not None else {}
    existing = load_config_values()
    values = {**existing, **imported}
    default_url = str(values.get("PAPERLESS_URL", ""))
    url_answer = ""
    if args.from_env is None or not default_url:
        url_answer = input_func(_prompt_with_default("Paperless URL", default_url)).strip()
    if url_answer:
        values["PAPERLESS_URL"] = url_answer
    elif not default_url:
        raise ConfigurationError("Paperless URL is required")

    existing_token = values.get("PAPERLESS_TOKEN")
    token_prompt = "Paperless API token (input hidden)"
    if existing_token:
        token_prompt += " [press Enter to retain current token]"
    token_answer = ""
    if args.from_env is None or not existing_token:
        token_answer = secret_input(f"{token_prompt}: ").strip()
    if token_answer:
        values["PAPERLESS_TOKEN"] = token_answer
    elif not existing_token:
        raise ConfigurationError("Paperless API token is required")

    if args.read_only:
        values["PAPERLESS_READ_ONLY"] = True
    elif "PAPERLESS_READ_ONLY" not in values:
        values["PAPERLESS_READ_ONLY"] = False

    settings = make_settings(values)
    check_connection = verify or _verify_connection
    result = check_connection(settings)
    target = write_config(settings)
    get_settings.cache_clear()
    print(f"Configuration saved to {target}")
    print(f"Connected to Paperless {result.get('paperless_version') or '(version unavailable)'}.")
    if not settings.paperless_read_only:
        print("Write tools are enabled; permanent document deletion remains unavailable.")


def _run_config(args: argparse.Namespace) -> None:
    if args.config_command == "show":
        values = load_config_values()
        target = config_path()
        if not values:
            print(f"No local configuration exists at {target}")
            return
        print(f"Configuration file: {target}")
        print(json_for_display(masked_config_values(values)))
        return
    if args.config_command == "reset":
        if not args.yes:
            answer = input(f"Remove local configuration at {config_path()}? [y/N] ").strip().lower()
            if answer not in {"y", "yes"}:
                print("Configuration was not removed.")
                return
        removed = reset_config()
        get_settings.cache_clear()
        print("Configuration removed." if removed else "No local configuration exists.")


def _verify_connection(settings: Settings) -> dict[str, Any]:
    async def check() -> dict[str, Any]:
        async with PaperlessClient(settings) as client:
            return await client.check_connection()

    return asyncio.run(check())


def _prompt_with_default(label: str, default: str) -> str:
    return f"{label} [{default}]: " if default else f"{label}: "


def json_for_display(values: dict[str, Any]) -> str:
    """Render a stable terminal representation used by the config subcommand."""
    import json

    return json.dumps(values, indent=2, sort_keys=True)
