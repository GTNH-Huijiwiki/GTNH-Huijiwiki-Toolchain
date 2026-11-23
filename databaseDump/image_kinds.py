"""图片上传任务的种类：物品图片（items）、实体图片（entities）与 GuideNH 图片（guidenh）。

image_inventory.py、image_upload.py、image_redirect.py 用 --kind 选择种类，默认 items：

- items：物品与流体图标（dumps/icons），上传计划与中文别名来自 split_all.py items 的结果，
  状态记在 image_inventory.db（GTNH）或 source_state/<模组>/<版本>/image_inventory.db。
- entities：GTNH-Dumper entityimages 任务导出的实体图片（dumps/entity-images），
  全身照 ``Entity <注册名>.png`` 与像素头像 ``EntityHead <注册名>.png`` 都上传；
  中文别名 ``<实体中文名>.png`` 重定向到头像。状态记在
  source_state/<模组>/<版本>/entity_image_inventory.db。
- guidenh：split_all.py guidenh 放进 source_state/GuideNH/images/ 的图片（指南页面里的图，
  以及官方静态站预渲染的 3D 场景静态图），上传计划在 image-plan.json，没有中文别名；
  状态记在 source_state/GuideNH/image_inventory.db。文件名由内容决定的场景图换了内容就是新文件名，
  不会覆盖线上旧图。

几种图片共用同一套判定（见 image_check.py）：线上文件与本地字节相同或视觉规则判定相同时不再上传，
重跑时按状态库与文件 sha1 缓存跳过已上传的图片。
"""

import json
import os
import sys
from pathlib import Path

from data_version import local_mod_name, source_directory, version_tag

KINDS = ('items', 'entities', 'guidenh')
LABELS = {'items': '物品图片', 'entities': '实体图片', 'guidenh': 'GuideNH 图片'}


def kind_from_argv(argv=None) -> str:
    """读取 --kind <种类> 或 --kind=<种类>；缺省为 items。"""
    argv = sys.argv[1:] if argv is None else argv
    kind = 'items'
    for index, arg in enumerate(argv):
        if arg == '--kind' and index + 1 < len(argv):
            kind = argv[index + 1]
        elif arg.startswith('--kind='):
            kind = arg.split('=', 1)[1]
    if kind not in KINDS:
        raise SystemExit(f'未知图片种类: {kind}（可选 {", ".join(KINDS)}）')
    return kind


def kind_paths(kind: str, script_dir: str) -> tuple[str, str, str, str | None]:
    """(数据源目录, 状态库, 图片目录, 物品中文别名表)；实体图片没有别名表。"""
    from image_version import image_paths
    if kind == 'items':
        return image_paths(script_dir)
    if kind == 'guidenh':
        import guidenh_scope
        return (str(guidenh_scope.state_directory()), str(guidenh_scope.image_database()),
                str(guidenh_scope.images_directory()), None)
    _, version = version_tag(required=True).split('/')
    mod = local_mod_name(required=True)
    source = source_directory(os.environ.get('DUMPS_DIR', ''), mod, check_exists=False)
    db = Path(script_dir) / 'source_state' / mod / version / 'entity_image_inventory.db'
    return str(source), str(db), str(source / 'entity-images'), None


def kind_plan(kind: str, source: str, resolved_csv: str | None) -> dict:
    if kind == 'items':
        from image_plan import prepare_plan
        return prepare_plan(source, resolved_csv)
    if kind == 'guidenh':
        return guidenh_plan()
    return entity_plan(source)


def guidenh_plan() -> dict:
    """GuideNH 图片计划：split_all.py guidenh 写出的 image-plan.json，没有重定向。"""
    import guidenh_scope
    path = guidenh_scope.image_plan_path()
    if not path.is_file():
        raise ValueError(f'缺少 GuideNH 图片计划: {path}（请先运行 split_all.py guidenh）')
    plan = json.loads(path.read_text('utf-8'))
    missing = [name for name in plan['uploadIcons'] if not (guidenh_scope.images_directory() / name).is_file()]
    if missing:
        raise ValueError(f'计划中的图片缺失: {missing[:5]}（请重新运行 split_all.py guidenh）')
    print(f"Guide image plan: {len(plan['uploadIcons']):,} files")
    return {'schemaVersion': 1, 'uploadIcons': plan['uploadIcons'], 'redirects': {}}


def kind_exempt(kind: str, source: str) -> dict[str, str]:
    """免更新名单与 config/image_unstable.json 共用；实体图片的文件名不与物品图标重叠。"""
    if kind == 'guidenh':
        return {}  # 指南图片不是游戏渲染的物品图标，没有动图或渲染波动
    from image_check import exempt_icons
    return exempt_icons(source)


