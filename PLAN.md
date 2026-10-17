# Implementation plan: daily Dataportal catalog exports

Build a minimal, repo-local Python application from the reviewed reference code. Harvest Dataportal’s underlying catalogs plus other Swedish catalogs in data.europa.eu, preserve source metadata, and publish the current exports into the repository’s `/data`.

Work sequentially without subagents. Write a small failing behavioral test before implementing each meaningful capability. Finish with a full GitHub-hosted harvest, verify its actual outputs, and make the repository public only after acceptance passes.

## Part 1 — Establish the application and configuration

**Tests first**

Test valid configuration, invalid settings, and path resolution. Add one small RDF fixture that will become the end-to-end test input.

**Implementation**

- Use a single importable `dataportalen_export` directory, with one file per substantial class and ordinary functions for small transformations.
- Provide `uv run python -m dataportalen_export` as the entry point.
- Use dependency-only uv configuration and a committed lockfile. Do not create an installable distribution, build backend, wheel, or publishing workflow.
- Keep runtime dependencies to HTTPX, RDFLib, Pydantic, and DuckDB. Use standard-library SQLite, TOML parsing, logging, gzip, and timezone handling.
- Use pytest and Ruff for development. Avoid an ORM, plugin framework, custom scheduler service, or dataframe dependency.

Root `config.toml` controls:

| Setting                        | Initial default                                |
| ------------------------------ | ---------------------------------------------- |
| Sources                        | Dataportal and Swedish data.europa.eu catalogs |
| EU aggregate exclusion         | `oppnadata`                                    |
| Daily harvest                  | Enabled, after 06:00 Europe/Stockholm          |
| Logging                        | INFO; HTTP logging WARNING                     |
| Request concurrency            | 8                                              |
| Request attempts               | 5                                              |
| Request/download timeouts      | 60 / 600 seconds                               |
| JSONL, SQLite, DuckDB, Parquet | Enabled                                        |
| JSON-LD                        | Enabled, configurable                          |
| Output directory               | `data`                                         |
| Gzip threshold                 | 95 MiB                                         |

Configuration errors fail before network access. Resolve paths relative to the repository. Keep operational settings out of competing CLI flags; retain only explicit manual controls such as forcing a refresh.

**Complete when:** the command loads validated configuration and processes the fixture without installing the repository as a package.

## Part 2 — Implement reliable source retrieval

**Tests first**

Use compact HTTP fixtures to cover pagination, transient retries, incomplete listings, repository-only datasets, missing distribution descriptions, and multiple catalog memberships. Exercise DatasetSeries discovery in the same fixtures.

Mock external HTTP responses, not our own classes.

**Implementation**

Preserve the useful capabilities already present in the reference:

- Download and parse Dataportal’s national RDF dump.
- Resolve underlying catalogs and memberships through EntryScape.
- Discover other Swedish EU catalogs.
- Reconcile EU search results with repository listings.
- Fetch referenced distribution descriptions missing from dataset RDF.
- Bound concurrent requests and retry transient failures.

Correct the reference’s assumptions:

- Catalog titles are labels, never identifiers.
- One dataset can belong to multiple catalogs.
- Context identity and catalog-resource identity remain distinct.
- DatasetSeries participate in discovery and membership resolution.
- Missing catalog metadata does not invalidate every dataset in that catalog.
- An incomplete listing never authorizes deletion.

Each source produces source records plus explicit discovery-completeness information. A failure in one source must not stop the other source.

**Complete when:** both integrations produce the same internal record contract, and a failed request or incomplete listing remains isolated.

## Part 3 — Define models, identity, and incremental refresh

**Tests first**

Extend the representative fixture to cover:

- Exact-URI identity and conflicting source variants.
- Multiple memberships and shared related objects.
- Unknown properties, all languages, and malformed optional values.
- Blank-node cycles and topology-sensitive change detection.
- Reusing unchanged records, retaining failed records, and confirmed removals.

Use these fixtures across conversion and refresh tests rather than creating a large collection of narrowly duplicated tests.

**Implementation**

Create six principal Pydantic models:

1. Catalog.
2. Dataset.
3. Dataset series.
4. Distribution.
5. Data service.
6. Publisher, preserving whether the source describes an organization or another agent type.

Use a new documented JSON contract:

- Stable IDs, original URIs, original RDF types, memberships, and provenance.
- Predictable arrays for collection fields.
- Language-to-list mappings, preserving original language tags.
- Explicit common fields.
- Unknown properties retained under full predicate IRIs.
- Invalid optional values retained in source metadata, with null typed projections and a warning.

