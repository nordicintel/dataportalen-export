# Replacement plan: daily JSONL scrape with optional SQLite

Replace the existing implementation with a direct adaptation of your [script](https://gist.github.com/rubenselander/d7f55c2d56e483db6c4417d44071a601) and the pasted SQL schema.

The scraper produces one complete `datasets.jsonl.gz` every day. The database builder reads that file and creates `catalog.db.gz`. There is no incremental harvesting, content comparison, retained source state, or export framework.

## 1. Delete the existing application and obsolete outputs

The first implementation commit removes:

- The entire `dataportalen_export/` package.
- Existing tests and RDF fixtures.
- Existing harvest/check workflows and `.github/last_attempt.json`.
- All existing generated files under `data/`.
- Old schema audits, implementation-specific documentation, and obsolete issue entries.
- Dependencies for HTTPX, RDFLib, Pydantic, and DuckDB.
- The old plan’s requirements, replacing them with this plan.

Preserve `.git`, ordinary Git history, `LICENSE`, repository settings, and applicable agent instructions. Rewrite the README, dependency configuration, and root `config.toml`.

**Do not migrate the old exported data or retain compatibility code.** The first new scrape supplies the new JSONL; the new database is built entirely from that JSONL.

The resulting working structure:

```text
.github/workflows/
    checks.yml
    harvest.yml
    database.yml

scripts/
    scrape.py
    build_database.py

tests/
    fixtures/records.jsonl
    test_scrape.py
    test_database.py

config.toml
database.schema.sql
pyproject.toml
uv.lock
README.md
LICENSE

data/
    datasets.jsonl.gz
    catalog.db.gz          # optional
```

Keep dependency-only uv configuration. This remains repo-local code, with no installable package or build backend.

## 2. Adapt the scraper directly

Use the gist’s functions and straightforward control flow rather than rebuilding a class hierarchy.

### Source and record handling

- Query `https://data.europa.eu/api/hub/search/search`.
- Use `filter=dataset`, `scroll=true`, and configurable page size (default **500**). Catalog IDs default to **`oppnadata`**; configured country IDs are queried separately and unioned, deduplicating by API dataset ID.
- Request complete records: **no `includes` projection**.
- Follow `/scroll` until it returns no records.
- Use an updated scroll ID when a response supplies one; otherwise continue with the existing ID.
- Keep recursive language-map filtering with configurable retained languages (default Swedish/English); an empty language list keeps all languages.
- Preserve everything else returned by the API, including nested distributions, data services, contacts, catalog information, and unfamiliar properties.
- Do not apply the previous project’s source exclusions, field-removal lists, frequency thresholds, URI reconciliation, or publisher pruning.
- Do not download underlying dataset files.

Replace unused source constants with the catalog/country selections in root `config.toml`; document union semantics.

### Requests

- Keep `requests`; configure timeout (default **120 seconds**), total attempts (default **five**), retry backoff, and successful-page delay (default zero) in TOML.
- Reuse one `requests.Session`.
- Remove the unconditional half-second delay between successful pages. Backoff applies to failed requests.
- Log page progress and the final number of records.
- Let an exhausted request failure terminate the run visibly.

### Writing the output

Stream records directly through `gzip.open(..., "wt", encoding="utf-8")`, one JSON object per line.

Every successful run replaces `data/datasets.jsonl.gz` completely:

- No modification-date checks.
- No comparison with the old file.
- No per-record reuse.
- No manifest, checksums, snapshots, or historical output directories.
- No post-scrape reconstruction of records.

Write to one temporary gzip file and rename it to the final filename when the stream finishes. This is a single-file replacement, not a staging framework. Failed requests do not publish a truncated gzip file.

### Configuration

Root `config.toml` controls catalog IDs, country IDs, language keys, page size,
request attempts, timeout, retry backoff, page delay, gzip level, logging level,
automatic schedule enablement, interval in days, local time, IANA timezone,
automatic database creation, and gzip level. Defaults are recorded in that file.
An empty language list keeps all languages. Catalog/country selections form a union.

`database.enabled` controls whether the daily harvest also builds SQLite. It does
not disable the standalone database command or manual workflow. Output filenames
remain fixed. Use ordinary dictionaries and standard-library TOML parsing.

## 3. Use the defined database schema in `database.schema.sql`

`database.schema.sql` has already been written and verified to execute in SQLite.
Use it directly as the authoritative schema, with five principal tables:

1. `catalogs`
2. `publishers`
3. `datasets`
4. `distributions`
5. `data_services`

The nine supporting tables become JSON columns on their parent objects.

### Language fields

Replace language-specific columns with a single JSON object column:

| Pasted columns                                   | Replacement          |
| ------------------------------------------------ | -------------------- |
| `title_sv`, `title_en`                           | `title`              |
| `description_sv`, `description_en`               | `description`        |
| `corporate_body_sv`, `corporate_body_en`         | `corporate_body`     |
| `version_notes_sv`, `version_notes_en`           | `version_notes`      |
| `label_sv`, `label_en` in nested objects         | `label`              |
| `sv_machine_translated`, `en_machine_translated` | `machine_translated` |

Examples:

```json
{ "sv": "Svensk titel", "en": "English title" }
```

```json
{ "sv": false, "en": true }
```

Declare every JSON-bearing column explicitly as `JSON`, not `TEXT`. The SQL file
currently defines 34 such columns, including language maps, embedded collections,
URL objects, and `record_json`. Serialize values as valid JSON when inserting them.
Preserve whichever language keys exist; do not invent translations or fill absent
languages with empty strings. Missing maps default to `{}` and missing collections
to `[]`; distribution `urls` defaults to an object with empty `access`, `download`,
and `service_endpoint` arrays.

Apply the same convention inside embedded objects such as categories and landing pages.

### Embed supporting tables

| Remove table        | Store on parent           |
| ------------------- | ------------------------- |
| `keywords`          | `datasets.keywords`       |
| `categories`        | `datasets.categories`     |
| `hvd_categories`    | `datasets.hvd_categories` |
| `temporal`          | `datasets.temporal`       |
| `creators`          | `datasets.creators`       |
| `landing_pages`     | `datasets.landing_pages`  |
| `contact_points`    | `datasets.contact_points` |
| `dataset_links`     | `datasets.links`          |
| `distribution_urls` | `distributions.urls`      |

Collections remain **JSON arrays of objects**, because a dataset can have several contacts, keywords, or temporal intervals. Language maps remain JSON objects.

For example:

```json
[
    {
        "id": "ENVI",
        "label": { "sv": "Miljö", "en": "Environment" }
    }
]
```

Distribution URLs become one structured value:

```json
{
    "access": ["https://example.org/api"],
    "download": ["https://example.org/data.csv"],
    "service_endpoint": ["https://example.org/service"]
}
```

Do not keep separate “first URL” columns duplicating these collections.

Likewise, replace the pasted schema’s comma/semicolon-separated collection strings with JSON arrays for fields such as identifiers, languages, endpoint URLs, documentation URLs, and applicable legislation. Preserve array elements rather than joining and subsequently splitting strings.

### Principal table contents

- **Catalogs:** ID, multilingual title/description, homepage, publisher details, country, source type, parent catalog, issued/modified.
- **Publishers:** source resource URI, name, type, email, homepage.
- **Datasets:** the pasted dataset fields, consolidated language fields, embedded supporting collections, and `record_json`.
- **Distributions:** the pasted distribution fields, consolidated language fields, structured URLs and other collections, plus the parent dataset reference.
- **Data services:** the pasted service fields, consolidated language fields, JSON URL collections, parent distribution/dataset references, and `record_json`.

Retain complete dataset records in `datasets.record_json`. This preserves API information that has no dedicated column without inventing an `extra_properties` transformation.

### Identity and relationships

- Keep the API’s dataset `resource` and `id`.
- Use catalog IDs and publisher resource URIs supplied by the API.
- Do not merge publishers by name. If a publisher has no resource URI, preserve its details on the dataset record and leave the publisher reference null.
- Give distribution rows a simple integer primary key and retain the API distribution identifier separately as `source_id`. This allows the same source distribution to occur under multiple datasets without a collision or custom hash.
- Give data-service rows an integer primary key and reference their distribution row and dataset.
- Store `spatial_resolution_m` as `REAL` to preserve fractional values.
- Retain foreign keys and the useful indexes on catalog, publisher, parent dataset, distribution, and distribution format.
- Remove indexes belonging to deleted supporting tables.

The SQL file is the sole definition of tables and its six indexes. Do not duplicate
DDL in Python or build a schema-generation system. Schema creation is already done;
the remaining implementation must consume this file rather than redesign it.

## 4. Implement one standalone database builder

Command:

```sh
uv run python scripts/build_database.py
```

Its operation is sequential:

1. Check that `data/datasets.jsonl.gz` exists.
2. Create a fresh temporary SQLite database.
3. Execute `database.schema.sql`.
4. Read the gzip JSONL line by line.
5. Insert catalog/publisher records and each dataset’s distributions/services.
6. Commit and close SQLite.
7. Compress the database with **gzip** to `data/catalog.db.gz`.
8. Replace the previous compressed database and remove temporary files.

Use `sqlite3`, `gzip`, `json`, and `pathlib` from the standard library. Use configurable gzip level **6** initially.

The builder:

- Performs **no HTTP requests**.
- Does not import or invoke the scraper.
- Always rebuilds from scratch.
- Uses explicit field mappings and a few small JSON helpers.
- Uses transactions and parameterized SQL.
- Reports inserted row counts.
- Does not add models, ORM classes, adapters, migration machinery, or incremental upserts.

Missing input produces a clear failure:

```text
Missing data/datasets.jsonl.gz. Run the Harvest workflow first.
```

Malformed input or a database error fails the build and leaves the previous compressed database intact. Optional source fields become SQL `NULL` or empty JSON collections as appropriate; do not silently skip whole datasets because an optional field is absent.

## 5. Replace GitHub Actions with simple workflows

### Daily harvest

`harvest.yml` has:

- One daily cron: **`0 6 * * *`** with native **`timezone: Europe/Stockholm`**, generated from root configuration by `python scripts/scrape.py --sync-schedule`. Checks verify the workflow stays in sync. No hourly runs.
- Manual dispatch.
- Checkout, Python/uv setup, locked dependencies, scraper, optional database build, commit/push.
- A single shared publication concurrency group with cancellation disabled.

Remove the hourly schedule gate, timezone scheduling module, last-attempt file, source checkpoints, and no-change logic.

On the daily trigger, use the configured day interval and latest commit touching the JSONL to decide whether to run. A due run scrapes everything. The schedule check is standard-library-only and installs no harvest dependencies when skipped. Manual dispatch bypasses the gate.

After a successful scrape, read `database.enabled` using standard-library TOML parsing:

- `true`: run the database builder.
- `false`: publish the new JSONL without rebuilding the database.

If the optional database build fails, publish the successfully completed JSONL, retain the previous database, and leave the workflow visibly failed. The two files’ Git history makes their respective update times visible; no manifest is introduced.

Stage only the expected generated filenames. Use a normal commit and push—never force-push or overwrite concurrent repository changes.

### Manual database rebuild

`database.yml` has `workflow_dispatch` and always runs the database builder, regardless of `database.enabled`.

It:

- Checks out the latest branch.
- Fails clearly if the gzip JSONL does not exist.
- Builds exclusively from the checked-out JSONL.
- Publishes only `catalog.db.gz`.
- Uses the same publication concurrency group as the harvest.

This provides the requested database recreation without rerunning the scrape.

### Checks

Keep a short workflow for Ruff and the small fixture tests. Trigger it for code, SQL, configuration, dependency, and workflow changes; generated data commits should not trigger it.

No test matrix, full network harvest in pull requests, or separate deployment workflow.

## 6. Test the replacement and finish the cleanup

Write the small behavioral tests before implementing their corresponding code.

### Scraper tests

Use compact mocked HTTP pages to verify:

- Complete records are retained without an `includes` filter.
- Pagination handles both repeated and updated scroll IDs.
- Nested language maps retain `sv`/`en`, while ordinary objects such as temporal `gte`/`lte` remain intact.
- The output is valid gzip JSONL.
- A later successful run replaces earlier records rather than appending.
- A failed request does not replace the completed output.

### Database tests

Use one small JSONL fixture containing multilingual fields, several nested collections, multiple distributions, and an embedded data service.

Verify:

- Exactly five tables exist.
- There are no `_sv`/`_en` columns or supporting collection tables.
- All 34 JSON-bearing columns have declared type `JSON`, as exposed by SQLite table metadata.
- Language maps and embedded collections survive SQL insertion.
- Dataset, distribution, and service parent references work.
- The `.db.gz` decompresses into a readable SQLite database.
- Missing gzip input produces the explicit error.
- Rebuilding removes records absent from the new JSONL.

Do not carry over the old test suite or test implementation-private helpers.

### Hosted acceptance

After the fixture tests and Ruff pass:

1. Run the new harvest once in GitHub Actions.
2. Open the resulting gzip JSONL and confirm it contains complete records from the configured scope.
3. Run the manual database workflow with automatic database generation disabled, proving that it works independently.
4. Decompress the database, query the five tables, and inspect multilingual and embedded JSON values.
5. Record actual elapsed time, record counts, and compressed file sizes in the completion report.
6. Finish the README with the two commands, configuration flag, output filenames, manual workflow instructions, and a small `json_extract()` query example.

**The end product is two straightforward scripts, one explicit SQL schema, one daily scrape, and an optional independently rebuildable database.**
