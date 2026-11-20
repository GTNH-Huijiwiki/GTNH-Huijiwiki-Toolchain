import unittest

from split_all import flatten_dimension


class FlattenDimensionTest(unittest.TestCase):
    def test_gtnei_fields_return_to_top_level(self):
        record = {'key': 'Mo', 'dimensionId': 28,
                  'gtneiInfo': {'abbreviatedName': 'Mo', 'localizedName': '月球', 'tier': 1, 'stoneTypes': ['Moon']},
                  'galacticSpaceInfo': {'gravity': 0.062}}
        self.assertEqual(flatten_dimension(record), {
            'abbreviatedName': 'Mo', 'localizedName': '月球', 'tier': 1, 'stoneTypes': ['Moon'],
            'key': 'Mo', 'dimensionId': 28, 'galacticSpaceInfo': {'gravity': 0.062}})

    def test_forge_only_dimension_is_kept(self):
        record = {'key': 'dim:-28', 'dimensionId': -28, 'providerState': 'unbound'}
        self.assertEqual(flatten_dimension(record), record)

    def test_old_flat_record_is_unchanged(self):
        record = {'abbreviatedName': 'Ow', 'tier': 0}
        self.assertEqual(flatten_dimension(record), record)


if __name__ == '__main__':
    unittest.main()
