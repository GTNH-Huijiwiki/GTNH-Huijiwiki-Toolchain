"""GuideNH 任务：把内容包仓库里的中文指南转成维基页面。

流程（``python split_all.py guidenh``，默认全流程也会执行）：

1. 同步内容包仓库（.env 的 GUIDENH_REPO，或直接使用已有的克隆 GUIDENH_DIR）。
2. 读取 ``assets/<模组>/guidenh/_zh_cn/**/*.md``，转换成 ``wikitext/指南/GuideNH/<模组>/<页面>.wikitext``（页面 ``指南:GuideNH/<模组>/<页面>``）。
   物品 ID 对照本版本已拆分的 Items（没有时用 DUMPS_DIR 的 advitempanel.json）解析成物品 key。
3. 3D 场景（GameScene）使用仓库里 website-export.zip（官方静态站）预渲染的占位图，页面里的图片取自内容包。
   图片先放到 ``source_state/GuideNH/images/`` 并写出上传计划，由 ``image_*.py --kind guidenh`` 上传。
4. 写出转换报告 ``source_state/GuideNH/guidenh-report.json``（未解析的物品、坏链接、不支持的标签等）。

页面上传由 wiki_inventory.py / data_upload.py 完成，图片上传见 README 的“GuideNH”一节。
"""

import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import zipfile
from collections import defaultdict
from pathlib import Path

import guidenh_convert as convert
import guidenh_scope as scope
from data_version import remote_mod_name, version_tag
from nbt_identity import nbt_identity

REPO_ZIP = 'website-export.zip'
LICENSE_URL = 'https://creativecommons.org/licenses/by-nc-sa/4.0/'
UPSTREAM_URL = 'https://github.com/GTNewHorizons/GTNH-Guide-Pack'


# ── 内容包仓库 ───────────────────────────────────────────────

def _git(directory, *args, check=True) -> str:
    result = subprocess.run(['git', '-C', str(directory), *args], capture_output=True, text=True,
                            encoding='utf-8', errors='replace')
    if check and result.returncode:
        raise RuntimeError(f'git {" ".join(args)} 失败: {result.stderr.strip()}')
    return result.stdout.strip()


_SYNCED = {}


def sync_repository() -> Path:
    """克隆或更新内容包仓库，返回本地目录；同一次运行只同步一次（物品拆分和 guidenh 任务都会用到）。
    GUIDENH_DIR 指定的已有克隆只读取，不更新。"""
    directory = scope.repository_directory()
    if str(directory) not in _SYNCED:
        _SYNCED[str(directory)] = _sync_repository(directory)
    return _SYNCED[str(directory)]


def _sync_repository(directory: Path) -> Path:
    custom = bool(os.environ.get('GUIDENH_DIR', '').strip())
    url = os.environ.get('GUIDENH_REPO', '').strip()
    if custom:
        if not (directory / 'assets').is_dir():
            raise ValueError(f'GUIDENH_DIR 不是内容包仓库（缺少 assets/）: {directory}')
        return directory
    if not url:
        raise ValueError('请在 .env 设置 GUIDENH_REPO（内容包仓库链接）或 GUIDENH_DIR（已有的本地克隆）')
    ref = os.environ.get('GUIDENH_REF', '').strip()
    if not (directory / '.git').is_dir():
        directory.parent.mkdir(parents=True, exist_ok=True)
        print(f'  Cloning {url} -> {directory}')
        command = ['git', 'clone', url, str(directory)]
        if subprocess.run(command).returncode:
            raise RuntimeError('克隆内容包仓库失败')
    else:
        print(f'  Updating {directory}')
        _git(directory, 'fetch', '--tags', 'origin')
        if ref:
            _git(directory, 'checkout', ref)
        if not ref or _git(directory, 'rev-parse', '--abbrev-ref', 'HEAD') != 'HEAD':
            _git(directory, 'pull', '--ff-only')
        _git(directory, 'lfs', 'pull', check=False)
    if ref and (directory / '.git').is_dir():
        _git(directory, 'checkout', ref)
    return directory


def repository_info(directory: Path) -> dict:
    if not (directory / '.git').exists():
        return {'commit': 'unknown', 'times': {}}
    commit = _git(directory, 'rev-parse', '--short=7', 'HEAD', check=False) or 'unknown'
    times, current = {}, None
    for line in _git(directory, 'log', '--format=%ct', '--name-only', check=False).splitlines():
        if re.fullmatch(r'\d{9,}', line):
            current = int(line)
        elif line.strip() and current is not None and line not in times:
            times[line] = current
    return {'commit': commit, 'times': times}


