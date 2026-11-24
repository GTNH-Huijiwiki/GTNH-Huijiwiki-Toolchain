import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import data_upload
import guidenh
import guidenh_convert as convert
import guidenh_scope as scope
import image_kinds
import wiki_inventory


def make_context(**overrides):
    items = guidenh.ItemIndex([
        ('mod:block', 'mod:block', None),
        ('mod:block:3', 'mod:block:3', None),
        ('mod:tool', 'mod:tool_gLuB0gegBqh1aRe', '{}'),
        ('mod:tool', 'mod:tool_AAAAAAAAAAAAAAA', '{energy:40000.0d,mode:1b}'),
        ('mod:wild:32767', 'mod:wild:32767', None),
    ])
    pages = {'mod/a/b': ('指南:GuideNH/mod/a/b', ''), 'mod/b': ('指南:GuideNH/mod/b', ''), 'mod/index': ('指南:GuideNH/mod/index', '')}

    def resolve_page(href):
        if href.startswith('http'):
            return (href, '', True)
        anchor = href.split('#', 1)[1] if '#' in href else ''
        path = href.split('#', 1)[0]
        if not path:
            return ('', anchor, False)
        target = pages.get('mod/' + path.removesuffix('.md').replace('../', ''))
        return (target[0], anchor, False) if target else None

    values = dict(page_key='mod/a/b', resolve_item=items, resolve_page=resolve_page,
                  resolve_image=lambda src: ('GuideNH_mod_x.png', 800, 600) if src.endswith('x.png') else None,
                  scenes=[convert.Scene('GuideNH_scene_1.png', 320, 280)],
                  subpages=lambda: [('指南:GuideNH/mod/a/c', 'C')], report={}, wrapped=False)
    values.update(overrides)
    return convert.PageContext(**values)


def body(text, **overrides):
    return convert.convert_document(text, make_context(**overrides))


class ItemIndexTest(unittest.TestCase):
    def setUp(self):
        self.context = make_context()
        self.items = self.context.resolve_item

    def test_meta_zero_and_plain_keys(self):
        self.assertEqual(self.items('mod:block'), 'mod:block')
        self.assertEqual(self.items('mod:block:0'), 'mod:block')
        self.assertEqual(self.items('mod:block:3'), 'mod:block:3')
        self.assertEqual(self.items('MOD:Block:3'), 'mod:block:3')

    def test_nbt_variants_use_hash_keys(self):
        self.assertEqual(self.items('mod:tool'), 'mod:tool_gLuB0gegBqh1aRe')  # 没有指定 NBT：取空 NBT 版本
        self.assertEqual(self.items('mod:tool:0:{mode:1b,energy:40000.0d}'), 'mod:tool_AAAAAAAAAAAAAAA')  # 字段顺序无关
        self.assertEqual(self.items('mod:tool', nbt='{mode:1b,energy:40000.0d}'), 'mod:tool_AAAAAAAAAAAAAAA')

    def test_wildcard_and_unknown(self):
        self.assertEqual(self.items('mod:wild:*'), 'mod:wild:32767')
        self.assertIsNone(self.items('mod:missing'))
        self.assertEqual(self.items('mod:block', meta='3'), 'mod:block:3')


class InlineTest(unittest.TestCase):
    def test_item_link_and_following_icon_collapse(self):
        text = body('见 <ItemLink id="mod:block" showIcon="left" /><ItemImage id="mod:block" /> 与 <ItemImage id="mod:block:3" scale="2" />。')
        self.assertEqual(text, '见 {{Item|@mod:block}} 与 {{Icon|@mod:block:3|size=32|nolink=1}}。')

    def test_unresolved_item_is_reported(self):
        context = make_context()
        text = convert.convert_document('<ItemLink id="mod:nothing" />', context)
        self.assertEqual(text, '<code>mod:nothing</code>')
        self.assertEqual(context.report['unresolvedItems'], ['mod:nothing'])

    def test_emphasis_color_and_escapes(self):
        text = body('**粗** *斜* ~~删~~ <Color id="GREEN">绿</Color> `a|b` \\*字面\\*')
        self.assertIn("'''粗'''", text)
        self.assertIn("''斜''", text)
        self.assertIn('<s>删</s>', text)
        self.assertIn('<span style="color:#1E9E1E">绿</span>', text)
        self.assertIn('<code><nowiki>a|b</nowiki></code>', text)
        self.assertIn('&#42;字面&#42;', text)

    def test_explicit_argb_colors_drop_alpha_and_darken(self):
        self.assertEqual(convert.wiki_color('#FFFF55FF'), '#C03AC0')
        self.assertEqual(convert.wiki_color('#B7A0D6'), '#B7A0D6')
        self.assertIsNone(convert.wiki_color('nonsense'))

    def test_wikitext_syntax_in_text_is_neutralised(self):
        text = body('公式 {{x}} 与 [[y]]，以及 <player> 标签')
        self.assertNotIn('{{', text)
        self.assertNotIn('[[', text)
        self.assertIn('&lt;player&gt;', text)

    def test_links_images_and_stray_closing_tags(self):
        text = body('[乙](../b.md#sec) [外](https://example.com) ![图](../x.png) '
                    '<BlockImage id="mod:block"></BlockImage>')
        self.assertIn('[[指南:GuideNH/mod/b#sec|乙]]', text)
        self.assertIn('[https://example.com 外]', text)
        self.assertIn('[[File:GuideNH_mod_x.png|600px|图]]', text)
        self.assertNotIn('</BlockImage>', text)

    def test_broken_link_keeps_text(self):
        context = make_context()
        text = convert.convert_document('[甲](missing.md)', context)
        self.assertEqual(text, '甲')
        self.assertEqual(context.report['brokenLinks'], ['missing.md'])

    def test_attribute_values_may_contain_angle_brackets(self):
        tag = convert.parse_tag('<BlockImage id="mod:block" nbt=\'{a:"x>y"}\' scale={2} />', 0)
        self.assertEqual(tag.attr('nbt'), '{a:"x>y"}')
        self.assertEqual(tag.attr('scale'), '2')
        self.assertTrue(tag.selfclose)


