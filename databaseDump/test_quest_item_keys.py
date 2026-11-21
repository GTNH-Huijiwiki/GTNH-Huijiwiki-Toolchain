import unittest

from quests import attach_item_keys


class QuestItemKeysTest(unittest.TestCase):
    def record(self):
        return {
            "questID": "AAAAAAAAAAAAAAAAAAADjA",
            "properties": {"betterquesting": {"icon": {"id": "TwilightForest:item.lampOfCinders", "Damage": 0}}},
            "tasks": [{"index": 0, "requiredItems": [{"id": "TwilightForest:item.lampOfCinders", "Damage": 0}]}],
            "rewards": [
                {"index": 0, "rewards": [{"id": "TConstruct:cleaver", "Damage": 0,
                                          "tag": {"display": {"Name": "Paper Cleaver"}}}]},
                {"index": 1, "choices": [{"id": "minecraft:paper", "Damage": 4, "tag": {"x": 1}},
                                         {"id": "minecraft:stone", "Damage": 0}]},
            ],
        }

    def test_only_nbt_variants_get_keys(self):
        record = self.record()
        keys = {"AAAAAAAAAAAAAAAAAAADjA": {
            "icon": "TwilightForest:item.lampOfCinders",
            "tasks": {"0": ["TwilightForest:item.lampOfCinders"]},
            "rewards": {"0": ["TConstruct:cleaver_abc"], "1": ["minecraft:paper:4_def", "minecraft:stone"]},
        }}
        self.assertEqual(attach_item_keys(record, keys), 2)
        self.assertEqual(record["rewards"][0]["rewards"][0]["key"], "TConstruct:cleaver_abc")
        self.assertEqual(record["rewards"][1]["choices"][0]["key"], "minecraft:paper:4_def")
        self.assertNotIn("key", record["rewards"][1]["choices"][1])
        self.assertNotIn("key", record["properties"]["betterquesting"]["icon"])

    def test_missing_quest_is_untouched(self):
        record = self.record()
        self.assertEqual(attach_item_keys(record, {}), 0)


if __name__ == "__main__":
    unittest.main()