Preserve original RDF and complete source variants. Generate JSON-LD from RDF rather than reconstructing it from simplified JSON.

Identity rules are deliberately conservative:

- Match datasets across sources using exact original URIs.
- Otherwise use source-qualified native identifiers.
- Never merge by title, publisher name, shared endpoint, or UUID alone.
- Never lowercase URI paths or use lossy slugs as identity.
- Prefer Dataportal for the default representation while retaining alternate source records.
- Preserve unresolved catalog membership explicitly instead of inventing a national-catalog membership.

Reuse unchanged records using source modification signals and correct RDF graph comparison. Rebuild exports from the accepted current state.

Apply these failure rules:

| Failure                                     | Result                                                                                       |
| ------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Invalid optional field                      | Preserve original value; continue                                                            |
| Existing record cannot refresh              | Retain its last good version and mark stale                                                  |
| New unusable record                         | Report and skip its structured representation                                                |
| Incomplete discovery                        | Preserve previous memberships; suppress deletions                                            |
| Entire source unavailable                   | Retain its previous state; continue other sources                                            |
| Confirmed disappearance                     | Remove that source membership; remove an object only when no surviving reference requires it |
| Unexpected programming or integrity failure | Fail visibly; preserve published products                                                    |

Do not assign fresh timestamps to stale content. Keep failures and source timestamps in the current manifest, without accumulating historical reports.

**Complete when:** repeated runs converge, changed records refresh, and one bad record cannot cause cascading loss.

## Part 4 — Implement the database and export products

**Tests first**

Run the fixture through each exporter, then inspect the products as a consumer:

- Query SQLite and DuckDB.
- Read Parquet tables.
- Parse combined, entity, and per-catalog JSONL.
- Parse JSON-LD and compare the represented RDF graph.
- Check that a failed exporter leaves published files unchanged.

Reuse one expected dataset across formats. Do not test private methods or reproduce implementation logic in assertions.

**Implementation**

SQLite and DuckDB expose the same logical schema:

- Six entity tables.
- Explicit membership/link tables for catalogs, datasets, series, distributions, services, and publishers.
- Source records and entity provenance.
- Current harvest status.

Use primary keys, relationship uniqueness, foreign keys where applicable, and indexes on IDs and join columns. Preserve complete JSON alongside useful queryable columns. Validate polymorphic provenance references before publication.

Write one Parquet file per logical table through DuckDB. Keep table definitions and row projections shared, with small explicit differences between SQL engines.

Produce:

| Product                            | Contents                                                 |
| ---------------------------------- | -------------------------------------------------------- |
| `datasets.jsonl`                   | Combined canonical dataset records                       |
| Object-type JSONL files            | Catalogs, series, distributions, services, publishers    |
| Per-catalog JSONL                  | Each catalog’s member datasets                           |
| `catalog.db`                       | SQLite database                                          |
| `catalog.duckdb`                   | DuckDB database                                          |
| Parquet directory                  | One file per logical table                               |
| Per-catalog JSON-LD                | Context and graph-based metadata                         |
| Compressed source records          | Current raw variants and incremental state               |
| Compressed original Dataportal RDF | Full source preservation                                 |
| `manifest.json`                    | Counts, freshness, failures, filenames, sizes, checksums |

Retained source records provide incremental state even when database exports are disabled. Do not introduce a separate cache database.

Keep exports deterministic. Do not rewrite unchanged record timestamps; retain unchanged binary products when their logical contents and schema are unchanged.

Build all products in a temporary working directory and validate them before publication. Commit the accepted set together.

Use gzip when necessary. If the largest combined JSONL still exceeds GitHub’s limit after compression, omit it and report that explicitly while keeping the split files. Other required oversized products block publication rather than silently disappearing.

**Complete when:** every enabled format represents the same accepted state, opens successfully, and fits GitHub’s file limits.

## Part 5 — Add unattended GitHub Actions

**Tests first**

Test the small scheduling function with a fixed clock: before/after the configured time, disabled scheduling, timezone changes, a previously attempted day, and manual bypass.

Test publication preparation with temporary directories. Keep workflow-specific verification focused on the actual hosted run.

**Implementation**

Create two workflows:

- **Checks:** deterministic tests and Ruff for code/configuration changes.
- **Harvest:** hourly due check plus manual dispatch.