class BlockTest(unittest.TestCase):
    def test_nested_lists(self):
        text = body('- 一\n  - 二\n    1. 三\n- 四\n\n1. 甲\n2. 乙')
        self.assertEqual(text.splitlines(), ['* 一', '** 二', '**# 三', '* 四', '# 甲', '# 乙'])

    def test_column_width_does_not_pin_content_to_the_left(self):
        wide = body('<Column width="600" align="center">\n\n正文\n\n</Column>')
        self.assertIn('max-width:1200px;margin:0 auto', wide)
        narrow = body('<Column width="300" align="center">\n\n正文\n\n</Column>')
        self.assertIn('max-width:600px;margin:0 auto', narrow)
        floated = body('<Column width="240" align="right">\n\n正文\n\n</Column>')
        self.assertIn('float:right', floated)
        self.assertIn('width:240px;max-width:100%', floated)

    def test_table_with_alignment_and_escaped_pipe(self):
        text = body('| A | B |\n| :-- | --: |\n| x\\|y | <ItemLink id="mod:block"/> |')
        self.assertIn('{| class="wikitable"', text)
        self.assertIn('! style="text-align:left" | A', text)
        self.assertIn('| style="text-align:right" | {{Item|@mod:block}}', text)
        self.assertIn('x{{!}}y', text)
        self.assertTrue(text.rstrip().endswith('|}'))

    def test_alert_and_quote(self):
        self.assertTrue(body('> [!WARNING]\n> 小心').startswith('{{Mbox\n|border=warning\n|text=小心'))
        self.assertTrue(body('> 引文').startswith('<blockquote>'))

    def test_code_fence_and_headings(self):
        text = body('## 标题 `x`\n\n```lua\nlocal a = {{1}}\n```')
        self.assertTrue(text.startswith('== 标题 '))
        self.assertIn('<pre>local a = {{1}}</pre>', text)

    def test_paragraph_joins_chinese_lines_without_spaces(self):
        self.assertEqual(body('第一行\n第二行\nline one\nline two'), '第一行第二行 line one line two')

    def test_scene_uses_next_static_preview_in_order(self):
        text = body('<GameScene align="right" width="400">\n  <ImportStructureLib controller="mod:block" />\n</GameScene>\n\n正文')
        self.assertTrue(text.startswith('{{GuideNH场景|GuideNH_scene_1.png|right|400'))
        context = make_context(scenes=[])
        convert.convert_document('<GameScene></GameScene>', context)
        self.assertEqual(context.report['missingScenes'], ['mod/a/b'])

    def test_tabs_and_subpages(self):
        text = body('<ContentTabs>\n  <Tab title="甲">\n  内容一\n  </Tab>\n  <Tab title="乙">\n  内容二\n  </Tab>\n</ContentTabs>\n\n<SubPages />')
        self.assertTrue(text.startswith('<tabber>\n甲=\n内容一\n|-|乙=\n内容二\n</tabber>'))
        self.assertTrue(text.endswith('* [[指南:GuideNH/mod/a/c|C]]'))

    def test_recipe_tags(self):
        text = body('<RecipeFor id="mod:block" handlerId="Shaped" />\n\n<RecipesFor id="mod:block" />\n\n<RecipesUsage id="mod:block" />')
        self.assertIn('{{Recipe|output=@mod:block|type=有序合成}}', text)
        self.assertIn('{{Recipe|output=@mod:block|limit=3}}', text)
        self.assertIn('{{Recipe|input=@mod:block|limit=3}}', text)

    def test_container_inside_code_fence_is_not_expanded(self):
        text = body('```\n<GameScene>\n</GameScene>\n```')
        self.assertIn('<pre>', text)
        self.assertIn('<GameScene>', text)

    def test_mdx_comments_are_removed(self):
        self.assertEqual(body('可见{/* 隐藏 */}文字'), '可见文字')


class WrappedModeTest(unittest.TestCase):
    """整页包在 {{DatabaseWrapper}} 里时，物品和配方写成 span，表格用 HTML。"""

    def test_items_icons_and_recipes_become_spans(self):
        text = body('<ItemLink id="mod:block" /> <ItemImage id="mod:block:3" scale="2" /> '
                    '<ItemLink id="mod:block" linksTo="b.md" />\n\n<RecipesFor id="mod:block" handlerId="Shaped" />',
                    wrapped=True)
        self.assertIn('<span class="db-item">@mod:block</span>', text)
        self.assertIn('<span class="db-icon" data-size="32">@mod:block:3</span>', text)
        self.assertIn('<span class="db-item" data-link="指南:GuideNH/mod/b">@mod:block</span>', text)
        self.assertIn('<span class="db-recipe" data-output="@mod:block" data-type="有序合成" data-limit="3"></span>', text)
        self.assertNotIn('{{Item', text)
        self.assertNotIn('{{Recipe', text)

    def test_tables_have_no_bare_pipes(self):
        text = body('| A | B |\n| --- | --- |\n| x | <ItemLink id="mod:block"/> |', wrapped=True)
        self.assertTrue(text.startswith('<table class="wikitable">'))
        self.assertNotIn('|', text.replace('{{!}}', ''))

    def test_tabber_content_falls_back_to_templates(self):
        text = body('<ContentTabs>\n<Tab title="甲">\n<ItemLink id="mod:block"/>\n</Tab>\n</ContentTabs>', wrapped=True)
        self.assertIn('{{Item|@mod:block}}', text)
        context = make_context(wrapped=True)
        convert.convert_document('<ContentTabs><Tab title="x">y</Tab></ContentTabs>', context)
        self.assertTrue(context.wrapped)

    def test_body_has_no_unprotected_pipes(self):
        text = body('## 标题\n\n[链](b.md) {{x}} `a|b`\n\n```\na|b\n```\n\n> [!NOTE]\n> 注意 <ItemLink id="mod:block"/>',
                    wrapped=True)
        self.assertNotIn('|', strip_protected(text))

    def test_skipped_interactive_content_uses_mbox(self):
        text = body('<FunctionGraph title="图"></FunctionGraph>', wrapped=True)
        self.assertTrue(text.startswith('{{Mbox'))


