"""
image_audit.py
只读审计：当前计划里每个待上传名字和每个待建重定向名字，在线上现在是什么状态。
不登录、不写任何东西。结果按类别汇总，--output 可把每个类别的标题清单写成 JSON。

用法（--kind items|entities，默认 items）:
    python image_audit.py
    python image_audit.py --kind entities
    python image_audit.py --output audit.json     # 写出每个类别的标题清单
    python image_audit.py -t 4                    # 线程数，默认 4

类别（见 image_files.py 里的说明）:
    待上传名字：free 空着 / own_file 已有自己的文件（会被新版本覆盖）/ plain_page 只有描述页
                / redirect 现在是重定向页（上传前要删）/ broken 文件 + #REDIRECT 文本（上传前要删）
    待建重定向：free 空着，直接建 / redirect 已是重定向页（已生效的不用动）
                / own_file 名字下有自己的文件（建重定向要先删）/ broken 同上且文本已写成 #REDIRECT 但没生效
                / plain_page 只有描述页
"""

import collections
import json
import os
import sys
import threading
import urllib.parse
import urllib.request

import image_files
from image_kinds import kind_from_argv, kind_paths, kind_plan
from image_version import require_source

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)


def _load_dotenv():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    for d in (script_dir, os.path.dirname(script_dir), os.path.dirname(os.path.dirname(script_dir))):
        env_path = os.path.join(d, ".env")
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    key, _sep, value = line.partition("=")
                    key, value = key.strip(), value.strip()
                    if key and value:
                        os.environ.setdefault(key, value)
            return


_load_dotenv()

BASE = os.environ.get("BASE_URL", "https://gtnh.huijiwiki.com/api.php").replace("http://", "https://")
AUTHKEY = os.environ.get("AUTHKEY", "")
KIND = kind_from_argv()
DUMPS_DIR, INVENTORY_DB, _ICONS_DIR, NEW_CSV = kind_paths(KIND, os.path.dirname(os.path.abspath(__file__)))


def post(params: dict) -> dict:
    request = urllib.request.Request(
        BASE, data=urllib.parse.urlencode(params).encode(),
        headers={"X-authkey": AUTHKEY, "User-Agent": "WikiBot/1.0",
                 "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def audit(titles: list[str], threads: int) -> dict[str, str]:
    chunks = [titles[i::threads] for i in range(threads)]
    states: dict[str, str] = {}
    lock = threading.Lock()

    def run(chunk):
        result = image_files.file_states(post, chunk)
        with lock:
            states.update(result)
            print(f"  {len(states)}/{len(titles)}")

    workers = [threading.Thread(target=run, args=(chunk,)) for chunk in chunks if chunk]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    return states


def main():
    require_source(DUMPS_DIR)
    args = sys.argv[1:]
    threads = 4
    output = None
    for i, a in enumerate(args):
        if a == "-t" and i + 1 < len(args):
            threads = int(args[i + 1])
        if a == "--output" and i + 1 < len(args):
            output = args[i + 1]

    plan = kind_plan(KIND, DUMPS_DIR, NEW_CSV)
    uploads = ["File:" + name for name in sorted(plan["uploadIcons"])]
    redirects = sorted(plan["redirects"])
    print(f"{len(uploads):,} planned upload names, {len(redirects):,} planned redirect titles")

    report = {}
    for label, titles in (("uploads", uploads), ("redirects", redirects)):
        print(f"== {label}")
        states = audit(titles, threads)
        groups = collections.defaultdict(list)
        for title, state in states.items():
            groups[state].append(title)
        report[label] = {state: sorted(items) for state, items in groups.items()}
        print(f"{label}: " + ", ".join(f"{state} {len(items):,}" for state, items in sorted(groups.items())))

    blocked_up = sum(len(report["uploads"].get(s, [])) for s in image_files.BLOCKS_UPLOAD)
    blocked_rd = sum(len(report["redirects"].get(s, [])) for s in image_files.BLOCKS_REDIRECT)
    print(f"\n上传前要先删的名字：{blocked_up:,}；建重定向前要先删的名字：{blocked_rd:,}"
          f"（image_upload.py / image_redirect.py 加 --delete-existing 处理）")
    if output:
        with open(output, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=1)
        print(f"清单写入 {output}")


if __name__ == "__main__":
    main()
