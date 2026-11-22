"""数据页的内容哈希：重新拆分只改了修改时间、内容没变的页面，不应重新上传。"""
import hashlib


def file_sha1(path) -> str:
    digest = hashlib.sha1()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()
