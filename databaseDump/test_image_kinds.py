import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import image_kinds
from image_kinds import entity_plan, kind_from_argv
from item_names import is_mob_display


def _write(directory: Path, images: dict, entities: list):
    (directory / 'entity-images').mkdir()
    for row in images.values():
        for field in ('full', 'head'):
            if row.get(field):
                (directory / 'entity-images' / row[field]).write_bytes(b'png')
    (directory / 'entity-images.json').write_text(json.dumps({'images': images}), 'utf-8')
    (directory / 'entities.json').write_text(json.dumps({'entities': entities}), 'utf-8')


class ImageKindsTest(unittest.TestCase):
    def test_kind_argument(self):
        self.assertEqual(kind_from_argv([]), 'items')
        self.assertEqual(kind_from_argv(['--kind', 'entities', '-t', '4']), 'entities')
        self.assertEqual(kind_from_argv(['--kind=entities']), 'entities')
        with self.assertRaises(SystemExit):
            kind_from_argv(['--kind', 'blocks'])

    def test_mob_display_items_take_no_names(self):
        self.assertTrue(is_mob_display({'key': 'ThaumicTinkerer:mobDisplay', 'translatedName': '蝙蝠'}))
        self.assertTrue(is_mob_display({'key': 'ThaumicHorizons:dummy'}))
        self.assertFalse(is_mob_display({'key': 'ThaumicHorizons:dummyVat'}))
        self.assertFalse(is_mob_display({'key': 'minecraft:bone'}))

    def test_entity_plan(self):
        images = {
            'Zombie': {'full': 'Entity Zombie.png', 'head': 'EntityHead Zombie.png'},
            'SpecialMobs.SpecialZombie': {'full': 'Entity SpecialMobs.SpecialZombie.png',
                                          'head': 'EntityHead SpecialMobs.SpecialZombie.png'},
            'Enderman': {'head': 'EntityHead Enderman.png'},
            'SpecialMobs.SpecialEnderman': {'head': 'EntityHead SpecialMobs.SpecialEnderman.png'},
            'A.Pixie': {'head': 'EntityHead A.Pixie.png'},
            'B.Goblin': {'head': 'EntityHead B.Goblin.png'},
            'Cow': {'head': 'EntityHead Cow.png'},
            'witchery.mandrake': {'head': 'EntityHead witchery.mandrake.png'},
            'NoName': {'head': 'EntityHead NoName.png'},
        }
        entities = [
            {'id': 'Zombie', 'name': '僵尸', 'mod': 'minecraft'},
            {'id': 'SpecialMobs.SpecialZombie', 'name': '僵尸', 'mod': 'SpecialMobs'},
            # 实现类被模组替换的原版实体仍按注册名算原版。
            {'id': 'Enderman', 'name': '末影人', 'mod': 'HardcoreEnderExpansion'},
            {'id': 'SpecialMobs.SpecialEnderman', 'name': '末影人', 'mod': 'SpecialMobs'},
            {'id': 'A.Pixie', 'name': '妖精'},
            {'id': 'B.Goblin', 'name': '妖精'},
            {'id': 'Cow', 'name': '牛', 'mod': 'minecraft'},
            {'id': 'witchery.mandrake', 'name': '曼德拉草'},
            {'id': 'NoName'},
        ]
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            _write(directory, images, entities)
            with mock.patch.object(image_kinds, '_item_titles', return_value={'File:曼德拉草.png'}):
                plan = entity_plan(str(directory))
        self.assertEqual(len(plan['uploadIcons']), 11)
        self.assertEqual(plan['redirects'], {
            'File:僵尸.png': 'EntityHead Zombie.png',
            'File:末影人.png': 'EntityHead Enderman.png',
            'File:牛.png': 'EntityHead Cow.png',
        })
        self.assertEqual(set(plan['redirectConflicts']), {'File:妖精.png', 'File:曼德拉草.png'})

    def test_entity_plan_webp_retires_png(self):
        images = {
            'Zombie': {'full': 'Entity Zombie.webp', 'head': 'EntityHead Zombie.webp'},
            'witchery.mandrake': {'head': 'EntityHead witchery.mandrake.webp'},
        }
        entities = [{'id': 'Zombie', 'name': '僵尸'}, {'id': 'witchery.mandrake', 'name': '曼德拉草'}]
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            _write(directory, images, entities)
            # 物品占着这个名字的 WebP，实体的 .webp 与 .png 别名都不能建。
            with mock.patch.object(image_kinds, '_item_titles', return_value={'File:曼德拉草.webp'}):
                plan = entity_plan(str(directory))
        self.assertEqual(plan['redirects'], {
            'File:僵尸.webp': 'EntityHead Zombie.webp',
            'File:僵尸.png': 'EntityHead Zombie.webp',
            'File:Entity Zombie.png': 'Entity Zombie.webp',
            'File:EntityHead Zombie.png': 'EntityHead Zombie.webp',
            'File:EntityHead witchery.mandrake.png': 'EntityHead witchery.mandrake.webp',
        })
        self.assertEqual(plan['retired'], [
            'File:Entity Zombie.png', 'File:EntityHead Zombie.png', 'File:EntityHead witchery.mandrake.png'])
        self.assertEqual(set(plan['redirectConflicts']), {'File:曼德拉草.webp'})

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            _write(directory, {'Cow': {'head': 'EntityHead Cow.png'}}, [{'id': 'Cow', 'name': '牛'}])
            (directory / 'entity-images' / 'EntityHead Cow.png').unlink()
            with self.assertRaises(ValueError):
                entity_plan(str(directory))


if __name__ == '__main__':
    unittest.main()
