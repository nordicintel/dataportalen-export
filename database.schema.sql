-- SQLite schema for the records in datasets.jsonl.gz.
-- JSON columns contain language-map objects or collection arrays.
-- Language maps contain available sv/en entries, without invented translations.
-- Nested objects retain their own language maps and source identifiers.

PRAGMA foreign_keys = ON;

CREATE TABLE catalogs (
    id               TEXT PRIMARY KEY NOT NULL,
    title            JSON NOT NULL DEFAULT '{}', -- JSON language map
    description      JSON NOT NULL DEFAULT '{}', -- JSON language map
    homepage         TEXT,
    publisher_name   TEXT,
    publisher_email  TEXT,
    country          TEXT,
    source_type      TEXT,
    is_part_of       TEXT,
    issued           TEXT,
    modified         TEXT,
    languages        JSON NOT NULL DEFAULT '[]',
    publisher        JSON NOT NULL DEFAULT '{}',
    record_json      JSON NOT NULL CHECK (json_valid(record_json))
);

CREATE TABLE publishers (
    resource  TEXT PRIMARY KEY NOT NULL,
    name      TEXT,
    type      TEXT,
    email     TEXT,
    homepage  TEXT
);

CREATE TABLE datasets (
    resource                 TEXT PRIMARY KEY NOT NULL,
    id                       TEXT NOT NULL UNIQUE,
    identifiers              JSON NOT NULL DEFAULT '[]', -- JSON array
    catalog_id               TEXT REFERENCES catalogs(id),
    publisher_resource       TEXT REFERENCES publishers(resource),
    publisher_name           TEXT,
    corporate_body_id        TEXT,
    corporate_body           JSON NOT NULL DEFAULT '{}', -- JSON language map
    title                    JSON NOT NULL DEFAULT '{}', -- JSON language map
    description              JSON NOT NULL DEFAULT '{}', -- JSON language map
    machine_translated       JSON NOT NULL DEFAULT '{}', -- JSON {"sv":false,"en":true}
    issued                   TEXT,
    modified                 TEXT,
    catalog_record_issued    TEXT,
    catalog_record_modified  TEXT,
    access_right             TEXT,
    accrual_periodicity      TEXT,
    is_hvd                   INTEGER CHECK (is_hvd IN (0, 1)),
    quality_score            INTEGER,
    languages                JSON NOT NULL DEFAULT '[]', -- JSON array
    version_info             TEXT,
    version_notes            JSON NOT NULL DEFAULT '{}', -- JSON language map
    temporal_resolution      TEXT,
    spatial_resolution_m     REAL,
    distribution_count       INTEGER NOT NULL,
    available_languages      JSON NOT NULL DEFAULT '[]', -- JSON array
    country                  TEXT,
    type                     JSON NOT NULL DEFAULT '{}',
    spatial_resources        JSON NOT NULL DEFAULT '[]',
    applicable_legislation   JSON NOT NULL DEFAULT '[]',
    topic_categories         JSON NOT NULL DEFAULT '[]',
    reference_systems        JSON NOT NULL DEFAULT '[]',
    in_series                JSON NOT NULL DEFAULT '[]',
    spatial_geojson          JSON NOT NULL DEFAULT '[]', -- JSON array of geometries
    keywords                 JSON NOT NULL DEFAULT '[]', -- JSON [{id,language,label}]
    categories               JSON NOT NULL DEFAULT '[]', -- JSON [{id,label:{sv,en},...}]
    hvd_categories           JSON NOT NULL DEFAULT '[]', -- JSON [{id,label:{sv,en},...}]
    temporal                 JSON NOT NULL DEFAULT '[]', -- JSON array of intervals
    creators                 JSON NOT NULL DEFAULT '[]', -- JSON array of agents
    landing_pages            JSON NOT NULL DEFAULT '[]', -- JSON array of page objects
    contact_points           JSON NOT NULL DEFAULT '[]', -- JSON array of contact objects
    links                    JSON NOT NULL DEFAULT '[]', -- JSON [{kind,url,label,...}]
    record_json              JSON NOT NULL CHECK (json_valid(record_json))
);

-- A source distribution can occur under more than one dataset.
-- id identifies this parent-specific row; source_id preserves the API identifier.
CREATE TABLE distributions (
    id                        INTEGER PRIMARY KEY,
    source_id                 TEXT,
    dataset_resource          TEXT NOT NULL REFERENCES datasets(resource),
    title                     JSON NOT NULL DEFAULT '{}', -- JSON language map
    description               JSON NOT NULL DEFAULT '{}', -- JSON language map
    format                    TEXT,
    format_label              TEXT,
    media_type                TEXT,
    license                   TEXT,
    license_id                TEXT,
    license_description       TEXT,
    urls                      JSON NOT NULL DEFAULT '{"access":[],"download":[],"service_endpoint":[]}',
    issued                    TEXT,
    modified                  TEXT,
    status                    TEXT,
    availability              TEXT,
    languages                 JSON NOT NULL DEFAULT '[]', -- JSON array
    byte_size                 INTEGER,
    checksum_algorithm        TEXT,
    checksum_value            TEXT,
    rights                    TEXT,
    temporal_resolution       TEXT,
    representation_technique  JSON NOT NULL DEFAULT '[]', -- JSON array
    conforms_to               JSON NOT NULL DEFAULT '[]', -- JSON array
    pages                     JSON NOT NULL DEFAULT '[]', -- JSON array
    applicable_legislation    JSON NOT NULL DEFAULT '[]', -- JSON array
    spatial_resolution_m      REAL,
    record_json               JSON NOT NULL CHECK (json_valid(record_json)),
    data_service_count        INTEGER NOT NULL
);

CREATE TABLE data_services (
    id                     INTEGER PRIMARY KEY,
    distribution_id        INTEGER NOT NULL REFERENCES distributions(id),
    dataset_resource       TEXT NOT NULL REFERENCES datasets(resource),
    title                  JSON NOT NULL DEFAULT '{}', -- JSON language map
    description            JSON NOT NULL DEFAULT '{}', -- JSON language map
    endpoint_urls          JSON NOT NULL DEFAULT '[]', -- JSON array
    endpoint_descriptions  JSON NOT NULL DEFAULT '[]', -- JSON array
    documentation_urls     JSON NOT NULL DEFAULT '[]', -- JSON array
    serves_dataset_count   INTEGER NOT NULL,
    license_id             TEXT,
    access_right           TEXT,
    contact_points         JSON NOT NULL DEFAULT '[]',
    serves_datasets        JSON NOT NULL DEFAULT '[]',
    license                JSON NOT NULL DEFAULT '{}',
    contact_name           TEXT,
    contact_email          TEXT,
    record_json            JSON NOT NULL CHECK (json_valid(record_json))
);

CREATE INDEX datasets_publisher ON datasets(publisher_resource);
CREATE INDEX datasets_catalog ON datasets(catalog_id);
CREATE INDEX distributions_dataset ON distributions(dataset_resource);
CREATE INDEX distributions_format ON distributions(format);
CREATE INDEX data_services_dataset ON data_services(dataset_resource);
CREATE INDEX data_services_distribution ON data_services(distribution_id);
