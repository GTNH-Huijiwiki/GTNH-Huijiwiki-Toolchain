"""GuideNH 的 Markdown/MDX → 维基文本（wikitext）转换。纯函数，不访问网络和文件系统。

输入是一页指南的正文（已去掉 YAML 表头），输出是维基文本。游戏数据相关的部分由调用方通过
``PageContext`` 注入：物品 ID → 物品 key、页面链接解析、图片解析、3D 场景静态图。

支持范围见 databaseDump/README.md 的“GuideNH”一节。不支持的交互内容
（函数图、思维导图等）输出一行提示，并记入 ``PageContext.report``。
"""

import re
import textwrap
from dataclasses import dataclass, field
from typing import Callable, Optional

# ── 颜色 ─────────────────────────────────────────────────────

# 游戏里的符号颜色是为深色背景挑的，放到白底维基上要换成更深的同色系。
WIKI_COLORS = {
    'BLACK': '#000000', 'DARK_BLUE': '#0000AA', 'DARK_GREEN': '#007A00', 'DARK_AQUA': '#008B8B',
    'DARK_RED': '#AA0000', 'DARK_PURPLE': '#8A008A', 'GOLD': '#B87800', 'GRAY': '#777777',
    'DARK_GRAY': '#555555', 'BLUE': '#3355EE', 'GREEN': '#1E9E1E', 'AQUA': '#0097A7',
    'RED': '#D32F2F', 'LIGHT_PURPLE': '#C03AC0', 'YELLOW': '#A07C00', 'WHITE': '#888888',
}
# 显式写出的游戏原色同样要换。
_BRIGHT_TO_WIKI = {
    '55FF55': '1E9E1E', 'FF5555': 'D32F2F', 'FFFF55': 'A07C00', '55FFFF': '0097A7', 'FF55FF': 'C03AC0',
    '5555FF': '3355EE', 'FFFFFF': '888888', 'AAAAAA': '777777', 'FFAA00': 'B87800', '00FFFF': '0097A7',
    '00AA00': '007A00', '00AAAA': '008B8B', 'AA00AA': '8A008A',
}


def wiki_color(value: str) -> Optional[str]:
    value = (value or '').strip()
    if not value:
        return None
    symbolic = WIKI_COLORS.get(value.upper().replace(' ', '_'))
    if symbolic:
        return symbolic
    match = re.fullmatch(r'#?([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})', value)
    if not match:
        return None
    digits = match.group(1).upper()
    if len(digits) == 3:
        digits = ''.join(char * 2 for char in digits)
    elif len(digits) == 8:  # ARGB
        digits = digits[2:]
    return '#' + _BRIGHT_TO_WIKI.get(digits, digits)


KEYBINDS = {
    'key.attack': '鼠标左键', 'key.use': '鼠标右键', 'key.pickItem': '鼠标中键', 'key.jump': '空格',
    'key.sneak': 'Shift', 'key.sprint': 'Ctrl', 'key.inventory': 'E', 'key.drop': 'Q',
    'key.chat': 'T', 'key.command': '/',
}

# GuideNH 的 recipe 处理器名 → 维基配方类型（模块:Recipe/MachineMapping）。认不出的不写 type。
HANDLER_TYPES = {
    'shaped': '有序合成', 'shaped crafting': '有序合成', 'minecraft.shaped': '有序合成',
    'shapeless': '无序合成', 'shapeless crafting': '无序合成', 'minecraft.shapeless': '无序合成',
    'smelting': '烧制', 'thaumcraft.arcane.shaped': '有序奥术合成', 'botania.runicaltar': '符文祭坛',
    'neiworldcrafting': 'AE2在世界中的合成', 'vendingmachine': 'Vending Machine',
    'gt.recipe.circuitassembler': '电路组装机', 'gt.recipe.laserengraver': '激光蚀刻机',
    'gt.recipe.chemicalreactor': '化学反应釜', 'gt.recipe.compressor': '压缩机',
    'gt.recipe.centrifuge': '离心机', 'gt.recipe.assembler': '组装机',
}

# ── 标签解析 ─────────────────────────────────────────────────

_NAME = re.compile(r'<(/?)([A-Za-z][A-Za-z0-9]*)')
_ATTR = re.compile(r'[A-Za-z_:][\w:.\-]*')


@dataclass
class Tag:
    name: str
    closing: bool
    selfclose: bool
    attrs: dict
    start: int
    end: int

    def attr(self, *names, default=None):
        for name in names:
            for key, value in self.attrs.items():
                if key.lower() == name.lower() and value is not True:
                    return value
        return default

    def flag(self, *names) -> bool:
        for name in names:
            for key, value in self.attrs.items():
                if key.lower() == name.lower():
                    return value is True or str(value).lower() not in ('false', '0', '')
        return False


