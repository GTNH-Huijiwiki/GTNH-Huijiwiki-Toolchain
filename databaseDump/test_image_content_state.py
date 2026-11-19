import sqlite3,tempfile,unittest
from contextlib import closing
from pathlib import Path
from image_state import update_state,mark_uploaded,reconcile_legacy,icon_hashes,pending_redirects

class ContentStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=self.root/'state.db'
    def state(self,name='a.png'):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            return conn.execute('SELECT icon_state,content_sha1,uploaded_sha1 FROM images WHERE icon_name=?',(name,)).fetchone()
    def scan(self,value):
        (self.root/'a.png').write_bytes(value)
        hashes=icon_hashes(self.root,{'a.png'})
        update_state(self.db,{'a.png'},{'File:别名.png':'a.png'},hashes)
        return hashes['a.png']
    def test_changed_content_same_name_requeues(self):
        old=self.scan(b'first');mark_uploaded(self.db,'a.png',old)
        self.assertEqual(self.state()[0],'已上传')
        self.scan(b'first');self.assertEqual(self.state()[0],'已上传')
        new=self.scan(b'updated');self.assertNotEqual(old,new)
        self.assertEqual(self.state()[0],'待更新')
        self.assertEqual(pending_redirects(self.db,{'File:别名.png':'a.png'}),[])
        mark_uploaded(self.db,'a.png',new)
        self.assertEqual(self.state(),('已上传',new,new))
    def test_old_ack_does_not_confirm_new_bytes(self):
        old=self.scan(b'first');new=self.scan(b'second')
        mark_uploaded(self.db,'a.png',old)
        self.assertEqual(self.state(),('待更新',new,old))
    def legacy(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute('CREATE TABLE images (icon_name TEXT PRIMARY KEY,icon_state TEXT NOT NULL)')
            conn.execute("INSERT INTO images VALUES ('a.png','已上传')")
    def test_legacy_must_verify_matching_remote(self):
        self.legacy();digest=self.scan(b'current')
        self.assertEqual(self.state(),('待核验',digest,None))
        self.scan(b'current');self.assertEqual(self.state()[0],'待核验')
        reconcile_legacy(self.db,{'a.png':digest})
        self.assertEqual(self.state(),('已上传',digest,digest))
    def test_legacy_stale_remote_requeues(self):
        self.legacy();digest=self.scan(b'current')
        reconcile_legacy(self.db,{'a.png':'old-remote-hash'})
        self.assertEqual(self.state(),('待更新',digest,'old-remote-hash'))
    def test_legacy_missing_remote_requeues(self):
        self.legacy();self.scan(b'current');reconcile_legacy(self.db,{'a.png':None})
        self.assertEqual(self.state()[0],'待更新')

if __name__=='__main__':unittest.main()
