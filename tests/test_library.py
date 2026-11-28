import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

from test_workbench import WorkbenchFixture

import app


class LibraryTests(WorkbenchFixture):
    def library(self, kind, project=1, **params):
        response = self.client.get(
            f"/api/projects/{project}/library/{kind}", params=params
        )
        response.raise_for_status()
        return response.json()

    def advance(self, run, stage, **fields):
        response = self.client.post(
            f"/api/runs/{run}/advance", json=dict(stage=stage, **fields)
        )
        response.raise_for_status()
        return response.json()

    def test_material_filters_pagination_and_project_isolation(self):
        first = self.create()
        second = self.create()
        other = self.client.post("/api/projects", json={"name": "Other"}).json()["id"]
        private = self.client.post(
            "/api/runs", json={**self.form, "project_id": other, "filename": "Private"}
        ).json()["id"]
        self.advance(first, 1)
        result = self.library("materials", offset=1, limit=2)
        self.assertEqual(result["total"], 4)
        self.assertEqual(result["sources"], [self.form["filename"]])
        self.assertEqual(
            [(row["run_id"], row["key"]) for row in result["items"]],
            [(second, "2"), (first, "1")],
        )
        self.assertEqual(set(result["statuses"]), {"保留", "正文重复", "待清洗"})
        self.assertEqual(
            self.library("materials", run_id=private),
            {"items": [], "sources": [], "statuses": [], "total": 0},
        )
        self.assertEqual(self.library("materials", source="访谈")["total"], 0)
        self.assertEqual(self.library("materials", offset=10**30)["items"], [])
        (row,) = self.library("materials", run_id=first, q="例子", status="正文重复")[
            "items"
        ]
        self.assertEqual(row["material_id"], 2)
        self.assertEqual(row["original"], row["cleaned"])
        for params in ({"offset": -1}, {"limit": 0}, {"limit": 101}, {"run_id": 2**63}):
            self.assertEqual(
                self.client.get(
                    "/api/projects/1/library/materials", params=params
                ).status_code,
                422,
            )
        self.assertEqual(
            self.client.get("/api/projects/999/library/materials").status_code, 404
        )

    def test_collection_evidence_and_actionable_topics(self):
        refs = []
        for source, body in [
            ("Paper", "First. shared signal."),
            ("Paper II", "Second. shared insight."),
        ]:
            form = {
                "name": source,
                "filename": source,
                "format": "txt",
                "content": body,
            }
            run = self.client.post("/api/runs", json=form).json()["id"]
            (material,) = self.library("materials", run_id=run)["items"]
            refs.append({field: material[field] for field in ("run_id", "material_id")})
        self.client.put("/api/projects/1", json={"topics": "shared"}).raise_for_status()
        response = self.client.post(
            "/api/projects/1/collections",
            json={"name": "Mix", "request_key": str(uuid4()), "items": refs},
        )
        response.raise_for_status()
        run = response.json()["id"]
        for ref in refs:
            (original,) = self.library('materials', run_id=ref['run_id'])['items']
            self.assertEqual(original['material_id'], ref['material_id'])
            self.assertIn('shared', original['original'])
        self.advance(run, 1)
        self.advance(run, 2)
        group = self.library("topics", run_id=run, source="Paper II", status="候选")[
            "items"
        ][0]
        self.assertEqual(group["source_count"], 2)
        self.assertEqual(
            [row["run_id"] for row in group["evidence"]],
            [ref["run_id"] for ref in refs],
        )
        self.assertEqual([row["material_id"] for row in group["evidence"]], [1, 1])
        self.assertEqual([row["id"] for row in group["evidence"]], [1, 2])
        self.assertEqual(
            [row["source"] for row in group["evidence"]], ["Paper", "Paper II"]
        )
        self.assertTrue(all("shared" in row["quote"] for row in group["evidence"]))
        self.advance(run, 3, candidate_key=group["key"], title="Chosen")
        self.assertEqual(self.library("topics", status="候选")["items"], [])
        self.assertEqual(self.library("topics", status="未采用")["total"], 2)
        (chosen,) = self.library("topics", status="已选")["items"]
        self.assertEqual((chosen["key"], chosen["title"]), (group["key"], "Chosen"))
        self.advance(run, 4)
        (draft,) = self.library("drafts", source="Paper")["items"]
        self.assertEqual(draft["evidence"], group["evidence"])
        for kind in ("topics", "drafts", "materials"):
            preview = self.library(kind, compact=True, preview=True, limit=3)
            self.assertIsNone(preview["total"])
            self.assertFalse(
                any(
                    {"original", "cleaned", "evidence"} & item.keys()
                    for item in preview["items"]
                )
            )

    def test_concurrent_decisions_accept_one_per_observed_review_count(self):
        run = self.create()
        for stage in range(1, 5):
            self.advance(run, stage, topic_id=1)
        path = f'/api/runs/{run}'
        def decide(decision):
            return self.client.post(path + '/review', json={
                'version': 1, 'decision': decision, 'expected_review_count': 0})
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(decide, ['通过', '退回']))
        self.assertCountEqual([r.status_code for r in results], [200, 409])
        reviews = self.client.get(path).json()['payload']['reviews']
        self.assertEqual(len(reviews), 1)
        self.assertNotIn('expected_review_count', reviews[0])

    def test_current_review_and_actual_edit_time(self):
        run = self.create()
        for stage in range(1, 5):
            self.advance(run, stage, topic_id=1)
        path = f"/api/runs/{run}"
        original = self.library("drafts")["items"][0]
        first_body = self.client.get(path + '/export?version=1').text
        review = {"version": 1, "decision": "通过"}
        self.client.post(path + "/review", json=review).raise_for_status()
        for stale in (review, {**review, "expected_review_count": 0, "decision": "退回"}):
            self.assertEqual(self.client.post(path + "/review", json=stale).status_code, 409)
        self.client.post(path + "/review", json={**review, "decision": "退回", "expected_review_count": 1}).raise_for_status()
        self.assertEqual(self.library("drafts")["items"][0]["status"], "退回")
        self.client.put(
            path + "/draft", json={"version": 1, "content": "Updated"}
        ).raise_for_status()
        edited = self.library("drafts")["items"][0]
        self.assertEqual(self.client.get(path + '/export?version=1').text, first_body)
        self.assertEqual(self.client.get(path + '/export?version=2').text, 'Updated')
        self.assertGreater(edited["updated"], original["updated"])
        self.assertEqual(
            (edited["key"], edited["version"], edited["status"]),
            (original["key"], 2, "待审核"),
        )
        self.client.put(
            path + "/draft", json={"version": 2, "content": "Updated"}
        ).raise_for_status()
        self.assertEqual(self.library("drafts")["items"][0], edited)

    def test_legacy_restore_and_draft_sort_fallback(self):
        run = self.create()
        for stage in range(1, 5):
            current = self.advance(run, stage, topic_id=1)
        saved = json.loads(app.notion_snapshot(current))
        data = saved["payload"]
        for field in ("recommendations", "draft_updated", "themes"):
            data.pop(field)
        data["topic"] = {"id": 1, "title": "Legacy selection"}
        with patch("app.read_snapshot", return_value=json.dumps(saved)):
            response = self.client.post(
                "/api/notion/restore", json={"project_id": 1, "page_id": "a" * 32}
            )
        response.raise_for_status()
        restored = response.json()["id"]
        with app.database() as conn:
            conn.execute(
                "UPDATE runs SET created='2000-01-01 00:00:00' WHERE id=?", (restored,)
            )
        new, old = self.library("drafts")["items"]
        self.assertEqual(
            (new["run_id"], old["run_id"], old["updated"]), (run, restored, None)
        )
        (selected,) = self.library("topics", run_id=restored)["items"]
        self.assertEqual((selected["key"], selected["status"]), ("1", "已选"))
        self.assertEqual(selected["evidence"][0]["run_id"], restored)

    def test_preview_stops_reading_when_page_is_full(self):
        for _ in range(5):
            self.create()
        with patch("app.library_rows", wraps=app.library_rows) as read:
            shelf = self.library("materials", limit=3, preview=True, compact=True)
        self.assertEqual(len(shelf["items"]), 3)
        self.assertEqual(read.call_count, 2)

    def test_restore_validates_optional_library_metadata(self):
        run = self.create()
        self.advance(run, 1)
        saved = json.loads(app.notion_snapshot(self.advance(run, 2)))
        for updates in (
            {"reviews": [None]},
            {"draft_updated": "unknown"},
            {"recommendations": [{"key": "1", "ids": [999], "title": "missing"}]},
        ):
            snapshot = {**saved, "payload": {**saved["payload"], **updates}}
            with patch("app.read_snapshot", return_value=json.dumps(snapshot)):
                response = self.client.post(
                    "/api/notion/restore", json={"project_id": 1, "page_id": "b" * 32}
                )
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.library("topics")["total"], 1)

    def test_material_preview_omits_copies_and_full_library_names_batches(self):
        run = self.create()
        path = f"/api/runs/{run}"
        for stage in range(1, 5):
            self.advance(run, stage, topic_id=1)
        replay = self.client.post(path + "/replay", json={}).json()
        variant = self.client.post(path + "/variant").json()
        collection = self.client.post(
            "/api/projects/1/collections",
            json={
                "name": "Collection",
                "request_key": str(uuid4()),
                "items": [{"run_id": run, "material_id": 1}],
            },
        ).json()
        preview = self.library("materials", preview=True, compact=True, limit=3)
        self.assertEqual([row["run_id"] for row in preview["items"]], [run, run])
        full = self.library("materials", compact=True)
        batches = {row["run_id"]: row["batch_name"] for row in full["items"]}
        self.assertEqual(
            batches,
            {
                run: self.form["name"],
                **{row["id"]: row["name"] for row in (replay, variant, collection)},
            },
        )

    def test_material_utc_date_bounds(self):
        stamps = [
            "2026-11-24 23:59:59",
            "2026-11-25 00:00:00",
            "2026-11-25 23:59:59",
            "2026-11-26 00:00:00",
        ]
        for stamp in stamps:
            run = self.create()
            with app.database() as conn:
                conn.execute("UPDATE runs SET created=? WHERE id=?", (stamp, run))
        day = "2026-11-25"
        shanghai = self.library("materials", since=day, until=day, since_offset=-480, until_offset=-480)["items"]
        self.assertEqual({row['created'] for row in shanghai}, {stamps[0], stamps[1]})
        rows = self.library("materials", since=day, until=day)["items"]
        self.assertEqual(
            [row["created"] for row in rows], [stamps[2]] * 2 + [stamps[1]] * 2
        )
        self.assertEqual(self.library("materials", since=day)["total"], 6)
        self.assertEqual(self.library("materials", until=day)["total"], 6)
        for params in (
            {"since": "2026-11-31"},
            {"until": "20261125"},
            {"since": day, "until": "2026-01-01"},
        ):
            self.assertEqual(
                self.client.get(
                    "/api/projects/1/library/materials", params=params
                ).status_code,
                422,
            )
        self.assertEqual(
            self.client.get(
                "/api/projects/1/library/topics", params={"since": day}
            ).status_code,
            422,
        )
