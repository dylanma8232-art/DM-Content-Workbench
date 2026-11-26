from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_workbench import WorkbenchFixture

import app
from collection import mount_collection


class CollectionTests(WorkbenchFixture):
    def setUp(self):
        super().setUp()
        api = FastAPI()
        mount_collection(api, app.database, app.insert_run, app.project_settings)
        self.api = self.enterContext(TestClient(api))
        self.ref = {"run_id": self.create(), "material_id": 2}
        self.data = {"name": "合集", "request_key": str(uuid4()), "items": [self.ref]}

    def post(self, project=1, **changes):
        return self.api.post(
            f"/api/projects/{project}/collections", json={**self.data, **changes}
        )

    def test_snapshot_and_pipeline(self):
        self.form.update(format="txt", filename="", name="原始记录", content="  原文  ")
        second = self.create()
        refs = [
            self.ref,
            {"run_id": second, "material_id": 1},
            {**self.ref, "material_id": 1},
        ]
        result = self.post(items=refs)
        self.assertEqual(result.status_code, 201, result.text)
        run = result.json()
        path = f"/api/runs/{run['id']}"
        self.assertEqual(run, self.client.get(path).json())
        materials = run["payload"]["materials"]
        for index, (ref, material) in enumerate(zip(refs, materials, strict=True), 1):
            original = self.client.get(f"/api/runs/{ref['run_id']}").json()
            item = original["payload"]["materials"][ref["material_id"] - 1]
            self.assertEqual(
                material,
                {
                    **item,
                    "id": index,
                    "provenance": {
                        **ref,
                        "title": item["title"],
                        "source": original["payload"]["config"]["filename"]
                        or original["name"],
                    },
                },
            )
        nested = self.post(
            request_key=str(uuid4()), items=[{"run_id": run["id"], "material_id": 2}]
        ).json()
        self.assertEqual(nested["payload"]["materials"], [{**materials[1], "id": 1}])
        for stage in range(1, 5):
            self.client.post(
                path + "/advance", json={"stage": stage, "topic_id": 1}
            ).raise_for_status()

    def test_project_isolation_and_atomic_retry(self):
        project = self.client.post("/api/projects", json={"name": "其他项目"}).json()[
            "id"
        ]
        self.form["project_id"] = project
        foreign = {"run_id": self.create(), "material_id": 1}
        self.assertEqual(self.post(items=[self.ref, foreign]).status_code, 404)
        self.assertEqual(self.post(project=999).status_code, 404)
        self.assertEqual(len(self.client.get("/api/runs").json()), 1)
        self.assertEqual(self.post().status_code, 201)
        self.assertEqual(
            self.post(project=project, items=[foreign]).json()["project_id"], project
        )

    def test_bounds_duplicates_and_required_uuid(self):
        for changes in [
            {"items": []},
            {"items": [self.ref] * 2},
            {"items": [{**self.ref, "material_id": 3}]},
            {"items": [{**self.ref, "run_id": 0}]},
            {"request_key": "invalid"},
            {"name": " "},
        ]:
            with self.subTest(changes=changes):
                self.assertEqual(self.post(**changes).status_code, 422)
        del self.data["request_key"]
        self.assertEqual(self.post().status_code, 422)
        self.form["content"] = chr(10).join(["名称,正文"] + ["甲,正文"] * 101)
        source = self.create()
        refs = [{"run_id": source, "material_id": index} for index in range(1, 102)]
        key = str(uuid4())
        self.assertEqual(self.post(items=refs, request_key=key).status_code, 422)
        self.assertEqual(
            len(
                self.post(items=refs[:100], request_key=key).json()["payload"][
                    "materials"
                ]
            ),
            100,
        )

    def test_concurrent_retries_and_conflicting_reuse(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(lambda _: self.post(), range(4)))
        for response in responses:
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(response.json(), responses[0].json())
        self.assertEqual(self.post(name="改名").status_code, 409)
        self.assertEqual(
            self.post(items=[{**self.ref, "material_id": 1}]).status_code, 409
        )
        self.assertEqual(len(self.client.get("/api/runs").json()), 2)
