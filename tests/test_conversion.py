import subprocess
import sys
from pathlib import Path

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import DCTERMS

from dataportalen_export.rdf_converter import RdfConverter
from dataportalen_export.rdf_graph import graph_digest


def test_one_resource_can_be_a_catalog_dataset_and_publisher():
    # SwedishPolls uses the same URI for these three RDF roles.
    graph = Graph().parse(
        data="""
        <urn:polls> a <http://www.w3.org/ns/dcat#Catalog>,
            <http://www.w3.org/ns/dcat#Dataset>, <http://xmlns.com/foaf/0.1/Agent>;
            <http://www.w3.org/ns/dcat#dataset> <urn:polls>;
            <http://purl.org/dc/terms/publisher> <urn:polls>.
    """,
        format="turtle",
    )
    entities, links = RdfConverter().convert(graph, source="dataportal", scope="roles")
    assert {entity.kind for entity in entities} == {
        "catalogs",
        "datasets",
        "publishers",
    }
    assert len({entity.id for entity in entities}) == 3
    assert {row[0] for row in links} == {
        "catalog_datasets",
        "catalog_publishers",
        "dataset_publishers",
    }


def test_dates_accept_reduced_precision_and_normalize_timestamps(caplog):
    cases = {
        "2022": "2022",
        "2006-09": "2006-09",
        "2024-02-29": "2024-02-29",
        "2026-10-17T08:30:00Z": "2026-10-17T08:30:00+00:00",
        "2026-10-17 08:30:00+02:00": "2026-10-17T08:30:00+02:00",
        "2023-02-29": None,
        "2022-13": None,
        "0NaN-NaN-NaN": None,
    }
    for original, expected in cases.items():
        graph = Graph().parse(
            data="<urn:dataset> a <http://www.w3.org/ns/dcat#Dataset> .",
            format="turtle",
        )
        graph.add((URIRef("urn:dataset"), DCTERMS.issued, Literal(original)))
        entities, _ = RdfConverter().convert(graph, source="dataportal", scope="dates")
        dataset = entities[0]
        assert dataset.issued == expected
        assert dataset.extra_properties[str(DCTERMS.issued)][0]["value"] == original
        if expected is not None:
            assert not caplog.records
        caplog.clear()


def test_byte_counts_accept_grouped_digits_and_preserve_source_text():
    for text, expected in [
        ("155 648", 155648),
        ("4\u00a0993\u00a0024", 4993024),
        ("7.", 7),
        ("2022-02-09", None),
    ]:
        graph = Graph().parse(
            data="<urn:dist> a <http://www.w3.org/ns/dcat#Distribution> .",
            format="turtle",
        )
        predicate = URIRef("http://www.w3.org/ns/dcat#byteSize")
        graph.add((URIRef("urn:dist"), predicate, Literal(text)))
        entities, _ = RdfConverter().convert(
            graph, source="dataportal", scope="numbers"
        )
        assert entities[0].byte_size == expected
        assert entities[0].extra_properties[str(predicate)][0]["value"] == text


def test_repeated_blank_node_components_do_not_stall_conversion():
    # Reduced from Trafikverket resource/254, where whole-graph canonicalization
    # stalled the hosted harvest. A subprocess bounds regression failures.
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
from rdflib import Graph
from dataportalen_export.rdf_graph import canonical_graph, graph_digest
graph = Graph().parse("tests/fixtures/repeated_blank_nodes.nt", format="nt")
relabeled = Graph().parse(data=graph.serialize(format="nt"), format="nt")
assert len(canonical_graph(graph)) == len(graph)
assert graph_digest(graph) == graph_digest(relabeled)
cycle = Graph().parse(data='_:a <urn:p> _:b . _:b <urn:p> _:a .', format="turtle")
separate = Graph().parse(data='_:a <urn:p> _:a . _:b <urn:p> _:b .', format="turtle")
assert graph_digest(cycle) != graph_digest(separate)
""",
        ],
        check=True,
        timeout=8,
    )


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
    assert any(link[0] == "catalog_services" for link in links)
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
