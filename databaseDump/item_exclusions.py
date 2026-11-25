"""Exclude combinatorial variants from wiki imports while preserving dependencies of retained recipes.

The dump stays a faithful copy of the game; everything left out of the wiki is decided here.
The rules are listed in README.md ("不导入维基的组合变体").
"""
import json
from collections import Counter
from pathlib import Path
from nbt_identity import nbt_identity

# Families whose recipes disappear together with their variants.
DECORATIVE = 'decorative'
BANNER = 'banner'
# Families that never remove recipes through their own outputs.
BOLT = 'bolt'
CONTAINER = 'container'
NEI_FLUID_FAMILY = 'nei_fluid'

DECORATIVE_REGISTRIES = {
    'ForgeMicroblock:microblock', 'ExtraUtilities:microblocks',
    'BuildCraft|Transport:pipeFacade', 'appliedenergistics2:item.ItemFacade',
}
BANNER_REGISTRY = 'etfuturum:banner'
BOLT_REGISTRY = 'TConstruct:BoltPart'
NEI_FLUID = 'NotEnoughItems:neiFluidDisplay'
# Registry name -> NBT field that marks a filled container.
CONTAINERS = {
    'gregtech:gt.Volumetric_Flask': 'Fluid',
    'miscutils:gt.Volumetric_Flask_32k': 'Fluid',
    'miscutils:gt.Volumetric_Flask_8k': 'Fluid',
    'miscutils:gt.Volumetric_Flask_Infinite': 'Fluid',
    'WitchingGadgets:item.WG_CrystalFlask': 'fluid',
}
OFANIX = 'Ztones:ofanix'
_FILTERED = DECORATIVE_REGISTRIES | set(CONTAINERS) | {BANNER_REGISTRY, BOLT_REGISTRY, NEI_FLUID}


def split_key(key):
    registry, sep, meta = key.rpartition(':')
    if sep and ':' in registry and meta.isdecimal():
        return registry, int(meta)
    return key, 0


def identity(item):
    return item.get('rawKey') or item.get('key'), nbt_identity(item.get('nbt') or '{}')


def _field(compound, name):
    """Value of one field in an nbt_identity compound, or None."""
    if compound and compound[0] == 'compound':
        for key, value in compound[1]:
            if key == name:
                return value
    return None


def family(key, tag):
    """Family of one variant; tag is the nbt_identity value of its NBT."""
    registry = split_key(key)[0]
    if registry in DECORATIVE_REGISTRIES:
        return DECORATIVE
    if registry == BOLT_REGISTRY:
        return BOLT
    if registry == NEI_FLUID:
        return NEI_FLUID_FAMILY
    if registry == BANNER_REGISTRY:
        return BANNER if _field(_field(tag, 'BlockEntityTag'), 'Patterns') is not None else None
    marker = CONTAINERS.get(registry)
    return CONTAINER if marker and _field(tag, marker) is not None else None


def item_family(item):
    key = item.get('rawKey') or item.get('key') or ''
    if split_key(key)[0] not in _FILTERED:
        return None
    return family(*identity(item))


def _shaft(ident):
    """Tinkers' bolt shaft material id; bolts without one sort last."""
    value = _field(_field(ident[1], 'DualMat'), 'Material2')
    if value and value[0] == 'scalar':
        return int(value[1].rstrip('bBsSlL'))
    return float('inf')


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


def _keys(value):
    """Item keys of one recipe field, whether written as strings or objects."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        if value.get('key') or value.get('rawKey'):
            yield value.get('rawKey') or value.get('key')
        else:
            for child in value.values():
                yield from _keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _keys(child)


def _references(value):
    """(item object or None, key) for every reference, including plain strings."""
    if isinstance(value, str):
        yield None, value
    elif isinstance(value, dict):
        if value.get('key') or value.get('rawKey'):
            yield value, value.get('rawKey') or value.get('key')
        else:
            for child in value.values():
                yield from _references(child)
    elif isinstance(value, list):
        for child in value:
            yield from _references(child)


def is_restyle(recipe):
    """Two occupied slots: the ofanix and a style of the Ztones block that comes out."""
    outputs = list(_keys(recipe.get('outputItems', [])))
    if len(outputs) != 1 or not outputs[0].startswith('Ztones:'):
        return False
    block = split_key(outputs[0])[0]
    slots = [list(_keys(slot)) for slot in recipe.get('inputItems') or [] if slot is not None]
    if len(slots) != 2 or [OFANIX] not in slots:
        return False
    slots.remove([OFANIX])
    return bool(slots[0]) and all(split_key(key)[0] == block for key in slots[0])


def _bolt_output(recipe):
    """(head key, shaft) of a recipe that makes only Tinkers' bolts, else None."""
    outputs = list(stacks(recipe.get('outputItems', [])))
    if not outputs or len(outputs) != len(list(_keys(recipe.get('outputItems', [])))):
        return None
    if any(split_key(item.get('rawKey') or item.get('key'))[0] != BOLT_REGISTRY for item in outputs):
        return None
    best = min((identity(item) for item in outputs), key=_shaft)
    return split_key(best[0]), _shaft(best)


