import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import image_check
from image_state import set_uploaded, update_state


def png(color, size=256, edge=None):
    image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    for x in range(32, size - 32):
        for y in range(32, size - 32):
            image.putpixel((x, y), color)
    if edge is not None:
        # 模拟不同显卡的光栅化差异：边缘一列像素整列翻转
        for y in range(32, size - 32):
            image.putpixel((size - 33, y), edge)
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    return buffer.getvalue()


class VisualRuleTest(unittest.TestCase):
    def test_edge_rasterization_counts_as_same(self):
        self.assertTrue(image_check.visually_same(png((200, 50, 50, 255)), png((200, 50, 50, 255), edge=(0, 0, 0, 0))))

    def test_colour_change_is_different(self):
        self.assertFalse(image_check.visually_same(png((200, 50, 50, 255)), png((50, 200, 50, 255))))

    def test_size_change_is_different(self):
        self.assertFalse(image_check.visually_same(png((1, 2, 3, 255)), png((1, 2, 3, 255), size=128)))

    def test_transparent_colour_is_ignored(self):
        a = Image.new('RGBA', (16, 16), (255, 0, 0, 0))
        b = Image.new('RGBA', (16, 16), (0, 0, 255, 0))
        data = []
        for image in (a, b):
            buffer = io.BytesIO()
            image.save(buffer, 'PNG')
            data.append(buffer.getvalue())
        self.assertEqual(image_check.visual_distance(*data), (0, 0))


class StateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / 'state.db')

    def tearDown(self):
        self.tmp.cleanup()

    def states(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return dict(conn.execute('SELECT icon_name, icon_state FROM images'))

    def test_visual_check_and_exemption_settle_icons(self):
        icons = {'a.png', 'b.png', 'c.png', 'd.png'}
        update_state(self.db, icons, {}, {'a.png': 'A2', 'b.png': 'B2', 'c.png': 'C2', 'd.png': 'D2'})
        with closing(sqlite3.connect(self.db)) as conn, conn:
            for name, sha1 in (('a.png', 'A1'), ('b.png', 'B1'), ('c.png', 'C1'), ('d.png', 'D2')):
                set_uploaded(conn, name, sha1)
            conn.execute("INSERT INTO visual_checks VALUES ('A1', 'A2', 1)")
            conn.execute("INSERT INTO visual_checks VALUES ('C1', 'C2', 0)")
        update_state(self.db, icons, {}, {'a.png': 'A2', 'b.png': 'B2', 'c.png': 'C2', 'd.png': 'D2'}, {'b.png'})
        self.assertEqual(self.states(), {'a.png': '已上传', 'b.png': '已上传', 'c.png': '待更新', 'd.png': '已上传'})
        # 本地再次变化后，旧判定不再适用
        update_state(self.db, icons, {}, {'a.png': 'A3', 'b.png': 'B2', 'c.png': 'C2', 'd.png': 'D2'}, {'b.png'})
        self.assertEqual(self.states()['a.png'], '待更新')

    def test_exemption_needs_an_online_file(self):
        update_state(self.db, {'new.png'}, {}, {'new.png': 'N1'}, {'new.png'})
        self.assertEqual(self.states(), {'new.png': '待更新'})

    def test_check_pending_records_verdicts_and_reuses_them(self):
        icons_dir = Path(self.tmp.name) / 'icons'
        icons_dir.mkdir()
        local = {'same.png': png((9, 9, 9, 255)), 'moved.png': png((9, 9, 9, 255)), 'new.png': png((1, 1, 1, 255))}
        for name, data in local.items():
            (icons_dir / name).write_bytes(data)
        import hashlib
        hashes = {name: hashlib.sha1(data).hexdigest() for name, data in local.items()}
        update_state(self.db, set(local), {}, hashes)
        remote = {'same.png': png((9, 9, 9, 255), edge=(0, 0, 0, 0)), 'moved.png': png((90, 9, 9, 255))}
        files = {name: {'sha1': hashlib.sha1(data).hexdigest(), 'url': name} for name, data in remote.items()}
        with patch('image_check.wiki_files', return_value=files) as query, \
                patch('image_check.download', side_effect=lambda url, sha1, cache: remote[url]):
            stats = image_check.check_pending(self.db, icons_dir, set(local), {}, Path(self.tmp.name), threads=2)
            self.assertEqual((stats['visualSame'], stats['changed'], stats['notOnWiki']), (1, 1, 1))
            # 判定立即生效，不必等下一次 update_state
            self.assertEqual(self.states(), {'same.png': '已上传', 'moved.png': '待更新', 'new.png': '待更新'})
            update_state(self.db, set(local), {}, hashes)
            self.assertEqual(self.states(), {'same.png': '已上传', 'moved.png': '待更新', 'new.png': '待更新'})
            image_check.check_pending(self.db, icons_dir, set(local), {}, Path(self.tmp.name), threads=2)
            # 已判定为变化的不再查询，只剩线上不存在的
            self.assertEqual(sorted(query.call_args.args[0]), ['new.png'])


class ExemptOnCheckTest(unittest.TestCase):
    setUp, tearDown, states = StateTest.setUp, StateTest.tearDown, StateTest.states

    def test_exempt_icon_settles_when_its_online_file_is_found(self):
        icons_dir = Path(self.tmp.name)
        (icons_dir / 'anim.png').write_bytes(png((1, 1, 1, 255)))
        update_state(self.db, {'anim.png'}, {}, {'anim.png': 'L1'}, {'anim.png'})
        with patch('image_check.wiki_files', return_value={'anim.png': {'sha1': 'W1', 'url': 'u'}}),                 patch('image_check.download', side_effect=AssertionError('exempt icons are not compared')):
            stats = image_check.check_pending(self.db, icons_dir, {'anim.png'}, {'anim.png': 'animated'}, icons_dir)
        self.assertEqual(stats['exempt'], 1)
        self.assertEqual(self.states(), {'anim.png': '已上传'})


class HashCacheTest(unittest.TestCase):
    def test_unchanged_files_come_from_cache(self):
        from image_state import icon_hashes
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'a.png').write_bytes(b'one')
            cache = root / 'cache.json'
            first = icon_hashes(root, {'a.png'}, cache)
            with patch('hashlib.sha1', side_effect=AssertionError('file was read again')):
                self.assertEqual(icon_hashes(root, {'a.png'}, cache), first)
            (root / 'a.png').write_bytes(b'changed')
            import os
            os.utime(root / 'a.png', ns=(1, 1))
            self.assertNotEqual(icon_hashes(root, {'a.png'}, cache), first)


