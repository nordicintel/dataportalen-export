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