def parse_tag(text: str, i: int) -> Optional[Tag]:
    """解析 text[i] 起的 <Tag attr="x" attr2={y} /> 或 </Tag>；属性值里的 > 不会截断。"""
    match = _NAME.match(text, i)
    if not match:
        return None
    closing, name = bool(match.group(1)), match.group(2)
    j, n, attrs = match.end(), len(text), {}
    while True:
        while j < n and text[j] in ' \t\r\n':
            j += 1
        if j >= n:
            return None
        if text[j] == '>':
            return Tag(name, closing, False, attrs, i, j + 1)
        if text.startswith('/>', j):
            return Tag(name, closing, True, attrs, i, j + 2)
        attr = _ATTR.match(text, j)
        if not attr or closing:
            return None
        key, j = attr.group(0), attr.end()
        k = j
        while k < n and text[k] in ' \t':
            k += 1
        if k < n and text[k] == '=':
            j = k + 1
            while j < n and text[j] in ' \t':
                j += 1
            if j >= n:
                return None
            if text[j] in '"\'':
                close = text.find(text[j], j + 1)
                if close < 0:
                    return None
                attrs[key] = text[j + 1:close]
                j = close + 1
            elif text[j] == '{':
                depth, k = 0, j
                while k < n:
                    if text[k] in '"\'':
                        quote = text[k]
                        k = text.find(quote, k + 1)
                        if k < 0:
                            return None
                    elif text[k] == '{':
                        depth += 1
                    elif text[k] == '}':
                        depth -= 1
                        if depth == 0:
                            break
                    k += 1
                if k >= n:
                    return None
                attrs[key] = text[j + 1:k].strip()
                j = k + 1
            else:
                bare = re.match(r'[^\s>/]+', text[j:])
                if not bare:
                    return None
                attrs[key] = bare.group(0)
                j += bare.end()
        else:
            attrs[key] = True


def find_close(text: str, pos: int, name: str) -> Optional[tuple]:
    """从 pos 起找与已打开的 <name> 配对的 </name>，返回 (闭合标签起点, 终点)。"""
    depth, i = 1, pos
    while True:
        j = text.find('<', i)
        if j < 0:
            return None
        tag = parse_tag(text, j)
        if not tag:
            i = j + 1
            continue
        if tag.name == name:
            if tag.closing:
                depth -= 1
                if depth == 0:
                    return j, tag.end
            elif not tag.selfclose:
                depth += 1
        i = tag.end


# ── 转换上下文 ───────────────────────────────────────────────

@dataclass
class Scene:
    file: str
    width: int = 320
    height: int = 280


@dataclass
class PageContext:
    page_key: str
    resolve_item: Callable[..., Optional[str]]            # (id, meta=None, nbt=None) -> 物品 key 或 None
    resolve_page: Callable[[str], Optional[tuple]]        # (href) -> (标题, 锚点文字, 是否外链) 或 None
    resolve_image: Callable[[str], Optional[tuple]]       # (src) -> (维基文件名, 宽, 高) 或 None
    scenes: list = field(default_factory=list)
    subpages: Callable[[], list] = lambda: []
    category_pages: Callable[[str], list] = lambda name: []
    report: dict = field(default_factory=dict)
    recipe_count: int = 0
    scene_index: int = 0
    # 整页包在 {{DatabaseWrapper}} 里：物品和配方写成 <span class="db-…">，整页只查一次库（见 README）。
    wrapped: bool = True
    guide_mod: str = ''   # 内容包的模组目录与页面路径：场景说明模板用它拼官方网页版的地址
    guide_page: str = ''
    has_formula: bool = False  # 页面里有公式，需要加载 MathJax

    def note(self, kind: str, detail: str):
        self.report.setdefault(kind, []).append(detail)

    def next_scene(self) -> Optional[Scene]:
        if self.scene_index < len(self.scenes):
            scene = self.scenes[self.scene_index]
            self.scene_index += 1
            return scene
        self.scene_index += 1
        return None


# ── 行内 ─────────────────────────────────────────────────────

_PAIRED_INLINE = {'Color', 'Tooltip', 'Spoiler', 'mark', 'u', 'kbd', 'sub', 'sup', 'a', 'SoundLink',
                  'CommandLink', 'Latex', 'QuestLink', 'b', 'i', 's', 'em', 'strong'}
_DROPPED = {'ImportStructure', 'ImportStructureLib', 'ImportPonder', 'RemoveBlocks', 'IsometricCamera',
            'BoxAnnotation', 'DiamondAnnotation', 'LineAnnotation', 'BlockAnnotation', 'TextAnnotation',
            'ImageAnnotation', 'Block', 'BlockStats', 'Param', 'Case', 'Switch', 'Default', 'Comment'}
_SELF_CONTAINED = {'ItemLink', 'ItemImage', 'BlockImage', 'KeyBind', 'PlayerName', 'RecipeFor', 'RecipesFor',
                   'Recipe', 'RecipesUsage', 'br'}
_KNOWN = _PAIRED_INLINE | _DROPPED | _SELF_CONTAINED | {'Tab', 'ContentTabs', 'Row', 'Column', 'GameScene', 'Scene'}
_ESCAPABLE = set('\\`*_{}[]()#+-.!|<>~=:&')
_ATOM = re.compile(r'\x00(\d+)\x01')


def _entity(char: str) -> str:
    return f'&#{ord(char)};'


def _template_arg(value: str) -> str:
    return value if '=' not in value else '1=' + value


def _item_wikitext(ctx: PageContext, ident: str, meta=None, nbt=None, mode='link', size=None, extra='') -> str:
    key = ctx.resolve_item(ident, meta, nbt)
    if not key:
        ctx.note('unresolvedItems', ident + (f':{meta}' if meta is not None else ''))
        return f'<code>{ident}</code>'
    if ctx.wrapped:
        label = key.replace('&', '&amp;').replace('<', '&lt;')
        if mode == 'icon':
            return '<span class="db-icon"' + (f' data-size="{size}"' if size else '') + f'>@{label}</span>'
        link = re.match(r'\|link=(.+)$', extra)
        return '<span class="db-item"' + (f' data-link="{link.group(1)}"' if link else '') + f'>@{label}</span>'
    if mode == 'icon':
        return '{{Icon|' + _template_arg('@' + key) + (f'|size={size}' if size else '') + '|nolink=1' + '}}'
    return '{{Item|' + _template_arg('@' + key) + extra + '}}'