# ── 物品 ─────────────────────────────────────────────────────

class ItemIndex:
    """物品 ID（modid:name[:meta][:{snbt}]）→ 维基物品 key。"""

    def __init__(self, entries, icons=None):
        self.icons = icons or {}  # 物品 key → (图标文件名, 帧数)
        self.by_raw = defaultdict(list)
        for raw, key, nbt in entries:
            self.by_raw[raw].append((key, nbt))
        self.folded = {raw.casefold(): raw for raw in self.by_raw}

    def __len__(self):
        return len(self.by_raw)

    def icon_name(self, ident):
        """物品 ID → 维基图标文件名（不带 .png）；找不到、动画图标（帧数大于 1）或没有图标时返回 None。"""
        key = self.resolve(ident) if ident else None
        icon, frames = self.icons.get(key, (None, 1))
        if not icon or frames > 1:
            return None
        return icon[:-4] if icon.lower().endswith('.png') else icon

    def __call__(self, ident, meta=None, nbt=None):
        return self.resolve(ident, meta, nbt)

    def resolve(self, ident, meta=None, nbt=None):
        snbt = None
        if ':{' in ident:
            ident, snbt = ident.split(':{', 1)
            snbt = '{' + snbt
        parts = ident.split(':')
        base, found_meta = ident, None
        if len(parts) >= 3 and re.fullmatch(r'\d+|\*', parts[-1]):
            base, found_meta = ':'.join(parts[:-1]), parts[-1]
        value = str(meta) if meta not in (None, '') else found_meta
        wildcard = value in ('*', '32767')
        damage = '32767' if wildcard else (value or '0')
        raw = base if damage == '0' else f'{base}:{damage}'
        entries = self.by_raw.get(raw) or self.by_raw.get(self.folded.get(raw.casefold(), ''))
        if not entries and wildcard:
            entries = self.by_raw.get(base)
        if not entries:
            return None
        nbt = nbt or snbt
        if nbt:
            wanted = nbt_identity(nbt)
            for key, stored in entries:
                if stored and nbt_identity(stored) == wanted:
                    return key
        plain = [key for key, stored in entries if not stored]
        if plain:
            return plain[0]
        empty = [key for key, stored in entries if stored == '{}']
        return (empty or [entries[0][0]])[0]


def load_item_index(dumps_dir, wikitext_root) -> ItemIndex:
    """优先读本版本已拆分的 Items（与线上一致的最终 key）；没有时读导出的 advitempanel.json。"""
    entries, icons = [], {}
    version = Path(wikitext_root) / 'Data' / 'Versions' / Path(*version_tag().split('/')) / 'Items'
    pages = sorted(version.glob('*/*.json')) if version.is_dir() else []
    if pages:
        print(f'  Items: {len(pages)} split pages under {version}')
        for page in pages:
            for item in json.loads(page.read_text('utf-8')).get('items', []):
                entries.append((item.get('rawKey') or item['key'], item['key'], item.get('nbt')))
                if item.get('iconName'):
                    icons[item['key']] = (item['iconName'], int(item.get('frameCount') or 1))
    else:
        path = Path(dumps_dir) / 'advitempanel.json'
        if not path.is_file():
            raise ValueError('找不到物品数据：请先运行 split_all.py items，或检查 DUMPS_DIR')
        print(f'  Items: {path} (no split pages yet)')
        data = json.loads(path.read_text('utf-8'))
        for item in next(iter(data.values())):
            entries.append((item['key'], item.get('shortKey') or item['key'], item.get('nbt')))
    return ItemIndex(entries, icons)


# ── 物品与指南页面的关联 ─────────────────────────────────────

