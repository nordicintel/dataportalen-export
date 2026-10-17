# Dataportal catalog exports

Current Swedish catalog metadata in JSONL, SQLite, DuckDB, Parquet, and JSON-LD.
The daily GitHub Actions harvest combines Dataportal's national RDF dump and
underlying organization catalogs with other Swedish catalogs from data.europa.eu.
The EU `oppnadata` aggregate is excluded because Dataportal supplies its records.
Distribution URLs describe the underlying data; the harvester does not download
those data files.

## Current products

All current products live in [`data/`](data/). Read `data/manifest.json` for the
actual filenames, sizes, checksums, counts, source outcomes, and freshness.

| Product | Use |
| --- | --- |
| `datasets.jsonl` | One canonical dataset per line, including distributions, catalog IDs, and source-record IDs |
| `catalogs.jsonl`, `dataset_series.jsonl`, `distributions.jsonl`, `data_services.jsonl`, `publishers.jsonl` | Separate object types |
| `catalogs/<id>/datasets.jsonl` | Datasets belonging to one underlying catalog |
| `catalog.db` | SQLite with explicit tables and relationships |
| `catalog.duckdb` | Equivalent DuckDB tables |
| `parquet/<table>.parquet` | One Parquet file per logical table |
| `jsonld/<catalog-id>.jsonld` | Embedded JSON-LD context and source-qualified named graphs |
| `source_records.jsonl.xz` | Complete current source variants, RDF, and incremental state |
| `raw/dataportal.rdf.gz` | Original national RDF dump, including material outside extracted entities |

Files above the configured size threshold use XZ compression by default;
`output.compression` can select `xz` or `gzip`, and `compression_level` selects
0–9 (default 6). Decompress a `.db.xz` or
`.duckdb.xz` before opening it. Python's standard-library `lzma` reads XZ files;
`gzip` reads the optional `.gz` alternative. Compression changes no database
tables, indexes, relationships, or metadata. The manifest names the actual files.
If the combined datasets JSONL is still too large after compression, it is
omitted explicitly and per-catalog files remain.
Other required files exceeding GitHub's limit prevent publication.

```python
import lzma
import json

with lzma.open("data/source_records.jsonl.xz", "rt", encoding="utf-8") as records:
    first = json.loads(next(records))
    print(first["source"], first["root_uri"], first["stale"])
```

## Run and configure

The application lives in this repository and is not installed as a Python
distribution. Dependencies and their resolved versions are managed by uv.

```sh
uv sync --locked
uv run python -m dataportalen_export
uv run python -m dataportalen_export --force
uv run python -m dataportalen_export --validate
```

Operational settings live in [`config.toml`](config.toml): active formats,
source selection, concurrency, timeouts, retry attempts, logging levels,
output path, compression method/threshold, and daily time/timezone. Paths resolve relative to
the configuration file, independently of the current working directory.
`--config` selects another TOML file for a deliberate separate run.

Unattended execution uses **GitHub-hosted Actions only**. The Harvest workflow
wakes hourly at minute 17. A standard-library-only check reads TOML and runs
the daily harvest after 06:00 Europe/Stockholm by default. GitHub can delay
scheduled starts. The check skips dependency installation and network requests
when no harvest is due. Manual workflow dispatch bypasses the time gate and
can request a forced refresh.

`.github/last_attempt.json` records the latest attempted day, including failures;
it prevents repeated full harvests every hour. It is separate from the data
manifest, which describes the last published product set. Only current filenames
are maintained; ordinary Git history still contains earlier commits.

## Data and identity

Six entity types are modeled: catalogs, datasets, dataset series, distributions,
data services, and publishers. Publisher RDF types remain intact: an agent is
not automatically asserted to be an organization.

IDs are stable SHA-256 digests of typed identities. Original URIs and native
source identifiers remain in the products. Dataset identity uses exact original
URIs when available. Catalog IDs include their source/context identity, never a
display title. Multiple catalog memberships are preserved. Titles, shared URLs,
and UUIDs alone do not merge datasets. Exact matches prefer Dataportal for the
default representation, while all complete source variants remain available.

