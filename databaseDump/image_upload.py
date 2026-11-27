"""
image_upload.py
多线程批量上传图标 — 从 image_inventory.db 读取 icon_state=待更新 的任务，
上传完成后实时标记为已上传。

用法（--kind items|entities 选择物品图片或实体图片，默认 items，见 image_kinds.py）:
    python image_upload.py                   # 全部（4 线程）
    python image_upload.py --kind entities   # 实体全身照与头像
    python image_upload.py --debug           # 前 20 张
    python image_upload.py -t 8              # 8 线程（线程多了会被站点限速）
    python image_upload.py --force-unstable  # 连同黑名单（config/image_unstable.json）里的图标一起上传
    python image_upload.py --delete-existing # 目标名字现在是重定向页的，先删除再上传

MediaWiki 的坑：现在是重定向页（文本为 #REDIRECT）的 File: 名字不能直接上传图片，必须先删页面。
上传前先查每个标题：是重定向页的默认跳过并汇总（CONFLICT），加 --delete-existing 才先删后传。
遇到站点限速时全部线程暂停（UPLOAD_RATE_LIMIT_WAIT 秒，默认 120）后重试。
"""

import os
import sys
import io
import json
import copy
import sqlite3
from contextlib import closing
import signal
import threading
import urllib.request
import urllib.parse
import http.cookiejar
import image_files
from image_version import require_source
from image_kinds import LABELS, kind_exempt, kind_from_argv, kind_paths, kind_plan, kind_unstable
from icon_names import icon_stem, local_path
from image_state import update_state, icon_hashes, mark_uploaded, reconcile_legacy, reshaped_icons, HASH_CACHE
from image_check import check_pending
from pathlib import Path
from item_names import canonical_file
import hashlib

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
KIND = kind_from_argv()
DUMPS_DIR, INVENTORY_DB, ICONS_DIR, NEW_CSV = kind_paths(KIND, os.path.dirname(os.path.abspath(__file__)))

BASE = os.environ.get("BASE_URL", "https://gtnh.huijiwiki.com/api.php")
if BASE == "http://gtnh.huijiwiki.com/api.php":
    BASE = "https://gtnh.huijiwiki.com/api.php"
AUTHKEY = os.environ.get("AUTHKEY", "")
USERNAME = os.environ.get("BOT_USERNAME", "")
PASSWORD = os.environ.get("BOT_PASSWORD", "")

_stop = threading.Event()
DEFAULT_THREADS = 4


# ── Task DB ────────────────────────────────────────────────────

def get_pending_icons(plan=None) -> list[str]:
    """读取当前来源中 icon_state=待更新 且本地存在的图标（去重）。"""
    if plan is None:
        plan = kind_plan(KIND, DUMPS_DIR, NEW_CSV)
    current_icons = set(plan['uploadIcons'])
    exempt = kind_exempt(KIND, current_icons)
    update_state(INVENTORY_DB, current_icons, plan['redirects'], icon_hashes(ICONS_DIR, current_icons, HASH_CACHE), exempt)
    with closing(sqlite3.connect(INVENTORY_DB)) as conn:
        legacy = [r[0] for r in conn.execute("SELECT icon_name FROM images WHERE icon_state='待核验'")
                  if r[0] in current_icons]
    # Verify legacy upload records that have no stored hash.
    opener = _make_opener()
    for offset in range(0, len(legacy), 50):
        batch = legacy[offset:offset + 50]
        data = _post_form(opener, dict(action='query', titles='|'.join('File:'+n for n in batch),
                                      prop='imageinfo', iiprop='sha1', format='json'))
        pages = {canonical_file(p['title'].split(':',1)[1]): p for p in data['query']['pages'].values()}
        reconcile_legacy(INVENTORY_DB, {n: pages.get(canonical_file(n), {}).get('imageinfo', [{}])[0].get('sha1')
                                      for n in batch})
    # 线上已有文件的先按判定规则核对，未变化或视觉相同的不重传。
    check_pending(INVENTORY_DB, ICONS_DIR, current_icons, exempt,
                  Path(__file__).resolve().parent / "source_state" / "wiki-originals")
    with closing(sqlite3.connect(INVENTORY_DB)) as conn:
        rows = conn.execute(
            "SELECT DISTINCT icon_name FROM images "
            "WHERE icon_state='待更新' AND icon_name != '' "
            "ORDER BY icon_name"
        ).fetchall()
    return [r[0] for r in rows if r[0] in current_icons]


def mark_icon_done(icon_name: str, sha1: str):
    mark_uploaded(INVENTORY_DB, icon_name, sha1)


# ── HTTP ─────────────────────────────────────────────────────

