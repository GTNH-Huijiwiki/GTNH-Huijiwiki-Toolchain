import json
import unittest
from pathlib import Path

from split_all import dimension_biomes_page, localize_biomes, with_climate_names

NAMES = {'biomes': [{'name': 'Oasis', 'zh': '绿洲', 'bookPages': [219]},
                    {'name': 'Oasis', 'zh': '绿洲（RWG2）', 'id': 323},
                    {'name': 'Plains', 'zh': '平原'},
                    {'name': 'Jungle', 'zh': ''}],
         'types': {'PLAINS': '平原'},
         'forestryTemperature': {'NORMAL': '一般'}, 'forestryHumidity': {'ARID': '干旱'}}


class LocalizeBiomesTest(unittest.TestCase):
    def test_id_then_pages_then_unique_name(self):
        biomes = [{'id': 323, 'name': 'Oasis', 'bookPages': [206]},
                  {'id': 88, 'name': 'Oasis', 'bookPages': [219]},
                  {'id': 235, 'name': 'Oasis'},
                  {'id': 1, 'name': 'Plains', 'types': ['PLAINS', 'NEW'], 'forestryTemperature': 'NORMAL',
                   'forestryHumidity': 'ARID'},
                  {'id': 21, 'name': 'Jungle'}]
        missing = localize_biomes(biomes, NAMES)
        self.assertEqual([b.get('nameZh') for b in biomes], ['绿洲（RWG2）', '绿洲', None, '平原', None])
        self.assertEqual(missing, ['Oasis', 'Jungle'])
        self.assertEqual(biomes[3]['typesZh'], ['平原', 'NEW'])
        self.assertEqual((biomes[3]['forestryTemperatureZh'], biomes[3]['forestryHumidityZh']), ('一般', '干旱'))


class ClimateNamesTest(unittest.TestCase):
    def test_climate_names_come_from_the_export(self):
        names = with_climate_names({'types': {}}, {'forestryClimates': {'temperature': {'NORMAL': '普通'},
                                                                       'humidity': {'ARID': '干旱'}}})
        self.assertEqual((names['forestryTemperature'], names['forestryHumidity']),
                         ({'NORMAL': '普通'}, {'ARID': '干旱'}))


class ConfiguredRwgNamesTest(unittest.TestCase):
    """RWG1 与 RWG2 有一批同名群系：整合包默认用的 RWG2 不加括注，RWG1 加“（RWG1）”，两代都按 ID 配置。"""

    RWG1 = {200: '冰河', 202: '寒冷河流', 205: '温带河流', 207: '炎热河流', 209: '潮湿河流', 211: '绿洲河流',
            213: '冰洋', 214: '寒冷海洋', 216: '温带海洋', 218: '炎热海洋', 223: '海洋绿洲', 224: '降雪沙漠',
            225: '降雪森林', 226: '冰冷平原', 227: '寒冷森林', 228: '炎热平原', 229: '炎热森林', 230: '炎热沙漠',
            231: '平原', 232: '热带岛屿', 233: '红杉林', 234: '丛林', 235: '绿洲', 236: '温带森林', 237: '潮湿海洋'}
    RWG2 = dict(zip(range(300, 330), (
        '冰河 寒冷河流 温带河流 炎热河流 潮湿河流 绿洲河流 冰洋 寒冷海洋 温带海洋 炎热海洋 潮湿海洋 海洋绿洲 降雪沙漠 '
        '降雪森林 冰冷平原 寒冷森林 炎热平原 炎热森林 炎热沙漠 平原 热带岛屿 红杉林 丛林 绿洲 温带森林 丛林平顶山 '
        '高地雨林 丛林丘陵 丛林峡谷 红杉丛林').split()))

    def test_names_by_id(self):
        names = json.loads((Path(__file__).parent / 'config' / 'biome_names.json').read_text('utf-8'))
        expected = {**{i: f'{zh}（RWG1）' for i, zh in self.RWG1.items()}, **self.RWG2}
        biomes = [{'id': i, 'name': 'unused'} for i in expected]
        localize_biomes(biomes, names)
        self.assertEqual({b['id']: b.get('nameZh') for b in biomes}, expected)


class DimensionBiomesPageTest(unittest.TestCase):
    def test_sorted_dimensions_and_pruned_fields(self):
        data = {'method': {'radius': 8192},
                'dimensions': [
                    {'dimensionId': 1, 'providerClass': 'x', 'samples': 9, 'biomes': [9, 9], 'spawnBiomes': [], 'chunkManagerSource': 'provider'},
                    {'dimensionId': -1, 'biomes': [8], 'spawnBiomes': [8]},
                    {'dimensionId': 7, 'biomes': [], 'errors': ['no chunk manager']}]}
        page = dimension_biomes_page(data)
        self.assertEqual(page['method'], {'radius': 8192})
        self.assertEqual(page['dimensions'], [
            {'dimensionId': -1, 'biomes': [8]},
            {'dimensionId': 1, 'biomes': [9], 'chunkManagerSource': 'provider'},
            {'dimensionId': 7, 'biomes': [], 'errors': ['no chunk manager']}])


if __name__ == '__main__':
    unittest.main()
