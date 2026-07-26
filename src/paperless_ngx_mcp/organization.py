"""Helpers for compact, AI-friendly Paperless organization summaries."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict

from paperless_ngx_mcp.client import JsonObject

MATCHING_ALGORITHMS = {
    0: "none",
    1: "any_word",
    2: "all_words",
    3: "exact_match",
    4: "regular_expression",
    5: "fuzzy_word",
    6: "automatic",
}

COUNTED_OBJECT_TYPES = {
    "tags",
    "correspondents",
    "document_types",
    "storage_paths",
    "custom_fields",
}


def enrich_organization_item(item: JsonObject) -> JsonObject:
    """Add human-readable values without removing Paperless API fields."""
    enriched = dict(item)
    algorithm = enriched.get("matching_algorithm")
    if isinstance(algorithm, int):
        enriched["matching_algorithm_label"] = MATCHING_ALGORITHMS.get(
            algorithm, f"unknown_{algorithm}"
        )
    return enriched


def summarize_organization(
    objects: dict[str, list[JsonObject]],
    document_counts: dict[str, int],
    *,
    sample_size: int,
) -> JsonObject:
    """Create a compact taxonomy and assignment health report."""
    summaries: dict[str, JsonObject] = {}
    for object_type, items in objects.items():
        summaries[object_type] = _summarize_object_type(
            object_type,
            items,
            sample_size=sample_size,
        )

    return {
        "documents": {
            "total": document_counts["total"],
            "missing_assignments": {
                "correspondent": document_counts["without_correspondent"],
                "document_type": document_counts["without_document_type"],
                "storage_path": document_counts["without_storage_path"],
                "tags": document_counts["without_tags"],
                "custom_fields": document_counts["without_custom_fields"],
                "archive_serial_number": document_counts["without_archive_serial_number"],
            },
        },
        "organization": summaries,
        "interpretation_notes": [
            (
                "Unused and single-document entries are review candidates, "
                "not automatic deletion candidates."
            ),
            (
                "Normalized duplicate groups differ only by case, spacing, "
                "punctuation, or Unicode form."
            ),
            (
                "Document counts can overlap because one document may have "
                "multiple tags or custom fields."
            ),
            "Use list_metadata to inspect complete records before recommending structural changes.",
        ],
    }


def _summarize_object_type(
    object_type: str,
    items: list[JsonObject],
    *,
    sample_size: int,
) -> JsonObject:
    result: JsonObject = {
        "total": len(items),
        "normalized_duplicate_groups": _duplicate_name_groups(items, sample_size),
    }

    if object_type in COUNTED_OBJECT_TYPES:
        counted = [item for item in items if isinstance(item.get("document_count"), int)]
        unused = [item for item in counted if item["document_count"] == 0]
        single_use = [item for item in counted if item["document_count"] == 1]
        most_used = sorted(
            counted,
            key=lambda item: (-int(item["document_count"]), _item_name(item).casefold()),
        )
        result.update(
            {
                "used": len(counted) - len(unused),
                "unused": len(unused),
                "single_document": len(single_use),
                "unused_examples": [_compact_item(item) for item in unused[:sample_size]],
                "single_document_examples": [
                    _compact_item(item) for item in single_use[:sample_size]
                ],
                "most_used": [_compact_item(item) for item in most_used[:sample_size]],
            }
        )

    algorithms = Counter(
        MATCHING_ALGORITHMS.get(value, f"unknown_{value}")
        for item in items
        if isinstance((value := item.get("matching_algorithm")), int)
    )
    if algorithms:
        result["matching_algorithms"] = dict(sorted(algorithms.items()))
        result["with_nonempty_match_rule"] = sum(
            1 for item in items if isinstance(item.get("match"), str) and item["match"].strip()
        )

    if object_type == "tags":
        result["inbox_tags"] = sum(item.get("is_inbox_tag") is True for item in items)
        result["root_tags"] = sum(item.get("parent") is None for item in items)
        result["nested_tags"] = sum(item.get("parent") is not None for item in items)
    elif object_type == "custom_fields":
        data_types = Counter(
            str(item["data_type"]) for item in items if item.get("data_type") is not None
        )
        result["data_types"] = dict(sorted(data_types.items()))
    elif object_type == "saved_views":
        result["shown_on_dashboard"] = sum(item.get("show_on_dashboard") is True for item in items)
        result["shown_in_sidebar"] = sum(item.get("show_in_sidebar") is True for item in items)

    return result


def _duplicate_name_groups(items: list[JsonObject], sample_size: int) -> list[JsonObject]:
    grouped: defaultdict[str, list[JsonObject]] = defaultdict(list)
    for item in items:
        name = item.get("name")
        if isinstance(name, str) and name.strip():
            grouped[_normalize_name(name)].append(item)

    duplicates = [
        {
            "normalized_name": normalized,
            "items": [_compact_item(item) for item in group],
        }
        for normalized, group in grouped.items()
        if len(group) > 1
    ]
    duplicates.sort(key=lambda group: str(group["normalized_name"]))
    return duplicates[:sample_size]


def _normalize_name(name: str) -> str:
    normalized = unicodedata.normalize("NFKC", name).casefold()
    return re.sub(r"[\W_]+", " ", normalized, flags=re.UNICODE).strip()


def _item_name(item: JsonObject) -> str:
    name = item.get("name")
    return name if isinstance(name, str) else ""


def _compact_item(item: JsonObject) -> JsonObject:
    fields = ("id", "name", "document_count", "path", "data_type", "parent")
    return {field: item[field] for field in fields if field in item}
