"""GuideNH 的页面范围、目录与配置。不依赖第三方库；调用方先加载 .env。

页面写入 ``<命名空间>:<前缀>/…``（默认 ``指南:GuideNH/…``），导航框数据写入 ``模块:Navbox/<前缀>/<模组>``。
这两个前缀下的页面全部由 guidenh 任务生成：wiki_inventory.py 会把线上该前缀下本地没有的页面列为待删除，
所以不要手工在这些前缀下建页面。
"""

import os
from pathlib import Path

from data_version import local_mod_name, title_part

LANGUAGE = 'zh_cn'
LANGUAGE_DIRECTORY = '_' + LANGUAGE
DEFAULT_PREFIX = 'GuideNH'
DEFAULT_NAMESPACE = '指南'
PAGE_SUFFIX = '.wikitext'
MODULE_SUFFIX = '.lua'
MODULE_NAMESPACE = 'Module'  # 工具链的本地镜像与标题都用英文名；线上显示为“模块”


def enabled() -> bool:
    """只有本体（MOD_LOCAL_NAME=GTNH）的流程处理指南页面。"""
    return local_mod_name() == 'GTNH'


def page_prefix() -> str:
    value = (os.environ.get('GUIDENH_PAGE_PREFIX') or DEFAULT_PREFIX).strip().strip('/')
    for part in value.split('/'):
        title_part(part)
    return value


def namespace() -> str:
    """维基命名空间名（wiki.py 的本地镜像也用这个名字作目录）。"""
    return title_part((os.environ.get('GUIDENH_NAMESPACE') or DEFAULT_NAMESPACE).strip())


def title_prefix() -> str:
    return f'{namespace()}:{page_prefix()}/'


def landing_title() -> str:
    """总览页直接用前缀本身作标题（指南:GuideNH），不是前缀下的子页面。"""
    return f'{namespace()}:{page_prefix()}'


def module_title() -> str:
    """导航框数据模块：整个 GuideNH 共用一个导航框。"""
    return f'{MODULE_NAMESPACE}:Navbox/{page_prefix()}'


def is_guidenh_title(title: str) -> bool:
    """标题是否属于 GuideNH：页面前缀下的页面，或导航框模块 Module:Navbox/GuideNH（含旧版按内容包拆分的
    Module:Navbox/GuideNH/…，用来清理旧模块）。空格与下划线等价；模块命名空间的中文名“模块”也认。"""
    normalized = title.replace(' ', '_')
    if normalized.startswith(title_prefix().replace(' ', '_')) or normalized == landing_title().replace(' ', '_'):
        return True
    for module in (module_title(), module_title().replace(MODULE_NAMESPACE, '模块', 1)):
        module = module.replace(' ', '_')
        if normalized == module or normalized.startswith(module + '/'):
            return True
    return False


DEFAULT_SITE_URL = 'https://www.gtnewhorizons.com/GTNH-Guide-Pack'


def official_url(mod: str, page_id: str) -> str:
    """官方网页版（GitHub Pages 部署的静态站）里同一页的地址。可用 GUIDENH_SITE_URL 改站点根地址。"""
    root = (os.environ.get('GUIDENH_SITE_URL') or DEFAULT_SITE_URL).strip().rstrip('/')
    return f'{root}/guides/{mod}/guidenh/{LANGUAGE}/{page_id}.html'


def top_directory() -> str:
    """作为 wiki_inventory 的“第一级目录”名，用于 --only / --delete-dir。"""
    return page_prefix().split('/')[0]


def state_directory() -> Path:
    return Path(__file__).resolve().parent / 'source_state' / 'GuideNH'


def repository_directory() -> Path:
    custom = os.environ.get('GUIDENH_DIR', '').strip()
    return Path(custom).expanduser().resolve() if custom else state_directory() / 'repo'


def images_directory() -> Path:
    return state_directory() / 'images'


def image_database() -> Path:
    return state_directory() / 'image_inventory.db'


def image_plan_path() -> Path:
    return state_directory() / 'image-plan.json'


def report_path() -> Path:
    return state_directory() / 'guidenh-report.json'


def local_pages_directory(wikitext_dir) -> Path:
    return Path(wikitext_dir) / namespace() / Path(*page_prefix().split('/'))


def local_landing_path(wikitext_dir) -> Path:
    return Path(wikitext_dir) / namespace() / (Path(*page_prefix().split('/')).as_posix() + PAGE_SUFFIX)


def local_module_path(wikitext_dir) -> Path:
    return Path(wikitext_dir) / MODULE_NAMESPACE / 'Navbox' / (Path(*page_prefix().split('/')).as_posix() + MODULE_SUFFIX)


def local_module_directory(wikitext_dir) -> Path:
    """旧版按内容包拆分的导航框模块目录，只用来清理。"""
    return Path(wikitext_dir) / MODULE_NAMESPACE / 'Navbox' / Path(*page_prefix().split('/'))


def title_to_relpath(title: str) -> str:
    """维基标题 → wikitext/ 下的相对路径（与 wiki.py 的本地镜像一致）。"""
    ns, _, rest = title.partition(':')
    suffix = MODULE_SUFFIX if ns == MODULE_NAMESPACE else PAGE_SUFFIX
    return '/'.join([ns] + rest.split('/')) + suffix
