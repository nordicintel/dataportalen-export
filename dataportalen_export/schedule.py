"""Standard-library-only hourly gate; importing this needs no project dependencies."""

import argparse
import json
import os
import tomllib
from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo


def due(settings, attempted_at, now, *, manual=False):
    if manual:
        return True
    if not settings.get("enabled", True):
        return False
    zone = ZoneInfo(settings.get("timezone", "Europe/Stockholm"))
    local = now.astimezone(zone)
    if local.time() < time.fromisoformat(settings.get("time", "06:00")):
        return False
    return (
        not attempted_at
        or datetime.fromisoformat(attempted_at).astimezone(zone).date() < local.date()
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manual", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    config = tomllib.loads((root / "config.toml").read_text(encoding="utf-8"))
    output = root / config.get("output", {}).get("directory", "data")
    manifest = (
        json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        if (output / "manifest.json").exists()
        else {}
    )
    # Failed runs keep their attempt stamp outside the published data manifest.
    stamp_file = root / ".github/last_attempt.json"
    attempt = (
        json.loads(stamp_file.read_text()).get("attempted_at")
        if stamp_file.exists()
        else manifest.get("attempted_at")
    )
    should_run = due(
        config.get("schedule", {}), attempt, datetime.now(UTC), manual=args.manual
    )
    print(f"due={str(should_run).lower()}")
    if output_file := os.environ.get("GITHUB_OUTPUT"):
        with open(output_file, "a", encoding="utf-8") as stream:
            stream.write(f"due={str(should_run).lower()}\n")
            stream.write(
                f"directory={output.resolve().relative_to(root.resolve()).as_posix()}\n"
            )


if __name__ == "__main__":
    main()
