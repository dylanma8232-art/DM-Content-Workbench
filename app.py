import csv
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

import integrations
import connections
import extraction
from backup import copy_database, workspace_path
from analysis import recommendations
from collection import mount_collection
from references import mount_references

ROOT = Path(__file__).resolve().parent
DRAFT_ORDER = "replace(coalesce(json_extract(payload,'$.draft_updated'),created),'T',' ')"
INGESTED_RUN = "json_extract(payload,'$.parent') IS NULL AND coalesce(json_extract(payload,'$.config.format'),'') <> 'collection'"


@asynccontextmanager
async def lifespan(application):
    with database() as conn:
        initialize(conn)
        conn.execute(f"""CREATE INDEX IF NOT EXISTS library_draft_order
                      ON runs(project_id, {DRAFT_ORDER}, id) WHERE stage=5""")
        conn.execute(f"""CREATE INDEX IF NOT EXISTS library_ingested_order
                      ON runs(project_id, id) WHERE {INGESTED_RUN}""")
    stop = threading.Event()
    worker = threading.Thread(target=schedule, args=(stop,), daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop.set()
        worker.join(timeout=1)


app = FastAPI(title="DM-内容工作台", lifespan=lifespan)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"],
)


def data_directory():
    with workspace_path() as (folder, _):
        return folder


@contextmanager
def database():
    with workspace_path() as (folder, _):
        if not folder.is_dir():
            raise RuntimeError('已配置的数据目录不可用，请检查磁盘挂载')
        conn = sqlite3.connect(folder / "workbench.sqlite3", timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                conn.execute("""CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, stage INTEGER NOT NULL,
                    payload TEXT NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP)""")
                yield conn
        finally:
            conn.close()


class StorageLocation(BaseModel):
    directory: str = Field(min_length=1, max_length=2048)


@app.put('/api/storage')
def set_storage(form: StorageLocation):
    path = Path(form.directory.strip()).expanduser()
    if not path.is_absolute():
        raise HTTPException(422, '请输入运行工作台的电脑上的绝对目录路径')
    try:
        destination = path.resolve()
        if destination.is_relative_to((ROOT / 'web').resolve()):
            raise HTTPException(422, '数据不能存放在公开的网页目录中')
        with workspace_path(exclusive=True) as (current, registry):
            if destination != current:
                copy_database(current / 'workbench.sqlite3', destination / 'workbench.sqlite3')
                registry.execute('INSERT OR REPLACE INTO location VALUES(1,?)', (str(destination),))
    except FileExistsError as exc:
        raise HTTPException(409, '该目录已有同名数据库，请选择新的目录') from exc
    except (OSError, ValueError, sqlite3.Error) as exc:
        raise HTTPException(422, '无法复制或验证数据库，请检查路径、磁盘空间与写入权限') from exc
    return {'data_dir': str(destination)}


class ImportForm(BaseModel):
    project_id: int = Field(default=1, ge=1)
    name: str = Field(min_length=1, max_length=100)
    filename: str = Field(max_length=255)
    format: Literal["csv", "json", "txt", "md"]
    content: str = Field(min_length=1, max_length=1000000)
    body_field: str = Field(default="body", max_length=100)
    title_field: str = Field(default="title", max_length=100)
    min_length: int = Field(default=1, ge=1, le=10000)
    focus: str = Field(default="", max_length=200)
    extracted_from: Literal["pdf", "media"] | None = None


class Action(BaseModel):
    stage: int = Field(ge=1, le=4)
    topic_id: int | None = None
    candidate_key: str | None = Field(default=None, max_length=100)
    title: str = Field(default="", max_length=200)
    engine: Literal["local", "model"] = "local"
    audience: str = Field(default="", max_length=200)
    style: str = Field(default="文章", max_length=100)


class Edit(BaseModel):
    content: str = Field(min_length=1, max_length=1000000)
    version: int = Field(ge=1)


def parse_materials(form):
    content = form.content.lstrip("\ufeff")
    body_field, title_field = form.body_field, form.title_field
    if form.format in ("md", "txt"):
        rows = [{"body": content, "title": form.filename or form.name}]
        body_field, title_field = "body", "title"
    else:
        try:
            rows = (
                list(csv.DictReader(io.StringIO(content), strict=True))
                if form.format == "csv"
                else json.loads(content)
            )
        except (ValueError, csv.Error) as exc:
            raise HTTPException(422, "文件格式无法解析，请检查内容或格式选择") from exc
    if (
        not isinstance(rows, list)
        or not 1 <= len(rows) <= 2000
        or not all(isinstance(row, dict) for row in rows)
    ):
        raise HTTPException(422, "请输入含 1 至 2000 条对象记录的文件")
    if not any(body_field in row for row in rows):
        raise HTTPException(422, "未找到正文字段，请核对列名")
    return [
        {
            "id": index,
            "title": str(row.get(title_field) or f"素材 {index}"),
            "body": row.get(body_field) if isinstance(row.get(body_field), str) else "",
            "raw": row,
        }
        for index, row in enumerate(rows, 1)
    ]


def get_run(conn, run_id):
    row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "找不到这次运行")
    return {**dict(row), "payload": json.loads(row["payload"])}


def persist(conn, run):
    conn.execute(
        "UPDATE runs SET stage=?, payload=? WHERE id=?",
        (run["stage"], json.dumps(run["payload"], ensure_ascii=False), run["id"]),
    )
    enqueue_snapshot(conn, run)
    return run


