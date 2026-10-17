import gzip
import json
import sqlite3
from pathlib import Path

import duckdb
import pytest
from rdflib import Dataset, Graph

from dataportalen_export.config import Config
from dataportalen_export.current_state import CurrentState, make_record
from dataportalen_export.exports.products import validate_products, write_products


def fixture_state():
    graph = Graph().parse(
        Path(__file__).parent / "fixtures/catalog.ttl", format="turtle"
    )
    record = make_record(
        graph,
        source="dataportal",
        native_id="fixture",
        scope="dataportal",
        root_uri="https://example.org/Dataset",
        stamp="one",
    )
    return CurrentState({record.id: record}), graph


def test_all_exports_are_consistent_readable_and_preserve_raw_graph(tmp_path):
    state, graph = fixture_state()
    config = Config()
    config.output.directory = tmp_path / "data"
    write_products(state, config, [], attempted_at="2026-10-17T07:00:00+00:00")
    path = config.output.directory
    validate_products(path)
    with sqlite3.connect(path / "catalog.db") as db:
        assert db.execute("select count(*) from datasets").fetchone()[0] == 1
        assert (
            db.execute("select count(*) from dataset_distributions").fetchone()[0] == 1
        )
        assert not db.execute("pragma foreign_key_check").fetchall()
    with duckdb.connect(str(path / "catalog.duckdb"), read_only=True) as db:
        assert db.execute("select count(*) from datasets").fetchone()[0] == 1
        assert (
            db.execute(
                "select count(*) from read_parquet(?)",
                [str(path / "parquet/datasets.parquet")],
            ).fetchone()[0]
            == 1
        )
    dataset = json.loads((path / "datasets.jsonl").read_text(encoding="utf-8"))
    assert dataset["title"]["fi"] == ["Väestö"]
    assert len(dataset["distributions"]) == 1
    assert dataset["catalog_ids"]
    linked = Dataset().parse(next((path / "jsonld").glob("*.jsonld")), format="json-ld")
    assert len(linked) == len(graph)
    with gzip.open(path / "source_records.jsonl.gz", "rt", encoding="utf-8") as stream:
        assert "rdf" in json.loads(next(stream))
    restored = CurrentState.load(path)
    assert restored.records.keys() == state.records.keys()


def test_export_failure_preserves_previous_directory(tmp_path):
    state, _ = fixture_state()
    config = Config()
    config.output.directory = tmp_path / "data"
    write_products(state, config, [], attempted_at="2026-10-17T07:00:00+00:00")
    before = (config.output.directory / "manifest.json").read_bytes()
    # An invalid polymorphic reference is rejected before replacing any output.
    record = next(iter(state.records.values()))
    record.entities[0]["id"] = ""
    with pytest.raises(ValueError):
        write_products(state, config, [], attempted_at="2026-10-18T07:00:00+00:00")
    assert (config.output.directory / "manifest.json").read_bytes() == before
