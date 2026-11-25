"""
wiki_inventory.py
扫描本地 wikitext/ 文件 + 查询 wiki 当前状态 → 生成任务清单。

四种状态:
    pending_upload   本地有文件 → wiki 上需要存在（新增或更新）
    pending_delete   wiki 上有页面，但本地已没有对应文件 → 需要删除
    kept_online      wiki 上有、本地没有，但不删：保留目录（KEEP_ONLINE_DIRS），
                     或本地整个目录缺失而未用 --delete-dir 点名
    done             本地和 wiki 已同步

线上标题用 allpages 按前缀精确列举；比较时把下划线与空格视为同一字符。

用法:
    python wiki_inventory.py                          # 增量更新（本地未改动且已上传过的不再上传）
    python wiki_inventory.py --refresh                # 全量重建：全部重新上传
    python wiki_inventory.py --delete-dir Tc4research # 允许删除本地已整个移除的目录
"""

import os
import sys
import json
import sqlite3
from contextlib import closing
import urllib.request
import urllib.parse
import http.cookiejar

import guidenh_scope as gscope
from data_version import in_scope, require_scope, version_module_title, version_tag
from page_hash import file_sha1

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)


def _load_dotenv():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    search = [script_dir]
    parent = os.path.dirname(script_dir)
    search.append(parent)
    search.append(os.path.dirname(parent))
    for d in search:
        env_path = os.path.join(d, ".env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if not _line or _line.startswith("#"):
                        continue
                    _k, _sep, _v = _line.partition("=")
                    _k, _v = _k.strip(), _v.strip()
                    if _k and _v:
                        os.environ.setdefault(_k, _v)
            return


_load_dotenv()

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get("BASE_URL", "https://gtnh.huijiwiki.com/api.php")
if BASE == "http://gtnh.huijiwiki.com/api.php":
    BASE = "https://gtnh.huijiwiki.com/api.php"
AUTHKEY = os.environ.get("AUTHKEY", "")
USERNAME = os.environ.get("EDITOR_USERNAME", "")
PASSWORD = os.environ.get("EDITOR_PASSWORD", "")
WIKITEXT_DIR = os.path.join(_PROJECT_ROOT, "wikitext")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wiki_inventory.db")
WIKI_PREFIX = "Data:Versions/" + version_tag() + "/"

# ── HTTP ─────────────────────────────────────────────────────

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _get(params: dict) -> dict:
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-authkey": AUTHKEY, "User-Agent": "WikiBot/1.0"})
    with opener.open(req) as resp:
        return json.loads(resp.read())


def _post(params: dict) -> dict:
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(BASE, data=data,
        headers={"X-authkey": AUTHKEY, "User-Agent": "WikiBot/1.0",
                 "Content-Type": "application/x-www-form-urlencoded"})
    with opener.open(req) as resp:
        return json.loads(resp.read())


def login():
    r = _get({"action": "query", "meta": "tokens", "type": "login", "format": "json"})
    lt = r["query"]["tokens"]["logintoken"]
    r = _post({
        "action": "login", "format": "json",
        "lgname": USERNAME, "lgpassword": PASSWORD, "lgtoken": lt,
    })
    if r["login"]["result"] != "Success":
        raise RuntimeError(f"Login failed: {r['login'].get('reason', r['login']['result'])}")
    print(f"Logged in as: {r['login']['lgusername']}")


# ── Path / Title conversion ──────────────────────────────────

def path_to_title(rel_path: str) -> str:
    """本地相对路径 → wiki 标题。
    Data/Versions/GTNH/.../Items/0.json → Data:Versions/GTNH/.../Items/0.json
    Module/Database/Versions/GTNH/1.lua → Module:Database/Versions/GTNH/1
    """
    parts = rel_path.replace("\\", "/").split("/", 1)
    if len(parts) == 2:
        if parts[0] == "Module":
            parts[1] = parts[1].removesuffix(".lua")
        elif parts[0] == gscope.namespace():
            parts[1] = parts[1].removesuffix(gscope.PAGE_SUFFIX)  # GuideNH 页面
        return parts[0] + ":" + parts[1]
    return "0:" + parts[0]


def title_to_path(title: str) -> str:
    """wiki 标题 → 本地相对路径。"""
    ns, sep, rest = title.partition(":")
    if sep:
        if ns == "Module":
            rest += ".lua"
        return ns + "/" + rest
    return "0/" + rest


# ── Database ─────────────────────────────────────────────────

def init_db(refresh: bool):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        if refresh:
            conn.execute("DROP TABLE IF EXISTS tasks")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                title  TEXT PRIMARY KEY,
                path   TEXT,
                status TEXT NOT NULL DEFAULT 'pending_upload',
                mtime  REAL,
                size   INTEGER,
                sha1   TEXT
            )
        """)
        if 'sha1' not in {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}:
            conn.execute("ALTER TABLE tasks ADD COLUMN sha1 TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON tasks(status)")
        conn.commit()


def get_pending_uploads() -> list[tuple[str, str]]:
    """获取待上传任务列表，返回 [(path, title), ...]"""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        rows = conn.execute(
            "SELECT path, title FROM tasks WHERE status='pending_upload' AND path IS NOT NULL ORDER BY path"
        ).fetchall()
    return rows


def get_pending_deletes() -> list[str]:
    """获取待删除的 wiki 标题列表。"""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        rows = conn.execute(
            "SELECT title FROM tasks WHERE status='pending_delete' ORDER BY title"
        ).fetchall()
    return [r[0] for r in rows]


def mark_upload_done(path: str, title: str, mtime: float, size: int, sha1: str | None = None):
    """上传成功 → done（同时记录内容哈希）"""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            "UPDATE tasks SET status='done', mtime=?, size=?, sha1=? WHERE title=?",
            (mtime, size, sha1, title)
        )
        conn.commit()


def mark_delete_done(title: str):
    """删除成功 → 移除记录"""
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("DELETE FROM tasks WHERE title=?", (title,))
        conn.commit()


def get_stats() -> dict:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM tasks GROUP BY status"
        ).fetchall()
    return {r[0]: r[1] for r in rows}


# ── Scan & Cross-reference ───────────────────────────────────

def scan_local_files() -> dict[str, tuple[float, int]]:
    """扫描当前版本的数据页与配置模块，返回 {rel_path: (mtime, size)}。"""
    files = {}
    scan_root = os.path.join(WIKITEXT_DIR, title_to_path(WIKI_PREFIX).replace("/", os.sep))
    wikitext_abs = os.path.abspath(WIKITEXT_DIR)
    if not os.path.isdir(scan_root):
        print(f"  Directory not found: {scan_root}")
    for root, dirs, names in os.walk(scan_root):
        for name in names:
            abs_path = os.path.join(root, name)
            rel = os.path.relpath(abs_path, wikitext_abs).replace("\\", "/")
            stat = os.stat(abs_path)
            files[rel] = (stat.st_mtime, stat.st_size)

    config_path = title_to_path(version_module_title(WIKI_PREFIX))
    config_abs = os.path.join(wikitext_abs, config_path)
    if os.path.isfile(config_abs):
        stat = os.stat(config_abs)
        files[config_path] = (stat.st_mtime, stat.st_size)

    # GuideNH 页面（split_all.py guidenh 生成），只属于本体流程；数据目录缺失时也要扫描。
    if gscope.enabled():
        for root, dirs, names in os.walk(gscope.local_pages_directory(WIKITEXT_DIR)):
            for name in names:
                if name.endswith(gscope.PAGE_SUFFIX):
                    abs_path = os.path.join(root, name)
                    stat = os.stat(abs_path)
                    files[os.path.relpath(abs_path, wikitext_abs).replace("\\", "/")] = (stat.st_mtime, stat.st_size)
        for single in (gscope.local_landing_path(WIKITEXT_DIR), gscope.local_module_path(WIKITEXT_DIR)):
            if single.is_file():  # 总览页和导航框数据模块各只有一个文件
                stat = single.stat()
                files[os.path.relpath(single, wikitext_abs).replace("\\", "/")] = (stat.st_mtime, stat.st_size)
    return files


# 工具链不生成、但线上需要保留的目录（版本目录下第一级）。
# 目前没有：gtnhdimension 已由 split_all.py dimensions 生成（Dumper 已导出完整维度与星系参数）。
KEEP_ONLINE_DIRS = frozenset()


def canonical_title(title: str) -> str:
    """MediaWiki 把下划线与空格视为同一字符；比较标题前统一为空格。"""
    return " ".join(title.replace("_", " ").split())


def top_directory(title: str) -> str:
    """版本目录下的第一级目录名（如 Recipes）；配置模块等不在版本目录内的返回空串。"""
    canonical, prefix = canonical_title(title), canonical_title(WIKI_PREFIX)
    if gscope.is_guidenh_title(title):
        return gscope.top_directory()
    if not canonical.startswith(prefix):
        return ""
    rest = canonical[len(prefix):]
    return rest.split("/", 1)[0] if "/" in rest else ""


def fetch_wiki_titles() -> set[str]:
    """列出线上该版本目录下的全部数据页（allpages 按前缀精确列举，不经搜索引擎）。"""
    namespace, _, prefix = WIKI_PREFIX.partition(":")
    ns_id = {"Data": 3500}[namespace]
    titles = set()
    params = {"action": "query", "list": "allpages", "apnamespace": ns_id,
              "apprefix": prefix, "aplimit": "max", "format": "json"}
    while True:
        r = _get(params)
        for p in r["query"]["allpages"]:
            if in_scope(p["title"], WIKI_PREFIX):
                titles.add(p["title"])
        if "continue" not in r:
            break
        params.update(r["continue"])
    if gscope.enabled():
        titles |= fetch_guidenh_titles()
    print(f"  Fetched {len(titles):,} titles")
    return titles


def namespace_id(name: str) -> int:
    """按名称查命名空间 ID（如 指南 → 16）。"""
    r = _get({"action": "query", "meta": "siteinfo", "siprop": "namespaces", "format": "json"})
    for entry in r["query"]["namespaces"].values():
        if name in (entry.get("*"), entry.get("canonical")):
            return entry["id"]
    raise ValueError(f"线上没有命名空间: {name}")


def fetch_guidenh_titles() -> set[str]:
    """线上 GuideNH 前缀下的全部页面，以及导航框数据模块（标题统一成与本地一致的 Module: 写法）。"""
    titles = set()
    for namespace, prefix in ((gscope.namespace(), gscope.page_prefix()),
                              (gscope.MODULE_NAMESPACE, gscope.module_title().partition(":")[2])):
        params = {"action": "query", "list": "allpages", "apnamespace": namespace_id(namespace),
                  "apprefix": prefix, "aplimit": "max", "format": "json"}
        while True:
            r = _get(params)
            for p in r["query"]["allpages"]:
                title = p["title"]
                if namespace == gscope.MODULE_NAMESPACE:
                    title = gscope.MODULE_NAMESPACE + ":" + title.partition(":")[2]
                if gscope.is_guidenh_title(title):
                    titles.add(title)
            if "continue" not in r:
                break
            params.update(r["continue"])
    return titles


def rebuild_tasks(refresh: bool, delete_dirs=frozenset()):
    """扫描本地文件 + wiki 状态 → 重建任务清单。"""
    print("Scanning local files...")
    local_files = scan_local_files()
    print(f"  Found {len(local_files):,} files")

    # 加载旧任务中的 done 记录（用于变更检测）
    old_done = {}
    if not refresh:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            for row in conn.execute(
                "SELECT title, path, mtime, size, sha1 FROM tasks WHERE status='done' AND path IS NOT NULL"
            ):
                old_done[canonical_title(row[0])] = (row[1], row[2], row[3], row[4])

    login()
    print(f"Fetching wiki pages under '{WIKI_PREFIX}'...")
    wiki_titles = fetch_wiki_titles()
    print(f"  Found {len(wiki_titles):,} wiki pages")

    # 按规范标题比较：本地路径用下划线，线上标题用空格，二者是同一页面。
    local_titles = {}  # canonical title → path
    for rel_path in local_files:
        local_titles[canonical_title(path_to_title(rel_path))] = rel_path
    local_dirs = {top_directory(path_to_title(rel_path)) for rel_path in local_files} - {""}

    tasks = []
    upload_count = 0
    delete_count = 0
    done_count = 0
    unchanged_count = 0

    # ① 本地文件的对应 wiki 标题
    for rel_path, (mtime, size) in sorted(local_files.items()):
        title = path_to_title(rel_path)

        sha1 = None
        if canonical_title(title) in old_done:
            old_path, old_mtime, old_size, old_sha1 = old_done[canonical_title(title)]
            if old_path == rel_path:
                if old_mtime == mtime and old_size == size:
                    tasks.append((title, rel_path, "done", mtime, size, old_sha1))
                    done_count += 1
                    continue
                # 修改时间变了不代表内容变了：重新拆分会改写所有文件，内容哈希相同的页不必再传。
                sha1 = file_sha1(os.path.join(os.path.abspath(WIKITEXT_DIR), rel_path))
                if old_sha1 and old_sha1 == sha1:
                    tasks.append((title, rel_path, "done", mtime, size, sha1))
                    done_count += 1
                    unchanged_count += 1
                    continue

        # 需要上传（无论 wiki 上是否存在，直接覆盖）
        tasks.append((title, rel_path, "pending_upload", mtime, size, sha1))
        upload_count += 1

    # ② wiki 上有但本地没有的 → 待删除。
    # 保留目录不删；整个目录在本地缺失时（例如拆分因缺输入而跳过），只有 --delete-dir 点名才删。
    kept = {}
    for title in sorted(wiki_titles):
        if canonical_title(title) in local_titles:
            continue
        directory = top_directory(title)
        if directory in KEEP_ONLINE_DIRS or (directory not in local_dirs and directory not in delete_dirs):
            tasks.append((title, None, "kept_online", None, None, None))
            kept[directory] = kept.get(directory, 0) + 1
            continue
        tasks.append((title, None, "pending_delete", None, None, None))
        delete_count += 1
    for directory, count in sorted(kept.items()):
        reason = "保留目录" if directory in KEEP_ONLINE_DIRS else "本地整个目录缺失，未用 --delete-dir 点名"
        print(f"  Kept online {directory or '(root)'}/: {count} page(s) — {reason}")

    # 写入
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("DELETE FROM tasks")
        conn.executemany(
            "INSERT INTO tasks (title, path, status, mtime, size, sha1) VALUES (?, ?, ?, ?, ?, ?)", tasks
        )
        conn.commit()

    print(f"\nTask list rebuilt:")
    print(f"  pending_upload:  {upload_count}")
    print(f"  pending_delete:  {delete_count}")
    print(f"  done:            {done_count}（其中 {unchanged_count} 页只是修改时间变了，内容哈希相同）")


# ── Main ─────────────────────────────────────────────────────

def main(argv=None):
    require_scope(WIKI_PREFIX)
    args = sys.argv[1:] if argv is None else argv
    gscope.apply_cli_flag(args)
    refresh = "--refresh" in args
    stats_only = "--stats" in args
    delete_dirs = frozenset(args[i + 1] for i, a in enumerate(args) if a == "--delete-dir" and i + 1 < len(args))

    if stats_only:
        stats = get_stats()
        if stats:
            print(f"Tasks: {stats}")
        else:
            print("DB is empty. Run without --stats to build.")
        return

    init_db(refresh)
    rebuild_tasks(refresh, delete_dirs)

    stats = get_stats()
    print(f"\nFinal: {stats}")
    print(f"DB: {DB_PATH}")


if __name__ == "__main__":
    main()