The hourly check reads TOML using the standard library. When no harvest is due, it performs no API traffic or harvest dependency installation.

For a due run:

1. Check out the latest repository state.
2. Install locked dependencies.
3. Harvest and produce the candidate exports.
4. Validate integrity and file sizes.
5. Commit and push the current `/data` products.
6. Report fresh, reused, stale, skipped, and removed counts.

Use one concurrency group and do not cancel an active harvest. Prevent data-only commits from triggering unnecessary code checks or harvest loops.

Record the attempted day separately from successful source refreshes so partial failures do not trigger another full harvest every hour. Manual dispatch can override the time gate.

Expected isolated record failures may produce a valid publication with warnings. Complete source failure, broken configuration, unusable exports, and publication failure remain visible failures.

Never force-push over newer repository work. A conflicting publication must preserve the remote branch.

**Complete when:** GitHub-hosted execution publishes actual exports safely and subsequent due checks skip correctly.

## Part 6 — Verify the complete harvest and publish the repository

Run the full deterministic suite and Ruff once the implementation is complete. Repeat checks only when changes or failures justify it.

Then perform acceptance in GitHub Actions:

1. Run a **full live harvest** with both sources and every default output enabled.
2. Verify actual source coverage, catalog memberships, and fresh/stale/skipped counts; investigate unexplained omissions.
3. Open both databases, validate relationships, read every Parquet file, and parse the JSONL and JSON-LD products.
4. Measure actual file sizes, compression, runtime, and runner resource use.
5. Run once more to verify incremental reuse and safe replacement of existing outputs.
6. Finish README documentation, commit the completed implementation, make the repository public, and verify anonymous access to the repository and published files.

The second run verifies incremental behavior; it is not another round of speculative testing. Do not rerun full harvests repeatedly without a concrete reason.

Document the command, configuration, schema, example SQL, download/decompression behavior, freshness indicators, and recovery procedure. Preserve source licenses and attribution in the metadata.

Acceptance requires a working full hosted harvest and usable published products—not merely passing fixture tests. Benign source irregularities may remain documented warnings; unexplained data loss, broken exports, and implementation errors must be resolved before public launch.

The resulting repository contains only current export filenames, with ordinary Git history. It has no local scheduler, historical export folders, installable package, or separate metrics harvester.

# Implementation plan: daily Dataportal catalog exports

Build a minimal, repo-local Python application from the reviewed reference code. Harvest Dataportal’s underlying catalogs plus other Swedish catalogs in data.europa.eu, preserve source metadata, and publish the current exports into the repository’s `/data`.

Work sequentially without subagents. Write a small failing behavioral test before implementing each meaningful capability. Finish with a full GitHub-hosted harvest, verify its actual outputs, and make the repository public only after acceptance passes.

## Part 1 — Establish the application and configuration

**Tests first**

Test valid configuration, invalid settings, and path resolution. Add one small RDF fixture that will become the end-to-end test input.

**Implementation**

- Use a single importable `dataportalen_export` directory, with one file per substantial class and ordinary functions for small transformations.
- Provide `uv run python -m dataportalen_export` as the entry point.
- Use dependency-only uv configuration and a committed lockfile. Do not create an installable distribution, build backend, wheel, or publishing workflow.
- Keep runtime dependencies to HTTPX, RDFLib, Pydantic, and DuckDB. Use standard-library SQLite, TOML parsing, logging, gzip, and timezone handling.
- Use pytest and Ruff for development. Avoid an ORM, plugin framework, custom scheduler service, or dataframe dependency.

Root `config.toml` controls:

| Setting                        | Initial default                                |
| ------------------------------ | ---------------------------------------------- |
| Sources                        | Dataportal and Swedish data.europa.eu catalogs |
| EU aggregate exclusion         | `oppnadata`                                    |
| Daily harvest                  | Enabled, after 06:00 Europe/Stockholm          |
| Logging                        | INFO; HTTP logging WARNING                     |
| Request concurrency            | 8                                              |
| Request attempts               | 5                                              |
| Request/download timeouts      | 60 / 600 seconds                               |
| JSONL, SQLite, DuckDB, Parquet | Enabled                                        |
| JSON-LD                        | Enabled, configurable                          |
| Output directory               | `data`                                         |
| Gzip threshold                 | 95 MiB                                         |

Configuration errors fail before network access. Resolve paths relative to the repository. Keep operational settings out of competing CLI flags; retain only explicit manual controls such as forcing a refresh.

