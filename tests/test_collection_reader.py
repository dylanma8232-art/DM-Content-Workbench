import unittest
from unittest.mock import patch
import httpx
import collection_reader as reader


class ReaderTests(unittest.TestCase):
    def test_structured_bilibili_results_keep_identity_and_text(self):
        response = httpx.Response(200, json={'code': 0, 'data': {'result': [
            {'bvid': 'BV1xx411c7mD', 'title': '<em>AI</em> 科普', 'description': '真实简介', 'author': '作者'}]}})
        with patch('collection_reader.httpx.get', return_value=response):
            items = reader.read('bilibili', 'topic', 'AI', '', 5)
        self.assertEqual(items[0]['external_id'], 'BV1xx411c7mD')
        self.assertEqual(items[0]['title'], 'AI 科普')
        self.assertEqual(items[0]['body'], '真实简介')

    def test_no_unsupported_account_command_or_rate_limit_switch(self):
        with patch('collection_reader.shutil.which', return_value=None):
            with self.assertRaises(reader.BrowserRequired):
                reader.read('tiktok', 'account', 'test', '', 5)
        with patch('collection_reader.httpx.get', return_value=httpx.Response(429)):
            with self.assertRaises(reader.CollectionBlocked):
                reader.read('bilibili', 'topic', 'AI', '', 5)

    def test_untrusted_adapter_links_are_ignored(self):
        self.assertIsNone(reader._item('x', '标题', '正文', 'https://x.com.evil.test/test/status/123'))