class ItemPageIndex:
    """页面表头 item_ids（以及 item_id）声明的“这一页讲的是这些物品”，物品 → 页面后缀（如 gregtech/multiblocks/hv/cleanroom）。

    ID 写法同 ItemIndex：meta 为 0 时 key 不带 :0，带 NBT 的写法忽略 NBT（关联到该注册物品的全部变体），
    meta 写 * 或 32767 表示该物品的全部 meta。页面后缀前面的前缀（指南:GuideNH/）由维基端的 Lua 拼接。
    """

    def __init__(self, pairs):
        self.exact, self.wildcard = defaultdict(list), defaultdict(list)
        for ident, suffix in pairs:
            ident = ident.split(':{', 1)[0].strip()
            parts = ident.split(':')
            if len(parts) >= 3 and parts[-1] in ('*', '32767'):
                self.wildcard[':'.join(parts[:-1]).casefold()].append(suffix)
            else:
                if len(parts) >= 3 and parts[-1] == '0':
                    ident = ':'.join(parts[:-1])
                self.exact[ident.casefold()].append(suffix)

    def __bool__(self):
        return bool(self.exact or self.wildcard)

    def pages_for(self, raw_key: str) -> list:
        raw = raw_key.casefold()
        base = raw.rsplit(':', 1)[0] if re.search(r':\d+$', raw) else raw
        found = list(self.exact.get(raw, ())) + list(self.wildcard.get(base, ()))
        return list(dict.fromkeys(found))


def item_page_index(repo=None) -> 'ItemPageIndex':
    """读取内容包里全部中文页面的 item_ids；没有配置内容包仓库或读取失败时返回空索引。"""
    if not scope.enabled():
        return ItemPageIndex([])
    try:
        repo = repo or sync_repository()
        pages = read_pages(repo)
    except Exception as error:
        print(f'  [GuideNH] 跳过指南页面关联：{error}')
        return ItemPageIndex([])
    pairs = []
    for page in sorted(pages.values(), key=lambda p: (-p.position, p.key)):
        ids = page.meta.get('item_ids') or []
        if page.meta.get('item_id'):
            ids = [page.meta['item_id']] + list(ids)
        pairs.extend((str(ident), page.key) for ident in ids)
    return ItemPageIndex(pairs)


def attach_guide_pages(items, index) -> int:
    """给物品记录加 guidenhPages（页面后缀列表，无关联则不加），返回带关联的物品数。"""
    count = 0
    for item in items:
        pages = index.pages_for(item.get('rawKey') or item.get('key') or '') if index else []
        if pages:
            item['guidenhPages'] = pages
            count += 1
        else:
            item.pop('guidenhPages', None)
    return count


# ── 页面 ─────────────────────────────────────────────────────

def clean_icon_id(value):
    """navigation.icon 写作 modid:name、modid:name:meta、<modid:name:meta> 或 'modid:name meta'，统一成 ItemIndex 认的写法。"""
    if not value:
        return None
    text = str(value).strip()
    if text.startswith('<') and text.endswith('>'):
        text = text[1:-1].strip()
    parts = text.split()
    if len(parts) == 2 and parts[1].isdigit():
        text = f'{parts[0]}:{parts[1]}'
    return text or None


def page_order(page):
    """与游戏里导航树一致：position 大的在前，同值按侧栏标题（navigation.title）排。"""
    return (-page.position, page.sidebar_title)


class Page:
    def __init__(self, mod, page_id, path, meta, body):
        self.mod, self.id, self.path, self.meta, self.body = mod, page_id, path, meta, body
        self.key = f'{mod}/{page_id}'
        navigation = meta.get('navigation') or {}
        # 侧栏标题（navigation.title，游戏里侧边栏用，可能很长）只作回退；页面名以正文开头的一级标题为准，
        # 导航框、上级路径、子页面列表和 DISPLAYTITLE 都用它
        self.sidebar_title = str(navigation.get('title') or page_id.rsplit('/', 1)[-1])
        self.title_text = convert.split_title(body)[0] or self.sidebar_title
        self.parent_href = navigation.get('parent')
        self.position = int(navigation.get('position') or 0)
        self.icon_id = clean_icon_id(navigation.get('icon'))
        self.wiki_title = f'{scope.title_prefix()}{self.key}'
        self.headings = []
        fence = None
        for line in body.splitlines():
            marker = re.match(r'^\s*(`{3,}|~{3,})', line)
            if marker:
                fence = None if fence and line.strip().startswith(fence) else (fence or marker.group(1))
                continue
            if not fence:
                heading = re.match(r'^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$', line)
                if heading:
                    self.headings.append(convert.plain_text(heading.group(1)))
        self.anchors = set(re.findall(r'<a\s+name="([^"]+)"', body))
        self.categories = []
        for entry in meta.get('categories') or []:
            self.categories.append(str(entry).split('|')[0].strip())


