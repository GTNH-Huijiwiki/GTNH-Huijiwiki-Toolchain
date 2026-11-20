import unittest

from split_all import attach_item_links, quest_ids_by_item, research_candidates, research_keys_by_item


class ItemLinksTest(unittest.TestCase):
    def test_candidates_follow_the_lua_rule(self):
        self.assertEqual(research_candidates('mod:item:3'), ['mod:item:3', 'mod:item:32767'])
        self.assertEqual(research_candidates('mod:item:0'), ['mod:item', 'mod:item:0', 'mod:item:32767'])
        self.assertEqual(research_candidates('mod:item'), ['mod:item', 'mod:item:0', 'mod:item:32767'])

    def test_research_positions_and_filters(self):
        data = {'categories': {'BASICS': {}}, 'researches': {
            'A': {'Key': 'A', 'CategoryKey': 'BASICS', 'Icon': 'mod:icon', 'ItemTriggerItems': ['mod:scan:32767'],
                  'Pages': [{'type': 'smelting', 'output': 'mod:smelt:2'},
                            {'type': 'arcane_crafting', 'recipes': [{'output': {'key': 'mod:nbt:1', 'nbt': '{}'}},
                                                                    {'output': {'enchantment': 'x'}}]}]},
            'B': {'Key': 'B', 'CategoryKey': 'BASICS', 'Icon': 'mod:icon', 'IconResource': 'thaumcraft:x'},
            'C': {'Key': 'C', 'CategoryKey': 'BASICS', 'Icon': 'mod:icon', 'IsVirtual': True},
            'D': {'Key': 'D', 'CategoryKey': 'MISSING', 'Icon': 'mod:icon'}}}
        index = research_keys_by_item(data)
        self.assertEqual(dict(index), {'mod:icon': ['A'], 'mod:scan:32767': ['A'], 'mod:smelt:2': ['A'], 'mod:nbt:1': ['A']})

    def test_quest_positions_and_damage(self):
        quests = [{'questID': 'q1', 'properties': {'betterquesting': {'icon': {'id': 'mod:a', 'Damage': 0}}},
                   'tasks': [{'requiredItems': [{'id': 'mod:b', 'Damage': 3}], 'requiredFluids': [{'FluidName': 'water'}]}],
                   'rewards': [{'rewards': [{'id': 'mod:c'}], 'choices': {'0': {'id': 'mod:d', 'Damage': 1}}}]}]
        self.assertEqual(sorted(quest_ids_by_item(quests)), ['fluid.water', 'mod:a', 'mod:b:3', 'mod:c', 'mod:d:1'])

    def test_items_get_fields_only_when_linked(self):
        items = [{'key': 'mod:scan:5', 'rawKey': 'mod:scan:5'}, {'key': 'mod:b:3_HASH', 'rawKey': 'mod:b:3'},
                 {'key': 'mod:none', 'rawKey': 'mod:none', 'questIds': ['stale']}]
        counts = attach_item_links(items, {'mod:scan:32767': ['A']}, {'mod:b:3': ['q2', 'q1']})
        self.assertEqual(counts, (1, 1))
        self.assertEqual(items[0]['researchKeys'], ['A'])
        self.assertEqual(items[1]['questIds'], ['q1', 'q2'])
        self.assertNotIn('questIds', items[2])


if __name__ == '__main__':
    unittest.main()
