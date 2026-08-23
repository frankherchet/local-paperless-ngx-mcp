"""Reference-safe previews for Paperless metadata deletion."""

from __future__ import annotations

import json
from typing import Any, TypeGuard

from paperless_ngx_mcp.mutation_policy import DELETABLE_ORGANIZATION_OBJECT_TYPES

JsonObject = dict[str, Any]

_WORKFLOW_FIELDS: dict[str, tuple[tuple[str, bool], ...]] = {
    "tags": (
        ("filter_has_tags", True),
        ("filter_has_all_tags", True),
        ("filter_has_not_tags", True),
        ("assign_tags", True),
        ("remove_tags", True),
    ),
    "correspondents": (
        ("filter_has_correspondent", False),
        ("filter_has_not_correspondents", True),
        ("filter_has_any_correspondents", True),
        ("assign_correspondent", False),
        ("remove_correspondents", True),
    ),
    "document_types": (
        ("filter_has_document_type", False),
        ("filter_has_not_document_types", True),
        ("filter_has_any_document_types", True),
        ("assign_document_type", False),
        ("remove_document_types", True),
    ),
    "storage_paths": (
        ("filter_has_storage_path", False),
        ("filter_has_not_storage_paths", True),
        ("filter_has_any_storage_paths", True),
        ("assign_storage_path", False),
        ("remove_storage_paths", True),
    ),
}
_MAIL_RULE_FIELDS: dict[str, tuple[tuple[str, bool], ...]] = {
    "tags": (("assign_tags", True),),
    "correspondents": (("assign_correspondent", False),),
    "document_types": (("assign_document_type", False),),
    "storage_paths": (("assign_storage_path", False),),
}
_SAVED_VIEW_RULE_TYPES = {
    "tags": {6, 7, 17, 22},
    "correspondents": {3, 26, 27},
    "document_types": {4, 28, 29},
    "storage_paths": {25, 30, 31},
}


def preview_metadata_deletion(
    object_type: str,
    items: list[JsonObject],
    workflows: list[JsonObject],
    mail_rules: list[JsonObject],
    saved_views: list[JsonObject],
    item_ids: list[int] | None,
) -> JsonObject:
    """Return the existing deletion-preview shape from already fetched records."""
    if object_type not in DELETABLE_ORGANIZATION_OBJECT_TYPES:
        raise ValueError(f"Unsupported deletable organization object type: {object_type}")

    selected_ids = set(item_ids) if item_ids is not None else None
    items_by_id: dict[int, JsonObject] = {}
    for item in items:
        item_id: object = item.get("id")
        if _is_positive_int(item_id) and (selected_ids is None or item_id in selected_ids):
            items_by_id[item_id] = item
    child_tag_ids: dict[int, list[int]] = {}
    if object_type == "tags":
        for item in items:
            tag_id: object = item.get("id")
            parent_id: object = item.get("parent")
            if _is_positive_int(tag_id) and _is_positive_int(parent_id):
                child_tag_ids.setdefault(parent_id, []).append(tag_id)

    candidates: list[JsonObject] = []
    blocked: list[JsonObject] = []
    referenced_items_omitted = 0
    for item_id in sorted(items_by_id):
        item = items_by_id[item_id]
        document_count = item.get("document_count")
        children = sorted(child_tag_ids.get(item_id, []))
        if selected_ids is None and _is_nonnegative_int(document_count) and document_count > 0:
            referenced_items_omitted += 1
            continue

        reasons: list[str] = []
        if not _is_nonnegative_int(document_count):
            reasons.append("document_count_unavailable")
        elif document_count != 0:
            reasons.append("referenced_by_documents")
        if children:
            reasons.append("parent_of_tags")
        workflow_references = _workflow_references(workflows, object_type, item_id)
        if workflow_references:
            reasons.append("referenced_by_workflows")
        mail_rule_references = _mail_rule_references(mail_rules, object_type, item_id)
        if mail_rule_references:
            reasons.append("referenced_by_mail_rules")
        saved_view_references = _saved_view_references(saved_views, object_type, item_id)
        if saved_view_references:
            reasons.append("referenced_by_saved_views")

        assessment: JsonObject = {
            "id": item_id,
            "name": item.get("name"),
            "document_count": document_count,
            "child_tag_ids": children,
            "deletable": not reasons,
            "blocking_reasons": reasons,
            "workflow_references": workflow_references,
            "mail_rule_references": mail_rule_references,
            "saved_view_references": saved_view_references,
        }
        if object_type == "storage_paths":
            assessment["path"] = item.get("path")
        (candidates if not reasons else blocked).append(assessment)

    missing_ids = sorted(selected_ids - set(items_by_id)) if selected_ids is not None else []
    return {
        "object_type": object_type,
        "requested_ids": sorted(selected_ids) if selected_ids is not None else None,
        "candidates": candidates,
        "blocked": blocked,
        "missing_ids": missing_ids,
        "candidate_count": len(candidates),
        "blocked_count": len(blocked),
        "scanned_count": len(items),
        "referenced_items_omitted": referenced_items_omitted,
        "reference_checks": [
            "document_count",
            "workflow_triggers",
            "workflow_actions",
            "mail_rules",
            "saved_views",
        ]
        + (["child_tag_relationships"] if object_type == "tags" else []),
        "requires_explicit_user_approval": True,
    }


