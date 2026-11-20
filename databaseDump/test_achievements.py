import unittest

from split_all import attach_record_links, biome_tags, ids_by_item, prepare_achievements


class AchievementLinkTest(unittest.TestCase):
    def data(self):
        return {
            "achievements": [
                {"id": "achievement.portal", "icon": "minecraft:obsidian"},
                {"id": "terrasteel_ingot", "icon": "Botania:manaResource:4",
                 "condition": {"type": "inventory.add", "item": "Botania:manaResource", "meta": 4}},
                {"id": "dezils_marshmallow", "icon": "DraconicEvolution:dezilsMarshmallow",
                 "condition": {"type": "item.use.finish", "item": "DraconicEvolution:dezilsMarshmallow"}},
                {"id": "nether", "condition": {"type": "dimension.join", "id": -1}},
            ],
            "trophies": [
                {"id": "terrasteel_ingot", "item": "amazingtrophies:trophy_t1",
                 "condition": {"type": "achievement", "id": "terrasteel_ingot"},
                 "model": {"type": "item", "registryName": "Botania:manaResource", "meta": 4}},
                {"id": "portal", "condition": {"type": "achievement", "id": "portal"},
                 "model": {"type": "complex", "keys": {"B": "minecraft:obsidian"}}},
            ],
        }

    def test_records_carry_items(self):
        achievements, trophies = prepare_achievements(self.data())
        self.assertEqual(achievements[0]["items"], ["minecraft:obsidian"])
        # 图标与条件是同一物品时只记一次
        self.assertEqual(achievements[1]["items"], ["Botania:manaResource:4"])
        self.assertNotIn("items", achievements[3])
        # 模组自己的成就 ID 不带前缀；原版的补上 achievement.
        self.assertEqual(trophies[0]["achievement"], "terrasteel_ingot")
        self.assertEqual(trophies[1]["achievement"], "achievement.portal")
        # 结构模型不算关联物品，只继承成就的物品
        self.assertEqual(trophies[1]["items"], ["minecraft:obsidian"])

    def test_items_match_raw_key_and_wildcard(self):
        achievements, trophies = prepare_achievements(self.data())
        achievements.append({"id": "any_wool", "items": ["minecraft:wool:32767"]})
        achievements.append({"id": "daybloom", "items": ["Botania:specialFlower_day"]})
        items = [{"key": "Botania:manaResource:4"}, {"key": "minecraft:obsidian"},
                 {"key": "minecraft:obsidian_x", "rawKey": "minecraft:obsidian"}, {"key": "minecraft:wool:14"},
                 {"key": "Botania:specialFlower_day", "rawKey": "Botania:specialFlower"},
                 {"key": "Botania:specialFlower_night", "rawKey": "Botania:specialFlower"},
                 {"key": "amazingtrophies:trophy_t1", "rawKey": "amazingtrophies:trophy"}]
        self.assertEqual(attach_record_links(items, "achievements", ids_by_item(achievements)), 5)
        self.assertEqual(items[0]["achievements"], ["terrasteel_ingot"])
        # 不带 NBT 的写法也匹配 NBT 变体；带 NBT 的短 key 只匹配自己
        self.assertEqual(items[2]["achievements"], ["achievement.portal"])
        self.assertEqual(items[4]["achievements"], ["daybloom"])
        self.assertNotIn("achievements", items[5])
        self.assertEqual(items[3]["achievements"], ["any_wool"])
        attach_record_links(items, "trophies", ids_by_item(trophies))
        self.assertEqual(items[1]["trophies"], ["portal"])
        self.assertEqual(items[6]["trophies"], ["terrasteel_ingot"])


class BiomeTagsTest(unittest.TestCase):
    def test_counts_and_translations(self):
        biomes = [{"types": ["FOREST", "HILLS"], "forestryTemperature": "NORMAL", "forestryHumidity": "DAMP"},
                  {"types": ["FOREST"], "forestryTemperature": "COLD", "forestryHumidity": "DAMP"}]
        names = {"types": {"FOREST": "森林"}, "forestryTemperature": {"COLD": "寒冷"}, "forestryHumidity": {}}
        tags = biome_tags(biomes, names)
        self.assertEqual(tags["types"], [{"id": "FOREST", "zh": "森林", "count": 2},
                                         {"id": "HILLS", "zh": None, "count": 1}])
        self.assertEqual(tags["forestryHumidity"], [{"id": "DAMP", "zh": None, "count": 2}])


if __name__ == "__main__":
    unittest.main()
