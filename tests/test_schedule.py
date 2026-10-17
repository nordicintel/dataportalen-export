from datetime import datetime

from dataportalen_export.schedule import due


def test_daily_gate_timezone_previous_attempt_disable_and_manual():
    settings = {"enabled": True, "time": "06:00", "timezone": "Europe/Stockholm"}
    before = datetime.fromisoformat("2026-10-17T03:59:00+00:00")
    after = datetime.fromisoformat("2026-10-17T04:17:00+00:00")
    assert not due(settings, None, before)
    assert due(settings, None, after)
    assert not due(settings, "2026-10-17T04:01:00+00:00", after)
    assert due(settings, "2026-10-16T04:01:00+00:00", after)
    assert not due({**settings, "enabled": False}, None, after)
    assert due({**settings, "enabled": False}, None, before, manual=True)
    assert due(settings, None, datetime.fromisoformat("2026-10-26T05:17:00+00:00"))
