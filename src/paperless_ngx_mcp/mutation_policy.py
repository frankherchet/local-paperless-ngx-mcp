"""Central safety policy for all Paperless mutations."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class PaperlessError(RuntimeError):
    """Base error raised by the Paperless MCP implementation."""


class ReadOnlyError(PaperlessError):
    """A mutation was attempted while read-only mode is active."""


class PermanentDeletionDisabled(PaperlessError):
    """The requested operation could permanently delete Paperless data."""


WRITABLE_ORGANIZATION_OBJECT_TYPES = frozenset(
    {
        "tags",
        "correspondents",
        "document_types",
        "storage_paths",
        "custom_fields",
    }
)

DELETABLE_ORGANIZATION_OBJECT_TYPES = frozenset(
    {
        "tags",
        "correspondents",
        "document_types",
        "storage_paths",
    }
)

SAFE_BULK_DOCUMENT_METHODS = frozenset(
    {
        "set_correspondent",
        "set_document_type",
        "set_storage_path",
        "modify_tags",
        "delete",  # Paperless moves documents to its reversible trash.
        "reprocess",  # Paperless applies its configured OCR processing mode.
    }
)


def ensure_write_enabled(read_only: bool) -> None:
    """Reject every mutation while the connection is explicitly read-only."""
    if read_only:
        raise ReadOnlyError(
            "Write tools are disabled. Set PAPERLESS_READ_ONLY=false to enable updates."
        )


def enforce_transport_mutation_policy(
    method: str,
    path: str,
    payload: Mapping[str, Any] | None,
    *,
    allow_workflow_deletion: bool = False,
    allow_metadata_deletion: bool = False,
) -> bool:
    """Reject unsafe requests and return whether the request mutates Paperless."""
    normalized_method = method.upper()
    normalized_path = path.lstrip("/")

    if normalized_method == "DELETE":
        workflow_id = normalized_path.removeprefix("api/workflows/").removesuffix("/")
        is_workflow = (
            normalized_path.startswith("api/workflows/")
            and workflow_id.isdigit()
            and int(workflow_id) > 0
        )
        if is_workflow and not allow_workflow_deletion:
            raise PermanentDeletionDisabled(
                "Workflow deletion must use delete_workflow after its dry-run preview."
            )
        if not is_workflow:
            raise PermanentDeletionDisabled(
                "HTTP DELETE is disabled in this MCP server. "
                "Documents can only be moved to Paperless trash; only workflow objects "
                "may be deleted through delete_workflow."
            )

    if normalized_method == "POST" and normalized_path == "api/bulk_edit_objects/":
        if not _is_allowed_metadata_deletion_payload(payload):
            raise PermanentDeletionDisabled(
                "Object bulk editing is restricted to deleting explicitly selected tags, "
                "correspondents, document types, or storage paths."
            )
        if not allow_metadata_deletion:
            raise PermanentDeletionDisabled(
                "Metadata deletion must use bulk_edit_objects after its reference-check preview."
            )

    if (
        normalized_method == "POST"
        and normalized_path == "api/trash/"
        and _payload_value(payload, "action") != "restore"
    ):
        raise PermanentDeletionDisabled(
            "Only restoring documents is allowed on the Paperless trash endpoint. "
            "Emptying trash is permanently disabled."
        )

    if (
        normalized_method == "POST"
        and normalized_path == "api/documents/bulk_edit/"
        and _payload_value(payload, "method") not in SAFE_BULK_DOCUMENT_METHODS
    ):
        raise PermanentDeletionDisabled(
            f"Bulk document method is not allowed: {_payload_value(payload, 'method')}"
        )

    return normalized_method not in {"GET", "HEAD", "OPTIONS"}


def ensure_writable_organization_type(object_type: str) -> None:
    """Validate the organization records that can be created or updated."""
    if object_type not in WRITABLE_ORGANIZATION_OBJECT_TYPES:
        raise ValueError(f"Unsupported writable organization object type: {object_type}")


def ensure_deletable_organization_type(object_type: str) -> None:
    """Validate the limited organization records that may be deleted."""
    if object_type not in DELETABLE_ORGANIZATION_OBJECT_TYPES:
        raise ValueError(f"Unsupported deletable organization object type: {object_type}")


def ensure_metadata_deletion_operation(object_type: str, operation: str) -> None:
    """Ensure that metadata bulk edits stay within the deletion allowlist."""
    ensure_deletable_organization_type(object_type)
    if operation != "delete":
        raise ValueError(f"Unsupported object bulk operation: {operation}")


def ensure_reference_review_before_deletion(
    *,
    dry_run: bool,
    blocked: object,
    missing_ids: object,
) -> None:
    """Require a clean reference review before submitting a metadata deletion."""
    if dry_run:
        return
    if not isinstance(blocked, list):
        raise ValueError("Organization deletion blocked: reference check result is invalid")
    if not isinstance(missing_ids, list) or not all(
        isinstance(item_id, int) and not isinstance(item_id, bool) and item_id > 0
        for item_id in missing_ids
    ):
        raise ValueError("Organization deletion blocked: reference check result is invalid")
    if blocked or missing_ids:
        raise ValueError(
            "Organization deletion blocked by reference check: "
            f"blocked={list(blocked)}, missing_ids={list(missing_ids)}"
        )


def ensure_safe_document_bulk_method(method: str) -> None:
    """Only allow reversible document bulk edits, including move-to-trash."""
    if method not in SAFE_BULK_DOCUMENT_METHODS:
        raise PermanentDeletionDisabled(f"Bulk document method is not allowed: {method}")


def _is_allowed_metadata_deletion_payload(payload: Mapping[str, Any] | None) -> bool:
    object_ids = _payload_value(payload, "objects")
    return (
        _payload_value(payload, "object_type") in DELETABLE_ORGANIZATION_OBJECT_TYPES
        and _payload_value(payload, "operation") == "delete"
        and _are_positive_ids(object_ids)
    )


def _are_positive_ids(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(
            isinstance(item_id, int) and not isinstance(item_id, bool) and item_id > 0
            for item_id in value
        )
    )


def _payload_value(payload: Mapping[str, Any] | None, key: str) -> object | None:
    return payload.get(key) if payload is not None else None
