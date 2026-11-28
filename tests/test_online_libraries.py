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

import draft_archive
import online_libraries as libraries


class OnlineLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(self.temp) / "test.sqlite3"
        self.requests = []
        self.settings = {}
        self.handler = self.notion
        real_client = httpx.Client
        self.enterContext(patch.object(libraries.httpx, "Client", side_effect=lambda **kw: real_client(
            transport=httpx.MockTransport(self.dispatch), **kw)))
        api = FastAPI()
        libraries.mount(api, self.database, self.project_settings, self.insert_run)
        self.api = self.enterContext(TestClient(api))
        self.base = "/api/projects/1/online-libraries"
        self.form = dict(provider="notion", name="资料库", address="a" * 32,
                         title_field="标题", body_field="正文", token="private-notion-token")

    @contextmanager
    def database(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                conn.execute("CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, project INTEGER, payload TEXT)")
                yield conn
        finally:
            conn.close()

    def project_settings(self, conn, project):
        if project not in (1, 2):
            raise HTTPException(404, "项目不存在")
        return self.settings

    def insert_run(self, conn, name, config, materials):
        payload = {"config": config, "materials": materials}
        run_id = conn.execute("INSERT INTO runs(project,payload) VALUES(?,?)", (config["project_id"], json.dumps(payload))).lastrowid
        return {"id": run_id, "stage": 1, "payload": payload}

    def dispatch(self, request):
        self.requests.append(request)
        return self.handler(request)

    def notion(self, request):
        self.assertEqual(request.url.host, "api.notion.com")
        self.assertEqual(request.headers["Notion-Version"], "2022-06-28")
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={"results": [self.page()], "has_more": False})
        if request.url.path.endswith("/children"):
            return httpx.Response(200, json={"results": [{"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "块正文"}]}}], "has_more": False})
        return httpx.Response(200, json={"properties": {"标题": {}, "正文": {}}})

    def page(self, body="正文内容"):
        return {"id": "b" * 32, "url": "https://notion.so/" + "b" * 32,
                "properties": {"标题": {"title": [{"plain_text": "文章"}, {"plain_text": "标题"}]},
                               "正文": {"rich_text": [{"plain_text": body}]}}}

    def save(self, **changes):
        result = self.api.post(self.base, json=self.form | changes)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def action(self, connection, action):
        return self.api.post(f"{self.base}/{connection['id']}/{action}")

    def test_contract_secret_storage_and_preservation(self):
        saved = self.save()
        self.assertEqual(set(saved), {"id", "provider", "name", "address", "title_field", "body_field", "status", "key_saved", "app_id", "table_id"})
        self.assertTrue(saved["key_saved"])
        self.assertNotIn("private-notion-token", json.dumps(saved))
        self.assertEqual(self.api.get(self.base).json(), [saved])
        self.save(id=saved["id"], token="   ", name="更新")
        with self.database() as conn:
            row = conn.execute("SELECT * FROM online_library_secrets").fetchone()
            self.assertEqual(row["token"], "private-notion-token")
            self.assertNotIn("token", conn.execute("SELECT * FROM online_libraries").fetchone().keys())
        omitted = self.form.copy()
        omitted.pop("token")
        omitted["id"] = saved["id"]
        self.assertEqual(self.api.post(self.base, json=omitted).status_code, 200)

    def test_check_real_records_and_stage_one_dedup_import(self):
        saved = self.save()
        checked = self.action(saved, "check")
        entry = self.api.get(self.base).json()[0]
        self.assertEqual(entry['fields'], ['标题','正文'])
        self.assertIsNone(entry['last_import'])
        self.assertEqual(checked.json(), {"status": "ready", "fields": ["标题", "正文"], "count": 1, "titles": ["文章标题"], "partial": False})
        imported = self.action(saved, "import").json()
        self.assertEqual(imported, {"status": "imported", "run_id": 1, "stage": 1, "added": 1, "source_added": 1, "source_updated": 0, "skipped": 0, "partial": False, "deferred": 0})
        self.assertEqual(self.action(saved, "import").json(), {"status": "unchanged", "run_id": None, "stage": None, "added": 0, "source_added": 0, "source_updated": 0, "skipped": 1, "partial": False, "deferred": 0})
        with self.database() as conn:
            payload = json.loads(conn.execute("SELECT payload FROM runs").fetchone()[0])
        self.assertEqual(payload["materials"][0]["body"], "正文内容")
        self.assertTrue(payload["materials"][0]["raw"]["source_url"].startswith("https://notion.so/"))
        self.assertEqual(payload["config"]["project_id"], 1)
        original = self.notion
        self.handler = lambda r: httpx.Response(200, json={"results": [self.page("修改")], "has_more": False}) if r.url.path.endswith("query") else original(r)
        changed = self.action(saved, "import").json()
        self.assertEqual(self.api.get(self.base).json()[0]['last_import'], changed)
        self.assertEqual((changed['added'], changed['source_added'], changed['source_updated']), (1, 0, 1))

    def test_disconnect_preserves_import_and_retires_id(self):
        saved = self.save(body_field='')
        url = f"{self.base}/{saved['id']}"
        self.action(saved, 'import').raise_for_status()
        with self.database() as conn:
            payload = conn.execute('SELECT payload FROM runs').fetchone()[0]
        self.assertEqual(self.api.delete(url.replace('/1/online-', '/2/online-')).status_code, 404)
        self.assertEqual(self.api.delete(url).json(), {'status': 'disconnected'})
        self.assertEqual(self.api.get(self.base).json(), [])
        self.assertNotEqual(self.save()['id'], saved['id'])
        for action in ('import', 'check'):
            self.assertEqual(self.action(saved, action).status_code, 404)
        self.assertEqual(self.api.post(self.base, json=self.form | {'id': saved['id']}).status_code, 404)
        with self.database() as conn:
            self.assertEqual(conn.execute('SELECT payload FROM runs').fetchone()[0], payload)
            for table in ('online_library_secrets', 'online_library_fingerprints', 'online_library_body_cache'):
                self.assertFalse(conn.execute(f'SELECT 1 FROM {table} WHERE library_id=?', (saved['id'],)).fetchone())

    def test_disconnect_rejects_archive_references(self):
        saved = self.save()
        url = f"{self.base}/{saved['id']}"
        self.settings['archive_library_id'] = saved['id']
        response = self.api.delete(url)
        self.assertEqual(response.status_code, 409)
        self.assertIn('默认归档', response.json()['detail'])
        self.settings.clear()
        with self.database() as conn:
            draft_archive._schema(conn)
            conn.execute('''INSERT INTO draft_archives(project_id, run_id, version, library_id, draft_hash, provider, state)
                VALUES(1,1,1,?,'hash','notion','sending')''', (saved['id'],))
        for state, reason in [('sending', '尚未确认'), ('sent', '查看回执'), ('uncertain', '查看回执'), ('failed', '查看回执')]:
            with self.database() as conn:
                conn.execute('UPDATE draft_archives SET state=?', (state,))
            response = self.api.delete(url)
            self.assertEqual(response.status_code, 409)
            self.assertIn(reason, response.json()['detail'])
            self.assertIn('移除该集成', response.json()['detail'])
            self.assertEqual(self.api.get(self.base).json(), [saved])
            with self.database() as conn:
                self.assertEqual(conn.execute('SELECT state FROM draft_archives').fetchone()[0], state)

        feishu = self.save(provider='feishu', address='AppToken', table_id='tblABC', app_id='app-id',
                           app_secret='test-app-secret', token='')
        with self.database() as conn:
            conn.execute('UPDATE draft_archives SET library_id=?,provider=?', (feishu['id'], 'feishu'))
        response = self.api.delete(f"{self.base}/{feishu['id']}")
        self.assertEqual(response.status_code, 409)
        self.assertIn('「分享」→「协作者」中移除该应用', response.json()['detail'])

    def test_disconnect_during_remote_read_prevents_commit(self):
        for action in ('check', 'import'):
            saved = self.save()
            def disconnect_during_query(request):
                if request.url.path.endswith('/query'):
                    self.api.delete(f"{self.base}/{saved['id']}").raise_for_status()
                return self.notion(request)
            self.handler = disconnect_during_query
            self.assertEqual(self.action(saved, action).status_code, 404)
            with self.database() as conn:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 0)
                self.assertEqual(conn.execute('SELECT status FROM online_libraries WHERE id=?', (saved['id'],)).fetchone()[0], 'disconnected')

    def test_project_isolation(self):
        saved = self.save()
        other = self.base.replace("/1/", "/2/")
        self.assertEqual(self.api.get(other).json(), [])
        for action in ("check", "import"):
            self.assertEqual(self.api.post(f"{other}/{saved['id']}/{action}").status_code, 404)
        self.assertEqual(self.api.post(other, json=self.form | {"id": saved["id"]}).status_code, 404)
        self.assertEqual(self.api.get(self.base.replace("/1/", "/999/")).status_code, 404)
        self.assertFalse(self.requests)

    def test_failed_check_preserves_configuration_and_status(self):
        saved = self.save()
        self.handler = lambda r: httpx.Response(403, text="private-notion-token remote error")
        response = self.action(saved, "check")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("private-notion-token", response.text)
        self.assertEqual(self.api.get(self.base).json(), [saved])

    def test_validation_never_echoes_input(self):
        for change in ({"provider": "private-notion-token"}, {"token": {"secret": "private-notion-token"}},
                       {"address": "https://evil.example/private-notion-token"},
                       {"address": "https://notion.so:bad/" + "a" * 32},
                       {"name": "private-notion-token"}):
            response = self.api.post(self.base, json=self.form | change)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertNotIn("private-notion-token", response.text)
        self.assertEqual(self.api.get(self.base).json(), [])

    def test_url_validation_and_no_environment_dependency(self):
        with patch.dict("os.environ", {"NOTION_TOKEN": "environment-secret"}):
            self.assertEqual(self.api.post(self.base, json=self.form | {"token": ""}).status_code, 422)
        self.save(address="https://workspace.notion.site/My-database-" + "a" * 32 + "?v=view")
        for address in ("http://notion.so/" + "a" * 32, "https://notion.so.evil.org/" + "a" * 32):
            self.assertEqual(self.api.post(self.base, json=self.form | {"address": address}).status_code, 422)

    def test_missing_fields_no_import(self):
        saved = self.save(body_field="不存在")
        response = self.action(saved, "import")
        self.assertEqual(response.status_code, 400)
        self.assertIn("字段不存在", response.json()["detail"])
        with self.database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0)

    def test_notion_block_fallback_and_limit(self):
        saved = self.save(body_field="")
        original = self.notion
        self.handler = lambda r: httpx.Response(200, json={"results": [self.page("") | {"id": f"{i:032x}"} for i in range(25)], "has_more": False}) if r.url.path.endswith("query") else original(r)
        result = self.action(saved, "import").json()
        self.assertEqual((result["added"], result["skipped"], result["deferred"]), (20, 0, 5))
        self.assertTrue(result["partial"])
        self.assertEqual(sum(r.url.path.endswith("children") for r in self.requests), 20)
        self.requests.clear()
        continued = self.action(saved, "import").json()
        self.assertEqual((continued["added"], continued["skipped"], continued["deferred"]), (5, 20, 0))
        self.assertFalse(continued["partial"])
        block_ids = [r.url.path.split("/")[-2] for r in self.requests if r.url.path.endswith("children")]
        self.assertEqual(block_ids, [f"{i:032x}" for i in range(20, 25)])
        self.requests.clear()
        unchanged = self.action(saved, "import").json()
        self.assertEqual((unchanged["added"], unchanged["skipped"], unchanged["deferred"]), (0, 25, 0))
        self.assertFalse(any(r.url.path.endswith("children") for r in self.requests))
        with self.database() as conn:
            materials = [item for run in conn.execute("SELECT payload FROM runs")
                         for item in json.loads(run[0])["materials"]]
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM online_library_body_cache").fetchone()[0], 25)
        self.assertEqual(len(materials), 25)
        self.assertTrue(all(item["body"] == "块正文" for item in materials))

    def test_body_cache_invalidates_metadata_and_field_config(self):
        saved = self.save(body_field="")
        page = self.page("") | {"last_edited_time": "2026-01-01T00:00:00Z"}
        page["properties"]["另一个正文"] = {"rich_text": []}
        page["properties"]["另一个标题"] = {"title": [{"plain_text": "新标题"}]}
        original = self.notion
        def remote(request):
            if request.url.path.endswith("query"):
                return httpx.Response(200, json={"results": [page], "has_more": False})
            if request.url.path.endswith("children"):
                return original(request)
            return httpx.Response(200, json={"properties": page["properties"]})
        self.handler = remote
        self.action(saved, "import").raise_for_status()
        def verify_refetch():
            self.requests.clear()
            self.action(saved, "import").raise_for_status()
            self.assertEqual(sum(r.url.path.endswith("children") for r in self.requests), 1)
        page["last_edited_time"] = "2026-01-02T00:00:00Z"
        verify_refetch()
        page["properties"]["标题"]["title"][0]["plain_text"] = "修改标题"
        verify_refetch()
        self.save(id=saved["id"], body_field="另一个正文")
        verify_refetch()
        self.save(id=saved["id"], title_field="另一个标题", body_field="另一个正文")
        verify_refetch()
        self.save(id=saved["id"], title_field="另一个标题", body_field="另一个正文",
                  address="https://notion.so/" + "a" * 32)
        verify_refetch()

    def test_check_does_not_persist_body_cache_and_nested_partial_is_not_deferred(self):
        saved = self.save(body_field="")
        original = self.notion
        def nested(request):
            response = original(request)
            if request.url.path.endswith("children"):
                data = response.json()
                data["results"][0]["has_children"] = True
                return httpx.Response(200, json=data)
            return response
        self.handler = nested
        self.action(saved, "check").raise_for_status()
        with self.database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM online_library_body_cache").fetchone()[0], 0)
        result = self.action(saved, "import").json()
        self.assertTrue(result["partial"])
        self.assertEqual(result["deferred"], 0)
        self.requests.clear()
        result = self.action(saved, "import").json()
        self.assertTrue(result["partial"])
        self.assertEqual(result["deferred"], 0)
        self.assertFalse(any(r.url.path.endswith("children") for r in self.requests))

    def test_notion_pagination_bound(self):
        saved = self.save()
        original = self.notion
        self.handler = lambda r: httpx.Response(200, json={"results": [self.page()], "has_more": True, "next_cursor": "cursor"}) if r.url.path.endswith("query") else original(r)
        result = self.action(saved, "import").json()
        self.assertTrue(result["partial"])
        self.assertEqual(sum(r.url.path.endswith("query") for r in self.requests), 10)

    def feishu(self, request):
        self.assertEqual(request.url.host, "open.feishu.cn")
        if request.url.path.endswith("internal"):
            self.assertEqual(json.loads(request.content), {"app_id": "cli_public", "app_secret": "private-feishu-secret"})
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "private-tenant-token"})
        self.assertEqual(request.headers["Authorization"], "Bearer private-tenant-token")
        if request.url.path.endswith("fields"):
            return httpx.Response(200, json={"code": 0, "data": {"items": [{"field_name": "标题"}, {"field_name": "正文"}], "has_more": False}})
        return httpx.Response(200, json={"code": 0, "data": {"items": [{"record_id": "rec1", "fields": {"标题": [{"text": "飞书标题"}], "正文": [{"text": "第一段"}, {"text": "第二段"}]}}], "has_more": False}})

    def save_feishu(self, **changes):
        self.handler = self.feishu
        return self.save(provider="feishu", address="https://team.feishu.cn/base/app123?table=tbl123", token="", app_id="cli_public", app_secret="private-feishu-secret", **changes)

    def test_feishu_import_and_nonsecret_app_id(self):
        saved = self.save_feishu()
        self.assertEqual(saved["app_id"], "cli_public")
        self.assertEqual(self.action(saved, "check").json()["titles"], ["飞书标题"])
        self.assertEqual(self.action(saved, "import").json()["added"], 1)
        with self.database() as conn:
            material = json.loads(conn.execute("SELECT payload FROM runs").fetchone()[0])["materials"][0]
        self.assertEqual(material["body"], "第一段\n第二段")
        self.assertIn("record=rec1", material["raw"]["source_url"])
        self.assertEqual(material["raw"]["record_id"], "rec1")

    def test_feishu_standalone_and_required_fields(self):
        self.form.update(provider="feishu", address="app123", table_id="tbl123", token="", app_id="cli_public", app_secret="private-feishu-secret")
        saved = self.save()
        self.handler = self.feishu
        self.assertEqual(self.action(saved, "check").status_code, 200)
        for change in ({"table_id": ""}, {"body_field": ""}):
            self.assertEqual(self.api.post(self.base, json=self.form | change).status_code, 422)

    def test_feishu_pagination_bound_and_auth_error(self):
        saved = self.save_feishu()
        original = self.feishu
        def paginated(request):
            response = original(request)
            if request.url.path.endswith("records"):
                data = response.json()
                data["data"].update(has_more=True, page_token="next")
                return httpx.Response(200, json=data)
            return response
        self.handler = paginated
        self.assertTrue(self.action(saved, "import").json()["partial"])
        self.assertEqual(sum(r.url.path.endswith("records") for r in self.requests), 20)
        self.handler = lambda r: httpx.Response(200, json={"code": 999, "msg": "private-feishu-secret"})
        response = self.action(saved, "check")
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("private-feishu-secret", response.text)

    def test_timeouts_redirects_large_or_invalid_responses(self):
        saved = self.save()
        def timeout(request):
            raise httpx.ReadTimeout("private-notion-token")
        for handler in (timeout, lambda r: httpx.Response(302, headers={"location": "https://evil.example"}),
                        lambda r: httpx.Response(200, content=b"x" * (libraries.MAX_BYTES + 1)),
                        lambda r: httpx.Response(200, content=b"not json private-notion-token")):
            self.handler = handler
            response = self.action(saved, "check")
            self.assertEqual(response.status_code, 400)
            self.assertNotIn("private-notion-token", response.text)

    def test_feishu_two_thousand_record_limit(self):
        saved = self.save_feishu()
        original = self.feishu
        def records(request):
            if not request.url.path.endswith("records"):
                return original(request)
            index = int(request.url.params.get("page_token", "0"))
            self.assertEqual(request.url.params["page_size"], "100")
            return httpx.Response(200, json={"code": 0, "data": {
                "items": [{"record_id": f"rec{index * 100 + i}", "fields": {"标题": "标题", "正文": "内容"}} for i in range(100)],
                "has_more": True, "page_token": str(index + 1)}})
        self.handler = records
        result = self.action(saved, "import").json()
        self.assertEqual(result["added"], 2000)
        self.assertTrue(result["partial"])
        self.assertEqual(sum(r.url.path.endswith("records") for r in self.requests), 20)

    def test_remote_content_cannot_disclose_credentials(self):
        saved = self.save()
        original = self.notion
        def echoed(request):
            if request.url.path.endswith("query"):
                page = self.page("private-notion-token")
                page["properties"]["标题"] = {"title": [{"plain_text": "private-notion-token"}]}
                return httpx.Response(200, json={"results": [page], "has_more": False})
            return original(request)
        self.handler = echoed
        self.assertNotIn("private-notion-token", self.action(saved, "check").text)
        self.action(saved, "import")
        with self.database() as conn:
            payload = conn.execute("SELECT payload FROM runs").fetchone()[0]
        self.assertNotIn("private-notion-token", payload)
        self.assertEqual(json.loads(payload)["config"]["notion_database"], "")

    def test_failed_insert_rolls_back_fingerprints(self):
        saved = self.save(body_field="")
        api = FastAPI()
        def broken(conn, *args):
            conn.execute("INSERT INTO runs(project,payload) VALUES(1,'{}')")
            raise RuntimeError("local failure")
        libraries.mount(api, self.database, self.project_settings, broken)
        with TestClient(api, raise_server_exceptions=False) as client:
            self.assertEqual(client.post(f"{self.base}/{saved['id']}/import").status_code, 500)
        with self.database() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM online_library_fingerprints").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM online_library_body_cache").fetchone()[0], 0)
        self.requests.clear()
        self.assertEqual(self.action(saved, "import").json()["added"], 1)
        self.assertEqual(sum(r.url.path.endswith("children") for r in self.requests), 1)


if __name__ == "__main__":
    unittest.main()