def _recipe_wikitext(ctx: PageContext, tag: Tag) -> str:
    ident = tag.attr('id')
    if not ident:
        return ''
    name = tag.name
    usage = name == 'RecipesUsage'
    key = ctx.resolve_item(ident)
    if not key:
        ctx.note('unresolvedItems', ident)
        return f'<code>{ident}</code>'
    ctx.recipe_count += 1
    params = []
    if usage:
        params.append(('input', '@' + key))
    else:
        out = tag.attr('output')
        out_key = ctx.resolve_item(out) if out else None
        params.append(('output', '@' + (out_key or key)))
        inp = tag.attr('input')
        if inp:
            in_key = ctx.resolve_item(inp)
            if in_key:
                params.append(('input', '@' + in_key))
            else:
                ctx.note('unresolvedItems', inp)
    handler = (tag.attr('handlerName', 'handlerId') or '').strip()
    recipe_type = handler if re.search(r'[一-鿿]', handler) else HANDLER_TYPES.get(handler.lower())
    if recipe_type:
        params.append(('type', recipe_type))
    elif handler:
        ctx.note('unmappedHandlers', handler)
    limit = tag.attr('limit')
    if name in ('RecipesFor', 'RecipesUsage'):
        params.append(('limit', str(limit or 3)))
    if ctx.wrapped:
        return '<span class="db-recipe"' + ''.join(f' data-{name}="{value}"' for name, value in params) + '></span>'
    if ctx.recipe_count > 10:
        params.append(('async-rendering', '1'))
    return '{{Recipe|' + '|'.join(f'{name}={value}' for name, value in params) + '}}'


def inline(text: str, ctx: PageContext) -> str:
    atoms = []

    def atom(value: str) -> str:
        atoms.append(value)
        return f'\x00{len(atoms) - 1}\x01'

    # 行内代码
    def code_span(match):
        body = match.group(2).replace('</nowiki>', '&lt;/nowiki&gt;')
        return atom(f'<code><nowiki>{body}</nowiki></code>')
    text = re.sub(r'(`+)(.+?)\1', code_span, text)

    # 反斜杠转义（表格里的 \| 变成 {{!}}）
    def escaped(match):
        char = match.group(1)
        return atom('{{!}}' if char == '|' else _entity(char))
    text = re.sub(r'\\([\\`*_{}\[\]()#+\-.!|<>~=:&])', escaped, text)

    # $$公式$$
    text = re.sub(r'\$\$(.+?)\$\$', lambda m: atom(_latex(ctx, m.group(1))), text)

    text = _inline_tags(text, ctx, atom)

    # 图片与链接
    def image(match):
        alt, src = match.group(1), match.group(2)
        resolved = ctx.resolve_image(src)
        if not resolved:
            ctx.note('missingImages', src)
            return ''
        name, width, _ = resolved
        size = f'|{min(width, 600)}px' if width else ''
        return atom(f'[[File:{name}{size}|{_plain(alt)}]]' if alt else f'[[File:{name}{size}]]')
    text = re.sub(r'!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)', image, text)

    def link(match):
        label, href = match.group(1), match.group(2)
        if '\x00' in label and 'Item' in ''.join(atoms[int(i)] for i in _ATOM.findall(label)):
            return label  # 物品模板自带链接，不能再嵌套
        target = ctx.resolve_page(href)
        if target is None:
            ctx.note('brokenLinks', f'{href}')
            return label
        title, anchor, external = target
        if external:
            return atom(f'[{title} {label}]' if label.strip() else f'[{title}]')
        full = title + ('#' + anchor if anchor else '')
        return atom(f'[[{full}|{label}]]' if label.strip() else f'[[{full}]]')
    text = re.sub(r'\[((?:[^\[\]]|\[[^\]]*\])*)\]\(\s*([^)\s]*)(?:\s+"[^"]*")?\s*\)', link, text)

    # 强调
    text = re.sub(r'\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*', r"'''''\1'''''", text)
    text = re.sub(r'\*\*(?=\S)(.+?)(?<=\S)\*\*', r"'''\1'''", text)
    text = re.sub(r'(?<![\w_])__(?=\S)(.+?)(?<=\S)__(?![\w_])', r"'''\1'''", text)
    text = re.sub(r'(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])', r"''\1''", text)
    text = re.sub(r'(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])', r"''\1''", text)
    text = re.sub(r'~~(?=\S)(.+?)(?<=\S)~~', r'<s>\1</s>', text)
    text = re.sub(r'==(?=\S)(.+?)(?<=\S)==', r'<span style="background:#ffe9a8">\1</span>', text)
    text = re.sub(r'\+\+(?=\S)(.+?)(?<=\S)\+\+', r'<u>\1</u>', text)
    text = re.sub(r'\^\^(?=\S)(.+?)(?<=\S)\^\^', r'<u>\1</u>', text)

    # 剩余文本里的维基语法字符
    text = text.replace('{{', '&#123;&#123;').replace('}}', '&#125;&#125;')
    text = text.replace('[[', '&#91;&#91;').replace(']]', '&#93;&#93;')
    text = re.sub(r'<(?=/?[A-Za-z!])(?!/?(?:span|u|s|kbd|sub|sup|code|nowiki|br|div|b|i|em|strong|math|blockquote|small)\b)',
                  '&lt;', text)

    for _ in range(8):
        if '\x00' not in text:
            break
        text = _ATOM.sub(lambda m: atoms[int(m.group(1))], text)
    return text