Text fields are language-to-list mappings, including `und` for untagged text.
Arrays remain arrays for one or many values. All original language tags survive.
`extra_properties` retains RDF properties by full predicate IRI; literal objects
retain their text, datatype, and language, and resource objects retain references.
The original RDF preserves graph detail, including supporting blank nodes.
Dates accept ISO years (`2022`), year-months (`2006-09`), full dates, and timestamps.
Reduced precision stays intact; timestamps use `T` and explicit supplied offsets.
Swedish written dates such as `10 juni 2020` normalize to `2020-06-10`.
Byte counts accept space-grouped digits such as `155 648`, including nonbreaking spaces.
Unparseable optional dates or numbers have null typed projections while their
source values remain available. Source licenses and attribution are preserved;
the code's MIT license does not replace the licenses of harvested metadata.

## Database schema

The six entity tables are `catalogs`, `datasets`, `dataset_series`,
`distributions`, `data_services`, and `publishers`. Each has:

| Column | Meaning |
| --- | --- |
| `id` | Non-null primary key |
| `uri` | Original/canonical URI; null for a blank-node entity |
| `title_json`, `description_json` | Non-null language maps |
| `issued`, `modified` | Nullable ISO year, year-month, date, or date-time projections |
| `types_json`, `identifiers_json`, `homepages_json` | Non-null JSON arrays |
| `full_json` | Complete convenient entity representation and RDF properties |

Additional columns are `context_ids_json` on catalogs;
`keywords_json`, `themes_json`, `access_rights_json` on datasets;
`access_urls_json`, `download_urls_json`, `media_types_json`, `formats_json`,
`licenses_json`, and nullable numeric `byte_size` on distributions;
and `endpoint_urls_json`, `endpoint_descriptions_json` on services.

Relationship tables use non-null `left_id` and `right_id`, a composite primary
key, and foreign keys to the named entities:

| Table | Left → right |
| --- | --- |
| `catalog_datasets` | catalog → dataset |
| `catalog_series` | catalog → series |
| `catalog_services` | catalog → service |
| `series_datasets` | series → dataset |
| `dataset_distributions` | dataset → distribution |
| `distribution_services` | distribution → service |
| `service_datasets` | service → dataset |
| `catalog_publishers`, `dataset_publishers`, `series_publishers` | named entity → publisher |

`source_records` stores `id`, `source`, `native_id`, `scope`, `root_uri`,
`stamp`, `digest`, `fetched_at`, `stale`, `rdf`, and `full_json` (all non-null).
Its JSON contains the source-specific entities and links; RDF is stored in its
own column. `entity_sources(kind, entity_id, source_record_id)` links every
entity to its variants. Polymorphic entity references are checked before export.

`harvest_status` contains one row per scope: `scope`, `source`, `record_count`,
`stale_count`, and `last_successful_fetch`. This timestamp describes retained
content; the manifest records the latest source check/attempt separately.

```sql
-- Datasets and every underlying catalog membership.
SELECT d.uri, d.title_json, c.title_json AS catalog_title
FROM datasets d
JOIN catalog_datasets cd ON cd.right_id = d.id
JOIN catalogs c ON c.id = cd.left_id;

-- Source freshness for each dataset variant.
SELECT d.uri, s.source, s.fetched_at, s.stale
FROM datasets d
JOIN entity_sources es ON es.entity_id = d.id AND es.kind = 'datasets'
JOIN source_records s ON s.id = es.source_record_id;
```

## Failure handling and recovery

Transient HTTP failures receive bounded retries. A failed existing record keeps
its last good version and successful-fetch timestamp; a new unusable record is
reported and skipped. Incomplete listings suppress deletion in their scope.
Confirmed disappearance removes only the affected source membership/variant.
One unavailable source does not block successful independent sources.

Expected isolated failures appear as warnings and stale/skipped counts. Broken
configuration, no successfully refreshed source, unusable first-run data,
integrity errors, oversized required exports, or a failed push are visible
workflow failures. Candidate files are checked before replacing current outputs.
The complete accepted set is published in one commit, never with a force-push.

Inspect the Actions summary and manifest on failure. Correct configuration or
source compatibility issues, then dispatch Harvest manually. Use `force` when
intentionally rebuilding retained source projections after a schema change.
Do not delete retained state to resolve a transient failure.

If export generation fails in Actions, the `failed-harvest-state` artifact retains
the source state, original dump, and any compressed SQLite candidate for one day.
These diagnostics are not published under `data/`; they let an export fix reuse
already retrieved metadata. Download the artifact from the failed run before it
expires. Size-limit errors include the exact compressed byte count.

## Development checks

```sh
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

Tests use a small RDF fixture, external HTTP fixtures, and actual exported files.
They are separate from full live verification in GitHub Actions. Add a failing
behavioral test before a meaningful change, then implement only that behavior.
