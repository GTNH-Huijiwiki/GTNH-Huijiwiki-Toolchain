import json
import tempfile
import unittest
from pathlib import Path

import bees


def export(directory: Path):
    files = {
        "species": {
            "chromosomes": ["SPECIES", "SPEED"],
            "branches": {"genus.apis": {"level": "GENUS", "name": "蜜蜂属", "scientific": "Apis"}},
            "species": [{"uid": "forestry.speciesForest", "branch": "genus.apis",
                         "genome": {"SPEED": "forestry.speedSlow"},
                         "products": [{"item": "Forestry:beeCombs", "chance": 0.3}], "specialties": []}],
        },
        "mutations": {"mode": {"name": "HARD", "mutationModifier": 0.75}, "modes": [],
                      "researchCatalysts": [{"item": "Forestry:honeyDrop", "suitability": 0.5}],
                      "mutations": [{"parents": ["forestry.speciesForest", "forestry.speciesForest"],
                                     "result": "forestry.speciesForest", "chance": 15.0, "conditions": []}]},
        "alleles": {"alleles": [{"uid": "forestry.speedSlow", "value": 0.8}]},
        "frames": {"frames": [{"item": "Forestry:frameUntreated", "production": 2.0}]},
        "flowers": {"flowers": [{"type": "flowersVanilla", "codeRules": [],
                                 "blocks": [{"block": "minecraft:red_flower", "item": "minecraft:red_flower",
                                             "plantWeight": 1.0}]}]},
        "hives": {"hives": [{"source": "forestry", "id": "Forestry:forest", "item": "Forestry:beehives:1",
                             "drops": [{"species": "forestry.speciesForest", "weight": 80,
                                        "additional": [{"item": "Forestry:beeCombs"}]}]}]},
    }
    for name, data in files.items():
        (directory / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")


class BeeSplitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        export(self.root)
        self.tables = bees.load(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_uid_keyed_tables_become_arrays(self):
        self.assertEqual("genus.apis", self.tables["beeBranches"][0]["uid"])
        bees.check_keys(self.tables)
        with self.assertRaises(ValueError):
            bees.check_keys({"branches": {"genus.apis": 1}})

    def test_references(self):
        self.assertEqual([], bees.check_references(self.tables))
        self.assertEqual({"Forestry:beeCombs", "Forestry:frameUntreated",
                          "Forestry:beehives:1", "Forestry:honeyDrop", "minecraft:red_flower"},
                         bees.referenced_items(self.tables))
        self.tables["beeMutations"][0]["result"] = "forestry.speciesMissing"
        self.assertEqual(["杂交引用了未知物种: forestry.speciesMissing"], bees.check_references(self.tables))

    def test_write_pages_and_remove_stale(self):
        stale = self.root / "out" / "Bees" / "Species" / "7.json"
        stale.parent.mkdir(parents=True)
        stale.write_text("{}", "utf-8")
        counts = bees.write(self.tables, self.root / "out")
        self.assertEqual({folder: 1 for folder, _ in bees.SECTIONS}, counts)
        self.assertFalse(stale.exists())
        for folder, field in bees.SECTIONS:
            page = json.loads((self.root / "out" / "Bees" / folder / "0.json").read_text("utf-8"))
            self.assertEqual(self.tables[field], page[field])


if __name__ == "__main__":
    unittest.main()
