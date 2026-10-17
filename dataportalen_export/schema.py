"""One explicit logical schema for both databases and Parquet."""

import json

from dataportalen_export.rdf_converter import LINKS, MODELS


def json_text(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


COMMON = [
    ("id", "TEXT PRIMARY KEY"),
    ("uri", "TEXT"),
    ("title_json", "TEXT NOT NULL"),
    ("description_json", "TEXT NOT NULL"),
    ("issued", "TEXT"),
    ("modified", "TEXT"),
    ("types_json", "TEXT NOT NULL"),
    ("identifiers_json", "TEXT NOT NULL"),
    ("homepages_json", "TEXT NOT NULL"),
]
EXTRA = {
    "catalogs": [("context_ids_json", "TEXT NOT NULL")],
    "datasets": [
        ("keywords_json", "TEXT NOT NULL"),
        ("themes_json", "TEXT NOT NULL"),
        ("access_rights_json", "TEXT NOT NULL"),
    ],
    "dataset_series": [],
    "distributions": [
        ("access_urls_json", "TEXT NOT NULL"),
        ("download_urls_json", "TEXT NOT NULL"),
        ("media_types_json", "TEXT NOT NULL"),
        ("formats_json", "TEXT NOT NULL"),
        ("licenses_json", "TEXT NOT NULL"),
        ("byte_size", "DOUBLE"),
    ],
    "data_services": [
        ("endpoint_urls_json", "TEXT NOT NULL"),
        ("endpoint_descriptions_json", "TEXT NOT NULL"),
    ],
    "publishers": [],
}


def tables_for(state, entities, links, provenance):
    """Return ordered table definitions and consumer rows, with explicit FKs."""
    tables = {}
    for kind in MODELS:
        columns = COMMON + EXTRA[kind] + [("full_json", "TEXT NOT NULL")]
        rows = []
        for identifier, entity in sorted(entities[kind].items()):
            if not identifier or entity["id"] != identifier:
                raise ValueError("Invalid entity identity")
            row = []
            for name, _ in columns:
                if name == "full_json":
                    row.append(json_text(entity))
                elif name.endswith("_json"):
                    row.append(json_text(entity.get(name[:-5], [])))
                else:
                    row.append(entity.get(name))
            rows.append(tuple(row))
        tables[kind] = (columns, [], rows)
    for table, (left, right) in LINKS.items():
        columns = [
            ("left_id", f"TEXT NOT NULL REFERENCES {left}(id)"),
            ("right_id", f"TEXT NOT NULL REFERENCES {right}(id)"),
        ]
        tables[table] = (
            columns,
            ["PRIMARY KEY(left_id, right_id)"],
            [(a, b) for name, a, b in links if name == table],
        )
    columns = [
        ("id", "TEXT PRIMARY KEY"),
        ("source", "TEXT NOT NULL"),
        ("native_id", "TEXT NOT NULL"),
        ("scope", "TEXT NOT NULL"),
        ("root_uri", "TEXT NOT NULL"),
        ("stamp", "TEXT NOT NULL"),
        ("digest", "TEXT NOT NULL"),
        ("fetched_at", "TEXT NOT NULL"),
        ("stale", "BOOLEAN NOT NULL"),
        ("rdf", "TEXT NOT NULL"),
        ("full_json", "TEXT NOT NULL"),
    ]
    rows = [
        (
            r.id,
            r.source,
            r.native_id,
            r.scope,
            r.root_uri,
            r.stamp,
            r.digest,
            r.fetched_at,
            r.stale,
            r.rdf,
            json_text(r.model_dump(exclude={"rdf"})),
        )
        for r in sorted(state.records.values(), key=lambda r: r.id)
    ]
    tables["source_records"] = (columns, [], rows)
    for kind, identifier, record in provenance:
        if identifier not in entities[kind] or record not in state.records:
            raise ValueError("Broken entity provenance")
    tables["entity_sources"] = (
        [
            ("kind", "TEXT NOT NULL"),
            ("entity_id", "TEXT NOT NULL"),
            ("source_record_id", "TEXT NOT NULL REFERENCES source_records(id)"),
        ],
        ["PRIMARY KEY(kind, entity_id, source_record_id)"],
        provenance,
    )
    status = []
    for scope in sorted({r.scope for r in state.records.values()}):
        records = [r for r in state.records.values() if r.scope == scope]
        status.append(
            (
                scope,
                records[0].source,
                len(records),
                sum(r.stale for r in records),
                max(r.fetched_at for r in records),
            )
        )
    tables["harvest_status"] = (
        [
            ("scope", "TEXT PRIMARY KEY"),
            ("source", "TEXT NOT NULL"),
            ("record_count", "BIGINT NOT NULL"),
            ("stale_count", "BIGINT NOT NULL"),
            ("last_successful_fetch", "TEXT NOT NULL"),
        ],
        [],
        status,
    )
    return tables


def populate(connection, tables):
    for table, (columns, constraints, rows) in tables.items():
        definition = ",".join(
            [f'"{name}" {kind}' for name, kind in columns] + constraints
        )
        connection.execute(f'CREATE TABLE "{table}" ({definition})')
        if rows:
            placeholders = ",".join("?" for _ in columns)
            connection.executemany(
                f'INSERT INTO "{table}" VALUES ({placeholders})', rows
            )
    for table in MODELS:
        connection.execute(f'CREATE INDEX "{table}_uri" ON "{table}" (uri)')
    for table in LINKS:
        connection.execute(f'CREATE INDEX "{table}_right" ON "{table}" (right_id)')
    connection.execute("CREATE INDEX source_scope ON source_records(scope)")
    connection.execute("CREATE INDEX provenance_entity ON entity_sources(entity_id)")
