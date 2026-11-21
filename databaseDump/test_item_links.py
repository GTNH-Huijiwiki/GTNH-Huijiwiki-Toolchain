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
        items = [{'key': 'mod:scan:5', 'rawKey': 'mod:scan:5'}, {'key': 'mod:b:3', 'rawKey': 'mod:b:3'},
                 {'key': 'mod:b:3_HASH', 'rawKey': 'mod:b:3', 'nbt': '{x:1}'},
                 {'key': 'mod:none', 'rawKey': 'mod:none', 'questIds': ['stale']},
                 {'key': 'fluid.water', 'shortKey': 'fluid.water', 'rawKey': 'gt:display:1'}]
        counts = attach_item_links(items, {'mod:scan:32767': ['A']}, {'mod:b:3': ['q2', 'q1'], 'gt:display:1': ['q3']})
        self.assertEqual(counts, (1, 2))
        self.assertEqual(items[0]['researchKeys'], ['A'])
        self.assertEqual(items[1]['questIds'], ['q1', 'q2'])
        # 带 NBT 的变体不因 rawKey 相同被关联
        self.assertNotIn('questIds', items[2])
        self.assertNotIn('questIds', items[3])
        self.assertEqual(items[4]['questIds'], ['q3'])

    def test_quest_links_follow_task_nbt_rules(self):
        bee = {'id': 'Forestry:beePrincessGE', 'Damage': 0, 'tag': {'Genome': 'imperial'}}
        flask = {'id': 'gt:flask', 'Damage': 0}
        quests = [{'questID': 'q', 'properties': {'betterquesting': {'icon': bee}},
                   'tasks': [{'index': 0, 'ignoreNBT': 0, 'requiredItems': [bee, flask]},
                             {'index': 1, 'ignoreNBT': 1, 'requiredItems': [bee]}],
                   'rewards': [{'index': 0, 'rewards': [{'id': 'TConstruct:cleaver', 'tag': {'display': {}}}]}]}]
        many = ['gt:flask'] + [f'gt:flask_{n}' for n in range(30)]
        bees = [f'Forestry:beePrincessGE_{n}' for n in range(40)]
        keys = {'q': {'icon': 'Forestry:beePrincessGE_imp', 'rewards': {'0': ['TConstruct:cleaver_paper']},
                      'taskMatches': {'0': [['Forestry:beePrincessGE_imp'], many], '1': [bees]}}}
        index = quest_ids_by_item(quests, keys)
        # 指定 NBT 的要求只关联匹配的那个品种；不限 NBT 且变体很多时只关联基础物品，没有基础物品就不关联
        self.assertEqual(sorted(index), ['Forestry:beePrincessGE_imp', 'TConstruct:cleaver_paper', 'gt:flask'])


if __name__ == '__main__':
    unittest.main()
