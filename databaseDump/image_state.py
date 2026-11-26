"""本地上传状态：图片与中文别名分表，允许多个别名指向同一图片。"""

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

from icon_names import directory_renames, local_path


def init_db(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS redirects ("
            "redirect_from TEXT PRIMARY KEY, icon_name TEXT NOT NULL, redirect_state TEXT NOT NULL)"
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(images)")}
        if "redirect_from" in columns:
            # 一次性迁移旧上传记录，普通扫描不会重建或覆盖人工选择。
            conn.execute(
                "INSERT OR IGNORE INTO redirects "
                "SELECT redirect_from, MIN(icon_name), "
                "CASE WHEN COUNT(DISTINCT icon_name) > 1 THEN '待更新' ELSE MIN(redirect_state) END "
                "FROM images WHERE redirect_from != '' GROUP BY redirect_from"
            )
            conn.execute("ALTER TABLE images RENAME TO images_legacy")
            conn.execute("CREATE TABLE images (icon_name TEXT PRIMARY KEY, icon_state TEXT NOT NULL)")
            conn.execute("INSERT INTO images SELECT icon_name, icon_state FROM images_legacy")
            conn.execute("DROP TABLE images_legacy")
        else:
            conn.execute("CREATE TABLE IF NOT EXISTS images (icon_name TEXT PRIMARY KEY, icon_state TEXT NOT NULL)")

        columns = {row[1] for row in conn.execute("PRAGMA table_info(images)")}
        for column in ("content_sha1", "uploaded_sha1"):
            if column not in columns:
                conn.execute(f"ALTER TABLE images ADD COLUMN {column} TEXT")
        # 视觉规则判定按（线上 sha1，本地 sha1）记录，见 image_check.py。
        conn.execute("CREATE TABLE IF NOT EXISTS visual_checks ("
                     "wiki_sha1 TEXT NOT NULL, content_sha1 TEXT NOT NULL, same INTEGER NOT NULL, "
                     "PRIMARY KEY (wiki_sha1, content_sha1))")
        # 黑名单图标里线上尺寸与本地不同的（线上 sha1，本地 sha1），黑名单对它们不起作用。
        conn.execute("CREATE TABLE IF NOT EXISTS reshaped ("
                     "wiki_sha1 TEXT NOT NULL, content_sha1 TEXT NOT NULL, PRIMARY KEY (wiki_sha1, content_sha1))")


# 线上已有文件时视为已上传：字节相同、视觉规则判定相同，或在黑名单（渲染不稳定）中且线上尺寸相同。
_SETTLED = ("uploaded_sha1=content_sha1 OR EXISTS (SELECT 1 FROM visual_checks v "
            "WHERE v.wiki_sha1=images.uploaded_sha1 AND v.content_sha1=images.content_sha1 AND v.same=1) "
            "OR (uploaded_sha1 IS NOT NULL AND icon_name IN (SELECT icon_name FROM temp.exempt) "
            "AND NOT EXISTS (SELECT 1 FROM reshaped r "
            "WHERE r.wiki_sha1=images.uploaded_sha1 AND r.content_sha1=images.content_sha1))")


def reshaped_icons(path) -> set[str]:
    """当前本地文件与线上文件尺寸不同、必须上传的黑名单图标。"""
    with closing(sqlite3.connect(path)) as conn:
        return {name for name, in conn.execute(
            "SELECT i.icon_name FROM images i JOIN reshaped r "
            "ON r.wiki_sha1=i.uploaded_sha1 AND r.content_sha1=i.content_sha1")}


def _load_exempt(conn, exempt):
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS exempt (icon_name TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM temp.exempt")
    conn.executemany("INSERT OR IGNORE INTO temp.exempt VALUES (?)", ((name,) for name in exempt))


def set_uploaded(conn, name, sha1, exempt=None):
    """记录线上当前文件的 sha1，并按判定规则更新状态。"""
    if exempt is not None:
        _load_exempt(conn, exempt)
    else:
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS exempt (icon_name TEXT PRIMARY KEY)")
    conn.execute("UPDATE images SET uploaded_sha1=? WHERE icon_name=?", (sha1, name))
    conn.execute(f"UPDATE images SET icon_state=CASE WHEN {_SETTLED} THEN '已上传' ELSE '待更新' END "
                 "WHERE icon_name=?", (name,))


HASH_CACHE = Path(__file__).resolve().parent / "source_state" / "file-sha1-cache.json"


def icon_hashes(directory, icons, cache_path=None):
    """文件 sha1；按（文件名、大小、修改时间）缓存在 source_state，未变的文件不再读取。

    GTNH-Dumper 输出的 PNG 是其内容缓存的硬链接，像素不变时修改时间也不变，重跑时几乎全部命中。
    """
    from concurrent.futures import ThreadPoolExecutor
    import json
    import os
    icons = set(icons)
    renames = directory_renames(Path(directory).parent)
    cache = {}
    if cache_path is not None and Path(cache_path).is_file():
        cache = json.loads(Path(cache_path).read_text("utf-8"))
    stats = {renames.get(entry.name, entry.name): (entry.stat().st_size, entry.stat().st_mtime_ns)
             for entry in os.scandir(directory) if renames.get(entry.name, entry.name) in icons}
    missing = icons - stats.keys()
    if missing:
        raise FileNotFoundError(f"图标文件缺失: {sorted(missing)[:5]}")
    result, todo = {}, []
    for name, (size, mtime) in stats.items():
        hit = cache.get(name)
        if hit and hit[0] == size and hit[1] == mtime:
            result[name] = hit[2]
        else:
            todo.append(name)

    def digest(name):
        return name, hashlib.sha1(local_path(directory, name).read_bytes()).hexdigest()

    with ThreadPoolExecutor(max_workers=8) as pool:
        for name, sha1 in pool.map(digest, todo):
            result[name] = sha1
    if cache_path is not None:
        cache.update({name: [stats[name][0], stats[name][1], result[name]] for name in todo})
        Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
        Path(cache_path).write_text(json.dumps(cache, separators=(",", ":")), "utf-8")
    if todo:
        print(f"Hashed {len(todo):,} image files; {len(result) - len(todo):,} from cache", flush=True)
    return result


def mark_uploaded(path, name, sha1):
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("UPDATE images SET uploaded_sha1=?, icon_state="
                     "CASE WHEN content_sha1=? THEN '已上传' ELSE '待更新' END WHERE icon_name=?",
                     (sha1, sha1, name))


def reconcile_legacy(path, live_hashes):
    with closing(sqlite3.connect(path)) as conn, conn:
        for name, sha1 in live_hashes.items():
            conn.execute("UPDATE images SET uploaded_sha1=?, icon_state="
                         "CASE WHEN content_sha1=? THEN '已上传' ELSE '待更新' END "
                         "WHERE icon_name=? AND icon_state='待核验'", (sha1, sha1, name))


def update_state(path, icons, redirects, hashes=None, exempt=()):
    init_db(path)
    with closing(sqlite3.connect(path)) as conn, conn:
        _load_exempt(conn, exempt)
        for icon in sorted(icons):
            conn.execute("INSERT OR IGNORE INTO images (icon_name,icon_state) VALUES (?, '待更新')", (icon,))
        if hashes is not None:
            for icon, sha1 in hashes.items():
                conn.execute("UPDATE images SET content_sha1=? WHERE icon_name=?", (sha1, icon))
                conn.execute("UPDATE images SET icon_state=CASE "
                             "WHEN uploaded_sha1 IS NULL AND icon_state IN ('已上传','待核验') THEN '待核验' "
                             f"WHEN {_SETTLED} THEN '已上传' "
                             "ELSE '待更新' END WHERE icon_name=?", (icon,))
        for title, icon in sorted(redirects.items()):
            if icon not in icons:
                raise ValueError(f"中文图片名指向缺失图片: {title} -> {icon}")
            conn.execute(
                "INSERT INTO redirects VALUES (?, ?, '待更新') "
                "ON CONFLICT(redirect_from) DO UPDATE "
                "SET icon_name=excluded.icon_name, redirect_state='待更新' "
                "WHERE redirects.icon_name != excluded.icon_name",
                (title, icon),
            )


def pending_redirects(path, current):
    if not Path(path).is_file():
        return []
    with closing(sqlite3.connect(path)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='redirects'").fetchone():
            raise ValueError("请先运行 image_inventory.py 迁移本地图片状态表")
        rows = conn.execute(
            "SELECT r.redirect_from, r.icon_name FROM redirects r "
            "JOIN images i ON i.icon_name=r.icon_name "
            "WHERE r.redirect_state='待更新' AND i.icon_state='已上传' ORDER BY r.redirect_from"
        ).fetchall()
    return [(title, icon) for title, icon in rows if current.get(title) == icon]
