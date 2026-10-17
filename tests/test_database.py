import gzip
import json
import sqlite3
from pathlib import Path

import pytest
import zstandard

from scripts.build_database import build_database


def test_database_embeds_collections_preserves_records_and_rebuilds(tmp_path):
    record = json.loads((Path(__file__).parent / "fixtures/records.jsonl").read_text())
    source = tmp_path / "datasets.jsonl.gz"
    target = tmp_path / "catalog.db.zst"
    with gzip.open(source, "wt", encoding="utf-8") as stream:
        stream.write(json.dumps(record) + "\n")
    counts = build_database(source, target, zstd_level=1)
    assert counts == {
        "catalogs": 1,
        "publishers": 1,
        "datasets": 1,
        "distributions": 2,
        "data_services": 1,
    }
    database = tmp_path / "catalog.db"
    with target.open("rb") as incoming, database.open("wb") as outgoing:
        zstandard.ZstdDecompressor().copy_stream(incoming, outgoing)
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            db.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
            == 5
        )
        assert (
            db.execute("SELECT json_extract(title,'$.sv') FROM datasets").fetchone()[0]
            == "Svensk titel"
        )
        assert (
            db.execute(
                "SELECT json_extract(categories,'$[0].label.en') FROM datasets"
            ).fetchone()[0]
            == "Environment"
        )
        assert (
            db.execute(
                "SELECT json_extract(temporal,'$[0].gte') FROM datasets"
            ).fetchone()[0]
            == "2020"
        )
        assert (
            db.execute(
                "SELECT json_extract(machine_translated,'$.en') FROM datasets"
            ).fetchone()[0]
            == 1
        )
        assert (
            json.loads(db.execute("SELECT record_json FROM datasets").fetchone()[0])
            == record
        )
        assert (
            db.execute(
                "SELECT count(DISTINCT source_id) FROM distributions"
            ).fetchone()[0]
            == 1
        )
        assert (
            db.execute(
                "SELECT json_array_length(urls,'$.access') FROM distributions ORDER BY id"
            ).fetchone()[0]
            == 2
        )
        assert (
            db.execute(
                "SELECT json_extract(title,'$.en') FROM data_services"
            ).fetchone()[0]
            == "Service"
        )
        assert (
            db.execute(
                "SELECT count(*) FROM data_services s JOIN distributions d ON d.id=s.distribution_id JOIN datasets t ON t.resource=s.dataset_resource AND t.resource=d.dataset_resource"
            ).fetchone()[0]
            == 1
        )
        columns = [
            r
            for (table,) in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            for r in db.execute(f"PRAGMA table_info({table})")
        ]
        assert sum(r[2] == "JSON" for r in columns) == 34
        assert not any(r[1].endswith(("_sv", "_en")) for r in columns)
    with gzip.open(source, "wt", encoding="utf-8") as stream:
        stream.write(json.dumps({"resource": "urn:new", "id": "new"}) + "\n")
    assert build_database(source, target)["distributions"] == 0


def test_missing_or_bad_input_preserves_database(tmp_path):
    target = tmp_path / "catalog.db.zst"
    target.write_bytes(b"previous database")
    source = tmp_path / "datasets.jsonl.gz"
    with pytest.raises(FileNotFoundError, match="Run the Harvest workflow first"):
        build_database(source, target)
    with gzip.open(source, "wt") as stream:
        stream.write('{"resource":"urn:one","id":"one"}\ninvalid json\n')
    with pytest.raises(json.JSONDecodeError):
        build_database(source, target)
    assert target.read_bytes() == b"previous database"
