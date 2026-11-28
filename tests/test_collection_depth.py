from unittest.mock import patch
import unittest

import collection_depth
from test_collection_tasks import PluginCollectionFixture


class DepthReaderTests(unittest.TestCase):
    item = dict(external_id='BV1xx411c7mD', title='列表标题', body='截断的简介',
                url='https://www.bilibili.com/video/BV1xx411c7mD', author='作者')

    def test_bilibili_prefers_timed_subtitles_and_preserves_multi_part_scope(self):
        responses = [
            {'code': 0, 'data': {'bvid': 'BV1xx411c7mD', 'aid': 1, 'cid': 2,
                                'title': '详情标题', 'desc': '完整的详情简介', 'pages': [{}, {}]}},
            {'data': {'subtitle': {'subtitles': [{'lan': 'zh-CN', 'subtitle_url': '//aisubtitle.hdslb.com/test.json'}]}}},
            {'body': [{'from': 1, 'to': 3, 'content': '视频里真正讲到的原话。'}]}]
        with patch('collection_depth.public_json', side_effect=responses):
            result=collection_depth.bili(dict(self.item))
        self.assertEqual(result['body'], '视频里真正讲到的原话。')
        self.assertEqual(result['content_level'], 'subtitle')
        self.assertEqual(result['segments'][0]['start'], 1)
        self.assertEqual(result['media_parts'], 2)
        self.assertEqual(result['media_part'], 1)

    def test_failed_details_keep_real_list_text_and_mark_failure(self):
        with patch('collection_depth.bili', side_effect=ValueError('登录')), patch('collection_browser.detail', side_effect=RuntimeError()), patch('collection_depth.media_text', side_effect=lambda item, deadline: item):
            result=collection_depth.enrich('bilibili',[self.item])[0]
        self.assertEqual(result['body'], self.item['body'])
        self.assertEqual(result['content_level'], 'summary')
        self.assertEqual(result['depth_status'], 'detail_unavailable')

    def test_vtt_keeps_timestamps(self):
        segments=collection_depth._vtt('WEBVTT\n\n00:01.200 --> 00:03.400\n这是字幕\n\n')
        self.assertEqual(segments, [{'start':1.2,'end':3.4,'text':'这是字幕'}])


class DepthPersistenceTests(PluginCollectionFixture):
    def test_shallow_retry_cannot_replace_deep_material(self):
        task=self.task()
        path=f'{self.base}/{task["id"]}'
        item=dict(DepthReaderTests.item,body='视频内的完整原话',content_level='subtitle',depth_status='subtitle_read',
                  segments=[{'start':0,'end':3,'text':'视频内的完整原话'}])
        first = self.collect(path, [item])
        second = self.collect(path, [DepthReaderTests.item])
        self.assertEqual(second['duplicates'],1)
        run=self.client.get('/api/runs/'+str(first['run_id'])).json()
        self.assertEqual(run['payload']['materials'][0]['body'],item['body'])
        raw=run['payload']['materials'][0]['raw']
        self.assertEqual(raw['content_level'],'subtitle')
        self.assertEqual(raw['segments'][0]['end'],3)
        library=self.client.get('/api/projects/1/library/materials').json()
        material=next(row for row in library['items'] if row['run_id']==first['run_id'])
        self.assertEqual(material['content_level'],'subtitle')
        self.assertEqual(material['segments'][0]['text'],'视频内的完整原话')
