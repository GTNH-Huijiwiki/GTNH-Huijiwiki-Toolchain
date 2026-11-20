"""魔导手册拆分：分类、要素、研究各成数字分页，范围划分与研究表一致。"""

import json
import os
from collections import Counter
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bounded_json import HARD_MAX_BYTES, encode_json
from data_version import addon_mods
import split_all


class ThaumonomiconPagesTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.source = root / 'dumps'
        self.source.mkdir()
        self.version = root / 'version'
        self.output = self.version / 'Thaumonomicon'
        context = patch.multiple(split_all, DUMPS_DIR=str(self.source),
                                 REFERENCE_DUMPS_DIR=str(self.source), VERSION=str(self.version),
                                 local_mod_name=lambda: 'GTNH')
        context.start()
        self.addCleanup(context.stop)

    def write_source(self, data):
        (self.source / 'tc4thaumonomicon.json').write_bytes(encode_json(data))

    def records(self, folder, field):
        rows = []
        for page in sorted((self.output / folder).glob('*.json'), key=lambda path: int(path.stem)):
            rows.extend(json.loads(page.read_bytes())[field])
        return rows

    def sample(self):
        return {
            'categories': {'BASICS': {'Key': 'BASICS'}, 'ADDON': {'Key': 'ADDON'}, 'EMPTY': {'Key': 'EMPTY'}},
            'aspects': {'aer': {'Tag': 'aer'}, 'ignis': {'Tag': 'ignis'}},
            'researches': {
                'A': {'Key': 'A', 'CategoryKey': 'BASICS', 'sourceMod': 'Thaumcraft', 'Pages': [{'type': 'text'}]},
                'B': {'Key': 'B', 'CategoryKey': 'ADDON', 'sourceMod': 'TwistSpaceTechnology'},
                'C': {'Key': 'C', 'CategoryKey': 'BASICS'},
            },
        }

    def test_task_is_registered_and_input_is_optional(self):
        self.assertIs(split_all.TASKS['thaumonomicon'][1], split_all.do_thaumonomicon)
        split_all.do_thaumonomicon(False)
        self.assertFalse(self.output.exists())
        (self.source / 'tc4thaumonomicon.json').write_bytes(b'')
        split_all.do_thaumonomicon(False)
        self.assertFalse(self.output.exists())

    def test_base_pack_keeps_records_and_drops_addon_research_and_unused_categories(self):
        self.write_source(self.sample())
        split_all.do_thaumonomicon(False)
        self.assertEqual([row['Key'] for row in self.records('Researches', 'tc4ThaumonomiconResearches')], ['A', 'C'])
        self.assertEqual([row['Key'] for row in self.records('Categories', 'tc4ThaumonomiconCategories')], ['BASICS'])
        self.assertEqual([row['Tag'] for row in self.records('Aspects', 'tc4Aspects')], ['aer', 'ignis'])
        self.assertEqual(self.records('Researches', 'tc4ThaumonomiconResearches')[0]['Pages'], [{'type': 'text'}])

    def test_addon_selects_by_source_mod_and_has_no_aspects(self):
        self.write_source(self.sample())
        with patch.object(split_all, 'DUMPS_DIR', str(self.source / 'filtered/twistspacetechnology')),                 patch.object(split_all, 'local_mod_name', return_value='twistspacetechnology'):
            split_all.do_thaumonomicon(False)
        self.assertEqual([row['Key'] for row in self.records('Researches', 'tc4ThaumonomiconResearches')], ['B'])
        self.assertEqual([row['Key'] for row in self.records('Categories', 'tc4ThaumonomiconCategories')], ['ADDON'])
        self.assertFalse((self.output / 'Aspects').exists())

    def test_pages_stay_within_limit_and_obsolete_pages_are_replaced(self):
        data = self.sample()
        data['researches'] = {str(i): {'Key': str(i), 'CategoryKey': 'BASICS', 'Text': 'x' * 800000}
                              for i in range(4)}
        self.write_source(data)
        stale = self.output / 'Researches'
        stale.mkdir(parents=True)
        (stale / '99.json').write_text('{}', encoding='utf-8')
        split_all.do_thaumonomicon(False)
        pages = sorted((self.output / 'Researches').glob('*.json'), key=lambda path: int(path.stem))
        self.assertEqual(len(pages), 2)
        for page in pages:
            self.assertLessEqual(page.stat().st_size, HARD_MAX_BYTES)
        self.assertEqual(len(self.records('Researches', 'tc4ThaumonomiconResearches')), 4)


    def test_public_addon_ids_are_casefolded_registration_ids(self):
        self.assertEqual(addon_mods(), {'twistspacetechnology', 'sciencenotleisure', 'programmablehatches'})

    def test_base_and_addon_scopes_partition_the_complete_export(self):
        rows = [
            {'Key': 'TST_BASE', 'CategoryKey': 'SHARED', 'sourceMod': 'Thaumcraft'},
            {'Key': 'TST_ADDON', 'CategoryKey': 'SHARED', 'sourceMod': 'TwistSpaceTechnology'},
            {'Key': 'GTNL_BASE', 'CategoryKey': 'ADDON', 'sourceMod': 'dreamcraft'},
            {'Key': 'GTNL_ADDON', 'CategoryKey': 'ADDON', 'sourceMod': 'sciencenotleisure'},
            {'Key': 'PH_BASE', 'CategoryKey': 'BASICS', 'sourceMod': 'KekzTech'},
            {'Key': 'PH_ADDON', 'CategoryKey': 'BASICS', 'sourceMod': 'ProgrammableHatches'},
            {'Key': 'SAME_PREFIX', 'CategoryKey': 'SHARED', 'sourceMod': 'TwistSpaceTechnologyExtension'},
            {'Key': 'OLD', 'CategoryKey': 'ADDON'},
        ]
        self.write_source({'categories': {key: {'Key': key} for key in ('BASICS', 'SHARED', 'ADDON')},
                           'aspects': {'aer': {'Tag': 'aer'}},
                           'researches': {row['Key']: row for row in rows}})
        scopes = {}
        for mod in ('GTNH', 'twistspacetechnology', 'sciencenotleisure', 'programmablehatches'):
            source = self.source if mod == 'GTNH' else self.source / 'filtered' / mod
            with patch.object(split_all, 'DUMPS_DIR', str(source)),                     patch.object(split_all, 'VERSION', str(self.version / mod)),                     patch.object(split_all, 'local_mod_name', return_value=mod):
                split_all.do_thaumonomicon(False)
            page = self.version / mod / 'Thaumonomicon/Researches/0.json'
            scopes[mod] = json.loads(page.read_bytes())['tc4ThaumonomiconResearches']
        self.assertEqual(scopes['GTNH'], [rows[index] for index in (0, 2, 4, 6, 7)])
        for mod, index in (('twistspacetechnology', 1), ('sciencenotleisure', 3), ('programmablehatches', 5)):
            self.assertEqual(scopes[mod], [rows[index]])
        # 每条研究在所有范围中恰好出现一次。
        found = [json.dumps(row, sort_keys=True) for selected in scopes.values() for row in selected]
        self.assertEqual(Counter(found), Counter(json.dumps(row, sort_keys=True) for row in rows))
        self.assertFalse((self.version / 'twistspacetechnology' / 'Thaumonomicon/Aspects').exists())