_FRONTMATTER = re.compile(r'\A---\r?\n(.*?)\r?\n---\r?\n?', re.S)


def read_pages(repo: Path) -> dict:
    import yaml
    pages = {}
    for path in sorted(repo.glob(f'assets/*/guidenh/{scope.LANGUAGE_DIRECTORY}/**/*.md')):
        mod = path.relative_to(repo / 'assets').parts[0]
        relative = path.relative_to(repo / 'assets' / mod / 'guidenh' / scope.LANGUAGE_DIRECTORY).as_posix()
        page_id = relative[:-3]
        if page_id.split('/')[0] == 'templates' or '/templates/' in '/' + page_id:
            continue
        text = path.read_text('utf-8').replace('\r\n', '\n')
        meta, body = {}, text
        match = _FRONTMATTER.match(text)
        if match:
            meta = yaml.safe_load(match.group(1)) or {}
            body = text[match.end():]
        pages[f'{mod}/{page_id}'] = Page(mod, page_id, path, meta if isinstance(meta, dict) else {}, body)
    return pages


class Site:
    """页面注册表：链接、锚点、导航与图片都在这里解析。"""

    def __init__(self, repo, pages, staging, zip_file):
        self.repo, self.pages, self.staging, self.zip = repo, pages, staging, zip_file
        self.folded = {key.casefold(): key for key in pages}
        self.images = {}      # 维基文件名 → 本地路径（本次用到的）
        self.pack_sizes = defaultdict(int)  # 每个内容包（模组目录）的页面数
        for page in pages.values():
            self.pack_sizes[page.mod] += 1
        self.children = defaultdict(list)
        for page in pages.values():
            parent = self.parent_key(page)
            if parent:
                self.children[parent].append(page)
        self.categories = defaultdict(list)
        for page in pages.values():
            for name in page.categories:
                self.categories[name.casefold()].append(page)

    def link_key(self, mod, page_id, href):
        """href（相对、根路径或 modid:path）→ 页面 key；找不到返回 None。"""
        href = href.split('#', 1)[0].strip()
        if not href:
            return f'{mod}/{page_id}'
        match = re.match(r'^([A-Za-z0-9_\-.]+):(?!//)(.*)$', href)
        if match:
            mod, target = match.group(1), match.group(2).lstrip('/')
        elif href.startswith('/'):
            target = href.lstrip('/')
        else:
            target = posixpath.normpath(posixpath.join(posixpath.dirname(page_id + '.md'), href))
        if target.endswith('.md'):
            target = target[:-3]
        key = f'{mod}/{target}'
        return self.folded.get(key.casefold())

    def parent_key(self, page):
        if not page.parent_href:
            return None
        return self.link_key(page.mod, page.id, str(page.parent_href))

    def anchor_text(self, key, anchor):
        page = self.pages[key]
        normal = lambda text: re.sub(r'[\W_]+', '-', text.lower()).strip('-')
        for heading in page.headings:
            if normal(heading) == normal(anchor):
                return heading
        if anchor in page.anchors:
            return anchor
        return None

    def context(self, page, items, scenes, report):
        def resolve_page(href):
            href = href.strip()
            if re.match(r'^(https?:)?//|^mailto:', href):
                return (href, '', True)
            if href.startswith(('sound:', 'sound-src:')):
                return None
            anchor = href.split('#', 1)[1] if '#' in href else ''
            key = self.link_key(page.mod, page.id, href)
            if not key:
                return None
            text = self.anchor_text(key, anchor) if anchor else ''
            if key == page.key:
                return ('', text or '', False)
            return (self.pages[key].wiki_title, text or '', False)

        def resolve_image(src):
            return self.image(page, src)

        def subpages():
            kids = sorted(self.children.get(page.key, []), key=page_order)
            return [(kid.wiki_title, kid.title_text) for kid in kids]

        def category_pages(name):
            found = sorted(self.categories.get(name.casefold(), []), key=page_order)
            return [(entry.wiki_title, entry.title_text) for entry in found if entry.key != page.key]

        return convert.PageContext(page.key, items, resolve_page, resolve_image, scenes, subpages,
                                   category_pages, report, guide_mod=page.mod, guide_page=page.id)

    def image(self, page, src):
        src = str(src).strip()
        match = re.match(r'^([A-Za-z0-9_\-.]+):(?!//)(.*)$', src)
        mod = page.mod
        if match:
            mod, target = match.group(1), match.group(2).lstrip('/')
        elif src.startswith('/'):
            target = src.lstrip('/')
        else:
            target = posixpath.normpath(posixpath.join(posixpath.dirname(page.id + '.md'), src))
        root = self.repo / 'assets' / mod / 'guidenh'
        for candidate in (root / scope.LANGUAGE_DIRECTORY / target, root / target):
            if candidate.is_file():
                break
        else:
            return None
        name = 'GuideNH_' + mod + '_' + re.sub(r'^assets/', '', target).replace('/', '_').replace(' ', '_')
        self.images[name] = candidate
        width, height = image_size(candidate)
        return name, width, height