def strip_protected(text):
    """去掉模板参数里不会被当成分隔符的部分：{{模板}}、[[链接]]、<nowiki>、<pre>。"""
    import re
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r'\{\{[^{}]*\}\}|\[\[[^\[\]]*\]\]|<nowiki>.*?</nowiki>|<pre>.*?</pre>', '', text, flags=re.S)
    return text


class FormulaTest(unittest.TestCase):
    """公式交给维基的 MathJax：行内 $…$，独占一段 $$…$$，用 nowiki 保护。"""

    def test_inline_forms(self):
        context = make_context()
        text = convert.convert_document('速度 $$k = 2$$ 倍，<Latex formula="x^2 | y" /> 以及 `$不是公式$`', context)
        self.assertIn('<nowiki>$k = 2$</nowiki>', text)
        self.assertIn('<nowiki>$x^2 | y$</nowiki>', text)
        self.assertIn('<code><nowiki>$不是公式$</nowiki></code>', text)
        self.assertTrue(context.has_formula)

    def test_display_forms(self):
        self.assertEqual(body('$$\\frac{1}{2}$$'), '<nowiki>$$\\frac{1}{2}$$</nowiki>')
        self.assertEqual(body('<Latex formula="a_{max} = b" />'), '<nowiki>$$a_{max} = b$$</nowiki>')

    def test_escaped_symbols_inside_text_move_outside(self):
        # MathJax 的文本模式不处理 \%，会显示出反斜杠
        self.assertEqual(body('<Latex formula="\\text{生长\\%} = 1" />'), '<nowiki>$$\\text{生长}\\% = 1$$</nowiki>')
        self.assertEqual(body('$$\\text{甲\\%乙} + \\text{丙}$$'), '<nowiki>$$\\text{甲}\\%\\text{乙} + \\text{丙}$$</nowiki>')
        self.assertEqual(body('$$100\\% \\times 2$$'), '<nowiki>$$100\\% \\times 2$$</nowiki>')

    def test_pages_without_formulas_do_not_load_mathjax(self):
        context = make_context()
        convert.convert_document('没有公式', context)
        self.assertFalse(context.has_formula)

    def test_formula_survives_the_wrapper_argument(self):
        text = body('速度 $$a|b$$', wrapped=True)
        self.assertNotIn('|', strip_protected(text))


class TitleAndOfficialLinkTest(unittest.TestCase):
    def test_split_title(self):
        self.assertEqual(convert.split_title('\n# 超净间\n\n正文'), ('超净间', '\n正文'))
        self.assertEqual(convert.split_title('# 带 **粗体** 的 `标题`\n正文'), ('带 粗体 的 标题', '正文'))
        self.assertEqual(convert.split_title('正文\n# 后面的标题'), (None, '正文\n# 后面的标题'))
        self.assertEqual(convert.split_title('## 二级标题'), (None, '## 二级标题'))

    def test_display_title_line_drops_parser_characters(self):
        self.assertEqual(guidenh.display_title_line('超净间 {x}|[y]'), '{{DISPLAYTITLE:超净间 xy}}\n')
        self.assertEqual(guidenh.display_title_line('{}'), '')

    def test_official_url_matches_the_published_site(self):
        self.assertEqual(scope.official_url('changelog', 'multis/nac'),
                         'https://www.gtnewhorizons.com/GTNH-Guide-Pack/guides/changelog/guidenh/zh_cn/multis/nac.html')
        with patch.dict(os.environ, {'GUIDENH_SITE_URL': 'https://example.com/pack/'}):
            self.assertEqual(scope.official_url('a', 'b/c'), 'https://example.com/pack/guides/a/guidenh/zh_cn/b/c.html')

    def test_scene_is_a_template_call_that_builds_the_official_link(self):
        # 说明文字和官方网页版链接在 {{GuideNH场景}} 模板里，页面只传参数，以后调整不必重新生成页面
        context = make_context(guide_mod='gregtech', guide_page='multiblocks/hv/cleanroom')
        text = convert.convert_document('<GameScene></GameScene>', context)
        self.assertEqual(text, '{{GuideNH场景|GuideNH_scene_1.png|center|320|模组=gregtech|页面=multiblocks/hv/cleanroom}}')
        plain = convert.convert_document('<GameScene></GameScene>', make_context())
        self.assertEqual(plain, '{{GuideNH场景|GuideNH_scene_1.png|center|320}}')

    def test_header_is_a_template_call(self):
        page = guidenh.Page('gregtech', 'multiblocks/hv/cleanroom', Path('x.md'),
                            {'author': 'a|b', 'date': '2026-05-26', 'navigation': {'title': '超净间'}}, '')
        site = guidenh.Site(Path('.'), {page.key: page}, Path('.'), None)
        # 上级导航最前面总是总览页（指南:GuideNH）
        self.assertEqual(guidenh.header(site, page, '4988a65'),
                         '{{GuideNH页首|上级=[[指南:GuideNH|总览]]|模组=gregtech|页面=multiblocks/hv/cleanroom|版本=4988a65'
                         '|作者=a{{!}}b|日期=2026-05-26}}')

    def test_breadcrumb_starts_with_the_overview_and_follows_the_navigation_tree(self):
        root = guidenh.Page('gt', 'index', Path('index.md'), {'navigation': {'title': '首页'}}, '')
        child = guidenh.Page('gt', 'machines', Path('machines.md'),
                             {'navigation': {'title': '机器', 'parent': 'index.md'}}, '')
        leaf = guidenh.Page('gt', 'machines/ebf', Path('ebf.md'),
                            {'navigation': {'title': '高炉', 'parent': '../machines.md'}}, '')
        site = guidenh.Site(Path('.'), {p.key: p for p in (root, child, leaf)}, Path('.'), None)
        self.assertEqual(guidenh.breadcrumb(site, root), '[[指南:GuideNH|总览]]')
        self.assertEqual(guidenh.breadcrumb(site, leaf),
                         '[[指南:GuideNH|总览]] › [[指南:GuideNH/gt/index|首页]] › [[指南:GuideNH/gt/machines|机器]]')

    def test_page_name_prefers_the_heading_over_the_sidebar_title(self):
        long = guidenh.Page('ae', 'cpu', Path('c.md'), {'navigation': {'title': '合成CPU多方块结构（很长的侧栏标题）'}}, '\n# 合成CPU\n正文')
        self.assertEqual(long.title_text, '合成CPU')
        self.assertEqual(long.sidebar_title, '合成CPU多方块结构（很长的侧栏标题）')
        bare = guidenh.Page('ae', 'x', Path('x.md'), {'navigation': {'title': '侧栏名'}}, '只有正文')
        self.assertEqual(bare.title_text, '侧栏名')
        parent = guidenh.Page('ae', 'index', Path('index.md'), {'navigation': {'title': '首页'}}, '# 应用能源2\n')
        child = guidenh.Page('ae', 'cpu', Path('c.md'), {'navigation': {'title': '长长长', 'parent': 'index.md'}}, '# 合成CPU\n')
        site = guidenh.Site(Path('.'), {p.key: p for p in (parent, child)}, Path('.'), None)
        self.assertEqual(guidenh.breadcrumb(site, child), '[[指南:GuideNH|总览]] › [[指南:GuideNH/ae/index|应用能源2]]')

    def test_landing_page_is_titled_by_the_prefix_and_has_navigation(self):
        root = guidenh.Page('gt', 'index', Path('index.md'), {'navigation': {'title': '首页'}}, '')
        site = guidenh.Site(Path('.'), {root.key: root}, Path('.'), None)
        text = guidenh.landing_page(site, '4988a65')
        self.assertIn('[[GuideNH]]', text)
        self.assertIn('* [[指南:GuideNH/gt/index|首页]]（gt）', text)
        self.assertIn('== 导航 ==\n{{GuideNH异步导航}}', text)
        self.assertIn('[[分类:GuideNH]]', text)
        self.assertTrue(text.startswith('{{DISPLAYTITLE:GuideNH - 总览}}'))