def _make_opener(jar=None):
    if jar is None:
        jar = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def _get(opener, params: dict) -> dict:
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-authkey": AUTHKEY, "User-Agent": "WikiBot/1.0"})
    with opener.open(req) as resp:
        return json.loads(resp.read())


def _post_form(opener, params: dict) -> dict:
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(BASE, data=data,
        headers={"X-authkey": AUTHKEY, "User-Agent": "WikiBot/1.0",
                 "Content-Type": "application/x-www-form-urlencoded"})
    with opener.open(req) as resp:
        return json.loads(resp.read())


def login_once(username: str, password: str) -> http.cookiejar.CookieJar:
    jar = http.cookiejar.CookieJar()
    opener = _make_opener(jar)
    r = _get(opener, {"action": "query", "meta": "tokens", "type": "login", "format": "json"})
    lt = r["query"]["tokens"]["logintoken"]
    r = _post_form(opener, {
        "action": "login", "format": "json",
        "lgname": username, "lgpassword": password, "lgtoken": lt,
    })
    if r["login"]["result"] != "Success":
        raise RuntimeError(f"Login failed: {r['login'].get('reason', r['login']['result'])}")
    print("Bot login succeeded")
    return jar


def clone_jar(src: http.cookiejar.CookieJar) -> http.cookiejar.CookieJar:
    dst = http.cookiejar.CookieJar()
    dst._cookies = copy.deepcopy(src._cookies)
    return dst


def make_opener_with_jar(jar):
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def upload_one(opener, path: str, bot: bool = True, filename: str | None = None) -> dict:
    """上传一张图片（multipart，per-thread CSRF 独立管理）。"""
    filename = filename or os.path.basename(path)
    with open(path, "rb") as f:
        file_data = f.read()

    for attempt in range(2):
        r_csrf = _get(opener, {"action": "query", "meta": "tokens", "format": "json"})
        csrf = r_csrf["query"]["tokens"]["csrftoken"]

        boundary = "----WikiBotBoundary"
        body = io.BytesIO()

        def add_field(k, v):
            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
            body.write(f"{v}\r\n".encode())

        add_field("action", "upload")
        add_field("format", "json")
        add_field("token", csrf)
        add_field("filename", filename)
        add_field("ignorewarnings", "1")
        if bot:
            add_field("bot", "1")

        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode())
        body.write(b"Content-Type: application/octet-stream\r\n\r\n")
        body.write(file_data)
        body.write(b"\r\n")
        body.write(f"--{boundary}--\r\n".encode())

        req = urllib.request.Request(BASE, data=body.getvalue(),
            headers={"X-authkey": AUTHKEY, "User-Agent": "WikiBot/1.0",
                     "Content-Type": f"multipart/form-data; boundary={boundary}"})
        with opener.open(req) as resp:
            r = json.loads(resp.read())

        if "error" in r:
            err = r["error"]["info"]
            if "Invalid CSRF" in err and attempt == 0:
                continue
            if r["error"].get("code") == "fileexists-no-change":
                # 同名文件的当前版本与本次内容完全相同（常见于上次请求已成功但响应中断）。
                return {"result": "Success", "_payload_sha1": hashlib.sha1(file_data).hexdigest()}
            image_files.raise_for_error(r)
        result = r["upload"]
        result["_payload_sha1"] = hashlib.sha1(file_data).hexdigest()
        return result

    raise RuntimeError("CSRF retry exhausted")


# ── Worker ───────────────────────────────────────────────────

def worker(icon_list: list, total: int, counters: dict, lock: threading.Lock,
           master_jar, delete_existing: bool = False):
    """线程：独立 jar + 独立 CSRF，上传自己分到的图标。每 50 张先查一次这些标题的线上状态。"""
    opener = make_opener_with_jar(clone_jar(master_jar))

    def post(params):
        return _post_form(opener, params)

    def get(params):
        return _get(opener, params)

    def report(status, icon_name, msg=""):
        with lock:
            counters[status] = counters.get(status, 0) + 1
            counters["done"] += 1
            extra = f" - {msg}" if msg else ""
            print(f"[{counters['done']}/{total}] {status} {icon_name}{extra}")

    for offset in range(0, len(icon_list), image_files.BATCH):
        if _stop.is_set():
            return
        batch = icon_list[offset:offset + image_files.BATCH]
        try:
            states = image_files.file_states(post, ["File:" + n for n in batch], _stop)
        except Exception as e:
            for icon_name in batch:
                report("FAIL", icon_name, f"查询线上状态失败: {e}")
            continue

        for icon_name in batch:
            if _stop.is_set():
                return
            path = str(local_path(ICONS_DIR, icon_name))

            if not os.path.exists(path):
                report("SKIP", icon_name, "(file not found)")
                continue

            try:
                if states.get("File:" + icon_name) in image_files.BLOCKS_UPLOAD:
                    if not delete_existing:
                        report("CONFLICT", icon_name, "这个名字现在是重定向页，不能直接上传；加 --delete-existing 先删再传")
                        continue
                    image_files.delete_page(post, get, "File:" + icon_name, "机器人：这个名字要上传真实图片，先删除重定向页", _stop)
                    counters["DELETED"] = counters.get("DELETED", 0) + 1
                result = image_files.call_with_backoff(
                    lambda: upload_one(opener, path, bot=True, filename=icon_name), icon_name, _stop)
                if result.get("result") == "Success":
                    status, msg = "OK", ""
                else:
                    raise RuntimeError(f"上传未成功: {result.get('result', '?')}")
                mark_icon_done(icon_name, result["_payload_sha1"])
            except Exception as e:
                # duplicate 可能指向另一文件名，不能据此认定目标图片已经可用。
                status, msg = "FAIL", str(e)
            report(status, icon_name, msg)