def image_size(path):
    try:
        from PIL import Image
        with Image.open(path) as image:
            return image.size
    except Exception:
        return 0, 0


# ── 3D 场景 ─────────────────────────────────────────────────

_SCENE_IMG = re.compile(r'<img\b[^>]*class="game-scene guide-scene"[^>]*>')


def read_scenes(zip_file, page, staging, used) -> list:
    """官方静态站里该页的场景占位图，按在页面里出现的顺序。"""
    if zip_file is None:
        return []
    member = f'website/guides/{page.mod}/guidenh/{scope.LANGUAGE}/{page.id}.html'
    try:
        html = zip_file.read(member).decode('utf-8')
    except KeyError:
        return []
    scenes = []
    for tag in _SCENE_IMG.findall(html):
        source = re.search(r'src="[^"]*?/placeholders/([0-9a-f]+)\.png"', tag)
        if not source:
            continue
        digest = source.group(1)
        name = f'GuideNH_scene_{digest[:16]}.png'
        width = re.search(r'data-scene-width="(\d+)"', tag)
        height = re.search(r'data-scene-height="(\d+)"', tag)
        target = staging / name
        if name not in used:
            data = zip_file.read(f'website/_res/placeholders/{digest}.png')
            if not target.is_file() or target.stat().st_size != len(data):
                target.write_bytes(data)
            used[name] = target
        scenes.append(convert.Scene(name, int(width.group(1)) if width else 320,
                                    int(height.group(1)) if height else 280))
    return scenes


# ── 页面输出 ─────────────────────────────────────────────────

def breadcrumb(site, page) -> str:
    chain, seen, key = [], set(), site.parent_key(page)
    while key and key not in seen:
        seen.add(key)
        parent = site.pages[key]
        chain.append(f'[[{parent.wiki_title}|{parent.title_text}]]')
        key = site.parent_key(parent)
    # 最前面总是总览页，之后才是按导航树逐级的上级页面
    chain.append(f'[[{scope.landing_title()}|总览]]')
    return ' › '.join(reversed(chain))


def _template_value(value) -> str:
    """作为模板参数值写出：竖线会被当成参数分隔，换成 {{!}}。"""
    return str(value).replace('|', '{{!}}').strip()


def header(site, page, commit) -> str:
    """页首交给 {{GuideNH页首}}：来源、许可、官方网页版的文字都在模板里，页面只传参数。"""
    author = page.meta.get('author')
    authors = page.meta.get('authors')
    names = [str(a.get('name') if isinstance(a, dict) else a) for a in authors] if isinstance(authors, list) else (
        [str(author)] if author else [])
    arguments = []
    trail = breadcrumb(site, page)
    if trail:
        arguments.append('上级=' + trail)  # 链接里的竖线在 [[ ]] 内，不会被当成参数分隔
    arguments += [f'模组={page.mod}', f'页面={page.id}', f'版本={commit}']
    if names:
        arguments.append('作者=' + _template_value('、'.join(names)))
    if page.meta.get('date'):
        arguments.append('日期=' + _template_value(page.meta['date']))
    return '{{GuideNH页首|' + '|'.join(arguments) + '}}'


def landing_page(site, commit) -> str:
    roots = sorted((p for p in site.pages.values() if not site.parent_key(p)), key=lambda p: (page_order(p), p.key))
    lines = [display_title_line('GuideNH - 总览').rstrip('\n'),
             '本页是 [[GuideNH]] 游戏内指南的内容总览，列出同步自 GuideNH 内容包 '
             f'[{UPSTREAM_URL} GTNH-Guide-Pack]（版本 {commit}）的中文页面；页面由机器人生成，请勿直接编辑。',
             f'内容采用 [{LICENSE_URL} CC BY-NC-SA 4.0] 许可；发现问题请到内容包仓库提交修改。', '']
    lines += [f'* [[{p.wiki_title}|{p.title_text}]]（{p.mod}）' for p in roots]
    lines += ['', '== 导航 ==', '{{GuideNH异步导航}}', '', '[[分类:GuideNH]]']
    return '\n'.join(lines) + '\n'


