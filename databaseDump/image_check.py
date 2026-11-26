"""图片是否需要重传的判定：视觉规则、黑名单和线上状态记录。

用法（在 .env 配好数据源后）:
    python image_check.py stability <dumpsA> <dumpsB> [...] [--replace]
        # 比较同一环境的多次导出，像素有差异的写入黑名单；--replace 用本次结果重建名单

判定规则（image_inventory.py 与 image_upload.py 共用）:
1. 本地文件 sha1 与线上文件 sha1 相同：不传。
2. 黑名单（config/image_unstable.json，多次导出仍有波动）：线上已有且尺寸相同的不更新，线上没有的默认
   也不传；image_upload.py 加 --force-unstable 才上传。线上文件尺寸与本地不同时黑名单不起作用，照常上传：
   数据页写的是本地帧数与帧尺寸，旧帧条会显示错乱。
3. 视觉规则：两张图缩到 32×32（BOX 平均、按 alpha 预乘）后逐像素取通道差最大值，
   平均值 ≤ 1 且最大值 ≤ 32 视为相同：不传。动图帧条（*_dynamic.png）逐帧套用，任一帧超限即视为不同；
   整图尺寸不同（帧数或帧尺寸变化）直接视为不同。判定按（线上 sha1，本地 sha1）记在 image_inventory.db
   （随仓库提交，其他维护者同样复用），下次不再下载比对。
4. 其余上传。
"""

import io
import json
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from PIL import Image

from icon_names import directory_renames, local_path

FINGERPRINT_SIZE = 32
MEAN_LIMIT = 1.0
MAX_LIMIT = 32
UNSTABLE_CONFIG = Path(__file__).resolve().parent / 'config' / 'image_unstable.json'
# 惠迹 CDN 拒绝非浏览器 UA。
BROWSER_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36')


# ── 视觉规则 ──────────────────────────────────────────────────

