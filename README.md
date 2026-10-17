# Dataportal catalog exports

Current Swedish catalog metadata, harvested daily by GitHub Actions. The application
lives in this repository; it is not an installable Python package.

## Products

Browse [catalog JSONL files](data/catalogs/) by name. The current
[manifest](data/manifest.json) lists files, sizes, object counts, source failures,
and freshness. `catalog_files` maps catalog identities to their downloads.

- `catalog.db` (or `.db.xz`): SQLite, with exactly six object tables.
- `parquet/`: the same six tables and columns, one file per table.
- `datasets.jsonl` (possibly `.xz`): datasets with catalog membership and distributions.
- `catalogs.jsonl`, `dataset_series.jsonl`, `distributions.jsonl`,
  `data_services.jsonl`, `publishers.jsonl`: individual object types.
- `catalogs/<name>.jsonl`: one catalog's datasets.
- `jsonld/<name>.jsonld`: JSON-LD derived from retained RDF graphs.
- `source_records/<source>/<catalog>.jsonl.xz`: retained parsed source records,
  source variants, graph descriptions, and fetch/modification timestamps.
- `raw/dataportal.rdf.gz`: the national RDF dump, with its downloaded bytes preserved.

There is no DuckDB database export. DuckDB is used internally to write/read Parquet.
Catalog names are readable labels; original URIs and stable IDs determine identity.
Duplicate labels use source context IDs or numeric suffixes.

## Run

```sh
uv sync --locked
uv run python -m dataportalen_export
uv run python -m dataportalen_export --validate
```

`--force` deliberately refetches unchanged records. `--config path/to/config.toml`
selects another configuration. All operational settings are in root `config.toml`:
formats, sources, exclusions, logging, HTTP concurrency/retries/timeouts, output
directory, compression, Parquet memory limit, and daily timing. Paths resolve
relative to the configuration. GitHub publication uses the configured directory.

The two sources are Dataportal's [documented public catalog API](https://docs.dataportal.se/registry/api/)
and Swedish catalogs in data.europa.eu. Defaults exclude:

- EU `oppnadata`, the aggregate already covered by Dataportal.
- EU `nsip-se` (National Single Information Point Sweden).
- Dataportal context `104`, the four-dataset Hack4heritage registration. Context
  `645` is kept; it contains those four datasets and two more.

Exclusions remove previously accepted records and memberships as well as stopping
retrieval. A transient source failure instead retains its last good data as stale.

## SQLite schema

Exactly six tables: `catalogs`, `datasets`, `dataset_series`, `distributions`,
`data_services`, and `publishers`. Each exposes `id`, original `uri`, a readable
`title`, multilingual `title_json`, `full_json`, `source_record_ids_json`, and
`variants_json`. Unknown or uncommon metadata remains accessible in JSON.
There are no junction, provenance, status, or source-record tables.

| Table | Main queryable fields and pointers |
| --- | --- |
| `catalogs` | Context IDs, description, homepage, dates, publisher |
| `datasets` | Catalog, publisher, series, description, dates, identifiers, keywords, themes, access rights, landing pages, languages, update frequency |
| `dataset_series` | Catalog, publisher, description, identifiers, dates |
| `distributions` | Parent dataset, service, access/download URLs, format, media type, license, byte size, description, dates |
| `data_services` | Catalog, publisher, served dataset IDs, endpoints, service type/protocol |
| `publishers` | Name, original agent/organization types, homepage |

Scalar pointers such as `datasets.publisher_id`, `datasets.catalog_id`,
`datasets.series_id`, and `distributions.dataset_id` have foreign keys and indexes.
A scalar pointer is null if the relationship is absent or has multiple targets;
multiple targets are retained in `full_json` as `<relationship>_ids`.
Services' `dataset_ids_json` retains their many-to-many served-dataset relationships.
No first target is silently selected.

