from paperless_ngx_mcp.organization import (
    DOCUMENT_COUNT_FILTERS,
    ORGANIZATION_OBJECT_TYPES,
    enrich_organization_item,
    summarize_organization,
)


def test_enrich_organization_item_preserves_input_and_labels_matching_algorithm() -> None:
    item = {"id": 4, "name": "Invoice", "matching_algorithm": 99}

    assert enrich_organization_item(item) == {
        **item,
        "matching_algorithm_label": "unknown_99",
    }
    assert item == {"id": 4, "name": "Invoice", "matching_algorithm": 99}


def test_organization_analysis_constants_describe_the_client_input_contract() -> None:
    assert {
        "tags",
        "correspondents",
        "document_types",
        "storage_paths",
        "custom_fields",
        "saved_views",
        "workflows",
    } == ORGANIZATION_OBJECT_TYPES
    assert DOCUMENT_COUNT_FILTERS["total"] is None
    assert DOCUMENT_COUNT_FILTERS["without_tags"] == ("is_tagged", False)


def test_summarize_organization_keeps_compact_analysis_shape() -> None:
    result = summarize_organization(
        {
            "tags": [
                {"id": 1, "name": "Tax", "document_count": 0, "is_inbox_tag": True},
                {"id": 2, "name": " tax! ", "document_count": 1, "parent": 1},
            ],
            "custom_fields": [
                {"id": 3, "name": "Amount", "document_count": 2, "data_type": "float"},
                {"id": 4, "name": "Paid", "document_count": 2, "data_type": "boolean"},
            ],
            "saved_views": [{"id": 5, "name": "Inbox", "show_on_dashboard": True}],
        },
        {
            "total": 12,
            "without_correspondent": 1,
            "without_document_type": 2,
            "without_storage_path": 3,
            "without_tags": 4,
            "without_custom_fields": 5,
            "without_archive_serial_number": 6,
        },
        sample_size=1,
    )

    assert result["documents"] == {
        "total": 12,
        "missing_assignments": {
            "correspondent": 1,
            "document_type": 2,
            "storage_path": 3,
            "tags": 4,
            "custom_fields": 5,
            "archive_serial_number": 6,
        },
    }
    assert result["organization"]["tags"] == {
        "total": 2,
        "normalized_duplicate_groups": [
            {
                "normalized_name": "tax",
                "items": [
                    {"id": 1, "name": "Tax", "document_count": 0},
                    {"id": 2, "name": " tax! ", "document_count": 1, "parent": 1},
                ],
            }
        ],
        "used": 1,
        "unused": 1,
        "single_document": 1,
        "unused_examples": [{"id": 1, "name": "Tax", "document_count": 0}],
        "single_document_examples": [{"id": 2, "name": " tax! ", "document_count": 1, "parent": 1}],
        "most_used": [{"id": 2, "name": " tax! ", "document_count": 1, "parent": 1}],
        "inbox_tags": 1,
        "root_tags": 1,
        "nested_tags": 1,
    }
    assert result["organization"]["custom_fields"]["data_types"] == {
        "boolean": 1,
        "float": 1,
    }
    assert result["organization"]["saved_views"] == {
        "total": 1,
        "normalized_duplicate_groups": [],
        "shown_on_dashboard": 1,
        "shown_in_sidebar": 0,
    }
