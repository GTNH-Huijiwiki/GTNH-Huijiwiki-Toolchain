"""Flags for decomposition of ordinary dust, not crushed/impure/pure ore stages."""
import json
from pathlib import Path

MACHINES = {
    "centrifuge": ("centrifuge", "multicentrifuge"),
    "dehydrator": ("chemicaldehydrator", "multidehydrator"),
    "electrolyzer": ("electrolyzer",),
}
FIELDS = [
    {"name": name, "type": "boolean", "title": {"en": name, "zh": label}}
    for name, label in (("centrifuge", "可离心分解"), ("dehydrator", "可脱水分解"), ("electrolyzer", "可电解分解"))
]

def item_keys(value, ore_dictionary, seen=frozenset()):
    if isinstance(value, str):
        if value.startswith("#"):
            name = value[1:]
            if name not in seen:
                yield from item_keys(ore_dictionary.get(name, []), ore_dictionary, seen | {name})
        else:
            yield value
    elif isinstance(value, dict):
        yield from item_keys(value.get("key", value.get("rawKey", [])), ore_dictionary, seen)
    elif isinstance(value, list):
        for child in value:
            yield from item_keys(child, ore_dictionary, seen)

def append_flags(rows, materials, ore_dictionary, recipe_documents):
    inputs = {name: set() for name in MACHINES}
    observed = set()
    for document in recipe_documents:
        handler = document.get("identifier", "").removeprefix("gt.recipe.")
        for name, handlers in MACHINES.items():
            if handler not in handlers:
                continue
            observed.add(name)
            for recipe in document["recipes"]:
                inputs[name].update(item_keys(recipe.get("inputItems", []), ore_dictionary))
    if rows and observed != set(MACHINES):
        raise ValueError("Missing decomposition recipe categories: " + ", ".join(sorted(set(MACHINES) - observed)))
    result = []
    for row in rows:
        material = row[0]
        dust = set(item_keys(ore_dictionary.get("dust" + material, []), ore_dictionary))
        dust.update(item_keys(materials.get(material, {}).get("OrePrefixes", {}).get("dust", []), ore_dictionary))
        result.append(list(row) + [bool(dust & inputs[name]) for name in MACHINES])
    return result

def read_recipe_documents(*directories):
    seen = set()
    handlers = {handler for group in MACHINES.values() for handler in group}
    for directory in directories:
        for handler in sorted(handlers):
            for path in Path(directory).glob(f"gregtech/GTNEIDefaultHandler_{handler}.json"):
                resolved = path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    yield json.loads(path.read_text(encoding="utf-8"))
