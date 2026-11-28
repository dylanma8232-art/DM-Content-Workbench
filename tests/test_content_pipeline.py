import threading
import time
import unittest
from unittest.mock import patch

import app
import content_pipeline
from test_collection_tasks import PluginCollectionFixture


class PipelineTests(unittest.TestCase):
    def test_ocr_enters_cleanable_text_but_visual_interpretation_stays_separate(self):
        item = dict(body='作者原文', content_level='detail_text', media_kind='image',
                    image_texts=[dict(index=1, text='图片中的文字', status='ok')],
                    image_observations=[dict(index=1, text='这是模型的图表观察', status='ok')])
        interpreted = dict(analysis_status='ready', analysis=dict(summary='摘要', missing=[]), evidence=[])
        with patch('content_media.enrich_media', side_effect=lambda p,i,d: i), patch('content_understanding.analyze', return_value=interpreted):
            result = content_pipeline.process('xiaohongshu', [item], '科普')[0]
        self.assertEqual(result['extracted_body'], '作者原文')
        self.assertIn('图片中的文字', result['body'])
        self.assertNotIn('这是模型的图表观察', result['body'])
        self.assertEqual(result['processing_status'], 'complete')
        self.assertEqual(item['body'], '作者原文')
        # Retrying a saved material never duplicates OCR text.
        with patch('content_media.enrich_media', side_effect=lambda p,i,d: i), patch('content_understanding.analyze', return_value=interpreted):
            retried = content_pipeline.process('xiaohongshu', [result])[0]
        self.assertEqual(retried['body'].count('图片中的文字'), 1)

    def test_new_transcription_replaces_saved_description_without_losing_speech(self):
        saved=dict(body='旧简介',extracted_body='旧简介',content_level='detail_text',media_kind='video')
        upgraded=dict(saved,body='这次成功取得的讲话',transcript_text='这次成功取得的讲话',content_level='transcript')
        with patch('content_media.enrich_media',return_value=upgraded), patch('content_understanding.analyze',return_value={'analysis_status':'unavailable'}) as analyze:
            result=content_pipeline.process('bilibili',[saved])[0]
        self.assertEqual(result['body'],'这次成功取得的讲话')
        self.assertEqual(analyze.call_args.args[0]['extracted_body'],'这次成功取得的讲话')

    def test_missing_model_and_sampling_gaps_stay_partial(self):
        item = dict(body='原文', content_level='detail_text', media_kind='video', media_parts=2,
                    missing=['speech_unavailable'])
        with patch('content_media.enrich_media', side_effect=lambda p,i,d:i), patch('content_understanding.analyze', return_value=dict(analysis_status='unavailable')):
            result=content_pipeline.process('bilibili',[item])[0]
        self.assertEqual(result['processing_status'],'partial')
        self.assertTrue(any('讲话' in m for m in result['processing_missing']))
        self.assertTrue(any('第 1 集' in m for m in result['processing_missing']))


