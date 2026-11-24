import threading
import unittest

import image_files as f


def page(title, **kw):
    return {"title": title, **kw}


class ClassifyTests(unittest.TestCase):
    def test_states(self):
        url = "https://x/gtnh/uploads/1/16/"
        self.assertEqual(f.classify_page(page("File:A b.png", missing=True)), f.FREE)
        # 自己的文件
        self.assertEqual(f.classify_page(page("File:A b.png", imageinfo=[{"url": url + "A_b.png"}])), f.OWN_FILE)
        # 自己的文件 + #REDIRECT 文本：重定向没生效
        self.assertEqual(f.classify_page(page("File:A b.png", redirect=True, imageinfo=[{"url": url + "A_b.png"}])), f.BROKEN)
        # 生效的重定向：imageinfo 是目标的文件
        self.assertEqual(f.classify_page(page("File:A b.png", redirect=True, imageinfo=[{"url": url + "Target.png"}])), f.REDIRECT)
        # 没有文件的重定向页
        self.assertEqual(f.classify_page(page("File:A b.png", redirect=True)), f.REDIRECT)
        self.assertEqual(f.classify_page(page("File:A b.png")), f.PLAIN_PAGE)

    def test_url_is_decoded(self):
        p = page("File:Icon 钢.png", imageinfo=[{"url": "https://x/Icon_%E9%92%A2.png"}])
        self.assertEqual(f.classify_page(p), f.OWN_FILE)

    def test_same_file_name(self):
        self.assertTrue(f.same_file_name("icon_A_b.png", "Icon A b.png"))
        self.assertTrue(f.same_file_name("Icon_A_b.png", "Icon_A_b.png"))
        self.assertFalse(f.same_file_name("Icon_A_c.png", "Icon_A_b.png"))
        self.assertFalse(f.same_file_name("", "Icon_A_b.png"))

    def test_details_report_stored_name(self):
        def post(params):
            return {"query": {"pages": [page("File:A.png", redirect=True, imageinfo=[{"url": "https://x/Target.png"}])]}}

        self.assertEqual(f.file_details(post, ["File:A.png"]), {"File:A.png": (f.REDIRECT, "Target.png")})

    def test_blocking_sets(self):
        self.assertEqual(f.BLOCKS_REDIRECT, {f.OWN_FILE, f.BROKEN})
        self.assertEqual(f.BLOCKS_UPLOAD, {f.REDIRECT, f.BROKEN})

    def test_file_states_batches_and_normalizes(self):
        calls = []

        def post(params):
            calls.append(params["titles"].split("|"))
            return {"query": {"normalized": [{"from": "file:a.png", "to": "File:A.png"}],
                               "pages": [{"title": "File:A.png", "missing": True}]}}

        states = f.file_states(post, ["file:a.png"])
        self.assertEqual(states, {"file:a.png": f.FREE})
        self.assertEqual(len(calls), 1)

    def test_rate_limit_is_distinguished(self):
        with self.assertRaises(f.RateLimitedError):
            f.raise_for_error({"error": {"code": "ratelimited", "info": "x"}})
        with self.assertRaises(RuntimeError) as ctx:
            f.raise_for_error({"error": {"code": "other", "info": "boom"}})
        self.assertNotIsInstance(ctx.exception, f.RateLimitedError)

    def test_backoff_retries_once_then_succeeds(self):
        f.RATE_LIMIT_WAIT = 0.01
        attempts = []

        def action():
            attempts.append(1)
            if len(attempts) == 1:
                raise f.RateLimitedError("slow down")
            return "ok"

        self.assertEqual(f.call_with_backoff(action, "x", threading.Event()), "ok")
        self.assertEqual(len(attempts), 2)

    def test_delete_missing_is_success(self):
        def post(params):
            return {"error": {"code": "missingtitle", "info": "gone"}}

        def get(params):
            return {"query": {"tokens": {"csrftoken": "t"}}}

        self.assertFalse(f.delete_page(post, get, "File:X.png", "r"))


if __name__ == "__main__":
    unittest.main()
