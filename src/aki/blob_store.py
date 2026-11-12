"""Content-addressed immutable blob storage with SHA-256 verification."""
from __future__ import annotations
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StoredBlob:
    sha256: str
    size_bytes: int
    path: Path


class BlobIntegrityError(IOError):
    """Stored bytes do not match their content-addressed identity."""


class ContentAddressedStore:
    """Store opaque source bytes under sha256/<prefix>/<digest>; never overwrite a valid blob."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, digest: str) -> Path:
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("digest must be a lowercase SHA-256 hex value")
        return self.root / "sha256" / digest[:2] / digest

    @staticmethod
    def _digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def put_bytes(self, data: bytes) -> StoredBlob:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        digest = self._digest(data)
        target = self._path_for(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing = target.read_bytes()
            if self._digest(existing) != digest:
                raise BlobIntegrityError(f"existing blob failed integrity check: {target}")
            return StoredBlob(digest, len(existing), target)

        fd, temporary_name = tempfile.mkstemp(prefix=".aki-", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
            except OSError:
                # Exclusive creation is a portable fallback on filesystems without hard links.
                try:
                    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                except FileExistsError:
                    pass
                else:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
            stored = target.read_bytes()
            if self._digest(stored) != digest:
                raise BlobIntegrityError(f"blob failed post-write integrity check: {target}")
            return StoredBlob(digest, len(stored), target)
        finally:
            temporary.unlink(missing_ok=True)

    def get_bytes(self, digest: str) -> bytes:
        path = self._path_for(digest)
        data = path.read_bytes()
        actual = self._digest(data)
        if actual != digest:
            raise BlobIntegrityError(f"expected {digest}, found {actual}")
        return data

    def verify(self, digest: str) -> bool:
        try:
            self.get_bytes(digest)
        except FileNotFoundError:
            return False
        return True
