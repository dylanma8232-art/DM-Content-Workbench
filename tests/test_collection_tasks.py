from unittest.mock import Mock, patch
import time
import app
from test_workbench import WorkbenchFixture



class PluginCollectionFixture(WorkbenchFixture):
    def setUp(self):
        super().setUp()
        stub = patch('collection_depth.enrich', side_effect=lambda platform, rows, **kwargs: rows)
        stub.start()
        self.addCleanup(stub.stop)
        media = patch('content_media.enrich_media', side_effect=lambda platform, item, deadline, **kwargs: dict(item))
        media.start()
        self.addCleanup(media.stop)
        env = patch.dict('os.environ', {'DM_MODEL_BASE_URL': '', 'DM_MODEL_NAME': ''})
        env.start()
        self.addCleanup(env.stop)

    base = '/api/projects/1/collection-tasks'

    def task(self, **values):
        data = dict(platform='bilibili', mode='topic', query='AI科普', limit=5)
        data.update(values)
        response = self.client.post(self.base, json=data)
        response.raise_for_status()
        return response.json()

    def start_attempt(self, path):
        with patch('collection_reader.read') as reader, patch('collection_browser.read') as browser:
            response = self.client.post(path + '/run')
        reader.assert_not_called()
        browser.assert_not_called()
        response.raise_for_status()
        task = response.json()['task']
        self.assertEqual(task['status'], 'browser')
        token = task['attempt_token']
        self.assertIsInstance(token, str)
        self.assertTrue(token)
        self.assertEqual(self.client.get(path).json()['attempt_token'], token)
        return token

    def collect(self, path, items):
        token = self.start_attempt(path)
        response = self.client.post(path + '/results', json={'attempt_token': token, 'items': items})
        response.raise_for_status()
        return response.json()


