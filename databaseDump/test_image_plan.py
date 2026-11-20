"""图片去重范围、代表选择、动画完整性与单级重定向回归。"""
import sqlite3
import json
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PIL import Image, PngImagePlugin

from image_plan import build_plan, pixel_hash, prepare_plan
from item_names import build_artifacts
from image_state import update_state, pending_redirects


class ImagePlanTests(unittest.TestCase):
    def test_cached_key_aliases_skip_hashing_and_flatten_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('base.png', 'variant.png'):
                (root / name).write_bytes(b'pixels must not be read')
            cached = {'schemaVersion': 1, 'groups': [
                {'registry': 'mod:item', 'role': 'static', 'sha256': 'existing-hash',
                 'target': 'base.png', 'duplicates': ['variant.png']}]}
            with patch('image_plan.pixel_hash', side_effect=AssertionError('unexpected hash')):
                plan = build_plan([], [], root, {'File:名称.png': 'variant.png'}, cached_plan=cached)
            self.assertEqual(plan['hashedCount'], 0)
            self.assertEqual(plan['deduplicatedCount'], 1)
            self.assertEqual(plan['uploadIcons'], ['base.png'])
            self.assertEqual(plan['redirects'], {'File:Variant.png': 'base.png', 'File:名称.png': 'base.png'})
            self.assertEqual(plan['groups'], cached['groups'])
            (root / 'base.png').unlink()
            with self.assertRaisesRegex(ValueError, '缺少 1 个目标'):
                build_plan([], [], root, {}, cached_plan=plan)

    def test_prepare_recovers_state_key_aliases_and_reuses_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, state = root / 'source', root / 'source_state/Example/1'
            (source / 'icons').mkdir(parents=True)
            state.mkdir(parents=True)
            for name in ('base.png', 'variant.png'):
                (source / 'icons' / name).write_bytes(b'pixels must not be read')
            items = [{'key': 'betterquesting:pattern_placeholder', 'shortKey': 'base', 'icon': 'base.png'},
                     {'key': 'betterquesting:pattern_placeholder', 'shortKey': 'variant', 'icon': 'variant.png'}]
            (source / 'advitempanel.json').write_text(json.dumps({'shortKey': items}), 'utf-8')
            icon_source = source / 'itemicon.json'
            icon_source.write_text(json.dumps({'shortKey': []}), 'utf-8')
            (state / 'image-name-resolution.json').write_text(json.dumps({'sourceIconsPath': str(icon_source)}), 'utf-8')
            (state / 'image-upload-plan.json').write_text(json.dumps({'schemaVersion': 1, 'groups': []}), 'utf-8')
            update_state(state / 'image_inventory.db', {'base.png'},
                         {'File:Variant.png': 'base.png', 'File:旧中文名.png': 'base.png'})
            with patch('image_version.redirect_map', return_value={'File:伪物品.png': 'variant.png'}), \
                    patch('image_plan.pixel_hash', side_effect=AssertionError('unexpected hash')):
                plan = prepare_plan(source, state / 'image_redirect.resolved.csv')
                self.assertEqual(plan['redirects'], {'File:Variant.png': 'base.png'})
                self.assertEqual(plan['keyAliases'], {'variant.png': 'base.png'})
                self.assertEqual(plan['uploadIcons'], ['base.png'])
                self.assertEqual(plan['hashedCount'], 0)
                (state / 'image_inventory.db').unlink()
                self.assertEqual(prepare_plan(source, state / 'image_redirect.resolved.csv'), plan)

    def test_empty_cache_does_not_hash_new_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('a.png', 'b.png'):
                (root / name).write_bytes(b'pixels must not be read')
            items = [{'key': 'mod:item', 'icon': name} for name in ('a.png', 'b.png')]
            with patch('image_plan.pixel_hash', side_effect=AssertionError('unexpected hash')):
                plan = build_plan(items, [], root, {}, cached_plan={'schemaVersion': 1})
            self.assertEqual(plan['uploadIcons'], ['a.png', 'b.png'])
            self.assertEqual(plan['hashedCount'], 0)

    def test_quest_placeholder_key_redirects_survive_stale_name_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = 'icon_betterquesting_pattern_placeholder.png'
            variant = 'icon_betterquesting_pattern_placeholder_hash.png'
            for icon in (base, variant, 'book.png'):
                Image.new('RGBA', (1, 1), 'red').save(root / icon)
            items = [
                {'key': 'betterquesting:pattern_placeholder', 'shortKey': 'base', 'icon': base},
                {'key': 'betterquesting:pattern_placeholder', 'shortKey': 'variant',
                 'nbt': '{x:1}', 'icon': variant},
                {'key': 'betterquesting:guide_book', 'shortKey': 'book', 'icon': 'book.png'},
            ]
            plan = build_plan(items, [], root, {'File:伪铁锭.png': variant,
                                                'File:占位符.png': base,
                                                'File:任务指南.png': 'book.png'})
            self.assertEqual(plan['redirects'], {
                'File:Icon betterquesting pattern placeholder hash.png': base,
                'File:任务指南.png': 'book.png'})
            self.assertEqual(plan['uploadIcons'], sorted([base, 'book.png']))

    def test_missing_redirect_target_reports_mapping_and_source(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError) as error:
                build_plan([], [], directory, {'File:编程电路1.png': 'missing.png'})
            message = str(error.exception)
            self.assertIn('File:编程电路1.png -> missing.png', message)
            self.assertIn(directory, message)
            self.assertIn('split_all.py items', message)

    def test_filtered_source_uses_validated_fallback_icons(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'filtered/Example'
            (source / 'icons').mkdir(parents=True)
            items = [{'key': 'example:item', 'shortKey': 'example:item',
                      'translatedName': '示例', 'iconName': 'icon.png'}]
            (source / 'advitempanel.json').write_text(json.dumps({'shortKey': items}), 'utf-8')
            fallback = root / 'itemicon.json'
            fallback.write_text(json.dumps({'shortKey': [
                {'shortKey': 'example:item', 'iconName': 'icon.png'}]}), 'utf-8')
            Image.new('RGBA', (1, 1), 'red').save(source / 'icons/icon.png')
            build_artifacts(items, root / 'version', root / 'state', source,
                            root / 'choices.json', icon_source=fallback)
            resolved = root / 'state/image_redirect.resolved.csv'
            with patch.dict(os.environ, {'DUMPS_DIR': str(root), 'MOD_LOCAL_NAME': 'Example',
                                         'IMAGE_NAME_CONFIG': str(root / 'choices.json')}):
                self.assertEqual(prepare_plan(source, resolved)['uploadIcons'], ['icon.png'])
                fallback.write_text('{"shortKey": []}', 'utf-8')
                with self.assertRaisesRegex(ValueError, '图片映射数据已修改'):
                    prepare_plan(source, resolved)

    def test_apng_compares_decoded_frames_timing_and_loop(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = Image.new('RGBA', (2, 2), 'red')
            second = Image.new('RGBA', (2, 2), 'blue')
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text('Description', 'different encoding')

            def save(name, frame=second, **options):
                settings = dict(duration=[100, 200], loop=0, compress_level=0)
                settings.update(options)
                first.save(root / name, save_all=True, append_images=[frame], **settings)

            save('a.png')
            save('b.png', compress_level=9, pnginfo=metadata)
            self.assertNotEqual((root / 'a.png').read_bytes(), (root / 'b.png').read_bytes())
            self.assertEqual(pixel_hash(root / 'a.png'), pixel_hash(root / 'b.png'))
            save('different.png', frame=Image.new('RGBA', (2, 2), 'green'))
            save('timing.png', duration=[100, 300])
            save('loop.png', loop=1)
            for name in ('different.png', 'timing.png', 'loop.png'):
                self.assertNotEqual(pixel_hash(root / 'a.png'), pixel_hash(root / name))

    def test_scope_priority_and_flattened_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('base', 'damage', 'nbt', 'other'):
                Image.new('RGBA', (2, 2), 'red').save(root / (name + '.png'))
            Image.new('RGBA', (2, 2), 'blue').save(root / 'different.png')
            items = [
                {'key': 'mod:item:2', 'shortKey': 'damage', 'icon': 'damage.png'},
                {'key': 'mod:item', 'shortKey': 'nbt', 'nbt': '{x:1}', 'icon': 'nbt.png'},
                {'key': 'other:item', 'shortKey': 'other', 'icon': 'other.png'},
                {'key': 'mod:item:3', 'shortKey': 'different', 'icon': 'different.png'},
                {'key': 'mod:item', 'shortKey': 'base', 'icon': 'base.png'},
            ]
            plan = build_plan(items, [], root, {'File:中文名.png': 'nbt.png'})
            self.assertEqual(plan['uploadIcons'], ['base.png', 'different.png', 'other.png'])
            self.assertEqual(plan['redirects'], {
                'File:Damage.png': 'base.png', 'File:Nbt.png': 'base.png',
                'File:中文名.png': 'base.png'})
            self.assertEqual(plan, build_plan(list(reversed(items)), [], root, {'File:中文名.png': 'nbt.png'}))
            db = root / 'state.db'
            update_state(db, set(plan['uploadIcons']), plan['redirects'])
            self.assertEqual(pending_redirects(db, plan['redirects']), [])
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.execute("UPDATE images SET icon_state='已上传' WHERE icon_name='base.png'")
            self.assertEqual(len(pending_redirects(db, plan['redirects'])), 3)

    def test_pixels_ignore_png_metadata_but_include_entire_strip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = Image.new('RGBA', (2, 4), 'red')
            image.save(root / 'a.png', compress_level=0)
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text('Description', 'different encoding')
            image.save(root / 'b.png', compress_level=9, pnginfo=metadata)
            self.assertEqual(pixel_hash(root / 'a.png'), pixel_hash(root / 'b.png'))
            image.putpixel((1, 3), (0, 0, 255, 255))
            image.save(root / 'c.png')
            self.assertNotEqual(pixel_hash(root / 'a.png'), pixel_hash(root / 'c.png'))
            Image.new('RGBA', (4, 2), 'red').save(root / 'd.png')
            self.assertNotEqual(pixel_hash(root / 'a.png'), pixel_hash(root / 'd.png'))

    def test_fallback_prefers_plain_then_damage_and_shared_files_do_not_cross_items(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            items = []
            for name, raw, nbt in [('tag', 'mod:item', '{x:1}'),
                                   ('plain', 'mod:item:5', ''),
                                   ('shared', 'mod:item:6', '')]:
                Image.new('RGBA', (1, 1), 'red').save(root / (name + '.png'))
                items.append({'key': raw, 'shortKey': name, 'icon': name + '.png', 'nbt': nbt})
            items.append({'key': 'mod:another', 'icon': 'shared.png'})
            plan = build_plan(items, [], root, {})
            self.assertEqual(plan['uploadIcons'], ['plain.png', 'shared.png'])
            self.assertEqual(plan['redirects'], {'File:Tag.png': 'plain.png'})

    def test_animation_companions_are_compared_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            items, records = [], []
            for damage, name in enumerate(('a', 'b', 'c')):
                Image.new('RGBA', (1, 1), 'red').save(root / (name + '.png'))
                strip = Image.new('RGBA', (2, 1), 'red')
                if name == 'c':
                    strip.putpixel((1, 0), (0, 0, 255, 255))
                strip.save(root / (name + '_dynamic.png'))
                items.append({'key': f'mod:item:{damage}', 'shortKey': name})
                records.append({'shortKey': name, 'iconName': name + '.png', 'frameCount': 2})
            plan = build_plan(items, records, root, {})
            self.assertEqual(plan['uploadIcons'], ['a.png', 'a_dynamic.png', 'c_dynamic.png'])
            self.assertEqual(plan['redirects']['File:B dynamic.png'], 'a_dynamic.png')
            self.assertNotIn('File:C dynamic.png', plan['redirects'])


if __name__ == '__main__':
    unittest.main()
