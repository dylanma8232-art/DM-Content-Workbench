import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import draft_archive as archive
import online_libraries as libraries
import app
from test_workbench import WorkbenchFixture


class RestorePreviewTests(WorkbenchFixture):
    def test_preview_binds_project_page_and_remote_version(self):
        run_id = self.create()
        base = f'/api/runs/{run_id}'
        for stage in range(1, 5):
            self.client.post(base + '/advance', json={'stage':stage,'topic_id':1}).raise_for_status()
        original = self.client.get(base).json()
        snapshot = app.notion_snapshot(original)
        path = '/api/notion/restore'
        form = {'project_id':1, 'page_id':'a' * 32}
        with patch('app.read_snapshot', return_value=snapshot) as read:
            preview = self.client.post(path + '?preview=true', json=form).json()
            self.assertEqual((preview['project_id'],preview['version'],preview['name']), (1,1,original['name']))
            confirmed = form | {'preview_id':preview['preview_id']}
            other = self.client.post('/api/projects',json={'name':'其他项目'}).json()['id']
            self.assertEqual(self.client.post(path,json=confirmed | {'project_id':other}).status_code, 409)
            self.assertEqual(self.client.post(path,json=confirmed | {'page_id':'b' * 32}).status_code, 409)
            read.return_value = snapshot + ' '
            self.assertEqual(self.client.post(path,json=confirmed).status_code, 409)
            read.return_value = snapshot
            restored = self.client.post(path,json=confirmed)
            restored.raise_for_status()
            self.assertNotEqual(restored.json()['id'],run_id)
            self.assertEqual(self.client.get(base).json()['payload']['drafts'],original['payload']['drafts'])


class DraftArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(self.temp) / 'archive.sqlite3'
        self.calls = []
        self.handler = self.notion
        self.settings = {'archive_library_id': 1}
        self.content = '# 标题\n\n完整 Markdown 正文\n**加粗**\n'
        real_client = httpx.Client
        self.enterContext(patch.object(archive.httpx, 'Client', side_effect=lambda **kw: real_client(
            transport=httpx.MockTransport(self.dispatch), **kw)))
        api = FastAPI()
        archive.mount(api, self.database, self.get_run, self.project_settings)
        self.api = self.enterContext(TestClient(api))
        with self.database() as conn:
            conn.execute('CREATE TABLE runs(id INTEGER PRIMARY KEY, project_id INTEGER, stage INTEGER, payload TEXT, name TEXT)')
            conn.execute('INSERT INTO runs VALUES(1,1,5,?,?)', (json.dumps({
                'drafts': [self.content], 'reviews': [{'version': 1, 'decision': '通过'}],
                'topic': {'title': '归档标题'}}, ensure_ascii=False), '示例'))
            archive._schema(conn)
            for library_id, project, provider in ((1, 1, 'notion'), (2, 1, 'feishu'), (3, 2, 'notion')):
                conn.execute('''INSERT INTO online_libraries
                    (id,project_id,provider,name,address,title_field,body_field,app_id,table_id,status)
                    VALUES(?,?,?,?,?,?,?,?,?,?)''', (library_id, project, provider, '资料库',
                    'https://team.feishu.cn/base/AppToken?table=tblABC' if provider == 'feishu' else 'a' * 32,
                    '标题', '正文', 'app-id', 'tblABC', 'configured'))
                conn.execute('INSERT INTO online_library_secrets VALUES(?,?,?)',
                             (library_id, 'notion-private-key', 'feishu-private-key'))

    @contextmanager
    def database(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def get_run(self, conn, run_id):
        row = conn.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if not row:
            raise HTTPException(404, '稿件不存在')
        return dict(row) | {'payload': json.loads(row['payload'])}

    def project_settings(self, conn, project):
        if project not in (1, 2):
            raise HTTPException(404, '项目不存在')
        return self.settings if project == 1 else {}

    def dispatch(self, request):
        self.calls.append(request)
        return self.handler(request)

    def notion(self, request):
        self.assertEqual(request.url.host, 'api.notion.com')
        self.assertEqual(request.headers['authorization'], 'Bearer notion-private-key')
        if request.method == 'GET':
            return httpx.Response(200, json={'properties': {
                '标题': {'type': 'title'}, '正文': {'type': 'rich_text'}}})
        return httpx.Response(200, json={'id': 'b' * 32, 'url': 'https://www.notion.so/' + 'b' * 32})

    def feishu(self, request):
        self.assertEqual(request.url.host, 'open.feishu.cn')
        if '/auth/' in request.url.path:
            return httpx.Response(200, json={'code': 0, 'tenant_access_token': 'tenant-private-key'})
        self.assertEqual(request.headers['authorization'], 'Bearer tenant-private-key')
        if request.method == 'GET':
            return httpx.Response(200, json={'code': 0, 'data': {'items': [
                {'field_name': '标题', 'type': 1}, {'field_name': '正文', 'type': 1}], 'has_more': False}})
        return httpx.Response(200, json={'code': 0, 'data': {'record': {'record_id': 'recABC123'}}})

    def post(self, **changes):
        return self.api.post('/api/runs/1/archive', json={'version': 1} | changes)

    def change_payload(self, change):
        with self.database() as conn:
            run = self.get_run(conn, 1)
            change(run['payload'])
            conn.execute('UPDATE runs SET payload=? WHERE id=1', (json.dumps(run['payload']),))

    def creates(self):
        return [r for r in self.calls if r.method == 'POST' and '/auth/' not in r.url.path]

    def test_notion_full_content_receipt_and_duplicate(self):
        self.content = '# 长文\n' + '中\n**abc**' * 900
        self.change_payload(lambda data: data.update(drafts=[self.content]))
        response = self.post()
        self.assertEqual(response.status_code, 200, response.text)
        receipt = response.json()
        self.assertEqual(receipt['state'], 'sent')
        self.assertEqual(receipt['remote_url'], 'https://www.notion.so/' + 'b' * 32)
        body = json.loads(self.creates()[0].content)
        self.assertEqual(body['parent'], {'database_id': 'a' * 32})
        spans = body['properties']['正文']['rich_text']
        self.assertEqual(''.join(s['text']['content'] for s in spans), self.content)
        blocks = body['children']
        self.assertLessEqual(len(blocks), 100)
        self.assertEqual(''.join(s['text']['content'] for b in blocks for s in b['paragraph']['rich_text']), self.content)
        self.assertTrue(all(len(s['text']['content']) <= 2000 for s in spans))
        self.assertEqual(self.post().json(), receipt)
        self.assertEqual(len(self.creates()), 1)
        listing = self.api.get('/api/projects/1/draft-archives')
        self.assertEqual(listing.json(), [receipt])
        self.assertEqual(self.api.get('/api/projects/2/draft-archives').json(), [])
        self.assertEqual(self.api.get('/api/projects/999/draft-archives').status_code, 404)
        for secret in ('notion-private-key', 'feishu-private-key', 'tenant-private-key'):
            self.assertNotIn(secret, response.text + listing.text)

    def test_feishu_fields_preserve_markdown_and_provided_host(self):
        self.handler = self.feishu
        result = self.post(library_id=2)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['state'], 'sent')
        self.assertEqual(result.json()['remote_url'], 'https://team.feishu.cn/base/AppToken?table=tblABC&record=recABC123')
        self.assertEqual(json.loads(self.creates()[0].content)['fields'], {'标题': '归档标题', '正文': self.content})
        self.assertEqual(self.post(library_id=2).json()['id'], result.json()['id'])
        self.assertEqual(len(self.creates()), 1)
        self.assertNotIn('private-key', result.text)

    def test_unapproved_mismatch_and_revoked_never_contact_remote(self):
        for reviews, drafts in (([], [self.content]),
                                ([{'version': 1, 'decision': '退回'}], [self.content]),
                                ([{'version': 1, 'decision': '通过'}], [self.content, '新稿']),
                                ([{'version': 1, 'decision': '通过'}, {'version': 1, 'decision': '退回'}], [self.content]),
                                ([{'version': 1, 'decision': '通过'}, {'version': 2, 'decision': '通过'}], [self.content])):
            self.change_payload(lambda data: data.update(drafts=drafts, reviews=reviews))
            self.assertEqual(self.post().status_code, 409)
        self.assertEqual(self.calls, [])

    def test_revocation_after_sent_still_returns_conflict(self):
        self.assertEqual(self.post().json()['state'], 'sent')
        self.change_payload(lambda data: data['reviews'].append({'version': 1, 'decision': '退回'}))
        self.assertEqual(self.post().status_code, 409)
        self.assertEqual(len(self.creates()), 1)

    def test_no_target_cross_project_bad_project_and_stage(self):
        self.settings = {}
        self.assertEqual(self.post().status_code, 422)
        self.assertEqual(self.post(library_id=3).status_code, 404)
        self.assertEqual(self.api.post('/api/runs/999/archive', json={'version': 1}).status_code, 404)
        with self.database() as conn:
            conn.execute('UPDATE runs SET project_id=999 WHERE id=1')
        self.assertEqual(self.post(library_id=1).status_code, 404)
        with self.database() as conn:
            conn.execute('UPDATE runs SET project_id=1,stage=4 WHERE id=1')
        self.assertEqual(self.post(library_id=1).status_code, 409)
        self.assertEqual(self.calls, [])

    def test_timeout_uncertain_blocks_second_create_and_survives_new_mount(self):
        def timeout(request):
            if request.method == 'POST':
                raise httpx.ReadTimeout('secret notion-private-key', request=request)
            return self.notion(request)
        self.handler = timeout
        result = self.post()
        self.assertEqual(result.json()['state'], 'uncertain')
        self.assertNotIn('notion-private-key', result.text)
        api = FastAPI()
        archive.mount(api, self.database, self.get_run, self.project_settings)
        with TestClient(api) as client:
            self.assertEqual(client.post('/api/runs/1/archive', json={'version': 1}).json()['state'], 'uncertain')
        self.assertEqual(len(self.creates()), 1)

    def test_sending_receipt_survives_interruption(self):
        def during(request):
            with self.database() as conn:
                self.assertEqual(conn.execute('SELECT state FROM draft_archives').fetchone()[0], 'sending')
            return self.notion(request)
        self.handler = during
        self.assertEqual(self.post().json()['state'], 'sent')
        with self.database() as conn:
            conn.execute("UPDATE draft_archives SET state='sending'")
        self.assertEqual(self.post().json()['state'], 'sending')
        self.assertEqual(len(self.creates()), 1)

    def test_definitive_http_failure_allows_retry_without_echoing_secrets(self):
        self.handler = lambda r: self.notion(r) if r.method == 'GET' else httpx.Response(
            403, json={'message': 'notion-private-key'})
        response = self.post()
        self.assertEqual(response.json()['state'], 'failed')
        receipt, = self.api.get('/api/projects/1/draft-archives').json()
        self.assertEqual((receipt['target_name'], receipt['library_id'], receipt['version']), ('资料库', 1, 1))
        with self.database() as conn:
            self.assertEqual(self.get_run(conn, 1)['payload']['drafts'][0], self.content)
        self.assertNotIn('notion-private-key', response.text)
        self.handler = self.notion
        self.assertEqual(self.post().json()['state'], 'sent')
        self.assertEqual(len(self.creates()), 2)

    def test_ambiguous_server_or_malformed_success_blocks_retry(self):
        for status, body in ((503, {}), (408, {}), (200, {})):
            with self.subTest(status=status, body=body):
                with self.database() as conn:
                    conn.execute('DELETE FROM draft_archives')
                self.calls.clear()
                self.handler = lambda r: self.notion(r) if r.method == 'GET' else httpx.Response(status, json=body)
                self.assertEqual(self.post().json()['state'], 'uncertain')
                self.assertEqual(self.post().json()['state'], 'uncertain')
                self.assertEqual(len(self.creates()), 1)

    def test_schema_validation_before_create(self):
        for properties in ({'标题': {'type': 'rich_text'}, '正文': {'type': 'rich_text'}},
                           {'标题': {'type': 'title'}},
                           {'标题': {'type': 'title'}, '正文': {'type': 'formula'}}):
            self.handler = lambda r: httpx.Response(200, json={'properties': properties})
            self.assertEqual(self.post().status_code, 422)
        self.assertEqual(self.creates(), [])

    def test_feishu_body_required_and_schema_types_checked(self):
        with self.database() as conn:
            conn.execute("UPDATE online_libraries SET body_field='' WHERE id=2")
        self.assertEqual(self.post(library_id=2).status_code, 422)
        self.assertEqual(self.calls, [])
        with self.database() as conn:
            conn.execute("UPDATE online_libraries SET body_field='正文' WHERE id=2")
        for field in ({'field_name': '正文', 'type': 20}, {'field_name': '正文', 'type': 1, 'is_read_only': True}):
            self.handler = lambda r: httpx.Response(200, json={'code': 0, 'data': {'items': [
                {'field_name': '标题', 'type': 1}, field]}}) if r.method == 'GET' else self.feishu(r)
            self.assertEqual(self.post(library_id=2).status_code, 422)
        self.assertEqual(self.creates(), [])

    def test_large_content_and_payload_limits_reject_without_truncation(self):
        self.change_payload(lambda data: data.update(drafts=['中' * 100001]))
        result = self.post()
        self.assertEqual(result.status_code, 422)
        self.assertIn('Markdown', result.text)
        self.assertEqual(self.calls, [])
        self.change_payload(lambda data: data.update(drafts=['中' * 100000]))
        self.assertEqual(self.post().status_code, 422)  # repeated body + blocks exceeds 500 KB
        self.assertEqual(self.creates(), [])
        # 100000 ASCII characters fit and are preserved exactly in both locations.
        content = 'x' * 100000
        self.change_payload(lambda data: data.update(drafts=[content]))
        with self.database() as conn:
            conn.execute('DELETE FROM draft_archives')
        self.assertEqual(self.post().json()['state'], 'sent')
        self.assertEqual(''.join(s['text']['content'] for s in json.loads(self.creates()[0].content)['properties']['正文']['rich_text']), content)

    def test_revocation_during_schema_read_blocks_remote_write(self):
        def revoke(request):
            response = self.notion(request)
            self.change_payload(lambda data: data['reviews'].append({'version': 1, 'decision': '退回'}))
            return response
        self.handler = revoke
        self.assertEqual(self.post().status_code, 409)
        self.assertEqual(self.creates(), [])
        self.assertEqual(self.api.get('/api/projects/1/draft-archives').json()[0]['state'], 'failed')

    def test_no_redirect_follow_and_untrusted_url_not_exposed(self):
        self.handler = lambda r: self.notion(r) if r.method == 'GET' else httpx.Response(
            200, json={'id': 'b' * 32, 'url': 'https://attacker.example/notion-private-key'})
        response = self.post()
        self.assertEqual(response.json()['state'], 'sent')
        self.assertEqual(response.json()['remote_url'], '')
        self.assertNotIn('notion-private-key', response.text)
        with self.database() as conn:
            conn.execute('DELETE FROM draft_archives')
        self.calls.clear()
        self.handler = lambda r: httpx.Response(302, headers={'Location': 'https://attacker.example'})
        self.assertEqual(self.post().json()['state'], 'failed')
        self.assertEqual(len(self.calls), 1)

    def test_changed_hash_cannot_reuse_receipt(self):
        self.assertEqual(self.post().json()['state'], 'sent')
        self.change_payload(lambda data: data.update(drafts=['被原地修改的草稿']))
        self.assertEqual(self.post().status_code, 409)
        self.assertEqual(len(self.creates()), 1)

    def test_invalid_input_never_echoes_input_or_makes_requests(self):
        for body in ({'version': True}, {'version': 0}, {'version': 'notion-private-key'},
                     {'version': 1, 'library_id': None}, {'version': 1, 'token': 'notion-private-key'}, []):
            response = self.api.post('/api/runs/1/archive', json=body)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn('notion-private-key', response.text)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