class ScopeTest(unittest.TestCase):
    def test_landing_page_is_the_prefix_itself(self):
        self.assertEqual(scope.landing_title(), '指南:GuideNH')
        self.assertTrue(scope.is_guidenh_title('指南:GuideNH'))
        self.assertTrue(scope.is_guidenh_title('指南:GuideNH'.replace(':', ':', 1)))
        self.assertFalse(scope.is_guidenh_title('指南:GuideNH教程'))  # 只认前缀本身和前缀加斜杠
        self.assertEqual(scope.title_to_relpath('指南:GuideNH'), '指南/GuideNH.wikitext')
        self.assertEqual(wiki_inventory.path_to_title('指南/GuideNH.wikitext'), '指南:GuideNH')

    def test_titles_and_paths(self):
        self.assertEqual(scope.title_prefix(), '指南:GuideNH/')
        self.assertTrue(scope.is_guidenh_title('指南:GuideNH/gregtech/index'))
        self.assertTrue(scope.is_guidenh_title('指南:GuideNH/gregtech/a b'.replace(' ', '_')))
        self.assertFalse(scope.is_guidenh_title('指南:养蜂发展路线'))
        self.assertFalse(scope.is_guidenh_title('GuideNH/x'))
        self.assertEqual(scope.title_to_relpath('指南:GuideNH/a/b'), '指南/GuideNH/a/b.wikitext')
        self.assertEqual(wiki_inventory.path_to_title('指南/GuideNH/a/b.wikitext'), '指南:GuideNH/a/b')
        self.assertEqual(wiki_inventory.path_to_title('Data/Versions/GTNH/1/Items/0.json'),
                         'Data:Versions/GTNH/1/Items/0.json')

    def test_navbox_module_titles_belong_to_the_guide_scope(self):
        self.assertTrue(scope.is_guidenh_title('Module:Navbox/GuideNH'))
        self.assertTrue(scope.is_guidenh_title('模块:Navbox/GuideNH'))  # 线上列出的中文命名空间名
        self.assertTrue(scope.is_guidenh_title('Module:Navbox/GuideNH/gregtech'))  # 旧版按内容包拆分的模块，要清理
        self.assertFalse(scope.is_guidenh_title('Module:Navbox/GuideNH2'))
        self.assertFalse(scope.is_guidenh_title('Module:Navbox/AE2'))
        self.assertEqual(scope.title_to_relpath('Module:Navbox/GuideNH'), 'Module/Navbox/GuideNH.lua')
        self.assertEqual(wiki_inventory.path_to_title('Module/Navbox/GuideNH.lua'), 'Module:Navbox/GuideNH')

    def test_custom_namespace_and_prefix(self):
        with patch.dict(os.environ, {'GUIDENH_NAMESPACE': '教程', 'GUIDENH_PAGE_PREFIX': 'Pack/Guide'}):
            self.assertEqual(scope.title_prefix(), '教程:Pack/Guide/')
        with patch.dict(os.environ, {'GUIDENH_PAGE_PREFIX': '../bad'}):
            with self.assertRaises(ValueError):
                scope.page_prefix()


