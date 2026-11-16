"""Rights-gated, polite, resumable HTTP acquisition.

Every request is preceded by an operation-scoped rights check and constrained by an
allowlist, a per-host minimum interval, a byte ceiling, redirect validation and an
SSRF guard. Nothing is fetched speculatively: the caller must hold a rights decision
permitting ``acquire`` for the exact edition or asset being retrieved.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .blob_store import ContentAddressedStore, StoredBlob
from .db import Database, new_id, utcnow
from .preflight import PreflightLimits, PreflightReport, preflight, sniff_mime
from .rights import RightsDenied


class AcquisitionError(RuntimeError):
    """A remote resource could not be acquired within policy."""


class PolicyViolation(AcquisitionError):
    """The request violates acquisition policy and must not be attempted."""


@dataclass(frozen=True, slots=True)
class AcquisitionPolicy:
    allowed_hosts: frozenset[str]
    min_interval_seconds: float = 2.0
    max_bytes: int = 512 * 1024 * 1024
    max_redirects: int = 3
    timeout_seconds: float = 60.0
    user_agent: str = "AKI-Research-Engine/0.1 (local archival research)"
    permit_private_addresses: bool = False
    require_https: bool = True

    def __post_init__(self) -> None:
        if not self.allowed_hosts:
            raise ValueError("acquisition policy requires at least one allowed host")
        if self.min_interval_seconds < 0:
            raise ValueError("min_interval_seconds cannot be negative")
        if self.max_bytes < 1:
            raise ValueError("max_bytes must be positive")

    def host_allowed(self, url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        if self.require_https and parsed.scheme != "https":
            return False
        host = (parsed.hostname or "").lower()
        return host in {allowed.lower() for allowed in self.allowed_hosts}


def assert_public_address(host: str, *, permit_private: bool = False) -> list[str]:
    """Resolve a hostname and reject addresses that could reach internal services."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise PolicyViolation(f"host {host!r} could not be resolved: {exc}") from exc
    addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        raise PolicyViolation(f"host {host!r} resolved to no addresses")
    if permit_private:
        return addresses
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified):
            raise PolicyViolation(f"host {host!r} resolves to non-public address {address}")
    return addresses


class _RedirectGuard(urllib.request.HTTPRedirectHandler):
    max_redirections = 3

    def __init__(self, policy: AcquisitionPolicy) -> None:
        self.policy = policy
        super().__init__()

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        if not self.policy.host_allowed(newurl):
            raise PolicyViolation(f"redirect to disallowed host blocked: {newurl}")
        assert_public_address(
            urllib.parse.urlparse(newurl).hostname or "",
            permit_private=self.policy.permit_private_addresses,
        )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class RateLimiter:
    """Enforce a minimum interval between requests to the same host."""

    def __init__(self) -> None:
        self._last: dict[str, float] = {}

    def wait(self, host: str, min_interval: float) -> float:
        now = time.monotonic()
        last = self._last.get(host)
        slept = 0.0
        if last is not None:
            remaining = min_interval - (now - last)
            if remaining > 0:
                time.sleep(remaining)
                slept = remaining
        self._last[host] = time.monotonic()
        return slept


@dataclass(frozen=True, slots=True)
class AcquisitionResult:
    source_url: str
    final_url: str
    http_status: int
    content_type: str
    sha256: str
    byte_size: int
    detected_mime: str
    preflight: PreflightReport
    stored: StoredBlob
    achieved_at: str
    attempts: int
    headers: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["preflight"] = self.preflight.to_dict()
        payload["stored"] = {"sha256": self.stored.sha256, "path": str(self.stored.path)}
        return payload


