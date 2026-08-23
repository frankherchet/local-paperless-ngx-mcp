"""Domain rules for the MCP-owned default intake workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

JsonObject = dict[str, Any]

DEFAULT_INTAKE_WORKFLOW_NAME = "Standard-Eingang – neue Dokumente"  # noqa: RUF001
DEFAULT_INTAKE_STORAGE_PATH_ID = 18

__all__ = [
    "DEFAULT_INTAKE_STORAGE_PATH_ID",
    "DEFAULT_INTAKE_WORKFLOW_NAME",
    "DefaultIntakePlan",
    "plan_default_intake",
    "verify_default_intake",
]


@dataclass(frozen=True)
class DefaultIntakePlan:
    """The only workflow mutation needed to reach the desired intake state."""

    definition: JsonObject
    operation: Literal["create", "update", "none"]
    workflow_id: int | None
    enabled: bool

    @property
    def changed(self) -> bool:
        return self.operation != "none"

    def result(self, storage_path: JsonObject) -> JsonObject:
        return {
            "changed": self.changed,
            "workflow_id": self.workflow_id,
            "name": DEFAULT_INTAKE_WORKFLOW_NAME,
            "enabled": self.enabled,
            "trigger": "document_added",
            "storage_path": {
                "id": storage_path.get("id"),
                "name": storage_path.get("name"),
            },
        }


def plan_default_intake(
    workflows: list[JsonObject],
    storage_path_id: int,
    *,
    enabled: bool,
) -> DefaultIntakePlan:
    """Plan the idempotent create, update, or no-op for the reserved workflow."""
    matching = [
        workflow for workflow in workflows if workflow.get("name") == DEFAULT_INTAKE_WORKFLOW_NAME
    ]
    if len(matching) > 1:
        raise ValueError(
            "Multiple workflows use the reserved default intake name; refusing to modify any."
        )

    existing = matching[0] if matching else None
    definition = _definition(
        storage_path_id,
        order=_order_after_storage_assignments(workflows, existing),
        enabled=enabled,
    )
    changed = existing is None or not _matches(existing, definition)
    workflow_id = existing.get("id") if existing is not None else None
    return DefaultIntakePlan(
        definition=definition,
        operation="create" if existing is None else "update" if changed else "none",
        workflow_id=workflow_id if isinstance(workflow_id, int) else None,
        enabled=enabled,
    )


def verify_default_intake(
    workflows: list[JsonObject],
    storage_path: JsonObject | None,
    *,
    storage_path_id: int = DEFAULT_INTAKE_STORAGE_PATH_ID,
) -> JsonObject:
    """Return the read-only verification report for the reserved workflow."""
    matching = [
        workflow for workflow in workflows if workflow.get("name") == DEFAULT_INTAKE_WORKFLOW_NAME
    ]
    if len(matching) != 1:
        return {
            "valid": False,
            "workflow_count": len(matching),
            "storage_path": storage_path,
            "problems": [
                f"expected exactly one workflow named {DEFAULT_INTAKE_WORKFLOW_NAME!r}, "
                f"found {len(matching)}"
            ],
        }

    workflow = matching[0]
    trigger_problems = _trigger_problems(workflow.get("triggers"))
    problems = [] if workflow.get("enabled") is True else ["workflow is not enabled"]
    problems.extend(trigger_problems)
    problems.extend(_action_problems(workflow.get("actions"), storage_path_id=storage_path_id))
    if storage_path is None:
        problems.append(f"storage path {storage_path_id} does not exist or is not visible")
    return {
        "valid": not problems,
        "workflow_count": 1,
        "workflow_id": workflow.get("id"),
        "name": workflow.get("name"),
        "enabled": workflow.get("enabled"),
        "trigger": "document_added" if not trigger_problems else None,
        "storage_path": storage_path,
        "problems": problems,
    }


def _order_after_storage_assignments(
    workflows: list[JsonObject],
    existing: JsonObject | None,
) -> int:
    highest = 0
    for workflow in workflows:
        if workflow is existing:
            continue
        actions = workflow.get("actions")
        if not isinstance(actions, list) or not any(
            isinstance(action, dict) and action.get("assign_storage_path") is not None
            for action in actions
        ):
            continue
        order = workflow.get("order")
        if isinstance(order, int) and not isinstance(order, bool):
            highest = max(highest, order)
    return max(1_000, highest + 1)


def _definition(storage_path_id: int, *, order: int, enabled: bool) -> JsonObject:
    return {
        "name": DEFAULT_INTAKE_WORKFLOW_NAME,
        "enabled": enabled,
        "order": order,
        "triggers": [
            {
                "type": 2,
                "matching_algorithm": 0,
                "match": "",
                "is_insensitive": True,
                "filter_path": None,
                "filter_filename": None,
                "filter_mailrule": None,
            }
        ],
        "actions": [{"type": 1, "assign_storage_path": storage_path_id}],
    }


def _matches(existing: JsonObject, definition: JsonObject) -> bool:
    if any(existing.get(field) != definition[field] for field in ("name", "enabled", "order")):
        return False
    return not _trigger_problems(existing.get("triggers")) and not _action_problems(
        existing.get("actions"),
        storage_path_id=definition["actions"][0]["assign_storage_path"],
    )


def _trigger_problems(triggers: Any) -> list[str]:
    if not isinstance(triggers, list) or len(triggers) != 1 or not isinstance(triggers[0], dict):
        return ["expected exactly one Document Added trigger"]
    trigger = triggers[0]
    problems: list[str] = []
    if trigger.get("type") != 2:
        problems.append("trigger is not Document Added")
    if trigger.get("matching_algorithm") != 0:
        problems.append("trigger matching_algorithm is not none")
    if trigger.get("match") != "":
        problems.append("trigger match is not empty")
    if trigger.get("is_insensitive") is not True:
        problems.append("trigger is_insensitive is not true")
    for key, value in trigger.items():
        if key.startswith("filter_") and value is not None and value != "" and value != []:
            problems.append(f"trigger has filter {key}")
    return problems


def _action_problems(actions: Any, *, storage_path_id: int) -> list[str]:
    if not isinstance(actions, list) or len(actions) != 1 or not isinstance(actions[0], dict):
        return ["expected exactly one Assignment action"]
    action = actions[0]
    problems: list[str] = []
    if action.get("type") != 1:
        problems.append("action is not Assignment")
    if action.get("assign_storage_path") != storage_path_id:
        problems.append(f"action does not assign storage path {storage_path_id}")
    for key, value in action.items():
        if key in {"id", "type", "assign_storage_path"}:
            continue
        if value is not None and value is not False and value != "" and value != [] and value != {}:
            problems.append(f"action has additional setting {key}")
    return problems