class DumperHashPlanTest(unittest.TestCase):
    def test_plan_groups_by_manifest_without_decoding(self):
        from image_plan import build_plan
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('a.png', 'a_v.png', 'a_w.png'):
                (root / name).write_bytes(b'not decoded')
            items = [{'key': 'm:a', 'shortKey': 'a', 'icon': 'a.png'},
                     {'key': 'm:a', 'shortKey': 'a_v', 'nbt': '{x:1}', 'icon': 'a_v.png'},
                     {'key': 'm:a', 'shortKey': 'a_w', 'nbt': '{x:2}', 'icon': 'a_w.png'}]
            with patch('image_plan.pixel_hash', side_effect=AssertionError('decoded')):
                plan = build_plan(items, [], root, {}, pixel_hashes={'a.png': 'h1', 'a_v.png': 'h1', 'a_w.png': 'h2'})
            self.assertEqual(plan['keyAliases'], {'a_v.png': 'a.png'})
            self.assertEqual(plan['uploadIcons'], ['a.png', 'a_w.png'])


def strip(colors, size=16):
    """每种颜色一帧的横向帧条。"""
    image = Image.new('RGBA', (size * len(colors), size), (0, 0, 0, 0))
    for index, color in enumerate(colors):
        image.paste(color, (index * size, 0, (index + 1) * size, size))
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    return buffer.getvalue()


