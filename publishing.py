"""Project publishing records and manual publisher portal handoffs."""

import hashlib
import json
import re
from datetime import datetime
from pathlib import PureWindowsPath
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import File, Form, HTTPException, Path, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, HttpUrl, ValidationError

MAX_BYTES = 64 * 1024 * 1024
PLATFORMS = [
    {"id": "xiaohongshu", "name": "小红书", "url": "https://creator.xiaohongshu.com/publish/publish"},
    {"id": "bilibili", "name": "B站", "url": "https://member.bilibili.com/platform/upload/video/frame"},
    {"id": "douyin", "name": "抖音", "url": "https://creator.douyin.com/creator-micro/content/upload"},
    {"id": "instagram", "name": "Instagram", "url": "https://www.instagram.com/"},
    {"id": "tiktok", "name": "TikTok", "url": "https://www.tiktok.com/tiktokstudio/upload"},
    {"id": "x", "name": "X", "url": "https://x.com/compose/post"},
]
PLATFORM_IDS = {platform['id'] for platform in PLATFORMS}
Identifier = Annotated[int, Path(ge=1, le=2**63 - 1)]
OptionalSource = Annotated[int | None, Form(ge=1, le=2**63 - 1)]


def initialize(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS publishing_posts (
        id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, request_id TEXT NOT NULL,
        fingerprint TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
        run_id INTEGER, version INTEGER, UNIQUE(project_id, request_id))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS publishing_assets (
        id INTEGER PRIMARY KEY, post_id INTEGER NOT NULL, name TEXT NOT NULL, data BLOB NOT NULL)''')
    conn.execute('CREATE INDEX IF NOT EXISTS publishing_assets_post ON publishing_assets(post_id)')
    conn.execute('''CREATE TABLE IF NOT EXISTS publishing_targets (
        id INTEGER PRIMARY KEY, post_id INTEGER NOT NULL, platform TEXT NOT NULL, account TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','failed','published')),
        url TEXT NOT NULL DEFAULT '', UNIQUE(post_id, platform, account))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS publication_metrics (
        target_id INTEGER NOT NULL, sampled TEXT NOT NULL, views INTEGER NOT NULL,
        engagements INTEGER NOT NULL, source TEXT NOT NULL, PRIMARY KEY(target_id,sampled))''')


def load_posts(conn, project, post_id=None, *, ids=None):
    condition = 'p.project_id=?'
    params = (project,)
    if ids is not None:
        if not ids:
            return []
        condition += ' AND p.id IN (' + ','.join('?' for _ in ids) + ')'
        params += tuple(ids)
    if post_id is not None:
        condition += ' AND p.id=?'
        params += (post_id,)
    posts = {row['id']: dict(row, assets=[], targets=[]) for row in conn.execute(
        f'SELECT id,title,body,run_id,version FROM publishing_posts p WHERE {condition} ORDER BY id DESC', params)}
    for key, columns in [('assets', 'c.id,c.name'), ('targets', 'c.id,c.platform,c.account,c.status,c.url')]:
        for row in conn.execute(f'''SELECT c.post_id,{columns} FROM publishing_{key} c
                JOIN publishing_posts p ON p.id=c.post_id WHERE {condition} ORDER BY c.id''', params):
            child = dict(row)
            posts[child.pop('post_id')][key].append(child)
    measurements = {}
    for row in conn.execute(f'''SELECT m.* FROM publication_metrics m JOIN publishing_targets t ON t.id=m.target_id
            JOIN publishing_posts p ON p.id=t.post_id WHERE {condition} ORDER BY m.sampled''', params):
        measurements.setdefault(row['target_id'], []).append(dict(row))
    for post in posts.values():
        for target in post['targets']:
            target['metrics'] = measurements.get(target['id'], [])
    return list(posts.values())


def parse_targets(raw):
    try:
        values = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise HTTPException(422, '无法解析发布目标') from exc
    if not isinstance(values, list) or not values:
        raise HTTPException(422, '请选择发布平台和账号')
    targets = []
    for item in values:
        if not isinstance(item, dict):
            raise HTTPException(422, '发布目标格式错误')
        platform, account = item.get('platform'), item.get('account')
        if not isinstance(platform, str) or platform not in PLATFORM_IDS:
            raise HTTPException(422, '不支持的发布平台')
        if not isinstance(account, str) or not 1 <= len(account.strip()) <= 100:
            raise HTTPException(422, '请输入有效的账号名称')
        targets.append((platform, account.strip()))
    if len(set(targets)) != len(targets):
        raise HTTPException(422, '平台账号重复')
    return targets


def check_source(conn, project, run_id, version, title, body):
    if run_id is None and version is None:
        return
    if run_id is None or version is None:
        raise HTTPException(422, '请选择稿件及版本')
    row = conn.execute('SELECT payload FROM runs WHERE project_id=? AND id=? AND stage=5',
                       (project, run_id)).fetchone()
    if not row:
        raise HTTPException(404, '项目内找不到该稿件')
    data = json.loads(row['payload'])
    decision = next((r['decision'] for r in reversed(data.get('reviews', [])) if r['version'] == version), None)
    drafts = data.get('drafts', [])
    if not drafts or version != len(drafts) or decision != '通过':
        raise HTTPException(409, '请选择已审核通过的最新稿件版本')
    source = drafts[version - 1]
    source_title = source.strip().split('\n', 1)[0].lstrip('#').strip()
    if body != source or title != source_title:
        raise HTTPException(409, '关联稿件内容已改动，请返回成稿库保存新版本并重新审核，或取消关联按独立内容准备发布')


class Confirmation(BaseModel):
    status: Literal['published', 'failed', 'pending']
    url: str = Field(default='', max_length=2083)


class Measurement(BaseModel):
    sampled: datetime
    views: int = Field(ge=0, le=10**12)
    engagements: int = Field(ge=0, le=10**12)
    source: str = Field(min_length=1, max_length=200)


def work_url(value):
    try:
        parsed = HttpUrl(value)
        if (value.lower().startswith('https://') and parsed.scheme == 'https' and parsed.host
                and not any(c.isspace() or c == '\\' or ord(c) < 32 for c in value)
                and not parsed.username and not parsed.password):
            return str(parsed)
    except ValidationError:
        pass
    raise HTTPException(422, '请填写有效的 HTTPS 作品链接')


def mount_publishing(app, database, project_settings):
    base = '/api/projects/{project}/publishing'

    @app.middleware('http')
    async def publishing_origin(request: Request, call_next):
        if request.method == 'POST' and re.fullmatch(r'/api/projects/[^/]+/publishing/?', request.url.path):
            origins = request.headers.getlist('origin')
            if origins and origins != [f'{request.url.scheme}://{request.url.netloc}']:
                return JSONResponse({'detail': '请在当前工作台页面提交发布内容'}, status_code=403)
        return await call_next(request)

    @app.get(base)
    def listing(project: Identifier, offset: int = Query(0, ge=0), limit: int | None = Query(None, ge=1, le=100),
                target_id: int | None = Query(None, ge=1, le=2**63-1)):
        with database() as conn:
            conn.execute('BEGIN')
            project_settings(conn, project)
            if limit is not None:
                ordered = '''SELECT p.id FROM publishing_posts p
                    LEFT JOIN publishing_targets t ON t.post_id=p.id WHERE p.project_id=? GROUP BY p.id
                    ORDER BY MAX(t.status!='published') DESC, p.id DESC'''
                total = conn.execute('SELECT count(*) FROM publishing_posts WHERE project_id=?', (project,)).fetchone()[0]
                if target_id:
                    target = conn.execute(f'''SELECT position FROM (SELECT id, row_number() OVER () - 1 AS position
                        FROM ({ordered})) WHERE id=(SELECT post_id FROM publishing_targets WHERE id=?)''',
                        (project, target_id)).fetchone()
                    if target:
                        offset = target[0] // limit * limit
                offset = min(offset, max(0, (total-1)//limit*limit))
                ids = [row[0] for row in conn.execute(ordered + ' LIMIT ? OFFSET ?', (project,limit,offset))]
                posts = {post['id']:post for post in load_posts(conn, project, ids=ids)}
                accounts = conn.execute('''SELECT DISTINCT platform,account FROM publishing_targets t
                    JOIN publishing_posts p ON p.id=t.post_id WHERE p.project_id=?''', (project,))
                return {'platforms':PLATFORMS, 'posts':[posts[key] for key in ids], 'offset':offset,
                        'total':total, 'accounts':[dict(row) for row in accounts]}
            return {'platforms': PLATFORMS, 'posts': load_posts(conn, project)}

    @app.post(base)
    def create(project: Identifier, title: Annotated[str, Form(max_length=200)],
               body: Annotated[str, Form(max_length=1000000)], targets: Annotated[str, Form()],
               request_id: Annotated[UUID, Form()], run_id: OptionalSource = None,
               version: OptionalSource = None, files: Annotated[list[UploadFile] | None, File()] = None):
        if not title.strip() or not body.strip():
            raise HTTPException(422, '请填写标题和正文')
        destinations = parse_targets(targets)
        assets = []
        total = 0
        for file in files or []:
            name = PureWindowsPath(file.filename or '').name
            if not name or any(ord(c) < 32 for c in name):
                raise HTTPException(422, '附件文件名无效')
            data = file.file.read(MAX_BYTES - total + 1)
            total += len(data)
            if total > MAX_BYTES:
                raise HTTPException(413, '附件总大小超过 64 MiB')
            assets.append((name, data))
        manifest = [title, body, sorted(destinations), run_id, version,
                    [(name, hashlib.sha256(data).hexdigest()) for name, data in assets]]
        fingerprint = hashlib.sha256(json.dumps(manifest).encode()).hexdigest()
        with database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            project_settings(conn, project)
            existing = conn.execute('SELECT id,fingerprint FROM publishing_posts WHERE project_id=? AND request_id=?',
                                    (project, str(request_id))).fetchone()
            if existing:
                if existing['fingerprint'] != fingerprint:
                    raise HTTPException(409, '请求编号已用于其他发布内容')
                return load_posts(conn, project, existing['id'])[0]
            check_source(conn, project, run_id, version, title, body)
            post_id = conn.execute('''INSERT INTO publishing_posts(project_id,request_id,fingerprint,title,body,run_id,version)
                VALUES(?,?,?,?,?,?,?)''', (project, str(request_id), fingerprint, title, body, run_id, version)).lastrowid
            conn.executemany('INSERT INTO publishing_assets(post_id,name,data) VALUES(?,?,?)',
                             ((post_id, name, data) for name, data in assets))
            conn.executemany('INSERT INTO publishing_targets(post_id,platform,account) VALUES(?,?,?)',
                             ((post_id, platform, account) for platform, account in destinations))
            return load_posts(conn, project, post_id)[0]

    @app.get(base + '/assets/{id}')
    def download(project: Identifier, id: Identifier):
        with database() as conn:
            row = conn.execute('''SELECT a.name,a.data FROM publishing_assets a JOIN publishing_posts p
                ON p.id=a.post_id WHERE p.project_id=? AND a.id=?''', (project, id)).fetchone()
            if not row:
                raise HTTPException(404, '项目内找不到该附件')
            return Response(row['data'], media_type='application/octet-stream', headers={
                'Content-Disposition': "attachment; filename*=UTF-8''" + quote(row['name'], safe=''),
                'X-Content-Type-Options': 'nosniff'})

    @app.put(base + '/targets/{id}/metrics')
    def measure(project: Identifier, id: Identifier, form: Measurement):
        from datetime import UTC
        if form.sampled.tzinfo is None or not form.source.strip():
            raise HTTPException(422, '请提供带时区的采样时间及数据来源')
        with database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            target = conn.execute('SELECT t.status FROM publishing_targets t JOIN publishing_posts p ON p.id=t.post_id WHERE t.id=? AND p.project_id=?', (id,project)).fetchone()
            if not target or target['status'] != 'published':
                raise HTTPException(409, '仅可为本项目已经确认发布的作品录入表现')
            conn.execute('INSERT OR REPLACE INTO publication_metrics VALUES(?,?,?,?,?)', (id,form.sampled.astimezone(UTC).isoformat(),form.views,form.engagements,form.source.strip()))
        return {'saved': True}

    @app.post(base + '/targets/{id}')
    def confirm(project: Identifier, id: Identifier, form: Confirmation):
        url = work_url(form.url) if form.status == 'published' else ''
        with database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('''SELECT t.* FROM publishing_targets t JOIN publishing_posts p
                ON p.id=t.post_id WHERE p.project_id=? AND t.id=?''', (project, id)).fetchone()
            if not row:
                raise HTTPException(404, '项目内找不到该发布目标')
            if row['status'] == 'published' and (form.status != 'published' or url != row['url']):
                raise HTTPException(409, '该目标已确认发布')
            conn.execute('UPDATE publishing_targets SET status=?,url=? WHERE id=?', (form.status, url, id))
            return {'id': id, 'platform': row['platform'], 'account': row['account'], 'status': form.status, 'url': url}
