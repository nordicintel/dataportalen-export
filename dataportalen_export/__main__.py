import argparse
import asyncio
import json
import logging
import os
from pathlib import Path

from dataportalen_export.config import load_config
from dataportalen_export.exports.products import validate_products
from dataportalen_export.harvester import Harvester


def main():
    parser = argparse.ArgumentParser(
        description="Harvest current Swedish catalog metadata"
    )
    parser.add_argument(
        "--force", action="store_true", help="Refetch unchanged source records"
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate existing products without harvesting",
    )
    parser.add_argument(
        "--config", type=Path, help="Configuration file; default is the repository root"
    )
    args = parser.parse_args()
    config = load_config(args.config)
    logging.basicConfig(
        level=config.logging.level, format="%(asctime)s %(levelname)s %(message)s"
    )
    logging.getLogger("httpx").setLevel(config.logging.http_level)
    logging.getLogger("httpcore").setLevel(config.logging.http_level)
    logging.getLogger("rdflib").setLevel(logging.ERROR)
    if args.validate:
        manifest = validate_products(config.output.directory)
    else:
        manifest = asyncio.run(Harvester().run(config, force=args.force))
    summary = {
        key: manifest[key]
        for key in (
            "status",
            "entity_counts",
            "record_count",
            "stale_records",
            "deleted_records",
            "sources",
            "omissions",
        )
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary_file := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary_file, "a", encoding="utf-8") as stream:
            stream.write(
                "### Harvest result\n\n```json\n"
                + json.dumps(summary, indent=2)
                + "\n```\n"
            )


if __name__ == "__main__":
    main()