class AcquisitionService:
    """Download authorized files into the immutable content-addressed vault."""

    def __init__(
        self,
        database: Database,
        store: ContentAddressedStore,
        policy: AcquisitionPolicy,
        *,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.database = database
        self.store = store
        self.policy = policy
        self.limiter = RateLimiter()
        self.sleeper = sleeper

    def _authorize(self, rights: Any, operation: str) -> None:
        """Re-evaluate rights at request time; never trust queue-time state."""
        from .production_domain import RightsOperation
        try:
            permitted = rights.permits(RightsOperation(operation))
        except Exception:
            permitted = False
        if not permitted:
            raise RightsDenied(
                f"operation '{operation}' is not authorized for this subject; "
                "record an evidence-backed rights decision before acquiring"
            )

    def download(
        self,
        url: str,
        *,
        rights: Any,
        destination_dir: str | Path,
        operation: str = "acquire",
        expected_media_types: tuple[str, ...] | None = None,
        max_retries: int = 2,
    ) -> AcquisitionResult:
        if not self.policy.host_allowed(url):
            raise PolicyViolation(f"URL is outside the acquisition allowlist: {url}")
        self._authorize(rights, operation)
        host = urllib.parse.urlparse(url).hostname or ""
        assert_public_address(host, permit_private=self.policy.permit_private_addresses)

        target_dir = Path(destination_dir).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        attempt = 0
        last_error: Exception | None = None
        while attempt <= max_retries:
            attempt += 1
            self.limiter.wait(host, self.policy.min_interval_seconds)
            try:
                return self._attempt(url, target_dir, expected_media_types, attempt)
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code in {429, 500, 502, 503, 504} and attempt <= max_retries:
                    self.sleeper(min(2 ** attempt, 30))
                    continue
                raise AcquisitionError(f"HTTP {exc.code} for {url}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                if attempt <= max_retries:
                    self.sleeper(min(2 ** attempt, 30))
                    continue
                raise AcquisitionError(f"network failure for {url}: {exc}") from exc
        raise AcquisitionError(f"acquisition failed after {attempt} attempts: {last_error}")

    def _attempt(
        self,
        url: str,
        target_dir: Path,
        expected_media_types: tuple[str, ...] | None,
        attempt: int,
    ) -> AcquisitionResult:
        opener = urllib.request.build_opener(_RedirectGuard(self.policy))
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.policy.user_agent,
                "Accept": "application/pdf,image/*,text/plain;q=0.8,*/*;q=0.5",
            },
        )
        temporary = target_dir / f".partial-{new_id()}"
        digest_hash = __import__("hashlib").sha256()
        written = 0
        try:
            with opener.open(request, timeout=self.policy.timeout_seconds) as response:
                final_url = response.geturl()
                if not self.policy.host_allowed(final_url):
                    raise PolicyViolation(f"final URL is outside the allowlist: {final_url}")
                status = int(response.status)
                content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                declared_length = response.headers.get("Content-Length")
                if declared_length is not None and int(declared_length) > self.policy.max_bytes:
                    raise PolicyViolation(
                        f"declared size {declared_length} exceeds policy maximum "
                        f"{self.policy.max_bytes}"
                    )
                with temporary.open("wb") as sink:
                    while chunk := response.read(1024 * 1024):
                        written += len(chunk)
                        if written > self.policy.max_bytes:
                            raise PolicyViolation(
                                f"download exceeded policy maximum of {self.policy.max_bytes} bytes"
                            )
                        digest_hash.update(chunk)
                        sink.write(chunk)
                headers = {key.lower(): value for key, value in response.headers.items()}
            if expected_media_types and content_type and content_type not in expected_media_types:
                raise PolicyViolation(
                    f"server returned {content_type!r}, expected one of {expected_media_types}"
                )
            with temporary.open("rb") as stream:
                head = stream.read(4096)
            detected = sniff_mime(head) or "application/octet-stream"
            if detected in {"application/x-executable", "text/html"}:
                raise PolicyViolation(f"refusing to archive {detected} content from {final_url}")
            report = preflight(temporary, PreflightLimits(max_bytes=self.policy.max_bytes))
            if not report.accepted:
                raise PolicyViolation(
                    f"preflight rejected {final_url}: {'; '.join(report.errors) or 'unknown'}"
                )
            stored = self.store.put_path(temporary)
            result = AcquisitionResult(
                source_url=url,
                final_url=final_url,
                http_status=status,
                content_type=content_type,
                sha256=stored.sha256,
                byte_size=stored.size_bytes,
                detected_mime=detected,
                preflight=report,
                stored=stored,
                achieved_at=utcnow(),
                attempts=attempt,
                headers=headers,
            )
            self._audit(result)
            return result
        finally:
            temporary.unlink(missing_ok=True)

    def _audit(self, result: AcquisitionResult) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO audit_events (id, occurred_at, actor_type, actor_id, action,"
                " subject_type, subject_id, after_hash, details) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    new_id(), utcnow(), "service", "acquisition", "asset.acquired", "asset",
                    result.sha256, result.sha256,
                    json.dumps(
                        {
                            "source_url": result.source_url,
                            "final_url": result.final_url,
                            "http_status": result.http_status,
                            "content_type": result.content_type,
                            "byte_size": result.byte_size,
                            "detected_mime": result.detected_mime,
                        },
                        sort_keys=True,
                    ),
                ),
            )