**Complete when:** the command loads validated configuration and processes the fixture without installing the repository as a package.

## Part 2 — Implement reliable source retrieval

**Tests first**

Use compact HTTP fixtures to cover pagination, transient retries, incomplete listings, repository-only datasets, missing distribution descriptions, and multiple catalog memberships. Exercise DatasetSeries discovery in the same fixtures.

Mock external HTTP responses, not our own classes.

**Implementation**

Preserve the useful capabilities already present in the reference:

- Download and parse Dataportal’s national RDF dump.
- Resolve underlying catalogs and memberships through EntryScape.
- Discover other Swedish EU catalogs.
- Reconcile EU search results with repository listings.
- Fetch referenced distribution descriptions missing from dataset RDF.
- Bound concurrent requests and retry transient failures.

Correct the reference’s assumptions:

- Catalog titles are labels, never identifiers.
- One dataset can belong to multiple catalogs.
- Context identity and catalog-resource identity remain distinct.
- DatasetSeries participate in discovery and membership resolution.
- Missing catalog metadata does not invalidate every dataset in that catalog.
- An incomplete listing never authorizes deletion.

Each source produces source records plus explicit discovery-completeness information. A failure in one source must not stop the other source.

**Complete when:** both integrations produce the same internal record contract, and a failed request or incomplete listing remains isolated.

## Part 3 — Define models, identity, and incremental refresh

**Tests first**

Extend the representative fixture to cover:

- Exact-URI identity and conflicting source variants.
- Multiple memberships and shared related objects.
- Unknown properties, all languages, and malformed optional values.
- Blank-node cycles and topology-sensitive change detection.
- Reusing unchanged records, retaining failed records, and confirmed removals.

Use these fixtures across conversion and refresh tests rather than creating a large collection of narrowly duplicated tests.

**Implementation**

Create six principal Pydantic models:

1. Catalog.
2. Dataset.
3. Dataset series.
4. Distribution.
5. Data service.
6. Publisher, preserving whether the source describes an organization or another agent type.

Use a new documented JSON contract:

- Stable IDs, original URIs, original RDF types, memberships, and provenance.
- Predictable arrays for collection fields.
- Language-to-list mappings, preserving original language tags.
- Explicit common fields.
- Unknown properties retained under full predicate IRIs.
- Invalid optional values retained in source metadata, with null typed projections and a warning.

Preserve original RDF and complete source variants. Generate JSON-LD from RDF rather than reconstructing it from simplified JSON.

Identity rules are deliberately conservative:

- Match datasets across sources using exact original URIs.
- Otherwise use source-qualified native identifiers.
- Never merge by title, publisher name, shared endpoint, or UUID alone.
- Never lowercase URI paths or use lossy slugs as identity.
- Prefer Dataportal for the default representation while retaining alternate source records.
- Preserve unresolved catalog membership explicitly instead of inventing a national-catalog membership.

Reuse unchanged records using source modification signals and correct RDF graph comparison. Rebuild exports from the accepted current state.

Apply these failure rules:

| Failure                                     | Result                                                                                       |
| ------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Invalid optional field                      | Preserve original value; continue                                                            |
| Existing record cannot refresh              | Retain its last good version and mark stale                                                  |
| New unusable record                         | Report and skip its structured representation                                                |
| Incomplete discovery                        | Preserve previous memberships; suppress deletions                                            |
| Entire source unavailable                   | Retain its previous state; continue other sources                                            |
| Confirmed disappearance                     | Remove that source membership; remove an object only when no surviving reference requires it |
| Unexpected programming or integrity failure | Fail visibly; preserve published products                                                    |

Do not assign fresh timestamps to stale content. Keep failures and source timestamps in the current manifest, without accumulating historical reports.

**Complete when:** repeated runs converge, changed records refresh, and one bad record cannot cause cascading loss.

## Part 4 — Implement the database and export products

**Tests first**

Run the fixture through each exporter, then inspect the products as a consumer:

- Query SQLite and DuckDB.
- Read Parquet tables.
- Parse combined, entity, and per-catalog JSONL.
- Parse JSON-LD and compare the represented RDF graph.
- Check that a failed exporter leaves published files unchanged.

Reuse one expected dataset across formats. Do not test private methods or reproduce implementation logic in assertions.

**Implementation**

SQLite and DuckDB expose the same logical schema:

- Six entity tables.
- Explicit membership/link tables for catalogs, datasets, series, distributions, services, and publishers.
- Source records and entity provenance.
- Current harvest status.

