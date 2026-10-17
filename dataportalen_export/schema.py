"""Six object tables; relationships and provenance stay on the objects."""

import json
from collections import defaultdict


def json_text(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def display_title(entity):
    titles = entity.get("title", {})
    return next(
        (
            text
            for lang in ("sv", "sv-SE", "und", "en", *sorted(titles))
            for text in titles.get(lang, [])
            if text.strip()
        ),
        entity.get("uri"),
    )


COMMON = [
    ("id", "TEXT NOT NULL"),
    ("uri", "TEXT"),
    ("title", "TEXT"),
    ("title_json", "TEXT NOT NULL"),
    ("types_json", "TEXT NOT NULL"),
    ("source_record_ids_json", "TEXT NOT NULL"),
    ("variants_json", "TEXT NOT NULL"),
]
EXTRA = {
    "catalogs": [
        ("context_ids_json", "TEXT NOT NULL"),
        ("description_json", "TEXT NOT NULL"),
        ("homepages_json", "TEXT NOT NULL"),
        ("issued", "TEXT"),
        ("modified", "TEXT"),
    ],
    "datasets": [
        ("description_json", "TEXT NOT NULL"),
        ("issued", "TEXT"),
        ("modified", "TEXT"),
        ("identifiers_json", "TEXT NOT NULL"),
        ("keywords_json", "TEXT NOT NULL"),
        ("themes_json", "TEXT NOT NULL"),
        ("access_rights_json", "TEXT NOT NULL"),
        ("landing_pages_json", "TEXT NOT NULL"),
        ("languages_json", "TEXT NOT NULL"),
        ("frequency_json", "TEXT NOT NULL"),
    ],
    "dataset_series": [
        ("description_json", "TEXT NOT NULL"),
        ("identifiers_json", "TEXT NOT NULL"),
        ("issued", "TEXT"),
        ("modified", "TEXT"),
    ],
    "distributions": [
        ("description_json", "TEXT NOT NULL"),
        ("issued", "TEXT"),
        ("modified", "TEXT"),
        ("access_urls_json", "TEXT NOT NULL"),
        ("download_urls_json", "TEXT NOT NULL"),
        ("media_types_json", "TEXT NOT NULL"),
        ("formats_json", "TEXT NOT NULL"),
        ("licenses_json", "TEXT NOT NULL"),
        ("byte_size", "DOUBLE"),
    ],
    "data_services": [
        ("description_json", "TEXT NOT NULL"),
        ("identifiers_json", "TEXT NOT NULL"),
        ("endpoint_urls_json", "TEXT NOT NULL"),
        ("endpoint_descriptions_json", "TEXT NOT NULL"),
        ("service_types_json", "TEXT NOT NULL"),
        ("service_protocols_json", "TEXT NOT NULL"),
    ],
    "publishers": [("homepages_json", "TEXT NOT NULL")],
}
PROJECTED = {
    "landing_pages": "http://www.w3.org/ns/dcat#landingPage",
    "languages": "http://purl.org/dc/terms/language",
    "frequency": "http://purl.org/dc/terms/accrualPeriodicity",
    "service_types": "http://data.europa.eu/930/serviceType",
    "service_protocols": "http://data.europa.eu/930/serviceProtocol",
}

PREDICATES = {
    "title": "http://purl.org/dc/terms/title",
    "description": "http://purl.org/dc/terms/description",
    "issued": "http://purl.org/dc/terms/issued",
    "modified": "http://purl.org/dc/terms/modified",
    "identifiers": "http://purl.org/dc/terms/identifier",
    "homepages": "http://xmlns.com/foaf/0.1/homepage",
    "keywords": "http://www.w3.org/ns/dcat#keyword",
    "themes": "http://www.w3.org/ns/dcat#theme",
    "access_rights": "http://purl.org/dc/terms/accessRights",
    "access_urls": "http://www.w3.org/ns/dcat#accessURL",
    "download_urls": "http://www.w3.org/ns/dcat#downloadURL",
    "media_types": "http://www.w3.org/ns/dcat#mediaType",
    "formats": "http://purl.org/dc/terms/format",
    "licenses": "http://purl.org/dc/terms/license",
    "byte_size": "http://www.w3.org/ns/dcat#byteSize",
    "endpoint_urls": "http://www.w3.org/ns/dcat#endpointURL",
    "endpoint_descriptions": "http://www.w3.org/ns/dcat#endpointDescription",
    **PROJECTED,
}
STANDARD_TYPES = {
    "catalogs": "Catalog",
    "datasets": "Dataset",
    "dataset_series": "DatasetSeries",
    "distributions": "Distribution",
    "data_services": "DataService",
}


def rdf_properties(entity):
    return {
        predicate: [
            {key: value for key, value in term.items() if value is not None}
            for term in terms
        ]
        for predicate, terms in entity.get("extra_properties", {}).items()
        if terms
    }


def object_json(entity):
    """Sparse consumer records; raw variants and RDF retain original source terms."""
    result = {
        key: value
        for key, value in entity.items()
        if key not in {"kind", "extra_properties"} and value not in (None, "", [], {})
    }
    properties = rdf_properties(entity)
    for name, predicate in PROJECTED.items():
        values = sorted(
            {
                term.get("id", term.get("value", ""))
                for term in properties.get(predicate, [])
            }
        )
        if values:
            result[name] = values
    for name, predicate in PREDICATES.items():
        if (
            name == "title"
            and entity["kind"] == "publishers"
            and "http://xmlns.com/foaf/0.1/name" in properties
        ):
            predicate = "http://xmlns.com/foaf/0.1/name"
        if name in result:
            properties.pop(predicate, None)
    standard = STANDARD_TYPES.get(entity["kind"])
    if standard and result.get("types") == [f"http://www.w3.org/ns/dcat#{standard}"]:
        result.pop("types")
    properties.pop("http://www.w3.org/1999/02/22-rdf-syntax-ns#type", None)
    if properties:
        result["extra_properties"] = properties
    return result


RELATIONS = {
    "catalogs": {"publisher": ("catalog_publishers", False, "publishers")},
    "dataset_series": {
        "publisher": ("series_publishers", False, "publishers"),
        "catalog": ("catalog_series", True, "catalogs"),
    },
    "datasets": {
        "publisher": ("dataset_publishers", False, "publishers"),
        "catalog": ("catalog_datasets", True, "catalogs"),
        "series": ("series_datasets", True, "dataset_series"),
    },
    "data_services": {
        "publisher": ("service_publishers", False, "publishers"),
        "catalog": ("catalog_services", True, "catalogs"),
        "dataset": ("service_datasets", False, "datasets"),
    },
    "distributions": {"service": ("distribution_services", False, "data_services")},
    "publishers": {},
}


def inline_relations(entity, outgoing, incoming, publishers):
    kind, identifier, result = entity["kind"], entity["id"], {}
    relations = dict(RELATIONS[kind])
    if kind == "distributions":
        relations["dataset"] = ("dataset_distributions", True, "datasets")
    for name, (relation, reverse, _) in relations.items():
        targets = (incoming if reverse else outgoing)[relation, identifier]
        if relation == "service_publishers":
            targets = [
                publishers[value["id"]]
                for value in entity["extra_properties"].get(
                    "http://purl.org/dc/terms/publisher", []
                )
                if value.get("id") in publishers
            ]
        targets = sorted(set(targets))
        if len(targets) == 1 and not (kind == "data_services" and name == "dataset"):
            result[f"{name}_id"] = targets[0]
        elif targets:
            result[f"{name}_ids"] = targets
    return result


def tables_for(state, entities, links, provenance):
    outgoing, incoming, origins = (
        defaultdict(list),
        defaultdict(list),
        defaultdict(list),
    )
    for relation, left, right in links:
        outgoing[relation, left].append(right)
        incoming[relation, right].append(left)
    for kind, identifier, record in provenance:
        if identifier not in entities[kind] or record not in state.records:
            raise ValueError("Broken entity provenance")
        origins[kind, identifier].append(record)
    publishers = {
        entity["uri"]: identifier
        for identifier, entity in entities["publishers"].items()
        if entity.get("uri")
    }
    tables = {}
    # Parents precede their children for SQL foreign keys.
    for kind in (
        "publishers",
        "catalogs",
        "dataset_series",
        "datasets",
        "data_services",
        "distributions",
    ):
        relations = RELATIONS[kind]
        columns = (
            [
                column
                for column in COMMON
                if not (kind == "dataset_series" and column[0] == "types_json")
            ]
            + EXTRA[kind]
            + [("full_json", "TEXT NOT NULL")]
        )
        for name, (_, _, target) in relations.items():
            if kind == "data_services" and name == "dataset":
                columns.append(("dataset_ids_json", "TEXT NOT NULL"))
            else:
                columns.append((f"{name}_id", f"TEXT REFERENCES {target}(id)"))
        if kind == "distributions":
            columns += [
                ("row_id", "TEXT NOT NULL"),
                ("dataset_id", "TEXT REFERENCES datasets(id)"),
            ]
        rows = []
        for identifier, entity in sorted(entities[kind].items()):
            if not identifier or entity["id"] != identifier:
                raise ValueError("Invalid entity identity")
            values = {
                **entity,
                "title": display_title(entity),
                "full_json": json_text(object_json(entity)),
            }
            for name, predicate in PROJECTED.items():
                values[name] = sorted(
                    {
                        term.get("id", term.get("value", ""))
                        for term in entity["extra_properties"].get(predicate, [])
                    }
                )
            values["source_record_ids"] = sorted(origins[kind, identifier])
            variants = {}
            for key in values["source_record_ids"]:
                record = state.records[key]
                item = next(
                    item for item in record.entities if item["id"] == identifier
                )
                variant = variants.setdefault(
                    (record.source, json_text(item)),
                    {"source": record.source, "entity": item, "source_record_ids": []},
                )
                variant["source_record_ids"].append(key)
            values["variants"] = list(variants.values())
            pointers = inline_relations(entity, outgoing, incoming, publishers)
            values.update(pointers)
            values["full_json"] = json_text({**object_json(entity), **pointers})
            parents = (
                sorted(set(incoming["dataset_distributions", identifier]))
                if kind == "distributions"
                else []
            )
            for parent in parents or [None]:
                if kind == "distributions":
                    values.update(
                        row_id=f"{identifier}/{parent or ''}", dataset_id=parent
                    )
                rows.append(
                    tuple(
                        json_text(
                            entity["title"]
                            if name == "title_json"
                            else values.get(name[:-5], [])
                        )
                        if name.endswith("_json") and name != "full_json"
                        else values.get(name)
                        for name, _ in columns
                    )
                )
        keys = ("row_id",) if kind == "distributions" else ("id",)
        tables[kind] = (columns, keys, rows)
    return tables


def populate(connection, tables, *, previous=None):
    """Create once, then delete/upsert only rows whose values changed."""
    changes = {}
    for table, (columns, keys, rows) in tables.items():
        if previous is None:
            definitions = [f'"{name}" {sql}' for name, sql in columns]
            definitions.append("PRIMARY KEY (" + ",".join(keys) + ")")
            connection.execute(f'CREATE TABLE "{table}" ({",".join(definitions)})')
    differences = {}
    for table, (columns, keys, rows) in tables.items():
        positions = [
            next(i for i, (name, _) in enumerate(columns) if name == key)
            for key in keys
        ]

        def indexed(items):
            return {tuple(row[i] for i in positions): row for row in items}

        old = indexed(previous[table][2]) if previous else {}
        current = indexed(rows)
        if len(current) != len(rows):
            raise ValueError(f"Duplicate row identity in {table}")
        differences[table] = (
            old.keys() - current.keys(),
            [row for key, row in current.items() if old.get(key) != row],
        )
    for table in reversed(tables):
        removed, _ = differences[table]
        if removed:
            where = " AND ".join(f'"{key}"=?' for key in tables[table][1])
            connection.executemany(
                f'DELETE FROM "{table}" WHERE {where}', sorted(removed)
            )
    for table, (columns, keys, _) in tables.items():
        removed, rows = differences[table]
        changes[table] = len(removed) + len(rows)
        if not rows:
            continue
        setters = ",".join(
            f'"{name}"=excluded."{name}"' for name, _ in columns if name not in keys
        )
        placeholders = ",".join("?" for _ in columns)
        connection.executemany(
            f'INSERT INTO "{table}" VALUES ({placeholders}) ON CONFLICT ({",".join(keys)}) DO UPDATE SET {setters}',
            rows,
        )
    if previous is None:
        for table in tables:
            connection.execute(f'CREATE INDEX "{table}_uri" ON "{table}" (uri)')
            for name, sql in tables[table][0]:
                if " REFERENCES " in sql:
                    connection.execute(
                        f'CREATE INDEX "{table}_{name}" ON "{table}" ("{name}")'
                    )
    return changes
