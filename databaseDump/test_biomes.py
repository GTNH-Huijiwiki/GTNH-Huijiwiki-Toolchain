import unittest

from split_all import dimension_biomes_page, localize_biomes

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
