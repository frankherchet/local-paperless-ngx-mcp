from __future__ import annotations

import pytest

from paperless_ngx_mcp.mutation_policy import (
    PermanentDeletionDisabled,
    ReadOnlyError,
    enforce_transport_mutation_policy,
    ensure_reference_review_before_deletion,
    ensure_write_enabled,
)


def test_transport_policy_distinguishes_reversible_and_blocked_mutations() -> None:
    assert enforce_transport_mutation_policy("GET", "/api/documents/", None) is False
    assert (
        enforce_transport_mutation_policy(
            "POST",
            "api/documents/bulk_edit/",
            {"method": "delete"},
        )
        is True
    )
    with pytest.raises(PermanentDeletionDisabled, match="HTTP DELETE is disabled"):
        enforce_transport_mutation_policy("DELETE", "api/documents/42/", None)


def test_workflow_delete_requires_the_internal_approved_path() -> None:
    with pytest.raises(PermanentDeletionDisabled, match="must use delete_workflow"):
        enforce_transport_mutation_policy("DELETE", "api/workflows/4/", None)

    assert (
        enforce_transport_mutation_policy(
            "DELETE",
            "api/workflows/4/",
            None,
            allow_workflow_deletion=True,
        )
        is True
    )


def test_read_only_mode_and_metadata_reference_review_are_enforced() -> None:
    with pytest.raises(ReadOnlyError, match="PAPERLESS_READ_ONLY=false"):
        ensure_write_enabled(True)

    ensure_write_enabled(False)
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
