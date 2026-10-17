from pathlib import Path

from rdflib import Graph, URIRef

from dataportalen_export.rdf_converter import RdfConverter, graph_digest


def test_fixture_preserves_six_entities_languages_literals_and_cycles():
    graph = Graph().parse(
        Path(__file__).parent / "fixtures/catalog.ttl", format="turtle"
    )
    converter = RdfConverter()
    entities, links = converter.convert(graph, source="dataportal", scope="fixture")
    assert {e.kind for e in entities} == {
        "catalogs",
        "datasets",
        "dataset_series",
        "distributions",
        "data_services",
        "publishers",
    }
    dataset = next(e for e in entities if e.kind == "datasets")
    assert dataset.title["fi"] == ["Väestö"]
    assert dataset.issued is None
    assert (
        dataset.extra_properties["https://example.org/precision"][0]["value"]
        == "1.2300"
    )
    assert any(link[0] == "dataset_distributions" for link in links)
    assert dataset.id != converter.entity_id("datasets", "https://example.org/dataset")
    relabeled = Graph().parse(data=graph.serialize(format="turtle"), format="turtle")
    assert graph_digest(graph) == graph_digest(relabeled)
    relabeled.add(
        (
            URIRef("https://example.org/new"),
            URIRef("https://example.org/p"),
            URIRef("https://example.org/Dataset"),
        )
    )
    assert graph_digest(graph) != graph_digest(relabeled)
