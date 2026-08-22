from __future__ import annotations

import pytest

from paperless_ngx_mcp.mutation_policy import (
    MutationOperation,
    PermanentDeletionDisabled,
    ReadOnlyError,
    WriteMode,
    classify_request,
    enforce_transport_mutation_policy,
    ensure_reference_review_before_deletion,
    ensure_write_enabled,
)


def test_request_classification_distinguishes_reversible_and_blocked_mutations() -> None:
    assert classify_request("GET", "/api/documents/", None).operation is MutationOperation.READ
    assert (
        classify_request(
            "POST",
            "api/documents/bulk_edit/",
            {"method": "delete"},
        ).operation
        is MutationOperation.DOCUMENT_BULK_EDIT
    )
    assert (
        classify_request("DELETE", "api/documents/42/", None).operation
        is MutationOperation.BLOCKED_DELETE
    )


def test_workflow_delete_requires_the_internal_approved_path() -> None:
    with pytest.raises(PermanentDeletionDisabled, match="must use delete_workflow"):
        enforce_transport_mutation_policy("DELETE", "api/workflows/4/", None)

    result = enforce_transport_mutation_policy(
        "DELETE",
        "api/workflows/4/",
        None,
        allow_workflow_deletion=True,
    )
    assert result.operation is MutationOperation.WORKFLOW_DELETE


def test_write_mode_and_metadata_reference_review_are_enforced() -> None:
    with pytest.raises(ReadOnlyError, match="PAPERLESS_READ_ONLY=false"):
        ensure_write_enabled(WriteMode.READ_ONLY)

    ensure_write_enabled(WriteMode.READ_WRITE)
    ensure_reference_review_before_deletion(dry_run=True, blocked=[{"id": 7}], missing_ids=[9])
    with pytest.raises(ValueError, match="reference check"):
        ensure_reference_review_before_deletion(
            dry_run=False,
            blocked=[{"id": 7}],
            missing_ids=[9],
        )
    with pytest.raises(ValueError, match="result is invalid"):
        ensure_reference_review_before_deletion(
            dry_run=False,
            blocked=None,
            missing_ids=[],
        )