GUIDE_PREFIX = '指南:GuideNH/'
DATA_PREFIX = 'Data:Versions/GTNH/9.9/'


class InventoryTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.wikitext = Path(tmp.name) / 'wikitext'
        self.db = Path(tmp.name) / 'tasks.db'
        self.enterContext(patch.dict(os.environ, {'MOD_LOCAL_NAME': 'GTNH'}))
        self.enterContext(patch.multiple(wiki_inventory, WIKITEXT_DIR=str(self.wikitext), DB_PATH=str(self.db),
                                         WIKI_PREFIX=DATA_PREFIX))
        wiki_inventory.init_db(True)
        self.write('Data/Versions/GTNH/9.9/Quests/0.json', '{}')

    def write(self, relative, text):
        path = self.wikitext / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, 'utf-8')

    def rebuild(self, online=(), delete_dirs=frozenset()):
        with patch.object(wiki_inventory, 'login'), \
                patch.object(wiki_inventory, 'fetch_wiki_titles', return_value=set(online)):
            wiki_inventory.rebuild_tasks(True, delete_dirs)
        with closing(sqlite3.connect(self.db)) as conn:
            return dict(conn.execute('SELECT title, status FROM tasks'))

    def test_guide_pages_are_scanned_with_their_titles(self):
        self.write('指南/GuideNH/gregtech/index.wikitext', 'x')
        tasks = self.rebuild()
        self.assertEqual(tasks[GUIDE_PREFIX + 'gregtech/index'], 'pending_upload')

    def test_stale_guide_pages_are_deleted_only_inside_the_prefix(self):
        self.write('指南/GuideNH/gregtech/index.wikitext', 'x')
        tasks = self.rebuild({GUIDE_PREFIX + 'gregtech/old', '指南:GuideNH/gregtech/index'})
        self.assertEqual(tasks[GUIDE_PREFIX + 'gregtech/old'], 'pending_delete')
        self.assertEqual(tasks[GUIDE_PREFIX + 'gregtech/index'], 'pending_upload')

    def test_missing_local_directory_keeps_online_pages(self):
        tasks = self.rebuild({GUIDE_PREFIX + 'gregtech/index'})
        self.assertEqual(tasks[GUIDE_PREFIX + 'gregtech/index'], 'kept_online')
        tasks = self.rebuild({GUIDE_PREFIX + 'gregtech/index'}, {'GuideNH'})
        self.assertEqual(tasks[GUIDE_PREFIX + 'gregtech/index'], 'pending_delete')

    def test_landing_page_is_scanned_and_the_old_overview_subpage_is_deleted(self):
        self.write('指南/GuideNH.wikitext', '总览')
        self.write('指南/GuideNH/gregtech/index.wikitext', 'x')
        tasks = self.rebuild({'指南:GuideNH', '指南:GuideNH/总览', '指南:GuideNH教程'})
        self.assertEqual(tasks['指南:GuideNH'], 'pending_upload')
        self.assertEqual(tasks['指南:GuideNH/总览'], 'pending_delete')  # 旧的总览子页面
        self.assertEqual(tasks['指南:GuideNH教程'], 'kept_online')  # 不在前缀范围内，不碰，更不会删

    def test_navbox_module_is_scanned_and_old_per_pack_modules_are_deleted(self):
        self.write('Module/Navbox/GuideNH.lua', 'return {}')
        tasks = self.rebuild({'Module:Navbox/GuideNH', 'Module:Navbox/GuideNH/gregtech'})
        self.assertEqual(tasks['Module:Navbox/GuideNH'], 'pending_upload')
        self.assertEqual(tasks['Module:Navbox/GuideNH/gregtech'], 'pending_delete')

    def test_addon_runs_ignore_guide_pages(self):
        self.write('指南/GuideNH/gregtech/index.wikitext', 'x')
        with patch.dict(os.environ, {'MOD_LOCAL_NAME': 'sciencenotleisure'}):
            tasks = self.rebuild()
        self.assertNotIn(GUIDE_PREFIX + 'gregtech/index', tasks)


class UploadSelectionTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {'MOD_LOCAL_NAME': 'GTNH'}))
        self.enterContext(patch.object(data_upload, 'WIKI_PREFIX', DATA_PREFIX))

    def test_only_selects_guide_pages_by_name(self):
        title = GUIDE_PREFIX + 'a/b'
        self.assertTrue(data_upload.selected(title, frozenset()))
        self.assertTrue(data_upload.selected(title, frozenset({'guidenh'})))
        self.assertFalse(data_upload.selected(title, frozenset({'Quests'})))
        self.assertFalse(data_upload.selected(DATA_PREFIX + 'Quests/0.json', frozenset({'GuideNH'})))
        self.assertTrue(data_upload.selected(DATA_PREFIX + 'Quests/0.json', frozenset({'Quests'})))

    def test_guide_pages_are_data_but_not_config(self):
        self.assertTrue(data_upload.is_data_title(GUIDE_PREFIX + 'a'))
        self.assertFalse(data_upload.is_data_title('Module:Database/Versions/GTNH/9.9'))


class ImageKindTest(unittest.TestCase):
    def test_plan_requires_task_output_and_has_no_redirects(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / 'images').mkdir()
            with patch.object(scope, 'state_directory', return_value=state):
                with self.assertRaises(ValueError):
                    image_kinds.kind_plan('guidenh', str(state), None)
                (state / 'images' / 'GuideNH_a.png').write_bytes(b'x')
                (state / 'image-plan.json').write_text(json.dumps({'uploadIcons': ['GuideNH_a.png']}), 'utf-8')
                plan = image_kinds.kind_plan('guidenh', str(state), None)
                self.assertEqual(plan, {'schemaVersion': 1, 'uploadIcons': ['GuideNH_a.png'], 'redirects': {}})
                (state / 'images' / 'GuideNH_a.png').unlink()
                with self.assertRaises(ValueError):
                    image_kinds.kind_plan('guidenh', str(state), None)
            self.assertEqual(image_kinds.kind_exempt('guidenh', str(state)), {})
            self.assertEqual(image_kinds.kind_unstable('guidenh'), set())
            self.assertIn('guidenh', image_kinds.KINDS)


