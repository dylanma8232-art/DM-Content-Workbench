from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time
from unittest.mock import patch

import app

import httpx
from test_workbench import WorkbenchFixture


class ConnectorTests(WorkbenchFixture):
    def test_preview_then_atomic_import(self):
        path = "/api/projects/1/connectors/reports"
        config = {"url": "https://example.org/reports", "records_path": "data.items"}
        self.client.put(path, json=config).raise_for_status()
        rows = [
            {
                "platform": "书评",
                "account": "编辑部",
                "day": "2026-01-01",
                "followers": 3,
                "views": 5,
                "engagements": 1,
                "published": 1,
            }
        ]
        with patch("connections.json_records", return_value=rows):
            preview = self.client.post(path + "/preview").json()
            self.assertEqual(preview["count"], 1)
            payload = {"preview_id": preview["preview_id"]}
            dest = "/api/projects/1/account-daily?day=2026-01-01"
            self.assertEqual(self.client.get(dest).json()["accounts"], [])
            rows[0]["views"] = 6
            self.assertEqual(
                self.client.post(path + "/import", json=payload).status_code, 409
            )
            rows[0]["views"] = 5
            imported=self.client.post(path + "/import", json=payload)
            imported.raise_for_status()
            self.assertEqual(imported.json()['latest'],'2026-01-01')
            self.assertEqual(self.client.get(dest).json()["accounts"][0]["views"], 5)
            rows.append(rows[0])
            self.assertEqual(self.client.post(path + "/preview").status_code, 422)

    def test_manual_and_hourly_sync_are_project_scoped(self):
        base = '/api/projects/1'
        endpoint = base + '/connectors/reports'
        state = base + '/report-sync'
        other = self.client.post('/api/projects', json={'name': '独立日报'}).json()['id']
        self.assertFalse(self.client.get(state).json()['configured'])
        self.assertEqual(self.client.put(state, json={'enabled': True}).status_code, 409)
        self.assertEqual(self.client.post(endpoint + '/sync').status_code, 409)
        self.client.put(endpoint, json={'url': 'https://example.org/daily'}).raise_for_status()
        rows = [dict(platform='平台', account='编辑', day='2026-01-01', followers=1,
                     views=2, engagements=3, published=4)]
        with patch('connections.json_records', return_value=rows) as fetch:
            app.app.state.sync_due_reports(Event())
            fetch.assert_not_called()
            result = self.client.post(endpoint + '/sync')
            result.raise_for_status()
            self.assertEqual(result.json()['count'], 1)
            first = self.client.get(state).json()
            self.assertTrue(first['configured'])
            self.assertFalse(first['enabled'])
            self.assertIsInstance(first['last_success'], str)
            rows.append(rows[0])
            self.client.put(state, json={'enabled': True}).raise_for_status()
            app.app.state.sync_due_reports(Event())
            failed = self.client.get(state).json()
            self.assertEqual(first['last_success'], failed['last_success'])
            self.assertIn('重复', failed['message'])
            app.app.state.sync_due_reports(Event())
            self.assertEqual(fetch.call_count, 2)
            rows.pop()
            with app.database() as conn:
                conn.execute('UPDATE report_sync SET due=0 WHERE project_id=1')
            rows[0]['views'] = 7
            app.app.state.sync_due_reports(Event())
            self.assertEqual(fetch.call_count, 3)
            query = '/account-daily?day=2026-01-01'
            self.assertEqual(self.client.get(base + query).json()['accounts'][0]['views'], 7)
            self.assertEqual(self.client.get(f'/api/projects/{other}' + query).json()['accounts'], [])
            self.assertFalse(self.client.get(f'/api/projects/{other}/report-sync').json()['enabled'])

    def test_running_workbench_polls_and_persists_next_hour(self):
        endpoint = '/api/projects/1/connectors/reports'
        state = '/api/projects/1/report-sync'
        self.client.put(endpoint, json={'url': 'https://example.org/daily'}).raise_for_status()
        with (patch('integrations.REPORT_POLL', 0.02),
              patch('connections.json_records', return_value=[]) as fetch,
              type(self.client)(app.app) as running):
            before = time.time()
            running.put(state, json={'enabled': True}).raise_for_status()
            deadline = time.monotonic() + 3
            while not running.get(state).json()['last_success']:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.02)
            with app.database() as conn:
                due = conn.execute('SELECT due FROM report_sync WHERE project_id=1').fetchone()[0]
            self.assertGreaterEqual(due, before + 3600)
            self.assertLessEqual(due, time.time() + 3600)
            fetch.assert_called_once()

    def test_inflight_sync_cannot_commit_after_config_change_or_disable(self):
        endpoint = '/api/projects/1/connectors/reports'
        state = '/api/projects/1/report-sync'
        original = {'url': 'https://example.org/old'}
        self.client.put(endpoint, json=original).raise_for_status()
        for automatic in (False, True):
            started, release = Event(), Event()
            def fetch(*args):
                started.set()
                self.assertTrue(release.wait(5))
                return [dict(platform='A', account='B', day='2026-01-01', followers=1,
                             views=2, engagements=3, published=4)]
            with patch('connections.json_records', side_effect=fetch), ThreadPoolExecutor(1) as pool:
                if automatic:
                    self.client.put(state, json={'enabled': True}).raise_for_status()
                    task = pool.submit(app.app.state.sync_due_reports, Event())
                else:
                    task = pool.submit(self.client.post, endpoint + '/sync')
                try:
                    self.assertTrue(started.wait(5))
                    self.assertEqual(self.client.post(endpoint + '/sync').status_code, 409)
                    if automatic:
                        self.client.put(state, json={'enabled': False}).raise_for_status()
                    else:
                        self.client.put(endpoint, json={'url': 'https://example.org/new'}).raise_for_status()
                        self.client.put(endpoint, json=original).raise_for_status()
                finally:
                    release.set()
                result = task.result()
                if not automatic:
                    self.assertEqual(result.status_code, 409)
            self.assertIsNone(self.client.get(state).json()['last_success'])
            self.assertEqual(self.client.get('/api/projects/1/account-daily?day=2026-01-01').json()['accounts'], [])

    def approved_webhook(self):
        run = self.create()
        base = "/api/runs/" + str(run)
        for stage in range(1, 5):
            self.client.post(
                base + "/advance", json={"stage": stage, "topic_id": 1}
            ).raise_for_status()
        path = base + "/webhook/1/"
        self.client.put(
            "/api/projects/1/connectors/drafts",
            json={"url": "https://example.org/publish"},
        ).raise_for_status()
        self.assertEqual(self.client.post(path + "preview").status_code, 409)
        self.client.post(
            base + "/review", json={"version": 1, "decision": "通过", "note": ""}
        ).raise_for_status()
        response = self.client.post(path + "preview").json()
        self.assertEqual(response["payload"]["event"], "draft.approved")
        payload = {"preview_id": response["preview_id"]}
        return base, path, response, payload

    def test_only_approved_version_and_send_once(self):
        _, path, response, payload = self.approved_webhook()
        with (
            patch("connections.public_endpoint"),
            patch("integrations.httpx.stream") as delivery,
        ):
            delivery.return_value.__enter__.return_value = httpx.Response(
                200, request=httpx.Request("POST", response["target"])
            )
            self.client.post(path + "send", json=payload).raise_for_status()
            self.client.post(path + "send", json=payload).raise_for_status()
            delivery.assert_called_once()
            self.assertEqual(
                delivery.call_args.kwargs["headers"]["Idempotency-Key"],
                payload["preview_id"],
            )

    def test_revoked_before_authorization_and_changed_preview(self):
        base, path, _, payload = self.approved_webhook()
        with patch('integrations.httpx.stream') as send:
            self.client.put('/api/projects/1/connectors/drafts', json={'url':'https://example.org/other'}).raise_for_status()
            self.assertEqual(self.client.post(path + 'send', json=payload).status_code, 409)
            key = self.client.post(path + 'preview').json()['preview_id']
            self.client.post(base + '/review', json={'version':1,'decision':'退回','expected_review_count':1}).raise_for_status()
            self.assertEqual(self.client.post(path + 'send', json={'preview_id':key}).status_code, 409)
            send.assert_not_called()
            self.assertEqual(self.client.get(base + '/webhook-receipts').json(), [])

    def test_authorized_before_revocation(self):
        base, path, preview, payload = self.approved_webhook()
        started, release = Event(), Event()
        def receive(*args, **kwargs):
            started.set()
            self.assertTrue(release.wait(5))
            from contextlib import nullcontext
            return nullcontext(httpx.Response(200, request=httpx.Request('POST', preview['target'])))
        with patch('connections.public_endpoint'), patch('integrations.httpx.stream', side_effect=receive) as send, ThreadPoolExecutor(1) as pool:
            pending = pool.submit(self.client.post, path + 'send', json=payload)
            try:
                self.assertTrue(started.wait(5))
                self.client.post(base + '/review', json={'version':1,'decision':'退回','expected_review_count':1}).raise_for_status()
                snapshot, = self.client.get(base + '/webhook-receipts').json()
                self.assertEqual((snapshot['state'], snapshot['approval']['decision'], snapshot['version'], snapshot['target']),
                                 ('发送中', '通过', 1, preview['target']))
            finally:
                release.set()
            pending.result().raise_for_status()
            send.assert_called_once()
        final, = self.client.get(base + '/webhook-receipts').json()
        self.assertEqual(final['state'], '已送达')
        self.assertEqual(final['approval'], snapshot['approval'])

    def test_uncertain_requires_attempt_specific_manual_retry(self):
        base, path, preview, payload = self.approved_webhook()
        with patch('connections.public_endpoint'), patch('integrations.httpx.stream') as send:
            processed = set()
            def receiver(*args, headers, **kwargs):
                key = headers['Idempotency-Key']
                if key not in processed:
                    processed.add(key)
                    raise httpx.ReadTimeout('接收端已处理，响应超时')
                from contextlib import nullcontext
                return nullcontext(httpx.Response(200, request=httpx.Request('POST', preview['target'])))
            send.side_effect = receiver
            self.assertEqual(self.client.post(path + 'send', json=payload).status_code, 502)
            attempt, = self.client.get(base + '/webhook-receipts').json()
            self.assertEqual(attempt['state'], '送达待核对')
            self.assertEqual(self.client.post(path + 'send', json=payload).status_code, 409)
            send.assert_called_once()
            retry = self.client.post(path + 'preview').json()['checked_attempt']
            self.assertEqual(retry, attempt['id'])
            self.client.post(path + 'send', json=payload | {'checked_attempt':retry}).raise_for_status()
            self.assertEqual(processed, {payload['preview_id']})
            self.assertEqual([call.kwargs['headers']['Idempotency-Key'] for call in send.call_args_list], [payload['preview_id']] * 2)
            self.assertEqual([row['state'] for row in self.client.get(base + '/webhook-receipts').json()], ['已送达', '送达待核对'])
