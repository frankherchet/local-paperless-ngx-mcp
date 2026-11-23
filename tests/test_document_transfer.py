from __future__ import annotations

import asyncio
import os
from importlib import import_module
from pathlib import Path

import httpx
import pytest
from fastmcp import Client

from paperless_ngx_mcp.client import PaperlessClient
from paperless_ngx_mcp.config import Settings
from paperless_ngx_mcp.server import create_server


@pytest.fixture
def transfer_dir(tmp_path: Path) -> Path:
    folder = tmp_path.resolve() / "transfer"
    folder.mkdir(mode=0o700)
    return folder


def settings_for(transfer_dir: Path | None, *, read_only: bool = False) -> Settings:
    return Settings.model_validate(
        {
            "PAPERLESS_URL": "http://paperless.test/paperless",
            "PAPERLESS_TOKEN": "test-token",
            "PAPERLESS_READ_ONLY": read_only,
            "PAPERLESS_TRANSFER_DIR": transfer_dir,
        }
    )


@pytest.mark.parametrize(
    ("tool", "route", "original"),
    [("preview_document", "preview", False), ("download_document", "download", True)],
)
async def test_file_tool_saves_local_document_without_returning_bytes(
    transfer_dir: Path, tool: str, route: str, original: bool
) -> None:
    document = b"%PDF-1.4 private document"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/paperless/api/documents/42/{route}/"
        assert request.url.params["version"] == "51"
        assert (request.url.params.get("original") == "true") == original
        assert request.headers["Authorization"] == "Token test-token"
        return httpx.Response(
            200,
            content=document,
            headers={
                "content-type": "application/pdf",
                "content-disposition": 'attachment; filename="../../original.pdf"',
            },
        )

    async with (
        PaperlessClient(
            settings_for(transfer_dir, read_only=True), transport=httpx.MockTransport(handler)
        ) as paperless,
        Client(create_server(paperless)) as client,
    ):
        result = await client.call_tool(
            tool, {"document_id": 42, "version_id": 51, "original": original}
        )

    assert result.structured_content is not None
    target = Path(result.structured_content["path"])
    assert target.parent == await asyncio.to_thread(transfer_dir.resolve)
    assert target.suffix == ".pdf"
    assert await asyncio.to_thread(target.read_bytes) == document
    assert result.structured_content["size_bytes"] == len(document)
    assert "private document" not in str(result.content)
    if os.name != "nt":
        info = await asyncio.to_thread(target.stat)
        assert info.st_mode & 0o077 == 0


@pytest.mark.parametrize("confirm_later", [False, True])
async def test_import_reports_confirmed_document(transfer_dir: Path, confirm_later: bool) -> None:
    source = transfer_dir / "invoice.pdf"
    await asyncio.to_thread(source.write_bytes, b"%PDF-1.4 test invoice")
    task_id = "1d5a70da-8566-4202-9cc1-c34791d11202"
    checks = 0
    uploads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal checks, uploads
        if request.method == "POST":
            uploads += 1
            assert request.url.path == "/paperless/api/documents/post_document/"
            assert request.headers["content-type"].startswith("multipart/form-data;")
            assert b'name="document"; filename="invoice.pdf"' in request.content
            assert b"%PDF-1.4 test invoice" in request.content
            return httpx.Response(200, json=task_id)
        assert request.url.path == "/paperless/api/tasks/"
        assert request.url.params["task_id"] == task_id
        checks += 1
        if checks == 1 and confirm_later:
            return httpx.Response(200, json={"results": []})
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "task_id": task_id,
                        "task_type": "consume_file",
                        "status": "SUCCESS",
                        "related_document_ids": [42],
                        "result_data": {"document_id": 42},
                    }
                ]
            },
        )

    async with (
        PaperlessClient(
            settings_for(transfer_dir), transport=httpx.MockTransport(handler)
        ) as paperless,
        Client(create_server(paperless)) as client,
    ):
        result = await client.call_tool(
            "import_document", {"filename": "invoice.pdf", "wait_seconds": 0}
        )
        if confirm_later:
            assert result.structured_content is not None
            assert result.structured_content["state"] == "not_found"
            assert result.structured_content["task_id"] == task_id
            result = await client.call_tool("get_document_import_status", {"task_id": task_id})

    assert result.structured_content is not None
    assert result.structured_content["document_id"] == 42
    assert result.structured_content["task_id"] == task_id
    assert uploads == 1
    assert await asyncio.to_thread(source.exists)