class ThaumonomiconEntryPointTests(unittest.TestCase):
    """命令行入口：只处理魔导手册时，附属可以没有自己的 filtered 目录；其他任务仍要求有。"""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'dumps'
        self.source.mkdir()
        self.filtered = self.source / 'filtered' / 'TwistSpaceTechnology'
        self.output = self.root / 'wikitext/Data/Versions/TST/fixture/Thaumonomicon/Researches/0.json'
        self.rows = [{'Key': 'BASE', 'CategoryKey': 'SHARED', 'sourceMod': 'Thaumcraft'},
                     {'Key': 'ADDON', 'CategoryKey': 'SHARED', 'sourceMod': 'TwistSpaceTechnology'}]
        self.write(self.source, self.rows)
        environment = patch.dict(os.environ, {'DUMPS_DIR': str(self.source), 'MOD_LOCAL_NAME': 'TwistSpaceTechnology',
                                              'MOD_REMOTE_NAME': 'TST', 'MOD_VERSION': 'fixture'})
        environment.start()
        self.addCleanup(environment.stop)
        # main 每次都会按环境变量重设这些模块全局量。
        names = ('DUMPS_DIR', 'REFERENCE_DUMPS_DIR', 'VERSION_TAG', 'VERSION', 'AUX_DIR')
        context = patch.multiple(split_all, _PROJECT_ROOT=str(self.root),
                                 **{name: getattr(split_all, name) for name in names})
        context.start()
        self.addCleanup(context.stop)

    @staticmethod
    def write(folder, rows):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'tc4thaumonomicon.json').write_bytes(encode_json(
            {'categories': {'SHARED': {'Key': 'SHARED'}}, 'researches': {row['Key']: row for row in rows}}))

    def test_task_extracts_full_export_without_filtered_directory(self):
        with patch('sys.argv', ['split_all.py', 'thaumonomicon']):
            split_all.main()
        self.assertEqual(json.loads(self.output.read_bytes()), {'tc4ThaumonomiconResearches': [self.rows[1]]})
        self.assertFalse(self.filtered.exists())

    def test_task_uses_existing_filtered_export(self):
        own = [{'Key': 'OWN', 'CategoryKey': 'SHARED'}]
        self.write(self.filtered, own)
        with patch('sys.argv', ['split_all.py', 'thaumonomicon']):
            split_all.main()
        self.assertEqual(json.loads(self.output.read_bytes()), {'tc4ThaumonomiconResearches': own})

    def test_default_and_other_tasks_require_their_filtered_directory(self):
        for arguments in ([], ['items'], ['thaumonomicon', 'recipes']):
            with self.subTest(arguments=arguments),                     patch('sys.argv', ['split_all.py', *arguments]),                     self.assertRaisesRegex(ValueError, 'python split_all.py thaumonomicon'):
                split_all.main()
            self.assertFalse(self.output.exists())

    def test_removed_research_task_is_rejected(self):
        self.assertNotIn('tc4research', split_all.TASKS)

    def test_configure_source_remains_strict_for_other_callers(self):
        with self.assertRaisesRegex(ValueError, '数据源目录不存在'):
            split_all.configure_source()


if __name__ == '__main__':
    unittest.main()
