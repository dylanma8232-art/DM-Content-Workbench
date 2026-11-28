import os
import unittest
from datetime import date
from unittest.mock import patch

from test_workbench import WorkbenchFixture


class AccountDailyTests(WorkbenchFixture):
    path = '/api/projects/1/account-daily'

    def record(self, day, account, followers, views, engagements=0, published=0):
        self.client.put(self.path, json={
            'platform': 'blog', 'account': account, 'day': day, 'followers': followers,
            'views': views, 'engagements': engagements, 'published': published,
        }).raise_for_status()

    def report(self, **params):
        response = self.client.get(self.path, params=params)
        response.raise_for_status()
        return response.json()

    def test_sparse_range_sums_flows_and_uses_latest_followers_per_account(self):
        self.record('2026-01-01', 'B', 4, 1)
        self.record('2026-01-02', 'A', 15, 100)
        self.record('2026-01-03', 'A', 20, 7, 1, 2)
        self.record('2026-01-03', 'B', 10, 2)
        self.record('2026-01-07', 'A', 13, 5, 2, 3)
        self.record('2026-01-12', 'B', 0, 0)
        self.record('2026-01-13', 'A', 30, 200)
        self.client.put('/api/projects/1/platform-accounts', json={
            'platform': 'blog', 'account': 'C',
        }).raise_for_status()

        result = self.report(start='2026-01-03', day='2026-01-12')
        self.assertEqual(result['accounts'], [
            dict(platform='blog', account='A', followers=13, views=12, engagements=3, published=5, growth=-2),
            dict(platform='blog', account='B', followers=0, views=2, engagements=0, published=0, growth=None),
            dict(platform='blog', account='C', followers=None, views=None, engagements=None, published=None, growth=None),
        ])
        self.assertEqual(result['trend'], [
            dict(day='2026-01-03', views=9, reported=2),
            dict(day='2026-01-07', views=5, reported=1),
            dict(day='2026-01-12', views=0, reported=1),
        ])

    def test_single_day_preserves_daily_metrics_and_trailing_week(self):
        self.record('2026-01-01', 'A', 1, 2)
        self.record('2026-01-02', 'A', 2, 4)
        self.record('2026-01-07', 'A', 5, 7)
        self.record('2026-01-08', 'A', 9, 10, 2, 1)
        result = self.report(day='2026-01-08')
        self.assertEqual(result, self.report(day='2026-01-08', start='2026-01-08'))
        self.assertEqual(result['accounts'], [
            dict(platform='blog', account='A', followers=9, views=10, engagements=2, published=1, growth=4),
        ])
        self.assertEqual(result['trend'], [
            dict(day='2026-01-02', views=4, reported=1),
            dict(day='2026-01-07', views=7, reported=1),
            dict(day='2026-01-08', views=10, reported=1),
        ])

    douyin_path = '/api/projects/1/douyin-daily'
    douyin = dict(account='账号甲', platform_uid='uid-a', day='2026-01-08', followers=28,
                  published=0, views=280, likes=1, comments=0, shares=0)

    def test_douyin_complete_report_retries_updates_and_range(self):
        for _ in range(2):
            self.client.post(self.douyin_path, json=self.douyin).raise_for_status()
        expected = dict(platform='抖音', account='账号甲', followers=28, published=0,
                        views=280, engagements=1, growth=None)
        daily = self.report(day=self.douyin['day'])
        self.assertEqual(daily['accounts'], [expected])
        self.assertEqual(daily['trend'], [dict(day=self.douyin['day'], views=280, reported=1)])
        self.client.post(self.douyin_path, json=dict(self.douyin, comments=2, shares=3)).raise_for_status()
        self.client.post(self.douyin_path, json=dict(self.douyin, day='2026-01-09', followers=30)).raise_for_status()
        result = self.report(start=self.douyin['day'], day='2026-01-09')
        self.assertEqual(result['accounts'], [dict(expected, followers=30, views=560, engagements=7)])

    def test_douyin_validates_before_binding(self):
        invalid = [dict(self.douyin, **{key: value}) for key, value in (
            ('followers', None), ('published', -1), ('likes', True), ('comments', '1'), ('views', 1.5),
            ('shares', 10**12+1), ('day', '2026-02-30'), ('day', '20260108'), ('platform_uid', ' '),
            ('account', ' '), ('platform', '其他'))]
        invalid.extend({k: v for k, v in self.douyin.items() if k != missing}
                       for missing in ('followers', 'published'))
        for body in invalid:
            with self.subTest(body=body):
                self.assertEqual(self.client.post(self.douyin_path, json=body).status_code, 422)
        self.assertEqual(self.report(day=self.douyin['day'])['accounts'], [])

    def test_douyin_conflicting_identity_does_not_replace_history(self):
        self.client.put(self.path, json=dict(platform='抖音', account='旧账号', day=self.douyin['day'],
                            followers=99, published=3, views=100, engagements=5)).raise_for_status()
        self.client.post(self.douyin_path, json=self.douyin).raise_for_status()
        before = self.report(day=self.douyin['day'])
        for account, uid in [('旧账号', 'new-uid'), ('账号甲', 'new-uid'), ('另一个名字', 'uid-a')]:
            response = self.client.post(self.douyin_path, json=dict(self.douyin, account=account, platform_uid=uid, views=0))
            self.assertEqual(response.status_code, 409)
        self.assertEqual(self.report(day=self.douyin['day']), before)

    def test_reversed_range_is_rejected(self):
        response = self.client.get(self.path, params={'start': '2026-01-09', 'day': '2026-01-08'})
        self.assertEqual(response.status_code, 422)

    base = '/api/projects/1'

    def register(self, account, day):
        with patch('app.date') as clock:
            clock.today.return_value = date.fromisoformat(day)
            self.client.put(self.base + '/platform-accounts', json=dict(platform='blog', account=account)).raise_for_status()

    def summary(self, start, end):
        result = self.client.get(self.base + '/reporting-summary', params=dict(start=start, day=end))
        result.raise_for_status()
        return result.json()

    def test_coverage_four_due_three_reported_and_unknown_history(self):
        self.register('A', '2026-01-01')
        self.register('B', '2026-01-03')
        for account, day in [('A','01'), ('A','02'), ('B','03')]:
            self.record('2026-01-' + day, account, 0, 0)
        summary = self.summary('2026-01-01','2026-01-03')
        self.assertEqual((summary['expected'],summary['reported'],summary['unknown']), (4,3,0))
        self.assertEqual(summary['missing'], [dict(platform='blog',account='A',day='2026-01-03')])
        self.record('2025-12-01','旧账号',0,0)
        self.client.put(self.base + '/reporting-period', json=dict(platform='blog',account='旧账号',active=True,day='2026-01-04')).raise_for_status()
        old = self.summary('2025-12-01','2025-12-02')
        self.assertEqual((old['unknown'],old['missing'],old['actual'][0]['reports']), (1,[],1))

    def test_complete_comparison_and_platform_weighted_rate_inputs(self):
        for account in ('A','B'):
            self.register(account,'2026-01-01')
        self.record('2026-01-01','A',0,0)
        self.record('2026-01-02','A',1,100,10,2)
        self.record('2026-01-02','B',1,10,9,1)
        current = self.summary('2026-01-02','2026-01-02')
        self.assertEqual(current['samples'],1)
        self.assertEqual(current['baseline'],dict(views=0,engagements=0,published=0))
        self.assertEqual(current['differences'],dict(views=100,engagements=10,published=2))
        self.assertEqual(self.summary('2026-01-01','2026-01-01')['samples'],0)
        rows = self.report(day='2026-01-02')['accounts']
        self.assertEqual((sum(row['engagements'] for row in rows),sum(row['views'] for row in rows)),(19,110))

    def test_old_name_replay_and_stop_preserve_history(self):
        old = dict(platform='抖音', account=self.douyin['account'])
        self.client.post(self.douyin_path,json=self.douyin).raise_for_status()
        self.client.put(self.base+'/platform-accounts/manage',json=dict(**old,new_platform='抖音',new_account='新名')).raise_for_status()
        self.client.post(self.douyin_path,json=self.douyin).raise_for_status()
        self.record(self.douyin['day'], '新名', 1, 1)
        key = dict(platform='抖音', account='新名')
        for active,day in [(True,'2026-01-08'),(False,'2026-01-09')]:
            self.client.put(self.base+'/reporting-period',json=dict(**key,active=active,day=day)).raise_for_status()
        self.assertEqual(self.summary('2026-01-09','2026-01-10')['missing'],[])
        self.assertEqual(self.client.get(self.base+'/account-impact',params=key).json()['reports'],1)
        self.assertEqual(len(self.report(day=self.douyin['day'])['accounts']),2)

    def test_csv_preview_mixed_errors_and_atomic_overwrite(self):
        self.record('2026-01-01','A',1,1)
        path = self.base + '/account-import'
        payload = dict(platform='blog',account='A',content='日期,粉丝,阅读或播放,互动,发布\n2026-01-01,1,7,0,0\n')
        mixed = dict(payload,content=payload['content']+'2026-01-02,2,oops,0,0\n2026-01-01,1,1,0,0')
        preview = self.client.post(path+'?preview=true',json=mixed).json()
        self.assertFalse(preview['valid'])
        self.assertEqual([(row['line'],row['error']) for row in preview['rows']],[(2,''),(3,'阅读或播放无效'),(4,'日期重复')])
        self.assertEqual(self.client.post(path,json=mixed).status_code,422)
        preview = self.client.post(path+'?preview=true',json=payload).json()
        self.assertEqual(preview['rows'][0]['changes'],{'views':[1,7]})
        self.record('2026-01-01','A',1,2)
        self.assertEqual(self.client.post(path,params={'preview_id':preview['preview_id']},json=payload).status_code,409)
        for status in ['覆盖','相同']:
            preview = self.client.post(path+'?preview=true',json=payload).json()
            self.assertEqual(preview['rows'][0]['status'],status)
            self.client.post(path,params={'preview_id':preview['preview_id']},json=payload).raise_for_status()
        self.assertEqual(self.report(day='2026-01-01')['trend'][0]['views'],7)

    def test_manual_preview_requires_fresh_values_for_overwrite(self):
        self.record('2026-01-01','A',1,10)
        body = dict(platform='blog',account='A',day='2026-01-01',followers=1,views=12,engagements=0,published=0)
        preview = self.client.put(self.path+'?preview=true',json=body).json()
        self.assertEqual(preview['row']['changes'], {'views':[10,12]})
        self.record('2026-01-01','A',1,15)
        self.assertEqual(self.client.put(self.path,params={'preview_id':preview['preview_id']},json=body).status_code,409)
        self.assertEqual(self.report(day=body['day'])['accounts'][0]['views'],15)

    def test_connector_preview_bound_to_project_mapping_and_retry(self):
        other = self.client.post('/api/projects',json={'name':'项目B'}).json()['id']
        paths = [f'/api/projects/{project}/connectors/reports' for project in (1,other)]
        config = dict(url='https://example.org/stats')
        for path in paths:
            self.client.put(path,json=config).raise_for_status()
        row = dict(platform='blog',account='A',day='2026-01-01',views=3,published=0,engagements=2,followers=1)
        with patch('connections.json_records',return_value=[row]):
            key = self.client.post(paths[0]+'/preview').json()['preview_id']
            self.assertEqual(self.client.post(paths[1]+'/import',json=dict(preview_id=key)).status_code,409)
            config['mapping'] = {field:field for field in row}
            config['mapping']['views'] = 'read'
            self.client.put(paths[0],json=config).raise_for_status()
            self.assertEqual(self.client.post(paths[0]+'/preview').status_code,422)
            row['read'] = 5
            self.assertEqual(self.client.post(paths[0]+'/import',json=dict(preview_id=key)).status_code,409)
            key = self.client.post(paths[0]+'/preview').json()['preview_id']
            self.client.post(paths[0]+'/import',json=dict(preview_id=key)).raise_for_status()
            self.assertEqual(self.report(day=row['day'])['accounts'][0]['views'],5)
            status = self.client.get(self.base+'/connectors').json()['reports']
            self.assertEqual(status['mapping']['views'],'read')
            self.assertIn('导入 1 条日报',status['status'])

    @unittest.skipUnless(os.getenv('DM_ACCOUNT_BROWSER_TEST') == '1', '启用 DM_ACCOUNT_BROWSER_TEST=1 运行 Chrome 验收')
    def test_browser_rate_csv_and_busy_request_order(self):
        from playwright.sync_api import expect, sync_playwright
        other = self.client.post('/api/setup',json={'name':'日报验收','audience':'编辑','style':'简报'}).json()['project_id']
        self.record('2026-01-02','A',1,100,10)
        self.record('2026-01-02','B',1,10,9)
        self.record('2026-01-01','A',0,0)
        origin = 'http://localhost'
        held = []

        def serve(route):
            request = route.request
            if not request.url.startswith(origin + '/'):
                route.abort()
                return
            response = self.client.request(request.method, request.url[len(origin):],
                                           content=request.post_data, headers={'content-type':'application/json'})
            route.fulfill(status=response.status_code, body=response.content,
                          content_type=response.headers.get('content-type','application/octet-stream'))

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome')
            page = browser.new_page()
            page.route('**/*',serve)
            page.goto(origin)
            expect(page.locator('#accountMetrics')).to_have_attribute('aria-busy','false')
            page.evaluate("async () => {switchProject(1); selectReportDay('2026-01-02'); await home()}")
            expect(page.locator('.accountRoster')).to_contain_text('17.27%')
            expect(page.locator('.accountRoster')).to_contain_text('19 次 / 阅读或播放 110 次')
            page.evaluate("async () => {selectReportDay('2026-01-01'); await home()}")
            expect(page.locator('.accountRoster')).to_contain_text('互动率 —')
            page.route('**/reporting-summary?*',lambda route: held.append(route))
            with page.expect_request('**/reporting-summary?*'):
                page.evaluate('() => {window.firstRead = home()}')
            with page.expect_request('**/reporting-summary?*'):
                page.evaluate('() => {window.secondRead = home()}')
            page.wait_for_function('document.querySelector("#accountMetrics").getAttribute("aria-busy") === "true"')
            self.assertEqual(len(held),2)
            busy = page.locator('#accountMetrics')
            expect(busy).to_have_attribute('aria-busy','true')
            serve(held[0])
            page.evaluate('window.firstRead')
            expect(busy).to_have_attribute('aria-busy','true')
            held[1].fulfill(status=503,json={'detail':'模拟读取失败'})
            page.evaluate('window.secondRead')
            expect(busy).to_have_attribute('aria-busy','false')
            expect(page.locator('#homeIntroText')).to_contain_text('模拟读取失败')
            page.unroute('**/reporting-summary?*')
            page.locator('#addReport').click()
            page.locator('#importDailyCsv').click()
            form = page.locator('#platformSetup')
            form.locator('[data-platform=bilibili]').click()
            form.locator('[name=account]').fill('报表')
            form.locator('[type=file]').set_input_files(dict(name='日报.csv',mimeType='text/csv',
                buffer='日期,粉丝,阅读或播放,互动,发布\n2026-01-02,1,5,0,0'.encode()))
            form.locator('#platformSave').click()
            expect(form.locator('#accountCsvResult')).to_contain_text('第 2 行')
            expect(form.locator('#platformSave')).to_have_text('确认导入')
            form.locator('#platformSave').click()
            expect(form).to_be_hidden()
            expect(busy).to_have_attribute('aria-busy','false')
            expect(busy).to_contain_text('115')
            page.locator('#addReport').click()
            page.locator('#reportForm [name=account]').fill('旧项目输入')
            page.evaluate('async id => {switchProject(id); await home()}',other)
            expect(page.locator('#reportForm')).to_be_hidden()
            expect(page.locator('#reportForm [name=account]')).to_have_value('')
            expect(page.locator('#accountDate')).not_to_have_value('2026-01-02')
            expect(busy).not_to_contain_text('115')
            browser.close()
