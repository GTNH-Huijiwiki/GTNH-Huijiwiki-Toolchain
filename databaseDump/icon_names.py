"""流体图标按流体 key 命名。

Dumper 用物品 shortKey 命名图标，而 GT 流体展示物品的 shortKey 带数字流体 ID。整合包升级后 ID 挪位，
同一个文件名就换成了别的流体：大批图片要重传，旧版本数据引用的图片也被覆盖。这里把本地文件名换算成
维基文件名，例如 ``icon_gregtech_gt.GregTech_FluidDisplay_462.png`` → ``icon_fluid.molten.gold.png``。

数据页、图片计划、状态库和黑名单一律使用维基文件名；本地 dumps 不改名，读本地文件时用 local_path()。
"""

import json
import re
from functools import lru_cache
from pathlib import Path

_GT_DISPLAY = re.compile(r'(gregtech:gt\.GregTech_FluidDisplay:\d+)(?:_(.+))?')
# 与 Dumper 的 Utils.replaceHuijiIllegalChars 一致，另外替换标题中不能用或会与下划线混同的字符。
_ILLEGAL = re.compile(r'[/:*"<>|\\?\[\]{}# ]')


def _stem(value):
    return 'icon_' + _ILLEGAL.sub('_', value)


def _title(name):
    """MediaWiki 视空格与下划线相同、首字母不分大小写。"""
    value = name.replace('_', ' ')
    return value[:1].upper() + value[1:]


def fluid_icon_renames(fluids, records) -> dict[str, str]:
    """fluid.json 的 fluids 与 itemicon.json 的 shortKey 记录 → {本地文件名: 维基文件名}（含动画条带）。"""
    keys = {fluid['gtDisplayKey']: fluid['key'] for fluid in fluids if fluid.get('gtDisplayKey')}
    renames, taken = {}, {}
    kept = []
    for record in records:
        local = record.get('iconName')
        if not local:
            continue
        match = _GT_DISPLAY.fullmatch(record.get('shortKey') or '')
        # 只换算按自身 shortKey 命名的图标；借用别的图标的记录跟随那条记录。
        if (not match or match[1] not in keys
                or local != 'icon_' + re.sub(r'[/:*"<>|\\?]', '_', record['shortKey']) + '.png'):
            kept.append(local)
            continue
        suffix = '_' + match[2] if match[2] else ''
        wiki = _stem(keys[match[1]] + suffix) + '.png'
        if taken.setdefault(_title(wiki), local) != local:
            raise ValueError(f'流体图标改名后重名: {wiki}（{taken[_title(wiki)]} 与 {local}）')
        renames[local] = wiki
    clash = sorted(name for name in kept if name not in renames and _title(name) in taken)
    if clash:
        raise ValueError(f'流体图标改名后与现有图标重名: {clash[:5]}')
    renames.update({local.removesuffix('.png') + '_dynamic.png': wiki.removesuffix('.png') + '_dynamic.png'
                    for local, wiki in list(renames.items())})
    return renames


def load_renames(fluid_json, itemicon_json) -> dict[str, str]:
    """缺任一文件（实体、指南图片目录，或不含流体的独立模组）时不改名。"""
    fluid_json, itemicon_json = Path(fluid_json), Path(itemicon_json)
    if not fluid_json.is_file() or not itemicon_json.is_file():
        return {}
    return fluid_icon_renames(json.loads(fluid_json.read_text('utf-8'))['fluids'],
                              json.loads(itemicon_json.read_text('utf-8'))['shortKey'])


@lru_cache(maxsize=None)
def _directory_renames(source: str):
    renames = load_renames(Path(source) / 'fluid.json', Path(source) / 'itemicon.json')
    return renames, {wiki: local for local, wiki in renames.items()}


def directory_renames(source) -> dict[str, str]:
    """dumps 目录的 {本地文件名: 维基文件名}。"""
    return _directory_renames(str(Path(source).resolve()))[0]


def local_path(icons_dir, name) -> Path:
    """维基文件名对应的本地图标路径；icons_dir 是 dumps 下的图标目录。"""
    icons_dir = Path(icons_dir)
    return icons_dir / _directory_renames(str(icons_dir.parent.resolve()))[1].get(name, name)


def icon_files(icons_dir) -> set[str]:
    """图标目录中全部 PNG 的维基文件名。"""
    renames = directory_renames(Path(icons_dir).parent)
    return {renames.get(path.name, path.name) for path in Path(icons_dir).glob('*.png')}
