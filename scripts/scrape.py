"""Stream complete data.europa.eu search records into one replacement gzip JSONL."""

import argparse
import gzip
import json
import logging
import os
import re
import time
import tomllib
from datetime import UTC, datetime
from datetime import time as clock_time
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://data.europa.eu/api/hub/search"
LANGUAGE_CODES = set(
    "bg cs da de el en es et fi fr ga hr hu it lt lv mt nl pl pt ro sk sl sv nb nn no is uk".split()
)
log = logging.getLogger(__name__)


def load_config(path: Path = ROOT / "config.toml") -> dict:
    config = tomllib.loads(path.read_text(encoding="utf-8"))
    settings = config["scrape"]
    for key in ("page_size", "max_attempts", "timeout_seconds"):
        if settings.get(key, 1) <= 0:
            raise ValueError(f"{key} must be positive")
    for key in ("catalogs", "countries", "languages"):
        values = settings.get(key, [])
        if not isinstance(values, list) or not all(
            isinstance(v, str) and v for v in values
        ):
            raise ValueError(f"{key} must be a list of nonempty strings")
    if not settings.get("catalogs") and not settings.get("countries"):
        raise ValueError("Specify at least one catalog or country")
    for key in ("retry_backoff_seconds", "page_delay_seconds"):
        if settings.get(key, 0) < 0:
            raise ValueError(f"{key} must be nonnegative")
    if not 0 <= settings.get("gzip_level", 6) <= 9:
        raise ValueError("gzip_level must be between 0 and 9")
    schedule = config.get("schedule", {})
    clock_time.fromisoformat(schedule.get("time", "06:00"))
    ZoneInfo(schedule.get("timezone", "Europe/Stockholm"))
    if schedule.get("interval_days", 1) < 1:
        raise ValueError("interval_days must be at least 1")
    return config


def keep_languages(value, languages: set[str]):
    if isinstance(value, dict):
        multilingual = bool(value) and all(
            key.split("-")[0] in LANGUAGE_CODES | languages for key in value
        )
        return {
            key: keep_languages(item, languages)
            for key, item in value.items()
            if not multilingual or not languages or key in languages
        }
    if isinstance(value, list):
        return [keep_languages(item, languages) for item in value]
    return value


def get_with_retry(session, url: str, params: dict, settings: dict):
    # Imported only for a scrape: schedule commands need no installed dependencies.
    import requests

    for attempt in range(settings["max_attempts"]):
        try:
            response = session.get(
                url, params=params, timeout=settings["timeout_seconds"]
            )
            response.raise_for_status()
            return response.json()["result"]
        except requests.RequestException as error:
            if attempt + 1 == settings["max_attempts"]:
                raise
            wait = settings["retry_backoff_seconds"] * 2**attempt
            log.warning("Request failed: %s; retrying in %ss", error, wait)
            time.sleep(wait)


def scrape(config: dict, output: Path = ROOT / "data/datasets.jsonl.gz") -> int:
    import requests

    settings = config["scrape"]
    facets = []
    if settings["catalogs"]:
        facets.append({"catalog": settings["catalogs"]})
    if settings["countries"]:
        facets.append({"country": settings["countries"]})
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".gz.tmp")
    seen = set()
    try:
        with (
            requests.Session() as session,
            gzip.open(
                temporary, "wt", encoding="utf-8", compresslevel=settings["gzip_level"]
            ) as stream,
        ):
            for facet in facets:
                result = get_with_retry(
                    session,
                    f"{BASE_URL}/search",
                    {
                        "filter": "dataset",
                        "limit": settings["page_size"],
                        "scroll": "true",
                        "facets": json.dumps(facet),
                    },
                    settings,
                )
                scroll_id = result.get("scrollId")
                while rows := result.get("results"):
                    for row in rows:
                        if row["id"] in seen:
                            continue
                        stream.write(
                            json.dumps(
                                keep_languages(row, set(settings["languages"])),
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                        seen.add(row["id"])
                    log.info("%d datasets retrieved", len(seen))
                    scroll_id = result.get("scrollId") or scroll_id
                    if not scroll_id:
                        break
                    time.sleep(settings["page_delay_seconds"])
                    result = get_with_retry(
                        session, f"{BASE_URL}/scroll", {"scrollId": scroll_id}, settings
                    )
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    log.info("Finished: %d datasets written to %s", len(seen), output)
    return len(seen)


def scrape_due(schedule: dict, last_scrape: str | None, now: datetime) -> bool:
    local = now.astimezone(ZoneInfo(schedule["timezone"]))
    if not schedule["enabled"] or local.time() < clock_time.fromisoformat(
        schedule["time"]
    ):
        return False
    if not last_scrape:
        return True
    previous = datetime.fromisoformat(last_scrape.replace("Z", "+00:00")).astimezone(
        local.tzinfo
    )
    return (local.date() - previous.date()).days >= schedule["interval_days"]


def sync_schedule(config: dict, workflow: Path, *, check=False):
    schedule = config["schedule"]
    hour, minute = map(int, schedule["time"].split(":"))
    body = workflow.read_text(encoding="utf-8")
    updated = re.sub(
        r"(?m)^    - cron:.*$", f"    - cron: '{minute} {hour} * * *'", body
    )
    updated = re.sub(
        r"(?m)^      timezone:.*$", f'      timezone: "{schedule["timezone"]}"', updated
    )
    if check and body != updated:
        raise ValueError(
            "Schedule differs from config.toml; run python scripts/scrape.py --sync-schedule"
        )
    if not check:
        workflow.write_text(updated, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-due", action="store_true")
    parser.add_argument("--last-scrape", default="")
    parser.add_argument("--sync-schedule", action="store_true")
    parser.add_argument("--check-schedule", action="store_true")
    args = parser.parse_args()
    config = load_config()
    if args.sync_schedule or args.check_schedule:
        sync_schedule(
            config, ROOT / ".github/workflows/harvest.yml", check=args.check_schedule
        )
    elif args.check_due:
        due = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch" or scrape_due(
            config["schedule"], args.last_scrape, datetime.now(UTC)
        )
        print(f"due={str(due).lower()}")
        if filename := os.environ.get("GITHUB_OUTPUT"):
            with open(filename, "a", encoding="utf-8") as stream:
                stream.write(f"due={str(due).lower()}\n")
                stream.write(f"database={str(config['database']['enabled']).lower()}\n")
    else:
        logging.basicConfig(
            level=config["scrape"]["log_level"],
            format="%(asctime)s %(levelname)s %(message)s",
        )
        count = scrape(config)
        if filename := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(filename, "a", encoding="utf-8") as stream:
                stream.write(
                    f"Harvested **{count:,} datasets** into datasets.jsonl.gz.\n"
                )


if __name__ == "__main__":
    main()
