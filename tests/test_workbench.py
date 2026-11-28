import os
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app
from backup import copy_database


class WorkbenchFixture(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        env = patch.dict(os.environ, {"DM_DATA_DIR": self.folder.name})
        env.start()
        self.addCleanup(env.stop)
        self.client = self.enterContext(TestClient(app.app))
        self.form = {
            "name": "阅读",
            "filename": "访谈.csv",
            "format": "csv",
            "body_field": "正文",
            "title_field": "名称",
            "content": "名称,正文\n甲,阅读材料。需要更多例子。\n乙,阅读材料。需要更多例子。",
            "focus": "例子",
        }

    def create(self):
        response = self.client.post("/api/runs", json=self.form)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]


class WorkbenchTests(WorkbenchFixture):
    def test_first_start_persists_project_without_credentials(self):
        self.assertEqual(self.client.get('/api/setup').json()['complete'],False)
        request={'name':'新工作区项目','audience':'编辑','style':'简报'}
        first=self.client.post('/api/setup',json=request).json()['project_id']
        self.assertEqual(self.client.get('/api/setup').json(),{'complete':True,'project_id':first})
        self.assertEqual(self.client.post('/api/setup',json={'name':'再次提交'}).json()['project_id'],first)
        self.assertEqual(next(row for row in self.client.get('/api/projects').json() if row['id']==first)['settings']['style'],'简报')
        with app.database() as conn:
            app.initialize(conn)
        self.assertEqual(self.client.get('/api/setup').json()['project_id'],first)

    def test_storage_copy_preserves_workspace_and_rejects_existing_destination(self):
        run_id = self.create()
        new_dir = Path(self.folder.name) / 'new'
        self.client.put('/api/storage', json={'directory':str(new_dir)}).raise_for_status()
        settings = self.client.get('/api/connections').json()
        self.assertEqual(settings['storage'], 'local')
        self.assertNotIn('data_dir', settings)
        self.assertNotIn(str(new_dir.resolve()), json.dumps(settings))
        self.assertEqual(self.client.get(f'/api/runs/{run_id}').json()['name'], '阅读')
        self.assertEqual(self.client.put('/api/storage', json={'directory':self.folder.name}).status_code, 409)
        self.assertEqual(app.data_directory(), new_dir.resolve())
        self.create()
        with closing(app.sqlite3.connect(Path(self.folder.name) / 'workbench.sqlite3')) as original:
            self.assertEqual(original.execute('SELECT count(*) FROM runs').fetchone()[0], 1)

    def test_existing_workspace_skips_initialization(self):
        run=self.create()
        with app.database() as conn:
            app.initialize(conn)
        self.assertTrue(self.client.get('/api/setup').json()['complete'])
        self.assertEqual(self.client.get(f'/api/runs/{run}').json()['name'],'阅读')

    def test_failed_migration_verification_preserves_active_database(self):
        run = self.create()
        target = Path(self.folder.name) / 'destination'
        with patch('backup.verify_database', side_effect=ValueError('模拟校验失败')):
            failed = self.client.put('/api/storage', json={'directory':str(target)})
        self.assertEqual(failed.status_code,422)
        self.assertFalse((target / 'workbench.sqlite3').exists())
        self.assertEqual(app.data_directory(),Path(self.folder.name).resolve())
        self.assertEqual(self.client.get(f'/api/runs/{run}').json()['name'],'阅读')

    def test_ocr_and_speech_have_independent_readiness(self):
        with patch.dict(os.environ,{'DM_OCR_ENABLED':'1','DM_ASR_BASE_URL':'','DM_ASR_MODEL':''}), patch('app.shutil.which',return_value='fixture-tool'):
            self.client.post('/api/connections/check',json={'target':'ocr'}).raise_for_status()
            speech = self.client.post('/api/connections/check',json={'target':'transcription'})
            self.assertEqual(speech.status_code,422)
            self.assertIn('转写',speech.json()['detail'])
            self.assertNotIn('FFmpeg',speech.json()['detail'])

    def test_connection_check_reports_actual_scope(self):
        with patch('connections.check_model') as model:
            self.client.post('/api/connections/check',json={'target':'model'}).raise_for_status()
            model.assert_called_once()
        dbid='00000000-0000-0000-0000-000000000001'
        with patch('connections.notion',return_value={'properties':{'标题':{'type':'title'}}}) as notion:
            result=self.client.post('/api/connections/check',json={'target':'notion','database':dbid})
            result.raise_for_status()
            notion.assert_called_once_with('GET','databases/'+dbid)
            self.assertIn('读取权限',result.json()['message'])
        with patch.dict(os.environ,{'DM_ASR_BASE_URL':'','DM_ASR_MODEL':''}):
            self.assertEqual(self.client.post('/api/connections/check',json={'target':'transcription'}).status_code,422)
    def test_project_pipeline_and_resume(self):
        project = self.client.post("/api/projects", json={"name": "独立项目"}).json()[
            "id"
        ]
        self.form["project_id"] = project
        self.assertEqual(
            self.client.post("/api/import-preview", json=self.form).json()["保留"], 1
        )
        run_id = self.create()
        self.assertEqual(self.client.get("/api/runs").json(), [])
        path = f"/api/runs/{run_id}"
        for stage in range(1, 5):
            response = self.client.post(
                path + "/advance", json={"stage": stage, "topic_id": 1}
            )
            self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("更多例子", self.client.get(path + "/export").text)
        self.assertEqual(
            self.client.post(path + "/advance", json={"stage": 4}).status_code, 409
        )
        self.client.put(path + "/draft", json={"version": 1, "content": "已修订"})
        self.assertEqual(
            self.client.put(
                path + "/draft", json={"version": 1, "content": "旧稿"}
            ).status_code,
            409,
        )
        variant = self.client.post(path + "/variant").json()
        self.assertEqual(variant["stage"], 4)
        rerun = self.client.post(path + "/replay", json={"min_length": 90}).json()
        self.assertEqual(rerun["stage"], 1)
        self.assertEqual(self.client.get(path + "/export").text, "已修订")
        self.assertIn('更多例子', self.client.get(path+'/export?version=1').text)
        self.assertEqual(self.client.get(path+'/export?version=3').status_code, 404)
        with patch("connections.generate", return_value="模型草稿"):
            generated = self.client.post(
                f"/api/runs/{variant['id']}/advance",
                json={"stage": 4, "engine": "model"},
            ).json()
            self.assertIn("模型草稿", generated["payload"]["drafts"][0])

    def test_incremental(self):
        source = self.client.post(
            "/api/sources",
            json={"name": "订阅", "kind": "rss", "address": "https://example.org/feed"},
        ).json()["id"]
        rows = [{"key": "item", "title": "标题", "body": "摘要"}]
        path = f"/api/sources/{source}/refresh"
        with patch("connections.rss", return_value=rows):
            self.assertIn("新增 1", self.client.post(path).json()["status"])
            self.assertIsNone(self.client.post(path).json()["run"])
            rows[0]["body"] = "新摘要"
            self.assertIn("更新 1", self.client.post(path).json()["status"])
        batches = self.client.get('/api/projects/1/overview').json()['batches']
        self.assertEqual([(row['added'],row['updated'],row['duplicates']) for row in batches],
                         [(0,1,0),(0,0,1),(1,0,0)])

    def test_notion_reconciliation(self):
        dbid = "00000000-0000-0000-0000-000000000001"
        pages = []

        def remote(method, path, body=None):
            if method == "GET" and path.startswith("databases/"):
                return {"properties": {"标题": {"type": "title"}}}
            if path.endswith("/query"):
                return {"results": [{"id": "page"}] if pages else []}
            if path == "pages":
                pages.append(body)
                return {"id": "page"}
            return {
                "results": [
                    {
                        "type": "code",
                        "code": {
                            "rich_text": [
                                {
                                    "plain_text": block["code"]["rich_text"][0]["text"][
                                        "content"
                                    ]
                                }
                            ]
                        },
                    }
                    for block in pages[0]["children"]
                ]
            }

        with patch("connections.notion", side_effect=remote):
            self.client.put(
                "/api/projects/1", json={"notion_database": dbid}
            ).raise_for_status()
            run_id = self.create()
            app.process_outbox()
            self.assertEqual(
                self.client.get(f"/api/runs/{run_id}/sync").json()[0]["state"], "已核实"
            )
            self.client.post(
                f"/api/runs/{run_id}/notion", json={"database": dbid}
            ).raise_for_status()
            self.assertEqual(len(pages), 1)
            restored = self.client.post(
                "/api/notion/restore", json={"page_id": dbid, "project_id": 1}
            )
            restored.raise_for_status()
            self.assertNotEqual(restored.json()["id"], run_id)
            self.assertEqual(
                restored.json()["payload"]["config"]["notion_database"], ""
            )
            pages[0]["children"][0]["code"]["rich_text"][0]["text"]["content"] = (
                "人工修订"
            )
            self.assertEqual(
                self.client.post(
                    f"/api/runs/{run_id}/notion", json={"database": dbid}
                ).status_code,
                409,
            )

    def test_model_adapter(self):
        import httpx

        import connections

        response = httpx.Response(
            200,
            request=httpx.Request("POST", "https://model.example/v1/chat/completions"),
            json={"choices": [{"message": {"content": "已有依据的草稿"}}]},
        )
        with (
            patch.dict(
                os.environ,
                {
                    "DM_MODEL_BASE_URL": "https://model.example/v1",
                    "DM_MODEL_NAME": "fixture",
                },
            ),
            patch("httpx.post", return_value=response) as post,
        ):
            self.assertEqual(
                connections.generate("选题", "依据", "读者", "短文"), "已有依据的草稿"
            )
            self.assertEqual(
                post.call_args.args[0], "https://model.example/v1/chat/completions"
            )
            self.assertIn(
                "依据", post.call_args.kwargs["json"]["messages"][1]["content"]
            )

    def test_failed_source_can_resume(self):
        source = self.client.post(
            "/api/sources",
            json={"name": "源", "kind": "rss", "address": "https://example.org/feed"},
        ).json()["id"]
        path = f"/api/sources/{source}/refresh"
        with patch("connections.rss", side_effect=app.HTTPException(502, "暂时不可用")):
            self.assertEqual(self.client.post(path).status_code, 502)
        self.assertEqual(self.client.get('/api/projects/1/overview').json()['failures'][0]['error'], '暂时不可用')
        with patch("connections.rss", return_value=[]):
            self.assertIn("读取 0", self.client.post(path).json()["status"])
        self.assertEqual(self.client.get('/api/projects/1/overview').json()['failures'], [])

    def test_backup_restore(self):
        run_id = self.create()
        path = Path(self.folder.name)
        copy_database(path / "workbench.sqlite3", path / "backup.sqlite3")
        copy_database(path / "backup.sqlite3", path / "restored" / "workbench.sqlite3")
        with patch.dict(os.environ, {"DM_DATA_DIR": str(path / "restored")}):
            self.assertEqual(app.detail(run_id)["name"], "阅读")
        with self.assertRaises(FileExistsError):
            copy_database(path / "backup.sqlite3", path / "workbench.sqlite3")

    def test_overview_tracks_project_and_versions(self):
        overview = "/api/projects/1/overview"
        self.assertEqual(self.client.get(overview).json()["by_stage"], [0] * 5)
        self.assertEqual(self.client.get("/api/projects/999/overview").status_code, 404)
        first = self.create()
        second = self.create()
        for stage in range(1, 5):
            self.client.post(
                f"/api/runs/{first}/advance", json={"stage": stage, "topic_id": 1}
            ).raise_for_status()
        self.client.put(
            f"/api/runs/{first}/draft", json={"version": 1, "content": "修订"}
        ).raise_for_status()
        self.client.post(
            "/api/sources",
            json={"name": "订阅", "kind": "rss", "address": "https://example.org/feed"},
        ).raise_for_status()
        other = self.client.post("/api/projects", json={"name": "另一个项目"}).json()[
            "id"
        ]
        self.form["project_id"] = other
        self.create()
        with app.database() as conn:
            conn.execute(
                "INSERT INTO outbox(run_id,target,snapshot,state) VALUES(?,?,?,'待处理')",
                (first, "目标", "{}"),
            )
        data = self.client.get(overview).json()
        self.assertEqual(data["by_stage"], [1, 0, 0, 0, 1])
        self.assertEqual([row["id"] for row in data["runs"]], [second, first])
        self.assertEqual(data["runs"][1]["versions"], 2)
        self.assertEqual(data["sources"][0]["name"], "订阅")
        self.assertEqual(data["conflicts"][0]["id"], first)
        isolated = self.client.get(f"/api/projects/{other}/overview").json()
        self.assertEqual(isolated["by_stage"], [1, 0, 0, 0, 0])
        self.assertEqual(isolated["sources"], [])
        self.assertEqual(isolated["conflicts"], [])

    def test_import_types_invalid_input_and_empty_selection(self):
        for fmt, content in [
            ("md", "# 标题\n正文"),
            ("txt", "纯文本"),
            ("json", '[{"正文":"一段文字"}]'),
        ]:
            response = self.client.post(
                "/api/runs", json={**self.form, "content": content, "format": fmt}
            )
            response.raise_for_status()
            self.assertEqual(len(response.json()["payload"]["materials"]), 1)
        for content in ("not-json", "[]", "{}", '[{"wrong":"value"}]'):
            self.assertEqual(
                self.client.post(
                    "/api/runs",
                    json={**self.form, "content": content, "format": "json"},
                ).status_code,
                422,
            )
        self.form["min_length"] = 500
        run = self.create()
        for stage in (1, 2):
            self.client.post(
                f"/api/runs/{run}/advance", json={"stage": stage}
            ).raise_for_status()
        self.assertEqual(
            self.client.post(
                f"/api/runs/{run}/advance", json={"stage": 3, "topic_id": 1}
            ).status_code,
            422,
        )
        self.assertEqual(self.client.get(f"/api/runs/{run}/export").status_code, 409)


if __name__ == "__main__":
    unittest.main()