def initialize(conn):
    integrations.initialize(conn)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY, name TEXT UNIQUE, settings TEXT)"
    )
    conn.execute("INSERT OR IGNORE INTO projects VALUES(1,'默认项目','{}')")
    if "project_id" not in [
        row["name"] for row in conn.execute("PRAGMA table_info(runs)")
    ]:
        conn.execute(
            "ALTER TABLE runs ADD COLUMN project_id INTEGER NOT NULL DEFAULT 1"
        )
    conn.execute("""CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY, project_id INTEGER,
        name TEXT, kind TEXT, address TEXT, title_field TEXT, body_field TEXT, minutes INTEGER,
        due REAL DEFAULT 0, lease TEXT, lease_until REAL DEFAULT 0, status TEXT DEFAULT '尚未采集')""")
    source_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sources)")}
    for column in ("records_path", "id_field", "next_path", "token_alias", "platform"):
        if column not in source_columns:
            conn.execute(
                f"ALTER TABLE sources ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
            )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS seen(source INTEGER, key TEXT, hash TEXT, PRIMARY KEY(source,key))"
    )
    conn.execute("CREATE TABLE IF NOT EXISTS workspace(id TEXT PRIMARY KEY)")
    conn.execute('''CREATE TABLE IF NOT EXISTS platform_accounts (
        project_id INTEGER NOT NULL, platform TEXT NOT NULL, account TEXT NOT NULL,
        PRIMARY KEY(project_id, platform, account))''')
    conn.execute("""CREATE TABLE IF NOT EXISTS account_daily (
        project_id INTEGER NOT NULL, platform TEXT NOT NULL, account TEXT NOT NULL,
        day TEXT NOT NULL, followers INTEGER NOT NULL, views INTEGER NOT NULL,
        engagements INTEGER NOT NULL, published INTEGER NOT NULL,
        PRIMARY KEY(project_id, platform, account, day))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS batches (
        id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, source_id INTEGER, run_id INTEGER,
        name TEXT NOT NULL, received INTEGER, added INTEGER, updated INTEGER, duplicates INTEGER,
        error TEXT, created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("CREATE INDEX IF NOT EXISTS batches_project ON batches(project_id,id)")
    if not conn.execute("SELECT id FROM workspace").fetchone():
        conn.execute("INSERT INTO workspace VALUES(?)", (uuid.uuid4().hex,))
    conn.execute("CREATE TABLE IF NOT EXISTS onboarding (id INTEGER PRIMARY KEY CHECK(id=1), project_id INTEGER NOT NULL)")
    conn.execute("""INSERT OR IGNORE INTO onboarding SELECT 1, 1 WHERE
                  EXISTS(SELECT 1 FROM runs) OR EXISTS(SELECT 1 FROM sources) OR
                  EXISTS(SELECT 1 FROM projects WHERE id<>1 OR settings<>'{}')""")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS deliveries(key TEXT PRIMARY KEY, page TEXT, status TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS generating(run_id INTEGER PRIMARY KEY, owner TEXT, expires REAL)"
    )
    conn.execute("""CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY,run_id INTEGER, target TEXT,
        snapshot TEXT, state TEXT DEFAULT '待同步', error TEXT DEFAULT '', lease_until REAL DEFAULT 0)""")


class Project(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class Rules(BaseModel):
    focus: str = Field(default="", max_length=200)
    min_length: int = Field(default=1, ge=1, le=10000)
    audience: str = Field(default="", max_length=200)
    style: str = Field(default="文章", max_length=100)
    notion_database: str = Field(default="", max_length=100)
    topics: str = Field(default="", max_length=500)


class SetupForm(BaseModel):
    platform: str = Field(default='',max_length=80)
    source_address: str = Field(default='',max_length=2000)
    source_kind: Literal['rss','web','api','notion'] = 'rss'
    minutes: int = Field(default=0,ge=0,le=43200)
    name: str = Field(min_length=1,max_length=100)
    audience: str = Field(default='',max_length=200)
    style: str = Field(default='文章',max_length=100)


@app.get('/api/setup')
def setup_status():
    with database() as conn:
        setup = conn.execute('SELECT project_id FROM onboarding WHERE id=1').fetchone()
    return {'complete':setup is not None,'project_id':setup['project_id'] if setup else 1}


@app.post('/api/setup')
def complete_setup(form:SetupForm):
    name=form.name.strip()
    if not name:
        raise HTTPException(422,'请填写项目名称')
    with database() as conn:
        conn.execute('BEGIN IMMEDIATE')
        existing=conn.execute('SELECT project_id FROM onboarding WHERE id=1').fetchone()
        if existing:
            return {'project_id':existing['project_id']}
        rules=Rules(audience=form.audience,style=form.style.strip() or '文章')
        existing_project=conn.execute('SELECT id,settings FROM projects WHERE name=?',(name,)).fetchone()
        if existing_project:
            project_id=existing_project['id']
        else:
            project_id=conn.execute('INSERT INTO projects(name,settings) VALUES(?,?)',(name,rules.model_dump_json())).lastrowid
        if existing_project and existing_project['settings']=='{}':
            conn.execute('UPDATE projects SET settings=? WHERE id=?',(rules.model_dump_json(),project_id))
        if form.source_address.strip():
            if not form.platform.strip():
                raise HTTPException(422,'请填写首个采集平台名称')
            conn.execute('''INSERT INTO sources(project_id,name,platform,kind,address,minutes,title_field,body_field)
                VALUES(?,?,?,?,?,?,?,?)''', (project_id, form.platform.strip(), form.platform.strip(),
                form.source_kind, form.source_address.strip(), form.minutes,
                'Name' if form.source_kind == 'notion' else 'title',
                'Body' if form.source_kind == 'notion' else 'body'))
        conn.execute('INSERT INTO onboarding VALUES(1,?)',(project_id,))
        return {'project_id':project_id}


def project_settings(conn, project_id):
    row = conn.execute(
        "SELECT settings FROM projects WHERE id=?", (project_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "项目不存在")
    return Rules(**json.loads(row["settings"])).model_dump()


class PlatformAccount(BaseModel):
    platform: str = Field(min_length=1, max_length=40)
    account: str = Field(min_length=1, max_length=100)


class AccountDay(PlatformAccount):
    day: date
    followers: int = Field(ge=0, le=10**12)
    views: int = Field(ge=0, le=10**12)
    engagements: int = Field(ge=0, le=10**12)
    published: int = Field(ge=0, le=10**8)


def platform_identity(report):
    platform, account = report.platform.strip(), report.account.strip()
    if not platform or not account:
        raise HTTPException(422, '平台和账号名称不能为空')
    return platform, account


@app.put('/api/projects/{project_id}/platform-accounts')
def register_account(project_id: int, settings: PlatformAccount):
    with database() as conn:
        project_settings(conn, project_id)
        conn.execute('INSERT OR IGNORE INTO platform_accounts VALUES(?,?,?)',
                     (project_id, *platform_identity(settings)))
    return {'saved': True}


@app.put('/api/projects/{project_id}/account-daily')
def save_account_day(project_id: int, report: AccountDay):
    with database() as conn:
        project_settings(conn, project_id)
        write_account_day(conn, project_id, report)
    return {'saved': True}


def write_account_day(conn, project_id, report):
    platform, account = platform_identity(report)
    conn.execute('''INSERT INTO account_daily VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(project_id,platform,account,day) DO UPDATE SET
            followers=excluded.followers, views=excluded.views,
            engagements=excluded.engagements, published=excluded.published''',
            (project_id, platform, account, report.day.isoformat(), report.followers,
             report.views, report.engagements, report.published))


class AccountImport(PlatformAccount):
    content: str = Field(min_length=1, max_length=250000)


@app.post('/api/projects/{project_id}/account-import')
def import_account_csv(project_id: int, source: AccountImport):
    columns = {'日期':'day', '粉丝':'followers', '阅读或播放':'views', '互动':'engagements', '发布':'published'}
    reader = csv.DictReader(io.StringIO(source.content.lstrip('\ufeff')), strict=True)
    reports = {}
    try:
        if reader.fieldnames is None or not set(columns).issubset(reader.fieldnames):
            raise HTTPException(422, 'CSV 表头需要包含：' + '、'.join(columns))
        for row in reader:
            report = AccountDay(platform=source.platform, account=source.account,
                                **{key:row[label] for label,key in columns.items()})
            if report.day in reports:
                raise HTTPException(422, f'日期重复：{report.day}')
            reports[report.day] = report
    except (ValueError, csv.Error) as exc:
        raise HTTPException(422, f'第 {reader.line_num} 行日期或指标无效') from exc
    if not reports:
        raise HTTPException(422, 'CSV 中没有日报记录')
    with database() as conn:
        project_settings(conn, project_id)
        for report in reports.values():
            write_account_day(conn, project_id, report)
    return {'count':len(reports), 'latest':max(reports).isoformat()}


@app.get('/api/projects/{project_id}/account-daily')
def account_daily(project_id: int, day: date):
    with database() as conn:
        project_settings(conn, project_id)
        rows = conn.execute('''WITH accounts AS (SELECT DISTINCT platform, account FROM
            account_daily WHERE project_id=:project AND day<=:day UNION SELECT platform,account
            FROM platform_accounts WHERE project_id=:project)
            SELECT a.*, d.followers, d.views, d.engagements, d.published,
                   d.followers - p.followers AS growth FROM accounts a
            LEFT JOIN account_daily d ON d.project_id=:project AND d.day=:day
                AND d.platform=a.platform AND d.account=a.account
            LEFT JOIN account_daily p ON p.project_id=:project AND p.day=date(:day,'-1 day')
                AND p.platform=a.platform AND p.account=a.account
            ORDER BY a.platform, a.account''', {'project': project_id, 'day': day.isoformat()})
        accounts = [dict(row) for row in rows]
        trend = conn.execute('''SELECT day, SUM(views) AS views, COUNT(*) AS reported
            FROM account_daily WHERE project_id=? AND day BETWEEN date(?,'-6 days') AND ?
            GROUP BY day ORDER BY day''', (project_id, day.isoformat(), day.isoformat()))
        return {'accounts': accounts, 'trend': [dict(row) for row in trend]}


@app.get("/api/projects")
def projects():
    with database() as conn:
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "settings": project_settings(conn, row["id"]),
            }
            for row in conn.execute("SELECT * FROM projects")
        ]


@app.post("/api/projects")
def add_project(project: Project):
    with database() as conn:
        try:
            return {
                "id": conn.execute(
                    "INSERT INTO projects(name,settings) VALUES(?,?)",
                    (project.name, "{}"),
                ).lastrowid
            }
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "已有同名项目") from exc


@app.put("/api/projects/{project_id}")
def save_rules(project_id: int, rules: Rules):
    if rules.notion_database:
        rules.notion_database = connections.database_id(rules.notion_database)
        connections.notion("GET", "databases/" + rules.notion_database)
    with database() as conn:
        project_settings(conn, project_id)
        conn.execute(
            "UPDATE projects SET settings=? WHERE id=?",
            (rules.model_dump_json(), project_id),
        )
    return rules


def insert_run(conn, name, config, materials, **extra):
    defaults = project_settings(conn, config.get("project_id", 1))
    payload = {"config": {**defaults, **config}, "materials": materials, **extra}
    cursor = conn.execute(
        "INSERT INTO runs(name,stage,payload,project_id) VALUES(?,1,?,?)",
        (name, json.dumps(payload, ensure_ascii=False), config.get("project_id", 1)),
    )
    run = get_run(conn, cursor.lastrowid)
    enqueue_snapshot(conn, run)
    return run


def enqueue_snapshot(conn, run):
    target = run["payload"]["config"].get("notion_database")
    if target:
        conn.execute(
            "INSERT INTO outbox(run_id,target,snapshot) VALUES(?,?,?)",
            (run["id"], target, notion_snapshot(run)),
        )


def clean(materials, minimum):
    seen = set()
    result = []
    for item in materials:
        body = " ".join(item["body"].split())
        if len(body) < minimum:
            reason = "低于最短正文长度"
        elif body in seen:
            reason = "正文重复"
        else:
            reason = "保留"
            seen.add(body)
        result.append({"id": item["id"], "body": body, "reason": reason})
    return result


@app.post("/api/import-preview")
def preview_import(form: ImportForm):
    rows = clean(parse_materials(form), form.min_length)
    return {
        "总数": len(rows),
        "保留": sum(row["reason"] == "保留" for row in rows),
        "前五条": rows[:5],
    }


@app.middleware("http")
async def local_writes(request: Request, call_next):
    if request.method in ("POST", "PUT") and request.headers.get("origin") not in (
        None,
        str(request.base_url).rstrip("/"),
    ):
        return PlainTextResponse("跨来源写入已拒绝", status_code=403)
    return await call_next(request)


@app.get("/api/projects/{project_id}/overview")
def overview(project_id: int):
    with database() as conn:
        conn.execute("BEGIN")
        project_settings(conn, project_id)
        rows = conn.execute(
            """
            SELECT id, name, stage, created,
                   json_extract(payload, '$.topic.title') AS topic,
                   json_array_length(payload, '$.drafts') AS versions,
                   json_array_length(payload, '$.insights') AS insights,
                   json_array_length(payload, '$.materials') AS materials,
                   (SELECT count(*) FROM json_each(json_extract(runs.payload,'$.cleaned'))
                    WHERE json_extract(value,'$.reason')='保留') AS kept,
                   json_array_length(payload, '$.recommendations') AS candidates,
                   (SELECT json_extract(value,'$.decision') FROM json_each(json_extract(runs.payload,'$.reviews'))
                    WHERE json_extract(value,'$.version')=json_array_length(runs.payload,'$.drafts')
                    ORDER BY key DESC LIMIT 1) AS review
              FROM runs WHERE project_id = ? ORDER BY id DESC
        """,
            (project_id,),
        ).fetchall()
        sources_summary = conn.execute(
            """
            SELECT id, name, kind, status, minutes FROM sources
             WHERE project_id = ? ORDER BY id DESC
        """,
            (project_id,),
        ).fetchall()
        conflicts = conn.execute(
            """
            SELECT runs.id, runs.name, COUNT(*) AS count FROM outbox
              JOIN runs ON runs.id = outbox.run_id
             WHERE runs.project_id = ? AND outbox.state = '待处理'
             GROUP BY runs.id ORDER BY runs.id DESC
        """,
            (project_id,),
        ).fetchall()
        batches = conn.execute("""SELECT * FROM batches WHERE project_id=?
                               ORDER BY id DESC LIMIT 8""", (project_id,)).fetchall()
        failures = conn.execute("""SELECT b.name,b.source_id,b.error FROM batches AS b
            WHERE b.project_id=? AND b.error IS NOT NULL AND b.source_id IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM batches AS newer WHERE newer.project_id=b.project_id
                            AND newer.source_id=b.source_id AND newer.id>b.id)""", (project_id,)).fetchall()
    counts = [0] * 5
    for row in rows:
        counts[row["stage"] - 1] += 1
    return {
        "runs": [dict(row) for row in rows],
        "by_stage": counts,
        "sources": [dict(row) for row in sources_summary],
        "conflicts": [dict(row) for row in conflicts],
        "batches": [dict(row) for row in batches],
        "failures": [dict(row) for row in failures],
    }


@app.get("/api/runs")
def history(project_id: int = 1):
    with database() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT id,name,stage,created FROM runs WHERE project_id=? ORDER BY id DESC",
                (project_id,),
            )
        ]


@app.post("/api/import-extract")
def extract_file(file: UploadFile):
    filename = Path(file.filename or "").name
    suffix = Path(filename).suffix.lower()
    if suffix not in extraction.MEDIA and suffix != ".pdf":
        raise HTTPException(422, "请选择 PDF 或支持的音视频文件")
    limit = 25_000_000 if suffix in extraction.MEDIA else 10_000_000
    content = file.file.read(limit + 1)
    if not content or len(content) > limit:
        raise HTTPException(422, f"文件为空或超过 {limit // 1_000_000} MB")
    if suffix == ".pdf":
        rows = extraction.pdf_rows(content)
        rows = [
            {**row, "title": f"{filename} · 第 {row['page']} 页"}
            for row in rows
            if row["body"]
        ]
    else:
        rows = [{"title": filename, "body": extraction.transcript(content, suffix)}]
    if not rows or not all(
        isinstance(row["body"], str) and row["body"].strip() for row in rows
    ):
        raise HTTPException(
            422, "未提取到正文。扫描 PDF 请先 OCR，音视频请检查是否有人声"
        )
    material = json.dumps(rows, ensure_ascii=False)
    if len(material) > 1_000_000:
        raise HTTPException(422, "提取结果超过 100 万字符，请分段导入")
    return {"content": material, "type": "pdf" if suffix == ".pdf" else "media"}


@app.post("/api/runs", status_code=201)
def create(form: ImportForm):
    materials = parse_materials(form)
    with database() as conn:
        run = insert_run(
            conn, form.name, form.model_dump(exclude={"content"}), materials
        )
        conn.execute("""INSERT INTO batches(project_id,run_id,name,received,added,updated,duplicates)
            VALUES(?,?,?,?,?,0,0)""", (form.project_id,run['id'],form.filename or form.name,len(materials),len(materials)))
        return run


def copy_materials(run):
    data = run['payload']
    source = data['config'].get('filename') or run['name']
    return [{**item, 'provenance': item.get('provenance') or {
        'run_id': run['id'], 'material_id': item['id'], 'source': source, 'title': item['title']
    }} for item in data['materials']]


@app.post("/api/runs/{run_id}/replay")
def replay(run_id: int, rules: Rules):
    with database() as conn:
        run = get_run(conn, run_id)
        data = run["payload"]
        return insert_run(
            conn,
            run["name"] + " · 重算",
            {**data["config"], **rules.model_dump(), "project_id": run["project_id"]},
            copy_materials(run),
            parent=run_id,
        )


@app.post("/api/runs/{run_id}/variant")
def variant(run_id: int):
    with database() as conn:
        run = get_run(conn, run_id)
        if run["stage"] != 5:
            raise HTTPException(409, "请先完成第一份稿件")
        data = run["payload"]
        result = insert_run(
            conn,
            run["name"] + " · 衍生稿",
            {**data["config"], "project_id": run["project_id"]},
            copy_materials(run),
            parent=run_id,
        )
        result["payload"].update({key: data[key] for key in
                                  ("cleaned", "insights", "topic", "recommendations", "themes") if key in data})
        result["stage"] = 4
        return persist(conn, result)


@app.get("/api/runs/{run_id}")
def detail(run_id: int):
    with database() as conn:
        return get_run(conn, run_id)


def topic_rows(data, materials, insights, stage):
    selected = data.get("topic", {})
    candidates = data.get("recommendations") or [
        {
            "key": str(ident),
            "title": materials[ident]["title"],
            "ids": [ident],
            "reason": "有一条可追溯原句",
        }
        for ident in insights
        if ident in materials
    ]
    selected_ids = selected.get("ids") or [selected.get("id")]
    selected_key = selected.get("key") or next(
        (str(row["key"]) for row in candidates if row["ids"] == selected_ids),
        str(selected.get("id")) if len(selected_ids) == 1 else "selected",
    )
    found = False
    for candidate in candidates:
        key = str(candidate["key"])
        if selected and key == selected_key:
            yield key, {**candidate, **selected}, "已选"
            found = True
        else:
            yield key, candidate, "候选" if stage == 3 else "未采用"
    if selected and not found:
        yield selected_key, selected, "已选"


def matching_sentence(body, term, fallback):
    sentences = re.split(r"(?<=[。！？!?])|(?<=\.)\s+", body)
    return next((part for part in sentences if term in part.casefold()), fallback)


def library_rows(run, kind, compact, query):
    data = json.loads(run["payload"])
    materials = {row["id"]: row for row in data["materials"]}
    cleaned = {row["id"]: row for row in data["cleaned"]} if run["stage"] >= 2 else {}
    default_source = data["config"].get("filename") or run["name"]
    origins = {}
    for ident, material in materials.items():
        provenance = material.get("provenance")
        source = provenance.get("source") if isinstance(provenance, dict) else None
        origins[ident] = (
            source if isinstance(source, str) and source else default_source
        )

    def entry(key, title, names, status, body, **fields):
        return dict(
            run_id=run["id"],
            batch_name=run["name"],
            key=str(key),
            title=title,
            source="、".join(names),
            status=status,
            excerpt=body[:160],
            **fields,
        )

    def matches(*parts):
        return not query or any(query in part.casefold() for part in parts)

    if kind == "materials":
        for ident, row in materials.items():
            result = cleaned.get(ident, {})
            body = result.get("body", "")
            names = [origins[ident]]
            item = entry(
                ident,
                row["title"],
                names,
                result.get("reason", "待清洗"),
                body or row["body"],
                material_id=ident,
                created=run["created"],
            )
            if not compact:
                item.update(original=row["body"], cleaned=body)
            yield item, names, matches(row["title"], names[0], row["body"], body)
        return

    insights = {row["id"]: row["quote"] for row in data.get("insights", [])}
    drafts = data.get("drafts") or []
    if kind == "drafts":
        if not drafts:
            return
        version = len(drafts)
        status = next(
            (
                review["decision"]
                for review in reversed(data.get("reviews", []))
                if review.get("version") == version
                and review.get("decision") in ("通过", "退回")
            ),
            "待审核",
        )
        topics = [("draft", data.get("topic", {}), status)]
    else:
        topics = topic_rows(data, materials, insights, run["stage"])

    for key, topic, status in topics:
        ids = list(
            dict.fromkeys(
                ident
                for ident in topic.get("ids", [topic.get("id")])
                if ident in materials
            )
        )
        names = list(dict.fromkeys(origins[ident] for ident in ids)) or [default_source]
        reason = topic.get("reason", "")
        body = drafts[-1] if kind == "drafts" else reason
        item = entry(key, topic.get("title", run["name"]), names, status, body)
        if kind == "drafts":
            item.update(version=version, updated=data.get("draft_updated"))
        else:
            item.update(source_count=len(names), reason=reason)
        if not compact:
            evidence = []
            for ident in ids:
                row = materials[ident]
                quote = insights.get(ident, "")
                if topic.get("term"):
                    body_text = cleaned.get(ident, {}).get("body", row["body"])
                    quote = matching_sentence(
                        body_text, topic["term"].casefold(), quote
                    )
                provenance = row.get("provenance") or {}
                evidence.append(
                    {
                        "id": ident,
                        "title": row["title"],
                        "quote": quote,
                        "original": row["body"],
                        "source": origins[ident],
                        "run_id": provenance.get("run_id", run["id"]),
                        "material_id": provenance.get("material_id", ident),
                    }
                )
            item["evidence"] = evidence
        matched = matches(item["title"], item["source"], body) or any(
            matches(materials[ident]["title"], materials[ident]["body"])
            for ident in ids
        )
        yield item, names, matched


@app.get("/api/projects/{project_id}/library/{kind}")
def project_library(
    project_id: Annotated[int, Field(ge=1, le=2**63 - 1)],
    kind: Literal["materials", "topics", "drafts"],
    q: str = "",
    status: str = "",
    source: str = "",
    compact: bool = False,
    preview: bool = False,
    since: str | None = Query(None, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"),
    until: str | None = Query(None, pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"),
    run_id: int | None = Query(None, ge=1, le=2**63 - 1),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    items, sources, statuses = [], set(), set()
    total = 0
    query = q.strip().casefold()
    if since or until:
        if kind != "materials":
            raise HTTPException(422, "日期筛选仅适用于素材库")
        try:
            for bound in (since, until):
                if bound:
                    date.fromisoformat(bound)
        except ValueError as exc:
            raise HTTPException(422, "请输入有效日期") from exc
        if since and until and since > until:
            raise HTTPException(422, "开始日期不能晚于结束日期")
    with database() as conn:
        conn.execute("BEGIN")
        project_settings(conn, project_id)
        sql = "SELECT id,name,stage,created,payload FROM runs WHERE project_id=?"
        params = [project_id]
        if kind == "materials" and preview:
            sql += f" AND {INGESTED_RUN}"
        if since:
            sql += " AND date(created)>=?"
            params.append(since)
        if until:
            sql += " AND date(created)<=?"
            params.append(until)
        if kind == "topics":
            sql += " AND stage=3" if preview and status == "候选" else " AND stage>=3"
        if run_id is not None:
            sql += " AND id=?"
            params.append(run_id)
        if kind == "drafts":
            sql += f" AND stage=5 ORDER BY {DRAFT_ORDER} DESC, id DESC"
        else:
            sql += " ORDER BY id DESC"
        for run in conn.execute(sql, params):
            for item, names, matched in library_rows(run, kind, compact, query):
                if not preview:
                    sources.update(names)
                    statuses.add(item["status"])
                if (
                    not matched
                    or (source and source not in names)
                    or (status and item["status"] != status)
                ):
                    continue
                if offset <= total and len(items) < limit:
                    items.append(item)
                    if preview and len(items) == limit:
                        return {
                            "items": items,
                            "total": None,
                            "sources": [],
                            "statuses": [],
                        }
                total += 1
    return {"items": items, "total": None if preview else total, "sources": sorted(sources), "statuses": sorted(statuses)}


def evidence_text(data, topic):
    ids = set(topic.get("ids", [topic["id"]]))
    term = topic.get("term", "").casefold()
    quotes = {
        item["id"]: item["quote"] for item in data["insights"] if item["id"] in ids
    }
    if term:
        for item in data["cleaned"]:
            if item["id"] not in ids:
                continue
            body = item["body"]
            quotes[item["id"]] = matching_sentence(body, term, quotes[item["id"]])
    return "\n\n".join(
        f"素材 {item_id}：{quote[:1500]}" for item_id, quote in quotes.items()
    )[:12000]


@app.post("/api/runs/{run_id}/advance")
def advance(run_id: int, action: Action):
    generated = None
    owner = None
    if action.stage == 4 and action.engine == "model":
        owner = uuid.uuid4().hex
        with database() as conn:
            conn.execute("BEGIN IMMEDIATE")
            snapshot = get_run(conn, run_id)
            if snapshot["stage"] != 4:
                raise HTTPException(409, "当前阶段无法生成")
            conn.execute(
                "DELETE FROM generating WHERE run_id=? AND expires<=?",
                (run_id, time.time()),
            )
            if not conn.execute(
                "INSERT OR IGNORE INTO generating VALUES(?,?,?)",
                (run_id, owner, time.time() + 300),
            ).rowcount:
                raise HTTPException(409, "该稿件正在生成")
        data = snapshot["payload"]
        topic = data["topic"]
        quote = evidence_text(data, topic)
        try:
            generated = connections.generate(
                topic["title"], quote, action.audience, action.style
            )
        except Exception:
            with database() as conn:
                conn.execute(
                    "DELETE FROM generating WHERE run_id=? AND owner=?", (run_id, owner)
                )
            raise
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if action.stage == 4:
            lock = conn.execute(
                "SELECT owner FROM generating WHERE run_id=? AND expires>?",
                (run_id, time.time()),
            ).fetchone()
            if lock and lock["owner"] != owner:
                raise HTTPException(409, "该稿件正在生成")
            conn.execute("DELETE FROM generating WHERE run_id=?", (run_id,))
        run = get_run(conn, run_id)
        if action.stage != run["stage"]:
            raise HTTPException(409, "进度已更新，请重新打开这次运行")
        data = run["payload"]
        if action.stage == 1:
            data["cleaned"] = clean(data["materials"], data["config"]["min_length"])
        elif action.stage == 2:
            insights = []
            focus = data["config"]["focus"].strip().casefold()
            for item in data["cleaned"]:
                if item["reason"] != "保留":
                    continue
                passages = re.split(r"(?<=[。！？!?])|(?<=\.)\s+", item["body"])
                matched = next(
                    (part for part in passages if focus and focus in part.casefold()),
                    None,
                )
                insights.append(
                    {
                        "id": item["id"],
                        "quote": (matched or passages[0]).strip(),
                        "matched": matched is not None,
                    }
                )
            data["insights"] = sorted(insights, key=lambda item: not item["matched"])
            dimensions = dict.fromkeys(
                term.strip()
                for term in re.split("[,，]", data["config"].get("topics", ""))
                if term.strip()
            )
            kept = [item for item in data["cleaned"] if item["reason"] == "保留"]
            data["themes"] = [
                {
                    "topic": term,
                    "sources": [
                        item["id"]
                        for item in kept
                        if term.casefold() in item["body"].casefold()
                    ],
                }
                for term in dimensions
            ]
            data["themes"].sort(key=lambda theme: len(theme["sources"]), reverse=True)
            data["recommendations"] = recommendations(
                data["materials"], data["insights"], data["themes"]
            )
        elif action.stage == 3:
            candidates = recommendations(
                data["materials"], data["insights"], data.get("themes", [])
            )
            key = action.candidate_key or str(action.topic_id)
            candidate = next((row for row in candidates if row["key"] == key), None)
            if not candidate:
                raise HTTPException(422, "请选择有原文依据的候选素材")
            data["topic"] = {
                "key": candidate["key"],
                "id": candidate["ids"][0],
                "ids": candidate["ids"],
                "title": action.title.strip() or candidate["title"],
                "reason": candidate["reason"],
            }
            if candidate.get("term"):
                data["topic"]["term"] = candidate["term"]
        else:
            topic = data["topic"]
            quote = evidence_text(data, topic)
            sources = [
                data["materials"][item_id - 1]
                for item_id in topic.get("ids", [topic["id"]])
            ]
            content = generated or f"# {topic['title']}\n\n{quote}"
            data["generation"] = action.model_dump(
                include={"style", "audience", "engine"}
            )
            references = "\n".join(
                f"- {source['title']}（素材 {source['id']}）" for source in sources
            )
            data["drafts"] = [content + "\n\n来源：\n" + references + "\n"]
            data["draft_updated"] = datetime.now(UTC).isoformat()
        run["stage"] += 1
        return persist(conn, run)


@app.put("/api/runs/{run_id}/draft")
def edit(run_id: int, update: Edit):
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        run = get_run(conn, run_id)
        drafts = run["payload"].get("drafts", [])
        if not drafts or len(drafts) != update.version:
            raise HTTPException(409, "版本已变化，请重新打开核对后再保存")
        if update.content != drafts[-1]:
            drafts.append(update.content)
            run["payload"]["draft_updated"] = datetime.now(UTC).isoformat()
        return persist(conn, run)


@app.get("/api/runs/{run_id}/export")
def export(run_id: int, version: int | None = Query(None, ge=1)):
    drafts = detail(run_id)["payload"].get("drafts")
    if not drafts:
        raise HTTPException(409, "请先生成草稿")
    version = len(drafts) if version is None else version
    if version > len(drafts):
        raise HTTPException(404, "稿件版本不存在")
    return PlainTextResponse(
        drafts[version - 1],
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="draft-{run_id}-v{version}.md"'},
    )


class Review(BaseModel):
    version: int = Field(ge=1)
    decision: Literal["通过", "退回"]
    note: str = Field(default="", max_length=1000)


@app.post("/api/runs/{run_id}/review")
def review_draft(run_id: int, form: Review):
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        run = get_run(conn, run_id)
        drafts = run["payload"].get("drafts", [])
        if not drafts or len(drafts) != form.version:
            raise HTTPException(409, "稿件版本已更新，请审阅最新版本")
        stamp = conn.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
        run["payload"].setdefault("reviews", []).append(
            {**form.model_dump(), "at": stamp}
        )
        return persist(conn, run)


class Source(BaseModel):
    platform: str = Field(default="", max_length=80)
    project_id: int = Field(default=1, ge=1)
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["rss", "notion", "web", "api"]
    address: str = Field(min_length=1, max_length=2000)
    title_field: str = Field(default="Name", max_length=100)
    body_field: str = Field(default="Body", max_length=100)
    minutes: int = Field(default=0, ge=0, le=43200)
    records_path: str = Field(default="", max_length=200)
    id_field: str = Field(default="", max_length=100)
    next_path: str = Field(default="", max_length=200)
    token_alias: str = Field(
        default="", max_length=100, pattern=r"^$|^DM_API_TOKEN_[A-Z0-9_]+$"
    )


def read_source(source):
    if source["kind"] == "rss":
        return connections.rss(source["address"])
    if source["kind"] == "web":
        return connections.webpage(source["address"])
    if source["kind"] == "api":
        return connections.json_api(
            source["address"],
            source["title_field"],
            source["body_field"],
            source["records_path"],
            source["id_field"],
            source["next_path"],
            source["token_alias"],
        )
    return connections.notion_rows(
        source["address"], source["title_field"], source["body_field"]
    )


@app.post("/api/sources/preview")
def preview_source(form: Source):
    rows = read_source(form.model_dump())
    return {"count": len(rows), "sample": rows[:3]}


@app.get("/api/sources")
def sources(project_id: int = 1):
    with database() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT id,name,kind,address,minutes,status,platform FROM sources WHERE project_id=? ORDER BY id",
                (project_id,),
            )
        ]


@app.post("/api/sources")
def add_source(form: Source):
    with database() as conn:
        project_settings(conn, form.project_id)
        cursor = conn.execute(
            "INSERT INTO sources(project_id,name,kind,address,title_field,body_field,minutes,records_path,id_field,next_path,token_alias,platform) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                form.project_id,
                form.name,
                form.kind,
                form.address,
                form.title_field,
                form.body_field,
                form.minutes,
                form.records_path,
                form.id_field,
                form.next_path,
                form.token_alias,
                form.platform.strip(),
            ),
        )
        return {"id": cursor.lastrowid}


class Interval(BaseModel):
    platform: str | None = Field(default=None,max_length=80)
    minutes: int = Field(ge=0, le=43200)


@app.put("/api/sources/{source_id}/interval")
def interval(source_id: int, form: Interval):
    with database() as conn:
        if not conn.execute(
            "UPDATE sources SET minutes=?,due=?,platform=coalesce(?,platform) WHERE id=?",
            (form.minutes, time.time() + 60 * form.minutes, form.platform, source_id),
        ).rowcount:
            raise HTTPException(404, "来源不存在")
    return form


@app.post("/api/sources/{source_id}/refresh")
def refresh(source_id: int):
    lease = uuid.uuid4().hex
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if not row:
            raise HTTPException(404, "来源不存在")
        source = dict(row)
        if source["lease_until"] > time.time():
            raise HTTPException(409, "该来源正在采集")
        conn.execute(
            "UPDATE sources SET lease=?,lease_until=?,status=? WHERE id=?",
            (lease, time.time() + 600, "正在采集", source_id),
        )
    try:
        rows = read_source(source)
        with database() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute(
                "SELECT id FROM sources WHERE id=? AND lease=?", (source_id, lease)
            ).fetchone():
                raise HTTPException(409, "本次采集已由新任务接管")
            materials = []
            updated = 0
            for record in rows:
                fingerprint = connections.digest(
                    json.dumps(record, ensure_ascii=False, sort_keys=True)
                )
                old = conn.execute(
                    "SELECT hash FROM seen WHERE source=? AND key=?",
                    (source_id, record["key"]),
                ).fetchone()
                if old and old["hash"] == fingerprint:
                    continue
                updated += int(old is not None)
                conn.execute(
                    "INSERT OR REPLACE INTO seen VALUES(?,?,?)",
                    (source_id, record["key"], fingerprint),
                )
                materials.append(
                    {
                        "id": len(materials) + 1,
                        "title": record["title"],
                        "body": record["body"],
                        "raw": record,
                    }
                )
            run = None
            if materials:
                config = {
                    "filename": source["name"],
                    "project_id": source["project_id"],
                    "source_id": source_id,
                    "format": source["kind"],
                }
                run = insert_run(conn, source["name"], config, materials)
            status = f"读取 {len(rows)} · 新增 {len(materials) - updated} · 更新 {updated} · 重复 {len(rows) - len(materials)}"
            conn.execute("""INSERT INTO batches(project_id,source_id,run_id,name,received,added,updated,duplicates)
                VALUES(?,?,?,?,?,?,?,?)""", (source['project_id'],source_id,run['id'] if run else None,
                source['name'],len(rows),len(materials)-updated,updated,len(rows)-len(materials)))
            conn.execute(
                "UPDATE sources SET status=?,lease=NULL,lease_until=0,due=?+60*minutes WHERE id=?",
                (status, time.time(), source_id),
            )
            return {"run": run, "status": status}
    except Exception as exc:
        message = exc.detail if isinstance(exc, HTTPException) else "来源处理失败"
        with database() as conn:
            released = conn.execute(
                "UPDATE sources SET status=?,lease=NULL,lease_until=0,due=?+60*minutes WHERE id=? AND lease=?",
                (message, time.time(), source_id, lease),
            )
            if released.rowcount:
                conn.execute("INSERT INTO batches(project_id,source_id,name,error) VALUES(?,?,?,?)",
                             (source['project_id'],source_id,source['name'],str(message)))
        raise


def schedule(stop):
    while not stop.wait(5):
        process_outbox()
        with database() as conn:
            ids = conn.execute(
                "SELECT id FROM sources WHERE minutes>0 AND due<=? AND lease_until<=?",
                (time.time(), time.time()),
            ).fetchall()
        for row in ids:
            if stop.is_set():
                return
            try:
                refresh(row["id"])
            except (HTTPException, ValueError, KeyError, sqlite3.Error) as exc:
                logging.getLogger(__name__).warning(
                    "来源 %s 刷新失败 (%s)", row["id"], type(exc).__name__
                )


class Destination(BaseModel):
    database: str = Field(min_length=1, max_length=100)


@app.get("/api/connections")
def connection_status():
    return {
        "model": os.getenv("DM_MODEL_NAME") if os.getenv("DM_MODEL_BASE_URL") else None,
        "notion": bool(os.getenv("DM_NOTION_TOKEN")),
        "transcription":bool(os.getenv('DM_ASR_BASE_URL') and os.getenv('DM_ASR_MODEL')),
        "ffmpeg":bool(shutil.which('ffmpeg')),
        "ocr_enabled":os.getenv('DM_OCR_ENABLED')=='1',
        "ocr_tools":bool(shutil.which('pdftoppm') and shutil.which('tesseract')),
        "data_dir":str(data_directory()),
    }


class ConnectionCheck(BaseModel):
    target: Literal['model','notion','transcription','ocr']
    database: str = Field(default='',max_length=100)


@app.post('/api/connections/check')
def check_connection(form:ConnectionCheck):
    if form.target=='model':
        connections.check_model()
        return {'message':'模型生成测试通过（使用合成内容，可能产生少量用量）'}
    if form.target=='notion':
        connection=connections.notion('GET','databases/'+connections.database_id(form.database))
        if not any(field.get('type')=='title' for field in connection.get('properties',{}).values()):
            raise HTTPException(422,'库可读取，但缺少可用标题属性')
        return {'message':'已验证数据库读取权限。写入权限以首次快照回读为准。'}
    status=connection_status()
    if form.target=='transcription':
        if not status['transcription'] or not status['ffmpeg']:
            raise HTTPException(422,'请配置转写服务并安装 FFmpeg')
        return {'message':'转写服务参数与 FFmpeg 已就绪。可上传短音频验证转写。'}
    if not status['ocr_enabled'] or not status['ocr_tools']:
        raise HTTPException(422,'请启用 OCR 并安装 Poppler 与 Tesseract')
    return {'message':'已找到 OCR 工具。可导入扫描 PDF 验证语言与识别结果。'}


def notion_snapshot(run):
    return json.dumps(
        {
            "schema": 1,
            "name": run["name"],
            "stage": run["stage"],
            "payload": run["payload"],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def read_snapshot(page):
    result = connections.notion("GET", f"blocks/{page}/children?page_size=100")
    chunks = []
    for block in result["results"]:
        if block["type"] != "code":
            raise HTTPException(409, "远端内容已变更，请人工核对")
        chunks.extend(span["plain_text"] for span in block["code"]["rich_text"])
    if result.get("has_more"):
        raise HTTPException(409, "远端内容长度不符")
    return "".join(chunks)


class RestorePage(BaseModel):
    page_id: str
    project_id: int = Field(ge=1)


def validate_library_snapshot(data, material_count):
    candidates = data.get("recommendations", [])
    if (
        not isinstance(data["config"], dict)
        or not isinstance(candidates, list)
        or not isinstance(data["config"].get("filename", ""), str)
    ):
        raise TypeError()
    keys = set()
    for candidate in candidates:
        if not isinstance(candidate, dict) or not isinstance(
            candidate.get("key"), (str, int)
        ):
            raise TypeError()
        key = str(candidate["key"])
        if key in keys or not key or not candidate.get("ids"):
            raise ValueError()
        keys.add(key)
    for topic in [*candidates, *([data["topic"]] if "topic" in data else [])]:
        if not isinstance(topic, dict) or not isinstance(topic.get("title"), str):
            raise TypeError()
        ids = topic.get("ids", [topic.get("id")])
        if (
            not isinstance(ids, list)
            or not ids
            or not all(
                type(ident) is int and 1 <= ident <= material_count for ident in ids
            )
            or not all(
                isinstance(topic.get(field, ""), str) for field in ("term", "reason")
            )
        ):
            raise ValueError()
    reviews = data.get("reviews", [])
    if not isinstance(reviews, list) or not all(
        isinstance(row, dict) for row in reviews
    ):
        raise ValueError()
    stamp = data.get("draft_updated")
    if stamp is not None:
        stamp = datetime.fromisoformat(stamp)
        data["draft_updated"] = stamp.replace(tzinfo=stamp.tzinfo or UTC).astimezone(UTC).isoformat()


@app.post("/api/notion/restore")
def restore_page(form: RestorePage):
    page = connections.database_id(form.page_id)
    try:
        saved = json.loads(read_snapshot(page))
        data = saved["payload"]
        stage = saved["stage"]
        if saved["schema"] != 1 or type(stage) is not int or stage not in range(1, 6):
            raise ValueError()
        materials = data["materials"]
        if not isinstance(materials, list) or not 1 <= len(materials) <= 2000:
            raise ValueError()
        if not all(
            item["id"] == index
            and isinstance(item["body"], str)
            and isinstance(item["title"], str)
            for index, item in enumerate(materials, 1)
        ):
            raise ValueError()
        config = data["config"]
        validate_library_snapshot(data, len(materials))
        rules = Rules(**config).model_dump()
        if stage >= 2 and (
            len(data["cleaned"]) != len(materials)
            or not all(
                isinstance(row["reason"], str)
                and isinstance(row["body"], str)
                and row["id"] == index
                for index, row in enumerate(data["cleaned"], 1)
            )
        ):
            raise ValueError()
        if stage >= 3 and not all(
            isinstance(row["quote"], str) and 1 <= row["id"] <= len(materials)
            for row in data["insights"]
        ):
            raise ValueError()
        if stage >= 4 and (
            not isinstance(data["topic"]["title"], str)
            or not any(row["id"] == data["topic"]["id"] for row in data["insights"])
        ):
            raise ValueError()
        if stage == 5 and (
            not data["drafts"]
            or not isinstance(data["drafts"], list)
            or not all(isinstance(draft, str) for draft in data["drafts"])
        ):
            raise ValueError()
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(422, "页面缺少有效的 DM 快照或版本不兼容") from exc
    with database() as conn:
        project_settings(conn, form.project_id)
        data["config"] = {
            **config,
            **rules,
            "project_id": form.project_id,
            "notion_database": "",
        }
        data["restored_from"] = page
        data.pop('parent', None)
        cursor = conn.execute(
            "INSERT INTO runs(name,stage,payload,project_id) VALUES(?,?,?,?)",
            (
                str(saved.get("name", "恢复运行")),
                stage,
                json.dumps(data, ensure_ascii=False),
                form.project_id,
            ),
        )
        restored_id = cursor.lastrowid
        for item in materials:
            old = item.get('provenance')
            if old:
                source = old.get('source') if isinstance(old, dict) else None
                item['provenance'] = {'run_id': restored_id, 'material_id': item['id'],
                                      'source': source or config.get('filename'), 'title': item['title']}
        conn.execute('UPDATE runs SET payload=? WHERE id=?', (json.dumps(data, ensure_ascii=False), restored_id))
        return get_run(conn, restored_id)


@app.post("/api/runs/{run_id}/notion")
def deliver(run_id: int, dest: Destination):
    run = detail(run_id)
    return write_snapshot(run_id, run["name"], notion_snapshot(run), dest.database)


def write_snapshot(run_id, name, snapshot, database_name):
    dbid = connections.database_id(database_name)
    chunks = [
        snapshot[offset : offset + 950] for offset in range(0, len(snapshot), 950)
    ]
    if len(chunks) > 100 or len(snapshot.encode()) > 350000:
        raise HTTPException(422, "快照超过 Notion 单页容量，请使用本地备份")
    props = connections.notion("GET", "databases/" + dbid)["properties"]
    title_field = next(
        (name for name, prop in props.items() if prop["type"] == "title"), None
    )
    if not title_field:
        raise HTTPException(422, "目标库缺少标题属性")
    fingerprint = connections.digest(snapshot)
    with database() as conn:
        workspace = conn.execute("SELECT id FROM workspace").fetchone()["id"]
        key = f"{dbid}/{workspace}/{run_id}/{fingerprint}"
        name = f"{name[:80]} · DM {workspace[:10]}-{run_id}-{fingerprint[:20]}"
        claimed = conn.execute(
            "INSERT OR IGNORE INTO deliveries VALUES(?,NULL,'pending')", (key,)
        ).rowcount
        receipt = conn.execute(
            "SELECT page FROM deliveries WHERE key=?", (key,)
        ).fetchone()
    page = receipt["page"]
    if not page:
        query = {"filter": {"property": title_field, "title": {"equals": name}}}
        try:
            matches = connections.notion("POST", f"databases/{dbid}/query", query)[
                "results"
            ]
        except HTTPException:
            if claimed:
                with database() as conn:
                    conn.execute(
                        "DELETE FROM deliveries WHERE key=? AND page IS NULL", (key,)
                    )
            raise
        if len(matches) > 1:
            raise HTTPException(409, "找到多个同名快照，请人工对账")
        if matches:
            page = matches[0]["id"]
        elif not claimed:
            raise HTTPException(409, "先前写入结果待确认；尚未查到快照，请稍后再对账")
        else:
            rich = lambda text: [{"type": "text", "text": {"content": text}}]
            blocks = [
                {
                    "object": "block",
                    "type": "code",
                    "code": {"language": "json", "rich_text": rich(chunk)},
                }
                for chunk in chunks
            ]
            page = connections.notion(
                "POST",
                "pages",
                {
                    "parent": {"database_id": dbid},
                    "properties": {title_field: {"title": rich(name)}},
                    "children": blocks,
                },
            )["id"]
        with database() as conn:
            conn.execute("UPDATE deliveries SET page=? WHERE key=?", (page, key))
    if read_snapshot(page) != snapshot:
        raise HTTPException(409, "远端快照与当前版本不同，已保留远端内容")
    with database() as conn:
        conn.execute("UPDATE deliveries SET status='verified' WHERE key=?", (key,))
    return {"page": page, "status": "已保存并回读核实", "hash": fingerprint}


def process_outbox():
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        job = conn.execute(
            "SELECT * FROM outbox WHERE state IN ('待同步','同步中') AND lease_until<? ORDER BY id LIMIT 1",
            (time.time(),),
        ).fetchone()
        if not job:
            return
        conn.execute(
            "UPDATE outbox SET state='同步中',lease_until=? WHERE id=?",
            (time.time() + 300, job["id"]),
        )
    try:
        write_snapshot(
            job["run_id"],
            json.loads(job["snapshot"])["name"],
            job["snapshot"],
            job["target"],
        )
        state, error = "已核实", ""
    except (HTTPException, KeyError, ValueError, sqlite3.Error) as exc:
        state, error = (
            "待处理",
            str(exc.detail) if isinstance(exc, HTTPException) else "连接器未完成写入",
        )
    with database() as conn:
        conn.execute(
            "UPDATE outbox SET state=?,error=?,lease_until=0 WHERE id=?",
            (state, error, job["id"]),
        )


@app.get("/api/runs/{run_id}/sync")
def sync_state(run_id: int):
    with database() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT id,state,error FROM outbox WHERE run_id=? ORDER BY id DESC",
                (run_id,),
            )
        ]


@app.post("/api/runs/{run_id}/sync")
def retry_sync(run_id: int):
    with database() as conn:
        conn.execute(
            "UPDATE outbox SET state='待同步',error='' WHERE run_id=? AND state='待处理'",
            (run_id,),
        )
    return {"status": "已重新排队"}


@app.get("/api/runs/{run_id}/receipts")
def receipts(run_id: int):
    with database() as conn:
        workspace = conn.execute("SELECT id FROM workspace").fetchone()["id"]
        return [
            dict(row)
            for row in conn.execute(
                "SELECT page,status FROM deliveries WHERE key LIKE ?",
                (f"%/{workspace}/{run_id}/%",),
            )
        ]


integrations.mount(app, database, project_settings, AccountDay, write_account_day, get_run)
mount_collection(app, database, insert_run, project_settings)
mount_references(app, database, project_settings, topic_rows)
app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="dashboard")
