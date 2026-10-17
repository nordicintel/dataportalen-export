import gzip
from pathlib import Path

from rdflib import Graph

from dataportalen_export.current_state import CurrentState, SourceBatch, make_record


def test_retained_projections_update_without_changing_source_freshness(tmp_path):
    graph = Graph().parse(
        data="""<urn:dataset> a <http://www.w3.org/ns/dcat#Dataset>;
        <http://purl.org/dc/terms/issued> "2006-09" .
        <urn:distribution> a <http://www.w3.org/ns/dcat#Distribution>;
            <http://www.w3.org/ns/dcat#byteSize> "155 648" .""",
        format="turtle",
    )
    record = make_record(
        graph,
        source="dataportal",
        native_id="dates",
        scope="dataportal",
        root_uri="urn:dataset",
        stamp="one",
    )
    record.entities[0]["issued"] = (
        None  # State produced by the former restrictive parser.
    )
    record.entities[1]["byte_size"] = None
    record.stale = True
    with gzip.open(
        tmp_path / "source_records.jsonl.gz", "wt", encoding="utf-8"
    ) as stream:
        stream.write(record.model_dump_json() + "\n")
    loaded = CurrentState.load(tmp_path).records[record.id]
    assert loaded.entities[0]["issued"] == "2006-09"
    assert loaded.entities[1]["byte_size"] == 155648
    assert (loaded.rdf, loaded.digest, loaded.fetched_at, loaded.stale) == (
        record.rdf,
        record.digest,
        record.fetched_at,
        True,
    )


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
