import unittest
from ore_processing import append_flags

class OreProcessingTests(unittest.TestCase):
    def test_ordinary_dust_only_and_candidates(self):
        materials = {"A": {"OrePrefixes": {"dust": "test:dust"}}}
        ore_dictionary = {"dustA": ["other:dust"]}
        recipes = [
            {"identifier": "gt.recipe.centrifuge", "recipes": [{"inputItems": ["test:dustPure"]}]},
            {"identifier": "gt.recipe.chemicaldehydrator", "recipes": [{"inputItems": [[{"key": "#dustA", "amount": 8}]]}]},
            {"identifier": "gt.recipe.electrolyzer", "recipes": [{"outputItems": ["test:dust"]}]},
        ]
        rows = [["A", "unchanged"], ["NoDust", "unchanged"]]
        self.assertEqual(append_flags(rows, materials, ore_dictionary, recipes),
                         [["A", "unchanged", False, True, False], ["NoDust", "unchanged", False, False, False]])
        self.assertEqual(len(rows[0]), 2)

    def test_multiblock_and_missing_input_category(self):
        with self.assertRaisesRegex(ValueError, "Missing"):
            append_flags([["A"]], {}, {}, [])
        recipes = [{"identifier": "gt.recipe." + name, "recipes": [{"inputItems": [{"key": "item:dust", "amount": 3}]}]}
                   for name in ("multicentrifuge", "multidehydrator", "electrolyzer")]
        self.assertEqual(append_flags([["A"]], {}, {"dustA": ["item:dust"]}, recipes), [["A", True, True, True]])

if __name__ == '__main__':
    unittest.main()
