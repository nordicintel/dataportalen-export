"""Rebuild the SQLite download from the current gzip JSONL, without network access."""

import gzip
import json
import os
import shutil
import sqlite3
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def items(value) -> list:
    return value if isinstance(value, list) else [] if value is None else [value]


def scalar(value):
    """Project a source code/reference/label into a scalar query column."""
    if isinstance(value, list):
        return scalar(value[0]) if value else None
    if isinstance(value, dict):
        for key in ("id", "resource", "label", "value", "sv", "en"):
            if value.get(key) is not None:
                return scalar(value[key])
        return None
    return value


def build_database(
    source: Path = ROOT / "data/datasets.jsonl.gz",
    target: Path = ROOT / "data/catalog.db.gz",
    *,
    gzip_level: int = 6,
) -> dict:
    if not source.is_file():
        raise FileNotFoundError(f"Missing {source}. Run the Harvest workflow first.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".database-", dir=target.parent
    ) as temporary:
        database = Path(temporary) / "catalog.db"
        db = sqlite3.connect(database)
        try:
            db.executescript((ROOT / "database.schema.sql").read_text(encoding="utf-8"))
            tables = (
                "catalogs",
                "publishers",
                "datasets",
                "distributions",
                "data_services",
            )
            json_columns = {
                table: {
                    r[1]
                    for r in db.execute(f"PRAGMA table_info({table})")
                    if r[2] == "JSON"
                }
                for table in tables
            }

            def insert(table, values, *, ignore=False):
                values = {
                    key: json.dumps(value, ensure_ascii=False)
                    if key in json_columns[table]
                    else scalar(value)
                    for key, value in values.items()
                }
                columns = ",".join(f'"{key}"' for key in values)
                sql = f'INSERT {"OR IGNORE" if ignore else ""} INTO "{table}" ({columns}) VALUES ({",".join("?" for _ in values)})'
                return db.execute(sql, list(values.values())).lastrowid

            with db, gzip.open(source, "rt", encoding="utf-8") as stream:
                for line in stream:
                    row = json.loads(line)
                    catalog = row.get("catalog") or {}
                    publisher = row.get("publisher") or {}
                    catalog_id = catalog.get("id")
                    publisher_uri = publisher.get("resource")
                    if catalog_id:
                        owner = catalog.get("publisher") or {}
                        insert(
                            "catalogs",
                            {
                                "id": catalog_id,
                                "title": catalog.get("title") or {},
                                "description": catalog.get("description") or {},
                                "homepage": catalog.get("homepage"),
                                "publisher_name": owner.get("name"),
                                "publisher_email": owner.get("email"),
                                "country": catalog.get("country"),
                                "source_type": catalog.get("source_type"),
                                "is_part_of": catalog.get("is_part_of"),
                                "issued": catalog.get("issued"),
                                "modified": catalog.get("modified"),
                                "languages": items(catalog.get("language")),
                                "publisher": owner,
                                "record_json": catalog,
                            },
                            ignore=True,
                        )
                    if publisher_uri:
                        insert(
                            "publishers",
                            {
                                key: publisher.get(key)
                                for key in (
                                    "resource",
                                    "name",
                                    "type",
                                    "email",
                                    "homepage",
                                )
                            },
                            ignore=True,
                        )
                    translations = row.get("translation_meta") or {}
                    classification = row.get("corporate-body-classification") or {}
                    label = classification.get("label") or {}
                    if isinstance(label, dict) and "title" in label:
                        label = label["title"]
                    record = row.get("catalog_record") or {}
                    distributions = items(row.get("distributions"))
                    links = [
                        {"kind": key, "value": value}
                        for key in (
                            "page",
                            "relation",
                            "source",
                            "conforms_to",
                            "has_version",
                            "is_version_of",
                            "provenance",
                            "qualified_attribution",
                            "is_referenced_by",
                        )
                        for value in items(row.get(key))
                    ]
                    insert(
                        "datasets",
                        {
                            "resource": row["resource"],
                            "id": row["id"],
                            "identifiers": items(row.get("identifier")),
                            "catalog_id": catalog_id,
                            "publisher_resource": publisher_uri,
                            "publisher_name": publisher.get("name"),
                            "corporate_body_id": classification.get("id"),
                            "corporate_body": label,
                            "title": row.get("title") or {},
                            "description": row.get("description") or {},
                            "machine_translated": {
                                lang: detail["machine_translated"]
                                for lang, detail in (
                                    translations.get("details") or {}
                                ).items()
                                if "machine_translated" in detail
                            },
                            "issued": row.get("issued"),
                            "modified": row.get("modified"),
                            "catalog_record_issued": record.get("issued"),
                            "catalog_record_modified": record.get("modified"),
                            "access_right": row.get("access_right"),
                            "accrual_periodicity": row.get("accrual_periodicity"),
                            "is_hvd": row.get("is_hvd"),
                            "quality_score": (row.get("quality_meas") or {}).get(
                                "scoring"
                            ),
                            "languages": items(row.get("language")),
                            "version_info": row.get("version_info"),
                            "version_notes": row.get("version_notes") or {},
                            "temporal_resolution": row.get("temporal_resolution"),
                            "spatial_resolution_m": row.get(
                                "spatial_resolution_in_meters"
                            ),
                            "distribution_count": len(distributions),
                            "available_languages": translations.get(
                                "full_available_languages"
                            )
                            or [],
                            "country": row.get("country"),
                            "type": row.get("type") or {},
                            "spatial_resources": items(row.get("spatial_resource")),
                            "applicable_legislation": items(
                                row.get("applicable_legislation")
                            ),
                            "topic_categories": items(row.get("topic_category")),
                            "reference_systems": items(row.get("reference_system")),
                            "in_series": items(row.get("in_series")),
                            "spatial_geojson": items(row.get("spatial")),
                            "keywords": items(row.get("keywords")),
                            "categories": items(row.get("categories")),
                            "hvd_categories": items(row.get("hvd_category")),
                            "temporal": items(row.get("temporal")),
                            "creators": items(row.get("creator")),
                            "landing_pages": items(row.get("landing_page")),
                            "contact_points": items(row.get("contact_point")),
                            "links": links,
                            "record_json": row,
                        },
                    )
                    for distribution in distributions:
                        services = items(distribution.get("access_service"))
                        license_info = distribution.get("license") or {}
                        checksum = distribution.get("checksum") or {}
                        distribution_id = insert(
                            "distributions",
                            {
                                "source_id": distribution.get("id"),
                                "dataset_resource": row["resource"],
                                "title": distribution.get("title") or {},
                                "description": distribution.get("description") or {},
                                "format": distribution.get("format"),
                                "format_label": (distribution.get("format") or {}).get(
                                    "label"
                                )
                                if isinstance(distribution.get("format"), dict)
                                else None,
                                "media_type": distribution.get("media_type"),
                                "license": license_info.get("resource")
                                if isinstance(license_info, dict)
                                else license_info,
                                "license_id": license_info.get("id")
                                if isinstance(license_info, dict)
                                else None,
                                "license_description": license_info.get("description")
                                if isinstance(license_info, dict)
                                else None,
                                "urls": {
                                    "access": items(distribution.get("access_url")),
                                    "download": items(distribution.get("download_url")),
                                    "service_endpoint": [
                                        url
                                        for service in services
                                        for url in items(service.get("endpoint_url"))
                                    ],
                                },
                                "issued": distribution.get("issued"),
                                "modified": distribution.get("modified"),
                                "status": distribution.get("status"),
                                "availability": distribution.get("availability"),
                                "languages": items(distribution.get("language")),
                                "byte_size": distribution.get("byte_size"),
                                "checksum_algorithm": checksum.get("algorithm"),
                                "checksum_value": checksum.get(
                                    "checksum_value", checksum.get("value")
                                ),
                                "rights": distribution.get("rights"),
                                "temporal_resolution": distribution.get(
                                    "temporal_resolution"
                                ),
                                "representation_technique": items(
                                    distribution.get("representation_technique")
                                ),
                                "conforms_to": items(distribution.get("conforms_to")),
                                "pages": items(distribution.get("page")),
                                "applicable_legislation": items(
                                    distribution.get("applicable_legislation")
                                ),
                                "spatial_resolution_m": distribution.get(
                                    "spatial_resolution_in_meters"
                                ),
                                "record_json": distribution,
                                "data_service_count": len(services),
                            },
                        )
                        for service in services:
                            contacts = items(service.get("contact_point"))
                            contact = contacts[0] if contacts else {}
                            insert(
                                "data_services",
                                {
                                    "distribution_id": distribution_id,
                                    "dataset_resource": row["resource"],
                                    "title": service.get("title") or {},
                                    "description": service.get("description") or {},
                                    "endpoint_urls": items(service.get("endpoint_url")),
                                    "endpoint_descriptions": items(
                                        service.get("endpoint_description")
                                    ),
                                    "documentation_urls": items(service.get("page")),
                                    "serves_dataset_count": len(
                                        items(service.get("serves_dataset"))
                                    ),
                                    "license_id": service.get("license"),
                                    "access_right": service.get("access_right"),
                                    "contact_points": contacts,
                                    "serves_datasets": items(
                                        service.get("serves_dataset")
                                    ),
                                    "license": service.get("license") or {},
                                    "contact_name": contact.get("name"),
                                    "contact_email": contact.get("email"),
                                    "record_json": service,
                                },
                            )
            counts = {
                table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                for table in tables
            }
        finally:
            db.close()
        compressed = Path(temporary) / "catalog.db.gz"
        with (
            database.open("rb") as incoming,
            gzip.open(compressed, "wb", compresslevel=gzip_level) as outgoing,
        ):
            shutil.copyfileobj(incoming, outgoing)
        os.replace(compressed, target)
    print(json.dumps(counts))
    return counts


def main():
    config = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    counts = build_database(gzip_level=config["database"]["gzip_level"])
    if filename := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(filename, "a", encoding="utf-8") as stream:
            stream.write(
                "SQLite table counts:\n```json\n"
                + json.dumps(counts, indent=2)
                + "\n```\n"
            )


if __name__ == "__main__":
    main()
