import json
import pytest
from aki.cli import main
from aki.manifest import read_manifest
from aki.domain import ProcessingOperation


def manifest_line():
    return {
        "source_id": "archive:work:edition-a",
        "title": "Example Work",
        "source_uri": "https://archive.example.org/work",
        "collection": "reviewed-seed",
        "edition_label": "digital scan",
        "rights": {
            "status": "licensed",
            "permitted_operations": ["store", "ocr"],
            "evidence_uri": "https://archive.example.org/license"
        }
    }


def test_manifest_reads_explicit_rights_and_metadata(tmp_path):
    path = tmp_path / "sources.jsonl"
    path.write_text(json.dumps(manifest_line()) + "\n", encoding="utf-8")
    records = list(read_manifest(path))
    assert len(records) == 1
    assert records[0].title == "Example Work"
    assert records[0].rights.permits(ProcessingOperation.OCR)


def test_manifest_reports_line_number_for_invalid_record(tmp_path):
    path = tmp_path / "invalid.jsonl"
    path.write_text('{"title": "incomplete"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"invalid.jsonl:1"):
        list(read_manifest(path))


def test_manifest_rejects_non_object_json(tmp_path):
    path = tmp_path / "array.jsonl"
    path.write_text('[]\n', encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        list(read_manifest(path))


def test_cli_init_creates_database(tmp_path, monkeypatch, capsys):
    database = tmp_path / "nested" / "catalog.sqlite3"
    monkeypatch.setattr("sys.argv", ["aki", "init", "--database", str(database)])
    assert main() == 0
    assert database.exists()
    assert '"status": "initialized"' in capsys.readouterr().out


def test_cli_import_and_list(tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "sources.jsonl"
    manifest.write_text(json.dumps(manifest_line()) + "\n", encoding="utf-8")
    database = tmp_path / "catalog.sqlite3"
    monkeypatch.setattr("sys.argv", ["aki", "catalog-import", str(manifest),
                                     "--database", str(database)])
    assert main() == 0
    assert '"added": 1' in capsys.readouterr().out
    monkeypatch.setattr("sys.argv", ["aki", "catalog-list", "--database", str(database)])
    assert main() == 0
    assert "Example Work" in capsys.readouterr().out
