from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from paperless_ngx_mcp.client import (
    DEFAULT_INTAKE_WORKFLOW_NAME,
    PaperlessApiError,
    PaperlessClient,
    PermanentDeletionDisabled,
    ReadOnlyError,
)
from paperless_ngx_mcp.config import Settings


def make_settings(*, read_only: bool = True) -> Settings:
    return Settings.model_validate(
        {
            "PAPERLESS_URL": "http://paperless.test/paperless",
            "PAPERLESS_TOKEN": "test-token",
            "PAPERLESS_API_VERSION": 10,
            "PAPERLESS_READ_ONLY": read_only,
        }
    )


@pytest.mark.asyncio
async def test_search_documents_sends_auth_and_summarizes_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/paperless/api/documents/"
        assert request.headers["Authorization"] == "Token test-token"
        assert request.headers["Accept"] == "application/json; version=10"
        assert request.url.params["text"] == "invoice"
        return httpx.Response(
            200,
            json={
                "count": 1,
                "results": [
                    {
                        "id": 7,
                        "title": "Invoice",
                        "created": "2026-01-01",
                        "content": "secret OCR text",
                        "tags": [2],
                    }
                ],
            },
        )

    async with PaperlessClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        result = await client.search_documents(
            query="invoice",
            mode="simple",
            page=1,
            page_size=20,
            ordering="-created",
            similar_to_id=None,
        )

    assert result == {
        "count": 1,
        "page": 1,
        "page_size": 20,
        "has_next": False,
        "has_previous": False,
        "results": [
            {
                "id": 7,
                "title": "Invoice",
                "created": "2026-01-01",
                "tags": [2],
            }
        ],
    }


@pytest.mark.asyncio
async def test_empty_simple_search_lists_documents_without_a_text_filter() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "text" not in request.url.params
        return httpx.Response(200, json={"count": 0, "all": [], "results": []})

    async with PaperlessClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        result = await client.search_documents(
            query="",
            mode="simple",
            page=1,
            page_size=20,
            ordering="-created",
            similar_to_id=None,
        )

    assert result["count"] == 0
    assert "all" not in result


@pytest.mark.asyncio
async def test_get_document_truncates_ocr_content() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json={"id": 5, "title": "Long document", "content": "x" * 2_000},
        )
    )

    async with PaperlessClient(make_settings(), transport=transport) as client:
        result = await client.get_document(
            5,
            include_content=True,
            include_file_metadata=False,
            max_content_chars=1_000,
        )

    assert result["content"] == "x" * 1_000
    assert result["content_truncated"] is True
    assert result["content_total_chars"] == 2_000


@pytest.mark.asyncio
async def test_get_document_history_returns_a_compact_audit_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/paperless/api/documents/5/history/"
        assert request.url.params["page"] == "1"
        assert request.url.params["page_size"] == "10"
        return httpx.Response(
            200,
            json={
                "count": 2,
                "next": "http://paperless.test/api/documents/5/history/?page=2",
                "previous": None,
                "results": [
                    {
                        "id": 9,
                        "timestamp": "2026-08-23T10:00:00Z",
                        "action": "Updated",
                        "changes": {
                            "title": ["Old", "New"],
                            "content": ["old OCR", "new OCR text"],
                        },
                        "actor": {"id": 1, "username": "frank"},
                    }
                ],
            },
        )

    async with PaperlessClient(
        make_settings(),
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await client.get_document_history(5)

    assert result["document_id"] == 5
    assert result["count"] == 2
    assert result["has_next"] is True
    assert result["entries"][0]["changes"] == {
        "title": ["Old", "New"],
        "content": {"changed": True, "before_chars": 7, "after_chars": 12},
    }
    assert "old OCR" not in json.dumps(result)


@pytest.mark.asyncio
async def test_get_document_history_full_preserves_legacy_raw_changes() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json=[{"id": 1, "changes": {"content": ["before", "after"]}}],
        )
    )
    async with PaperlessClient(make_settings(), transport=transport) as client:
        result = await client.get_document_history(5, detail="full")

    assert result["entries"][0]["changes"]["content"] == ["before", "after"]