def _text_escapes_outside(source: str) -> str:
    """游戏用的 jlatexmath 认 \\text{生长\\%}，MathJax 在文本模式里不处理转义，会把反斜杠原样显示。
    把 \\text{} 里的 \\% \\& \\# \\_ \\$ 挪到外面：\\text{A\\%B} → \\text{A}\\%\\text{B}。"""
    def fix(match):
        pieces = re.split(r'(\\[%&#_$])', match.group(1))
        if len(pieces) == 1:
            return match.group(0)
        return ''.join(piece if piece.startswith('\\') else (f'\\text{{{piece}}}' if piece else '')
                       for piece in pieces)
    return re.sub(r'\\text\{([^{}]*)\}', fix, source)


def _latex(ctx: 'PageContext', formula: str, display: bool = False) -> str:
    """维基用 MathJax（页面里加 {{#html:MathJax3}}）渲染 $…$ 与 $$…$$；nowiki 防止维基语法和模板参数分隔吞掉公式。"""
    ctx.has_formula = True
    source = _text_escapes_outside(re.sub(r'\s+', ' ', formula.strip())).replace('</nowiki', '&lt;/nowiki')
    mark = '$$' if display else '$'
    return f'<nowiki>{mark}{source}{mark}</nowiki>'


def _plain(text: str) -> str:
    text = re.sub(r'<[^>]*>', '', text)
    text = re.sub(r"[*_`~]|''+", '', text)
    return re.sub(r'\s+', ' ', text).strip()


def _inline_tags(text: str, ctx: PageContext, atom) -> str:
    """把行内的 MDX/HTML 标签换成原子；成对标签的内容递归转换。"""
    out, i, n = [], 0, len(text)
    while i < n:
        j = text.find('<', i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j])
        tag = parse_tag(text, j)
        if tag and tag.closing and tag.name in _KNOWN:
            i = tag.end  # 没有配对的闭合标签（作者写成了 <X ...></X>）
            continue
        if not tag or tag.closing:
            out.append('<')
            i = j + 1
            continue
        name, i = tag.name, tag.end
        if name in _SELF_CONTAINED and not tag.selfclose:
            empty_close = re.match(r'\s*</' + name + r'\s*>', text[i:])
            if empty_close:
                i += empty_close.end()
        inner = None
        if name in _PAIRED_INLINE and not tag.selfclose:
            close = find_close(text, tag.end, name)
            if close:
                inner = text[tag.end:close[0]]
                i = close[1]
        if name == 'br':
            clear = (tag.attr('clear') or '').lower()
            out.append(atom('{{Clear}}' if clear in ('all', 'left', 'right') else '<br/>'))
        elif name == 'ItemLink':
            ident = tag.attr('id')
            if not ident:
                ctx.note('badTags', 'ItemLink 缺少 id')
                continue
            link_to = tag.attr('linksTo')
            extra = ''
            if link_to:
                target = ctx.resolve_page(link_to)
                if target and not target[2]:
                    extra = '|link=' + target[0]
            out.append(atom(_item_wikitext(ctx, ident, extra=extra)))
            # 游戏里常写成“链接+图标”，维基的 {{Item}} 已带图标，去掉紧跟的同一物品图标
            peek = re.match(r'\s*<ItemImage\b[^>]*>', text[i:])
            if peek:
                follower = parse_tag(text, i + peek.group(0).index('<'))
                if follower and follower.attr('id') == ident and not follower.attr('label'):
                    i += peek.end()
        elif name == 'ItemImage':
            ident = tag.attr('id')
            if not ident:
                continue
            scale = _number(tag.attr('scale'), 1)
            if tag.attr('label'):
                out.append(atom(_item_wikitext(ctx, ident)))
            else:
                out.append(atom(_item_wikitext(ctx, ident, mode='icon', size=int(16 * scale))))
        elif name == 'BlockImage':
            ident = tag.attr('id')
            if not ident:
                continue
            scale = _number(tag.attr('scale'), 1)
            out.append(atom(_item_wikitext(ctx, ident, meta=tag.attr('meta'), nbt=tag.attr('nbt'),
                                           mode='icon', size=min(int(16 * scale), 96))))
        elif name in ('RecipeFor', 'RecipesFor', 'Recipe', 'RecipesUsage'):
            out.append(atom(_recipe_wikitext(ctx, tag)))
        elif name == 'KeyBind':
            ident = tag.attr('id', 'action') or ''
            out.append(atom(f'<kbd>{KEYBINDS.get(ident, ident.rsplit(".", 1)[-1])}</kbd>'))
        elif name == 'PlayerName':
            out.append('玩家')
        elif name == 'Color':
            color = wiki_color(tag.attr('id') or tag.attr('color') or '')
            body = inline(inner if inner is not None else '', ctx)
            out.append(atom(f'<span style="color:{color}">{body}</span>' if color else body))
        elif name in ('Tooltip', 'Spoiler'):
            body = inline(inner or '', ctx)
            label = tag.attr('label')
            if name == 'Tooltip' and label:
                out.append(atom(f'<span title="{_plain(body).replace(chr(34), "")}">{inline(label, ctx)}</span>'))
            else:
                out.append(atom(body))
        elif name in ('mark', 'u', 'kbd', 'sub', 'sup', 's', 'b', 'i', 'em', 'strong'):
            body = inline(inner or '', ctx)
            if name == 'mark':
                out.append(atom(f'<span style="background:#ffe9a8">{body}</span>'))
            else:
                out.append(atom(f'<{name}>{body}</{name}>'))
        elif name in ('SoundLink', 'CommandLink'):
            out.append(atom(inline(inner or '', ctx)))
        elif name == 'Latex':
            formula = tag.attr('formula') or (inner or '')
            out.append(atom(_latex(ctx, formula)))
        elif name == 'QuestLink':
            quest = tag.attr('id')
            label = tag.attr('text') or (inner or '')
            if quest:
                text_arg = '|' + inline(label, ctx) if label else ''
                out.append(atom('{{任务链接|' + quest + text_arg + '}}'))
        elif name == 'a':
            anchor = tag.attr('name')
            href = tag.attr('href')
            if anchor and not href:
                out.append(atom(f'<span id="{anchor}"></span>'))
            elif href:
                body = inline(inner or '', ctx)
                target = ctx.resolve_page(href)
                if target is None:
                    ctx.note('brokenLinks', href)
                    out.append(atom(body))
                else:
                    title, frag, external = target
                    out.append(atom(f'[{title} {body}]' if external else f'[[{title}{"#" + frag if frag else ""}|{body}]]'))
        elif name in _DROPPED:
            pass
        else:
            ctx.note('unsupportedTags', name)
            out.append('&lt;' + name + '&gt;')
    return ''.join(out)


