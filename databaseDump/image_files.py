"""
image_files.py
文件页（File:）的线上状态检查、删除和限速退避，供 image_upload.py、image_redirect.py、image_audit.py 共用。

MediaWiki 的坑：曾经上传过文件的 File: 名字，之后无论怎么编辑成 #REDIRECT 都不会生效，只被当成图片描述；
反过来，现在是重定向页的名字也不能直接上传图片。两种情况都必须先删除这个文件页，再新建（重定向或上传）。
所以写入前要先知道每个标题现在是什么状态。

判断“这个名字下有没有自己的文件”不能只看有没有 imageinfo：生效的重定向，接口也会返回目标文件的 imageinfo。
要比较 imageinfo.url 末尾的文件名和标题自己的文件名，相同才是自己的文件。
"""

import os
import threading
import time
import urllib.parse

# 状态
FREE = "free"                  # 页面不存在
OWN_FILE = "own_file"          # 这个名字下存着自己的文件，页面是普通描述
BROKEN = "broken"              # 自己的文件 + 页面文本是 #REDIRECT：重定向没生效，是最典型的坑
REDIRECT = "redirect"          # 页面是重定向页（文本是 #REDIRECT，且没有自己的文件）
PLAIN_PAGE = "plain_page"      # 存在，但既不是重定向也没有自己的文件（只剩描述页）

BATCH = 50
RATE_LIMIT_WAIT = float(os.environ.get("UPLOAD_RATE_LIMIT_WAIT", "120"))
RATE_LIMIT_RETRIES = 5


class RateLimitedError(RuntimeError):
    """API 返回 ratelimited（反滥用限速）；稍后重试通常能成功。"""


def is_rate_limited(code, message) -> bool:
    return code == "ratelimited" or "anti-abuse" in (message or "").lower()


def raise_for_error(response: dict):
    """把 API 的 error 转成异常：限速单独一类，便于退避重试。"""
    if "error" in response:
        err = response["error"]
        message = err.get("info", "")
        if is_rate_limited(err.get("code"), message):
            raise RateLimitedError(message)
        raise RuntimeError(message)


_pause_lock = threading.Lock()
_pause_until = 0.0


def call_with_backoff(action, label: str, stop: threading.Event | None = None):
    """执行 action；遇到限速就让所有线程暂停后重试同一件事，其他错误原样抛出。"""
    global _pause_until
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        while True:
            with _pause_lock:
                remaining = _pause_until - time.monotonic()
            if remaining <= 0 or (stop is not None and stop.is_set()):
                break
            time.sleep(min(remaining, 1.0))
        if stop is not None and stop.is_set():
            raise RuntimeError("已中断")
        try:
            return action()
        except RateLimitedError:
            if attempt == RATE_LIMIT_RETRIES:
                raise
            with _pause_lock:
                _pause_until = max(_pause_until, time.monotonic() + RATE_LIMIT_WAIT)
            print(f"  被限速：所有线程暂停 {RATE_LIMIT_WAIT:.0f} 秒后重试 {label}"
                  f"（{attempt + 1}/{RATE_LIMIT_RETRIES}）")


def _file_part(title: str) -> str:
    return title.split(":", 1)[1].replace(" ", "_")


def stored_under_own_name(page: dict) -> bool:
    return stored_name(page) == _file_part(page["title"])


def classify_page(page: dict) -> str:
    if page.get("missing"):
        return FREE
    if stored_under_own_name(page):
        return BROKEN if page.get("redirect") else OWN_FILE
    if page.get("redirect"):
        return REDIRECT
    return PLAIN_PAGE


# 想写重定向的名字：有自己文件的（OWN_FILE、BROKEN）要先删；想上传图片的名字：是重定向页的（REDIRECT、BROKEN）要先删。
BLOCKS_REDIRECT = {OWN_FILE, BROKEN}
BLOCKS_UPLOAD = {REDIRECT, BROKEN}


def stored_name(page: dict) -> str:
    """imageinfo 里实际存的文件名（生效的重定向是目标文件的名字），没有文件返回空串。"""
    info = page.get("imageinfo") or []
    if not info:
        return ""
    return urllib.parse.unquote(info[0].get("url", "").rsplit("/", 1)[-1]).replace(" ", "_")


