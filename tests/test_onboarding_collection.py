from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import app
from test_workbench import WorkbenchFixture


class OnboardingCollectionTests(WorkbenchFixture):
    def setUp(self):
        # Keep background scheduling out of onboarding and its assertions.
        self.enterContext(patch('app.schedule'))
        super().setUp()
        self.reader = self.enterContext(patch('collection_reader.read'))
        self.browser = self.enterContext(patch('collection_browser.read'))
        self.insert_run = self.enterContext(patch('app.insert_run'))
        self.rss = self.enterContext(patch('connections.rss'))
        self.addCleanup(self.assert_no_collection)

    def assert_no_collection(self):
        for operation in (self.reader, self.browser, self.insert_run, self.rss):
            operation.assert_not_called()
        with app.database() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM runs').fetchone()[0], 0)

    def setup(self, **values):
        request = dict(name='首个项目', audience='编辑', style='简报', collection=dict(
            platform='x', mode='account', query=' https://twitter.com/example/ ', limit=7, minutes=15))
        request.update(values)
        return self.client.post('/api/setup', json=request)

    def test_account_is_normalized_and_scheduled_without_running(self):
        with patch('app.time.time', return_value=1700000000):
            response = self.setup()
        self.assertEqual(response.status_code, 200, response.text)
        project = response.json()['project_id']
        with app.database() as conn:
            task = dict(conn.execute('SELECT * FROM collection_tasks').fetchone())
            self.assertEqual(conn.execute('SELECT count(*) FROM sources').fetchone()[0], 0)
        self.assertEqual({key: task[key] for key in (
            'project_id', 'platform', 'mode', 'query', 'url', 'limit', 'minutes', 'due', 'status')},
            dict(project_id=project, platform='x', mode='account', query='example',
                 url='https://x.com/example', limit=7, minutes=15, due=1700000900, status='ready'))
        self.assertEqual(self.client.get('/api/setup').json(), dict(complete=True, project_id=project))
        projects = self.client.get('/api/projects').json()
        self.assertEqual(next(row for row in projects if row['id'] == project)['settings']['audience'], '编辑')

    def test_topic_uses_existing_project_and_default_schedule(self):
        with app.database() as conn:
            project = conn.execute("INSERT INTO projects(name,settings) VALUES('首个项目','{}')").lastrowid
        with patch('app.time.time', return_value=1700000000):
            response = self.setup(collection=dict(platform='bilibili', mode='topic', query=' AI 科普 ', limit=5))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), dict(project_id=project, created=True))
        with app.database() as conn:
            task = conn.execute('SELECT * FROM collection_tasks').fetchone()
            self.assertEqual((task['project_id'], task['query'], task['url'], task['minutes'], task['due']),
                             (project, 'AI 科普', 'https://search.bilibili.com/all?keyword=AI%20%E7%A7%91%E6%99%AE', 0, 1700000000))
            self.assertIn('简报', conn.execute('SELECT settings FROM projects WHERE id=?', (project,)).fetchone()[0])

    def test_invalid_collection_leaves_no_partial_setup(self):
        with app.database() as conn:
            before = [tuple(row) for row in conn.execute('SELECT * FROM projects')]
        for collection in (
            dict(platform='youtube', mode='topic', query='AI', limit=5),
            dict(platform='x', mode='account', query='https://evil.test/example', limit=5),
            dict(platform='bilibili', mode='account', query='not-a-user-id', limit=5),
            dict(platform='x', mode='account', query='home', limit=5),
        ):
            with self.subTest(collection=collection):
                response = self.setup(collection=collection, platform='RSS', source_address='https://example.org/feed')
                self.assertEqual(response.status_code, 422, response.text)
                with app.database() as conn:
                    self.assertEqual([tuple(row) for row in conn.execute('SELECT * FROM projects')], before)
                    for table in ('onboarding', 'sources', 'collection_tasks'):
                        self.assertEqual(conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)
                self.assertFalse(self.client.get('/api/setup').json()['complete'])

    def test_invalid_account_does_not_update_existing_project_settings(self):
        with app.database() as conn:
            project = conn.execute("INSERT INTO projects(name,settings) VALUES('首个项目','{}')").lastrowid
        response = self.setup(collection=dict(platform='x', mode='account', query='@invalid/name', limit=5))
        self.assertEqual(response.status_code, 422, response.text)
        with app.database() as conn:
            self.assertEqual(conn.execute('SELECT settings FROM projects WHERE id=?', (project,)).fetchone()[0], '{}')
            self.assertEqual(conn.execute('SELECT count(*) FROM onboarding').fetchone()[0], 0)

    def test_legacy_source_and_collection_are_created_only_once(self):
        values = dict(platform=' 订阅 ', source_address=' https://example.org/feed ', source_kind='rss', minutes=60)
        response = self.setup(**values)
        self.assertEqual(response.status_code, 200, response.text)
        project = response.json()['project_id']
        self.assertEqual(self.setup(name='重复项目', **values).json(), dict(project_id=project, created=False))
        # An already completed workspace also skips semantic target validation.
        self.assertEqual(self.setup(collection=dict(platform='x', mode='account', query='home', limit=1)).json(),
                         dict(project_id=project, created=False))
        with app.database() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM collection_tasks').fetchone()[0], 1)
            sources = conn.execute('SELECT * FROM sources').fetchall()
            self.assertEqual(len(sources), 1)
            source = sources[0]
            self.assertEqual((source['project_id'], source['name'], source['platform'], source['kind'],
                              source['address'], source['minutes'], source['title_field'], source['body_field']),
                             (project, '订阅', '订阅', 'rss', 'https://example.org/feed', 60, 'title', 'body'))
            self.assertIsNone(conn.execute("SELECT id FROM projects WHERE name='重复项目'").fetchone())

    def test_failed_setup_keeps_active_workspace_and_allows_same_directory_retry(self):
        original = app.data_directory()
        target = original / 'moved'
        response = self.setup(directory=str(target), collection=dict(platform='x', mode='account', query='home'))
        self.assertEqual(response.status_code, 422)
        self.assertEqual(app.data_directory(), original)
        self.assertFalse((target / 'workbench.sqlite3').exists())
        self.assertFalse(self.client.get('/api/setup').json()['complete'])
        self.setup(directory=str(target)).raise_for_status()
        self.assertEqual(app.data_directory(), target)

    def test_concurrent_setup_uses_only_winning_directory_and_project(self):
        root = Path(self.folder.name).resolve()
        def submit(name):
            response = self.setup(name=name, directory=str(root / name))
            response.raise_for_status()
            return response.json()
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(submit, ['甲', '乙']))
        self.assertCountEqual([row['created'] for row in results], [True, False])
        self.assertEqual(results[0]['project_id'], results[1]['project_id'])
        winner, loser = ('甲', '乙') if results[0]['created'] else ('乙', '甲')
        self.assertEqual(app.data_directory(), root / winner)
        self.assertFalse((root / loser).exists())
        with app.database() as conn:
            self.assertEqual(conn.execute('SELECT name FROM projects WHERE id=?', (results[0]['project_id'],)).fetchone()[0], winner)
            self.assertEqual(conn.execute('SELECT count(*) FROM collection_tasks').fetchone()[0], 1)

    def test_legacy_only_and_explicit_null_collection(self):
        response = self.setup(collection=None, platform='Notion', source_kind='notion', source_address='database-id')
        self.assertEqual(response.status_code, 200, response.text)
        with app.database() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM collection_tasks').fetchone()[0], 0)
            source = conn.execute('SELECT * FROM sources').fetchone()
            self.assertEqual((source['kind'], source['title_field'], source['body_field']), ('notion', 'Name', 'Body'))

    def test_invalid_legacy_source_leaves_no_collection_or_project(self):
        response = self.setup(source_address='https://example.org/feed')
        self.assertEqual(response.status_code, 422, response.text)
        with app.database() as conn:
            self.assertIsNone(conn.execute("SELECT id FROM projects WHERE name='首个项目'").fetchone())
            for table in ('onboarding', 'sources', 'collection_tasks'):
                self.assertEqual(conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)
