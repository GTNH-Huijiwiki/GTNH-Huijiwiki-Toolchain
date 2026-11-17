"""本地上传状态：图片与中文别名分表，允许多个别名指向同一图片。"""

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path


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


def icon_hashes(directory, icons):
    return {name: hashlib.sha1((Path(directory) / name).read_bytes()).hexdigest() for name in icons}


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


def update_state(path, icons, redirects, hashes=None):
    init_db(path)
    with closing(sqlite3.connect(path)) as conn, conn:
        for icon in sorted(icons):
            conn.execute("INSERT OR IGNORE INTO images (icon_name,icon_state) VALUES (?, '待更新')", (icon,))
        if hashes is not None:
            for icon, sha1 in hashes.items():
                conn.execute("UPDATE images SET content_sha1=?, icon_state=CASE "
                             "WHEN uploaded_sha1=? THEN '已上传' "
                             "WHEN uploaded_sha1 IS NULL AND icon_state IN ('已上传','待核验') THEN '待核验' "
                             "ELSE '待更新' END WHERE icon_name=?", (sha1, sha1, icon))
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
