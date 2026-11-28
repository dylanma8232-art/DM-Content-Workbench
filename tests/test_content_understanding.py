import copy
import json
import os
import unittest
from unittest.mock import patch

import httpx

import content_understanding as understanding


class ContentUnderstandingTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            'DM_MODEL_BASE_URL': 'https://model.example/v1/',
            'DM_MODEL_NAME': 'test-model', 'DM_MODEL_KEY': 'test-key',
            'DM_MODEL_VERSION': 'revision-1',
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.post = patch('content_understanding.httpx.post').start()
        self.addCleanup(patch.stopall)
        self.item = {
            'body': '先准备材料，再进行测量。', 'content_level': 'subtitle',
            'segments': [{'start': 0, 'end': 2, 'text': '先准备材料。'}],
            'image_texts': ['', {'text': '测量结果为三厘米。'}],
            'visual_frames': [{'timestamp': 2, 'description': '尺子放在材料旁边。'}],
        }
        self.analysis = {
            'summary': '准备材料并测量。', 'topics': ['测量'],
            'claims': [{'text': '先准备材料。', 'evidence_ids': ['body', 'subtitle:1']},
                       {'text': '展示尺子和测量结果。', 'evidence_ids': ['image:2', 'frame:1']}],
            'steps': ['准备材料', '测量'],
            'relevance': {'matches': True, 'reason': '包含测量过程'}, 'missing': [],
        }
        self.reply(self.analysis)

    def reply(self, analysis):
        self.post.return_value = httpx.Response(200, json={
            'choices': [{'message': {'content': json.dumps(analysis, ensure_ascii=False)}}]
        }, request=httpx.Request('POST', 'https://model.example/v1/chat/completions'))

    def test_accepts_valid_analysis_preserves_evidence_and_records_retry_metadata(self):
        original = copy.deepcopy(self.item)
        result = understanding.analyze(self.item, '测量')
        self.assertEqual(result['analysis_status'], 'ready')
        self.assertTrue(result['complete'])
        self.assertEqual(result['analysis'], self.analysis)
        self.assertEqual(result['errors'], [])
        self.assertEqual(self.item, original)
        for key, value in original.items():
            self.assertEqual(result[key], value)
        self.assertEqual(result['model'], 'test-model')
        self.assertEqual(result['model_version'], 'revision-1')
        self.assertEqual(result['prompt_version'], understanding.PROMPT_VERSION)
        self.assertEqual(len(result['content_fingerprint']), 64)
        args, kwargs = self.post.call_args
        self.assertEqual(args[0], 'https://model.example/v1/chat/completions')
        self.assertGreater(kwargs['timeout'], 0)
        self.assertLessEqual(kwargs['timeout'], 60)
        self.assertEqual(kwargs['headers']['Authorization'], 'Bearer test-key')
        prompt = json.loads(kwargs['json']['messages'][1]['content'])
        self.assertEqual(prompt['topic'], '测量')
        self.assertEqual(prompt['evidence']['subtitle:1'], original['segments'][0]['text'])
        self.assertEqual(set(prompt['evidence']), {'body', 'subtitle:1', 'image:2', 'frame:1'})
        result['segments'][0]['text'] = '修改副本'
        self.assertEqual(self.item, original)

    def test_invalid_or_empty_references_reject_entire_analysis(self):
        for refs in (['image:1'], ['subtitle:0'], ['frame:2'], ['body', 'invented'], [], [1], [{}]):
            with self.subTest(refs=refs):
                self.analysis['claims'][0]['evidence_ids'] = refs
                self.reply(self.analysis)
                result = understanding.analyze(self.item)
                self.assertEqual(result['analysis_status'], 'failed')
                self.assertEqual(result['analysis'], {})
                self.assertFalse(result['complete'])
                self.assertTrue(result['errors'])

    def test_missing_configuration_never_calls_model(self):
        for name in ('DM_MODEL_BASE_URL', 'DM_MODEL_NAME'):
            with self.subTest(name=name), patch.dict(os.environ, {name: ''}):
                result = understanding.analyze(self.item)
                self.assertEqual(result['analysis_status'], 'unavailable')
                self.assertEqual(result['analysis'], {})
                self.assertFalse(result['complete'])
                self.assertTrue(result['errors'])
        self.post.assert_not_called()

    def test_title_and_media_urls_are_not_extracted_evidence(self):
        result = understanding.analyze({
            'title': '测量教程', 'body': '', 'image_texts': [],
            'visual_frames': [{'url': 'https://example.com/frame.jpg'}], 'segments': [],
        })
        self.assertEqual(result['analysis_status'], 'unavailable')
        self.assertEqual(result['analysis'], {})
        self.post.assert_not_called()

    def test_malformed_analysis_never_fills_missing_fields(self):
        variants = [None, [], {'summary': '仅有摘要'},
                    dict(self.analysis, extra='补造字段'),
                    dict(self.analysis, relevance={'matches': 'true', 'reason': '原因'}),
                    dict(self.analysis, claims=[]), dict(self.analysis, steps='步骤')]
        for analysis in variants:
            with self.subTest(analysis=analysis):
                self.reply(analysis)
                result = understanding.analyze(self.item)
                self.assertEqual(result['analysis_status'], 'failed')
                self.assertEqual(result['analysis'], {})
                self.assertFalse(result['complete'])

    def test_model_failures_have_chinese_errors_and_preserve_input(self):
        replies = [httpx.Response(200, text='not json'),
                   httpx.Response(200, json={'choices': []}),
                   httpx.Response(200, json={'choices': [{'message': {'content': 'not json'}}]}),
                   httpx.Response(503)]
        for response in replies:
            response.request = httpx.Request('POST', 'https://model.example')
            self.post.return_value = response
            result = understanding.analyze(self.item)
            self.assertEqual(result['analysis_status'], 'failed')
            self.assertEqual(result['body'], self.item['body'])
            self.assertEqual(result['analysis'], {})
            self.assertRegex(result['errors'][0], '[\u3400-\u9fff]')
        self.post.side_effect = httpx.ReadTimeout('timeout')
        self.assertEqual(understanding.analyze(self.item)['analysis_status'], 'failed')

    def test_incomplete_sources_cannot_be_marked_complete(self):
        variants = [dict(self.item, content_truncated=True),
                    dict(self.item, detail_error='部分内容读取失败'),
                    dict(self.item, media_parts=2, media_part=1),
                    {'body': '列表摘要', 'content_level': 'summary'}]
        for item in variants:
            with self.subTest(item=item):
                analysis = copy.deepcopy(self.analysis)
                analysis['claims'] = [{'text': '材料内容', 'evidence_ids': ['body']}]
                self.reply(analysis)
                result = understanding.analyze(item)
                self.assertEqual(result['analysis_status'], 'ready')
                self.assertFalse(result['complete'])
        self.analysis['missing'] = ['缺少最终结果']
        self.reply(self.analysis)
        self.assertFalse(understanding.analyze(self.item)['complete'])

    def test_fingerprint_is_stable_and_changes_with_extracted_content(self):
        first = understanding.analyze(self.item)
        reordered = dict(reversed(list(self.item.items())))
        self.assertEqual(first['content_fingerprint'],
                         understanding.analyze(reordered)['content_fingerprint'])
        self.assertNotEqual(first['content_fingerprint'],
                            understanding.analyze(self.item, '另一个主题')['content_fingerprint'])
        changed = dict(self.item, body='新提取的正文')
        self.assertNotEqual(first['content_fingerprint'],
                            understanding.analyze(changed)['content_fingerprint'])

    def test_pipeline_shape_and_extraction_statuses(self):
        item = copy.deepcopy(self.item)
        item['image_texts'] = [
            {'text': '失败的图像提取', 'status': 'failed'},
            {'text': '测量结果为三厘米。', 'status': 'completed'},
        ]
        item['visual_frames'] = [{'time': 2, 'text': '尺子放在材料旁边。', 'status': 'ok'}]
        result = understanding.analyze(item)
        self.assertEqual(result['analysis_status'], 'ready')
        self.assertEqual(result['analysis_error'], '')
        self.assertEqual(result['analysis_model'], 'test-model')
        self.assertEqual(result['analysis_version'], understanding.PROMPT_VERSION)
        self.assertEqual(result['analysis_fingerprint'], result['content_fingerprint'])
        refs = {row['id']: row['text'] for row in result['evidence']}
        self.assertNotIn('image:1', refs)
        self.assertEqual(refs['image:2'], '测量结果为三厘米。')
        self.assertEqual(result['body'], item['body'])
        with patch.dict(os.environ, {'DM_MODEL_NAME': ''}):
            self.post.reset_mock()
            unavailable = understanding.analyze(item)
            self.post.assert_not_called()
        self.assertTrue(unavailable['analysis_error'])
        self.assertEqual(unavailable['evidence'], result['evidence'])

    def test_material_raw_extraction_is_supported(self):
        result = understanding.analyze({'id': 1, 'body': self.item['body'], 'raw': self.item})
        self.assertEqual(result['analysis_status'], 'ready')
        self.assertEqual(result['raw'], self.item)
        self.assertTrue(result['complete'])


if __name__ == '__main__':
    unittest.main()