Use primary keys, relationship uniqueness, foreign keys where applicable, and indexes on IDs and join columns. Preserve complete JSON alongside useful queryable columns. Validate polymorphic provenance references before publication.

Write one Parquet file per logical table through DuckDB. Keep table definitions and row projections shared, with small explicit differences between SQL engines.

Produce:

| Product                            | Contents                                                 |
| ---------------------------------- | -------------------------------------------------------- |
| `datasets.jsonl`                   | Combined canonical dataset records                       |
| Object-type JSONL files            | Catalogs, series, distributions, services, publishers    |
| Per-catalog JSONL                  | Each catalog’s member datasets                           |
| `catalog.db`                       | SQLite database                                          |
| `catalog.duckdb`                   | DuckDB database                                          |
| Parquet directory                  | One file per logical table                               |
| Per-catalog JSON-LD                | Context and graph-based metadata                         |
| Compressed source records          | Current raw variants and incremental state               |
| Compressed original Dataportal RDF | Full source preservation                                 |
| `manifest.json`                    | Counts, freshness, failures, filenames, sizes, checksums |

Retained source records provide incremental state even when database exports are disabled. Do not introduce a separate cache database.

Keep exports deterministic. Do not rewrite unchanged record timestamps; retain unchanged binary products when their logical contents and schema are unchanged.

Build all products in a temporary working directory and validate them before publication. Commit the accepted set together.

Use gzip when necessary. If the largest combined JSONL still exceeds GitHub’s limit after compression, omit it and report that explicitly while keeping the split files. Other required oversized products block publication rather than silently disappearing.

**Complete when:** every enabled format represents the same accepted state, opens successfully, and fits GitHub’s file limits.

## Part 5 — Add unattended GitHub Actions

**Tests first**

Test the small scheduling function with a fixed clock: before/after the configured time, disabled scheduling, timezone changes, a previously attempted day, and manual bypass.

Test publication preparation with temporary directories. Keep workflow-specific verification focused on the actual hosted run.

**Implementation**

Create two workflows:

- **Checks:** deterministic tests and Ruff for code/configuration changes.
- **Harvest:** hourly due check plus manual dispatch.

The hourly check reads TOML using the standard library. When no harvest is due, it performs no API traffic or harvest dependency installation.

For a due run:

1. Check out the latest repository state.
2. Install locked dependencies.
3. Harvest and produce the candidate exports.
4. Validate integrity and file sizes.
5. Commit and push the current `/data` products.
6. Report fresh, reused, stale, skipped, and removed counts.

Use one concurrency group and do not cancel an active harvest. Prevent data-only commits from triggering unnecessary code checks or harvest loops.

Record the attempted day separately from successful source refreshes so partial failures do not trigger another full harvest every hour. Manual dispatch can override the time gate.

Expected isolated record failures may produce a valid publication with warnings. Complete source failure, broken configuration, unusable exports, and publication failure remain visible failures.

Never force-push over newer repository work. A conflicting publication must preserve the remote branch.

**Complete when:** GitHub-hosted execution publishes actual exports safely and subsequent due checks skip correctly.

## Part 6 — Verify the complete harvest and publish the repository

Run the full deterministic suite and Ruff once the implementation is complete. Repeat checks only when changes or failures justify it.

Then perform acceptance in GitHub Actions:

1. Run a **full live harvest** with both sources and every default output enabled.
2. Verify actual source coverage, catalog memberships, and fresh/stale/skipped counts; investigate unexplained omissions.
3. Open both databases, validate relationships, read every Parquet file, and parse the JSONL and JSON-LD products.
4. Measure actual file sizes, compression, runtime, and runner resource use.
5. Run once more to verify incremental reuse and safe replacement of existing outputs.
6. Finish README documentation, commit the completed implementation, make the repository public, and verify anonymous access to the repository and published files.

The second run verifies incremental behavior; it is not another round of speculative testing. Do not rerun full harvests repeatedly without a concrete reason.

Document the command, configuration, schema, example SQL, download/decompression behavior, freshness indicators, and recovery procedure. Preserve source licenses and attribution in the metadata.

Acceptance requires a working full hosted harvest and usable published products—not merely passing fixture tests. Benign source irregularities may remain documented warnings; unexplained data loss, broken exports, and implementation errors must be resolved before public launch.

The resulting repository contains only current export filenames, with ordinary Git history. It has no local scheduler, historical export folders, installable package, or separate metrics harvester.
