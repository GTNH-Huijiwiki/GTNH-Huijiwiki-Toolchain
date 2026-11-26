"""Dumper 的 bees/*.json -> Bees/{Species,Mutations,Alleles,Branches,Frames,Flowers,Hives,Meta}/N.json。

游戏里读得到的内容都放进来，同一内容只存一处：物品只记 key（名称、图标查物品表），气候只记 id（中文名在群系数据里）。
游戏里没有、靠人读代码写的说明（比如特殊效果的实际作用）留在维基页面上。
每个目录是数字分页，页内一个顶层数组，字段名全库唯一，便于线上 Lua 用 $unwind 查询。
"""

import json
from pathlib import Path

from bounded_json import DEFAULT_MAX_BYTES, HARD_MAX_BYTES, array_pages, write_bounded

# (输出子目录, 页内数组字段)
SECTIONS = (
    ("Species", "beeSpecies"),
    ("Mutations", "beeMutations"),
    ("Alleles", "beeAlleles"),
    ("Branches", "beeBranches"),
    ("Frames", "beeFrames"),
    ("Flowers", "beeFlowers"),
    ("Hives", "beeHives"),
    ("Meta", "beeMeta"),
)


def load(source: Path) -> dict[str, list]:
    """读 Dumper 导出，整理成 {页内数组字段: 记录}。"""
    def read(name):
        return json.loads((source / f"{name}.json").read_text(encoding="utf-8"))

    species, mutations = read("species"), read("mutations")
    # Data 页的键名里不能有点号（会被改写成全角），以 uid 为键的表改成数组。
    branches = [{"uid": uid, **row} for uid, row in sorted(species["branches"].items())]
    meta = {
        "chromosomes": species["chromosomes"],
        "mode": mutations["mode"],
        "modes": mutations["modes"],
        "researchCatalysts": mutations["researchCatalysts"],
    }
    return {
        "beeSpecies": species["species"],
        "beeMutations": mutations["mutations"],
        "beeAlleles": read("alleles")["alleles"],
        "beeBranches": branches,
        "beeFrames": read("frames")["frames"],
        "beeFlowers": read("flowers")["flowers"],
        "beeHives": read("hives")["hives"],
        "beeMeta": [meta],
    }


def check_keys(value, path=""):
    """Data 命名空间是文档库：键名不能含点号，也不能以 $ 开头。"""
    if isinstance(value, dict):
        for key, child in value.items():
            if "." in key or key.startswith("$"):
                raise ValueError(f"蜜蜂数据的键名不能进 Data 页: {key!r}（{path or '根'}）")
            check_keys(child, f"{path}/{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            check_keys(child, f"{path}[{index}]")


def referenced_items(tables: dict[str, list]) -> set[str]:
    """各表引用到的物品 key。"""
    keys = set()
    for row in tables["beeSpecies"]:
        keys.update(entry["item"] for field in ("products", "specialties") for entry in row.get(field, ()))
    keys.update(row["item"] for row in tables["beeFrames"])
    keys.update(block["item"] for row in tables["beeFlowers"] for block in row["blocks"] if "item" in block)
    keys.update(entry["item"] for row in tables["beeMeta"] for entry in row["researchCatalysts"])
    for hive in tables["beeHives"]:
        if "item" in hive:
            keys.add(hive["item"])
        keys.update(entry["item"] for drop in hive["drops"] for entry in drop["additional"])
    return keys


def check_references(tables: dict[str, list]) -> list[str]:
    """杂交、基因组与蜂巢引用的物种和等位基因都应能在表里查到；返回问题清单。"""
    species = {row["uid"] for row in tables["beeSpecies"]}
    alleles = {row["uid"] for row in tables["beeAlleles"]}
    problems = []
    for row in tables["beeMutations"]:
        for uid in (*row["parents"], row["result"]):
            if uid not in species:
                problems.append(f"杂交引用了未知物种: {uid}")
    for row in tables["beeSpecies"]:
        for uid in row.get("genome", {}).values():
            if uid not in alleles:
                problems.append(f"基因组引用了未知等位基因: {uid}（{row['uid']}）")
    for hive in tables["beeHives"]:
        for drop in hive["drops"]:
            if drop.get("species") not in species:
                problems.append(f"蜂巢掉落引用了未知物种: {drop.get('species')}（{hive['source']}:{hive['id']}）")
    return sorted(set(problems))


def write(tables: dict[str, list], version_dir: Path) -> dict[str, int]:
    """写分页并清掉多余的旧分页；返回每个目录的页数。"""
    check_keys(tables)
    counts = {}
    for folder, field in SECTIONS:
        output = Path(version_dir) / "Bees" / folder
        written = []
        for part, content in enumerate(array_pages(tables[field], {}, field, DEFAULT_MAX_BYTES)):
            path = output / f"{part}.json"
            write_bounded(path, content, HARD_MAX_BYTES)
            written.append(path)
        for old in output.glob("[0-9]*.json"):
            if old.stem.isdecimal() and old not in written:
                old.unlink()
        counts[folder] = len(written)
    return counts
