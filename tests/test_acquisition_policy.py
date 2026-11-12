"""Preflight, quarantine, SSRF refusal and rights-gated acquisition."""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aki.acquisition import (
    AcquisitionPolicy,
    AcquisitionService,
    PolicyViolation,
    assert_public_address,
)
from aki.blob_store import ContentAddressedStore
from aki.db import Database, initialize
from aki.preflight import PreflightLimits, Quarantine, preflight, sniff_mime
from aki.production_domain import RightsDecision, RightsDecisionState, RightsOperation


def _grant(operations=(RightsOperation.ACQUIRE,)) -> RightsDecision:
    return RightsDecision(
        decision_id="d1",
        subject_id="edt-1",
        state=RightsDecisionState.CLEARED_LIMITED,
        permitted_operations=frozenset(operations),
    )


class _Handler(BaseHTTPRequestHandler):
    body = b"archive fixture payload"
    content_type = "text/plain"

    def do_GET(self):  # noqa: N802 - stdlib hook name
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://example.com/evil")
            self.end_headers()
            return
        payload = self.body
        self.send_response(200)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # silence test server output
        return


@pytest.fixture()
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _service(tmp_path):
    database = Database(tmp_path / "aki.sqlite3")
    initialize(database.path)
    policy = AcquisitionPolicy(
        allowed_hosts=frozenset({"127.0.0.1"}),
        min_interval_seconds=0,
        require_https=False,
        permit_private_addresses=True,
        max_bytes=1024,
    )
    return AcquisitionService(database, ContentAddressedStore(tmp_path / "blobs"), policy)


def test_sniff_mime_ignores_extension():
    assert sniff_mime(b"%PDF-1.7\n...") == "application/pdf"
    assert sniff_mime(b"\x89PNG\r\n\x1a\n") == "image/png"
    assert sniff_mime(b"MZ\x90\x00") == "application/x-executable"
    assert sniff_mime(b"plain words") == "text/plain"


def test_preflight_rejects_executable(tmp_path):
    target = tmp_path / "payload.pdf"
    target.write_bytes(b"MZ\x90\x00" + b"\x00" * 64)
    report = preflight(target)
    assert report.status == "quarantined"
    assert "executable" in " ".join(report.errors)


def test_preflight_rejects_oversize(tmp_path):
    target = tmp_path / "big.txt"
    target.write_bytes(b"a" * 2048)
    report = preflight(target, PreflightLimits(max_bytes=1024))
    assert not report.accepted
    assert any("maximum size" in error for error in report.errors)


def test_preflight_detects_bad_extension(tmp_path):
    target = tmp_path / "scan.pdf"
    target.write_bytes(b"just text, not a pdf")
    report = preflight(target)
    assert any("declares" in warning for warning in report.warnings)


def test_quarantine_writes_sidecar(tmp_path):
    target = tmp_path / "bad.pdf"
    target.write_bytes(b"MZ\x90\x00bad")
    report = preflight(target)
    held = Quarantine(tmp_path / "q").hold(target, report, reason="executable")
    assert held.exists()
    assert (held.parent / (held.name + ".reason.json")).exists()


def test_ssrf_guard_rejects_loopback_by_default():
    with pytest.raises(PolicyViolation):
        assert_public_address("127.0.0.1")


def test_ssrf_guard_rejects_private_ranges():
    with pytest.raises(PolicyViolation):
        assert_public_address("10.0.0.5")


def test_policy_requires_https_and_allowlist():
    policy = AcquisitionPolicy(allowed_hosts=frozenset({"example.org"}))
    assert policy.host_allowed("https://example.org/a") is True
    assert policy.host_allowed("http://example.org/a") is False
    assert policy.host_allowed("https://other.org/a") is False


def test_download_archives_and_audits(tmp_path, server):
    service = _service(tmp_path)
    result = service.download(
        f"{server}/book.txt",
        rights=_grant(),
        destination_dir=tmp_path / "dl",
        expected_media_types=("text/plain",),
    )
    assert result.byte_size == len(_Handler.body)
    assert result.detected_mime == "text/plain"
    assert service.store.verify(result.sha256)
    with service.database.connect() as connection:
        actions = [row["action"] for row in connection.execute("SELECT action FROM audit_events")]
    assert actions == ["asset.acquired"]


def test_download_refuses_without_rights(tmp_path, server):
    service = _service(tmp_path)
    denied = RightsDecision(
        decision_id="d2",
        subject_id="edt-1",
        state=RightsDecisionState.REVIEW_REQUIRED,
    )
    with pytest.raises(PermissionError):
        service.download(
            f"{server}/book.txt", rights=denied, destination_dir=tmp_path / "dl"
        )


def test_download_refuses_offallowlist_host(tmp_path):
    service = _service(tmp_path)
    with pytest.raises(PolicyViolation):
        service.download(
            "https://not-allowed.example.org/x.pdf",
            rights=_grant(),
            destination_dir=tmp_path / "dl",
        )


def test_redirect_to_disallowed_host_is_blocked(tmp_path, server):
    service = _service(tmp_path)
    with pytest.raises(Exception) as excinfo:
        service.download(
            f"{server}/redirect", rights=_grant(), destination_dir=tmp_path / "dl"
        )
    assert "redirect" in str(excinfo.value).lower() or "disallowed" in str(excinfo.value).lower()


def test_html_response_is_refused(tmp_path, server):
    _Handler.content_type = "text/html"
    _Handler.body = b"<html><body>not a document</body></html>"
    try:
        service = _service(tmp_path)
        with pytest.raises(PolicyViolation):
            service.download(
                f"{server}/page", rights=_grant(), destination_dir=tmp_path / "dl",
                expected_media_types=("text/plain",),
            )
    finally:
        _Handler.content_type = "text/plain"
        _Handler.body = b"archive fixture payload"