def kind_unstable(kind: str) -> set[str]:
    if kind == 'guidenh':
        return set()
    from image_check import unstable_icons
    return unstable_icons()


import re

_MOB_DISPLAY_ICON = re.compile(r'(?i)^icon[ _]ThaumicTinkerer[ _]mobDisplay|^icon[ _]ThaumicHorizons[ _]dummy\.png$')


def _item_titles(source: Path) -> set[str]:
    """物品图片占用的中文与文件标题，实体别名不能与它们重名。"""
    from image_version import image_paths
    from item_names import canonical_file
    script_dir = Path(__file__).resolve().parent
    try:
        _, _, icons, resolved = image_paths(str(script_dir))
    except ValueError:
        return set()
    titles = set()
    if resolved and Path(resolved).is_file():
        import csv
        with open(resolved, encoding='utf-8-sig', newline='') as file:
            for row in csv.reader(file):
                if not row or not row[0].startswith('File:'):
                    continue
                # 生物展示物品不再占用中文名（item_names.is_mob_display）；旧的别名表里可能还有。
                if len(row) > 1 and _MOB_DISPLAY_ICON.match(row[1].removeprefix('File:')):
                    continue
                titles.add('File:' + canonical_file(row[0].removeprefix('File:')))
    if Path(icons).is_dir():
        titles.update('File:' + canonical_file(path.name) for path in Path(icons).glob('*.png'))
    return titles


def entity_plan(source: str) -> dict:
    """实体图片计划：上传 entity-images.json 中的全部文件；中文名唯一且不与物品图片重名时重定向到头像。"""
    from item_names import canonical_file
    source = Path(source)
    manifest_path = source / 'entity-images.json'
    if not manifest_path.is_file():
        raise ValueError(f'缺少实体图片清单: {manifest_path}（请用 GTNH-Dumper 的 entityimages 任务导出）')
    manifest = json.loads(manifest_path.read_text('utf-8'))
    entities = {row['id']: row for row in
                json.loads((source / 'entities.json').read_text('utf-8')).get('entities', [])}
    directory = source / 'entity-images'
    uploads, heads = set(), {}
    for entity_id, row in manifest['images'].items():
        for field in ('full', 'head'):
            name = row.get(field)
            if name:
                if not (directory / name).is_file():
                    raise ValueError(f'实体图片清单中的文件缺失: {name}')
                uploads.add(name)
        if row.get('head'):
            heads[entity_id] = row['head']

    by_title = {}
    for entity_id, head in heads.items():
        name = (entities.get(entity_id) or {}).get('name')
        if not name:
            continue
        by_title.setdefault('File:' + canonical_file(name + '.png'), []).append((entity_id, head))
    item_titles = _item_titles(source)
    redirects, conflicts = {}, {}
    for title, owners in sorted(by_title.items()):
        if len(owners) > 1:
            # 同名时原版实体优先（Special Mobs 的基础“特殊”版、Et Futurum 的末影龙等与原版同名）。
            # 按注册名判断：原版注册名没有“模组.”前缀，实现类可能被模组替换（HEE 的末影人）。
            vanilla = [owner for owner in owners if '.' not in owner[0].split('#')[0]]
            if len(vanilla) == 1:
                owners = vanilla
        if len(owners) > 1:
            conflicts[title] = '多个实体同名: ' + ', '.join(entity for entity, _ in owners)
        elif title in item_titles:
            conflicts[title] = '与物品图片重名: ' + owners[0][0]
        else:
            redirects[title] = owners[0][1]
    plan = {'schemaVersion': 1, 'uploadIcons': sorted(uploads), 'redirects': redirects,
            'redirectConflicts': conflicts}
    print(f"Entity image plan: {len(uploads):,} files; {len(redirects):,} name redirects; "
          f"{len(conflicts):,} names left out")
    for title, reason in list(conflicts.items())[:20]:
        print(f'  left out {title}: {reason}')
    return plan


def standalone_files(titles) -> set[str]:
    """线上已存在、且不是重定向的同名文件页：实体别名不覆盖别人上传的独立图片。"""
    from image_check import _api
    from item_names import canonical_file
    titles = sorted(titles)
    found = set()
    for offset in range(0, len(titles), 50):
        batch = titles[offset:offset + 50]
        data = _api(dict(action='query', titles='|'.join(batch), prop='imageinfo|info', iiprop='sha1'))
        for page in data['query']['pages']:
            if page.get('missing') or page.get('redirect') or not page.get('imageinfo'):
                continue
            # 返回的标题带本地化命名空间（文件:），按规范文件名比较。
            found.add('File:' + canonical_file(page['title'].split(':', 1)[1]))
    return found
