import gzip
import json
from datetime import datetime

import pytest
import requests

from scripts.scrape import load_config, scrape, scrape_due


def test_scroll_preserves_complete_records_filters_languages_and_replaces(
    tmp_path, monkeypatch
):
    config = load_config()
    config["scrape"].update(
        page_size=2, catalogs=["oppnadata"], countries=["se"], languages=["sv", "en"]
    )
    output = tmp_path / "datasets.jsonl.gz"
    output.write_bytes(b"old export")
    record = {
        "id": "one",
        "title": {"sv": "Svenska", "en": "English", "de": "Deutsch"},
        "temporal": [{"gte": "2020", "lte": "2024"}],
        "distributions": [
            {"access_service": [{"title": {"en": "API", "de": "Dienst"}}]}
        ],
        "unknown": {"keep": True},
    }
    replies = iter(
        [
            {"results": [record], "scrollId": "first"},
            {"results": [{"id": "two"}], "scrollId": "second"},
            {"results": [{"id": "three"}], "scrollId": "second"},
            {"results": []},
            {"results": [record, {"id": "four"}]},
        ]
    )
    calls = []

    def get(session, url, *, params, timeout):
        calls.append((url, params, timeout))
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({"result": next(replies)}).encode()
        return response

    monkeypatch.setattr(requests.Session, "get", get)
    assert scrape(config, output) == 4
    with gzip.open(output, "rt", encoding="utf-8") as stream:
        rows = [json.loads(line) for line in stream]
    assert [r["id"] for r in rows] == ["one", "two", "three", "four"]
    assert rows[0]["title"] == {"sv": "Svenska", "en": "English"}
    assert rows[0]["temporal"] == [{"gte": "2020", "lte": "2024"}]
    assert rows[0]["distributions"][0]["access_service"][0]["title"] == {"en": "API"}
    assert rows[0]["unknown"] == {"keep": True}
    assert calls[0][1]["limit"] == 2
    assert json.loads(calls[0][1]["facets"]) == {"catalog": ["oppnadata"]}
    assert json.loads(calls[-1][1]["facets"]) == {"country": ["se"]}
    assert calls[1][1] == {"scrollId": "first"}
    assert calls[2][1] == calls[3][1] == {"scrollId": "second"}
    assert all(
        "includes" not in params and timeout == 120 for _, params, timeout in calls
    )


def test_retry_then_failure_preserves_completed_file(tmp_path, monkeypatch):
    config = load_config()
    config["scrape"].update(max_attempts=2, retry_backoff_seconds=0)
    output = tmp_path / "datasets.jsonl.gz"
    output.write_bytes(b"previous completed export")
    attempts = []

    def get(*args, **kwargs):
        attempts.append(1)
        if len(attempts) != 2:
            raise requests.Timeout("timeout")
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"result":{"results":[{"id":"one"}],"scrollId":"cursor"}}'
        return response

    monkeypatch.setattr(requests.Session, "get", get)
    with pytest.raises(requests.Timeout):
        scrape(config, output)
    assert len(attempts) == 4
    assert output.read_bytes() == b"previous completed export"


@pytest.mark.parametrize(
    "last,now,enabled,expected",
    [
        (None, "2026-10-17T03:59:00+00:00", True, False),
        (None, "2026-10-17T04:00:00+00:00", True, True),
        ("2026-10-17T04:05:00+00:00", "2026-10-18T05:00:00+00:00", True, False),
        ("2026-10-17T04:05:00+00:00", "2026-10-19T05:00:00+00:00", True, True),
        (None, "2026-10-17T05:00:00+00:00", False, False),
        (None, "2026-12-17T04:59:00+00:00", True, False),
        (None, "2026-12-17T05:00:00+00:00", True, True),
    ],
)
def test_stockholm_schedule(last, now, enabled, expected):
    schedule = {
        "enabled": enabled,
        "interval_days": 2,
        "time": "06:00",
        "timezone": "Europe/Stockholm",
    }
    assert scrape_due(schedule, last, datetime.fromisoformat(now)) is expected


def test_invalid_config_fails_before_network(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[scrape]\ncatalogs=["oppnadata"]\npage_size=0\n')
    with pytest.raises(ValueError, match="page_size"):
        load_config(path)
