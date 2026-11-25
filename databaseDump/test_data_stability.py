import copy
import json
import tempfile
import unittest
from pathlib import Path

import data_stability


class ApplyTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = Path(directory.name)

    def run_rules(self, rules, data):
        return data_stability.apply('a/b.json', copy.deepcopy(data), self.state, rules)

    def test_keep_restores_listed_fields_of_listed_records_only(self):
        rules = [{'mode': 'keep', 'file': 'a/b.json', 'records': 'rows', 'key': 'id',
                  'fields': ['aspect', 'info.biomes'], 'keys': ['x', 'y']}]
        first = {'rows': [{'id': 'x', 'aspect': 1, 'info': {'biomes': [1]}, 'name': 'old'},
                          {'id': 'y'},
                          {'id': 'z', 'aspect': 1}]}
        self.assertEqual(self.run_rules(rules, first), first)
        second = {'rows': [{'id': 'x', 'aspect': 2, 'name': 'new'},
                           {'id': 'y', 'aspect': 5, 'info': {'biomes': [2]}},
                           {'id': 'z', 'aspect': 2}]}
        self.assertEqual(self.run_rules(rules, second)['rows'], [
            {'id': 'x', 'aspect': 1, 'info': {'biomes': [1]}, 'name': 'new'},
            {'id': 'y', 'info': {}},
            {'id': 'z', 'aspect': 2}])

    def test_keep_matches_records_by_structured_key(self):
        key = json.dumps([{'key': 'spawner'}], separators=(',', ':'))
        rules = [{'mode': 'keep', 'file': 'a/b.json', 'records': 'recipes', 'key': 'inputItems',
                  'fields': ['spawnList'], 'keys': [key]}]
        self.run_rules(rules, {'recipes': [{'inputItems': [{'key': 'spawner'}], 'spawnList': ['one']}]})
        again = self.run_rules(rules, {'recipes': [{'inputItems': [{'key': 'spawner'}], 'spawnList': ['two']}]})
        self.assertEqual(again['recipes'][0]['spawnList'], ['one'])

    def test_keep_file_returns_the_first_content(self):
        rules = [{'mode': 'keepFile', 'file': 'a/b.json'}]
        self.run_rules(rules, {'recipes': [1]})
        self.assertEqual(self.run_rules(rules, {'recipes': [2, 3]}), {'recipes': [1]})

    def test_keep_order_places_new_records_after_their_neighbour(self):
        rules = [{'mode': 'keepOrder', 'file': 'a/b.json', 'records': 'recipes'}]
        self.run_rules(rules, {'recipes': ['a', 'b', 'c', 'd']})
        shuffled = self.run_rules(rules, {'recipes': ['c', 'new', 'a', 'd']})
        self.assertEqual(shuffled['recipes'], ['a', 'c', 'new', 'd'])
        self.assertEqual(self.run_rules(rules, {'recipes': ['d', 'new', 'c', 'a']})['recipes'],
                         ['a', 'c', 'new', 'd'])

    def test_sort_orders_listed_lists_without_state(self):
        rules = [{'mode': 'sort', 'file': 'a/b.json', 'records': 'biomes', 'key': 'id',
                  'fields': ['spawns.monster'], 'keys': [171]}]
        data = {'biomes': [{'id': 171, 'spawns': {'monster': [{'entity': 'b'}, {'entity': 'a'}]}},
                           {'id': 1, 'spawns': {'monster': [{'entity': 'b'}, {'entity': 'a'}]}}]}
        result = self.run_rules(rules, data)
        self.assertEqual([[m['entity'] for m in b['spawns']['monster']] for b in result['biomes']],
                         [['a', 'b'], ['b', 'a']])
        self.assertEqual(list(self.state.iterdir()), [])

    def test_other_files_are_untouched(self):
        rules = [{'mode': 'keepFile', 'file': 'other.json'}]
        self.assertEqual(self.run_rules(rules, {'v': 1}), {'v': 1})
        self.assertEqual(self.run_rules(rules, {'v': 2}), {'v': 2})

    def test_seed_replaces_recorded_values(self):
        rules = [{'mode': 'keepFile', 'file': 'a/b.json'}]
        with tempfile.TemporaryDirectory() as dumps:
            source = Path(dumps) / 'a' / 'b.json'
            source.parent.mkdir()
            data_stability.apply('a/b.json', {'v': 'stale'}, self.state / '1' / 'stable', rules)
            source.write_text('{"v": "online"}', 'utf-8')
            self.assertEqual(data_stability.seed(dumps, '1', rules, self.state), ['a/b.json'])
        self.assertEqual(data_stability.apply('a/b.json', {'v': 'new'}, self.state / '1' / 'stable', rules),
                         {'v': 'online'})


class ConfigTest(unittest.TestCase):
    def test_repository_rules_are_complete(self):
        rules = data_stability.load_rules()
        self.assertTrue(rules)
        for rule in rules:
            self.assertIn(rule['mode'], ('keep', 'keepFile', 'keepOrder', 'sort'))
            self.assertTrue(rule['file'].endswith('.json'))
            if rule['mode'] in ('keep', 'sort'):
                self.assertTrue(rule['records'] and rule['key'] and rule['fields'] and rule['keys'], rule['file'])


if __name__ == '__main__':
    unittest.main()
