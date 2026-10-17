import json
import lzma
import os
import sqlite3
from pathlib import Path

import duckdb
import pytest
from rdflib import BNode, Dataset, Graph
from rdflib.compare import isomorphic

from dataportalen_export.config import Config
from dataportalen_export.current_state import CurrentState, make_record
from dataportalen_export.exports.jsonld_exporter import JsonldExporter
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
        assert (
            next(
                row
                for row in db.execute("pragma table_info(datasets)")
                if row[1] == "id"
            )[3]
            == 1
        )
        assert db.execute("select count(*) from datasets").fetchone()[0] == 1
        assert (
            db.execute("select count(*) from dataset_distributions").fetchone()[0] == 1
        )
        assert not db.execute("pragma foreign_key_check").fetchall()
        expected_tables = {
            name: sorted(db.execute(f'SELECT * FROM "{name}"').fetchall())
            for (name,) in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    with duckdb.connect(str(path / "catalog.duckdb"), read_only=True) as db:
        for name, rows in expected_tables.items():
            assert sorted(db.execute(f'SELECT * FROM "{name}"').fetchall()) == rows
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
    represented = Graph()
    for subject, predicate, obj, _ in linked.quads((None, None, None, None)):
        represented.add((subject, predicate, obj))
    assert isomorphic(represented, graph)
    with lzma.open(path / "source_records.jsonl.xz", "rt", encoding="utf-8") as stream:
        assert "rdf" in json.loads(next(stream))
    restored = CurrentState.load(path)
    assert restored.records.keys() == state.records.keys()


def test_export_failure_preserves_previous_directory(tmp_path, monkeypatch):
    state, _ = fixture_state()
    config = Config()
    config.output.directory = tmp_path / "data"
    write_products(state, config, [], attempted_at="2026-10-17T07:00:00+00:00")
    before = (config.output.directory / "manifest.json").read_bytes()
    # An invalid polymorphic reference is rejected before replacing any output.
    record = next(iter(state.records.values()))
    record.entities[0]["id"] = ""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(ValueError):
        write_products(state, config, [], attempted_at="2026-10-18T07:00:00+00:00")
    assert (config.output.directory / "manifest.json").read_bytes() == before
    assert list(tmp_path.glob(".export-*/data"))


def test_xz_products_are_readable_and_reusable(tmp_path):
    state, _ = fixture_state()
    config = Config()
    config.output.directory = tmp_path / "data"
    config.output.compression = "xz"
    config.output.compression_threshold_mib = 0.001
    write_products(state, config, [], attempted_at="2026-10-17T07:00:00+00:00")
    path = config.output.directory
    before = (path / "catalog.db.xz").read_bytes()
    database = tmp_path / "unpacked.db"
    database.write_bytes(lzma.decompress(before))
    with sqlite3.connect(database) as db:
        assert db.execute("select count(*) from datasets").fetchone()[0] == 1
    restored = CurrentState.load(path)
    assert restored.records.keys() == state.records.keys()
    os.utime(path / "source_records.jsonl.xz", (1000000000, 1000000000))
    write_products(restored, config, [], attempted_at="2026-10-18T07:00:00+00:00")
    assert (path / "catalog.db.xz").read_bytes() == before
    assert (path / "source_records.jsonl.xz").stat().st_mtime == 1000000000
    validate_products(path)


def test_jsonld_variants_do_not_share_unrelated_blank_nodes(tmp_path):
    state, graph = fixture_state()
    other = make_record(
        graph,
        source="dataeu",
        native_id="other",
        scope="dataeu:test",
        root_uri="https://example.org/Dataset",
        stamp="two",
    )
    state.records[other.id] = other
    entities, links, provenance = state.materialize()
    JsonldExporter().write(tmp_path, state, entities, links, provenance)
    catalog = next(iter(entities["catalogs"]))
    dataset = Dataset().parse(tmp_path / f"{catalog}.jsonld", format="json-ld")
    blank_sets = [
        {node for triple in graph for node in triple if isinstance(node, BNode)}
        for graph in dataset.graphs()
        if len(graph)
    ]
    assert len(blank_sets) == 2
    assert not blank_sets[0] & blank_sets[1]


def test_only_changed_catalog_products_are_rebuilt(tmp_path):
    state, graph = fixture_state()
    other_graph = Graph().parse(
        data=graph.serialize(format="turtle").replace(
            "https://example.org/", "https://other.example.org/"
        ),
        format="turtle",
    )
    other = make_record(
        other_graph,
        source="dataportal",
        native_id="other",
        scope="dataportal",
        root_uri="https://other.example.org/Dataset",
        stamp="one",
    )
    state.records[other.id] = other
    config = Config()
    config.output.directory = tmp_path / "data"
    write_products(state, config, [], attempted_at="first")
    other_catalog = next(e["id"] for e in other.entities if e["kind"] == "catalogs")
    unchanged_paths = [
        config.output.directory / "catalogs" / other_catalog / "datasets.jsonl",
        config.output.directory / "jsonld" / f"{other_catalog}.jsonld",
    ]
    before = {path: path.read_bytes() for path in unchanged_paths}
    for path in unchanged_paths:
        os.utime(path, (1000000000, 1000000000))
    # One dataset changes; the independent catalog's published files must be reused.
    from rdflib import Literal, URIRef
    from rdflib.namespace import DCTERMS

    graph.set(
        (URIRef("https://example.org/Dataset"), DCTERMS.title, Literal("Changed"))
    )
    changed = make_record(
        graph,
        source="dataportal",
        native_id="fixture",
        scope="dataportal",
        root_uri="https://example.org/Dataset",
        stamp="two",
    )
    state.records[changed.id] = changed
    write_products(state, config, [], attempted_at="second")
    for path in unchanged_paths:
        assert path.read_bytes() == before[path]
        assert path.stat().st_mtime == 1000000000
    rows = [
        json.loads(line)
        for line in (config.output.directory / "datasets.jsonl")
        .read_text()
        .splitlines()
    ]
    assert any(row["title"] == {"und": ["Changed"]} for row in rows)
