"""上传遇到站点限速（ratelimited）时，所有线程暂停后重试同一个页面，其他错误不重试。"""

import io
from contextlib import redirect_stdout
import unittest
from unittest.mock import patch

import data_upload


class UploadBackoffTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(data_upload, 'RATE_LIMIT_WAIT', 0.01))
        self.enterContext(patch.object(data_upload, '_pause_until', 0.0))
        self.enterContext(redirect_stdout(io.StringIO()))
        data_upload._stop.clear()
        self.addCleanup(data_upload._stop.clear)

    def test_default_threads_stay_below_the_rate_limit(self):
        self.assertEqual(data_upload.DEFAULT_THREADS, 4)

    def test_rate_limit_is_recognised_by_code_or_text(self):
        self.assertTrue(data_upload.is_rate_limited('ratelimited', ''))
        self.assertTrue(data_upload.is_rate_limited('', 'As an anti-abuse measure, you are limited'))
        self.assertFalse(data_upload.is_rate_limited('badtoken', 'Invalid CSRF token'))
        self.assertFalse(data_upload.is_rate_limited(None, None))

    def test_retries_the_same_page_until_it_succeeds(self):
        calls = []

        def action():
            calls.append(1)
            if len(calls) < 3:
                raise data_upload.RateLimitedError('limited')
            return {'result': 'Success'}

        self.assertEqual(data_upload.call_with_backoff(action, 'Data:Versions/X/1/Items/00/0.json'),
                         {'result': 'Success'})
        self.assertEqual(len(calls), 3)

    def test_gives_up_after_the_retry_budget(self):
        calls = []

        def action():
            calls.append(1)
            raise data_upload.RateLimitedError('limited')

        with self.assertRaises(data_upload.RateLimitedError):
            data_upload.call_with_backoff(action, 'page')
        self.assertEqual(len(calls), data_upload.RATE_LIMIT_RETRIES + 1)

    def test_other_errors_are_not_retried(self):
        calls = []

        def action():
            calls.append(1)
            raise RuntimeError('permission denied')

        with self.assertRaises(RuntimeError):
            data_upload.call_with_backoff(action, 'page')
        self.assertEqual(len(calls), 1)

    def test_edit_page_turns_the_api_error_into_a_rate_limit(self):
        token = {'query': {'tokens': {'csrftoken': 'T'}}}
        error = {'error': {'code': 'ratelimited', 'info': 'Please try again in a few minutes.'}}
        with patch.object(data_upload, '_get', return_value=token), \
                patch.object(data_upload, '_post_form', return_value=error):
            with self.assertRaises(data_upload.RateLimitedError):
                data_upload.edit_page(object(), 'Data:Versions/X/1/Items/00/0.json', '{}')

    def test_interrupt_stops_waiting(self):
        data_upload._stop.set()
        with self.assertRaises(RuntimeError):
            data_upload.call_with_backoff(lambda: 'never', 'page')


if __name__ == '__main__':
    unittest.main()