def _fingerprint_pixels(image):
    """RGBA 图缩到 32×32 后的预乘像素元组列表。"""
    raw = image.resize((FINGERPRINT_SIZE, FINGERPRINT_SIZE), Image.BOX).tobytes()
    pixels = []
    for i in range(0, len(raw), 4):
        r, g, b, a = raw[i:i + 4]
        pixels.append((r * a // 255, g * a // 255, b * a // 255, a))
    return pixels


def fingerprint(data: bytes):
    """32×32 预乘 RGBA；返回 (原尺寸, 像素元组列表)。"""
    with Image.open(io.BytesIO(data)) as image:
        return image.size, _fingerprint_pixels(image.convert('RGBA'))


def _distance(pa, pb):
    diffs = [max(abs(x - y) for x, y in zip(u, v)) for u, v in zip(pa, pb)]
    return sum(diffs) / len(diffs), max(diffs)


def visual_distance(a: bytes, b: bytes):
    """(平均差, 最大差)；尺寸不同返回 None。"""
    size_a, pa = fingerprint(a)
    size_b, pb = fingerprint(b)
    if size_a != size_b:
        return None
    return _distance(pa, pb)


def _within_limits(distance) -> bool:
    return distance[0] <= MEAN_LIMIT and distance[1] <= MAX_LIMIT


def visually_same(a: bytes, b: bytes) -> bool:
    distance = visual_distance(a, b)
    return distance is not None and _within_limits(distance)


def strips_same(a: bytes, b: bytes) -> bool:
    """横向帧条逐帧套用视觉规则。整条缩到 32×32 会把上百帧压在一起，某一帧画错也看不出来。"""
    with Image.open(io.BytesIO(a)) as first, Image.open(io.BytesIO(b)) as second:
        if first.size != second.size:
            return False
        width, height = first.size
        if width % height:
            return visually_same(a, b)
        first, second = first.convert('RGBA'), second.convert('RGBA')
    for left in range(0, width, height):
        box = (left, 0, left + height, height)
        one, other = first.crop(box), second.crop(box)
        if one.tobytes() != other.tobytes() \
                and not _within_limits(_distance(_fingerprint_pixels(one), _fingerprint_pixels(other))):
            return False
    return True


def same_picture(name: str, remote: bytes, local: bytes) -> bool:
    return (strips_same if name.endswith('_dynamic.png') else visually_same)(remote, local)


# ── 黑名单 ────────────────────────────────────────────────────

def unstable_icons() -> set[str]:
    if not UNSTABLE_CONFIG.is_file():
        return set()
    return set(json.loads(UNSTABLE_CONFIG.read_text('utf-8'))['icons'])


def exempt_icons() -> dict[str, str]:
    """图标名 → 免更新原因（线上已有同尺寸文件时不更新）。"""
    return {name: 'unstable' for name in unstable_icons()}


def pixel_manifest(source) -> dict[str, str]:
    """Dumper 的像素哈希：物品图标的 icon-hashes.json 与实体图片 entity-images.json 的 pixelHashes 合并。"""
    result = {}
    icons = Path(source) / 'icon-hashes.json'
    if icons.is_file():
        renames = directory_renames(source)
        result.update({renames.get(name, name): digest
                       for name, digest in json.loads(icons.read_text('utf-8'))['images'].items()})
    entities = Path(source) / 'entity-images.json'
    if entities.is_file():
        result.update(json.loads(entities.read_text('utf-8')).get('pixelHashes', {}))
    if not result:
        raise ValueError(f'缺少 Dumper 像素哈希清单: {icons} 或 {entities}')
    return result


def record_stability(sources, replace=False) -> dict:
    """同一环境多次导出中像素有差异的图片写入黑名单（config/image_unstable.json）。

    传入两个或更多 dumps 目录，任意两次之间像素不同即列入。物品图标与实体图片（entity-images.json）都参与比较，
    只比较各次都有的文件。默认并入现有名单；replace=True 时只用本次比较的结果重建名单
    （例如导出器修好一批波动后），本次没有比较到的文件保持原状。
    """
    if len(sources) < 2:
        raise ValueError('至少需要两次导出')
    manifests = [pixel_manifest(source) for source in sources]
    common = set.intersection(*(set(m) for m in manifests))
    changed = {name for name in common if len({m[name] for m in manifests}) > 1}
    known = unstable_icons() - common if replace else unstable_icons()
    merged = sorted(known | changed)
    UNSTABLE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    UNSTABLE_CONFIG.write_text(json.dumps({
        'description': '图片黑名单：同一整合包、同一导出器多次导出像素仍不同的图标（渲染依赖时间或随机数）。'
                       'image_upload.py 默认拒绝上传这些图标，加 --force-unstable 才上传。'
                       '由 image_check.py stability 生成。',
        'icons': merged}, ensure_ascii=False, indent=1) + chr(10), 'utf-8')
    return {'runs': len(sources), 'compared': len(common), 'changed': len(changed),
            'added': len(changed - known), 'total': len(merged)}


# ── 线上查询 ──────────────────────────────────────────────────

def _api(params: dict) -> dict:
    base = os.environ.get('BASE_URL', 'https://gtnh.huijiwiki.com/api.php').replace('http://', 'https://', 1)
    data = urllib.parse.urlencode(dict(params, format='json', formatversion='2')).encode()
    request = urllib.request.Request(base, data=data, headers={
        'X-authkey': os.environ.get('AUTHKEY', ''), 'User-Agent': 'WikiBot/1.0',
        'Content-Type': 'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read())


def wiki_files(names, threads=8) -> dict[str, dict]:
    """文件名 → {'sha1', 'url', 'size'}；线上不存在的不返回。每批 50 个标题，多线程并发查询。"""
    from item_names import canonical_file
    names = sorted(names)
    batches = [names[offset:offset + 50] for offset in range(0, len(names), 50)]

    def query(batch):
        for attempt in range(3):
            try:
                return batch, _api(dict(action='query', titles='|'.join('File:' + n for n in batch),
                                        prop='imageinfo', iiprop='sha1|url|size'))
            except OSError:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1))

    result = {}
    with ThreadPoolExecutor(max_workers=threads) as pool:
        for done, (batch, data) in enumerate(pool.map(query, batches), 1):
            by_title = {canonical_file(p['title'].split(':', 1)[1]): p for p in data['query']['pages']}
            for name in batch:
                info = (by_title.get(canonical_file(name), {}).get('imageinfo') or [None])[0]
                if info:
                    result[name] = {'sha1': info['sha1'], 'url': info['url'],
                                    'size': (info.get('width'), info.get('height'))}
            if done % 100 == 0:
                print(f'  queried {done * 50:,}/{len(names):,} wiki files', flush=True)
    return result


def download(url: str, sha1: str, cache_dir: Path) -> bytes:
    path = cache_dir / sha1[:2] / f'{sha1}.png'
    if path.is_file():
        return path.read_bytes()
    request = urllib.request.Request(url, headers={'User-Agent': BROWSER_UA, 'Referer': 'https://gtnh.huijiwiki.com/'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
            break
        except OSError:
            # CDN 偶尔在并发下断开连接；稍等后重试。
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


# ── 本地状态库 ────────────────────────────────────────────────

def init_tables(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS visual_checks ('
                 'wiki_sha1 TEXT NOT NULL, content_sha1 TEXT NOT NULL, same INTEGER NOT NULL, '
                 'PRIMARY KEY (wiki_sha1, content_sha1))')


def _reshaped(info, path: Path) -> bool:
    """线上文件与本地文件的像素尺寸不同。线上没给出尺寸时当作相同。"""
    size = info.get('size')
    if not size or not all(size):
        return False
    with Image.open(path) as image:
        return image.size != tuple(size)


def check_pending(db_path, icons_dir, current_icons, exempt, cache_dir, threads=16) -> dict:
    """为待更新图标查线上文件并做判定，结果写回状态库；返回统计。"""
    from image_state import _load_exempt, set_uploaded
    with closing(sqlite3.connect(db_path)) as conn:
        init_tables(conn)
        rows = conn.execute(
            "SELECT i.icon_name, i.content_sha1, i.uploaded_sha1 FROM images i "
            "LEFT JOIN visual_checks v ON v.wiki_sha1=i.uploaded_sha1 AND v.content_sha1=i.content_sha1 "
            "WHERE i.icon_state='待更新' AND v.same IS NULL").fetchall()
        # 只靠黑名单算作已上传的图也要看线上尺寸：帧数或帧尺寸变了就不能再沿用旧文件。
        listed = conn.execute(
            "SELECT i.icon_name, i.content_sha1, i.uploaded_sha1 FROM images i "
            "WHERE i.icon_state='已上传' AND i.uploaded_sha1 != i.content_sha1").fetchall()
    rows += [row for row in listed if row[0] in exempt]
    rows = [row for row in rows if row[0] in current_icons]
    stats = {'pending': len(rows), 'onWiki': 0, 'sameBytes': 0, 'exempt': 0, 'reshaped': 0, 'visualSame': 0,
             'changed': 0, 'notOnWiki': 0, 'failed': 0}
    if not rows:
        return stats
    print(f'Checking {len(rows):,} pending icons against the wiki', flush=True)
    files = wiki_files(name for name, _, _ in rows)
    stats['onWiki'] = len(files)
    stats['notOnWiki'] = len(rows) - len(files)
    to_compare = []
    with closing(sqlite3.connect(db_path)) as conn, conn:
        _load_exempt(conn, exempt)
        for name, content, _ in rows:
            info = files.get(name)
            if info is None:
                continue
            reshaped = info['sha1'] != content and name in exempt and _reshaped(info, local_path(icons_dir, name))
            if reshaped:
                conn.execute('INSERT OR IGNORE INTO reshaped VALUES (?, ?)', (info['sha1'], content))
            set_uploaded(conn, name, info['sha1'])
            if info['sha1'] == content:
                stats['sameBytes'] += 1
            elif reshaped:
                stats['reshaped'] += 1
            elif name in exempt:
                stats['exempt'] += 1
            else:
                to_compare.append((name, content, info))

    def compare(task):
        name, content, info = task
        try:
            remote = download(info['url'], info['sha1'], cache_dir)
            return name, content, info['sha1'], same_picture(name, remote, local_path(icons_dir, name).read_bytes())
        except Exception as error:  # noqa: BLE001 - 单张失败保留待更新，下次重试
            print(f'  compare failed {name}: {error}', flush=True)
            return name, content, info['sha1'], None

    done = 0
    with ThreadPoolExecutor(max_workers=threads) as pool, closing(sqlite3.connect(db_path)) as conn:
        for name, content, wiki_sha1, same in pool.map(compare, to_compare):
            done += 1
            if same is None:
                stats['failed'] += 1
            else:
                stats['visualSame' if same else 'changed'] += 1
                conn.execute('INSERT OR REPLACE INTO visual_checks VALUES (?, ?, ?)', (wiki_sha1, content, int(same)))
                if same:
                    # 立即生效，同一次上传不会再传视觉相同的图。
                    conn.execute("UPDATE images SET icon_state='已上传' WHERE icon_name=? "
                                 "AND uploaded_sha1=? AND content_sha1=?", (name, wiki_sha1, content))
            if done % 1000 == 0:
                conn.commit()
                print(f'  compared {done:,}/{len(to_compare):,}', flush=True)
        conn.commit()
    return stats


def main(argv):
    replace = '--replace' in argv
    args = [a for a in argv if a != '--replace']
    if len(args) >= 3 and args[0] == 'stability':
        print(record_stability(args[1:], replace))
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == '__main__':
    main(sys.argv[1:])
