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
    info = page.get("imageinfo") or []
    if not info:
        return False
    stored = urllib.parse.unquote(info[0].get("url", "").rsplit("/", 1)[-1]).replace(" ", "_")
    return stored == _file_part(page["title"])


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


def file_states(post, titles: list[str], stop: threading.Event | None = None) -> dict[str, str]:
    """标题 → 状态。post(params) 发 POST 并返回解析后的 JSON。每次最多查 50 个标题。"""
    states: dict[str, str] = {}
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
            states[title] = classify_page(pages.get(normalized.get(title, title), {"title": title, "missing": True}))
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