@pytest.mark.asyncio
async def test_compact_history_has_a_bounded_response_for_large_ocr_diffs() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            json=[
                {
                    "id": item,
                    "changes": {"content": ["before" * 10_000, "after" * 10_000]},
                }
                for item in range(20)
            ],
        )
    )
    async with PaperlessClient(make_settings(), transport=transport) as client:
        result = await client.get_document_history(5)

    assert result["count"] == 20
    assert len(result["entries"]) == 10
    assert result["has_next"] is True
    assert len(json.dumps(result)) < 3_000
    assert "beforebefore" not in json.dumps(result)


@pytest.mark.asyncio
async def test_task_queries_return_a_task_and_active_queue() -> None:
    task_id = "3c6e91d1-6b5a-4bb1-aaf7-7cf9fd3aa4f2"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/paperless/api/tasks/":
            assert request.url.params["task_id"] == task_id
            assert request.url.params["page_size"] == "1"
            return httpx.Response(200, json={"results": [{"id": task_id, "status": "SUCCESS"}]})
        assert request.url.path == "/paperless/api/tasks/active/"
        return httpx.Response(200, json=[{"id": task_id, "status": "STARTED"}])

    async with PaperlessClient(
        make_settings(),
        transport=httpx.MockTransport(handler),
    ) as client:
        task = await client.get_task(task_id)
        active = await client.list_active_tasks()

    assert task == {"task_id": task_id, "found": True, "task": {"id": task_id, "status": "SUCCESS"}}
    assert active == {"count": 1, "tasks": [{"id": task_id, "status": "STARTED"}]}


@pytest.mark.asyncio
async def test_task_queries_normalize_legacy_api_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/paperless/api/tasks/"
        return httpx.Response(
            200,
            json=[
                {
                    "id": "task-1",
                    "task_name": "reprocess_document",
                    "status": "FAILURE",
                    "related_document": 42,
                    "result": "failed OCR",
                    "input_data": "must stay hidden",
                }
            ],
        )

    async with PaperlessClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        result = await client.get_task("task-1")

    assert result["task"] == {
        "id": "task-1",
        "task_type": "reprocess_document",
        "status": "FAILURE",
        "related_document_ids": [42],
        "result": "failed OCR",
    }


@pytest.mark.asyncio
async def test_task_overview_uses_native_aggregates_and_compacts_active_tasks() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/status_counts/"):
            return httpx.Response(200, json={"all": 4, "in_progress": 1})
        if request.url.path.endswith("/summary/"):
            assert request.url.params["days"] == "7"
            return httpx.Response(
                200,
                json=[{"task_type": "reprocess_document", "total_count": 4}],
            )
        return httpx.Response(
            200,
            json=[
                {
                    "task_id": "task-1",
                    "task_type": "reprocess_document",
                    "status": "started",
                    "input_data": "large internal input",
                    "related_document_ids": [5],
                }
            ],
        )

    async with PaperlessClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        result = await client.get_task_overview(days=7, include_active=True)

    assert result["status_counts"] == {"all": 4, "in_progress": 1}
    assert result["by_type"] == [{"task_type": "reprocess_document", "total_count": 4}]
    assert result["active"] == [
        {
            "task_id": "task-1",
            "task_type": "reprocess_document",
            "status": "started",
            "related_document_ids": [5],
        }
    ]


@pytest.mark.asyncio
async def test_document_suggestions_reads_both_sources_and_tolerates_missing_ai() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/suggestions/"):
            return httpx.Response(200, json={"tags": [2], "dates": ["2026-08-01"]})
        assert request.url.path.endswith("/ai_suggestions/")
        return httpx.Response(400, json={"detail": "AI suggestions disabled"})

    async with PaperlessClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        result = await client.get_document_suggestions(5, source="both")

    assert result == {
        "document_id": 5,
        "paperless": {"tags": [2], "dates": ["2026-08-01"]},
        "unavailable": ["ai"],
    }


