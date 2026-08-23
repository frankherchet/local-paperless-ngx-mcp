from __future__ import annotations

import pytest

from paperless_ngx_mcp.metadata_cleanup import preview_metadata_deletion


def test_preview_preserves_shape_and_all_reference_details() -> None:
    preview = preview_metadata_deletion(
        "tags",
        [
            {"id": 1, "name": "Parent", "document_count": 0},
            {"id": 2, "name": "Used", "document_count": 3},
            {"id": 3, "name": "Child", "document_count": 0, "parent": 1},
            {"id": 4, "name": "Free", "document_count": 0},
        ],
        [
            {
                "id": 10,
                "name": "Tag workflow",
                "enabled": True,
                "triggers": [],
                "actions": [{"id": 11, "assign_tags": [3]}],
            }
        ],
        [{"id": 12, "name": "Tag rule", "assign_tags": [3]}],
        [
            {
                "id": 13,
                "name": "Tag view",
                "filter_rules": [{"rule_type": 6, "value": "[3]"}],
            }
        ],
        [4, 3, 2, 1, 99],
    )

    assert [item["id"] for item in preview["candidates"]] == [4]
    assert [item["id"] for item in preview["blocked"]] == [1, 2, 3]
    assert preview["blocked"][0]["blocking_reasons"] == ["parent_of_tags"]
    assert preview["blocked"][1]["blocking_reasons"] == ["referenced_by_documents"]
    assert preview["blocked"][2]["blocking_reasons"] == [
        "referenced_by_workflows",
        "referenced_by_mail_rules",
        "referenced_by_saved_views",
    ]
    assert preview["blocked"][2]["workflow_references"][0]["field"] == "assign_tags"
    assert preview["blocked"][2]["mail_rule_references"][0]["mail_rule_id"] == 12
    assert preview["blocked"][2]["saved_view_references"][0]["saved_view_id"] == 13
    assert preview["missing_ids"] == [99]
    assert preview["candidate_count"] == 1
    assert preview["blocked_count"] == 3
    assert preview["scanned_count"] == 4
    assert preview["reference_checks"][-1] == "child_tag_relationships"
    assert preview["requires_explicit_user_approval"] is True


def test_unselected_used_items_are_omitted_but_invalid_references_fail_closed() -> None:
    preview = preview_metadata_deletion(
        "correspondents",
        [
            {"id": 1, "name": "Used", "document_count": 1},
            {"id": 2, "name": "Free", "document_count": 0},
        ],
        [],
        [],
        [],
        None,
    )

    assert preview["candidates"] == [
        {
            "id": 2,
            "name": "Free",
            "document_count": 0,
            "child_tag_ids": [],
            "deletable": True,
            "blocking_reasons": [],
            "workflow_references": [],
            "mail_rule_references": [],
            "saved_view_references": [],
        }
    ]
    assert preview["referenced_items_omitted"] == 1

    with pytest.raises(ValueError, match="Invalid metadata reference"):
        preview_metadata_deletion(
            "correspondents",
            [{"id": 2, "name": "Free", "document_count": 0}],
            [{"id": 5, "triggers": [{"filter_has_correspondent": "2"}], "actions": []}],
            [],
            [],
            [2],
        )
