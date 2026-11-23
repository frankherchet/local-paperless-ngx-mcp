"""Confine file transfers to an explicitly configured, trusted local directory."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from collections.abc import AsyncIterable, Iterator
from contextlib import contextmanager
from email.message import Message
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO

from paperless_ngx_mcp.mutation_policy import PaperlessError

_WINDOWS_DEVICE = re.compile(r"(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])", re.IGNORECASE)
_SAFE_SUFFIX = re.compile(r"\.[a-zA-Z0-9]{1,12}\Z")

_WINDOWS_ACL_CHECK = r"""
$ErrorActionPreference = 'Stop'
$current = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$trusted = @($current, 'S-1-5-18', 'S-1-5-32-544')
$replace = [int][Security.AccessControl.FileSystemRights]::Delete `
    -bor [int][Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles `
    -bor [int][Security.AccessControl.FileSystemRights]::ChangePermissions `
    -bor [int][Security.AccessControl.FileSystemRights]::TakeOwnership `
    -bor [int][Security.AccessControl.FileSystemRights]::WriteAttributes -bor 0x50000000
$directories = @()
for ($dir = [IO.DirectoryInfo]::new($env:PAPERLESS_ACL_PATH); $dir; $dir = $dir.Parent) {
    $directories += $dir
}
for ($i = $directories.Count - 1; $i -ge 0; $i--) {
    $dir = $directories[$i]
    $leaf = $i -eq 0
    if ($dir.Attributes -band [IO.FileAttributes]::ReparsePoint) { exit 1 }
    $acl = Get-Acl -LiteralPath $dir.FullName
    $raw = [Security.AccessControl.RawSecurityDescriptor]::new(
        $acl.GetSecurityDescriptorBinaryForm(), 0
    )
    if ($null -eq $raw.DiscretionaryAcl -or -not $acl.AreAccessRulesCanonical) { exit 1 }
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted) { exit 1 }
    foreach ($ace in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($ace.AccessControlType -ne 'Allow' -or $ace.IdentityReference.Value -in $trusted) {
            continue
        }
        $inheritOnly = ($ace.PropagationFlags -band
            [Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0
        if ($leaf -and $inheritOnly -and $ace.IdentityReference.Value -eq 'S-1-3-0') {
            continue  # Creator Owner on new files resolves to the trusted caller.
        }
        if ($leaf -or (-not $inheritOnly -and ([int]$ace.FileSystemRights -band $replace))) {
            exit 1
        }
    }
}
exit 0
"""


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
    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    powershell = Path(system_root) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    try:
        checked = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_ACL_CHECK],
            env={"SystemRoot": system_root, "PAPERLESS_ACL_PATH": str(directory)},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PaperlessError("Could not verify the Windows transfer directory ACL") from exc
    if checked.returncode:
        raise PaperlessError("PAPERLESS_TRANSFER_DIR requires a private, trusted Windows ACL")


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
