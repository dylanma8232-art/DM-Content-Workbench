import json
from collections import defaultdict
from typing import Annotated
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field, StringConstraints


class MaterialRef(BaseModel):
    run_id: int = Field(strict=True, ge=1, le=2**63 - 1)
    material_id: int = Field(strict=True, ge=1, le=2**63 - 1)


class CollectionForm(BaseModel):
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ]
    items: list[MaterialRef] = Field(min_length=1, max_length=100)
    request_key: UUID


def mount_collection(app, database, insert_run, project_settings):
    @app.post("/api/projects/{project_id}/collections", status_code=201)
    def create_collection(
        project_id: Annotated[int, Field(ge=1, le=2**63 - 1)], form: CollectionForm
    ):
        refs = [(item.run_id, item.material_id) for item in form.items]
        if len(set(refs)) != len(refs):
            raise HTTPException(422, "不能重复选择同一份素材")
        signature = json.dumps([form.name, refs], ensure_ascii=False)
        key = (project_id, str(form.request_key))
        with database() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project_settings(conn, project_id)
            conn.execute("""CREATE TABLE IF NOT EXISTS collection_requests (
                project_id INTEGER NOT NULL, request_key TEXT NOT NULL,
                signature TEXT NOT NULL, run_id INTEGER NOT NULL,
                PRIMARY KEY (project_id, request_key))""")
            previous = conn.execute(
                """SELECT signature, run_id FROM collection_requests
                WHERE project_id=? AND request_key=?""",
                key,
            ).fetchone()
            if previous:
                if previous["signature"] != signature:
                    raise HTTPException(409, "此请求编号已用于其他素材合集")
                row = conn.execute(
                    "SELECT * FROM runs WHERE id=? AND project_id=?",
                    (previous["run_id"], project_id),
                ).fetchone()
                return {**dict(row), "payload": json.loads(row["payload"])}

            selected = defaultdict(list)
            for index, (run_id, material_id) in enumerate(refs):
                selected[run_id].append((index, material_id))
            materials = [None] * len(refs)
            for run_id, entries in selected.items():
                row = conn.execute(
                    "SELECT name, payload FROM runs WHERE id=? AND project_id=?",
                    (run_id, project_id),
                ).fetchone()
                if row is None:
                    raise HTTPException(404, "项目内找不到这次运行")
                payload = json.loads(row["payload"])
                by_id = {item["id"]: item for item in payload["materials"]}
                source = payload["config"].get("filename") or row["name"]
                for index, material_id in entries:
                    if material_id not in by_id:
                        raise HTTPException(422, "所选素材编号超出范围")
                    item = by_id[material_id]
                    provenance = item.get("provenance") or {
                        "run_id": run_id,
                        "material_id": material_id,
                        "source": source,
                        "title": item["title"],
                    }
                    materials[index] = {
                        **item,
                        "id": index + 1,
                        "provenance": provenance,
                    }

            config = {
                "project_id": project_id,
                "format": "collection",
                "filename": form.name,
            }
            run = insert_run(conn, form.name, config, materials)
            conn.execute(
                """INSERT INTO collection_requests(project_id, request_key, signature, run_id)
                VALUES (?,?,?,?)""",
                (*key, signature, run["id"]),
            )
            return run
