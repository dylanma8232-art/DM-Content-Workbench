import hashlib
import json
import time
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

import connections

Kind = Literal["reports", "drafts"]
KEYS = ("platform", "account", "day", "followers", "views", "engagements", "published")


class Connector(BaseModel):
    url: str = Field(max_length=2000)
    token_alias: str = Field(
        default="", pattern="^$|^DM_API_TOKEN_[A-Z0-9_]+$", max_length=100
    )
    records_path: str = Field(default="", max_length=200)
    next_path: str = Field(default="", max_length=200)
    mapping: dict[str, str] = Field(default_factory=lambda: dict(zip(KEYS, KEYS)))


class Confirm(BaseModel):
    preview_id: str = Field(min_length=64, max_length=64)


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def initialize(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS connectors(project_id INTEGER, kind TEXT,
        config TEXT NOT NULL, status TEXT DEFAULT '', PRIMARY KEY(project_id,kind))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS webhook_receipts(id TEXT PRIMARY KEY,
        sent INTEGER DEFAULT 0, lease REAL DEFAULT 0)""")


def mount(app, database, project_settings, account_day, write_account_day, get_run):
    def config_for(conn, project, kind):
        project_settings(conn, project)
        row = conn.execute(
            "SELECT config FROM connectors WHERE project_id=? AND kind=?",
            (project, kind),
        ).fetchone()
        if not row:
            raise HTTPException(409, "请先在第三方连接中保存接口配置")
        return Connector.model_validate_json(row["config"])

    def status(conn, project, kind, config, message):
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        conn.execute(
            "UPDATE connectors SET status=? WHERE project_id=? AND kind=? AND config=?",
            (stamp + " · " + message, project, kind, config.model_dump_json()),
        )

    @app.get("/api/projects/{project}/connectors")
    def list_connectors(project: int):
        with database() as conn:
            project_settings(conn, project)
            return {
                row["kind"]: {**json.loads(row["config"]), "status": row["status"]}
                for row in conn.execute(
                    "SELECT * FROM connectors WHERE project_id=?", (project,)
                )
            }

    @app.put("/api/projects/{project}/connectors/{kind}")
    def save_connector(project: int, kind: Kind, config: Connector):
        url = urlsplit(config.url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.fragment
        ):
            raise HTTPException(422, "请填写完整的 HTTPS 地址，鉴权使用服务端令牌变量")
        if kind == "reports" and (
            set(config.mapping) != set(KEYS)
            or any(
                not path.strip() or len(path) > 200 for path in config.mapping.values()
            )
        ):
            raise HTTPException(422, "请填写全部七个日报字段的路径")
        with database() as conn:
            project_settings(conn, project)
            conn.execute(
                """INSERT INTO connectors(project_id,kind,config) VALUES(?,?,?)
                ON CONFLICT(project_id,kind) DO UPDATE SET config=excluded.config,status=''
                WHERE config<>excluded.config""",
                (project, kind, config.model_dump_json()),
            )
        return {"saved": True}

    @app.post("/api/projects/{project}/connectors/reports/preview")
    @app.post("/api/projects/{project}/connectors/reports/import")
    def reports(project: int, request: Request, confirmation: Confirm | None = None):
        commit = request.url.path.endswith("/import")
        with database() as conn:
            config = config_for(conn, project, "reports")
        try:
            rows = connections.json_records(
                config.url, config.records_path, config.next_path, config.token_alias
            )
            records = []
            seen = set()
            for index, row in enumerate(rows, 1):
                try:
                    item = account_day(
                        **{
                            key: connections.api_value(row, path)
                            for key, path in config.mapping.items()
                        }
                    )
                    item.platform, item.account = (
                        item.platform.strip(),
                        item.account.strip(),
                    )
                    identity = item.platform, item.account, item.day
                    if not all(identity) or identity in seen:
                        raise ValueError()
                except (ValidationError, KeyError, TypeError, ValueError) as exc:
                    raise HTTPException(
                        422, f"第 {index} 条日报字段缺失、无效或账号日期重复"
                    ) from exc
                seen.add(identity)
                records.append(item)
            snapshot = [item.model_dump(mode="json") for item in records]
            key = fingerprint([project, config.model_dump(), snapshot])
            if commit:
                if not confirmation or confirmation.preview_id != key:
                    raise HTTPException(409, "接口数据或配置已变化，请重新预览")
                with database() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    if config_for(conn, project, "reports") != config:
                        raise HTTPException(409, "配置已变化，请重新预览")
                    for item in records:
                        write_account_day(conn, project, item)
                    status(
                        conn, project, "reports", config, f"导入 {len(records)} 条日报"
                    )
            return {"count": len(records), "sample": snapshot[:3], "preview_id": key}
        except HTTPException as exc:
            with database() as conn:
                status(conn, project, "reports", config, str(exc.detail))
            raise

    @app.post("/api/runs/{run_id}/webhook/{version}/{action}")
    def webhook(
        run_id: int,
        version: int,
        action: Literal["preview", "send"],
        confirmation: Confirm | None = None,
    ):
        with database() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = get_run(conn, run_id)
            project = run["project_id"]
            data = run["payload"]
            reviews = [r for r in data.get("reviews", []) if r["version"] == version]
            if (
                version < 1
                or len(data.get("drafts", [])) != version
                or not reviews
                or reviews[-1]["decision"] != "通过"
            ):
                raise HTTPException(409, "仅可发送审核通过的当前版本")
            config = config_for(conn, project, "drafts")
            payload = {
                "schema_version": 1,
                "event": "draft.approved",
                "project_id": project,
                "run_id": run_id,
                "version": version,
                "title": data["topic"]["title"],
                "markdown": data["drafts"][version - 1],
            }
            workspace = conn.execute("SELECT id FROM workspace").fetchone()[0]
            key = fingerprint([workspace, config.url, payload])
            if action == "preview":
                return {"target": config.url, "payload": payload, "preview_id": key}
            if not confirmation or confirmation.preview_id != key:
                raise HTTPException(409, "目标或稿件已变化，请重新预览")
            conn.execute("INSERT OR IGNORE INTO webhook_receipts(id) VALUES(?)", (key,))
            receipt = conn.execute(
                "SELECT sent,lease FROM webhook_receipts WHERE id=?", (key,)
            ).fetchone()
            if receipt["sent"]:
                return {"message": "该版本已送达此接口"}
            if receipt["lease"] > time.time():
                raise HTTPException(409, "该版本正在发送")
            conn.execute(
                "UPDATE webhook_receipts SET lease=? WHERE id=?",
                (time.time() + 120, key),
            )
        try:
            connections.public_endpoint(config.url)
            headers = connections.api_headers(config.token_alias) or {}
            with httpx.stream(
                "POST",
                config.url,
                json=payload,
                headers={**headers, "Idempotency-Key": key},
                timeout=12,
                follow_redirects=False,
            ) as response:
                response.raise_for_status()
            message = f"版本 {version} 已送达（HTTP {response.status_code}）"
            sent = 1
        except (httpx.HTTPError, HTTPException) as exc:
            message = (
                str(exc.detail)
                if isinstance(exc, HTTPException)
                else (
                    f"接收端返回 HTTP {exc.response.status_code}"
                    if isinstance(exc, httpx.HTTPStatusError)
                    else "未收到确认，请核对接收端记录"
                )
            )
            sent = 0
        with database() as conn:
            conn.execute(
                "UPDATE webhook_receipts SET sent=?,lease=0 WHERE id=?", (sent, key)
            )
            status(conn, project, "drafts", config, message)
        if not sent:
            raise HTTPException(502, message)
        return {"message": message}
