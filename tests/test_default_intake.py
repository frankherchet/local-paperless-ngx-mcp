from __future__ import annotations

import pytest

from paperless_ngx_mcp.default_intake import (
    DEFAULT_INTAKE_WORKFLOW_NAME,
    plan_default_intake,
    verify_default_intake,
)


def test_plan_creates_after_other_storage_assignments() -> None:
    plan = plan_default_intake(
        [
            {
                "id": 4,
                "name": "Existing storage assignment",
                "order": 1_500,
                "actions": [{"type": 1, "assign_storage_path": 2}],
            }
        ],
        18,
        enabled=True,
    )

    assert plan.operation == "create"
    assert plan.changed is True
    assert plan.workflow_id is None
    assert plan.definition["order"] == 1_501
    assert plan.definition["actions"] == [{"type": 1, "assign_storage_path": 18}]
    assert plan.result({"id": 18, "name": "00 Eingang/Zu prüfen"}) == {
        "changed": True,
        "workflow_id": None,
        "name": DEFAULT_INTAKE_WORKFLOW_NAME,
        "enabled": True,
        "trigger": "document_added",
        "storage_path": {"id": 18, "name": "00 Eingang/Zu prüfen"},
    }


def test_plan_refuses_ambiguous_reserved_workflow() -> None:
    workflows = [{"name": DEFAULT_INTAKE_WORKFLOW_NAME}] * 2

    with pytest.raises(ValueError, match="Multiple workflows use the reserved default intake name"):
        plan_default_intake(workflows, 18, enabled=True)


def test_plan_is_a_noop_for_the_desired_definition() -> None:
    desired = plan_default_intake([], 18, enabled=True).definition
    plan = plan_default_intake([{"id": 7, **desired}], 18, enabled=True)

    assert plan.operation == "none"
    assert plan.changed is False
    assert plan.workflow_id == 7


def test_verify_reports_filters_and_missing_storage_path() -> None:
    report = verify_default_intake(
        [
            {
                "id": 7,
                "name": DEFAULT_INTAKE_WORKFLOW_NAME,
                "enabled": True,
                "triggers": [
                    {
                        "type": 2,
                        "matching_algorithm": 0,
                        "match": "",
                        "is_insensitive": True,
                        "filter_path": "mail/*",
                    }
                ],
                "actions": [{"type": 1, "assign_storage_path": 18}],
            }
        ],
        None,
    )

    assert report["valid"] is False
    assert report["trigger"] is None
    assert report["problems"] == [
        "trigger has filter filter_path",
        "storage path 18 does not exist or is not visible",
    ]