class AnimationRuleTest(unittest.TestCase):
    RAMP = [(255, 2 * i, 0, 255) for i in range(128)]

    def test_one_wrong_frame_in_a_long_strip_is_different(self):
        broken = list(self.RAMP)
        broken[42] = (255, 0, 0, 255)
        # 整条缩到 32×32 时单个错帧被周围帧摊薄，逐帧比对才看得出来
        self.assertTrue(image_check.visually_same(strip(broken), strip(self.RAMP)))
        self.assertFalse(image_check.same_picture('a_dynamic.png', strip(broken), strip(self.RAMP)))

    def test_invisible_difference_in_every_frame_is_same(self):
        near = [(255, g + 1, 0, 255) for _, g, _, _ in self.RAMP]
        self.assertTrue(image_check.same_picture('a_dynamic.png', strip(near), strip(self.RAMP)))

    def test_frame_count_or_frame_size_change_is_different(self):
        self.assertFalse(image_check.strips_same(strip(self.RAMP[:64]), strip(self.RAMP)))
        self.assertFalse(image_check.strips_same(strip(self.RAMP, 32), strip(self.RAMP)))


class ReshapedTest(unittest.TestCase):
    setUp, tearDown, states = StateTest.setUp, StateTest.tearDown, StateTest.states

    def test_blacklisted_icon_with_new_dimensions_becomes_pending(self):
        from image_state import reshaped_icons
        icons_dir = Path(self.tmp.name)
        names = {'grown_dynamic.png', 'kept_dynamic.png'}
        for name in names:
            (icons_dir / name).write_bytes(strip([(1, 1, 1, 255)] * 4))
        hashes = {name: 'L-' + name for name in names}
        exempt = {name: 'unstable' for name in names}
        update_state(self.db, names, {}, hashes, exempt)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            for name in names:
                set_uploaded(conn, name, 'W-' + name.split('_')[0], exempt)
        self.assertEqual(set(self.states().values()), {'已上传'})
        files = {'grown_dynamic.png': {'sha1': 'W-grown', 'url': 'g', 'size': (16, 16)},
                 'kept_dynamic.png': {'sha1': 'W-kept', 'url': 'k', 'size': (64, 16)}}
        with patch('image_check.wiki_files', return_value=files), \
                patch('image_check.download', side_effect=AssertionError('blacklisted icons are not compared')):
            stats = image_check.check_pending(self.db, icons_dir, names, exempt, icons_dir)
        self.assertEqual((stats['reshaped'], stats['exempt']), (1, 1))
        expected = {'grown_dynamic.png': '待更新', 'kept_dynamic.png': '已上传'}
        self.assertEqual(self.states(), expected)
        self.assertEqual(reshaped_icons(self.db), {'grown_dynamic.png'})
        # 离线重算状态时结论不丢
        update_state(self.db, names, {}, hashes, exempt)
        self.assertEqual(self.states(), expected)


class StabilityTest(unittest.TestCase):
    def test_unstable_list_only_grows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for run, images in (('a', {'x.png': '1', 'y.png': '2'}), ('b', {'x.png': '1', 'y.png': '3'})):
                (root / run).mkdir()
                (root / run / 'icon-hashes.json').write_text(json.dumps({'images': images}))
            config = root / 'image_unstable.json'
            config.write_text(json.dumps({'icons': ['old.png']}))
            with patch('image_check.UNSTABLE_CONFIG', config):
                result = image_check.record_stability([root / 'a', root / 'b'])
                self.assertEqual(result['added'], 1)
                self.assertEqual(image_check.unstable_icons(), {'old.png', 'y.png'})
                # 修好导出器后用新结果重建名单：只重建本次比较到的文件，其余（如另一类图片）保持原状
                image_check.record_stability([root / 'a', root / 'a'], replace=True)
                self.assertEqual(image_check.unstable_icons(), {'old.png'})

    def test_entity_images_join_the_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for run, hashes in (('a', {'Entity X.png': '1', 'EntityHead X.png': '2'}),
                                ('b', {'Entity X.png': '1', 'EntityHead X.png': '9'})):
                (root / run).mkdir()
                (root / run / 'entity-images.json').write_text(json.dumps({'pixelHashes': hashes}))
            config = root / 'image_unstable.json'
            config.write_text(json.dumps({'icons': ['item.png']}))
            with patch('image_check.UNSTABLE_CONFIG', config):
                result = image_check.record_stability([root / 'a', root / 'b'], replace=True)
                self.assertEqual(result['compared'], 2)
                self.assertEqual(image_check.unstable_icons(), {'item.png', 'EntityHead X.png'})


if __name__ == '__main__':
    unittest.main()
