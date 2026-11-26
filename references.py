import json
from typing import Annotated

from fastapi import HTTPException, Path

Identifier = Annotated[int, Path(ge=1, le=2**63 - 1)]


def material_origin(conn, project_id, run_id, material_id):
    previous = None
    while True:
        path = f"$.materials[{material_id - 1}]"
        row = conn.execute(
            """SELECT json_extract(payload, '$.parent') AS parent,
                      json_extract(payload, ? || '.id') AS local_id,
                      json_extract(payload, ? || '.provenance.run_id') AS origin_run,
                      json_extract(payload, ? || '.provenance.material_id') AS origin_id
               FROM runs WHERE project_id=? AND id=?""",
            (path, path, path, project_id, run_id),
        ).fetchone()
        if row is None or row["local_id"] != material_id:
            if previous is None:
                raise HTTPException(404, "项目内找不到这份素材")
            return previous
        previous = run_id, material_id
        if row["origin_run"] is not None:
            ancestor, ident = row["origin_run"], row["origin_id"]
        else:
            ancestor, ident = row["parent"], material_id
        if (
            type(ancestor) is not int
            or not 0 < ancestor < run_id
            or type(ident) is not int
            or not 0 < ident <= 2**63 - 1
        ):
            return previous
        run_id, material_id = ancestor, ident


MATCHED_RUNS = """
WITH RECURSIVE parents AS MATERIALIZED (
    SELECT id, json_extract(payload, '$.parent') AS parent
    FROM runs WHERE project_id=:project AND json_type(payload, '$.parent')='integer'
), copies AS MATERIALIZED (
    SELECT runs.id, json_extract(material.value, '$.id') AS local_id,
           json_extract(material.value, '$.provenance.run_id') AS origin,
           json_extract(material.value, '$.provenance.material_id') AS ident
    FROM runs JOIN json_each(runs.payload, '$.materials') AS material
    WHERE runs.project_id=:project AND runs.id>:origin
      AND json_type(material.value, '$.provenance.run_id')='integer'
), lineage(id, local_id) AS (
    SELECT :origin, :ident
    UNION
    SELECT copies.id, copies.local_id FROM copies JOIN lineage
      ON copies.origin=lineage.id AND copies.ident=lineage.local_id AND copies.id>lineage.id
    UNION
    SELECT parents.id, lineage.local_id FROM lineage JOIN parents ON parents.parent=lineage.id
    JOIN runs ON runs.id=parents.id
    WHERE parents.id>lineage.id AND json_extract(runs.payload,
          '$.materials[' || (lineage.local_id-1) || '].id')=lineage.local_id
      AND json_extract(runs.payload, '$.materials[' || (lineage.local_id-1) || '].provenance.run_id') IS NULL
), matches AS (
    SELECT id, json_group_array(local_id) AS local_ids FROM lineage GROUP BY id
)
SELECT runs.id, runs.name, runs.stage, runs.payload, matches.local_ids
FROM matches JOIN runs ON runs.id=matches.id WHERE runs.stage>=3 ORDER BY runs.id DESC
"""


def mount_references(app, database, project_settings, topic_rows):
    @app.get("/api/projects/{project_id}/references/{run_id}/{material_id}")
    def references(project_id: Identifier, run_id: Identifier, material_id: Identifier):
        topics, drafts = [], []
        with database() as conn:
            conn.execute("BEGIN")
            project_settings(conn, project_id)
            origin, ident = material_origin(conn, project_id, run_id, material_id)
            for run in conn.execute(
                MATCHED_RUNS, {"project": project_id, "origin": origin, "ident": ident}
            ):
                local_ids = set(json.loads(run["local_ids"]))
                data = json.loads(run["payload"])
                materials = {row["id"]: row for row in data["materials"]}
                insights = {row["id"]: row["quote"] for row in data.get("insights", [])}
                for key, topic, _ in topic_rows(
                    data, materials, insights, run["stage"]
                ):
                    if local_ids.intersection(topic.get("ids") or [topic.get("id")]):
                        topics.append(
                            {
                                "run_id": run["id"],
                                "key": str(key),
                                "title": topic.get("title", run["name"]),
                            }
                        )
                selected = data.get("topic") or {}
                if data.get("drafts") and local_ids.intersection(
                    selected.get("ids") or [selected.get("id")]
                ):
                    drafts.append(
                        {
                            "run_id": run["id"],
                            "key": "draft",
                            "title": selected.get("title", run["name"]),
                        }
                    )
        return {"topics": topics, "drafts": drafts}
