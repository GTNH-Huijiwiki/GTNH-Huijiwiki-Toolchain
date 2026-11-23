import tempfile
import unittest
from pathlib import Path

import page_hash
from platform_json import normalize_keys, normalize_page


class NormalizeKeysTest(unittest.TestCase):
    def test_keys_get_fullwidth(self):
        self.assertEqual(normalize_keys('{"T2.5内存": "x", "a$b":1}'), '{"T2．5内存": "x", "a＄b":1}')

    def test_values_unchanged(self):
        text = '{"k": "a.b$c", "list": ["x.y", "z"]}'
        self.assertEqual(normalize_keys(text), text)

    def test_nested_and_layout_preserved(self):
        text = '{\n  "a.b" :\n  {\n    "c.d": "v.w"\n  }\n}\n'
        self.assertEqual(normalize_keys(text), '{\n  "a．b" :\n  {\n    "c．d": "v.w"\n  }\n}\n')

    def test_escaped_quote_in_key_and_value_containing_colon(self):
        self.assertEqual(normalize_keys(r'{"a\".b": "c: d.e", "f.g": "h"}'), r'{"a\"．b": "c: d.e", "f．g": "h"}')

    def test_value_followed_by_comma_not_key(self):
        self.assertEqual(normalize_keys('["a.b", "c.d"]'), '["a.b", "c.d"]')

    def test_idempotent(self):
        once = normalize_keys('{"a.b": {"c$d": 1}}')
        self.assertEqual(normalize_keys(once), once)

    def test_only_json_pages(self):
        self.assertEqual(normalize_page('{"a.b":1}', 'Data:X/0.json'), '{"a．b":1}')
        self.assertEqual(normalize_page('a.b: c', 'Module:X'), 'a.b: c')


class HashTest(unittest.TestCase):
    def test_hash_matches_normalized_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / 'a.json', Path(tmp) / 'b.json'
            a.write_text('{"x.y": "1"}', 'utf-8')
            b.write_text('{"x．y": "1"}', 'utf-8')
            self.assertEqual(page_hash.file_sha1(a), page_hash.file_sha1(b))

    def test_non_json_hash_is_raw(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'a.wikitext'
            p.write_bytes(b'a.b')
            self.assertEqual(page_hash.file_sha1(p), hashlib.sha1(b'a.b').hexdigest())


if __name__ == '__main__':
    unittest.main()