def _workflow_references(
    workflows: list[JsonObject], object_type: str, item_id: int
) -> list[JsonObject]:
    references: list[JsonObject] = []
    fields = _WORKFLOW_FIELDS[object_type]
    trigger_fields = {field for field, _many in fields if field.startswith("filter_")}
    for workflow in workflows:
        for section_name in ("triggers", "actions"):
            entries = workflow.get(section_name)
            if not isinstance(entries, list):
                raise ValueError(f"Invalid workflow {section_name} reference data")
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("Invalid workflow reference entry")
                for field, many in fields:
                    if (field in trigger_fields) != (section_name == "triggers"):
                        continue
                    if _matches_reference(
                        entry.get(field), item_id, many, f"workflow {workflow.get('id')} {field}"
                    ):
                        references.append(
                            {
                                "workflow_id": workflow.get("id"),
                                "workflow_name": workflow.get("name"),
                                "workflow_enabled": workflow.get("enabled"),
                                "section": section_name,
                                "entry_id": entry.get("id"),
                                "field": field,
                            }
                        )
    return references


def _mail_rule_references(
    mail_rules: list[JsonObject], object_type: str, item_id: int
) -> list[JsonObject]:
    references: list[JsonObject] = []
    for rule in mail_rules:
        for field, many in _MAIL_RULE_FIELDS[object_type]:
            if _matches_reference(
                rule.get(field), item_id, many, f"mail rule {rule.get('id')} {field}"
            ):
                references.append(
                    {
                        "mail_rule_id": rule.get("id"),
                        "mail_rule_name": rule.get("name"),
                        "field": field,
                    }
                )
    return references


def _saved_view_references(
    saved_views: list[JsonObject], object_type: str, item_id: int
) -> list[JsonObject]:
    references: list[JsonObject] = []
    for saved_view in saved_views:
        rules = saved_view.get("filter_rules", [])
        if not isinstance(rules, list) or not all(isinstance(rule, dict) for rule in rules):
            raise ValueError("Invalid saved view filter rule data")
        for rule in rules:
            rule_type = rule.get("rule_type")
            if rule_type in _SAVED_VIEW_RULE_TYPES[object_type] and item_id in _saved_view_ids(
                rule.get("value")
            ):
                references.append(
                    {
                        "saved_view_id": saved_view.get("id"),
                        "saved_view_name": saved_view.get("name"),
                        "rule_type": rule_type,
                    }
                )
    return references


def _matches_reference(value: object, item_id: int, many: bool, context: str) -> bool:
    if value is None:
        return False
    if many:
        if not isinstance(value, list) or not all(
            _is_positive_int(reference_id) for reference_id in value
        ):
            raise ValueError(f"Invalid metadata reference in {context}")
        return item_id in value
    if not _is_positive_int(value):
        raise ValueError(f"Invalid metadata reference in {context}")
    return value == item_id


def _saved_view_ids(value: object) -> set[int]:
    if value is None or value == "":
        return set()
    if isinstance(value, int) and not isinstance(value, bool):
        values: object = [value]
    elif isinstance(value, list):
        values = value
    elif isinstance(value, str):
        try:
            values = json.loads(value)
        except json.JSONDecodeError:
            values = value.split(",")
    else:
        raise ValueError("Invalid saved view metadata reference")
    if isinstance(values, int) and not isinstance(values, bool):
        values = [values]
    if not isinstance(values, list):
        raise ValueError("Invalid saved view metadata reference")
    result: set[int] = set()
    for raw_value in values:
        if isinstance(raw_value, str) and raw_value.isdigit():
            raw_value = int(raw_value)
        if not _is_positive_int(raw_value):
            raise ValueError("Invalid saved view metadata reference")
        result.add(raw_value)
    return result


def _is_positive_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_nonnegative_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