@pytest.mark.asyncio
async def test_archive_statistics_prunes_empty_values_without_dropping_zeroes() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"documents_total": 12, "inbox": 0, "empty": [], "nested": {"unused": None}},
        )
    )
    async with PaperlessClient(make_settings(), transport=transport) as client:
        result = await client.get_archive_statistics()

    assert result == {"documents_total": 12, "inbox": 0}


@pytest.mark.asyncio
async def test_update_is_blocked_in_read_only_mode() -> None:
    async with PaperlessClient(make_settings()) as client:
        with pytest.raises(ReadOnlyError, match="PAPERLESS_READ_ONLY=false"):
            await client.update_document(5, {"title": "Changed"})


@pytest.mark.asyncio
async def test_api_errors_have_a_useful_message() -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(401, json={"detail": "Invalid token."})
    )

    async with PaperlessClient(make_settings(), transport=transport) as client:
        with pytest.raises(PaperlessApiError, match="HTTP 401: Invalid token"):
            await client.get_document(
                1,
                include_content=False,
                include_file_metadata=False,
                max_content_chars=1_000,
            )


@pytest.mark.asyncio
async def test_get_document_can_include_file_checksums() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/metadata/"):
            return httpx.Response(
                200,
                json={
                    "original_checksum": "original-sha256",
                    "archive_checksum": "archive-sha256",
                },
            )
        return httpx.Response(200, json={"id": 5, "title": "Document", "content": "OCR"})

    async with PaperlessClient(
        make_settings(),
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await client.get_document(
            5,
            include_content=False,
            include_file_metadata=True,
            max_content_chars=1_000,
        )

    assert result["file_metadata"] == {
        "original_checksum": "original-sha256",
        "archive_checksum": "archive-sha256",
    }


@pytest.mark.asyncio
async def test_list_objects_removes_all_ids_and_labels_matching_algorithm() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/paperless/api/correspondents/"
        assert request.url.params["name__icontains"] == "Bank"
        return httpx.Response(
            200,
            json={
                "count": 1,
                "next": None,
                "previous": None,
                "all": [8],
                "results": [
                    {
                        "id": 8,
                        "name": "Bank",
                        "document_count": 12,
                        "matching_algorithm": 6,
                    }
                ],
            },
        )

    async with PaperlessClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        result = await client.list_objects(
            "correspondents",
            page=1,
            page_size=100,
            ordering="name",
            name_contains="Bank",
        )

    assert "all" not in result
    assert result["results"][0]["matching_algorithm_label"] == "automatic"


@pytest.mark.asyncio
async def test_list_objects_compacts_mail_rules_and_supports_full_detail_lookup() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/mail_rules/"):
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "results": [
                        {
                            "id": 3,
                            "name": "Invoices",
                            "enabled": True,
                            "filter_subject": "Invoice",
                            "assign_tags": [7],
                            "owner": 1,
                            "user_can_change": True,
                        }
                    ],
                },
            )
        assert request.url.path.endswith("/tags/9/")
        return httpx.Response(200, json={"id": 9, "name": "Tax", "slug": "tax"})

    async with PaperlessClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        rules = await client.list_objects(
            "mail_rules", page=1, page_size=25, ordering="name", detail="compact"
        )
        tag = await client.list_objects(
            "tags", page=1, page_size=25, ordering="name", detail="full", item_id=9
        )

    assert rules["results"] == [
        {
            "id": 3,
            "name": "Invoices",
            "enabled": True,
            "filter_subject": "Invoice",
            "assign_tags": [7],
        }
    ]
    assert tag["results"] == [{"id": 9, "name": "Tax", "slug": "tax"}]


@pytest.mark.asyncio
async def test_find_documents_missing_metadata_uses_document_filter() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/paperless/api/documents/"
        assert request.url.params["correspondent__isnull"] == "true"
        return httpx.Response(
            200,
            json={
                "count": 1,
                "next": None,
                "previous": None,
                "results": [
                    {
                        "id": 42,
                        "title": "Unassigned",
                        "content": "must not be returned",
                    }
                ],
            },
        )

    async with PaperlessClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        result = await client.find_documents_missing_metadata(
            "correspondent",
            page=1,
            page_size=20,
            ordering="-created",
        )

    assert result["missing_field"] == "correspondent"
    assert result["results"] == [{"id": 42, "title": "Unassigned"}]


