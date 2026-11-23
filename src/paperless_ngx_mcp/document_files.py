"""Confine file transfers to an explicitly configured, trusted local directory."""

from __future__ import annotations

import os
import re
import stat
import sys
from collections.abc import AsyncIterable, Iterator
from contextlib import contextmanager
from email.message import Message
from importlib import import_module
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO

from paperless_ngx_mcp.mutation_policy import PaperlessError

_WINDOWS_DEVICE = re.compile(r"(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])", re.IGNORECASE)
_SAFE_SUFFIX = re.compile(r"\.[a-zA-Z0-9]{1,12}\Z")


def transfer_directory(configured: Path | None) -> Path:
    if configured is None:
        raise PaperlessError("File transfers require PAPERLESS_TRANSFER_DIR")
    if not configured.is_absolute() or ".." in configured.parts:
        raise PaperlessError("PAPERLESS_TRANSFER_DIR must be an absolute path without '..'")
    if sys.platform == "win32":
        _check_windows_acl(configured)
    else:
        _check_posix_directory(configured, os.getuid())
    return configured


def _directory_attributes(path: Path) -> os.stat_result:
    try:
        attributes = path.lstat()
    except (OSError, ValueError) as exc:
        raise PaperlessError("PAPERLESS_TRANSFER_DIR must be an existing directory") from exc
    reparse = getattr(attributes, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    if not stat.S_ISDIR(attributes.st_mode) or reparse:
        raise PaperlessError("PAPERLESS_TRANSFER_DIR must not contain symlinks or junctions")
    return attributes


def _check_posix_directory(directory: Path, owner: int) -> None:
    for ancestor in reversed(directory.parents):
        info = _directory_attributes(ancestor)
        writable_by_others = info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX
        if info.st_uid not in {0, owner} or writable_by_others:
            raise PaperlessError("PAPERLESS_TRANSFER_DIR has an untrusted parent directory")
    info = _directory_attributes(directory)
    if info.st_uid != owner or info.st_mode & 0o077:
        raise PaperlessError("PAPERLESS_TRANSFER_DIR must be owned by you and private (mode 700)")


def _check_windows_acl(directory: Path) -> None:
    if directory.anchor.startswith("\\\\"):
        raise PaperlessError("PAPERLESS_TRANSFER_DIR must be on a local Windows drive")
    security = import_module("win32security")
    api = import_module("win32api")
    rights = import_module("ntsecuritycon")
    win_error = import_module("pywintypes").error
    replace = (
        rights.DELETE
        | rights.FILE_DELETE_CHILD
        | rights.WRITE_DAC
        | rights.WRITE_OWNER
        | rights.FILE_WRITE_ATTRIBUTES
        | rights.GENERIC_WRITE
        | rights.GENERIC_ALL
    )
    unsafe = "PAPERLESS_TRANSFER_DIR requires a private, trusted Windows ACL"
    try:
        token = security.OpenProcessToken(api.GetCurrentProcess(), rights.TOKEN_QUERY)
        try:
            user = security.GetTokenInformation(token, security.TokenUser)[0]
        finally:
            token.Close()
        trusted = {
            security.ConvertSidToStringSid(user),
            "S-1-5-18",  # Local System
            "S-1-5-32-544",  # Built-in Administrators
            "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",  # TrustedInstaller
        }
        for path in (*reversed(directory.parents), directory):
            _directory_attributes(path)
            leaf = path == directory
            descriptor = security.GetNamedSecurityInfo(
                str(path),
                security.SE_FILE_OBJECT,
                security.OWNER_SECURITY_INFORMATION | security.DACL_SECURITY_INFORMATION,
            )
            owner = descriptor.GetSecurityDescriptorOwner()
            acl = descriptor.GetSecurityDescriptorDacl()
            if acl is None:
                raise PaperlessError(f"{unsafe}: a path component has no DACL")
            if owner is None or security.ConvertSidToStringSid(owner) not in trusted:
                level = len(directory.parts) - len(path.parts)
                raise PaperlessError(f"{unsafe}: untrusted directory owner at depth {level}")
            for index in range(acl.GetAceCount()):
                ace = acl.GetAce(index)
                ace_type, flags = ace[0]
                if ace_type == rights.ACCESS_DENIED_ACE_TYPE:
                    continue
                if ace_type != rights.ACCESS_ALLOWED_ACE_TYPE:
                    raise PaperlessError(unsafe)
                sid = security.ConvertSidToStringSid(ace[2])
                inherit_only = bool(flags & rights.INHERIT_ONLY_ACE)
                if sid in trusted or (inherit_only and sid == "S-1-3-0"):
                    continue
                if leaf or (not inherit_only and ace[1] & replace):
                    raise PaperlessError(unsafe)
    except win_error as exc:
        raise PaperlessError("Could not verify the Windows transfer directory ACL") from exc


@contextmanager
def open_upload(directory: Path, filename: str) -> Iterator[BinaryIO]:
    if (
        not filename
        or filename[-1] in ". "
        or any(character in '/\\:<>"|?*' or ord(character) < 32 for character in filename)
        or _WINDOWS_DEVICE.fullmatch(filename.split(".", 1)[0].rstrip(" "))
    ):
        raise ValueError("Select a single filename in PAPERLESS_TRANSFER_DIR, not a path")

    path = directory / filename
    try:
        expected = path.lstat()
        if not stat.S_ISREG(expected.st_mode) or expected.st_nlink != 1:
            raise OSError("Not a regular, unlinked file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags | getattr(os, "O_BINARY", 0))
    except OSError as exc:
        raise PaperlessError(
            "Select an existing regular file, not a symbolic or hard link"
        ) from exc
    with os.fdopen(fd, "rb") as handle:
        actual = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(actual.st_mode)
            or actual.st_nlink != 1
            or not os.path.samestat(expected, actual)
        ):
            raise PaperlessError("The selected upload is no longer the original regular file")
        yield handle


async def save_document_file(
    directory: Path,
    prefix: str,
    chunks: AsyncIterable[bytes],
    *,
    content_disposition: str,
    content_type: str,
) -> tuple[Path, int]:
    """Save to a fresh private file, ignoring any upstream filename except its suffix."""
    metadata = Message()
    metadata["Content-Disposition"] = content_disposition
    suffix = Path(metadata.get_filename() or "").suffix
    if content_type == "application/pdf":
        suffix = ".pdf"
    elif not _SAFE_SUFFIX.fullmatch(suffix):
        suffix = ".bin"

    target: Path | None = None
    saved = False
    size = 0
    try:
        with NamedTemporaryFile(dir=directory, prefix=prefix, suffix=suffix, delete=False) as file:
            target = Path(file.name)
            async for chunk in chunks:
                size += file.write(chunk)
        saved = True
        return target, size
    except OSError as exc:
        raise PaperlessError("Could not save the document in PAPERLESS_TRANSFER_DIR") from exc
    finally:
        if target is not None and not saved:
            target.unlink(missing_ok=True)
