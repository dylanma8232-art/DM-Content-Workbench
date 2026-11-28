import json
from uuid import uuid4

from test_workbench import WorkbenchFixture


class PlatformAccountManagementTests(WorkbenchFixture):
    base = '/api/projects/1'
    old = {'platform': 'bilibili', 'account': '编辑部'}
    new = {'platform': 'xiaohongshu', 'account': '主账号'}

    def record(self, base, identity, day, views):
        self.client.put(base + '/account-daily', json={
            **identity, 'day': day, 'followers': 12, 'views': views, 'engagements': 1, 'published': 2,
        }).raise_for_status()

    def report(self, base):
        return self.client.get(base + '/account-daily', params={
            'start': '2026-01-01', 'day': '2026-01-31',
        }).json()

    def rename(self):
        return self.client.put(self.base + '/platform-accounts/manage', json={
            **self.old, 'new_platform': self.new['platform'], 'new_account': self.new['account'],
        })

    def delete(self, identity):
        return self.client.request('DELETE', self.base + '/platform-accounts/manage', json=identity)

    def test_new_report_invalidates_deletion_confirmation(self):
        self.record(self.base, self.old, '2026-01-02', 7)
        result = self.client.request('DELETE', self.base + '/platform-accounts/manage?expected_reports=0', json=self.old)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.client.get(self.base + '/account-impact', params=self.old).json()['reports'], 1)

    def test_move_then_delete_preserves_other_project_and_publishing_snapshot(self):
        project = self.client.post('/api/projects', json={'name': '另一项目'}).json()['id']
        other = f'/api/projects/{project}'
        for base in (self.base, other):
            self.client.put(base + '/platform-accounts', json=self.old).raise_for_status()
            self.record(base, self.old, '2026-01-01', 8)
            self.record(base, self.old, '2026-01-31', 5)
        self.record(other, self.new, '2026-01-01', 17)
        other_before = self.report(other)
        before = self.report(self.base)
        publishing = self.base + '/publishing'
        self.client.post(publishing, data={'request_id': str(uuid4()), 'title': '标题',
            'body': '正文', 'targets': json.dumps([self.old])}).raise_for_status()
        snapshot = self.client.get(publishing).json()['posts']

        self.rename().raise_for_status()
        self.assertEqual(self.report(self.base), {**before, 'accounts': [{**before['accounts'][0], **self.new}]})
        self.delete(self.new).raise_for_status()
        self.assertEqual(self.report(self.base), {'accounts': [], 'trend': []})
        self.assertEqual(self.report(other), other_before)
        self.assertEqual(self.client.get(publishing).json()['posts'], snapshot)

    def test_history_only_source_and_target_even_on_different_days(self):
        self.record(self.base, self.old, '2026-01-01', 4)
        self.record(self.base, self.new, '2026-01-31', 9)
        before = self.report(self.base)
        self.assertEqual(self.rename().status_code, 409)
        self.assertEqual(self.report(self.base), before)
        self.delete(self.new).raise_for_status()
        self.rename().raise_for_status()
        self.assertEqual(self.report(self.base)['accounts'], [{**before['accounts'][0], **self.new}])
        self.assertEqual(self.rename().status_code, 404)
        self.assertEqual(self.delete(self.old).status_code, 404)

    def test_registered_target_without_reports_blocks_move(self):
        for identity in (self.old, self.new):
            self.client.put(self.base + '/platform-accounts', json=identity).raise_for_status()
        before = self.report(self.base)
        self.assertEqual(self.rename().status_code, 409)
        self.assertEqual(self.report(self.base), before)
