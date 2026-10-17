import asyncio
from pathlib import Path

import httpx
import pytest
from rdflib import Graph

from dataportalen_export.config import Harvest
from dataportalen_export.current_state import CurrentState
from dataportalen_export.dataportal_source import DataportalSource
from dataportalen_export.portal_client import PortalClient


def test_eu_listing_reconciles_repository_only_records_and_retries():
    calls = 0

    def respond(request):
        nonlocal calls
        if request.url.path.endswith("/search"):
            calls += 1
            if calls == 1:
                return httpx.Response(503, headers={"Retry-After": "0"})
            return httpx.Response(
                200,
                json={
                    "result": {
                        "count": 1,
                        "results": [
                            {"id": "a", "catalog_record": {"modified": "today"}}
                        ],
                    }
                },
            )
        return httpx.Response(200, json=["a", "b"])

    async def run():
        async with PortalClient(
            Harvest(), transport=httpx.MockTransport(respond)
        ) as client:
            refs = await client.eu_listing("test")
            assert refs == {"a": "today", "b": ""}

    asyncio.run(run())
    assert calls == 2


def test_failed_catalog_metadata_preserves_known_memberships(tmp_path):
    graph = Graph().parse(
        Path(__file__).parent / "fixtures/catalog.ttl", format="turtle"
    )
    graph.parse(
        data="<https://example.org/catalog> a <http://www.w3.org/ns/dcat#Dataset> .",
        format="turtle",
    )
    dump = graph.serialize(format="xml").encode()
    catalog = """@prefix dcat: <http://www.w3.org/ns/dcat#> .
        <https://example.org/catalog> a dcat:Catalog;
            dcat:dataset <https://example.org/Dataset>, <https://example.org/series> .
        <https://example.org/another> a dcat:Catalog; <https://example.org/detail> _:a .
        _:a <https://example.org/next> _:b . _:b <https://example.org/next> _:a ."""
    fail_metadata = False

    def respond(request):
        if request.url.path == "/all.rdf":
            return httpx.Response(200, content=dump)
        if "/metadata/" in request.url.path:
            return httpx.Response(404 if fail_metadata else 200, text=catalog)
        query = request.url.params["query"]
        if "#Catalog" in query:
            rows = [{"contextId": "context", "entryId": "catalog"}]
            # EntryScape's unrestricted index count includes inaccessible entries.
            if "public:true" not in query:
                return httpx.Response(
                    200, json={"results": 2, "resource": {"children": rows}}
                )
        else:
            uri = "series" if "#DatasetSeries" in query else "Dataset"
            rows = [
                {
                    "contextId": "context",
                    "entryId": uri,
                    "info": {
                        "entry": {
                            "http://entrystore.org/terms/resource": [
                                {"value": "https://example.org/" + uri}
                            ]
                        }
                    },
                }
            ]
        return httpx.Response(
            200, json={"results": len(rows), "resource": {"children": rows}}
        )

    async def run():
        nonlocal fail_metadata
        async with PortalClient(
            Harvest(), transport=httpx.MockTransport(respond)
        ) as client:
            first, _ = await DataportalSource().harvest(client, {}, tmp_path)
            state = CurrentState(first.records)
            assert len(state.materialize()[0]["catalogs"]) == 2
            unchanged, _ = await DataportalSource().harvest(
                client, state.records, tmp_path
            )
            assert {key: record.rdf for key, record in unchanged.records.items()} == {
                key: record.rdf for key, record in first.records.items()
            }
            before = {
                r.root_uri: r.catalog_ids
                for r in first.records.values()
                if r.catalog_ids
            }
            assert "https://example.org/Dataset" in before
            assert "https://example.org/series" in before
            fail_metadata = True
            second, _ = await DataportalSource().harvest(
                client, state.records, tmp_path
            )
            state.apply(second)
            assert not second.complete_scopes
            assert second.failures
            after = {
                r.root_uri: r.catalog_ids
                for r in state.records.values()
                if r.catalog_ids
            }
            assert after == before

    asyncio.run(run())


def test_truncated_listing_does_not_look_complete():
    def respond(request):
        return httpx.Response(
            200, json={"result": {"count": 2, "results": [{"id": "a"}]}}
        )

    async def run():
        async with PortalClient(
            Harvest(), transport=httpx.MockTransport(respond)
        ) as client:
            with pytest.raises(ValueError, match="Incomplete"):
                await client.eu_listing("test")

    asyncio.run(run())


def test_scroll_cursor_can_remain_the_same_between_pages():
    pages = iter([[{"id": "b"}], []])

    def respond(request):
        if request.url.path.endswith("/search"):
            return httpx.Response(
                200,
                json={
                    "result": {"count": 2, "results": [{"id": "a"}], "scrollId": "same"}
                },
            )
        if request.url.path.endswith("/scroll"):
            return httpx.Response(
                200, json={"result": {"results": next(pages), "scrollId": "same"}}
            )
        return httpx.Response(200, json=["a", "b"])

    async def run():
        async with PortalClient(
            Harvest(), transport=httpx.MockTransport(respond)
        ) as client:
            assert await client.eu_listing("test") == {"a": "", "b": ""}

    asyncio.run(run())
