"""Conservative invalidation of records sharing changed RDF descriptions."""

import re

from rdflib import URIRef

from dataportalen_export.rdf_converter import parse_rdf


def changed_uris(old, graph):
    previous = parse_rdf(old.rdf) if old else None
    subjects = {subject for subject in graph.subjects() if isinstance(subject, URIRef)}
    if previous is not None:
        subjects.update(
            subject for subject in previous.subjects() if isinstance(subject, URIRef)
        )
    return {
        str(subject)
        for subject in subjects
        if previous is None
        or set(previous.predicate_objects(subject))
        != set(graph.predicate_objects(subject))
    }


def may_reference(rdf, uris):
    """A textual prefilter only: ambiguous Turtle terms always receive a graph check."""
    if "@base" in rdf:
        return True
    prefixes = re.findall(r"@prefix\s+([\w.-]*):\s*<([^>]*)>", rdf)
    for uri in uris:
        if uri in rdf:
            return True
        for prefix, namespace in prefixes:
            if not uri.startswith(namespace):
                continue
            local = uri[len(namespace) :]
            if (
                not local
                or not re.fullmatch(r"[\w.:%-]+", local)
                or not (local[0].isalnum() or local[0] == "_")
                or local.endswith(".")
            ):
                return True
            if f"{prefix}:{local}" in rdf:
                return True
    return False
