from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from paperless_ngx_mcp import import_tracking
from paperless_ngx_mcp.import_tracking import wait_for_document_import
from paperless_ngx_mcp.mutation_policy import PaperlessError


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(import_tracking, "_monotonic", fake.monotonic)
    monkeypatch.setattr(import_tracking, "_sleep", fake.sleep)
    return fake


def found(**task: Any) -> dict[str, Any]:
    return {
        "task_id": "t1",
        "found": True,
        "task": {"task_id": "t1", "task_type": "consume_file", **task},
    }


class getter:
    def __init__(self, *responses: dict[str, Any] | BaseException) -> None:
        self.responses = list(responses)
        self.calls: list[str] = []

    async def __call__(self, task_id: str) -> dict[str, Any]:
        self.calls.append(task_id)
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, BaseException):
            raise response
        return response


async def run(get_task: Any, wait: int = 0) -> dict[str, Any]:
    return await wait_for_document_import("t1", get_task, wait_seconds=wait)


async def test_success_with_result_document_id(clock: Clock) -> None:
    get = getter(found(status="success", result={"document_id": 42}, related_document_ids=[42]))
    assert await run(get) == {
        "task_id": "t1",
        "polls": 1,
        "status": "success",
        "state": "imported",
        "document_id": 42,
    }


async def test_success_legacy_string_exact_only(clock: Clock) -> None:
    ok = getter(found(status="SUCCESS", result="Success. New document id 7 created"))
    assert (await run(ok))["document_id"] == 7
    junk = getter(found(status="success", result="Success. New document id 7 created extra"))
    assert (await run(junk))["state"] == "completed_without_document"


async def test_success_without_document_is_not_imported(clock: Clock) -> None:
    get = getter(found(status="success", result={"reason": "split"}, related_document_ids=[9]))
    out = await run(get)
    assert out["state"] == "completed_without_document"
    assert "document_id" not in out


@pytest.mark.parametrize("bad", [True, 0, -1, "5", None, 1.5])
async def test_invalid_document_id_rejected(clock: Clock, bad: Any) -> None:
    get = getter(found(status="success", result={"document_id": bad}))
    assert (await run(get))["state"] == "completed_without_document"


async def test_related_ids_contradiction_is_unverified(clock: Clock) -> None:
    get = getter(found(status="success", result={"document_id": 1}, related_document_ids=[2]))
    out = await run(get)
    assert out["state"] == "completed_without_document"
    assert "document_id" not in out


async def test_failure_never_yields_document_id(clock: Clock) -> None:
    get = getter(
        found(
            status="failure",
            result={"error": "boom", "document_id": 3},
            related_document_ids=[3],
        )
    )
    out = await run(get)
    assert out["state"] == "failed"
    assert out["message"] == "boom"
    assert "document_id" not in out


async def test_duplicate_failure(clock: Clock) -> None:
    get = getter(found(status="failure", result={"duplicate_of": 11, "duplicate_in_trash": False}))
    out = await run(get)
    assert out["state"] == "failed"
    assert out["reason"] == "duplicate"
    assert out["duplicate_of"] == 11
    assert "document_id" not in out


async def test_revoked_is_failed_and_message_bounded(clock: Clock) -> None:
    get = getter(found(status="revoked", result="x" * 900))
    out = await run(get)
    assert out["state"] == "failed"
    assert len(out["message"]) == 500


async def test_wait_zero_queries_once_and_reports_pending(clock: Clock) -> None:
    get = getter(found(status="started"))
    out = await run(get, 0)
    assert out == {"task_id": "t1", "polls": 1, "state": "pending", "status": "started"}
    assert get.calls == ["t1"]
    assert clock.sleeps == []


async def test_polls_with_bounded_backoff_until_success(clock: Clock) -> None:
    get = getter(
        found(status="pending"),
        found(status="started"),
        found(status="started"),
        found(status="success", result={"document_id": 5}),
    )
    out = await run(get, 60)
    assert out["state"] == "imported"
    assert out["polls"] == 4
    assert clock.sleeps == [1.0, 2.0, 4.0]


async def test_deadline_caps_sleep_and_stops(clock: Clock) -> None:
    get = getter(found(status="pending"))
    out = await run(get, 4)
    assert out["state"] == "pending"
    assert clock.sleeps == [1.0, 2.0, 1.0]
    assert out["polls"] == 3


async def test_not_found_then_appears(clock: Clock) -> None:
    get = getter(
        {"task_id": "t1", "found": False, "task": None},
        found(status="success", result={"document_id": 8}),
    )
    assert (await run(get, 5))["document_id"] == 8


async def test_not_found_at_deadline(clock: Clock) -> None:
    get = getter({"task_id": "t1", "found": False, "task": None})
    out = await run(get, 2)
    assert out["state"] == "not_found"
    assert out["task_id"] == "t1"


async def test_other_task_id_or_type_is_not_trusted(clock: Clock) -> None:
    other = getter(
        {
            "found": True,
            "task": {"task_id": "other", "status": "success", "result": {"document_id": 1}},
        }
    )
    assert (await run(other))["state"] == "not_found"
    wrong_type = getter(
        found(task_type="train_classifier", status="success", result={"document_id": 1})
    )
    out = await run(wrong_type)
    assert out["state"] == "failed"
    assert "document_id" not in out


async def test_unknown_status_stays_pending(clock: Clock) -> None:
    out = await run(getter(found(status="weird", result={"document_id": 1})))
    assert out["state"] == "pending"
    assert "document_id" not in out


async def test_transient_poll_errors_are_retried_and_reported(clock: Clock) -> None:
    get = getter(
        httpx.ConnectError("down"),
        PaperlessError("503"),
        found(status="success", result={"document_id": 6}),
    )
    out = await run(get, 10)
    assert out["state"] == "imported"
    assert "poll_error" not in out


async def test_poll_error_at_deadline_preserves_task_id(clock: Clock) -> None:
    out = await run(getter(httpx.ReadTimeout("slow")), 0)
    assert out["state"] == "pending"
    assert out["task_id"] == "t1"
    assert out["poll_error"] == "slow"


async def test_wait_deadline_includes_in_flight_request() -> None:
    async def stuck(_task_id: str) -> dict[str, Any]:
        await asyncio.Future[None]()
        raise AssertionError("Unreachable")

    out = await wait_for_document_import("t1", stuck, wait_seconds=1)
    assert out["state"] == "pending"
    assert out["task_id"] == "t1"
    assert out["poll_error"] == "Import status check timed out."


async def test_programming_errors_propagate(clock: Clock) -> None:
    with pytest.raises(KeyError):
        await run(getter(KeyError("bug")), 5)


@pytest.mark.parametrize("wait", [-1, 61, True, 1.5, "3"])
async def test_invalid_wait_seconds(clock: Clock, wait: Any) -> None:
    get = getter(found(status="pending"))
    with pytest.raises(ValueError):
        await run(get, wait)
    assert get.calls == []


async def test_blank_task_id_rejected(clock: Clock) -> None:
    with pytest.raises(ValueError):
        await wait_for_document_import(" ", getter(found(status="pending")), wait_seconds=0)