class SafetyTest(unittest.TestCase):
    """删除是自动的（本地不再生成 → 清单待删除 → 上传成功后删除），但转换出错或仓库异常时不能误删。"""

    def test_failed_pages_keep_their_local_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / '指南' / 'GuideNH'
            kept = directory / 'a' / 'broken.wikitext'
            gone = directory / 'a' / 'removed.wikitext'
            kept.parent.mkdir(parents=True)
            kept.write_text('旧内容', 'utf-8')
            gone.write_text('旧内容', 'utf-8')
            removed = guidenh.write_pages(directory, {GUIDE_PREFIX + 'a/ok': '新'},
                                          protected=[GUIDE_PREFIX + 'a/broken'])
            self.assertEqual(removed, 1)
            self.assertTrue(kept.exists())
            self.assertFalse(gone.exists())

    def test_empty_content_pack_aborts_without_touching_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'repo'
            (repo / 'assets').mkdir(parents=True)
            wikitext = Path(tmp) / 'wikitext'
            existing = wikitext / '指南' / 'GuideNH' / 'a.wikitext'
            existing.parent.mkdir(parents=True)
            existing.write_text('x', 'utf-8')
            with patch.dict(os.environ, {'MOD_LOCAL_NAME': 'GTNH', 'GUIDENH_DIR': str(repo)}):
                with self.assertRaises(ValueError):
                    guidenh.run(tmp, wikitext)
            self.assertTrue(existing.exists())

    def test_page_removed_upstream_is_deleted_online_after_uploads(self):
        # 端到端：本地文件消失 → 清单 pending_delete → data_upload 在上传成功后删除
        with tempfile.TemporaryDirectory() as tmp:
            wikitext = Path(tmp) / 'wikitext'
            db = Path(tmp) / 'tasks.db'
            page = wikitext / '指南' / 'GuideNH' / 'gregtech' / 'index.wikitext'
            page.parent.mkdir(parents=True)
            page.write_text('x', 'utf-8')
            with patch.dict(os.environ, {'MOD_LOCAL_NAME': 'GTNH'}), \
                    patch.multiple(wiki_inventory, WIKITEXT_DIR=str(wikitext), DB_PATH=str(db), WIKI_PREFIX=DATA_PREFIX), \
                    patch.multiple(data_upload, WIKITEXT_DIR=str(wikitext), TASK_DB=str(db), WIKI_PREFIX=DATA_PREFIX):
                wiki_inventory.init_db(True)
                online = {GUIDE_PREFIX + 'gregtech/index', GUIDE_PREFIX + 'gregtech/removed_later'}
                with patch.object(wiki_inventory, 'login'), \
                        patch.object(wiki_inventory, 'fetch_wiki_titles', return_value=online):
                    wiki_inventory.rebuild_tasks(True)
                import http.cookiejar
                with patch.object(data_upload, 'login_once', return_value=http.cookiejar.CookieJar()), \
                        patch.object(data_upload.signal, 'signal'), \
                        patch.object(data_upload, 'edit_page', return_value={}), \
                        patch.object(data_upload, 'delete_page', return_value={}) as delete:
                    data_upload.main(['--only', 'GuideNH'])
                self.assertEqual([call.args[1] for call in delete.call_args_list], [GUIDE_PREFIX + 'gregtech/removed_later'])


class ItemPageLinkTest(unittest.TestCase):
    """物品记录的 guidenhPages：页面后缀列表，前缀由维基端拼接。"""

    def index(self):
        return guidenh.ItemPageIndex([
            ('gregtech:gt.blockmachines:1172', 'gregtech/multiblocks/hv/cleanroom'),
            ('mod:plain', 'mod/plain_page'),
            ('mod:zero:0', 'mod/zero_page'),
            ('mod:tool:0:{energy:1d}', 'mod/tool_page'),
            ('mod:wild:*', 'mod/wild_page'),
            ('MOD:Plain', 'mod/second_page'),
        ])

    def test_matching_rules(self):
        index = self.index()
        self.assertEqual(index.pages_for('gregtech:gt.blockmachines:1172'), ['gregtech/multiblocks/hv/cleanroom'])
        self.assertEqual(index.pages_for('mod:plain'), ['mod/plain_page', 'mod/second_page'])  # 大小写不敏感，去重保序
        self.assertEqual(index.pages_for('mod:zero'), ['mod/zero_page'])  # :0 等同省略 meta
        self.assertEqual(index.pages_for('mod:tool'), ['mod/tool_page'])  # NBT 写法忽略 NBT
        self.assertEqual(index.pages_for('mod:wild'), ['mod/wild_page'])  # meta 通配
        self.assertEqual(index.pages_for('mod:wild:7'), ['mod/wild_page'])
        self.assertEqual(index.pages_for('gregtech:gt.blockmachines:1173'), [])

    def test_attach_sets_and_clears_the_field(self):
        items = [{'key': 'mod:plain_AAA', 'rawKey': 'mod:plain'}, {'key': 'mod:other'},
                 {'key': 'mod:zero', 'guidenhPages': ['旧']}]
        self.assertEqual(guidenh.attach_guide_pages(items, self.index()), 2)
        self.assertEqual(items[0]['guidenhPages'], ['mod/plain_page', 'mod/second_page'])
        self.assertNotIn('guidenhPages', items[1])
        self.assertEqual(items[2]['guidenhPages'], ['mod/zero_page'])  # 以 key 匹配（没有 rawKey）

    def test_empty_index_is_falsey_and_removes_stale_fields(self):
        empty = guidenh.ItemPageIndex([])
        self.assertFalse(empty)
        items = [{'key': 'a', 'guidenhPages': ['x']}]
        self.assertEqual(guidenh.attach_guide_pages(items, empty), 0)
        self.assertNotIn('guidenhPages', items[0])

    def test_real_pages_declare_items_through_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            directory = repo / 'assets' / 'mod' / 'guidenh' / '_zh_cn' / 'a'
            directory.mkdir(parents=True)
            (directory / 'one.md').write_text('---\nitem_ids:\n  - mod:x:3\nnavigation:\n  title: 一\n---\n# 一\n', 'utf-8')
            (directory / 'two.md').write_text('---\nitem_id: mod:x:3\nnavigation:\n  title: 二\n  position: 5\n---\n# 二\n', 'utf-8')
            with patch.dict(os.environ, {'MOD_LOCAL_NAME': 'GTNH'}):
                index = guidenh.item_page_index(repo)
            self.assertEqual(index.pages_for('mod:x:3'), ['mod/a/two', 'mod/a/one'])  # position 大的在前

    def test_not_configured_gives_an_empty_index(self):
        with patch.dict(os.environ, {'MOD_LOCAL_NAME': 'sciencenotleisure'}):
            self.assertFalse(guidenh.item_page_index())


