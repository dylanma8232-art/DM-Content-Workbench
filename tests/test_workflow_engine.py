"""Pure workflow tests: synthetic evidence and mocked transport only."""
import copy
import json
import unittest
from unittest.mock import Mock, patch

import httpx
from fastapi import HTTPException

import workflow_engine as workflow


def material(ident, body, **extra):
    return {'id': ident, 'title': f'材料 {ident}', 'body': body, **extra}


def response(content):
    return httpx.Response(200, request=httpx.Request('POST', 'https://model.example/v1/chat/completions'),
                          json={'choices': [{'message': {'content': json.dumps(content)}}]})


class WorkflowTests(unittest.TestCase):
    def test_near_duplicate_exclusion_and_preserved_original(self):
        text = '社区花园以雨水收集和堆肥为基础，志愿者每周记录植物生长和土壤湿度。'
        items = [material(8, text * 4), material(11, text * 4 + '补充记录。'),
                 material(20, '另一份完全不同的厨房烹饪记录。')]
        original = copy.deepcopy(items)
        rows = workflow.clean(items, {'dedup_mode': 'similar', 'dedup_threshold': .7})
        self.assertEqual(rows[1]['reason'], '正文相似')
        self.assertEqual(rows[1]['same_as'], 8)
        self.assertEqual(rows[2]['reason'], '保留')
        self.assertEqual(workflow.clean(items, {'dedup_mode': 'exact'})[1]['reason'], '保留')
        self.assertEqual(workflow.clean(items, {'dedup_mode': 'off'})[1]['reason'], '保留')
        self.assertEqual(items, original)

    def test_ready_visual_length_and_fingerprint_identity(self):
        items = [
            material(1, '', raw={'analysis_status': 'ready', 'analysis_fingerprint': 'image-a',
                                'visual_frames': [{'text': '第一幅画面的植物观察'}]}),
            material(2, '', raw={'analysis_status': 'ready', 'analysis_fingerprint': 'image-b',
                                'image_observations': [{'text': '第二幅画面的植物观察'}]}),
            material(3, '', raw={'analysis_status': 'ready', 'analysis_fingerprint': 'image-a',
                                'visual_frames': [{'text': '第一幅画面的植物观察'}]}),
            material(4, '', raw={'analysis_status': 'failed', 'analysis_fingerprint': 'image-c',
                                'visual_frames': [{'text': '未验证的画面观察不应计入长度'}]}),
            material(5, '', raw={'analysis_status': 'ready', 'analysis_fingerprint': 'image-d',
                                'visual_frames': [{'text': '短'}]}),
            material(6, '正文', raw={'analysis_status': 'ready', 'analysis_fingerprint': 'image-e',
                                   'visual_frames': [{'text': '画面'}],
                                   'image_observations': [{'text': '观察'}]}),
        ]
        original = copy.deepcopy(items)
        for mode in ('exact', 'similar', 'off'):
            with self.subTest(mode=mode):
                rows = workflow.clean(items, {'min_length': 6, 'dedup_mode': mode})
                self.assertEqual([r['reason'] for r in rows[:2]], ['保留', '保留'])
                self.assertEqual(rows[3]['reason'], '低于最短正文长度')
                self.assertEqual(rows[4]['reason'], '低于最短正文长度')
                self.assertEqual(rows[5]['reason'], '保留')
                self.assertEqual(rows[0]['body'], '')
                if mode == 'off':
                    self.assertEqual(rows[2]['reason'], '保留')
                else:
                    self.assertEqual(rows[2]['same_as'], 1)
                    self.assertNotEqual(rows[2]['reason'], '保留')
        self.assertEqual(items, original)

    def test_clean_settings_and_manual_decisions(self):
        items = [material(1, ' 正文 https://example.test/path '), material(2, '正文'),
                 material(3, '排除词和其他信息'), material(4, '短')]
        rows = workflow.clean(items, {'remove_links': True, 'exclude_terms': '其他,排除词\n广告',
                                      'min_length': 2})
        self.assertEqual(rows[0]['body'], '正文')
        self.assertEqual(rows[1]['same_as'], 1)
        self.assertIn('排除词', rows[2]['reason'])
        self.assertEqual(rows[3]['reason'], '低于最短正文长度')
        override = workflow.clean(items, {'manual_decisions': {'3': 'keep', '1': 'exclude'},
                                           'exclude_terms': '排除词'})
        self.assertEqual(override[2]['reason'], '保留')
        self.assertEqual(override[0]['reason'], '人工排除')

    def test_default_evidence_no_network_multiple_passages_and_focus(self):
        items = [material(10, '第一句。关注节水。第三句。'), material(30, '节水尚待更多记录。', partial=True)]
        snapshot = copy.deepcopy(items)
        with patch.object(workflow.httpx, 'post', side_effect=AssertionError('不得联网')) as post:
            result = workflow.analyze(items, workflow.clean(items, {}),
                                      {'evidence_count': 2, 'focus': '节水', 'topics': '节水,不存在'})
        post.assert_not_called()
        self.assertEqual(len(result['insights']), 2)
        self.assertEqual(result['insights'][0]['quote'], '关注节水。')
        self.assertEqual(len(result['insights'][0]['evidence']), 2)
        self.assertEqual(result['themes'][0]['sources'], [10, 30])
        self.assertIn('M10:body:2', result['research']['ledger'])
        self.assertTrue(result['research']['gaps'])
        self.assertFalse(result['research']['verified'])
        self.assertEqual(items, snapshot)

    def test_saved_ai_claim_preserves_non_quote_label_and_validates_refs(self):
        item = material(5, '', raw={'analysis_status': 'ready',
            'analysis': {'claims': [{'text': '画面显示植物', 'evidence_ids': ['frame:2']}]},
            'evidence': [{'id': 'frame:2', 'text': '一盆植物'}]})
        rows = [{'id': 5, 'body': '', 'reason': '保留'}]
        result = workflow.analyze([item], rows, {})
        self.assertIn('非作者原话', result['insights'][0]['quote'])
        self.assertIn('非作者原话', result['research']['ledger']['M5:frame:2']['label'])
        item['raw']['analysis']['claims'][0]['evidence_ids'] = ['frame:99']
        with self.assertRaises(HTTPException) as error:
            workflow.analyze([item], rows, {})
        self.assertEqual(error.exception.status_code, 422)
        self.assertIn('进度已保留', error.exception.detail)

    def test_model_schema_refs_and_no_silent_fallback(self):
        items = [material(7, '材料仅记录一次节水实验。')]
        cleaned = workflow.clean(items, {})
        config = {'analysis_engine': 'model', 'model_base': 'https://model.example/v1/',
                  'model_name': 'synthetic-model', 'model_key': 'synthetic-secret'}
        valid = {'summary': [{'text': '材料记录一次实验', 'refs': ['M7:body:1']}],
                 'material_findings': [{'text': '实验记录', 'refs': ['M7:body:1'], 'material_id': 7}],
                 'gaps': [], 'conflicts': []}
        with patch.object(workflow.httpx, 'post', return_value=response(valid)) as post:
            result = workflow.analyze(items, cleaned, config)
        self.assertEqual(result['research']['summary'], valid['summary'])
        self.assertEqual(post.call_args.args[0], 'https://model.example/v1/chat/completions')
        self.assertFalse(post.call_args.kwargs['follow_redirects'])
        prompt = post.call_args.kwargs['json']['messages'][0]['content']
        self.assertIn('不得补造', prompt)
        invalids = []
        for key in ('summary', 'material_findings', 'gaps', 'conflicts'):
            invalid = copy.deepcopy(valid)
            invalid[key] = [{'text': '编造的发现', 'refs': ['M999:body:1']}]
            if key == 'material_findings':
                invalid[key][0]['material_id'] = 999
            invalids.append(invalid)
        wrong_material = copy.deepcopy(valid)
        wrong_material['material_findings'][0]['material_id'] = 99
        invalids += [wrong_material, {'summary': '无引用断言'}, []]
        for invalid in invalids:
            with self.subTest(invalid=invalid), patch.object(workflow.httpx, 'post', return_value=response(invalid)):
                with self.assertRaises(HTTPException) as error:
                    workflow.analyze(items, cleaned, config)
                self.assertEqual(error.exception.status_code, 502)
                self.assertIn('进度已保留', error.exception.detail)
                self.assertNotIn('编造', error.exception.detail)
        with patch.object(workflow.httpx, 'post', side_effect=httpx.ConnectError('synthetic-secret')):
            with self.assertRaises(HTTPException) as error:
                workflow.analyze(items, cleaned, config)
        self.assertNotIn('synthetic-secret', error.exception.detail)

    def test_candidate_source_families_focus_partial_and_limit(self):
        items = [material(1, '节水', url='https://a.test/1', author='同一作者'),
                 material(2, '节水', url='https://b.test/2', author='同一作者'),
                 material(3, '节水', url='https://c.test/3', author='其他作者', partial=True),
                 material(4, '未知来源')]
        insights = [{'id': i, 'quote': '节水', 'matched': i == 3} for i in range(1, 5)]
        base = [{'key': 'a', 'title': '共享作者', 'ids': [1, 2]},
                {'key': 'b', 'title': '节水', 'ids': [1, 3]},
                {'key': 'c', 'title': '未知来源', 'ids': [4]}]
        result = workflow.rank_candidates(items, insights, [], {'min_topic_sources': 2}, base)
        self.assertEqual([r['key'] for r in result], ['b'])
        self.assertEqual(result[0]['source_count'], 2)
        self.assertEqual(result[0]['partial_count'], 1)
        rows = workflow.rank_candidates(items, insights, [], {'topic_limit': 1, 'focus': '节水'}, base)
        self.assertEqual(len(rows), 1)
        unknown = workflow.rank_candidates(items, insights, [], {}, [base[2]])[0]
        self.assertTrue(unknown['no_sources'])
        self.assertIn('身份不明', unknown['reason'])

    def test_draft_settings_legacy_and_token_aware_callbacks(self):
        config = {'target_words': 1200, 'outline': '先依据，再讨论',
                  'writing_requirements': '避免夸张', 'generation_max_tokens': 4096}
        evidence = '素材 1：只记录一次观察。'
        local = workflow.draft({'title': '节水'}, evidence, '社区读者', '文章', config)
        for text in ('1200', '先依据，再讨论', '避免夸张', '社区读者', evidence, '不保证字数'):
            self.assertIn(text, local)
        long_evidence = '素材 1：观察记录。' * 400
        shorter = workflow.draft('标题', long_evidence, '', '', {'target_words': 100})
        longer = workflow.draft('标题', long_evidence, '', '', {'target_words': 2000})
        self.assertLess(len(shorter), len(longer))
        self.assertIn('按目标篇幅节选', shorter)
        calls = []
        def legacy(title, text, audience, style):
            calls.append((title, text, audience, style))
            return '模型稿件'
        self.assertEqual(workflow.draft('节水', evidence, '', '文章', {**config, 'engine': 'model'}, legacy), '模型稿件')
        self.assertIn('避免夸张', calls[0][3])
        self.assertIn('1200', calls[0][3])
        modern = Mock(return_value='现代接口稿件')
        workflow.draft('节水', evidence, '', '文章', {**config, 'engine': 'model'}, modern)
        self.assertEqual(modern.call_args.kwargs['max_tokens'], 4096)
        with self.assertRaises(HTTPException):
            workflow.draft('节水', evidence, '', '', {'engine': 'model'}, Mock(side_effect=RuntimeError('secret')))

    def test_bounds_and_budget(self):
        for config in ({'evidence_count': 6}, {'analysis_engine': 'auto'}):
            with self.assertRaises(HTTPException):
                workflow.analyze([], [], config)
        for config in ({'dedup_threshold': .69}, {'dedup_threshold': float('nan')}, {'min_length': True}):
            with self.assertRaises(HTTPException):
                workflow.clean([], config)
        for config in ({'topic_limit': 21}, {'min_topic_sources': 11}):
            with self.assertRaises(HTTPException):
                workflow.rank_candidates([], [], [], config)
        for config in ({'target_words': 99}, {'generation_max_tokens': 8001}):
            with self.assertRaises(HTTPException):
                workflow.draft('标题', '素材 1：正文', '', '', config)
        items = [material(i, ('不同材料正文' + str(i)) * 300) for i in range(1, 101)]
        result = workflow.analyze(items, workflow.clean(items, {}), {'evidence_count': 5})
        self.assertLessEqual(sum(len(e['text']) for e in result['research']['ledger'].values()), 60000)
        self.assertLessEqual(len(result['research']['ledger']), 2000)
        self.assertTrue(result['research']['gaps'])


if __name__ == '__main__':
    unittest.main()