class CollectionTaskTests(PluginCollectionFixture):
    def test_six_fixed_platforms_and_project_isolation(self):
        platforms = self.client.get(self.base).json()['platforms']
        self.assertEqual({p['id'] for p in platforms}, {'instagram','tiktok','douyin','xiaohongshu','bilibili','x'})
        self.assertEqual(self.client.post(self.base, json=dict(platform='youtube',mode='topic',query='AI',limit=5)).status_code,422)
        task=self.task()
        project=self.client.post('/api/projects',json={'name':'另一个'}).json()['id']
        self.assertEqual(self.client.get(f'/api/projects/{project}/collection-tasks/{task["id"]}').status_code,404)
        self.assertEqual(self.client.post(self.base,json=dict(platform='x',mode='account',query='https://evil.test/name',limit=5)).status_code,422)

    def test_real_flow_idempotency_changed_content_and_stale_browser_attempt(self):
        task=self.task()
        path=f'{self.base}/{task["id"]}'
        item=dict(external_id='BV1xx411c7mD',title='AI科普',body='具体原文',url='https://www.bilibili.com/video/BV1xx411c7mD',author='作者')
        token = self.start_attempt(path)
        payload = {'attempt_token': token, 'items': [item]}
        first = self.client.post(path + '/results', json=payload).json()
        with patch('content_pipeline.process') as process:
            self.assertEqual(self.client.post(path + '/results', json=payload).json(), first)
            process.assert_not_called()
        second = self.collect(path, [item])
        self.assertEqual(self.client.post(path + '/results', json=payload).status_code, 409)
        self.assertEqual(first['added'],1)
        self.assertEqual(second['duplicates'],1)
        self.assertEqual(second['batch_partial'], 0)
        self.assertIsNone(second['run_id'])
        latest = self.client.get(self.base).json()['tasks'][0]
        self.assertIsNone(latest['run_id'])
        self.assertEqual(latest['last_run_id'], first['run_id'])
        changed = self.collect(path, [dict(item, body='更正后的原文')])
        self.assertEqual(changed['updated'],1)
        self.assertEqual(self.client.get(path).json()['run_id'], changed['run_id'])
        old=self.client.get('/api/runs/'+str(first['run_id'])).json()
        self.assertEqual(old['payload']['materials'][0]['body'],'具体原文')

    def test_edit_preserves_history_and_expires_browser_attempt(self):
        task = self.task()
        path = f'{self.base}/{task["id"]}'
        items = [dict(external_id=key, title='作品', body='原始证据', author='作者',
                      url=f'https://www.bilibili.com/video/{key}') for key in ('av1', 'av2')]
        result = self.collect(path, items)
        token = self.start_attempt(path)
        batch = '/api/runs/' + str(result['run_id'])
        history = self.client.get(batch).json()
        unchanged = self.client.put(path, json={'limit': 5, 'minutes': 0}).json()
        self.assertEqual(unchanged['attempt_token'], token)
        edited = self.client.put(path, json={'limit': 1, 'minutes': 47}).json()
        self.assertEqual((edited['id'], edited['last_run_id'], edited['status']), (task['id'], result['run_id'], 'ready'))
        self.assertAlmostEqual(edited['due'], time.time() + 47 * 60, delta=5)
        with patch('collection_depth.enrich') as enrich:
            response = self.client.post(path + '/results', json={'attempt_token': token, 'items': items})
            self.assertEqual(response.status_code, 409)
            enrich.assert_not_called()
        self.client.post(path + '/reprocess').raise_for_status()
        self.assertEqual(self.client.get(batch).json(), history)
        token = self.start_attempt(path)
        self.assertEqual(self.client.post(path + '/results', json={'attempt_token': token, 'items': items}).status_code, 422)

    def test_edit_validation_scope_and_active_lease(self):
        task = self.task()
        path = f'{self.base}/{task["id"]}'
        settings = {'limit': 2, 'minutes': 0}
        for invalid in ({'query': '另一目标'}, {'limit': 51}, {'minutes': -1}):
            self.assertEqual(self.client.put(path, json=settings | invalid).status_code, 422)
        self.assertEqual(self.client.put(path.replace('/projects/1/', '/projects/2/'), json=settings).status_code, 404)
        token = self.start_attempt(path)
        payload = {'attempt_token': token, 'items': []}

        def during_processing(platform, records, *args, **kwargs):
            self.assertEqual(self.client.put(path, json=settings).status_code, 409)
            self.assertEqual(self.client.post(path + '/results', json=payload).status_code, 409)
            active = self.client.post(path + '/run').json()
            self.assertTrue(active['in_progress'])
            self.assertEqual(active['task']['attempt_token'], token)
            return records

        with patch('content_pipeline.process', side_effect=during_processing) as process:
            self.client.post(path + '/results', json=payload).raise_for_status()
            process.assert_called_once()
        self.assertEqual(self.client.get(path).json()['limit'], 5)

    def test_scheduler_rechecks_after_switch_to_manual(self):
        task = self.task(minutes=60)
        path = f'{self.base}/{task["id"]}'
        with app.database() as conn:
            conn.execute('UPDATE collection_tasks SET due=0 WHERE id=?', (task['id'],))
        def pause_after_selection():
            self.client.put(path, json={'limit': 5, 'minutes': 0}).raise_for_status()
            return False
        stop = Mock()
        stop.is_set.side_effect = pause_after_selection
        with patch('collection_reader.read') as reader:
            app.app.state.collect_due(stop)
            reader.assert_not_called()
        self.assertEqual(self.client.get(path).json()['status'], 'ready')

    def test_only_original_task_accepts_plugin_results(self):
        path = self.base+'/'+str(self.task(minutes=5)['id'])
        other_path = self.base+'/'+str(self.task(query='其他任务')['id'])
        other = self.client.get(other_path).json()
        self.start_attempt(path)
        paused = self.client.get(path).json()
        self.assertIsNone(paused['next_run'])
        callback = {'attempt_token':paused['attempt_token'],'items':[]}
        self.assertEqual(self.client.post(other_path+'/results',json=callback).status_code,409)
        self.client.post(path+'/results',json=callback).raise_for_status()
        self.assertEqual(self.client.get(path).json()['status'],'completed')
        self.assertEqual(self.client.get(other_path).json(),other)

    def test_low_quality_return_has_no_actionable_material(self):
        task = self.task()
        self.assertIsNone(task['next_run'])
        path = f'{self.base}/{task["id"]}'
        item = dict(external_id='av7',title='作品',body='正文',author='作者',url='https://www.bilibili.com/video/av7')
        self.collect(path, [dict(item, content_level='detail_text')])
        result = self.collect(path, [item])
        self.assertEqual((result['skipped_quality'],result['source_duplicates'],result['batch_partial'],result['imported']),(1,0,0,0))
        self.assertIsNone(result['run_id'])
        self.assertTrue(self.client.get(path).json()['message'])