def _number(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ── 块 ───────────────────────────────────────────────────────

CONTAINER_TAGS = {'GameScene', 'Scene', 'ContentTabs', 'Row', 'Column', 'details', 'FloatingImage', 'Category',
                  'SubPages', 'ItemGrid', 'Template', 'Mermaid', 'FunctionGraph', 'Function', 'div',
                  'CsvTable', 'ImportStructure', 'ImportStructureLib', 'ImportPonder', 'RemoveBlocks',
                  'Special', 'Structure', 'ColumnChart', 'BarChart', 'LineChart', 'PieChart', 'ScatterChart'}
_FENCE = re.compile(r'^(\s*)(`{3,}|~{3,})(.*)$')
_HEADING = re.compile(r'^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$')
_HR = re.compile(r'^\s{0,3}([-*_])(?:\s*\1){2,}\s*$')
_LIST = re.compile(r'^(\s*)([-*+]|\d+[.)])\s+(.*)$')
_SEP_CELL = re.compile(r'^\s*:?-{2,}:?\s*$')


def split_containers(text: str) -> list:
    """把正文切成 ('md', 文本) 与 ('tag', Tag, 内容) 两类片段；围栏代码块里的标签不算。"""
    segments, idx, n, start, fence = [], 0, len(text), 0, None
    while idx < n:
        eol = text.find('\n', idx)
        if eol < 0:
            eol = n
        line = text[idx:eol]
        if fence:
            if re.match(r'^\s*' + re.escape(fence[0]) + '{' + str(len(fence)) + r',}\s*$', line):
                fence = None
            idx = eol + 1
            continue
        opened = _FENCE.match(line)
        if opened:
            fence = opened.group(2)
            idx = eol + 1
            continue
        stripped = line.lstrip()
        if stripped.startswith('<'):
            tag = parse_tag(text, idx + len(line) - len(stripped))
            if tag and not tag.closing and tag.name in CONTAINER_TAGS:
                inner, end = '', tag.end
                if not tag.selfclose:
                    close = find_close(text, tag.end, tag.name)
                    if close:
                        inner, end = text[tag.end:close[0]], close[1]
                segments.append(('md', text[start:idx]))
                segments.append(('tag', tag, inner))
                idx = start = end
                continue
        idx = eol + 1
    segments.append(('md', text[start:]))
    return segments


def convert_document(text: str, ctx: PageContext) -> str:
    parts = []
    for segment in split_containers(text):
        if segment[0] == 'md':
            rendered = convert_markdown(segment[1], ctx)
        else:
            rendered = convert_container(segment[1], segment[2], ctx)
        if rendered.strip():
            parts.append(rendered.strip('\n'))
    return '\n\n'.join(parts)


def _is_block_start(line: str) -> bool:
    return bool(_HEADING.match(line) or _FENCE.match(line) or _HR.match(line) or _LIST.match(line)
                or line.lstrip().startswith(('>', '|')))


def _join_lines(lines: list) -> str:
    """段落折行：中文相邻不补空格；行尾两个空格或反斜杠是硬换行。"""
    result = ''
    for number, line in enumerate(lines):
        hard = line.endswith('  ') or line.endswith('\\')
        piece = line.strip()
        if line.endswith('\\'):
            piece = piece[:-1]
        if number:
            prev = result[-1:] if not result.endswith('<br/>') else ''
            cjk = lambda c: bool(c) and '⺀' <= c <= '鿿' or c in '，。、；：？！）】」』》'
            if result.endswith('<br/>') or (prev and cjk(prev) and cjk(piece[:1])):
                pass
            else:
                result += ' '
        result += piece
        if hard and number < len(lines) - 1:
            result += '<br/>'
    return result


def _safe_line_start(text: str) -> str:
    if re.match(r'^(?:[*#:;=\s]|-{4}|\{\|)', text):
        return '<nowiki/>' + text
    return text


def convert_markdown(text: str, ctx: PageContext) -> str:
    text = re.sub(r'<!--.*?-->', '', text, flags=re.S)
    text = re.sub(r'\{/\*.*?\*/\}', '', text, flags=re.S)
    lines = text.split('\n')
    blocks, i = [], 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        fence = _FENCE.match(line)
        if fence:
            marker, info = fence.group(2), fence.group(3).strip()
            body, i = [], i + 1
            while i < len(lines) and not re.match(r'^\s*' + re.escape(marker[0]) + '{' + str(len(marker)) + r',}\s*$', lines[i]):
                body.append(lines[i])
                i += 1
            i += 1
            blocks.append(_fenced(info, '\n'.join(body), ctx))
            continue
        heading = _HEADING.match(line)
        if heading:
            level = len(heading.group(1))
            title = inline(heading.group(2), ctx).replace("'''", '').strip()
            blocks.append('=' * level + ' ' + title + ' ' + '=' * level)
            i += 1
            continue
        if _HR.match(line):
            blocks.append('----')
            i += 1
            continue
        if line.lstrip().startswith('|') and i + 1 < len(lines) and _is_separator(lines[i + 1]):
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith('|'):
                rows.append(lines[i])
                i += 1
            if i < len(lines) and lines[i].strip().startswith('{:'):
                i += 1
            blocks.append(_table(rows, ctx))
            continue
        if line.lstrip().startswith('>'):
            quote = []
            while i < len(lines) and lines[i].lstrip().startswith('>'):
                quote.append(re.sub(r'^\s*>\s?', '', lines[i]))
                i += 1
            blocks.append(_blockquote(quote, ctx))
            continue
        if _LIST.match(line):
            items = []
            while i < len(lines):
                current = lines[i]
                if _LIST.match(current):
                    items.append(current)
                elif current.strip() and current.startswith((' ', '\t')) and items:
                    items.append(current)
                elif not current.strip() and i + 1 < len(lines) and (_LIST.match(lines[i + 1])
                                                                   or lines[i + 1].startswith(('  ', '\t'))) and items:
                    pass
                else:
                    break
                i += 1
            blocks.append(_list(items, ctx))
            continue
        paragraph = []
        while i < len(lines) and lines[i].strip() and not (paragraph and _is_block_start(lines[i])):
            paragraph.append(lines[i])
            i += 1
        joined = _join_lines(paragraph)
        display = (re.fullmatch(r'\$\$(.+)\$\$', joined.strip())
                   or re.fullmatch(r'<Latex\s+formula="([^"]+)"\s*/?>(?:</Latex>)?', joined.strip()))
        icons = _icon_only_paragraph(joined, ctx)
        if display and '$$' not in display.group(1):  # 独占一段的公式用 $$，MathJax 会居中显示
            blocks.append(_latex(ctx, display.group(1), display=True))
        elif icons:  # 整段只有连续的单独图标，用 IconList 排成一组，不直接摆图标
            blocks.append(icons)
        else:
            blocks.append(_safe_line_start(inline(joined, ctx)))
    return '\n\n'.join(block for block in blocks if block)


def _icon_only_paragraph(text: str, ctx: PageContext) -> Optional[str]:
    """整段只有 <ItemImage>、<BlockImage>（不带文字标签）时，返回 IconList；否则返回 None。

    整页包装时写成 <span class="db-icon-list">@key,@key</span>（由 {{DatabaseWrapper}} 统一查库），否则写 {{IconList|…}}。
    有任何一个物品解析不了，就整段回到普通写法，保留 <code>ID</code> 提示。
    """
    text, i, keys = text.strip(), 0, []
    while i < len(text):
        if text[i].isspace():
            i += 1
            continue
        tag = parse_tag(text, i)
        if not tag or tag.closing or tag.name not in ('ItemImage', 'BlockImage') or tag.attr('label'):
            return None
        ident = tag.attr('id')
        if not ident:
            return None
        i = tag.end
        if not tag.selfclose:
            close = re.match(r'\s*</' + tag.name + r'\s*>', text[i:])
            i += close.end() if close else 0
        key = ctx.resolve_item(ident, tag.attr('meta'), tag.attr('nbt')) if tag.name == 'BlockImage' \
            else ctx.resolve_item(ident)
        if not key:
            return None
        keys.append(key)
    if not keys:
        return None
    references = ','.join('@' + key.replace('\\', '\\\\').replace(',', '\\,') for key in keys)
    if ctx.wrapped:
        return '<span class="db-icon-list">' + references.replace('&', '&amp;').replace('<', '&lt;') + '</span>'
    return '{{IconList|' + ('1=' if '=' in references else '') + references + '}}'


def _merge_icon_lists(text: str) -> Optional[str]:
    """文本由若干个 IconList 块组成（别无他物）时，合并成一个 IconList，参数依次连续；否则返回 None。

    内容包里一行（Row）的每个图标常常各占一段，逐段转换会得到一个图标一个框，应当是同一组里连续的参数。
    """
    blocks = [block for block in text.split('\n\n') if block.strip()]
    references, wrapped = [], None
    for block in blocks:
        match = re.fullmatch(r'\s*<span class="db-icon-list">([^<]*)</span>\s*', block)
        if match:
            used_wrapped = True
        else:
            match = re.fullmatch(r'\s*\{\{IconList\|(?:1=)?([^{}]*)\}\}\s*', block)
            used_wrapped = False
        if not match or (wrapped is not None and wrapped != used_wrapped):
            return None
        wrapped = used_wrapped
        references.append(match.group(1))
    if not references:
        return None
    joined = ','.join(references)
    if wrapped:
        return '<span class="db-icon-list">' + joined + '</span>'
    return '{{IconList|' + ('1=' if '=' in joined else '') + joined + '}}'


def _fenced(info: str, body: str, ctx: PageContext) -> str:
    language = info.split()[0].lower() if info else ''
    if language == 'csv':
        rows = [[cell.strip() for cell in row.split(',')] for row in body.strip().splitlines() if row.strip()]
        if rows and ctx.wrapped:
            has_header = 'header=false' not in info
            return _html_table(rows[0] if has_header else [], rows[1:] if has_header else rows, [], ctx)
        if rows:
            out = ['{| class="wikitable"']
            for number, row in enumerate(rows):
                out.append('|-')
                out.extend(('! ' if number == 0 and 'header=false' not in info else '| ') + inline(cell, ctx) for cell in row)
            out.append('|}')
            return '\n'.join(out)
    if language in ('mermaid', 'funcgraph'):
        ctx.note('interactiveSkipped', language)
        return _INTERACTIVE_NOTE
    body = body.replace('</pre', '&lt;/pre')
    return '<pre>' + body + '</pre>'


def _is_separator(line: str) -> bool:
    cells = [cell for cell in line.strip().strip('|').split('|')]
    return bool(cells) and all(_SEP_CELL.match(cell) for cell in cells) and '-' in line


def _split_row(line: str) -> list:
    text = line.strip()
    if text.startswith('|'):
        text = text[1:]
    if text.endswith('|') and not text.endswith('\\|'):
        text = text[:-1]
    cells, current, in_code, i = [], [], False, 0
    while i < len(text):
        char = text[i]
        if char == '\\' and i + 1 < len(text) and text[i + 1] == '|':
            current.append('\\|')
            i += 2
            continue
        if char == '`':
            in_code = not in_code
        if char == '|' and not in_code:
            cells.append(''.join(current))
            current = []
        else:
            current.append(char)
        i += 1
    cells.append(''.join(current))
    return [cell.strip() for cell in cells]


def _html_table(header: list, body: list, aligns: list, ctx: PageContext, first_is_header=True) -> str:
    """整页包在模板参数里时，维基表格语法里的竖线会被当成参数分隔，改用 HTML 表格。"""
    def cells(tag, row):
        out = []
        for number, text in enumerate(row):
            align = aligns[number] if number < len(aligns) else ''
            style = f' style="text-align:{align}"' if align else ''
            out.append(f'<{tag}{style}>{inline(text, ctx) or "&nbsp;"}</{tag}>')
        return '<tr>' + ''.join(out) + '</tr>'
    lines = ['<table class="wikitable">']
    if header:
        lines.append(cells('th', header))
    lines += [cells('td', row) for row in body]
    return chr(10).join(lines + ['</table>'])


def _table(rows: list, ctx: PageContext) -> str:
    header = _split_row(rows[0])
    aligns = []
    for cell in _split_row(rows[1]):
        left, right = cell.startswith(':'), cell.endswith(':')
        aligns.append('center' if left and right else 'right' if right else 'left' if left else '')
    if ctx.wrapped:
        return _html_table(header, [_split_row(row) for row in rows[2:]], aligns, ctx)
    out = ['{| class="wikitable"', '|-']
    for number, cell in enumerate(header):
        out.append(_cell('!', cell, aligns, number, ctx))
    for row in rows[2:]:
        out.append('|-')
        for number, cell in enumerate(_split_row(row)):
            out.append(_cell('|', cell, aligns, number, ctx))
    out.append('|}')
    return '\n'.join(out)


def _cell(marker: str, text: str, aligns: list, number: int, ctx: PageContext) -> str:
    body = inline(text, ctx) or '&nbsp;'
    align = aligns[number] if number < len(aligns) else ''
    prefix = f' style="text-align:{align}" |' if align else ''
    return f'{marker}{prefix} {body}'


_ALERTS = {'NOTE': 'info', 'TIP': 'success', 'IMPORTANT': 'primary', 'WARNING': 'warning', 'CAUTION': 'danger'}


def _blockquote(lines: list, ctx: PageContext) -> str:
    alert = re.match(r'^\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*$', lines[0]) if lines else None
    if alert:
        body = convert_document('\n'.join(lines[1:]), ctx)
        return '{{Mbox\n|border=' + _ALERTS[alert.group(1)] + '\n|text=' + body.replace('|', '{{!}}') + '\n}}'
    return '<blockquote>\n' + convert_document('\n'.join(lines), ctx) + '\n</blockquote>'


def _list(lines: list, ctx: PageContext) -> str:
    entries = []  # (缩进, 是否有序, 文本行)
    for line in lines:
        match = _LIST.match(line.expandtabs(4))
        if match:
            entries.append([len(match.group(1)), match.group(2)[0].isdigit(), [match.group(3)]])
        elif entries:
            entries[-1][2].append(line.strip())
    out, stack = [], []
    for indent, ordered, parts in entries:
        while stack and stack[-1][0] >= indent and len(stack) > 0 and stack[-1][0] > indent:
            stack.pop()
        if stack and stack[-1][0] == indent:
            stack[-1] = (indent, ordered)
        else:
            stack.append((indent, ordered))
        prefix = ''.join('#' if kind else '*' for _, kind in stack)
        out.append(prefix + ' ' + inline(_join_lines(parts).replace('\n', ' '), ctx))
    return '\n'.join(out)


# ── 容器标签 ─────────────────────────────────────────────────

def scene_template(ctx: PageContext, file: str, align: str, width: int) -> str:
    """静态预览图交给 {{GuideNH场景}}：说明文字和指向官方网页版的链接都在模板里，生成的页面只传参数。"""
    guide = f'|模组={ctx.guide_mod}|页面={ctx.guide_page}' if ctx.guide_mod and ctx.guide_page else ''
    return '{{GuideNH场景|' + f'{file}|{align}|{width}' + guide + '}}'


_INTERACTIVE_NOTE = '{{Mbox\n|border=info\n|text=此处是游戏内的交互内容（函数图、思维导图等），网页上无法显示，请在游戏中查看。\n}}'


def _thumb(file: str, align: str, width: int, caption: str) -> str:
    return '{{Thumb|' + file + '|' + align + '|' + f'{width}px' + ('|' + caption if caption else '') + '}}'


# 游戏 GUI 像素与网页像素的大致比例（官方静态站的 --gui-scale 为 2）
COLUMN_SCALE = 2


def convert_container(tag: Tag, inner: str, ctx: PageContext) -> str:
    name = tag.name
    if name in ('GameScene', 'Scene'):
        scene = ctx.next_scene()
        if scene is None:
            ctx.note('missingScenes', ctx.page_key)
            return ''
        align = (tag.attr('align') or '').lower()
        width = min(int(_number(tag.attr('width'), scene.width) or scene.width), 480)
        return scene_template(ctx, scene.file, align if align in ('left', 'right') else 'center', width)
    if name == 'ContentTabs':
        return _tabs(inner, ctx)
    if name in ('Row', 'Column', 'div'):
        body = convert_document(textwrap.dedent(inner), ctx)
        gap = int(_number(tag.attr('gap'), 8))
        if name == 'Row':
            # 一行（Row）里全是单独的图标时，合并成一个 IconList 的连续参数；IconList 自己会排版，不再包 flex 的 div
            merged = _merge_icon_lists(body)
            if merged:
                return merged
            return f'<div style="display:flex;flex-wrap:wrap;align-items:flex-start;gap:{gap}px">\n{body}\n</div>'
        align = (tag.attr('align') or '').lower()
        width = tag.attr('width')
        style = []
        if align in ('left', 'right'):
            style.append(f'float:{align};margin:0 {"0 8px 12px" if align == "right" else "12px 8px 0"}')
        if width:
            pixels = int(_number(width, 300))
            if align in ('left', 'right'):
                style.append(f'width:{pixels}px;max-width:100%')
            else:
                # 游戏里的列宽是 GUI 像素，网页上约放大 2 倍；用 max-width 而不是 width，窄屏时随容器收缩，不会出现横向滚动条
                style.append(f'max-width:{pixels * COLUMN_SCALE}px;margin:0 auto')
        return '<div' + (f' style="{";".join(style)}"' if style else '') + f'>\n{body}\n</div>'
    if name == 'FloatingImage':
        src = tag.attr('src')
        resolved = ctx.resolve_image(src) if src else None
        if not resolved:
            ctx.note('missingImages', str(src))
            return convert_document(inner, ctx) if inner.strip() else ''
        file, natural, _ = resolved
        width = int(_number(tag.attr('displayWidth', 'width'), natural or 300))
        width = min(width, natural or width, 600)
        align = (tag.attr('align') or '').lower()
        thumb = _thumb(file, align if align in ('left', 'right') else 'center', width, _plain(tag.attr('title') or ''))
        return thumb + ('\n\n' + convert_document(inner, ctx) if inner.strip() else '')
    if name == 'SubPages':
        return '\n'.join(f'* [[{title}|{label}]]' for title, label in ctx.subpages())
    if name == 'Category':
        pages = ctx.category_pages(tag.attr('name') or '')
        return '\n'.join(f'* [[{title}|{label}]]' for title, label in pages)
    if name == 'details':
        summary = re.search(r'<summary>(.*?)</summary>', inner, re.S)
        label = _plain(inline(summary.group(1), ctx)) if summary else '展开'
        body = convert_document(inner[summary.end():] if summary else inner, ctx)
        return (f'<div class="mw-collapsible mw-collapsed" data-expandtext="{label}" data-collapsetext="{label}">'
                f'\n{body}\n</div>')
    if name in ('ImportStructure', 'ImportStructureLib', 'ImportPonder', 'RemoveBlocks'):
        return ''
    if name == 'Template':
        ctx.note('unsupportedTags', 'Template')
        return ''
    ctx.note('interactiveSkipped', name)
    return _INTERACTIVE_NOTE


def _tabs(inner: str, ctx: PageContext) -> str:
    tabs, i = [], 0
    while True:
        j = inner.find('<Tab', i)
        if j < 0:
            break
        tag = parse_tag(inner, j)
        if not tag or tag.name != 'Tab' or tag.closing:
            i = j + 4
            continue
        close = find_close(inner, tag.end, 'Tab')
        body = inner[tag.end:close[0]] if close else inner[tag.end:]
        # <tabber> 是扩展标签，内容不会经过 {{DatabaseWrapper}}，里面改用普通的 {{Item}}、{{Recipe}} 模板
        wrapped, ctx.wrapped = ctx.wrapped, False
        try:
            content = convert_document(textwrap.dedent(body), ctx)
        finally:
            ctx.wrapped = wrapped
        tabs.append((str(tag.attr('title') or f'标签{len(tabs) + 1}').replace('=', '＝').replace('|', '｜'), content))
        i = close[1] if close else len(inner)
    if not tabs:
        return convert_document(textwrap.dedent(inner), ctx)
    parts = [(('' if number == 0 else '|-|') + title + '=\n' + body) for number, (title, body) in enumerate(tabs)]
    return '<tabber>\n' + '\n'.join(parts) + '\n</tabber>'


def split_title(body: str) -> tuple:
    """正文开头的一级标题 → (标题纯文本, 去掉该标题后的正文)；没有则 (None, 原正文)。"""
    match = re.match(r'\s*#[ \t]+(.+?)[ \t]*#*[ \t]*(?:\n|\Z)', body)
    if not match:
        return None, body
    title = plain_text(match.group(1))
    return (title or None), body[match.end():]


def plain_text(markdown: str) -> str:
    """标题等需要纯文本的场合：去掉标记。"""
    text = re.sub(r'<[^>]*>', '', markdown)
    text = re.sub(r'!?\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'[*_`~]|==|\+\+', '', text)
    return re.sub(r'\s+', ' ', text).strip()