def static_drops(recipes):
    """{index: rule} for one recipe page: rules that do not depend on what else is retained."""
    drops, bolts = {}, {}
    for index, recipe in enumerate(recipes):
        if not isinstance(recipe, dict) or 'questId' in recipe:
            continue
        if is_restyle(recipe):
            drops[index] = 'ztones_restyle'
            continue
        bolt = _bolt_output(recipe)
        if bolt is None:
            continue
        head, shaft = bolt
        if head not in bolts or shaft < bolts[head][0]:
            if head in bolts:
                drops[bolts[head][1]] = 'bolt_variants'
            bolts[head] = (shaft, index)
        else:
            drops[index] = 'bolt_variants'
    return drops


class ExclusionPolicy:
    def __init__(self, items, documents, quest_keys=()):
        candidates, quest_needed = {}, set()
        quest_keys = set(quest_keys)
        for item in items:
            kind = item_family(item)
            if kind is None or kind == NEI_FLUID_FAMILY:
                continue
            ident = identity(item)
            candidates[ident] = kind
            if item.get('shortKey') in quest_keys:
                quest_needed.add(ident)
        by_key = {}
        for ident, kind in candidates.items():
            if kind == DECORATIVE:
                by_key.setdefault(ident[0], set()).add(ident)
        self.protected = set(quest_needed)
        pending = []
        for document in documents:
            recipes = document.get('recipes', [])
            drops = static_drops(recipes)
            for index, recipe in enumerate(recipes):
                if index in drops or not isinstance(recipe, dict):
                    continue
                refs = set()
                for item in stacks(recipe):
                    if split_key(item.get('rawKey') or item.get('key'))[0] not in _FILTERED:
                        continue
                    ident = identity(item)
                    if ident in candidates:
                        refs.add(ident)
                    elif family(*ident) == DECORATIVE and not item.get('nbt'):
                        # An ingredient without NBT matches all variants.
                        refs.update(by_key.get(ident[0], ()))
                if not refs:
                    continue
                outputs = list(stacks(recipe.get('outputItems', [])))
                if 'questId' in recipe or not outputs or any(
                        item_family(item) not in (DECORATIVE, BANNER) for item in outputs):
                    self.protected.update(refs)
                else:
                    pending.append(({identity(item) for item in outputs} & candidates.keys(), refs))
        changed = True
        while changed:
            changed = False
            for outputs, refs in pending:
                if outputs & self.protected and not refs <= self.protected:
                    self.protected.update(refs)
                    changed = True
        # Every bolt head keeps at least one item, even without a recipe or quest.
        heads = {split_key(ident[0]) for ident in self.protected if candidates.get(ident) == BOLT}
        fallback = {}
        for ident, kind in candidates.items():
            head = split_key(ident[0])
            if kind == BOLT and head not in heads:
                rank = (_shaft(ident), repr(ident))
                if head not in fallback or rank < fallback[head][0]:
                    fallback[head] = (rank, ident)
        self.protected.update(ident for _, ident in fallback.values())
        self.removed = candidates.keys() - self.protected

    def keep_item(self, item):
        kind = item_family(item)
        if kind is None:
            return True
        return kind != NEI_FLUID_FAMILY and identity(item) in self.protected

    def _removed_with_variants(self, recipe):
        """Rule name when a recipe only makes variants that are left out."""
        if 'questId' in recipe:
            return None
        outputs = list(stacks(recipe.get('outputItems', [])))
        if not outputs or len(outputs) != len(list(_keys(recipe.get('outputItems', [])))):
            return None
        kinds = {item_family(item) for item in outputs}
        if not kinds <= {DECORATIVE, BANNER} or any(self.keep_item(item) for item in outputs):
            return None
        return 'patterned_banners' if BANNER in kinds else 'decorative_variants'

    def filter_recipes(self, recipes):
        """(kept recipes, Counter of removal rules) for one recipe page."""
        drops = static_drops(recipes)
        kept, counts = [], Counter()
        for index, recipe in enumerate(recipes):
            rule = drops.get(index) or (isinstance(recipe, dict) and self._removed_with_variants(recipe))
            if rule:
                counts[rule] += 1
            else:
                kept.append(recipe)
        return kept, counts


