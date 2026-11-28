import json
from uuid import uuid4

from test_workbench import WorkbenchFixture


class PublishingTests(WorkbenchFixture):
    def test_project_scoped_media_and_per_account_receipts(self):
        base = '/api/projects/1/publishing'
        other = self.client.post('/api/projects', json={'name': '另一项目'}).json()['id']
        other_base = f'/api/projects/{other}/publishing'
        targets = [{'platform': 'bilibili', 'account': '主账号'},
                   {'platform': 'xiaohongshu', 'account': '编辑部'}]
        form = {'request_id': str(uuid4()), 'title': '发布样例', 'body': '原文',
                'targets': json.dumps(targets)}
        media = [('files', ('cover.png', b'\x89PNG\r\n\x1a\nexample', 'image/png'))]
        for _ in range(2):
            self.client.post(base, data=form, files=media).raise_for_status()
        posts = self.client.get(base).json()['posts']
        self.assertEqual(len(posts), 1)
        post = posts[0]
        self.assertEqual(post['body'], '原文')
        self.assertEqual(len(post['targets']), 2)
        asset = f"/assets/{post['assets'][0]['id']}"
        self.assertEqual(self.client.get(base + asset).content, media[0][1][1])
        self.assertEqual(self.client.get(other_base + asset).status_code, 404)
        self.assertEqual(self.client.get(other_base).json()['posts'], [])
        target = next(t for t in post['targets'] if t['platform'] == 'bilibili')
        path = f"/targets/{target['id']}"
        update = {'status': 'published', 'url': 'https://www.bilibili.com/video/BV1xx411c7mD'}
        self.assertEqual(self.client.post(other_base + path, json=update).status_code, 404)
        self.client.post(base + path, json=update).raise_for_status()
        states = {t['platform']: t['status'] for t in self.client.get(base).json()['posts'][0]['targets']}
        self.assertEqual(states, {'bilibili': 'published', 'xiaohongshu': 'pending'})

    def reviewed_draft(self):
        run_id = self.create()
        path = f'/api/runs/{run_id}'
        for stage in range(1, 5):
            self.client.post(path + '/advance', json={'stage': stage, 'topic_id': 1}).raise_for_status()
        source = '## 审核标题\n\n完整 **Markdown** 正文。\n\n来源：\n- 原始依据\n'
        self.client.put(path + '/draft', json={'version': 1, 'content': source}).raise_for_status()
        self.client.post(path + '/review', json={'version': 2, 'decision': '通过'}).raise_for_status()
        return path, {'request_id': str(uuid4()), 'title': '审核标题', 'body': source,
                      'run_id': run_id, 'version': 2,
                      'targets': json.dumps([{'platform': 'bilibili', 'account': '主账号'}])}

    def test_per_target_retry_progress_and_work_measurements(self):
        base = '/api/projects/1/publishing'
        targets = [{'platform': platform, 'account':'编辑'} for platform in ('bilibili','douyin')]
        form = {'request_id':str(uuid4()), 'title':'样例', 'body':'正文', 'targets':json.dumps(targets)}
        first, second = self.client.post(base, data=form).json()['targets']
        paths = [base + '/targets/' + str(row['id']) for row in (first, second)]
        urls = ['https://example.org/work/a', 'https://example.org/work/b']
        sample = {'views':5, 'engagements':1,'source':'人工核对','sampled':'2026-11-28T14:00:00+08:00'}
        self.assertEqual(self.client.put(paths[0] + '/metrics', json=sample).status_code, 409)
        self.client.post(paths[0], json={'status':'published','url':urls[0]}).raise_for_status()
        self.client.put('/api/projects/1/account-daily', json=targets[0] | {'day':'2026-11-28','views':99,'followers':2,'engagements':8,'published':1}).raise_for_status()
        for state in ('failed','pending'):
            self.client.post(paths[1], json={'status':state}).raise_for_status()
            rows = self.client.get(base).json()['posts'][0]['targets']
            self.assertEqual([(r['status'],r['url'],r['metrics']) for r in rows], [('published',urls[0],[]),(state,'',[])])
        self.client.post(paths[1], json={'status':'published','url':urls[1]}).raise_for_status()
        for index, path in enumerate(paths):
            self.client.put(path + '/metrics', json=sample | {'views':index}).raise_for_status()
        self.client.put(paths[0] + '/metrics', json=sample | {'sampled':'2026-11-28T06:00:00Z'}).raise_for_status()
        rows = self.client.get(base).json()['posts'][0]['targets']
        self.assertEqual([r['status'] for r in rows], ['published'] * 2)
        self.assertEqual([[m['views'] for m in r['metrics']] for r in rows], [[5],[1]])

    def test_linked_publishing_preserves_reviewed_snapshot_and_assets(self):
        path, form = self.reviewed_draft()
        base = '/api/projects/1/publishing'
        media = [('files', ('source.md', form['body'].encode(), 'text/markdown'))]
        response = self.client.post(base, data=form, files=media)
        response.raise_for_status()
        post = response.json()
        for key in ('title', 'body', 'run_id', 'version'):
            self.assertEqual(post[key], form[key])
        self.assertEqual(self.client.get(base + f"/assets/{post['assets'][0]['id']}").content,
                         form['body'].encode())
        self.client.put(path + '/draft', json={'version': 2, 'content': '# 新版本\n已改动'}).raise_for_status()
        self.assertEqual(self.client.post(base, data=form, files=media).json(), post)
        self.assertEqual(self.client.get(base).json()['posts'], [post])

    def test_linked_publishing_rejects_changed_title_or_full_markdown(self):
        _, form = self.reviewed_draft()
        base = '/api/projects/1/publishing'
        changes = [{'title': '另一个标题'}, {'title': ' 审核标题'},
                   {'body': form['body'].rstrip()}, {'body': '完整 Markdown 正文。'},
                   {'body': form['body'] + '\n新增内容'}]
        for change in changes:
            with self.subTest(change=change):
                response = self.client.post(base, data={**form, **change})
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()['detail'],
                                 '关联稿件内容已改动，请返回成稿库保存新版本并重新审核，或取消关联按独立内容准备发布')
        self.assertEqual(self.client.get(base).json()['posts'], [])
        self.client.post(base, data=form).raise_for_status()

    def test_linked_publishing_requires_latest_review_and_version(self):
        path, form = self.reviewed_draft()
        base = '/api/projects/1/publishing'
        self.client.post(path + '/review', json={'version': 2, 'expected_review_count': 1, 'decision': '退回'}).raise_for_status()
        self.assertEqual(self.client.post(base, data=form).status_code, 409)
        self.client.post(path + '/review', json={'version': 2, 'expected_review_count': 2, 'decision': '通过'}).raise_for_status()
        self.client.put(path + '/draft', json={'version': 2, 'content': '# 新标题\n新版本'}).raise_for_status()
        self.assertEqual(self.client.post(base, data=form).status_code, 409)
        latest = {**form, 'version': 3, 'title': '新标题', 'body': '# 新标题\n新版本'}
        self.assertEqual(self.client.post(base, data=latest).status_code, 409)
        self.client.post(path + '/review', json={'version': 3, 'decision': '通过'}).raise_for_status()
        self.assertEqual(self.client.post(base, data=form).status_code, 409)
        self.client.post(base, data=latest).raise_for_status()

    def test_standalone_content_can_differ_from_reviewed_draft(self):
        _, form = self.reviewed_draft()
        form.pop('run_id')
        form.pop('version')
        form.update(title='自行填写的标题', body='不经过 AI 的独立正文\n')
        response = self.client.post('/api/projects/1/publishing', data=form)
        response.raise_for_status()
        post = response.json()
        self.assertEqual((post['title'], post['body']), (form['title'], form['body']))
        self.assertIsNone(post['run_id'])
        self.assertIsNone(post['version'])

    def test_extension_download_and_navigation(self):
        import io
        from zipfile import ZipFile

        page = self.client.get('/').text
        self.assertIn('发布', page)
        response = self.client.get('/api/publishing/extension.zip')
        response.raise_for_status()
        with ZipFile(io.BytesIO(response.content)) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            for resource in [manifest['background']['service_worker'], manifest['action']['default_popup'],
                             *manifest['content_scripts'][0]['js'], 'workbench.mjs']:
                self.assertIn(resource, archive.namelist())
        self.assertEqual(manifest['manifest_version'], 3)