def related_item_ids(page) -> list:
    ids = list(page.meta.get('item_ids') or [])
    if page.meta.get('item_id'):
        ids.insert(0, page.meta['item_id'])
    return [str(ident) for ident in ids]


def related_items_block(context, page) -> str:
    """页面表头 item_ids 声明的关联物品，紧跟在页首声明框后面，不单独成章节：交给 {{GuideNH关联物品}}，
    模板用 <blockquote> 和 {{Columns}} 排成多列无序列表；页面只传物品行，找不到的物品记入报告并跳过。"""
    entries, seen = [], set()
    for ident in related_item_ids(page):
        key = context.resolve_item(ident)
        if not key:
            context.note('unresolvedItems', ident)
            continue
        if key not in seen:
            seen.add(key)
            entries.append('* ' + convert._item_wikitext(context, ident))
    if not entries:
        return ''
    return '{{GuideNH关联物品|1=\n' + '\n'.join(entries) + '\n}}'


def convert_page(site, page, items, zip_file, staging, used_scene_images, commit, report):
    scenes = read_scenes(zip_file, page, staging, used_scene_images)
    page_report = {}
    context = site.context(page, items, scenes, page_report)
    # 页面标题是路径，显示标题取正文开头的一级标题（没有则用导航标题），并去掉正文里重复的标题
    heading, source_body = convert.split_title(page.body)
    display_title = f'{scope.title_prefix()}{heading or page.title_text}'  # 指南:GuideNH/正文标题
    body = convert.convert_document(source_body.strip('\n'), context)
    related = related_items_block(context, page)
    if related:
        body = related + '\n\n' + body  # 紧跟在页首声明框后面
    scene_tags = len(re.findall(r'<(?:GameScene|Scene)\b', page.body))
    if scene_tags != len(scenes) and scene_tags:
        page_report.setdefault('sceneCountMismatch', []).append(f'{scene_tags} 个场景，静态图 {len(scenes)} 张')
    for kind, values in page_report.items():
        report.setdefault(kind, {})[page.key] = values
    # 物品图标与配方整页只查一次库；包在模板参数里的正文不能出现裸竖线（转换器已改用 HTML 表格）
    wrapped = '{{DatabaseWrapper|html=\n' + body + '\n}}' if context.wrapped else body
    # 有公式的页面加载 MathJax（维基其他页面的写法），公式在正文里写成 $…$ 与 $$…$$
    mathjax = '{{#html:MathJax3}}\n' if context.has_formula else ''
    # 分类在 {{GuideNH页首}} 里；导航框放在自己的“导航”章节（条目页同样放在页尾）
    footer = chr(10) * 2 + '== 导航 ==' + chr(10) + '{{GuideNH异步导航}}'
    return display_title_line(display_title) + mathjax + header(site, page, commit) + '\n\n' + wrapped + footer + '\n'


def display_title_line(title: str) -> str:
    """{{DISPLAYTITLE:…}}；标题里不能有会被解析的字符。"""
    clean = re.sub(r'[{}|<>\[\]]', '', title).strip()
    return '{{DISPLAYTITLE:' + clean + '}}\n' if clean else ''


def _lua_string(value) -> str:
    text = str(value).replace(chr(92), chr(92) * 2).replace('"', chr(92) + '"').replace(chr(10), ' ')
    return '"' + text + '"'