def fluid_names(fluids):
    """{fluid id: 'fluid.name'} from fluid.json entries; the GT display metadata is the fluid id."""
    names = {}
    for entry in fluids:
        display = entry.get('gtDisplayKey')
        if display:
            names[split_key(display)[1]] = entry['key']
    return names


def rewrite_fluids(value, names, unresolved):
    """Point NEI fluid display references at fluids, in place. Returns (new value, rewritten count).

    A reference keeps its slot; the NEI amount (millibuckets) becomes the item amount.
    """
    if isinstance(value, str):
        if split_key(value)[0] != NEI_FLUID:
            return value, 0
        name = names.get(split_key(value)[1])
        if name is None:
            unresolved.add(value)
            return value, 0
        return name, 1
    count = 0
    if isinstance(value, list):
        for index, child in enumerate(value):
            value[index], found = rewrite_fluids(child, names, unresolved)
            count += found
        return value, count
    if isinstance(value, dict):
        key = value.get('rawKey') or value.get('key')
        if isinstance(key, str) and split_key(key)[0] == NEI_FLUID:
            name = names.get(split_key(key)[1])
            if name is None:
                unresolved.add(key)
                return value, 0
            tag = _field(nbt_identity(value.get('nbt') or '{}'), 'neiFluidDisplayAmount')
            amount = int(tag[1].rstrip('bBsSlL')) if tag else 0
            result = {k: v for k, v in value.items() if k not in ('key', 'rawKey', 'nbt')}
            result = {'key': name, **result}
            if amount > 1:
                result['amount'] = amount
            return result, 1
        for child_key, child in list(value.items()):
            value[child_key], found = rewrite_fluids(child, names, unresolved)
            count += found
    return value, count


def _is_fluid_slot(slot):
    """A slot that only holds NEI fluid displays (one, or alternatives)."""
    if isinstance(slot, list):
        return bool(slot) and all(_is_fluid_slot(entry) for entry in slot)
    if isinstance(slot, dict):
        slot = slot.get('rawKey') or slot.get('key')
    return isinstance(slot, str) and split_key(slot)[0] == NEI_FLUID


def _as_fluid(entry):
    """A rewritten reference in the shape of a fluid slot."""
    if isinstance(entry, list):
        return [_as_fluid(child) for child in entry]
    entry = {'key': entry} if isinstance(entry, str) else dict(entry)
    if 'amount' in entry:
        entry['showAmount'] = True
    return entry


def convert_fluid_displays(recipes, names, unresolved):
    """Turn NEI fluid displays into fluid slots, in place. Returns the number of references converted.

    A slot that holds only NEI displays moves from inputItems/outputItems to inputFluids/outputFluids, so the
    remaining item slots keep the positions the recipe layouts expect. Anywhere else the key is rewritten in place.
    """
    count = 0
    for recipe in recipes:
        for items_field, fluids_field in (('inputItems', 'inputFluids'), ('outputItems', 'outputFluids')):
            slots = recipe.get(items_field)
            if not isinstance(slots, list) or not any(_is_fluid_slot(slot) for slot in slots):
                continue
            kept, moved = [], []
            for slot in slots:
                if not _is_fluid_slot(slot):
                    kept.append(slot)
                    continue
                before = len(unresolved)
                converted, found = rewrite_fluids(slot, names, unresolved)
                count += found
                if len(unresolved) > before:
                    # Without a fluid name the reference stays where it was, so nothing is silently lost.
                    kept.append(converted)
                else:
                    moved.append(_as_fluid(converted))
            recipe[items_field] = kept
            if moved:
                recipe[fluids_field] = list(recipe.get(fluids_field) or []) + moved
        _, found = rewrite_fluids(recipe, names, unresolved)
        count += found
    return count


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


def quest_item_keys(root):
    """Exact item keys the quest book shows (icons, task items, rewards) from betterquesting/itemKeys.json.

    taskMatches is left out: a task that ignores NBT accepts every variant of an item.
    """
    path = Path(root) / 'betterquesting' / 'itemKeys.json'
    if not path.is_file():
        return set()
    keys = set()
    for entry in json.loads(path.read_text('utf-8')).values():
        keys.add(entry.get('icon'))
        for kind in ('tasks', 'rewards'):
            for values in (entry.get(kind) or {}).values():
                keys.update(values)
    keys.discard(None)
    return keys


def load_policy(source, reference=None):
    roots = list(dict.fromkeys(str(Path(p).resolve()) for p in (source, reference or source)))
    items, quests = [], set()
    for root in roots:
        path = Path(root) / 'advitempanel.json'
        if path.exists():
            items.extend(json.loads(path.read_text('utf-8'))['shortKey'])
        quests |= quest_item_keys(root)
    return ExclusionPolicy(items, read_documents(*roots), quests)


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
