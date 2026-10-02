"""Immutable source files beneath the single authorized Knowledgebase root."""

import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .config import local_app_data
from .models import ArchivedFile, ProviderError

AUTHORIZED_ROOT = Path(r"C:\Users\giova\Balun Energy\Balun Energy - Dokumente\06 Elektrovienna\07 Playground\Knowledgebase")


def source_reference(mailbox: str, message_id: str, part_id: str | None = None) -> str:
    """Length-unambiguous identities; filenames and customer names never form paths."""
    identity = json.dumps([mailbox.casefold(), message_id], separators=(",", ":"))
    message_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    if part_id is None:
        return f"00_raw/gmail/{message_key[:2]}/{message_key}.eml"
    part_key = hashlib.sha256(json.dumps([mailbox.casefold(), message_id, part_id],
                                       separators=(",", ":")).encode("utf-8")).hexdigest()
    return f"00_raw/attachments/{part_key[:2]}/{part_key}.bin"


class SourceArchive:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root is not None else AUTHORIZED_ROOT
        if not self.root.is_absolute() or self.root != AUTHORIZED_ROOT:
            raise ProviderError("archive_root_not_authorized")
        if self.root.is_relative_to(local_app_data()):
            raise ProviderError("archive_root_not_authorized")
        self._guard(self.root)

    def _guard(self, path: Path):
        if not path.is_relative_to(self.root) or path.resolve() != path.absolute():
            raise ProviderError("archive_path_unsafe")
        for item in (path, *path.parents):
            if (item / ".git").exists():
                raise ProviderError("archive_path_unsafe")
            if item.is_symlink():
                raise ProviderError("archive_path_unsafe")
            if item.exists():
                tag = getattr(item.lstat(), "st_reparse_tag", 0)
                if tag in (getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C),
                           getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)):
                    raise ProviderError("archive_path_unsafe")

    def path(self, reference: str) -> Path:
        parts = PurePosixPath(reference).parts
        if (not parts or PurePosixPath(reference).is_absolute() or "\\" in reference
                or any(p in (".", "..") or ":" in p for p in reference.split("/"))
                or len(parts) < 3 or parts[:2] not in (("00_raw", "gmail"), ("00_raw", "attachments"))
                or any(not p or p.endswith((" ", ".")) for p in reference.split("/"))):
            raise ProviderError("archive_path_unsafe")
        if not re.fullmatch(r"00_raw/(gmail/[0-9a-f]{2}/[0-9a-f]{64}\.eml|attachments/[0-9a-f]{2}/[0-9a-f]{64}\.bin)", reference):
            raise ProviderError("archive_path_unsafe")
        target = self.root.joinpath(*parts)
        self._guard(target)
        return target

    def verify(self, record: ArchivedFile):
        target = self.path(record.reference)
        if not target.is_file():
            raise ProviderError("archive_file_missing")
        digest = hashlib.sha256()
        size = 0
        with target.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
        if size != record.byte_length or digest.hexdigest() != record.sha256:
            raise ProviderError("archive_integrity_mismatch")

    def publish(self, reference: str, data: bytes) -> ArchivedFile:
        record = ArchivedFile(reference, hashlib.sha256(data).hexdigest(), len(data),
                              datetime.now(timezone.utc).isoformat())
        target = self.path(reference)
        if target.exists():
            self.verify(record)
            return record
        # Recheck each component before and after mkdir, including existing junctions.
        for directory in reversed((target.parent, *target.parent.parents)):
            if directory.is_relative_to(self.root):
                self._guard(directory)
                directory.mkdir(exist_ok=True)
                self._guard(directory)
        temporary = None
        try:
            self._guard(target)
            descriptor, name = tempfile.mkstemp(prefix=".archive-", suffix=".tmp", dir=target.parent)
            temporary = Path(name)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            self._guard(temporary)
            self._guard(target)
            try:
                if os.name == "nt":
                    # Windows rename is atomic and refuses an existing destination.
                    os.rename(temporary, target)
                else:
                    os.link(temporary, target)  # Atomic, no-replace publication on POSIX.
            except FileExistsError:
                pass
            self.verify(record)
            return record
        finally:
            if temporary is not None:
                self._guard(temporary)
                temporary.unlink(missing_ok=True)
