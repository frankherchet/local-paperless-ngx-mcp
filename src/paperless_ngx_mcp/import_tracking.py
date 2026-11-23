"""Resolve the outcome of a Paperless-ngx consume (import) task.

Upstream behaviour (paperless-ngx v3.5.1, ``documents/tasks.py`` and
``documents/signals/handlers.py``):

* Success: task status ``success`` and ``result_data == {"document_id": <int>}``.
* Duplicate: the consume task *returns* ``{"duplicate_of": <int>,
  "duplicate_in_trash": <bool>}``; the postrun handler then forces status
  ``failure``. ``duplicate_of`` is an existing document, never a new import.
* Stopped (barcode split, double-sided scan, workflow deleted the document):
  status ``success`` with ``result_data == {"reason": ...}`` and no document.
* Errors: status ``failure`` (or ``revoked``) with an error/reason message.
* ``pending`` and ``started`` are the only non-terminal states.

A document ID is reported only from ``result.document_id`` (or the exact legacy
success message) of a task whose status is ``success``, and only when it does not
contradict ``related_document_ids``. Filenames and free text are never parsed.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from paperless_ngx_mcp.mutation_policy import PaperlessError

MAX_WAIT_SECONDS = 60
INITIAL_POLL_INTERVAL = 1.0
MAX_POLL_INTERVAL = 5.0
MAX_MESSAGE_LENGTH = 500

_LEGACY_SUCCESS = re.compile(r"Success\. New document id ([1-9][0-9]*) created")
_FAILED_STATUSES = frozenset({"failure", "revoked"})
_RUNNING_STATUSES = frozenset({"pending", "started"})

# Polling failures that are worth retrying until the deadline. Anything else
# (TypeError, KeyError, ...) is a programming error and propagates.
_TRANSIENT_ERRORS = (PaperlessError, httpx.HTTPError)


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _monotonic() -> float:
    return time.monotonic()


def _is_id(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _bounded(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()[:MAX_MESSAGE_LENGTH]
    return None


def _message(result: object) -> str | None:
    if isinstance(result, dict):
        return (
            _bounded(result.get("error_message"))
            or _bounded(result.get("error"))
            or _bounded(result.get("message"))
            or _bounded(result.get("reason"))
        )
    return _bounded(result)


def _verified_document_id(task: dict[str, Any]) -> tuple[int | None, str | None]:
    """Return (document_id, reason_it_is_missing) for a successful task."""
    result = task.get("result")
    candidate: object = None
    if isinstance(result, dict):
        candidate = result.get("document_id")
    elif isinstance(result, str):
        match = _LEGACY_SUCCESS.fullmatch(result.strip())
        candidate = int(match.group(1)) if match else None
    if not isinstance(candidate, int) or not _is_id(candidate):
        return None, "Task succeeded but reported no created document."
    related = task.get("related_document_ids")
    if isinstance(related, list) and related and candidate not in related:
        return None, "Task document_id contradicts related_document_ids."
    return candidate, None


def _classify(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Map one task payload to an outcome dict with a ``state``."""
    task = payload.get("task")
    if not payload.get("found") or not isinstance(task, dict):
        return {"state": "not_found"}
    reported_id = task.get("task_id")
    if reported_id != task_id:
        return {"state": "not_found", "message": "Paperless returned a different task."}
    task_type = task.get("task_type")
    if task_type not in ("consume_file", "documents.tasks.consume_file"):
        return {"state": "failed", "message": f"Task is not an import task: {task_type}"}

    status = str(task.get("status", "")).lower()
    outcome: dict[str, Any] = {"status": status or None}
    result = task.get("result")
    if status in _FAILED_STATUSES:
        outcome["state"] = "failed"
        if isinstance(result, dict) and _is_id(result.get("duplicate_of")):
            outcome["reason"] = "duplicate"
            outcome["duplicate_of"] = result["duplicate_of"]
            if isinstance(result.get("duplicate_in_trash"), bool):
                outcome["duplicate_in_trash"] = result["duplicate_in_trash"]
        message = _message(result)
        if message:
            outcome["message"] = message
        return outcome
    if status == "success":
        document_id, missing = _verified_document_id(task)
        if document_id is None:
            outcome["state"] = "completed_without_document"
            outcome["message"] = _message(result) or missing
        else:
            outcome["state"] = "imported"
            outcome["document_id"] = document_id
        return outcome
    # pending, started, or an unrecognised status: keep waiting.
    if status in _RUNNING_STATUSES:
        return {"state": "pending", "status": status}
    return {"state": "pending", "status": status or None, "message": "Unrecognised task status."}


async def wait_for_document_import(
    task_id: str,
    get_task: Callable[[str], Awaitable[dict[str, Any]]],
    *,
    wait_seconds: int,
) -> dict[str, Any]:
    """Poll one consume task until it finishes or ``wait_seconds`` elapse.

    ``wait_seconds=0`` queries exactly once. Raises ``ValueError`` for a blank
    ``task_id``, or a ``wait_seconds`` that is not an int in ``0..60``.

    Always returns ``task_id``, ``state`` and ``polls``. ``state`` is one of:

    * ``imported``: ``document_id`` is verified by upstream task data.
    * ``failed``: status failure/revoked (``message``; for duplicates
      ``reason="duplicate"`` and ``duplicate_of``, which is NOT a new document).
    * ``completed_without_document``: success but no verifiable new document
      (e.g. split or stopped by a plugin); ``message`` explains.
    * ``pending``: still pending/started (or unknown status) at the deadline.
    * ``not_found``: Paperless had no matching task at the deadline.

    Pending/not_found results include ``poll_error`` when the last poll raised
    ``PaperlessError``/``httpx.HTTPError``; such errors never discard task_id
    and polling continues until the deadline. Other exceptions propagate.
    """
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id must be a non-empty string")
    if (
        not isinstance(wait_seconds, int)
        or isinstance(wait_seconds, bool)
        or not 0 <= wait_seconds <= MAX_WAIT_SECONDS
    ):
        raise ValueError(f"wait_seconds must be an integer from 0 to {MAX_WAIT_SECONDS}")

    deadline = _monotonic() + wait_seconds
    interval = INITIAL_POLL_INTERVAL
    polls = 0
    poll_error: str | None = None
    outcome: dict[str, Any] = {"state": "pending"}
    while (remaining := deadline - _monotonic()) > 0 or polls == 0:
        polls += 1
        try:
            async with asyncio.timeout(max(0, remaining) if wait_seconds else None):
                payload = await get_task(task_id)
        except TimeoutError:
            poll_error = "Import status check timed out."
            break
        except _TRANSIENT_ERRORS as exc:
            poll_error = _bounded(str(exc)) or type(exc).__name__
            outcome = {"state": "pending"}
        else:
            poll_error = None
            outcome = _classify(task_id, payload)

        if outcome["state"] not in {"pending", "not_found"}:
            break
        remaining = deadline - _monotonic()
        if remaining <= 0:
            break
        await _sleep(min(interval, remaining))
        interval = min(interval * 2, MAX_POLL_INTERVAL)
    if poll_error:
        outcome["poll_error"] = poll_error
    return {"task_id": task_id, "polls": polls, **outcome}
