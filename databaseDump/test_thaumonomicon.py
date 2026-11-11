"""魔导手册拆分：分类、要素、研究各成数字分页，范围划分与研究表一致。"""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bounded_json import HARD_MAX_BYTES, encode_json
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


if __name__ == '__main__':
    unittest.main()
