"""Exclude decorative variant families while preserving dependencies of retained recipes."""
import json
from pathlib import Path
from nbt_identity import nbt_identity

def excluded_family(key):
    registry, sep, meta = key.rpartition(':')
    if sep and ':' in registry and meta.isdecimal():
        damage = int(meta)
    else:
        registry, damage = key, 0
    return (registry == 'ExtraUtilities:microblocks' and damage == 0 or
            registry in {'BuildCraft|Transport:pipeFacade', 'appliedenergistics2:item.ItemFacade',
                         'ForgeMicroblock:microblock'})

def identity(item):
    return item.get('rawKey') or item.get('key'), nbt_identity(item.get('nbt') or '{}')

def stacks(value):
    if isinstance(value, dict):
        if value.get('key') or value.get('rawKey'):
            yield value
        else:
            for child in value.values():
                yield from stacks(child)
    elif isinstance(value, list):
        for child in value:
            yield from stacks(child)

class ExclusionPolicy:
    def __init__(self, items, documents):
        candidates = {identity(item) for item in items if excluded_family(identity(item)[0])}
        by_key = {}
        for candidate in candidates:
            by_key.setdefault(candidate[0], set()).add(candidate)
        self.protected = set()
        pending = []
        for document in documents:
            for recipe in document.get('recipes', []):
                if not isinstance(recipe, dict):
                    continue
                refs = set()
                for item in stacks(recipe):
                    ident = identity(item)
                    if ident in candidates:
                        refs.add(ident)
                    elif excluded_family(ident[0]) and not item.get('nbt'):
                        # An ingredient without NBT matches all variants.
                        refs.update(by_key.get(ident[0], ()))
                if not refs:
                    continue
                outputs = list(stacks(recipe.get('outputItems', [])))
                output_refs = {identity(item) for item in outputs if identity(item) in candidates}
                if 'questId' in recipe or not outputs or any(not excluded_family(identity(item)[0]) for item in outputs):
                    self.protected.update(refs)
                else:
                    pending.append((output_refs, refs))
        changed = True
        while changed:
            changed = False
            for outputs, refs in pending:
                if outputs & self.protected and not refs <= self.protected:
                    self.protected.update(refs)
                    changed = True
        self.removed = candidates - self.protected

    def keep_item(self, item):
        return not excluded_family(identity(item)[0]) or identity(item) in self.protected

    def keep_recipe(self, recipe):
        if 'questId' in recipe:
            return True
        outputs = list(stacks(recipe.get('outputItems', [])))
        return not outputs or any(self.keep_item(item) for item in outputs)

def read_documents(*roots):
    seen = set()
    for root in roots:
        for path in sorted((Path(root) / 'recipes').rglob('*.json')):
            if 'filtered' in path.relative_to(Path(root) / 'recipes').parts:
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield json.loads(path.read_text('utf-8'))

def load_policy(source, reference=None):
    roots = list(dict.fromkeys(str(Path(p).resolve()) for p in (source, reference or source)))
    items = []
    for root in roots:
        path = Path(root) / 'advitempanel.json'
        if path.exists():
            items.extend(json.loads(path.read_text('utf-8'))['shortKey'])
    return ExclusionPolicy(items, read_documents(*roots))

def excluded_icons(items, icon_records, policy):
    records = {r['shortKey']: r for r in icon_records}
    removed, retained = set(), set()
    for item in items:
        record = records.get(item.get('shortKey'), {})
        name = record.get('iconName') or item.get('icon')
        if not name:
            continue
        target = retained if policy.keep_item(item) else removed
        target.add(name)
        if record.get('frameCount', 1) > 1:
            target.add(name.removesuffix('.png') + '_dynamic.png')
    return removed - retained
