"""灰机平台对 Data: JSON 页的改写：对象键里的 . 和 $ 会被换成全角的 ．（U+FF0E）和 ＄（U+FF04）。

MongoDB 的字段名不能含 . 和 $，平台在保存 JSON 页时把键改成全角写法，页面源码里存的也是改写后的样子
（所以线上的 ItemNames 里是 ``T2．5内存``，模块:Database 查询时也按同样规则转换，并两种写法都找）。

工具链上传前先做同样的转换，哈希也按转换后的内容计算：本地文件、上传内容和线上源码一致，
清单不会因为这个改写而把没有变化的页面反复当成差异；平台改写与否，上传结果都一样。
只改对象的键，字符串值（物品名、NBT 等）和排版（缩进、分隔符）原样保留。
"""

import re

_STRING = re.compile(r'"(?:\\.|[^"\\])*"')
_KEY_TABLE = {ord('.'): '．', ord('$'): '＄'}


def normalize_keys(text: str) -> str:
    """JSON 文本里作为对象键的字符串：. → ．，$ → ＄。依靠词法扫描，不重新序列化，排版保持不变。"""
    if '.' not in text and '$' not in text:
        return text
    out, position = [], 0
    for match in _STRING.finditer(text):
        end = match.end()
        following = end
        while following < len(text) and text[following] in ' \t\r\n':
            following += 1
        out.append(text[position:match.start()])
        token = match.group(0)
        if following < len(text) and text[following] == ':':
            token = token.translate(_KEY_TABLE)
        out.append(token)
        position = end
    out.append(text[position:])
    return ''.join(out)


def normalize_page(text: str, path_or_title: str) -> str:
    """只处理 JSON 页（路径或标题以 .json 结尾）。"""
    return normalize_keys(text) if str(path_or_title).lower().endswith('.json') else text
