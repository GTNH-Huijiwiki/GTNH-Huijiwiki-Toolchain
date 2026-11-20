"""图片是否需要重传的判定：视觉规则、免更新名单和线上状态记录。

用法（在 .env 配好数据源后）:
    python image_check.py stability <dumpsA> <dumpsB>   # 比较同一环境两次导出，记录不稳定图片

判定规则（image_inventory.py 与 image_upload.py 共用）:
1. 本地文件 sha1 与线上文件 sha1 相同：不传。
2. 免更新：导出时是动图（itemicon.json 的 frameCount > 1，含静态图与帧条），或在不稳定名单里，
   且线上已有文件：不传。
3. 视觉规则：两张图缩到 32×32（BOX 平均、按 alpha 预乘）后逐像素取通道差最大值，
   平均值 ≤ 1 且最大值 ≤ 32 视为相同：不传。判定按（线上 sha1，本地 sha1）记在 image_inventory.db
   （随仓库提交，其他维护者同样复用），下次不再下载比对。
4. 其余上传。
"""

import io
import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from PIL import Image

FINGERPRINT_SIZE = 32
MEAN_LIMIT = 1.0
MAX_LIMIT = 32
UNSTABLE_CONFIG = Path(__file__).resolve().parent / 'config' / 'image_unstable.json'
# 惠迹 CDN 拒绝非浏览器 UA。
BROWSER_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36')


# ── 视觉规则 ──────────────────────────────────────────────────

