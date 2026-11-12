import hashlib
import pytest
from aki.blob_store import ContentAddressedStore


def test_blob_store_round_trip_and_deduplicates(tmp_path):
    store = ContentAddressedStore(tmp_path / "archive")
    payload = b"immutable original scan bytes"
    first = store.put_bytes(payload)
    second = store.put_bytes(payload)
    assert first.sha256 == hashlib.sha256(payload).hexdigest()
    assert first == second
    assert store.get_bytes(first.sha256) == payload
    assert store.verify(first.sha256)


def test_blob_store_rejects_non_bytes(tmp_path):
    store = ContentAddressedStore(tmp_path)
    with pytest.raises(TypeError):
        store.put_bytes("not bytes")


def test_blob_store_rejects_invalid_digest(tmp_path):
    store = ContentAddressedStore(tmp_path)
    with pytest.raises(ValueError):
        store.get_bytes("../escape")
