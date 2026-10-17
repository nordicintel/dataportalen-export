import asyncio

import httpx
import pytest

from dataportalen_export.config import Harvest
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
