import hashlib
import re
import unicodedata
from collections import Counter, defaultdict

from dataportalen_export.schema import json_text


def catalog_names(catalogs):
    """Readable file labels; exact catalog identities remain in the records."""
    bases = {}
    for identifier, catalog in sorted(catalogs.items()):
        titles = catalog["title"]
        label = next(
            (
                value
                for lang in ("sv", "sv-SE", "und", "en", *sorted(titles))
                for value in titles.get(lang, [])
                if value.strip()
            ),
            catalog.get("uri") or "catalog",
        )
        label = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()
        bases[identifier] = (
            re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:100] or "catalog"
        )
    counts, used, names = Counter(bases.values()), set(), {}
    for identifier, base in bases.items():
        contexts = catalogs[identifier].get("context_ids", [])
        if counts[base] > 1 and contexts:
            base += "-context-" + re.sub(r"[^a-z0-9-]", "-", str(contexts[0]).lower())
        if re.fullmatch(r"con|prn|aux|nul|com[0-9]|lpt[0-9]", base):
            base = "catalog-" + base
        name, number = base, 2
        while name in used:
            name, number = f"{base}-{number}", number + 1
        used.add(name)
        names[identifier] = name
    return names


def rows_digest(rows):
    digest = hashlib.sha256()
    for row in rows:
        digest.update((json_text(row) + "\n").encode())
    return digest.hexdigest()


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json_text(row) + "\n")


class JsonlExporter:
    def write(self, directory, entities, links, provenance, settings, *, reuse=None):
        def emit(name, items):
            items = list(items)
            if reuse and reuse(name, rows_digest(items)):
                return
            write_jsonl(directory / name, items)

        memberships, distributions, sources = (
            defaultdict(list),
            defaultdict(list),
            defaultdict(list),
        )
        for table, left, right in links:
            if table == "catalog_datasets":
                memberships[right].append(left)
            elif table == "dataset_distributions":
                distributions[left].append(entities["distributions"][right])
        for kind, identifier, record in provenance:
            sources[(kind, identifier)].append(record)
        rows = {}
        for identifier, entity in sorted(entities["datasets"].items()):
            rows[identifier] = {
                **entity,
                "catalog_ids": sorted(memberships[identifier]),
                "distributions": distributions[identifier],
                "source_record_ids": sorted(sources[("datasets", identifier)]),
            }
        if settings.combined_jsonl:
            emit("datasets.jsonl", rows.values())
        if settings.entity_jsonl:
            for kind, items in entities.items():
                if kind != "datasets":
                    emit(
                        f"{kind}.jsonl",
                        (
                            {**entity, "source_record_ids": sources[(kind, identifier)]}
                            for identifier, entity in sorted(items.items())
                        ),
                    )
        if settings.catalog_jsonl:
            by_catalog = defaultdict(list)
            for identifier, catalogs in memberships.items():
                for catalog in catalogs:
                    by_catalog[catalog].append(rows[identifier])
            for catalog, name in catalog_names(entities["catalogs"]).items():
                emit(
                    f"catalogs/{name}.jsonl",
                    by_catalog[catalog],
                )