def navbox_modules(site, items=None) -> dict:
    """整个 GuideNH 共用一个导航框（模块:Navbox/<前缀>），每个内容包是一个顶层分组，其下按导航树分组。

    格式沿用维基其他导航框的数据表：title、defaultIcon、list；带 list 的表是分组（groupName），与单项平级。
    页面的 navigation.icon 能解析到有静态图标的物品时，写成单项的 icon（图标文件名）；有子页面的页面成为分组，分组第一项是页面自己（带它的图标）。
    """
    def ordered(pages):
        return sorted(pages, key=page_order)

    def icon_field(page):
        name = items.icon_name(page.icon_id) if items is not None and page.icon_id else None
        return _lua_string(name) if name else 'false'

    def entry(page, indent, extra=()):
        pad = '    ' * indent
        children = ordered(site.children.get(page.key, [])) + list(extra)
        item = (f'{{name = {_lua_string(page.title_text)}, link = {_lua_string(page.wiki_title)}, '
                f'icon = {icon_field(page)}}}')
        if not children:
            return pad + item + ','
        own = f'{{name = {_lua_string("总览")}, link = {_lua_string(page.wiki_title)}, icon = {icon_field(page)}}}'
        lines = [pad + '{', pad + f'    groupName = {_lua_string(page.title_text)},', pad + '    list = {',
                 pad + '        ' + own + ',']
        lines += [entry(child, indent + 2) for child in children]
        lines += [pad + '    },', pad + '},']
        return chr(10).join(lines)

    packs = []
    for mod in sorted({page.mod for page in site.pages.values()}):
        roots = ordered(page for page in site.pages.values() if page.mod == mod and not site.parent_key(page))
        main = next((page for page in roots if page.id == 'index'), roots[0])
        packs.append((main, [page for page in roots if page is not main]))
    packs.sort(key=lambda pack: (-pack[0].position, pack[0].mod))
    overview = scope.landing_title()
    lines = ['-- 导航框数据：由工具链（databaseDump 的 guidenh 任务）按内容包的导航树生成，不要手工编辑；',
             '-- 由 {{GuideNH导航}} 绘制，写法见 [[Project:沙盒/navbox]]。',
             'return {',
             f'    title = {_lua_string("[[" + overview + "|GuideNH]]")},',
             '    defaultIcon = false,', '    list = {']
    lines += [entry(main, 2, others) for main, others in packs]
    lines += ['    },', '}', '']
    return {scope.module_title(): chr(10).join(lines)}


def page_path(pages_directory: Path, title: str, prefix=None, suffix=None) -> Path:
    prefix = scope.title_prefix() if prefix is None else prefix
    relative = title[len(prefix):]
    path = pages_directory.joinpath(*relative.split('/'))
    return path.with_name(path.name + (scope.PAGE_SUFFIX if suffix is None else suffix))