Distributions have one row per dataset/distribution pair. `id` retains the canonical
object identity; `row_id` is the row's primary key. A shared distribution therefore
appears once per parent, preserving its original URI and metadata. An unassigned
distribution has a null `dataset_id`. Object counts in the manifest count distinct
objects; table counts include these parent rows.

```sql
SELECT d.title, c.title AS catalog, p.title AS publisher
FROM datasets d
LEFT JOIN catalogs c ON c.id = d.catalog_id
LEFT JOIN publishers p ON p.id = d.publisher_id;

SELECT d.title, r.access_urls_json, r.download_urls_json, r.formats_json
FROM datasets d
JOIN distributions r ON r.dataset_id = d.id;
```

Dedicated columns follow observed field usage. For example, dataset homepages and
catalog identifiers were entirely empty; publisher dates appeared once each.
These do not receive redundant dedicated columns. Populated rare values remain
in `full_json` and source variants. Sparse JSONL omits empty/null fields, redundant
object `kind`, and an implied standard RDF type. Additional RDF types remain.
Explicit projected fields avoid repeating their predicate values in `extra_properties`;
that map contains unprojected predicates under full IRIs. This is a custom normalized
JSON representation, not the original source format.

`variants_json` retains per-source RDF predicate/term maps and their source-record IDs.
Complete source-record metadata and RDF graphs are in the source exports. Per-record
Turtle and JSON-LD are derived serializations; only the national dump is preserved
as downloaded bytes. Source licenses, attribution, language tags, and unparsed
optional values remain in the retained metadata.

## Refresh and publication

Small public modification/count checks run before loading source records. An
unchanged run updates only the manifest and attempt stamp; it does not load state,
construct exports, hash files, or revalidate accepted products.

Changed contexts scope membership queries and RDF processing. EU records use
catalog-record modification timestamps before fetching descriptions. Shared changed
descriptions invalidate their dependents. A changed national dump is downloaded
and parsed once; its original bytes remain available separately.

Source state is split by source/catalog so only changed groups are serialized and
compressed. SQLite applies row deletions/upserts to a copy of the accepted database;
it does not recreate all tables for a record change. Changed Parquet tables and
JSONL/JSON-LD products are regenerated; unchanged products retain their accepted
bytes. A changed combined JSONL or compressed SQLite file still requires rewriting
that whole file. There are no file-checksum or product-hash registries.

All products are prepared in a temporary directory, checked, and accepted together.
SQLite foreign keys/integrity and row counts, all Parquet files, JSONL, JSON-LD,
and file sizes are validated for changed products. `--validate` checks all files.
An export failure preserves the previous products; publication never force-pushes.

Expected isolated record failures produce warnings and retain existing records with
their old fetch timestamps. Incomplete listings do not authorize deletion. An entire
source outage is a visible failure; validated independent updates may still publish.
After correcting an error, manually dispatch Harvest; do not delete retained state.

GitHub Actions checks the daily gate hourly and harvests after 06:00 Europe/Stockholm
by default. When not due, it installs no harvest dependencies and makes no API calls.
One concurrency group prevents overlapping harvests. The attempt day is separate
from successful source refreshes, preventing hourly retries after partial failures.

Compression is XZ preset 6 by default, with optional gzip. The level is configurable; preset 6 uses less encoder memory than preset 9. Open a compressed database after
unpacking it:

```python
import lzma
import shutil

with (
    lzma.open("data/catalog.db.xz", "rb") as source,
    open("catalog.db", "wb") as target,
):
    shutil.copyfileobj(source, target)
```

Required files must fit GitHub's limit. Only an oversized combined datasets JSONL
may be omitted explicitly; per-catalog files remain available.

## Development

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Tests use external HTTP fixtures and actual exported files. They verify source
selection, failure isolation, identities, RDF topology, sparse projections, six-table
SQLite/Parquet consistency, shared distribution parents, incremental SQLite updates,
and reuse of unrelated products. Hosted runs verify actual source integrations.