@pytest.mark.parametrize(
    ("enabled", "read_only", "filename", "message"),
    [
        (False, False, "invoice.pdf", "PAPERLESS_TRANSFER_DIR"),
        (True, True, "invoice.pdf", "PAPERLESS_READ_ONLY=false"),
        (True, False, "../secret.pdf", "single filename"),
        (True, False, "report.pdf:secret", "single filename"),
    ],
)
async def test_import_rejects_unapproved_local_reads(
    transfer_dir: Path, enabled: bool, read_only: bool, filename: str, message: str
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        pytest.fail("Rejected upload must not contact Paperless")

    async with (
        PaperlessClient(
            settings_for(transfer_dir if enabled else None, read_only=read_only),
            transport=httpx.MockTransport(handler),
        ) as paperless,
        Client(create_server(paperless)) as client,
    ):
        result = await client.call_tool(
            "import_document", {"filename": filename}, raise_on_error=False
        )

    assert result.is_error
    assert message in str(result.content)


@pytest.mark.skipif(os.name == "nt", reason="Creating symlinks can require Windows privileges")
async def test_import_does_not_follow_symlink(transfer_dir: Path) -> None:
    secret = transfer_dir.parent / "secret.pdf"
    await asyncio.to_thread(secret.write_bytes, b"outside transfer directory")
    await asyncio.to_thread((transfer_dir / "invoice.pdf").symlink_to, secret)

    def handler(_request: httpx.Request) -> httpx.Response:
        pytest.fail("Symlink must not be uploaded")

    async with (
        PaperlessClient(
            settings_for(transfer_dir), transport=httpx.MockTransport(handler)
        ) as paperless,
        Client(create_server(paperless)) as client,
    ):
        result = await client.call_tool(
            "import_document", {"filename": "invoice.pdf"}, raise_on_error=False
        )

    assert result.is_error
    assert "regular file" in str(result.content)


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory mode")
async def test_download_rejects_directory_with_untrusted_parent(transfer_dir: Path) -> None:
    await asyncio.to_thread(transfer_dir.parent.chmod, 0o777)

    def handler(_request: httpx.Request) -> httpx.Response:
        pytest.fail("Rejected download must not contact Paperless")

    async with (
        PaperlessClient(
            settings_for(transfer_dir), transport=httpx.MockTransport(handler)
        ) as paperless,
        Client(create_server(paperless)) as client,
    ):
        result = await client.call_tool(
            "download_document", {"document_id": 42}, raise_on_error=False
        )

    assert result.is_error
    assert "untrusted parent" in str(result.content)


@pytest.mark.skipif(os.name != "nt", reason="Requires native Windows ACLs")
async def test_download_rejects_windows_folder_readable_by_everyone(transfer_dir: Path) -> None:
    def allow_everyone_to_read() -> None:
        security = import_module("win32security")
        rights = import_module("ntsecuritycon")
        path = str(transfer_dir)
        descriptor = security.GetNamedSecurityInfo(
            path, security.SE_FILE_OBJECT, security.DACL_SECURITY_INFORMATION
        )
        acl = descriptor.GetSecurityDescriptorDacl()
        acl.AddAccessAllowedAce(
            security.ACL_REVISION,
            rights.FILE_GENERIC_READ,
            security.ConvertStringSidToSid("S-1-1-0"),
        )
        security.SetNamedSecurityInfo(
            path, security.SE_FILE_OBJECT, security.DACL_SECURITY_INFORMATION, None, None, acl, None
        )

    def handler(_request: httpx.Request) -> httpx.Response:
        pytest.fail("Rejected download must not contact Paperless")

    await asyncio.to_thread(allow_everyone_to_read)
    async with (
        PaperlessClient(
            settings_for(transfer_dir), transport=httpx.MockTransport(handler)
        ) as paperless,
        Client(create_server(paperless)) as client,
    ):
        result = await client.call_tool(
            "download_document", {"document_id": 42}, raise_on_error=False
        )

    assert result.is_error
    assert "private, trusted Windows ACL" in str(result.content)
