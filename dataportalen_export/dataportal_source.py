import gzip
import logging
import shutil

import httpx
from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import RDF

from dataportalen_export.context_index import ContextIndex
from dataportalen_export.current_state import SourceBatch, make_record
from dataportalen_export.identity import stable_id
from dataportalen_export.portal_client import DUMP
from dataportalen_export.rdf_converter import closure
from dataportalen_export.rdf_graph import graph_digest

DCAT = Namespace("http://www.w3.org/ns/dcat#")
log = logging.getLogger(__name__)


class DataportalSource:
    async def harvest(self, client, previous, work, *, force=False):
        batch = SourceBatch("dataportal")
        dump_path = work / "all.rdf"
        log.info("Downloading national RDF dump")
        try:
            await client.download(DUMP, dump_path)
            graph = Graph()
            try:
                graph.parse(dump_path, format="xml")
            except Exception as error:
                raise ValueError(f"Unparseable national dump: {error}") from error
        except (httpx.HTTPError, ValueError) as error:
            batch.failures["dataportal"] = str(error)[:400]
            return batch, None
        nodes = sorted(
            {
                node
                for kind in (DCAT.Dataset, DCAT.DatasetSeries, DCAT.DataService)
                for node in graph.subjects(RDF.type, kind)
                if isinstance(node, URIRef)
            }
        )
        if not nodes:
            batch.failures["dataportal"] = "Dump contains no datasets or series"
            return batch, None
        log.info(
            "National dump: %d triples; %d dataset/series roots", len(graph), len(nodes)
        )
        index = ContextIndex()
        try:
            await index.load(client, batch, previous)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            batch.failures["dataportal:contexts"] = str(error)[:400]
        for number, node in enumerate(nodes, 1):
            native = str(node)
            key = stable_id("source", f"dataportal:{native}")
            batch.seen.add(key)
            old = previous.get(key)
            try:
                subgraph = closure(graph, node)
                digest = graph_digest(subgraph)
                catalogs = index.membership(native)
                if not index.complete and old:
                    catalogs = sorted(set(catalogs) | set(old.catalog_ids))
                if (
                    old
                    and not force
                    and old.digest == digest
                    and old.catalog_ids == catalogs
                ):
                    batch.records[key] = old
                    batch.reused += 1
                else:
                    batch.records[key] = make_record(
                        subgraph,
                        source="dataportal",
                        native_id=native,
                        scope="dataportal",
                        root_uri=native,
                        stamp=digest,
                        catalog_ids=catalogs,
                    )
                    if old and old.digest == digest:
                        batch.records[key].fetched_at = old.fetched_at
                        batch.records[key].rdf = old.rdf
                    batch.fetched += 1
            except ValueError as error:
                batch.failures[key] = str(error)[:400]
            if number % 2000 == 0:
                log.info("Dataportal processed %d/%d roots", number, len(nodes))
        if index.complete:
            batch.complete_scopes.add("dataportal")
        batch.successful_scopes.add("dataportal")
        raw = work / "dataportal.rdf.gz"
        with dump_path.open("rb") as source, raw.open("wb") as target:
            with gzip.GzipFile(
                fileobj=target, mode="wb", mtime=0, filename=""
            ) as compressed:
                shutil.copyfileobj(source, compressed)
        return batch, raw