class RelatedItemsBlockTest(unittest.TestCase):
    """关联物品不单独成章节：紧跟在页首声明框后面，用 <blockquote> 和 {{Columns}}，不写内联样式。"""

    def page(self, meta):
        return guidenh.Page('mod', 'a/b', Path('x.md'), meta, '')

    def test_items_go_to_the_related_items_template(self):
        context = make_context(wrapped=True)
        block = guidenh.related_items_block(context, self.page(
            {'item_id': 'mod:block', 'item_ids': ['mod:block:3', 'mod:block', 'mod:missing']}))
        # 交给 {{GuideNH关联物品}}：版式（blockquote、Columns、<nowiki/> 等）都在模板里，页面只传物品行
        self.assertEqual(block, '{{GuideNH关联物品|1=\n* <span class="db-item">@mod:block</span>\n'
                                '* <span class="db-item">@mod:block:3</span>\n}}')  # 去重，找不到的跳过
        self.assertNotIn('style=', block)  # 不自己写内联样式
        self.assertEqual(context.report['unresolvedItems'], ['mod:missing'])
        self.assertNotIn('|', strip_protected(block))

    def test_pages_without_items_get_no_block(self):
        self.assertEqual(guidenh.related_items_block(make_context(), self.page({})), '')
        self.assertEqual(guidenh.related_items_block(make_context(), self.page({'item_ids': ['mod:missing']})), '')

    def test_unwrapped_pages_use_item_templates(self):
        block = guidenh.related_items_block(make_context(wrapped=False), self.page({'item_ids': ['mod:block']}))
        self.assertIn('* {{Item|@mod:block}}', block)


class IconListTest(unittest.TestCase):
    """连续的单独图标不直接摆出来，用 IconList 排成一组。"""

    def test_icon_only_paragraph_becomes_icon_list(self):
        text = body('<ItemImage id="mod:block" />\n<ItemImage id="mod:block:3" scale="2" /> <BlockImage id="mod:block" meta="3" />',
                    wrapped=True)
        self.assertEqual(text, '<span class="db-icon-list">@mod:block,@mod:block:3,@mod:block:3</span>')
        unwrapped = body('<ItemImage id="mod:block" /> <ItemImage id="mod:block:3" />')
        self.assertEqual(unwrapped, '{{IconList|@mod:block,@mod:block:3}}')

    def test_text_labels_and_mixed_content_are_not_icon_lists(self):
        self.assertIn('db-icon', body('说明 <ItemImage id="mod:block" />', wrapped=True))
        self.assertNotIn('db-icon-list', body('说明 <ItemImage id="mod:block" />', wrapped=True))
        self.assertNotIn('IconList', body('<ItemImage id="mod:block" label="right" />'))
        self.assertNotIn('IconList', body('<ItemImage id="mod:missing" />'))  # 解析不了就回到普通写法

    def test_row_with_only_icons_drops_the_flex_wrapper(self):
        text = body('<Row gap="20">\n<ItemImage id="mod:block" />\n<ItemImage id="mod:block:3" />\n</Row>', wrapped=True)
        self.assertEqual(text, '<span class="db-icon-list">@mod:block,@mod:block:3</span>')
        # 内容包里 Row 的每个图标常常各占一段：必须合并成同一个 IconList 的连续参数，而不是一个图标一个框
        separate = body('<Column>\n<Row>\n<ItemImage id="mod:block" />\n\n<ItemImage id="mod:block:3" />\n\n'
                        '<ItemImage id="mod:block" />\n</Row>\n</Column>', wrapped=True)
        self.assertEqual(separate.count('db-icon-list'), 1)  # 只有一组
        self.assertIn('@mod:block,@mod:block:3,@mod:block', separate)
        self.assertEqual(body('<Row>\n<ItemImage id="mod:block" />\n\n<ItemImage id="mod:block:3" />\n</Row>'),
                         '{{IconList|@mod:block,@mod:block:3}}')
        mixed = body('<Row gap="20">\n文字\n\n<ItemImage id="mod:block" />\n</Row>', wrapped=True)
        self.assertIn('display:flex', mixed)