@pytest.mark.asyncio
async def test_organization_overview_fetches_all_pages_and_assignment_counts() -> None:
    object_results = {
        "correspondents": [{"id": 1, "name": "Bank", "document_count": 10}],
        "custom_fields": [{"id": 2, "name": "Account", "document_count": 5, "data_type": "string"}],
        "document_types": [{"id": 3, "name": "Invoice", "document_count": 8}],
        "mail_rules": [{"id": 9, "name": "Invoices", "enabled": True}],
        "saved_views": [
            {
                "id": 4,
                "name": "Inbox",
                "show_on_dashboard": True,
                "show_in_sidebar": True,
            }
        ],
        "storage_paths": [{"id": 5, "name": "Archive", "document_count": 20}],
        "workflows": [
            {
                "id": 8,
                "name": "Inbox workflow",
                "enabled": True,
                "triggers": [],
                "actions": [],
            }
        ],
    }
    missing_counts = {
        "correspondent__isnull": 3,
        "document_type__isnull": 4,
        "storage_path__isnull": 5,
        "is_tagged": 6,
        "has_custom_fields": 7,
        "archive_serial_number__isnull": 8,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/paperless/api/documents/":
            for parameter, count in missing_counts.items():
                if parameter in request.url.params:
                    return httpx.Response(200, json={"count": count, "results": []})
            return httpx.Response(200, json={"count": 20, "results": []})

        object_type = request.url.path.removeprefix("/paperless/api/").strip("/")
        if object_type == "tags":
            page = int(request.url.params["page"])
            if page == 1:
                return httpx.Response(
                    200,
                    json={
                        "count": 2,
                        "next": "http://paperless.test/api/tags/?page=2",
                        "results": [{"id": 6, "name": "Tax", "document_count": 0}],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "count": 2,
                    "next": None,
                    "results": [{"id": 7, "name": " tax! ", "document_count": 1}],
                },
            )
        return httpx.Response(
            200,
            json={
                "count": 1,
                "next": None,
                "results": object_results[object_type],
            },
        )

    async with PaperlessClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        result = await client.get_organization_overview(sample_size=10)

    assert result["documents"]["total"] == 20
    assert result["documents"]["missing_assignments"] == {
        "correspondent": 3,
        "document_type": 4,
        "storage_path": 5,
        "tags": 6,
        "custom_fields": 7,
        "archive_serial_number": 8,
    }
    assert result["organization"]["tags"]["total"] == 2
    assert result["organization"]["tags"]["unused"] == 1
    assert result["organization"]["tags"]["single_document"] == 1
    assert (
        result["organization"]["tags"]["normalized_duplicate_groups"][0]["normalized_name"] == "tax"
    )


@pytest.mark.asyncio
async def test_organization_overview_survives_unavailable_mail_rules() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/paperless/api/documents/":
            return httpx.Response(200, json={"count": 0, "results": []})
        if request.url.path == "/paperless/api/mail_rules/":
            return httpx.Response(403, json={"detail": "Forbidden"})
        return httpx.Response(200, json={"count": 0, "next": None, "results": []})

    async with PaperlessClient(make_settings(), transport=httpx.MockTransport(handler)) as client:
        result = await client.get_organization_overview(sample_size=1)

    assert result["unavailable"] == ["mail_rules"]
    assert "mail_rules" not in result["organization"]


@pytest.mark.asyncio
async def test_permanent_document_deletion_is_blocked_before_network() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("Deletion guard must run before the network request")

    async with PaperlessClient(
        make_settings(read_only=False),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(PermanentDeletionDisabled, match="HTTP DELETE is disabled"):
            await client.request("DELETE", "api/documents/42/")

        with pytest.raises(PermanentDeletionDisabled, match="Emptying trash"):
            await client.request(
                "POST",
                "api/trash/",
                json={"documents": [42], "action": "empty"},
            )

        with pytest.raises(PermanentDeletionDisabled, match="method is not allowed: merge"):
            await client.request(
                "POST",
                "api/documents/bulk_edit/",
                json={"documents": [42], "method": "merge", "parameters": {}},
            )

        with pytest.raises(PermanentDeletionDisabled, match="Object bulk editing is restricted"):
            await client.request(
                "POST",
                "api/bulk_edit_objects/",
                json={
                    "objects": [42],
                    "object_type": "custom_fields",
                    "operation": "delete",
                },
            )


@pytest.mark.asyncio
async def test_raw_write_requests_respect_read_only_and_workflow_preview_guards() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("Mutation guard must run before the network request")

    async with PaperlessClient(
        make_settings(),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(ReadOnlyError, match="PAPERLESS_READ_ONLY=false"):
            await client.request("POST", "api/tags/", json={"name": "Nope"})
        with pytest.raises(ReadOnlyError, match="PAPERLESS_READ_ONLY=false"):
            await client.request("PATCH", "api/tags/1/", json={"name": "Nope"})

    async with PaperlessClient(
        make_settings(read_only=False),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(PermanentDeletionDisabled, match="must use delete_workflow"):
            await client.request("DELETE", "api/workflows/42/")
        with pytest.raises(PermanentDeletionDisabled, match="must use bulk_edit_objects"):
            await client.request(
                "POST",
                "api/bulk_edit_objects/",
                json={"objects": [42], "object_type": "tags", "operation": "delete"},
            )


@pytest.mark.asyncio
async def test_document_organization_writes_use_allowlisted_bulk_methods() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"result": "OK"})

    async with PaperlessClient(
        make_settings(read_only=False),
        transport=httpx.MockTransport(handler),
    ) as client:
        await client.set_document_metadata_field([10, 11], "correspondent", 7)
        await client.modify_document_tags(
            [10, 11],
            add_tag_ids=[2],
            remove_tag_ids=[3],
        )
        await client.move_documents_to_trash([12])
        await client.reprocess_documents([13])

    assert requests == [
        {
            "documents": [10, 11],
            "method": "set_correspondent",
            "parameters": {"correspondent": 7},
        },
        {
            "documents": [10, 11],
            "method": "modify_tags",
            "parameters": {"add_tags": [2], "remove_tags": [3]},
        },
        {"documents": [12], "method": "delete", "parameters": {}},
        {"documents": [13], "method": "reprocess", "parameters": {}},
    ]


@pytest.mark.asyncio
async def test_restore_is_the_only_allowed_trash_action() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/paperless/api/trash/"
        assert json.loads(request.content) == {"documents": [42], "action": "restore"}
        return httpx.Response(200, json={"result": "OK"})

    async with PaperlessClient(
        make_settings(read_only=False),
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await client.restore_documents_from_trash([42])

    assert result == {"result": "OK"}


@pytest.mark.asyncio
async def test_create_and_update_organization_items_use_no_delete_method() -> None:
    seen: list[tuple[str, str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"id": 8, "name": "Finance"})

    async with PaperlessClient(
        make_settings(read_only=False),
        transport=httpx.MockTransport(handler),
    ) as client:
        await client.create_organization_item("tags", {"name": "Finance"})
        await client.update_organization_item("tags", 8, {"name": "Finances"})

    assert seen == [
        ("POST", "/paperless/api/tags/", {"name": "Finance"}),
        ("PATCH", "/paperless/api/tags/8/", {"name": "Finances"}),
    ]


@pytest.mark.asyncio
async def test_bulk_edit_objects_dry_run_checks_documents_and_child_tags() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        if request.url.path == "/paperless/api/workflows/":
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "next": None,
                    "results": [
                        {
                            "id": 20,
                            "name": "Assign tag",
                            "enabled": True,
                            "triggers": [],
                            "actions": [{"id": 21, "assign_tags": [3]}],
                        }
                    ],
                },
            )
        if request.url.path in {
            "/paperless/api/mail_rules/",
            "/paperless/api/saved_views/",
        }:
            return httpx.Response(200, json={"count": 0, "next": None, "results": []})
        assert request.url.path == "/paperless/api/tags/"
        return httpx.Response(
            200,
            json={
                "count": 4,
                "next": None,
                "results": [
                    {"id": 1, "name": "Parent", "document_count": 0, "parent": None},
                    {"id": 2, "name": "Used", "document_count": 4, "parent": None},
                    {"id": 3, "name": "Leaf", "document_count": 0, "parent": 1},
                    {"id": 4, "name": "Unused", "document_count": 0, "parent": None},
                ],
            },
        )

    async with PaperlessClient(
        make_settings(),
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await client.bulk_edit_objects(
            "tags",
            [1, 2, 3, 4, 99],
            "delete",
            dry_run=True,
        )

    preview = result["preflight"]
    assert [item["id"] for item in preview["candidates"]] == [4]
    assert [item["id"] for item in preview["blocked"]] == [1, 2, 3]
    assert preview["blocked"][0]["blocking_reasons"] == ["parent_of_tags"]
    assert preview["blocked"][1]["blocking_reasons"] == ["referenced_by_documents"]
    assert preview["blocked"][2]["blocking_reasons"] == ["referenced_by_workflows"]
    assert preview["blocked"][2]["workflow_references"][0]["workflow_id"] == 20
    assert preview["blocked"][2]["workflow_references"][0]["field"] == "assign_tags"
    assert preview["missing_ids"] == [99]
    assert preview["requires_explicit_user_approval"] is True
    assert result["dry_run"] is True
    assert result["deletion_submitted"] is False


@pytest.mark.asyncio
async def test_bulk_edit_objects_rechecks_then_uses_matching_rest_endpoint() -> None:
    seen: list[tuple[str, str, dict[str, object] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        if request.method == "GET":
            if request.url.path == "/paperless/api/workflows/":
                return httpx.Response(
                    200,
                    json={"count": 0, "next": None, "results": []},
                )
            if request.url.path in {
                "/paperless/api/mail_rules/",
                "/paperless/api/saved_views/",
            }:
                return httpx.Response(200, json={"count": 0, "next": None, "results": []})
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "next": None,
                    "results": [
                        {"id": 12, "name": "Unused sender", "document_count": 0},
                    ],
                },
            )
        return httpx.Response(200, json={"result": "OK"})

    async with PaperlessClient(
        make_settings(read_only=False),
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await client.bulk_edit_objects(
            "correspondents",
            [12],
            "delete",
            dry_run=False,
        )

    assert seen == [
        ("GET", "/paperless/api/correspondents/", None),
        ("GET", "/paperless/api/workflows/", None),
        ("GET", "/paperless/api/mail_rules/", None),
        ("GET", "/paperless/api/saved_views/", None),
        (
            "POST",
            "/paperless/api/bulk_edit_objects/",
            {
                "objects": [12],
                "object_type": "correspondents",
                "operation": "delete",
            },
        ),
    ]
    assert result["deletion_submitted"] is True
    assert result["preflight"]["candidate_count"] == 1


@pytest.mark.asyncio
async def test_delete_used_organization_item_is_blocked_before_mutation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            raise AssertionError("Referenced item must not be deleted")
        if request.url.path == "/paperless/api/workflows/":
            return httpx.Response(200, json={"count": 0, "next": None, "results": []})
        return httpx.Response(
            200,
            json={
                "count": 1,
                "next": None,
                "results": [{"id": 7, "name": "Used type", "document_count": 2}],
            },
        )

    async with PaperlessClient(
        make_settings(read_only=False),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(ValueError, match="referenced_by_documents"):
            await client.bulk_edit_objects(
                "document_types",
                [7],
                "delete",
                dry_run=False,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("object_type", "workflow_field"),
    [
        ("correspondents", "filter_has_any_correspondents"),
        ("document_types", "filter_has_any_document_types"),
        ("storage_paths", "filter_has_any_storage_paths"),
    ],
)
async def test_v10_workflow_any_filters_block_metadata_deletion(
    object_type: str,
    workflow_field: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            raise AssertionError("Referenced metadata must not be deleted")
        if request.url.path == f"/paperless/api/{object_type}/":
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "next": None,
                    "results": [{"id": 11, "name": "Referenced", "document_count": 0}],
                },
            )
        if request.url.path == "/paperless/api/workflows/":
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "next": None,
                    "results": [
                        {
                            "id": 77,
                            "name": "v10 any filter",
                            "triggers": [{workflow_field: [11]}],
                            "actions": [],
                        }
                    ],
                },
            )
        return httpx.Response(200, json={"count": 0, "next": None, "results": []})

    async with PaperlessClient(
        make_settings(read_only=False),
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await client.bulk_edit_objects(object_type, [11], "delete", dry_run=True)

    blocked = result["preflight"]["blocked"]
    assert blocked[0]["blocking_reasons"] == ["referenced_by_workflows"]
    assert blocked[0]["workflow_references"][0]["field"] == workflow_field


@pytest.mark.asyncio
async def test_mail_rule_and_saved_view_references_block_metadata_deletion() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            raise AssertionError("Referenced metadata must not be deleted")
        if request.url.path == "/paperless/api/tags/":
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "next": None,
                    "results": [{"id": 8, "name": "Protected", "document_count": 0}],
                },
            )
        if request.url.path == "/paperless/api/mail_rules/":
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "next": None,
                    "results": [{"id": 3, "name": "Mail tag", "assign_tags": [8]}],
                },
            )
        if request.url.path == "/paperless/api/saved_views/":
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "next": None,
                    "results": [
                        {
                            "id": 5,
                            "name": "Tag view",
                            "filter_rules": [{"rule_type": 6, "value": "[8]"}],
                        }
                    ],
                },
            )
        return httpx.Response(200, json={"count": 0, "next": None, "results": []})

    async with PaperlessClient(
        make_settings(read_only=False),
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await client.bulk_edit_objects("tags", [8], "delete", dry_run=True)

    blocked = result["preflight"]["blocked"]
    assert blocked[0]["blocking_reasons"] == [
        "referenced_by_mail_rules",
        "referenced_by_saved_views",
    ]
    assert blocked[0]["mail_rule_references"][0]["mail_rule_id"] == 3
    assert blocked[0]["saved_view_references"][0]["saved_view_id"] == 5


@pytest.mark.asyncio
async def test_document_notes_maps_list_and_create_to_rest_subresource() -> None:
    seen: list[tuple[str, str, dict[str, object] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        return httpx.Response(200, json={"count": 1, "results": [{"id": 9, "note": "Reason"}]})

    async with PaperlessClient(
        make_settings(read_only=False),
        transport=httpx.MockTransport(handler),
    ) as client:
        await client.document_notes(5, "list", note=None, page=1, page_size=20)
        await client.document_notes(
            5,
            "create",
            note="Moved to trash; restore by removing tag X.",
            page=1,
            page_size=20,
        )

    assert seen == [
        ("GET", "/paperless/api/documents/5/notes/", None),
        (
            "POST",
            "/paperless/api/documents/5/notes/",
            {"note": "Moved to trash; restore by removing tag X."},
        ),
    ]


@pytest.mark.asyncio
async def test_workflow_crud_and_delete_dry_run_use_only_workflow_endpoints() -> None:
    seen: list[tuple[str, str, dict[str, object] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(
            200,
            json={"id": 9, "name": "Incoming", "triggers": [], "actions": []},
        )

    async with PaperlessClient(
        make_settings(read_only=False),
        transport=httpx.MockTransport(handler),
    ) as client:
        await client.create_workflow(
            {"name": "Incoming", "triggers": [{"type": 2}], "actions": [{"type": 1}]}
        )
        await client.update_workflow(9, {"enabled": False})
        preview = await client.delete_workflow(9, dry_run=True)
        deleted = await client.delete_workflow(9, dry_run=False)

    assert preview["deleted"] is False
    assert deleted["deleted"] is True
    assert seen == [
        (
            "POST",
            "/paperless/api/workflows/",
            {"name": "Incoming", "triggers": [{"type": 2}], "actions": [{"type": 1}]},
        ),
        ("PATCH", "/paperless/api/workflows/9/", {"enabled": False}),
        ("GET", "/paperless/api/workflows/9/", None),
        ("GET", "/paperless/api/workflows/9/", None),
        ("DELETE", "/paperless/api/workflows/9/", None),
    ]


@pytest.mark.asyncio
async def test_configure_default_intake_dry_run_creates_only_a_plan() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "GET":
            raise AssertionError("Dry run must not create or update a workflow")
        if request.url.path == "/paperless/api/storage_paths/":
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "next": None,
                    "results": [{"id": 18, "name": "00 Eingang/Zu prüfen"}],
                },
            )
        assert request.url.path == "/paperless/api/workflows/"
        return httpx.Response(
            200,
            json={
                "count": 1,
                "next": None,
                "results": [
                    {
                        "id": 4,
                        "name": "Existing storage assignment",
                        "order": 1_500,
                        "enabled": True,
                        "triggers": [],
                        "actions": [{"type": 1, "assign_storage_path": 2}],
                    }
                ],
            },
        )

    async with PaperlessClient(
        make_settings(),
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await client.configure_default_intake(18, dry_run=True, enabled=True)

    assert result["changed"] is True
    assert result["workflow_id"] is None
    assert result["planned_operation"] == "create"
    assert result["planned_definition"]["order"] == 1_501
    assert result["planned_definition"]["triggers"][0] == {
        "type": 2,
        "matching_algorithm": 0,
        "match": "",
        "is_insensitive": True,
        "filter_path": None,
        "filter_filename": None,
        "filter_mailrule": None,
    }
    assert result["storage_path"] == {"id": 18, "name": "00 Eingang/Zu prüfen"}


@pytest.mark.asyncio
async def test_configure_default_intake_updates_only_reserved_workflow() -> None:
    seen: list[tuple[str, str, dict[str, object] | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        if request.url.path == "/paperless/api/storage_paths/":
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "next": None,
                    "results": [{"id": 18, "name": "00 Eingang/Zu prüfen"}],
                },
            )
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "next": None,
                    "results": [
                        {
                            "id": 12,
                            "name": DEFAULT_INTAKE_WORKFLOW_NAME,
                            "order": 1,
                            "enabled": False,
                            "triggers": [{"type": 1}],
                            "actions": [{"type": 1, "assign_storage_path": 2}],
                        }
                    ],
                },
            )
        return httpx.Response(200, json={"id": 12, "name": DEFAULT_INTAKE_WORKFLOW_NAME})

    async with PaperlessClient(
        make_settings(read_only=False),
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await client.configure_default_intake(18, dry_run=False, enabled=True)

    assert result["changed"] is True
    assert result["workflow_id"] == 12
    patch_request = next(request for request in seen if request[0] == "PATCH")
    assert patch_request[1] == "/paperless/api/workflows/12/"
    assert patch_request[2] == result["planned_definition"]


@pytest.mark.asyncio
async def test_verify_default_intake_detects_filters_and_reports_valid_definition() -> None:
    workflow: dict[str, Any] = {
        "id": 7,
        "name": DEFAULT_INTAKE_WORKFLOW_NAME,
        "order": 1000,
        "enabled": True,
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
        "actions": [{"type": 1, "assign_storage_path": 18}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/paperless/api/storage_paths/":
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "next": None,
                    "results": [{"id": 18, "name": "00 Eingang/Zu prüfen"}],
                },
            )
        return httpx.Response(200, json={"count": 1, "next": None, "results": [workflow]})

    async with PaperlessClient(
        make_settings(),
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await client.verify_default_intake()

    assert result["valid"] is True
    assert result["trigger"] == "document_added"
    workflow["triggers"][0]["filter_path"] = "mail/*"
    async with PaperlessClient(
        make_settings(),
        transport=httpx.MockTransport(handler),
    ) as client:
        invalid = await client.verify_default_intake()
    assert invalid["valid"] is False
    assert "trigger has filter filter_path" in invalid["problems"]