def write_pages(pages_directory: Path, rendered: dict, prune: bool = True, protected=(), prefix=None, suffix=None):
    """写出页面并清掉不再生成的旧文件（线上对应的页面随后由清单列为待删除）。

    protected：这次转换失败的页面标题，保留它们已有的本地文件——转换出错不能变成线上页面被删。
    """
    suffix = scope.PAGE_SUFFIX if suffix is None else suffix
    keep = {page_path(pages_directory, title, prefix, suffix).resolve() for title in protected}
    pages_directory.mkdir(parents=True, exist_ok=True)
    for title, text in rendered.items():
        path = page_path(pages_directory, title, prefix, suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or path.read_text('utf-8') != text:
            path.write_text(text, 'utf-8', newline='\n')
        keep.add(path.resolve())
    removed = 0
    for path in (pages_directory.rglob('*' + suffix) if prune else ()):
        if path.resolve() not in keep:
            path.unlink()
            removed += 1
    for directory in sorted((p for p in pages_directory.rglob('*') if p.is_dir()), reverse=True):
        if not any(directory.iterdir()):
            directory.rmdir()
    return removed


def write_navbox_module(wikitext_root, text: str, prune: bool = True) -> int:
    """写出导航框数据模块（一个文件），并清掉旧版按内容包拆分的模块文件（线上对应的模块随后由清单列为待删除）。"""
    path = scope.local_module_path(wikitext_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file() or path.read_text('utf-8') != text:
        path.write_text(text, 'utf-8', newline='\n')
    legacy = scope.local_module_directory(wikitext_root)
    if not prune or not legacy.is_dir():
        return 0
    removed = sum(1 for item in legacy.rglob('*') if item.is_file())
    shutil.rmtree(legacy)
    return removed


def stage_images(site, used_scene_images, staging: Path) -> dict:
    """页面用到的图片放进 staging（保持修改时间不变，sha1 缓存才能命中），返回 {文件名: 路径}。"""
    staging.mkdir(parents=True, exist_ok=True)
    plan = dict(used_scene_images)
    for name, source in site.images.items():
        target = staging / name
        if not target.is_file() or target.stat().st_size != source.stat().st_size \
                or target.read_bytes() != source.read_bytes():
            shutil.copy2(source, target)
        plan[name] = target
    return plan


def run(dumps_dir, wikitext_root, debug=False):
    if not scope.enabled():
        print('  跳过：GuideNH 只在 MOD_LOCAL_NAME=GTNH 时处理')
        return
    try:
        repo = sync_repository()
    except ValueError as error:
        print(f'  跳过：{error}')
        return
    info = repository_info(repo)
    commit = info['commit']
    print(f'  Content pack: {repo} @ {commit}')
    pages = read_pages(repo)
    print(f'  Pages ({scope.LANGUAGE}): {len(pages)}')
    if not pages:
        # 仓库不对或目录结构变了：什么都不动，否则线上的指南页面会被当成“已删除”清掉
        raise ValueError(f'内容包里没有找到中文指南页面（assets/*/guidenh/{scope.LANGUAGE_DIRECTORY}/**/*.md）: {repo}')
    items = load_item_index(dumps_dir, wikitext_root)
    print(f'  Item identities: {len(items):,}')
    staging = scope.images_directory()
    zip_path = repo / REPO_ZIP
    zip_file = zipfile.ZipFile(zip_path) if zip_path.is_file() and zipfile.is_zipfile(zip_path) else None
    if zip_file is None:
        print(f'  警告：缺少 {REPO_ZIP}（git lfs pull 了吗？），页面里的 3D 场景将没有预览图')
    site = Site(repo, pages, staging, zip_file)
    staging.mkdir(parents=True, exist_ok=True)

    report, used_scenes, rendered = {}, {}, {}
    limit = 12 if debug else None
    for number, page in enumerate(pages.values()):
        if limit and number >= limit:
            break
        try:
            rendered[page.wiki_title] = convert_page(site, page, items, zip_file, staging, used_scenes, commit, report)
        except Exception as error:  # 一页出错不拖累其他页面
            report.setdefault('failedPages', {})[page.key] = repr(error)
            print(f'  FAILED {page.key}: {error!r}', file=sys.stderr)
    landing = landing_page(site, commit)
    if zip_file:
        zip_file.close()
    failed = [pages[key].wiki_title for key in report.get('failedPages', {})]
    removed = write_pages(scope.local_pages_directory(wikitext_root), rendered, prune=not limit, protected=failed)
    landing_path = scope.local_landing_path(wikitext_root)
    if not landing_path.is_file() or landing_path.read_text('utf-8') != landing:
        landing_path.write_text(landing, 'utf-8', newline='\n')
    removed += write_navbox_module(wikitext_root, navbox_modules(site, items)[scope.module_title()], prune=not limit)
    plan = stage_images(site, used_scenes, staging)
    scope.image_plan_path().write_text(json.dumps(
        {'schemaVersion': 1, 'uploadIcons': sorted(plan), 'redirects': {}}, ensure_ascii=False, indent=1), 'utf-8')

    # 导出之后改过的页面，场景预览可能比 md 旧
    zip_time = info['times'].get(REPO_ZIP)
    stale = []
    if zip_time:
        for page in pages.values():
            changed = info['times'].get(f'assets/{page.mod}/guidenh/{scope.LANGUAGE_DIRECTORY}/{page.id}.md')
            if changed and changed > zip_time and re.search(r'<(?:GameScene|Scene)\b', page.body):
                stale.append(page.key)
    summary = {
        'commit': commit, 'pages': len(rendered), 'removedStalePages': removed,
        'images': len(plan), 'sceneImages': len(used_scenes),
        'stalePreviewPages': stale,
        'counts': {kind: sum(len(v) if isinstance(v, list) else 1 for v in values.values())
                   for kind, values in report.items()},
    }
    scope.report_path().write_text(json.dumps({'summary': summary, 'details': report},
                                              ensure_ascii=False, indent=1), 'utf-8')
    print(f'  Wrote {len(rendered)} pages, removed {removed} stale; images planned: {len(plan)} '
          f'({len(used_scenes)} scene previews)')
    for kind, count in sorted(summary['counts'].items()):
        print(f'    {kind}: {count}')
    if stale:
        print(f'    预览图可能偏旧的页面（md 在静态站导出之后改过）: {len(stale)}')
    print(f'  Report: {scope.report_path()}')
