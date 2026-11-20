import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import wiki_inventory

PREFIX = 'Data:Versions/GTNH/9.9/'


class InventoryScopeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.wikitext = Path(tmp.name) / 'wikitext'
        self.db = Path(tmp.name) / 'tasks.db'
        self.enterContext(patch.multiple(wiki_inventory, WIKITEXT_DIR=str(self.wikitext),
                                         DB_PATH=str(self.db), WIKI_PREFIX=PREFIX))
        wiki_inventory.init_db(True)
        for page in ('Recipes/forestry/BeeBreedingHandler_null/0.json', 'Recipes/gregtech/a/0.json',
                     'Quests/0.json'):
            path = self.wikitext / wiki_inventory.title_to_path(PREFIX + page)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}', 'utf-8')

    def rebuild(self, online, delete_dirs=frozenset()):
        with patch.object(wiki_inventory, 'login'), \
                patch.object(wiki_inventory, 'fetch_wiki_titles', return_value={PREFIX + t for t in online}):
            wiki_inventory.rebuild_tasks(True, delete_dirs)
        with closing(sqlite3.connect(self.db)) as conn:
            return {title.removeprefix(PREFIX): status for title, status in
                    conn.execute('SELECT title, status FROM tasks WHERE path IS NULL')}

    def test_space_and_underscore_are_the_same_page(self):
        self.assertEqual(self.rebuild({'Recipes/forestry/BeeBreedingHandler null/0.json'}), {})

    def test_stale_pages_in_generated_directories_are_deleted(self):
        result = self.rebuild({'Recipes/gregtech/a/00/0.json', 'Quests/1.json'})
        self.assertEqual(result, {'Recipes/gregtech/a/00/0.json': 'pending_delete', 'Quests/1.json': 'pending_delete'})

    def test_kept_and_missing_directories_need_explicit_consent(self):
        self.enterContext(patch.object(wiki_inventory, 'KEEP_ONLINE_DIRS', frozenset({'gtnhdimension'})))
        online = {'gtnhdimension/0.json', 'Tc4research/0.json'}
        self.assertEqual(self.rebuild(online), {'gtnhdimension/0.json': 'kept_online', 'Tc4research/0.json': 'kept_online'})
        self.assertEqual(self.rebuild(online, {'Tc4research', 'gtnhdimension'}),
                         {'gtnhdimension/0.json': 'kept_online', 'Tc4research/0.json': 'pending_delete'})


class DeleteAfterUploadTest(InventoryScopeTest):
    def setUp(self):
        super().setUp()
        import data_upload
        self.data_upload = data_upload
        self.enterContext(patch.multiple(data_upload, WIKITEXT_DIR=str(self.wikitext),
                                         TASK_DB=str(self.db), WIKI_PREFIX=PREFIX))

    def run_upload(self, edit_error=None, args=(), uploads=None):
        import http.cookiejar
        self.rebuild({'Recipes/gregtech/a/00/0.json'})
        with patch.object(self.data_upload, 'login_once', return_value=http.cookiejar.CookieJar()),                 patch.object(self.data_upload.signal, 'signal'),                 patch.object(self.data_upload, 'edit_page', side_effect=edit_error, return_value={}) as edit,                 patch.object(self.data_upload, 'delete_page', return_value={}) as delete:
            self.data_upload.main(list(args))
        if uploads is not None:
            uploads.extend(call.args[1].removeprefix(PREFIX) for call in edit.call_args_list)
        return [call.args[1] for call in delete.call_args_list]

    def test_only_limits_uploads_and_deletes_to_selected_directories(self):
        uploads = []
        self.assertEqual(self.run_upload(args=['--only', 'Quests'], uploads=uploads), [])
        self.assertEqual(uploads, ['Quests/0.json'])
        uploads.clear()
        self.assertEqual(self.run_upload(args=['--only', 'Recipes'], uploads=uploads),
                         [PREFIX + 'Recipes/gregtech/a/00/0.json'])
        self.assertEqual(sorted(uploads), ['Recipes/forestry/BeeBreedingHandler_null/0.json', 'Recipes/gregtech/a/0.json'])

    def test_failed_upload_postpones_deletes(self):
        self.assertEqual(self.run_upload(RuntimeError('boom')), [])

    def test_deletes_follow_successful_uploads(self):
        self.assertEqual(self.run_upload(), [PREFIX + 'Recipes/gregtech/a/00/0.json'])

    # 父类的清单用例在这里不重复运行
    test_space_and_underscore_are_the_same_page = None
    test_stale_pages_in_generated_directories_are_deleted = None
    test_kept_and_missing_directories_need_explicit_consent = None


if __name__ == '__main__':
    unittest.main()