class NavboxModuleTest(unittest.TestCase):
    def site(self):
        def page(mod, page_id, title, parent=None, position=0):
            navigation = {'title': title, 'position': position}
            if parent:
                navigation['parent'] = parent
            return guidenh.Page(mod, page_id, Path(page_id + '.md'), {'navigation': navigation}, '')
        pages = [page('gt', 'index', '首页', position=3), page('gt', 'machines', '机器', 'index.md', 5),
                 page('gt', 'power', '电力', 'index.md', 1), page('gt', 'machines/ebf', '高炉 "EBF"', '../machines.md', 9),
                 page('gt', 'machines/lathe', '车床', '../machines.md'), page('gt', 'tips', '技巧'),
                 page('solo', 'index', '单页', position=9)]
        return guidenh.Site(Path('.'), {p.key: p for p in pages}, Path('.'), None)

    def test_one_navbox_for_the_whole_guide_with_a_group_per_pack(self):
        modules = guidenh.navbox_modules(self.site())
        self.assertEqual(list(modules), ['Module:Navbox/GuideNH'])
        text = modules['Module:Navbox/GuideNH']
        self.assertIn('title = "[[指南:GuideNH|GuideNH]]"', text)
        # 内容包按首页的 position 排序：单页（9）在 GT（3）之前；只有一页的内容包就是一个单项
        self.assertLess(text.index('name = "单页"'), text.index('groupName = "首页"'))
        self.assertIn('{name = "单页", link = "指南:GuideNH/solo/index", icon = false}', text)
        self.assertIn('groupName = "首页"', text)
        self.assertIn('{name = "总览", link = "指南:GuideNH/gt/index", icon = false}', text)
        # 子树按导航树分组，position 大的在前；没有上级的其他根页面（技巧）归入内容包分组
        self.assertIn('groupName = "机器"', text)
        self.assertIn('{name = "高炉 \\"EBF\\"", link = "指南:GuideNH/gt/machines/ebf", icon = false}', text)
        self.assertLess(text.index('groupName = "机器"'), text.index('name = "电力"'))
        self.assertLess(text.index('name = "高炉'), text.index('name = "车床"'))
        self.assertIn('{name = "技巧", link = "指南:GuideNH/gt/tips", icon = false}', text)
        self.assertNotIn('style', text)

    def test_navbox_uses_item_icons_of_the_pages(self):
        items = guidenh.ItemIndex([('mod:wrench', 'mod:wrench', None), ('mod:anim', 'mod:anim', None)],
                                  {'mod:wrench': ('icon_mod_wrench.png', 1), 'mod:anim': ('icon_mod_anim.png', 8)})
        def page(page_id, title, icon, parent=None):
            navigation = {'title': title, 'icon': icon}
            if parent:
                navigation['parent'] = parent
            return guidenh.Page('gt', page_id, Path(page_id + '.md'), {'navigation': navigation}, '')
        pages = [page('index', '首页', None), page('a', '甲', 'mod:wrench', 'index.md'), page('b', '乙', '<mod:anim>', 'index.md'),
                 page('c', '丙', 'mod:missing', 'index.md'), page('d', '丁', 'mod:wrench 0', 'index.md')]
        site = guidenh.Site(Path('.'), {p.key: p for p in pages}, Path('.'), None)
        text = guidenh.navbox_modules(site, items)['Module:Navbox/GuideNH']
        self.assertIn('{name = "甲", link = "指南:GuideNH/gt/a", icon = "icon_mod_wrench"}', text)
        self.assertIn('{name = "乙", link = "指南:GuideNH/gt/b", icon = false}', text)  # 动画图标不用
        self.assertIn('{name = "丙", link = "指南:GuideNH/gt/c", icon = false}', text)  # 解析不到
        self.assertIn('{name = "丁", link = "指南:GuideNH/gt/d", icon = "icon_mod_wrench"}', text)
        self.assertEqual(guidenh.clean_icon_id('<mod:x:3>'), 'mod:x:3')
        self.assertEqual(guidenh.clean_icon_id('minecraft:potion 16384'), 'minecraft:potion:16384')
        self.assertIsNone(guidenh.clean_icon_id(None))

    def test_order_follows_position_descending_then_sidebar_title(self):
        low = guidenh.Page('gt', 'a', Path('a.md'), {'navigation': {'title': '乙', 'position': 1}}, '# 子')
        high = guidenh.Page('gt', 'b', Path('b.md'), {'navigation': {'title': '甲', 'position': 5}}, '# 孙')
        tie = guidenh.Page('gt', 'c', Path('c.md'), {'navigation': {'title': '丙', 'position': 1}}, '# 啊')
        self.assertEqual([p.id for p in sorted([low, high, tie], key=guidenh.page_order)], ['b', 'c', 'a'])

    def test_navbox_module_is_one_file_and_old_per_pack_modules_are_cleaned(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / 'Module' / 'Navbox' / 'GuideNH' / 'gregtech.lua'
            legacy.parent.mkdir(parents=True)
            legacy.write_text('旧', 'utf-8')
            with patch.dict(os.environ, {'MOD_LOCAL_NAME': 'GTNH'}):
                removed = guidenh.write_navbox_module(tmp, 'return {}')
            self.assertEqual(removed, 1)
            self.assertEqual((Path(tmp) / 'Module' / 'Navbox' / 'GuideNH.lua').read_text('utf-8'), 'return {}')
            self.assertFalse(legacy.parent.exists())


class WritePagesTest(unittest.TestCase):
    def test_pages_are_written_and_stale_ones_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / '指南' / 'GuideNH'
            stale = directory / 'old' / 'gone.wikitext'
            stale.parent.mkdir(parents=True)
            stale.write_text('x', 'utf-8')
            removed = guidenh.write_pages(directory, {GUIDE_PREFIX + 'gregtech/index': '页面'})
            self.assertEqual(removed, 1)
            self.assertEqual((directory / 'gregtech' / 'index.wikitext').read_text('utf-8'), '页面')
            self.assertFalse((directory / 'old').exists())
            guidenh.write_pages(directory, {}, prune=False)
            self.assertTrue((directory / 'gregtech' / 'index.wikitext').exists())


if __name__ == '__main__':
    unittest.main()