def fingerprint(data: bytes):
    """32×32 预乘 RGBA；返回 (原尺寸, 像素元组列表)。"""
    with Image.open(io.BytesIO(data)) as image:
        size = image.size
        small = image.convert('RGBA').resize((FINGERPRINT_SIZE, FINGERPRINT_SIZE), Image.BOX)
    raw = small.tobytes()
    pixels = []
    for i in range(0, len(raw), 4):
        r, g, b, a = raw[i:i + 4]
        pixels.append((r * a // 255, g * a // 255, b * a // 255, a))
    return size, pixels


def visual_distance(a: bytes, b: bytes):
    """(平均差, 最大差)；尺寸不同返回 None。"""
    size_a, pa = fingerprint(a)
    size_b, pb = fingerprint(b)
    if size_a != size_b:
        return None
    diffs = [max(abs(x - y) for x, y in zip(u, v)) for u, v in zip(pa, pb)]
    return sum(diffs) / len(diffs), max(diffs)


def visually_same(a: bytes, b: bytes) -> bool:
    distance = visual_distance(a, b)
    return distance is not None and distance[0] <= MEAN_LIMIT and distance[1] <= MAX_LIMIT


# ── 免更新名单 ────────────────────────────────────────────────

def animated_icons(source) -> set[str]:
    """导出时是动图的物品：静态图与帧条都免更新。"""
    path = Path(source) / 'itemicon.json'
    if not path.is_file():
        return set()
    result = set()
    for row in json.loads(path.read_text('utf-8'))['shortKey']:
        if (row.get('frameCount') or 1) > 1 and row.get('iconName'):
            result.add(row['iconName'])
            result.add(row['iconName'].removesuffix('.png') + '_dynamic.png')
    return result


def unstable_icons() -> set[str]:
    if not UNSTABLE_CONFIG.is_file():
        return set()
    return set(json.loads(UNSTABLE_CONFIG.read_text('utf-8'))['icons'])


def exempt_icons(source) -> dict[str, str]:
    """图标名 → 免更新原因。"""
    reasons = {name: 'unstable' for name in unstable_icons()}
    reasons.update((name, 'animated') for name in animated_icons(source))
    return reasons


def pixel_manifest(source) -> dict[str, str]:
    path = Path(source) / 'icon-hashes.json'
    if not path.is_file():
        raise ValueError(f'缺少 Dumper 像素哈希清单: {path}')
    return json.loads(path.read_text('utf-8'))['images']


def record_stability(source_a, source_b) -> dict:
    """同一环境的两次导出中像素不同的图片，并入不稳定名单（只增不减）。"""
    a, b = pixel_manifest(source_a), pixel_manifest(source_b)
    changed = {name for name in a.keys() & b.keys() if a[name] != b[name]}
    known = unstable_icons()
    merged = sorted(known | changed)
    UNSTABLE_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    UNSTABLE_CONFIG.write_text(json.dumps({
        'description': '同一整合包、同一导出器连续两次导出像素仍不同的图标（渲染依赖时间或随机数）。'
                       '这些图标线上已有文件时不再更新。由 image_check.py stability 生成，只增不减。',
        'icons': merged}, ensure_ascii=False, indent=1) + '\n', 'utf-8')
    return {'compared': len(a.keys() & b.keys()), 'changed': len(changed),
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


def wiki_files(names) -> dict[str, dict]:
    """文件名 → {'sha1', 'url'}；线上不存在的不返回。"""
    from item_names import canonical_file
    names = sorted(names)
    result = {}
    for offset in range(0, len(names), 50):
        batch = names[offset:offset + 50]
        data = _api(dict(action='query', titles='|'.join('File:' + n for n in batch),
                         prop='imageinfo', iiprop='sha1|url'))
        by_title = {canonical_file(p['title'].split(':', 1)[1]): p for p in data['query']['pages']}
        for name in batch:
            info = (by_title.get(canonical_file(name), {}).get('imageinfo') or [None])[0]
            if info:
                result[name] = {'sha1': info['sha1'], 'url': info['url']}
    return result


def download(url: str, sha1: str, cache_dir: Path) -> bytes:
    path = cache_dir / sha1[:2] / f'{sha1}.png'
    if path.is_file():
        return path.read_bytes()
    request = urllib.request.Request(url, headers={'User-Agent': BROWSER_UA, 'Referer': 'https://gtnh.huijiwiki.com/'})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = response.read()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


# ── 本地状态库 ────────────────────────────────────────────────

def init_tables(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS visual_checks ('
                 'wiki_sha1 TEXT NOT NULL, content_sha1 TEXT NOT NULL, same INTEGER NOT NULL, '
                 'PRIMARY KEY (wiki_sha1, content_sha1))')


def check_pending(db_path, icons_dir, current_icons, exempt, cache_dir, threads=16) -> dict:
    """为待更新图标查线上文件并做判定，结果写回状态库；返回统计。"""
    from image_state import set_uploaded
    with closing(sqlite3.connect(db_path)) as conn:
        init_tables(conn)
        rows = conn.execute(
            "SELECT i.icon_name, i.content_sha1, i.uploaded_sha1 FROM images i "
            "LEFT JOIN visual_checks v ON v.wiki_sha1=i.uploaded_sha1 AND v.content_sha1=i.content_sha1 "
            "WHERE i.icon_state='待更新' AND v.same IS NULL").fetchall()
    rows = [row for row in rows if row[0] in current_icons]
    stats = {'pending': len(rows), 'onWiki': 0, 'sameBytes': 0, 'exempt': 0, 'visualSame': 0, 'changed': 0,
             'notOnWiki': 0, 'failed': 0}
    if not rows:
        return stats
    print(f'Checking {len(rows):,} pending icons against the wiki', flush=True)
    files = wiki_files(name for name, _, _ in rows)
    stats['onWiki'] = len(files)
    stats['notOnWiki'] = len(rows) - len(files)
    to_compare = []
    with closing(sqlite3.connect(db_path)) as conn, conn:
        for name, content, _ in rows:
            info = files.get(name)
            if info is None:
                continue
            set_uploaded(conn, name, info['sha1'])
            if info['sha1'] == content:
                stats['sameBytes'] += 1
            elif name in exempt:
                stats['exempt'] += 1
            else:
                to_compare.append((name, content, info))

    def compare(task):
        name, content, info = task
        try:
            remote = download(info['url'], info['sha1'], cache_dir)
            return name, content, info['sha1'], visually_same(remote, (Path(icons_dir) / name).read_bytes())
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
            if done % 1000 == 0:
                conn.commit()
                print(f'  compared {done:,}/{len(to_compare):,}', flush=True)
        conn.commit()
    return stats


def main(argv):
    if len(argv) == 3 and argv[0] == 'stability':
        print(record_stability(argv[1], argv[2]))
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == '__main__':
    main(sys.argv[1:])
