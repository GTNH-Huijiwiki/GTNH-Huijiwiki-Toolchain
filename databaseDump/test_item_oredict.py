import unittest

from split_all import attach_ore_dictionaries, require_ore_entries


class ItemOreDictTest(unittest.TestCase):
    def test_reverse_membership_matches_ore_pages(self):
        items = [
            {'key': 'gregtech:gt.metaitem.01:11305', 'rawKey': 'gregtech:gt.metaitem.01:11305'},
            {'key': 'minecraft:iron_ingot', 'rawKey': 'minecraft:iron_ingot'},
            {'key': 'minecraft:wool:3', 'rawKey': 'minecraft:wool:3'},
            {'key': 'minecraft:wool:4', 'rawKey': 'minecraft:wool:4'},
            {'key': 'minecraft:stone', 'rawKey': 'minecraft:stone', 'oredict': ['stale']},
        ]
        entries = [
            {'oreDict': 'ingotSteel', 'items': ['gregtech:gt.metaitem.01:11305']},
            {'oreDict': 'ingotIron', 'items': ['minecraft:iron_ingot']},
            {'oreDict': 'blockWool', 'items': ['minecraft:wool:32767']},
            {'oreDict': 'ingotAny', 'items': ['gregtech:gt.metaitem.01:11305', 'minecraft:iron_ingot']},
        ]
        self.assertEqual(attach_ore_dictionaries(items, entries), 4)
        self.assertEqual(items[0]['oredict'], ['ingotSteel', 'ingotAny'])
        self.assertEqual(items[1]['oredict'], ['ingotIron', 'ingotAny'])
        self.assertEqual(items[2]['oredict'], ['blockWool'])
        self.assertEqual(items[3]['oredict'], ['blockWool'])
        self.assertNotIn('oredict', items[4])


class EmptyOreDictTest(unittest.TestCase):
    def test_empty_export_is_rejected(self):
        with self.assertRaises(ValueError):
            require_ore_entries([], 'oreDictionary.json')


if __name__ == '__main__':
    unittest.main()
