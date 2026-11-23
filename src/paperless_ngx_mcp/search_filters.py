"""Typed, read-only document filters mapped to Paperless django-filter parameters.

Parameter spellings verified against paperless-ngx v3.5.1 ``documents/filters.py``:
``created__gte``/``created__lte`` (inclusive; ``Document.created`` is a DateField),
``tags__id__all`` (document has every ID), ``tags__id__none`` (document has none),
and ``correspondent__id``/``document_type__id``/``storage_path__id`` (exact ID match).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

QueryValue = str | int | bool


def _positive_id(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must contain only positive integers")
    return value


def _unique_ids(name: str, values: Sequence[int] | None) -> list[int]:
    if not values:
        return []
    return list(dict.fromkeys(_positive_id(name, value) for value in values))


def document_filter_params(
    *,
    created_from: date | None = None,
    created_to: date | None = None,
    tag_ids: Sequence[int] | None = None,
    exclude_tag_ids: Sequence[int] | None = None,
    correspondent_id: int | None = None,
    document_type_id: int | None = None,
    storage_path_id: int | None = None,
) -> dict[str, QueryValue]:
    """Validate filters and return Paperless query parameters; empty when none are set."""
    if created_from is not None and created_to is not None and created_from > created_to:
        raise ValueError("created_from must not be after created_to")
    required = _unique_ids("tag_ids", tag_ids)
    excluded = _unique_ids("exclude_tag_ids", exclude_tag_ids)
    if overlap := sorted(set(required) & set(excluded)):
        raise ValueError(f"tag_ids and exclude_tag_ids overlap: {overlap}")

    params: dict[str, QueryValue] = {}
    if created_from is not None:
        params["created__gte"] = created_from.isoformat()
    if created_to is not None:
        params["created__lte"] = created_to.isoformat()
    if required:
        params["tags__id__all"] = ",".join(map(str, required))
    if excluded:
        params["tags__id__none"] = ",".join(map(str, excluded))
    for name, key, value in (
        ("correspondent_id", "correspondent__id", correspondent_id),
        ("document_type_id", "document_type__id", document_type_id),
        ("storage_path_id", "storage_path__id", storage_path_id),
    ):
        if value is not None:
            if isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
            params[key] = value
    return params
