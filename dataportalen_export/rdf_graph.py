"""Canonicalize independent blank-node components without global permutations."""

import hashlib
from collections import defaultdict

from rdflib import BNode, Graph
from rdflib.compare import to_canonical_graph


def graph_bytes(graph: Graph) -> bytes:
    return b"\n".join(
        sorted(graph.serialize(format="nt", encoding="utf-8").splitlines())
    )


def canonical_graph(graph: Graph) -> Graph:
    """Preserve graph topology while separating independent blank-node searches.

    Named resources anchor each component. Identical independent components get
    distinct occurrence labels, so multiplicity and shared-node relationships
    survive. RDFLib still canonicalizes every connected blank-node component.
    """
    result = Graph()
    attached = defaultdict(set)
    for triple in graph:
        nodes = {term for term in triple if isinstance(term, BNode)}
        if not nodes:
            result.add(triple)
        for node in nodes:
            attached[node].add(triple)
    remaining = set(attached)
    components = []
    while remaining:
        pending = [remaining.pop()]
        triples = set()
        while pending:
            node = pending.pop()
            triples.update(attached[node])
            for triple in attached[node]:
                for term in triple:
                    if term in remaining:
                        remaining.remove(term)
                        pending.append(term)
        component = Graph()
        for triple in triples:
            component.add(triple)
        canonical = to_canonical_graph(component)
        components.append((graph_bytes(canonical), canonical))
    # Compact labels avoid repeating two long hashes in every RDF property.
    # Equal components remain separate occurrences with identical local topology.
    for index, (_, canonical) in enumerate(
        sorted(components, key=lambda item: item[0])
    ):
        nodes = sorted(
            {term for triple in canonical for term in triple if isinstance(term, BNode)}
        )
        labels = {
            node: BNode(f"b{index}n{number}") for number, node in enumerate(nodes)
        }
        for triple in canonical:
            result.add(tuple(labels.get(term, term) for term in triple))
    return result


def graph_digest(graph: Graph) -> str:
    return hashlib.sha256(graph_bytes(canonical_graph(graph))).hexdigest()
