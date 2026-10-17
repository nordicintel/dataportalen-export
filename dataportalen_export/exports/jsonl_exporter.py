import re
import unicodedata
from collections import Counter, defaultdict

from dataportalen_export.schema import inline_relations, json_text, object_json


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


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json_text(row) + "\n")


def jsonl_products(entities, links, provenance, settings):
    memberships, distributions, sources = (
        defaultdict(list),
        defaultdict(list),
        defaultdict(list),
    )
    outgoing, incoming = defaultdict(list), defaultdict(list)
    publishers = {
        entity["uri"]: identifier
        for identifier, entity in entities["publishers"].items()
        if entity.get("uri")
    }
    for table, left, right in links:
        outgoing[table, left].append(right)
        incoming[table, right].append(left)
        if table == "catalog_datasets":
            memberships[right].append(left)
    for table, left, right in links:
        if table == "dataset_distributions":
            entity = entities["distributions"][right]
            distributions[left].append(
                {
                    **object_json(entity),
                    **inline_relations(entity, outgoing, incoming, publishers),
                }
            )
    for kind, identifier, record in provenance:
        sources[(kind, identifier)].append(record)
    rows = {}
    for identifier, entity in sorted(entities["datasets"].items()):
        rows[identifier] = {
            **object_json(entity),
            **inline_relations(entity, outgoing, incoming, publishers),
            "distributions": distributions[identifier],
            "source_record_ids": sorted(sources[("datasets", identifier)]),
        }
    if settings.combined_jsonl:
        yield "datasets.jsonl", list(rows.values())
    if settings.entity_jsonl:
        for kind, items in entities.items():
            if kind != "datasets":
                yield (
                    f"{kind}.jsonl",
                    [
                        {
                            **object_json(entity),
                            "source_record_ids": sources[(kind, identifier)],
                        }
                        for identifier, entity in sorted(items.items())
                    ],
                )
    if settings.catalog_jsonl:
        by_catalog = defaultdict(list)
        for identifier, catalogs in memberships.items():
            for catalog in catalogs:
                by_catalog[catalog].append(rows[identifier])
        for catalog, name in catalog_names(entities["catalogs"]).items():
            yield f"catalogs/{name}.jsonl", by_catalog[catalog]
