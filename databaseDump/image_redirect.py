"""
image_redirect.py
多线程批量创建图片重定向 — 从 image_inventory.db 读取 redirect_state=待更新 的任务，
完成后实时标记为已上传。

用法（--kind items|entities 选择物品图片或实体图片，默认 items，见 image_kinds.py）:
    python image_redirect.py                   # 全部（4 线程）
    python image_redirect.py --kind entities   # 实体中文名 → 头像
    python image_redirect.py --debug           # 前 20 条
    python image_redirect.py -t 8              # 8 线程（线程多了会被站点限速）
    python image_redirect.py --delete-existing # 名字下已有真实文件的，先删除文件页再建重定向
    python image_redirect.py --recheck         # 不看状态库，按线上实际状态核对计划里全部重定向，没生效的补建

MediaWiki 的坑：曾上传过文件的 File: 名字，改成 #REDIRECT 不会生效，只当成图片描述；必须先删页面再新建。
所以创建前先查每个标题：名字下有自己文件的默认跳过并汇总（CONFLICT），加 --delete-existing 才先删后建。
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
from image_version import require_source
from image_state import pending_redirects
from image_state import update_state
import image_files
from image_kinds import LABELS, kind_from_argv, kind_paths, kind_plan, standalone_files

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

BASE = os.environ.get("BASE_URL", "https://gtnh.huijiwiki.com/api.php")
if BASE == "http://gtnh.huijiwiki.com/api.php":
    BASE = "https://gtnh.huijiwiki.com/api.php"
AUTHKEY = os.environ.get("AUTHKEY", "")
KIND = kind_from_argv()
DUMPS_DIR, INVENTORY_DB, _, NEW_CSV = kind_paths(KIND, os.path.dirname(os.path.abspath(__file__)))
USERNAME = os.environ.get("BOT_USERNAME", "")
PASSWORD = os.environ.get("BOT_PASSWORD", "")

_stop = threading.Event()
DEFAULT_THREADS = 4
BATCH = image_files.BATCH

# MediaWiki 标题非法字符 → "-"
_INVALID_TITLE_CHARS = str.maketrans("[]{}|<>", "-------")


def sanitize_title(title: str) -> str:
    prefix, sep, rest = title.partition(":")
    if sep:
        return prefix + ":" + rest.translate(_INVALID_TITLE_CHARS)
    return title.translate(_INVALID_TITLE_CHARS)


# ── Task DB ────────────────────────────────────────────────────

def get_pending_redirects(plan=None, recheck: bool = False) -> list[tuple[str, str]]:
    """读取当前 CSV 中有效且图片已上传的待更新重定向；recheck 时取计划里的全部重定向（目标图片要已上传）。"""
    if plan is None:
        plan = kind_plan(KIND, DUMPS_DIR, NEW_CSV)
    current_redirects = plan['redirects']
    update_state(INVENTORY_DB, set(plan['uploadIcons']), current_redirects)
    tasks = pending_redirects(INVENTORY_DB, current_redirects)
    if recheck:
        with closing(sqlite3.connect(INVENTORY_DB)) as conn:
            uploaded = {r[0] for r in conn.execute("SELECT icon_name FROM images WHERE icon_state='已上传'")}
        tasks = sorted((title, icon) for title, icon in current_redirects.items() if icon in uploaded)
    if KIND == 'entities' and tasks:
        # 实体中文名可能已被人工上传的独立图片占用，不把它改成重定向。
        # 退役项名下本来就是旧实体 PNG，照常走“有自己的文件”的流程（没加 --delete-existing 时只报冲突）。
        retired = set(plan.get('retired', ()))
        taken = standalone_files(title for title, _ in tasks if title not in retired)
        for title in sorted(taken):
            print(f"  left out {title}: 线上已有同名独立图片")
        tasks = [(title, icon) for title, icon in tasks if title not in taken]
    return tasks


def mark_redirect_done(redirect_from: str, icon_name: str):
    """重定向创建成功 → redirect_state = 已上传"""
    with closing(sqlite3.connect(INVENTORY_DB)) as conn:
        conn.execute(
            "UPDATE redirects SET redirect_state='已上传' WHERE redirect_from=? AND icon_name=?",
            (redirect_from, icon_name)
        )
        conn.commit()


# ── HTTP ─────────────────────────────────────────────────────

def _make_opener():
    return urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )


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
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    r = _get(opener, {"action": "query", "meta": "tokens", "type": "login", "format": "json"})
    lt = r["query"]["tokens"]["logintoken"]
    r = _post_form(opener, {
        "action": "login", "format": "json",
        "lgname": username, "lgpassword": password, "lgtoken": lt,
    })
    if r["login"]["result"] != "Success":
        raise RuntimeError(f"Login failed: {r['login'].get('reason', r['login']['result'])}")
    print(f"Logged in as: {r['login']['lgusername']}")
    return jar


def clone_jar(src: http.cookiejar.CookieJar) -> http.cookiejar.CookieJar:
    dst = http.cookiejar.CookieJar()
    dst._cookies = copy.deepcopy(src._cookies)
    return dst


def make_opener_with_jar(jar):
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def fetch_csrf(opener) -> str:
    r = _get(opener, {"action": "query", "meta": "tokens", "format": "json"})
    return r["query"]["tokens"]["csrftoken"]


def create_redirect(opener, from_title: str, to_title: str,
                    csrf: str, summary: str = "", bot: bool = True):
    """返回 None 表示 CSRF 过期需刷新。"""
    text = f"#REDIRECT [[{to_title}]]"
    params = {
        "action": "edit",
        "format": "json",
        "title": from_title,
        "text": text,
        "summary": summary,
        "token": csrf,
    }
    if bot:
        params["bot"] = "1"
    r = _post_form(opener, params)
    if "error" in r:
        err = r["error"]["info"]
        if "Invalid CSRF" in err or "badtoken" in err.lower():
            return None
        image_files.raise_for_error(r)
    return r["edit"]


# ── Worker ───────────────────────────────────────────────────

def worker(task_list: list, total: int, counters: dict, lock: threading.Lock,
           master_jar, delete_existing: bool = False, recheck: bool = False):
    """线程：独立 jar + 独立 CSRF，完成一条立刻写 DB。每 50 条先查一次这些标题的线上状态。"""
    opener = make_opener_with_jar(clone_jar(master_jar))
    csrf = fetch_csrf(opener)
    token_box = {"csrf": csrf}

    def post(params):
        return _post_form(opener, params)

    def get(params):
        return _get(opener, params)

    def report(status, from_title, to_title, msg=""):
        with lock:
            counters[status] = counters.get(status, 0) + 1
            counters["done"] += 1
            extra = f" - {msg}" if msg else ""
            print(f"[{counters['done']}/{total}] {status} {from_title} -> {to_title}{extra}")

    for offset in range(0, len(task_list), BATCH):
        if _stop.is_set():
            return
        batch = task_list[offset:offset + BATCH]
        try:
            details = image_files.file_details(post, [t for t, _ in batch], _stop)
            states = {t: d[0] for t, d in details.items()}
        except Exception as e:
            for from_title, icon_name in batch:
                report("FAIL", from_title, f"File:{icon_name}", f"查询线上状态失败: {e}")
            continue

        for (from_title, icon_name) in batch:
            if _stop.is_set():
                return
            to_title = f"File:{icon_name}"
            try:
                if (recheck and states.get(from_title) == image_files.REDIRECT
                        and image_files.same_file_name(details[from_title][1], icon_name)):
                    mark_redirect_done(from_title, icon_name)
                    report("SKIP", from_title, to_title, "已生效")
                    continue
                if states.get(from_title) in image_files.BLOCKS_REDIRECT:
                    if not delete_existing:
                        report("CONFLICT", from_title, to_title, "这个名字下已有自己的文件，写重定向不会生效；加 --delete-existing 先删再建")
                        continue
                    restored_text = image_files.delete_keep_history(
                        post, get, from_title, "机器人：这个名字要改成重定向，先删除原文件页", token_box, _stop)
                    counters["DELETED"] = counters.get("DELETED", 0) + 1
                    if restored_text is not None and restored_text.strip() == f"#REDIRECT [[{to_title}]]":
                        # 恢复出来的最新文本已经是要写的重定向，不用再编辑。
                        mark_redirect_done(from_title, icon_name)
                        report("OK", from_title, to_title, "删除后恢复历史，文本已是目标")
                        continue

                def edit():
                    nonlocal csrf
                    result = create_redirect(opener, from_title, to_title, csrf,
                                             summary="机器人：批量创建图片重定向", bot=True)
                    if result is None:
                        csrf = fetch_csrf(opener)
                        result = create_redirect(opener, from_title, to_title, csrf,
                                                 summary="机器人：批量创建图片重定向", bot=True)
                    return result

                result = image_files.call_with_backoff(edit, from_title, _stop)
                if result is None:
                    status, msg = "FAIL", "CSRF retry exhausted"
                else:
                    mark_redirect_done(from_title, icon_name)
                    status = "OK" if "nochange" not in result else "SKIP"
                    msg = ""
            except Exception as e:
                status, msg = "FAIL", str(e)
            report(status, from_title, to_title, msg)


# ── Main ─────────────────────────────────────────────────────

def main():
    require_source(DUMPS_DIR)
    _stop.clear()
    args = sys.argv[1:]
    debug = "--debug" in args
    threads = DEFAULT_THREADS
    delete_existing = "--delete-existing" in args
    recheck = "--recheck" in args

    for i, a in enumerate(args):
        if a == "-t" and i + 1 < len(args):
            threads = int(args[i + 1])

    print(f"[{LABELS[KIND]}]")
    plan = kind_plan(KIND, DUMPS_DIR, NEW_CSV)
    tasks = get_pending_redirects(plan, recheck)
    if not tasks:
        print("No pending redirects. Run image_inventory.py first.")
        return

    if debug:
        tasks = tasks[:20]
        threads = 2
        print(f"[DEBUG] Only {len(tasks)} tasks")

    total = len(tasks)
    print(f"{total:,} pending redirects, {threads} threads")

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
            args=(chunks[tid], total, counters, lock, master_jar, delete_existing, recheck),
            daemon=True,
        )
        t.start()
        workers.append(t)
        print(f"  Thread {tid}: {len(chunks[tid])} redirects")

    print(f"\nCreating {total} redirects... (Ctrl+C to stop)\n")

    try:
        for t in workers:
            while t.is_alive():
                t.join(timeout=0.5)
    except KeyboardInterrupt:
        print("\n\nInterrupted! Stopping...")
        _stop.set()
        for t in workers:
            t.join(timeout=2)

    remaining = len(get_pending_redirects(plan))  # 状态库里仍待更新的
    print(f"Done! OK: {counters['OK']}, SKIP: {counters['SKIP']}, FAIL: {counters['FAIL']}, "
          f"CONFLICT: {counters['CONFLICT']}, DELETED: {counters['DELETED']}")
    if counters["CONFLICT"]:
        print(f"{counters['CONFLICT']} titles already hold their own file, so a redirect there would not work. "
              f"Re-run with --delete-existing to delete those file pages and create the redirects.")
    if remaining:
        print(f"{remaining} redirects still pending (re-run to retry failures)")


if __name__ == "__main__":
    main()
