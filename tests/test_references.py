from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_workbench import WorkbenchFixture

import app
from collection import mount_collection
from references import mount_references


class ReferenceTests(WorkbenchFixture):
    def setUp(self):
        super().setUp()
        api = FastAPI()
        mount_references(api, app.database, app.project_settings, app.topic_rows)
        mount_collection(api, app.database, app.insert_run, app.project_settings)
        self.api = self.enterContext(TestClient(api))

    def lookup(self, run, material, project=1):
        return self.api.get(f"/api/projects/{project}/references/{run}/{material}")

    def standalone(self):
        self.form.update(format="txt", filename="Shared title", content="shared quote")
        return self.create()

    def collect(self, *runs):
        result = self.api.post(
            "/api/projects/1/collections",
            json={
                "name": "Collection",
                "request_key": str(uuid4()),
                "items": [{"run_id": run, "material_id": 1} for run in runs],
            },
        )
        result.raise_for_status()
        return result.json()["id"]

    def analyze(self, run, selected):
        with app.database() as conn:
            record = app.get_run(conn, run)
            record["stage"] = 5
            record["payload"].update(
                insights=[
                    {"id": item["id"], "quote": "shared quote"}
                    for item in record["payload"]["materials"]
                ],
                topic=selected,
                drafts=["first", "edited"],
            )
            app.persist(conn, record)

    def test_mixed_collection_selected_evidence_and_legacy_topic_ids(self):
        first, second = self.standalone(), self.standalone()
        mixed = self.collect(second, first)
        self.analyze(first, {"id": 1, "title": "Original"})
        self.analyze(mixed, {"id": 1, "title": "Chosen"})
        unchosen = {"run_id": mixed, "key": "2", "title": "Shared title"}
        original = {"run_id": first, "key": "1", "title": "Original"}
        expected = {
            "topics": [unchosen, original],
            "drafts": [{**original, "key": "draft"}],
        }
        self.assertEqual(self.lookup(first, 1).json(), expected)
        self.assertEqual(self.lookup(mixed, 2).json(), expected)
        chosen = {"run_id": mixed, "key": "1", "title": "Chosen"}
        self.assertEqual(
            self.lookup(second, 1).json(),
            {"topics": [chosen], "drafts": [{**chosen, "key": "draft"}]},
        )

    def test_group_evidence_and_old_parent_chain(self):
        original = self.standalone()
        with app.database() as conn:
            data = app.get_run(conn, original)["payload"]
            replay = app.insert_run(
                conn, "Old replay", data["config"], data["materials"], parent=original
            )["id"]
        mixed = self.collect(self.standalone(), replay)
        with app.database() as conn:
            record = app.get_run(conn, mixed)
            group = {"key": "group-1", "ids": [1, 2], "title": "Candidate"}
            record["stage"] = 5
            record["payload"].update(
                recommendations=[group],
                topic={**group, "title": "Selected"},
                drafts=["v1"],
            )
            app.persist(conn, record)
        expected = {
            "topics": [{"run_id": mixed, "key": "group-1", "title": "Selected"}],
            "drafts": [{"run_id": mixed, "key": "draft", "title": "Selected"}],
        }
        for run, ident in [(mixed, 2), (replay, 1), (original, 1)]:
            self.assertEqual(self.lookup(run, ident).json(), expected)

    def test_two_copies_in_one_candidate_return_one_reference(self):
        origin = self.standalone()
        copy = self.collect(origin)
        mixed = self.collect(origin, copy)
        with app.database() as conn:
            record = app.get_run(conn, mixed)
            record["stage"] = 3
            record["payload"]["recommendations"] = [
                {"key": "group-1", "ids": [1, 2], "title": "Both copies"}
            ]
            app.persist(conn, record)
        expected = {
            "topics": [{"run_id": mixed, "key": "group-1", "title": "Both copies"}],
            "drafts": [],
        }
        self.assertEqual(self.lookup(origin, 1).json(), expected)
        self.assertEqual(self.lookup(mixed, 2).json(), expected)

    def test_isolation_and_missing_materials(self):
        first = self.standalone()
        project = self.client.post("/api/projects", json={"name": "Separate"}).json()[
            "id"
        ]
        self.form["project_id"] = project
        foreign = self.standalone()
        with app.database() as conn:
            record = app.get_run(conn, foreign)
            record["payload"]["parent"] = first
            record["payload"]["materials"][0]["provenance"] = {
                "run_id": first,
                "material_id": 1,
            }
            app.persist(conn, record)
        self.analyze(foreign, {"id": 1, "title": "Private"})
        self.assertEqual(self.lookup(first, 1).json(), {"topics": [], "drafts": []})
        self.assertEqual(self.lookup(foreign, 1).status_code, 404)
        self.assertEqual(self.lookup(first, 1, project).status_code, 404)
        self.assertEqual(self.lookup(first, 2).status_code, 404)
        self.assertEqual(self.lookup(first, 0).status_code, 422)
