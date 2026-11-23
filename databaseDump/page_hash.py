"""数据页的内容哈希：重新拆分只改了修改时间、内容没变的页面，不应重新上传。

JSON 页按平台保存后的样子计算（键里的 . 和 $ 换成全角，见 platform_json.py），
这样和实际上传、线上保存的内容一致；没有带点号或 $ 的键的页面，哈希与原文件字节的 sha1 相同。
"""
import hashlib

from platform_json import normalize_keys


def file_sha1(path) -> str:
    digest = hashlib.sha1()
    if str(path).lower().endswith('.json'):
        with open(path, 'rb') as handle:
            data = handle.read()
        try:
            data = normalize_keys(data.decode('utf-8')).encode('utf-8')
        except UnicodeDecodeError:
            pass  # 不是 UTF-8 的文件按原字节计算
        digest.update(data)
        return digest.hexdigest()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()