class PipelinePersistenceTests(PluginCollectionFixture):
    item = dict(external_id='BV1xx411c7mD', title='科普视频', body='这是一段较完整的原始讲话内容。包含案例和结论，可以用来清洗并进行选题分析。',
                author='作者', url='https://www.bilibili.com/video/BV1xx411c7mD',
                content_level='subtitle', media_kind='video', segments=[dict(start=0,end=5,text='原始讲话')])

    def test_retry_one_material_keeps_frames_and_batch(self):
        task = self.task()
        item = dict(self.item, content_level='detail_text', segments=[], missing=['speech_unavailable'],
                    visual_frames=[dict(time=2.5, text='既有画面', status='ok')])
        with patch('content_understanding.analyze', return_value={'analysis_status':'ready'}):
            result = self.collect(f'{self.base}/{task["id"]}', [item,item])
        self.assertEqual((result['received'],result['imported'],result['batch_partial']), (2,1,1))
        run_id = result['run_id']
        path = f'/api/projects/1/collection-materials/{run_id}/1'
        self.assertEqual(self.client.get(path.replace('/projects/1/', '/projects/2/')).status_code, 404)
        with patch('collection_depth.media_text',return_value={'content_level':'transcript','body':'  '}):
            self.assertEqual(self.client.post(path+'/retry-speech').status_code, 422)
        def recover(*args):
            self.assertEqual(self.client.post(path+'/retry-speech').status_code, 409)
            return {'content_level':'transcript','body':'本条讲话'}
        with patch('collection_depth.media_text',side_effect=recover) as speech, patch('content_media.enrich_media') as media:
            retry = self.client.post(path+'/retry-speech').json()
            self.assertTrue(retry['before']['can_retry'])
            self.assertEqual(retry['after']['speech'],'本条讲话')
            self.client.post(path+'/retry-speech').raise_for_status()
            media.assert_not_called()
            speech.assert_called_once()
        payload = self.client.get(f'/api/runs/{run_id}').json()['payload']
        self.assertEqual(len(payload['materials']),1)
        self.assertEqual(payload['materials'][0]['raw']['visual_frames'],item['visual_frames'])
        self.assertFalse(self.client.get(path).json()['can_retry'])
        self.assertEqual(self.client.get(self.base).json()['tasks'][0]['batch_partial'],0)
        with app.database() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM runs').fetchone()[0],1)

    def test_auto_clean_evidence_library_and_retry_preserve_previous_run(self):
        task=self.task()
        path=f'{self.base}/{task["id"]}'
        first = self.collect(path, [self.item])
        run=self.client.get(f'/api/runs/{first["run_id"]}').json()
        self.assertEqual(run['stage'],2)
        self.assertIn('cleaned',run['payload'])
        self.assertEqual(run['payload']['materials'][0]['raw']['processing_status'],'partial')
        missing = self.client.get('/api/projects/1/library/materials', params={'run_id': first['run_id'], 'partial_only': True}).json()
        self.assertEqual(first['batch_partial'], 1)
        self.assertEqual(missing['total'], first['batch_partial'])
        analysis=dict(summary='视频讲解案例', missing=[], relevance=dict(matches=True,reason='符合科普'),
                      topics=['科普'],steps=[],claims=[dict(text='讲解案例',evidence_ids=['body'])])
        with patch('content_understanding.analyze',return_value=dict(analysis_status='ready',analysis=analysis,
                        analysis_fingerprint='configured-model-v1',evidence=[dict(id='body',text=self.item['body'])])):
            retried=self.client.post(path+'/reprocess')
        retried.raise_for_status()
        self.assertEqual(retried.json()['updated'],1)
        old=self.client.get(f'/api/runs/{first["run_id"]}').json()
        self.assertEqual(old['payload']['materials'][0]['raw']['analysis_status'],'unavailable')
        materials=self.client.get('/api/projects/1/library/materials').json()['items']
        latest=next(m for m in materials if m['run_id']==retried.json()['run_id'])
        self.assertEqual(latest['analysis']['summary'],'视频讲解案例')
        # Missing model on a later retry cannot replace a richer saved analysis.
        downgraded=self.client.post(path+'/reprocess').json()
        self.assertEqual(downgraded['duplicates'],1)
        self.assertEqual(downgraded['batch_partial'], 0)
        self.assertEqual(self.client.get(path).json()['last_run_id'],retried.json()['run_id'])

    def test_frame_only_content_can_reach_topics_with_explicit_analysis_labels(self):
        task=self.task()
        frame_text='画面显示软件设置页，用户选择了模型并点击运行按钮。' * 5
        item=dict(self.item,body='演示',segments=[],content_level='detail_text',
                  visual_frames=[dict(time=2,text=frame_text,status='ok')])
        parsed=dict(summary='AI 软件操作演示',topics=['AI'],claims=[dict(text='演示模型设置步骤',evidence_ids=['frame:1'])],
                    relevance=dict(matches=True,reason='软件教学'),steps=['选择模型','运行'],missing=[])
        interpreted=dict(analysis_status='ready',analysis=parsed,analysis_fingerprint='frames',
                         evidence=[dict(id='frame:1',text=frame_text)])
        with patch('content_understanding.analyze',return_value=interpreted):
            result = self.collect(f'{self.base}/{task["id"]}', [item])
        run_id=result['run_id']
        run=self.client.get(f'/api/runs/{run_id}').json()
        self.assertEqual(run['payload']['cleaned'][0]['reason'],'保留')
        advanced=self.client.post(f'/api/runs/{run_id}/advance',json={'stage':2})
        advanced.raise_for_status()
        run=self.client.get(f'/api/runs/{run_id}').json()
        self.assertEqual(run['payload']['insights'][0]['source_kind'],'analysis')
        self.assertIn('内容解析',run['payload']['recommendations'][0]['reason'])
        evidence=app.evidence_text(run['payload'],{'id':1})
        self.assertIn('非作者原话',evidence)
        self.assertIn('依据 frame:1',evidence)
        self.assertIn(frame_text,evidence)

    def test_scheduler_runs_due_task_and_pauses_login_handoff(self):
        task=self.task(minutes=60)
        with app.database() as conn:
            conn.execute('UPDATE collection_tasks SET due=0 WHERE id=?',(task['id'],))
        path = f'{self.base}/{task["id"]}'
        with patch('collection_reader.read') as reader, patch('collection_browser.read') as browser:
            app.app.state.collect_due(threading.Event())
            pending = self.client.get(path).json()
            self.assertEqual(pending['status'], 'browser')
            self.assertTrue(pending['attempt_token'])
            with app.database() as conn:
                conn.execute('UPDATE collection_tasks SET due=0 WHERE id=?', (task['id'],))
            app.app.state.collect_due(threading.Event())
            self.assertEqual(self.client.get(path).json()['attempt_token'], pending['attempt_token'])
            reader.assert_not_called()
            browser.assert_not_called()
        self.client.post(path + '/results', json={'attempt_token': pending['attempt_token'], 'items': [self.item]}).raise_for_status()
        self.assertEqual(self.client.get(path).json()['status'], 'completed')

    def test_images_are_saved_and_served_only_inside_their_project(self):
        from content_image_store import ImageCache
        cache=ImageCache(app.database,1,budget=100)
        raw=b'\x89PNG\r\n\x1a\nexample'
        image_id=cache.put('https://images.example/1.png',raw,'image/png')
        self.assertEqual(cache.get('https://images.example/1.png')[0],raw)
        self.assertEqual(self.client.get(f'/api/projects/1/collection-images/{image_id}').content,raw)
        other=self.client.post('/api/projects',json={'name':'其他项目'}).json()['id']
        self.assertEqual(self.client.get(f'/api/projects/{other}/collection-images/{image_id}').status_code,404)
        with self.assertRaises(ValueError):
            ImageCache(app.database,1,budget=1).put('https://images.example/2.png',raw+b'new','image/png')

    def test_saving_original_image_is_an_update_even_when_text_is_unchanged(self):
        task=self.task(platform='xiaohongshu')
        item=dict(self.item,external_id='0123456789abcdef01234567',url='https://www.xiaohongshu.com/explore/0123456789abcdef01234567',
                  content_level='detail_text',media_kind='image',image_assets=[],missing=['image_not_saved'])
        result=dict(analysis_status='ready',analysis={'summary':'摘要','missing':[]},analysis_fingerprint='stable')
        path=f'{self.base}/{task["id"]}'
        with patch('content_understanding.analyze',return_value=result):
            self.collect(path, [item])
        def saved(platform,item,deadline,**kwargs):
            return dict(item,image_assets=[dict(index=1,id='a'*64)],missing=[])
        with patch('content_media.enrich_media',side_effect=saved),patch('content_understanding.analyze',return_value=result):
            retry=self.client.post(path+'/reprocess').json()
        self.assertEqual(retry['updated'],1)
        self.assertEqual(retry['processing_counts']['complete'],1)

    def test_active_task_lease_blocks_concurrent_run(self):
        task=self.task()
        with app.database() as conn:
            conn.execute('UPDATE collection_tasks SET lease_until=? WHERE id=?',(time.time()+60,task['id']))
        with patch('collection_reader.read') as read:
            self.assertTrue(self.client.post(f'{self.base}/{task["id"]}/run').json()['in_progress'])
        read.assert_not_called()
