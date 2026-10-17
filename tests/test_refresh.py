from pathlib import Path

from rdflib import Graph

from dataportalen_export.current_state import CurrentState, SourceBatch, make_record


def test_refresh_retains_failures_and_only_deletes_confirmed_absences():
    graph = Graph().parse(
        Path(__file__).parent / "fixtures/catalog.ttl", format="turtle"
    )
    record = make_record(
        graph,
        source="dataportal",
        native_id="x",
        scope="dataportal",
        root_uri="https://example.org/Dataset",
        stamp="one",
    )
    state = CurrentState({record.id: record})
    incomplete = SourceBatch(source="dataportal")
    state.apply(incomplete)
    assert state.records[record.id].fetched_at == record.fetched_at
    assert state.records[record.id].stale
    complete_but_failed_record = SourceBatch(
        source="dataportal", complete_scopes={"dataportal"}, seen={record.id}
    )
    state.apply(complete_but_failed_record)
    assert record.id in state.records
    state.apply(SourceBatch(source="dataportal", complete_scopes={"dataportal"}))
    assert not state.records


def test_variants_link_by_exact_uri_and_preserve_both_payloads():
    graph = Graph().parse(
        Path(__file__).parent / "fixtures/catalog.ttl", format="turtle"
    )
    a = make_record(
        graph,
        source="dataportal",
        native_id="a",
        scope="dataportal",
        root_uri="https://example.org/Dataset",
        stamp="a",
    )
    b = make_record(
        graph,
        source="dataeu",
        native_id="b",
        scope="dataeu:test",
        root_uri="https://example.org/Dataset",
        stamp="b",
    )
    state = CurrentState({a.id: a, b.id: b})
    entities, links, provenance = state.materialize()
    assert len(entities["datasets"]) == 1
    assert len({row[2] for row in provenance if row[0] == "datasets"}) == 2
    assert len(state.records) == 2


def test_removed_record_keeps_only_objects_still_referenced_and_survives_reload(
    tmp_path,
):
    import json
    import lzma

    old = make_record(
        Graph().parse(
            data="""
        <urn:removed> a <http://www.w3.org/ns/dcat#Dataset>;
          <http://purl.org/dc/terms/publisher> <urn:publisher> .
        <urn:publisher> a <http://xmlns.com/foaf/0.1/Organization>;
          <http://xmlns.com/foaf/0.1/name> "Known publisher" .
        """,
            format="turtle",
        ),
        source="dataeu",
        native_id="removed",
        scope="dataeu:one",
        root_uri="urn:removed",
        stamp="one",
    )
    alive = make_record(
        Graph().parse(
            data="""
        <urn:alive> a <http://www.w3.org/ns/dcat#Dataset>;
          <http://purl.org/dc/terms/publisher> <urn:publisher> .
        """,
            format="turtle",
        ),
        source="dataeu",
        native_id="alive",
        scope="dataeu:one",
        root_uri="urn:alive",
        stamp="one",
    )
    state = CurrentState({old.id: old, alive.id: alive})
    state.apply(
        SourceBatch(
            "dataeu",
            records={alive.id: alive},
            seen={alive.id},
            complete_scopes={"dataeu:one"},
        )
    )
    entities, links, _ = state.materialize()
    assert {e["uri"] for e in entities["datasets"].values()} == {"urn:alive"}
    assert {e["uri"] for e in entities["publishers"].values()} == {"urn:publisher"}
    assert any(table == "dataset_publishers" for table, _, _ in links)
    assert state.records[old.id].stale
    assert state.records[old.id].fetched_at == old.fetched_at
    with lzma.open(
        tmp_path / "source_records.jsonl.xz", "wt", encoding="utf-8"
    ) as stream:
        for record in state.records.values():
            stream.write(record.model_dump_json() + "\n")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"referenced_records": state.referenced_records})
    )
    loaded = CurrentState.load(tmp_path)
    assert old.id not in loaded.discovery_records
    assert loaded.materialize()[0] == entities
    fresh = make_record(
        Graph().parse(
            data="<urn:alive> a <http://www.w3.org/ns/dcat#Dataset> .", format="turtle"
        ),
        source="dataeu",
        native_id="alive",
        scope="dataeu:one",
        root_uri="urn:alive",
        stamp="two",
    )
    loaded.apply(
        SourceBatch(
            "dataeu",
            records={fresh.id: fresh},
            seen={fresh.id},
            complete_scopes={"dataeu:one"},
        )
    )
    assert not loaded.materialize()[0]["publishers"]
    assert old.id not in loaded.records
