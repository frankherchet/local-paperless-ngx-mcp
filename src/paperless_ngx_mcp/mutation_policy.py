"""Central safety policy for all Paperless mutations.

The policy is deliberately independent from HTTP and FastMCP.  It classifies an
outgoing request and validates the small set of destructive operations that this
project permits.  Keeping these checks here makes the permanent-document-deletion
ban testable without a running Paperless instance.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class PaperlessError(RuntimeError):
    """Base error raised by the Paperless MCP implementation."""


class ReadOnlyError(PaperlessError):
    """A mutation was attempted while read-only mode is active."""


class PermanentDeletionDisabled(PaperlessError):
    """The requested operation could permanently delete Paperless data."""


class WriteMode(StrEnum):
    """Whether the configured Paperless connection permits mutations."""

    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class MutationOperation(StrEnum):
    """The policy-relevant class of an outgoing Paperless request."""

    READ = "read"
    OTHER_WRITE = "other_write"
    WORKFLOW_DELETE = "workflow_delete"
    METADATA_DELETE = "metadata_delete"
    DOCUMENT_BULK_EDIT = "document_bulk_edit"
    TRASH_RESTORE = "trash_restore"
    BLOCKED_DELETE = "blocked_delete"
    BLOCKED_METADATA_DELETE = "blocked_metadata_delete"
    BLOCKED_DOCUMENT_BULK_EDIT = "blocked_document_bulk_edit"
    BLOCKED_TRASH_ACTION = "blocked_trash_action"


@dataclass(frozen=True)
class RequestMutation:
    """Classification returned before a request reaches the network."""

    operation: MutationOperation
    method: str
    path: str

    @property
    def is_mutation(self) -> bool:
        return self.operation is not MutationOperation.READ


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
        "add_tag",
        "remove_tag",
        "modify_tags",
        "modify_custom_fields",
        "delete",  # Paperless moves documents to its reversible trash.
        "reprocess",  # Paperless applies its configured OCR processing mode.
    }
)


def write_mode(*, read_only: bool) -> WriteMode:
    """Return the policy mode for the configured connection."""
    return WriteMode.READ_ONLY if read_only else WriteMode.READ_WRITE


def ensure_write_enabled(mode: WriteMode) -> None:
    """Reject every mutation while the connection is explicitly read-only."""
    if mode is WriteMode.READ_ONLY:
        raise ReadOnlyError(
            "Write tools are disabled. Set PAPERLESS_READ_ONLY=false to enable updates."
        )


def classify_request(
    method: str,
    path: str,
    payload: Mapping[str, Any] | None,
) -> RequestMutation:
    """Classify a request according to the permanent-deletion safety policy."""
    normalized_method = method.upper()
    normalized_path = path.lstrip("/")

    if normalized_method == "DELETE":
        workflow_id = normalized_path.removeprefix("api/workflows/").removesuffix("/")
        if (
            normalized_path.startswith("api/workflows/")
            and workflow_id.isdigit()
            and int(workflow_id) > 0
        ):
            return RequestMutation(
                MutationOperation.WORKFLOW_DELETE,
                normalized_method,
                normalized_path,
            )
        return RequestMutation(MutationOperation.BLOCKED_DELETE, normalized_method, normalized_path)

    if normalized_method == "POST" and normalized_path == "api/bulk_edit_objects/":
        if _is_allowed_metadata_deletion_payload(payload):
            operation = MutationOperation.METADATA_DELETE
        else:
            operation = MutationOperation.BLOCKED_METADATA_DELETE
        return RequestMutation(operation, normalized_method, normalized_path)

    if normalized_method == "POST" and normalized_path == "api/trash/":
        operation = (
            MutationOperation.TRASH_RESTORE
            if _payload_value(payload, "action") == "restore"
            else MutationOperation.BLOCKED_TRASH_ACTION
        )
        return RequestMutation(operation, normalized_method, normalized_path)

    if normalized_method == "POST" and normalized_path == "api/documents/bulk_edit/":
        operation = (
            MutationOperation.DOCUMENT_BULK_EDIT
            if _payload_value(payload, "method") in SAFE_BULK_DOCUMENT_METHODS
            else MutationOperation.BLOCKED_DOCUMENT_BULK_EDIT
        )
        return RequestMutation(operation, normalized_method, normalized_path)

    operation = (
        MutationOperation.READ
        if normalized_method in {"GET", "HEAD", "OPTIONS"}
        else MutationOperation.OTHER_WRITE
    )
    return RequestMutation(operation, normalized_method, normalized_path)


def enforce_transport_mutation_policy(
    method: str,
    path: str,
    payload: Mapping[str, Any] | None,
    *,
    allow_workflow_deletion: bool = False,
    allow_metadata_deletion: bool = False,
) -> RequestMutation:
    """Classify and reject unsafe requests before they are sent to Paperless."""
    request = classify_request(method, path, payload)
    if request.operation is MutationOperation.WORKFLOW_DELETE and not allow_workflow_deletion:
        raise PermanentDeletionDisabled(
            "Workflow deletion must use delete_workflow after its dry-run preview."
        )
    if request.operation is MutationOperation.METADATA_DELETE and not allow_metadata_deletion:
        raise PermanentDeletionDisabled(
            "Metadata deletion must use bulk_edit_objects after its reference-check preview."
        )
    if request.operation is MutationOperation.BLOCKED_DELETE:
        raise PermanentDeletionDisabled(
            "HTTP DELETE is disabled in this MCP server. "
            "Documents can only be moved to Paperless trash; only workflow objects "
            "may be deleted through delete_workflow."
        )
    if request.operation is MutationOperation.BLOCKED_METADATA_DELETE:
        raise PermanentDeletionDisabled(
            "Object bulk editing is restricted to deleting explicitly selected tags, "
            "correspondents, document types, or storage paths."
        )
    if request.operation is MutationOperation.BLOCKED_TRASH_ACTION:
        raise PermanentDeletionDisabled(
            "Only restoring documents is allowed on the Paperless trash endpoint. "
            "Emptying trash is permanently disabled."
        )
    if request.operation is MutationOperation.BLOCKED_DOCUMENT_BULK_EDIT:
        raise PermanentDeletionDisabled(
            f"Bulk document method is not allowed: {_payload_value(payload, 'method')}"
        )
    return request


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
