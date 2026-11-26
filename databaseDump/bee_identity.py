"""蜂类物品的身份：蜂种与类型。

所有蜂后是同一个物品（公主蜂、雄蜂、幼虫也是），蜂种只写在 NBT 的基因组里。Dumper 在物品表与配方的蜂类物品上
带 ``beeSpecies``（蜂种等位基因 uid，与 Bees 数据同一套）和 ``beeType``。
"""

BEE_TYPES_BY_RAW_KEY = {
    "Forestry:beeDroneGE": "DRONE",
    "Forestry:beePrincessGE": "PRINCESS",
    "Forestry:beeQueenGE": "QUEEN",
    "Forestry:beeLarvaeGE": "LARVAE",
}
BEE_TYPES = frozenset(BEE_TYPES_BY_RAW_KEY.values())
FIELDS = ("beeSpecies", "beeType")


def bee_identity(record, raw_key=None) -> tuple[str, str] | None:
    """记录上完整且与基础物品一致的 (beeSpecies, beeType)；缺失或对不上时返回 None。"""
    if not isinstance(record, dict):
        return None
    species, bee_type = record.get("beeSpecies"), record.get("beeType")
    if not isinstance(species, str) or not species or bee_type not in BEE_TYPES:
        return None
    expected = BEE_TYPES_BY_RAW_KEY.get(raw_key)
    if expected and expected != bee_type:
        return None
    return species, bee_type
