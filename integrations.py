import hashlib
import json
import logging
import uuid
import time
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, StrictBool, ValidationError

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


REPORT_INTERVAL = 3600
REPORT_POLL = 60


class SyncSettings(BaseModel):
    enabled: StrictBool


class Confirm(BaseModel):
    preview_id: str = Field(min_length=64, max_length=64)


class DeliveryConfirm(Confirm):
    checked_attempt: int | None = Field(default=None, gt=0)


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def initialize(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS connectors(project_id INTEGER, kind TEXT,
        config TEXT NOT NULL, status TEXT DEFAULT '', PRIMARY KEY(project_id,kind))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS report_sync(
        project_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
        due REAL NOT NULL DEFAULT 0, token TEXT, lease_until REAL NOT NULL DEFAULT 0,
        last_success TEXT, message TEXT NOT NULL DEFAULT '')""")
    conn.execute("""CREATE TABLE IF NOT EXISTS webhook_receipts(id TEXT PRIMARY KEY,
        sent INTEGER DEFAULT 0, lease REAL DEFAULT 0)""")
    conn.execute('''CREATE TABLE IF NOT EXISTS webhook_dispatches (
        id INTEGER PRIMARY KEY, receipt TEXT, project INTEGER, run_id INTEGER, version INTEGER,
        target TEXT, approval TEXT, state TEXT, detail TEXT, started TEXT DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('CREATE INDEX IF NOT EXISTS webhook_dispatch_receipt ON webhook_dispatches(receipt,id)')


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
            conn.execute("BEGIN IMMEDIATE")
            saved = conn.execute(
                """INSERT INTO connectors(project_id,kind,config) VALUES(?,?,?)
                ON CONFLICT(project_id,kind) DO UPDATE SET config=excluded.config,status=''
                WHERE config<>excluded.config""",
                (project, kind, config.model_dump_json()),
            )
            if kind == 'reports' and saved.rowcount:
                conn.execute("""UPDATE report_sync SET token=NULL, lease_until=0,
                    due=0, last_success=NULL, message='' WHERE project_id=?""", (project,))
        return {"saved": True}

    def fetch_reports(config):
        rows = connections.json_records(
            config.url, config.records_path, config.next_path, config.token_alias
        )
        records = []
        seen = set()
        for index, row in enumerate(rows, 1):
            try:
                item = account_day(**{
                    key: connections.api_value(row, path)
                    for key, path in config.mapping.items()
                })
                item.platform, item.account = item.platform.strip(), item.account.strip()
                identity = item.platform, item.account, item.day
                if not all(identity) or identity in seen:
                    raise ValueError()
            except (ValidationError, KeyError, TypeError, ValueError) as exc:
                raise HTTPException(
                    422, f"第 {index} 条日报字段缺失、无效或账号日期重复"
                ) from exc
            seen.add(identity)
            records.append(item)
        return records

    def write_reports(conn, project, records):
        for item in records:
            write_account_day(conn, project, item)

    @app.post("/api/projects/{project}/connectors/reports/preview")
    @app.post("/api/projects/{project}/connectors/reports/import")
    def reports(project: int, request: Request, confirmation: Confirm | None = None):
        commit = request.url.path.endswith("/import")
        with database() as conn:
            config = config_for(conn, project, "reports")
        try:
            records = fetch_reports(config)
            snapshot = [item.model_dump(mode="json") for item in records]
            key = fingerprint([project, config.model_dump(), snapshot])
            if commit:
                if not confirmation or confirmation.preview_id != key:
                    raise HTTPException(409, "接口数据或配置已变化，请重新预览")
                with database() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    if config_for(conn, project, "reports") != config:
                        raise HTTPException(409, "配置已变化，请重新预览")
                    write_reports(conn, project, records)
                    conn.execute("UPDATE report_sync SET token=NULL, lease_until=0 WHERE project_id=?", (project,))
                    status(
                        conn, project, "reports", config, f"导入 {len(records)} 条日报"
                    )
            return {"count": len(records), "sample": snapshot[:3], "preview_id": key,
                    "latest": max((item['day'] for item in snapshot),default=None)}
        except HTTPException as exc:
            with database() as conn:
                status(conn, project, "reports", config, str(exc.detail))
            raise

    def sync_state(conn, project):
        project_settings(conn, project)
        row = conn.execute("""SELECT s.enabled, s.last_success, s.message FROM connectors c
            LEFT JOIN report_sync s USING(project_id) WHERE project_id=? AND kind='reports'""",
            (project,)).fetchone()
        return {"configured": row is not None, "enabled": bool(row and row['enabled']),
                "last_success": row['last_success'] if row else None,
                "message": (row['message'] or '') if row else '请先保存日报接口配置'}

    @app.get('/api/projects/{project}/report-sync')
    def get_sync(project: int):
        with database() as conn:
            return sync_state(conn, project)

    @app.put('/api/projects/{project}/report-sync')
    def set_sync(project: int, form: SyncSettings):
        with database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if form.enabled:
                config_for(conn, project, 'reports')
            conn.execute('''INSERT INTO report_sync(project_id,enabled) VALUES(?,?)
                ON CONFLICT(project_id) DO UPDATE SET enabled=excluded.enabled, due=0,
                token=NULL, lease_until=0 WHERE enabled<>excluded.enabled''', (project, form.enabled))
            return sync_state(conn, project)

    def sync_reports(project, automatic=False):
        token = uuid.uuid4().hex
        with database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            config = config_for(conn, project, 'reports')
            conn.execute('INSERT OR IGNORE INTO report_sync(project_id) VALUES(?)', (project,))
            now = time.time()
            claimed = conn.execute('''UPDATE report_sync SET token=?, lease_until=?
                WHERE project_id=? AND lease_until<=? AND (?=0 OR (enabled=1 AND due<=?))''',
                (token, now + REPORT_INTERVAL, project, now, automatic, now))
            if not claimed.rowcount:
                if automatic:
                    return
                raise HTTPException(409, '日报正在同步')
        try:
            records = fetch_reports(config)
            message = f'已同步 {len(records)} 条日报'
            with database() as conn:
                conn.execute('BEGIN IMMEDIATE')
                finished = conn.execute('''UPDATE report_sync SET token=NULL, lease_until=0,
                    due=?, last_success=?, message=? WHERE project_id=? AND token=?''',
                    (time.time() + REPORT_INTERVAL, datetime.now(UTC).isoformat(), message, project, token))
                if not finished.rowcount or config_for(conn, project, 'reports') != config:
                    raise HTTPException(409, '同步设置或配置已变化，请重新同步')
                write_reports(conn, project, records)
                status(conn, project, 'reports', config, message)
            return {'count': len(records), 'latest': max((item.day for item in records), default=None), 'message': message}
        except Exception as exc:
            message = str(exc.detail) if isinstance(exc, HTTPException) else '日报同步失败'
            with database() as conn:
                conn.execute('''UPDATE report_sync SET token=NULL, lease_until=0, message=?, due=?
                    WHERE project_id=? AND token=?''', (message, time.time() + REPORT_INTERVAL, project, token))
            raise

    @app.post('/api/projects/{project}/connectors/reports/sync')
    def manual_sync(project: int):
        return sync_reports(project)

    def sync_due(stop):
        with database() as conn:
            now = time.time()
            projects = conn.execute('''SELECT project_id FROM report_sync
                WHERE enabled=1 AND due<=? AND lease_until<=?''', (now, now)).fetchall()
        for row in projects:
            if stop.is_set():
                break
            try:
                sync_reports(row['project_id'], automatic=True)
            except Exception as exc:
                logging.getLogger(__name__).warning('项目 %s 日报同步失败 (%s)', row['project_id'], type(exc).__name__)

    app.state.sync_due_reports = sync_due

    @app.get('/api/runs/{run_id}/webhook-receipts')
    def dispatches(run_id: int):
        with database() as conn:
            run = get_run(conn, run_id)
            rows = conn.execute('''SELECT d.id,d.receipt,d.version,d.target,d.approval,
                CASE WHEN d.state='发送中' AND r.lease<=? THEN '送达待核对' ELSE d.state END AS state,
                d.detail,d.started FROM webhook_dispatches d JOIN webhook_receipts r ON r.id=d.receipt
                WHERE d.run_id=? AND d.project=? ORDER BY d.id DESC''', (time.time(), run_id, run['project_id']))
            return [dict(row) | {'approval': json.loads(row['approval'])} for row in rows]

    @app.post("/api/runs/{run_id}/webhook/{version}/{action}")
    def webhook(
        run_id: int,
        version: int,
        action: Literal["preview", "send"],
        confirmation: DeliveryConfirm | None = None,
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
            previous = conn.execute('SELECT id,state FROM webhook_dispatches WHERE receipt=? ORDER BY id DESC LIMIT 1', (key,)).fetchone()
            receipt = conn.execute('SELECT sent,lease FROM webhook_receipts WHERE id=?', (key,)).fetchone()
            busy = receipt and receipt['lease'] > time.time()
            uncertain = previous and previous['state'] in ('发送中', '送达待核对') and not busy
            if action == "preview":
                state = '待发送'
                if receipt and receipt['sent']:
                    state = '已送达'
                elif busy:
                    state = '发送中'
                elif uncertain:
                    state = '送达待核对'
                return {"target": config.url, "payload": payload, "preview_id": key,
                        "checked_attempt": previous['id'] if state == '送达待核对' else None, 'state': state}
            if not confirmation or confirmation.preview_id != key:
                raise HTTPException(409, "目标或稿件已变化，请重新预览")
            conn.execute("INSERT OR IGNORE INTO webhook_receipts(id) VALUES(?)", (key,))
            if receipt and receipt['sent']:
                return {"message": "该版本已送达此接口"}
            if busy:
                raise HTTPException(409, "该版本正在发送")
            if uncertain and confirmation.checked_attempt != previous['id']:
                raise HTTPException(409, '送达待核对，请先核对接收端记录，再确认重试本次投递')
            if uncertain and previous['state'] == '发送中':
                conn.execute("UPDATE webhook_dispatches SET state='送达待核对',detail='上次发送未返回回执' WHERE id=?", (previous['id'],))
            conn.execute(
                "UPDATE webhook_receipts SET lease=? WHERE id=?",
                (time.time() + 120, key),
            )
            dispatch = conn.execute('''INSERT INTO webhook_dispatches(receipt,project,run_id,version,target,approval,state,detail)
                VALUES(?,?,?,?,?,?,'发送中','已核验此版本的审核记录')''',
                (key, project, run_id, version, config.url, json.dumps(reviews[-1], ensure_ascii=False))).lastrowid
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
            outcome = '已送达'
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
            uncertain_response = isinstance(exc, httpx.RequestError) or (isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500)
            outcome = '送达待核对' if uncertain_response else '发送失败'
        with database() as conn:
            conn.execute('UPDATE webhook_dispatches SET state=?,detail=? WHERE id=?', (outcome,message,dispatch))
            conn.execute(
                """UPDATE webhook_receipts SET sent=max(sent,?),
                lease=CASE WHEN (SELECT max(id) FROM webhook_dispatches WHERE receipt=?)=? THEN 0 ELSE lease END
                WHERE id=?""", (sent, key, dispatch, key)
            )
            status(conn, project, "drafts", config, message)
        if not sent:
            raise HTTPException(502, message)
        return {"message": message}