def same_file_name(a: str, b: str) -> bool:
    """文件名比较：空格等同下划线，首字母大小写不敏感（MediaWiki 标题首字母自动大写）。"""
    a, b = a.replace(" ", "_"), b.replace(" ", "_")
    return bool(a) and a[:1].upper() + a[1:] == b[:1].upper() + b[1:]


def file_states(post, titles: list[str], stop: threading.Event | None = None) -> dict[str, str]:
    """标题 → 状态。post(params) 发 POST 并返回解析后的 JSON。每次最多查 50 个标题。"""
    return {title: state for title, (state, _) in file_details(post, titles, stop).items()}


def file_details(post, titles: list[str], stop: threading.Event | None = None) -> dict[str, tuple[str, str]]:
    """标题 → (状态, imageinfo 里实际存的文件名)。"""
    states: dict[str, tuple[str, str]] = {}
    for offset in range(0, len(titles), BATCH):
        batch = titles[offset:offset + BATCH]

        def ask(batch=batch):
            response = post({"action": "query", "format": "json", "formatversion": "2",
                             "titles": "|".join(batch), "prop": "info|imageinfo", "iiprop": "url"})
            raise_for_error(response)
            return response["query"]

        query = call_with_backoff(ask, f"查询 {batch[0]} 等 {len(batch)} 个标题", stop)
        normalized = {item["from"]: item["to"] for item in query.get("normalized", [])}
        pages = {page["title"]: page for page in query["pages"]}
        for title in batch:
            page = pages.get(normalized.get(title, title), {"title": title, "missing": True})
            states[title] = (classify_page(page), stored_name(page))
    return states


def delete_page(post, get, title: str, reason: str, stop: threading.Event | None = None) -> bool:
    """删除页面（文件页连同文件）。已经不存在算成功。返回是否真的删了。"""
    def run():
        token = get({"action": "query", "meta": "tokens", "format": "json"})["query"]["tokens"]["csrftoken"]
        response = post({"action": "delete", "format": "json", "title": title,
                         "token": token, "reason": reason, "bot": "1"})
        if response.get("error", {}).get("code") == "missingtitle":
            return False
        raise_for_error(response)
        return True

    return call_with_backoff(run, f"删除 {title}", stop)


def delete_keep_history(post, get, title: str, reason: str, token_box: dict,
                        stop: threading.Event | None = None) -> str | None:
    """删除文件页（连同文件），再只恢复页面文字的各个修订，文件保持删除。

    恢复后页面保持原来的页面 ID 和修订历史，页面上能看到 delete 和 undelete 日志，而且名字下不再有自己的文件，
    所以重定向可以生效。返回恢复出来的最新文本（用来判断是否还需要再编辑一次），页面原本不存在则返回 None。
    token_box 是每个线程自己的 {"csrf": ...}，令牌复用，失效时自动刷新。
    """
    def token():
        if not token_box.get("csrf"):
            token_box["csrf"] = get({"action": "query", "meta": "tokens", "format": "json"})["query"]["tokens"]["csrftoken"]
        return token_box["csrf"]

    def write(params):
        for attempt in range(2):
            response = post({**params, "format": "json", "token": token()})
            err = response.get("error", {})
            if err.get("code") == "badtoken" and attempt == 0:
                token_box["csrf"] = None
                continue
            return response
        return response

    def run():
        response = write({"action": "delete", "title": title, "reason": reason})
        if response.get("error", {}).get("code") == "missingtitle":
            return None
        raise_for_error(response)
        listing = get({"action": "query", "format": "json", "formatversion": "2", "prop": "deletedrevisions",
                       "titles": title, "drvprop": "ids|timestamp|content", "drvslots": "main", "drvlimit": "max"})
        raise_for_error(listing)
        revisions = []
        for page in listing["query"]["pages"]:
            revisions.extend(page.get("deletedrevisions", []))
        if not revisions:
            return ""
        latest = max(revisions, key=lambda rev: rev["timestamp"])
        restored = write({"action": "undelete", "title": title, "reason": "机器人：恢复页面历史，文件保持删除",
                          "timestamps": "|".join(rev["timestamp"] for rev in revisions)})
        raise_for_error(restored)
        return latest["slots"]["main"].get("content", "")

    return call_with_backoff(run, f"删除并恢复历史 {title}", stop)
