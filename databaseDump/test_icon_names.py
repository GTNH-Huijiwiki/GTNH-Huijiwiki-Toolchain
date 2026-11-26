"""流体图标按流体 key 命名：换算规则，以及图片计划与状态库读取本地文件。"""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from icon_names import fluid_icon_renames, icon_files, local_path
from image_state import icon_hashes

KEY = 'gregtech:gt.GregTech_FluidDisplay:'


def record(short_key, icon=None):
    icon = icon or 'icon_' + short_key.replace(':', '_') + '.png'
    return {'shortKey': short_key, 'iconName': icon}


class IconNameTests(unittest.TestCase):
    def test_fluid_display_icons_are_named_by_fluid_key(self):
        fluids = [{'gtDisplayKey': KEY + '462', 'key': 'fluid.molten.gold'},
                  {'gtDisplayKey': KEY + '7', 'key': 'fluid.diluted monazite mud'},
                  {'gtDisplayKey': KEY + '8', 'key': 'fluid.cropsnh:jagi'}]
        renames = fluid_icon_renames(fluids, [
            record(KEY + '462'), record(KEY + '462_AbC'), record(KEY + '7'), record(KEY + '8'),
            record(KEY + '999'), record('minecraft:stone'),
            record('gregtech:gt.Volumetric_Flask_x', 'icon_gregtech_gt.Volumetric_Flask.png')])
        self.assertEqual(renames['icon_gregtech_gt.GregTech_FluidDisplay_462.png'], 'icon_fluid.molten.gold.png')
        self.assertEqual(renames['icon_gregtech_gt.GregTech_FluidDisplay_462_dynamic.png'],
                         'icon_fluid.molten.gold_dynamic.png')
        self.assertEqual(renames['icon_gregtech_gt.GregTech_FluidDisplay_462_AbC.png'],
                         'icon_fluid.molten.gold_AbC.png')
        self.assertEqual(renames['icon_gregtech_gt.GregTech_FluidDisplay_7.png'],
                         'icon_fluid.diluted_monazite_mud.png')
        self.assertEqual(renames['icon_gregtech_gt.GregTech_FluidDisplay_8.png'], 'icon_fluid.cropsnh_jagi.png')
        # 没有对应流体的展示物品与其他图标保持原名。
        self.assertEqual(len(renames), 8)

    def test_names_that_collapse_to_one_title_are_rejected(self):
        fluids = [{'gtDisplayKey': KEY + '1', 'key': 'fluid.a b'}, {'gtDisplayKey': KEY + '2', 'key': 'fluid.a_b'}]
        with self.assertRaisesRegex(ValueError, '重名'):
            fluid_icon_renames(fluids, [record(KEY + '1'), record(KEY + '2')])

    def test_local_files_are_read_through_wiki_names(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            (source / 'icons').mkdir()
            (source / 'icons' / 'icon_gregtech_gt.GregTech_FluidDisplay_3.png').write_bytes(b'gold')
            (source / 'icons' / 'icon_minecraft_stone.png').write_bytes(b'stone')
            (source / 'fluid.json').write_text(json.dumps(
                {'fluids': [{'gtDisplayKey': KEY + '3', 'key': 'fluid.molten.gold'}]}), 'utf-8')
            (source / 'itemicon.json').write_text(json.dumps({'shortKey': [record(KEY + '3')]}), 'utf-8')
            names = {'icon_fluid.molten.gold.png', 'icon_minecraft_stone.png'}
            self.assertEqual(icon_files(source / 'icons'), names)
            self.assertEqual(local_path(source / 'icons', 'icon_fluid.molten.gold.png').read_bytes(), b'gold')
            self.assertEqual(icon_hashes(source / 'icons', names)['icon_fluid.molten.gold.png'],
                             hashlib.sha1(b'gold').hexdigest())


if __name__ == '__main__':
    unittest.main()