# ── Main ─────────────────────────────────────────────────────

def main():
    require_source(DUMPS_DIR)
    _stop.clear()
    args = sys.argv[1:]
    debug = "--debug" in args
    threads = DEFAULT_THREADS
    delete_existing = "--delete-existing" in args

    for i, a in enumerate(args):
        if a == "-t" and i + 1 < len(args):
            threads = int(args[i + 1])

    print(f"[{LABELS[KIND]}]")
    plan = kind_plan(KIND, DUMPS_DIR, NEW_CSV)
    tasks = get_pending_icons(plan)
    # 黑名单里的图标多次导出仍有波动，默认拒绝上传（线上没有的也不传），--force-unstable 才上传。
    # 线上尺寸与本地不同的除外：不传的话页面上的动图会显示错乱。
    unstable = kind_unstable(KIND)
    blocked = sorted({t for t in tasks if icon_stem(t) in unstable} - reshaped_icons(INVENTORY_DB))
    if blocked and "--force-unstable" not in args:
        tasks = [t for t in tasks if t not in set(blocked)]
        with closing(sqlite3.connect(INVENTORY_DB)) as conn:
            missing = [n for n in blocked if conn.execute(
                "SELECT uploaded_sha1 FROM images WHERE icon_name=?", (n,)).fetchone()[0] is None]
        print(f"Refused {len(blocked):,} blacklisted icons (config/image_unstable.json); "
              f"{len(missing):,} of them are not on the wiki yet. Use --force-unstable to upload them.")
        for name in missing[:20]:
            print(f"  not on wiki: {name}")
    if not tasks:
        print("No pending icons. Run image_inventory.py first.")
        return

    if debug:
        tasks = tasks[:20]
        threads = 2
        print(f"[DEBUG] Only {len(tasks)} icons")

    total = len(tasks)
    print(f"{total:,} pending icons, {threads} threads")

    master_jar = login_once(USERNAME, PASSWORD)

    def on_interrupt(_sig, _frame):
        print("\n\nInterrupted! Stopping...")
        _stop.set()
    signal.signal(signal.SIGINT, on_interrupt)

    chunks = [[] for _ in range(threads)]
    for i, t in enumerate(tasks):
        chunks[i % threads].append(t)

    counters = {"OK": 0, "SKIP": 0, "FAIL": 0, "CONFLICT": 0, "DELETED": 0, "done": 0}
    lock = threading.Lock()
    workers = []
    for tid in range(threads):
        if not chunks[tid]:
            continue
        t = threading.Thread(
            target=worker,
            args=(chunks[tid], total, counters, lock, master_jar, delete_existing),
            daemon=True,
        )
        t.start()
        workers.append(t)
        print(f"  Thread {tid}: {len(chunks[tid])} icons")

    print(f"\nUploading {total} icons... (Ctrl+C to stop)\n")

    try:
        for t in workers:
            while t.is_alive():
                t.join(timeout=0.5)
    except KeyboardInterrupt:
        print("\n\nInterrupted! Stopping...")
        _stop.set()
        for t in workers:
            t.join(timeout=2)

    remaining = len(get_pending_icons(plan))
    print(f"Done! OK: {counters['OK']}, SKIP: {counters['SKIP']}, FAIL: {counters['FAIL']}, "
          f"CONFLICT: {counters['CONFLICT']}, DELETED: {counters['DELETED']}")
    if counters["CONFLICT"]:
        print(f"{counters['CONFLICT']} titles are redirect pages right now, so the upload was skipped. "
              f"Re-run with --delete-existing to delete those pages and upload the images.")
    if remaining:
        print(f"{remaining} icons still pending (re-run to retry failures)")


if __name__ == "__main__":
    main()
