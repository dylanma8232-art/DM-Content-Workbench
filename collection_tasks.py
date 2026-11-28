"""Project-scoped collection tasks collected by the browser companion."""

import hashlib
import json
import re
import secrets
import time
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit

from fastapi import HTTPException, Query
from pydantic import BaseModel, Field, StringConstraints, ValidationError, model_validator


NAMES = {"instagram": "Instagram", "tiktok": "TikTok", "douyin": "抖音",
         "xiaohongshu": "小红书", "bilibili": "哔哩哔哩", "x": "X"}
HOSTS = {key: f"www.{key}.com" for key in NAMES}
HOSTS["x"] = "x.com"
ACCOUNT = {
    "instagram": ("/{}/", r"[A-Za-z0-9._]{1,30}"),
    "tiktok": ("/@{}", r"[A-Za-z0-9._]{1,24}"),
    "douyin": ("/user/{}", r"[A-Za-z0-9_-]{8,128}"),
    "xiaohongshu": ("/user/profile/{}", r"[0-9a-fA-F]{24}"),
    "bilibili": ("/{}", r"[1-9][0-9]{0,19}"),
    "x": ("/{}", r"[a-zA-Z0-9_]{1,15}"),
}
SEARCH = {"instagram": "www.instagram.com/explore/search/keyword/?q=",
          "tiktok": "www.tiktok.com/search?q=", "douyin": "www.douyin.com/search/",
          "xiaohongshu": "www.xiaohongshu.com/search_result?keyword=",
          "bilibili": "search.bilibili.com/all?keyword=", "x": "x.com/search?q="}
POST = {"instagram": r"/(?:p|reel)/([A-Za-z0-9_-]+)",
        "tiktok": r"/@[A-Za-z0-9._]{1,24}/video/([0-9]+)",
        "douyin": r"/(?:video|note)/([0-9]+)",
        "xiaohongshu": r"/(?:explore|discovery/item)/([a-fA-F0-9]{24})",
        "bilibili": r"/video/(BV[A-Za-z0-9]{10}|av[0-9]+)",
        "x": r"/[A-Za-z0-9_]{1,15}/status/([0-9]+)"}
RESERVED = {"instagram": {"p", "reel", "reels", "accounts", "explore", "direct", "stories"},
            "x": {"i", "home", "search", "explore", "settings", "messages", "notifications", "compose", "intent"}}
Identifier = Annotated[int, Field(ge=1, le=2**63 - 1)]
Text = Annotated[str, StringConstraints(strip_whitespace=True)]
CollectorLabel = Annotated[Text, Field(max_length=256)]
PLUGIN_PENDING = '等待浏览器插件采集并回传作品'


class TaskForm(BaseModel):
    collector_label: CollectorLabel | None = None
    platform: Literal["instagram", "tiktok", "douyin", "xiaohongshu", "bilibili", "x"]
    mode: Literal["account", "topic"]
    query: Text = Field(min_length=1, max_length=2048)
    limit: int = Field(strict=True, ge=1, le=50)
    minutes: int = Field(default=0, ge=0, le=43200)


class TaskSettings(BaseModel, extra='forbid'):
    collector_label: CollectorLabel | None = None
    limit: int = Field(strict=True, ge=1, le=50)
    minutes: int = Field(strict=True, ge=0, le=43200)


class Segment(BaseModel):
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    text: Text = Field(min_length=1, max_length=5000)

    @model_validator(mode='after')
    def chronological(self):
        if self.end <= self.start:
            raise ValueError('字幕结束时间必须晚于开始时间')
        return self


class ImageText(BaseModel):
    index: int = Field(ge=1, le=1000)
    text: str = Field(default='', max_length=20000)
    status: str = Field(max_length=50)
    error: str = Field(default='', max_length=500)


class VisualFrame(BaseModel):
    time: float = Field(ge=0, allow_inf_nan=False)
    text: str = Field(default='', max_length=20000)
    status: str = Field(max_length=50)


class Item(BaseModel):
    external_id: Text = Field(min_length=1, max_length=128)
    title: Text = Field(max_length=500)
    body: Text = Field(max_length=50000)
    url: str = Field(max_length=4096)
    access_url: str = Field(default='', max_length=4096)
    author: Text = Field(max_length=256)
    content_level: Literal['title_only', 'summary', 'visible_text', 'detail_text', 'subtitle', 'transcript'] = 'summary'
    depth_status: str = Field(default='detail_unavailable', max_length=80)
    detail_error: str = Field(default='', max_length=500)
    detail_source: str = Field(default='', max_length=80)
    content_truncated: bool = False
    language: str = Field(default='', max_length=40)
    segments: list[Segment] = Field(default_factory=list, max_length=2000)
    media_parts: int = Field(default=1, ge=1, le=10000)
    media_part: int = Field(default=1, ge=1, le=10000)
    image_urls: list[str] = Field(default_factory=list, max_length=24)
    images_incomplete: bool = False
    image_assets: list[dict] = Field(default_factory=list, max_length=24)
    frame_assets: list[dict] = Field(default_factory=list, max_length=8)
    media_kind: Literal['unknown', 'image', 'video', 'audio', 'text'] = 'unknown'
    transcript_text: str = Field(default='', max_length=50000)
    transcript_segments: list[Segment] = Field(default_factory=list, max_length=2000)
    image_texts: list[ImageText] = Field(default_factory=list, max_length=24)
    image_observations: list[ImageText] = Field(default_factory=list, max_length=24)
    description_text: str = Field(default='', max_length=50000)
    visual_frames: list[VisualFrame] = Field(default_factory=list, max_length=8)
    media_status: str = Field(default='', max_length=80)
    missing: list[str] = Field(default_factory=list, max_length=100)
    extracted_body: str = Field(default='', max_length=50000)
    analysis_status: str = Field(default='unavailable', max_length=80)
    analysis_truncated: bool = False
    analysis: dict = Field(default_factory=dict)
    analysis_error: str = Field(default='', max_length=1000)
    analysis_model: str = Field(default='', max_length=200)
    analysis_version: str = Field(default='', max_length=80)
    analysis_fingerprint: str = Field(default='', max_length=100)
    evidence: list[dict] = Field(default_factory=list, max_length=300)
    processing_status: Literal['complete', 'partial'] = 'partial'
    processing_missing: list[str] = Field(default_factory=list, max_length=100)


class Results(BaseModel):
    collector_confirmed: bool = Field(default=False, strict=True)
    attempt_token: str = Field(min_length=1, max_length=128)
    items: list[Item] = Field(max_length=50)


def checked_path(url, host):
    allowed = {host, host.removeprefix('www.')}
    if host == 'x.com':
        allowed.update(('www.x.com', 'twitter.com', 'www.twitter.com'))
    if re.search(r'[\s\\\x00-\x1f\x7f]', url):
        raise HTTPException(422, '链接含无效字符')
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise HTTPException(422, '链接格式无效') from exc
    if parts.scheme != 'https' or parts.netloc.lower() not in allowed:
        raise HTTPException(422, '请输入对应平台的官方 HTTPS 链接')
    return parts.path.removesuffix('/')


def target(form):
    platform, query = form.platform, form.query
    if re.search(r'[\x00-\x1f\x7f]', query):
        raise HTTPException(422, '查询含无效字符')
    if form.mode == 'topic':
        if len(query) > 200 or (platform == 'douyin' and query in ('.', '..')):
            raise HTTPException(422, '话题无效或过长')
        return query, 'https://' + SEARCH[platform] + quote(query, safe='')
    host = 'space.bilibili.com' if platform == 'bilibili' else HOSTS[platform]
    template, identifier = ACCOUNT[platform]
    if '://' in query:
        path = checked_path(query, host)
        match = re.fullmatch(template.rstrip('/').format(f'({identifier})'), path)
        query = match.group(1) if match else ''
    elif platform in ('instagram', 'tiktok', 'x'):
        query = query.removeprefix('@')
    if (not re.fullmatch(identifier, query) or query in ('.', '..')
            or query.lower() in RESERVED.get(platform, ())):
        raise HTTPException(422, '请输入有效的账号标识或官方主页链接')
    return query, 'https://' + host + template.format(query)


def normalized(platform, item):
    path = checked_path(item.url, HOSTS[platform])
    match = re.fullmatch(POST[platform], path)
    if not match or match.group(1) != item.external_id:
        raise HTTPException(422, '作品链接与平台或作品标识不匹配')
    if item.access_url:
        access_path = checked_path(item.access_url, HOSTS[platform])
        if access_path != path:
            raise HTTPException(422, '详情访问地址与作品不一致')
    if not item.title and not item.body:
        raise HTTPException(422, '作品缺少标题和正文')
    return {**item.model_dump(), 'url': 'https://' + HOSTS[platform] + path}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def initialize(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS collection_material_retries (
        run_id INTEGER, material_id INTEGER, token TEXT, expires REAL,
        PRIMARY KEY(run_id, material_id))''')
    import content_image_store
    content_image_store.initialize(conn)
    conn.execute('''CREATE TABLE IF NOT EXISTS collection_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL, platform TEXT NOT NULL,
        mode TEXT NOT NULL, query TEXT NOT NULL, url TEXT NOT NULL, "limit" INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'ready', attempt_token TEXT, receipt TEXT, signature TEXT,
        created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS collection_posts (
        project_id INTEGER NOT NULL, platform TEXT NOT NULL, external_id TEXT NOT NULL, hash TEXT NOT NULL,
        PRIMARY KEY(project_id,platform,external_id))''')
    columns = {row[1] for row in conn.execute('PRAGMA table_info(collection_posts)')}
    if 'depth_rank' not in columns:
        conn.execute('ALTER TABLE collection_posts ADD COLUMN depth_rank INTEGER NOT NULL DEFAULT 0')
    if 'quality' not in columns:
        conn.execute('ALTER TABLE collection_posts ADD COLUMN quality INTEGER NOT NULL DEFAULT 0')
    task_columns = {row[1] for row in conn.execute('PRAGMA table_info(collection_tasks)')}
    for name, definition in [('collector_label', "TEXT NOT NULL DEFAULT ''"), ('minutes', 'INTEGER NOT NULL DEFAULT 0'), ('due', 'REAL NOT NULL DEFAULT 0'),
                             ('lease_until', 'REAL NOT NULL DEFAULT 0'), ('last_run_id', 'INTEGER'), ('current_partial', 'INTEGER'), ('last_error', "TEXT NOT NULL DEFAULT ''")]:
        if name not in task_columns:
            conn.execute(f'ALTER TABLE collection_tasks ADD COLUMN {name} {definition}')


def insert_task(conn, project, form):
    query, url = target(form)
    return conn.execute('''INSERT INTO collection_tasks(project_id,platform,mode,query,url,"limit",minutes,due,collector_label)
        VALUES(?,?,?,?,?,?,?,?,?)''', (project, form.platform, form.mode, query, url, form.limit,
        form.minutes, time.time() + form.minutes * 60, form.collector_label or '')).lastrowid


def public(row):
    value = {key: row[key] for key in row.keys() if key not in ('receipt', 'signature')}
    receipt = json.loads(row['receipt']) if row['receipt'] else {}
    value['run_id'] = receipt.get('run_id')
    value['batch_partial'] = row['current_partial'] if receipt and row['current_partial'] is not None else receipt.get('batch_partial')
    value['result'] = receipt or None
    value['next_run'] = row['due'] if row['minutes'] and row['status'] != 'browser' else None
    value['last_run_id'] = value['run_id'] or row['last_run_id']
    value['message'] = row['last_error'] or receipt.get('message', '')
    return value


def mount(app, database, insert_run, project_settings, clean=None, persist=None):
    route = '/api/projects/{project}/collection-tasks'
    import content_image_store
    content_image_store.mount(app, database, project_settings)

    def process_content(task, rows):
        import content_pipeline
        cache = content_image_store.ImageCache(database, task['project_id'])
        return content_pipeline.process(task['platform'], rows,
            task['query'] if task['mode'] == 'topic' else '', media_cache=cache)

    def lookup(conn, project, source):
        row = conn.execute('SELECT * FROM collection_tasks WHERE project_id=? AND id=?',
                           (project, source)).fetchone()
        if row is None:
            raise HTTPException(404, '项目中找不到采集任务')
        return row

    media_route = '/api/projects/{project}/collection-materials/{run_id}/{material_id}'

    def material(conn, project, run_id, material_id):
        row = conn.execute('SELECT payload FROM runs WHERE project_id=? AND id=?', (project, run_id)).fetchone()
        payload = json.loads(row['payload']) if row else {}
        config = payload.get('config', {})
        for record in payload.get('materials', []):
            if record['id'] == material_id:
                if config.get('format') == 'platform' and config.get('platform') in HOSTS and record.get('raw'):
                    return payload, record['raw']
                body = record.get('body') or ''
                if config.get('extracted_from') == 'media':
                    return payload, {'body':body, 'media_kind':'audio', 'transcript_text':body}
                return payload, {'body':body, 'media_kind':'text'}
        raise HTTPException(404, '项目中找不到该素材')

    @app.get(media_route)
    def material_report(project: Identifier, run_id: Identifier, material_id: Identifier):
        from content_pipeline import media_report
        with database() as conn:
            payload, raw = material(conn, project, run_id, material_id)
            report = media_report(raw)
            task_id = payload['config'].get('collection_task_id')
            task = conn.execute('SELECT id,last_run_id FROM collection_tasks WHERE project_id=? AND id=?', (project,task_id)).fetchone()
            if task:
                report.update(task_id=task['id'], latest_batch=task['last_run_id']==run_id)
            return report

    @app.post(media_route + '/retry-speech')
    def retry_speech(project: Identifier, run_id: Identifier, material_id: Identifier):
        import time
        import collection_depth
        from content_pipeline import SPEECH_MISSING, media_report
        token = secrets.token_hex(16)
        key = (run_id, material_id)
        with database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            payload, raw = material(conn, project, *key)
            before = media_report(raw)
            if not before['can_retry']:
                return {'before': before, 'after': before}
            try:
                source = normalized(payload['config']['platform'], Item.model_validate(raw))
            except ValidationError as exc:
                raise HTTPException(422, '旧素材缺少平台标识或有效的来源信息，请重新采集') from exc
            source['content_level'] = 'detail_text'
            if not conn.execute('''INSERT INTO collection_material_retries VALUES(?,?,?,?)
                ON CONFLICT(run_id,material_id) DO UPDATE SET token=excluded.token,expires=excluded.expires
                WHERE expires <= ?''', (*key, token, time.time()+180, time.time())).rowcount:
                raise HTTPException(409, '此素材正在补转写，请稍后刷新')
        try:
            try:
                speech = collection_depth.media_text(source, time.monotonic()+90)
            except Exception as exc:
                raise HTTPException(502, '补转写失败，请检查内容处理服务后重试') from exc
            words = (speech.get('body') or '').strip()
            if speech.get('content_level') not in ('subtitle','transcript') or not words:
                raise HTTPException(422, '本次仍未取得字幕或转写，请检查原文和语音转写设置')
            with database() as conn:
                conn.execute('BEGIN IMMEDIATE')
                if not conn.execute('SELECT 1 FROM collection_material_retries WHERE run_id=? AND material_id=? AND token=?', (*key, token)).fetchone():
                    raise HTTPException(409, '本次补转写已过期，请刷新素材')
                payload, current = material(conn, project, *key)
                if current != raw:
                    raise HTTPException(409, '素材已变化，请刷新后重试')
                current['transcript_text'] = words[:50000]
                current['transcript_segments'] = [Segment.model_validate(entry).model_dump() for entry in speech.get('segments', [])[:2000]]
                current['missing'] = [reason for reason in current.get('missing', []) if reason != 'speech_unavailable']
                current['processing_missing'] = [reason for reason in before['missing'] if reason not in (SPEECH_MISSING, 'speech_unavailable')]
                current['processing_status'] = 'partial' if current['processing_missing'] or current['missing'] or current.get('analysis_status') != 'ready' else 'complete'
                conn.execute('UPDATE runs SET payload=? WHERE id=? AND project_id=?', (json.dumps(payload, ensure_ascii=False), run_id, project))
                task = conn.execute('SELECT id,receipt FROM collection_tasks WHERE project_id=? AND id=?',
                                    (project, payload['config'].get('collection_task_id'))).fetchone()
                receipt = json.loads(task['receipt']) if task and task['receipt'] else {}
                if receipt.get('run_id') == run_id:
                    partial = sum(entry.get('raw', {}).get('processing_status') == 'partial' for entry in payload['materials'])
                    conn.execute('UPDATE collection_tasks SET current_partial=? WHERE id=?', (partial, task['id']))
                return {'before': before, 'after': media_report(current)}
        finally:
            with database() as conn:
                conn.execute('DELETE FROM collection_material_retries WHERE run_id=? AND material_id=? AND token=?', (*key, token))

    @app.get(route)
    def tasks(project: Identifier, offset: int = Query(0, ge=0), limit: int | None = Query(None, ge=1, le=100),
              task_id: int | None = Query(None, ge=1, le=2**63-1)):
        with database() as conn:
            conn.execute('BEGIN')
            project_settings(conn, project)
            sql = 'SELECT * FROM collection_tasks WHERE project_id=? ORDER BY id DESC'
            params = [project]
            paging = {}
            if limit is not None:
                total = conn.execute('SELECT count(*) FROM collection_tasks WHERE project_id=?', params).fetchone()[0]
                if task_id and conn.execute('SELECT 1 FROM collection_tasks WHERE project_id=? AND id=?', (project, task_id)).fetchone():
                    ahead = conn.execute('SELECT count(*) FROM collection_tasks WHERE project_id=? AND id>?', (project, task_id)).fetchone()[0]
                    offset = ahead // limit * limit
                offset = min(offset, max(0, (total-1)//limit*limit))
                paging = {'total':total, 'offset':offset}
                sql += ' LIMIT ? OFFSET ?'
                params.extend([limit, offset])
            rows = conn.execute(sql, params)
            return {**paging, 'tasks': [public(row) for row in rows],
                    'platforms': [{'id': key, 'name': name} for key, name in NAMES.items()]}

    @app.post(route, status_code=201)
    def create(project: Identifier, form: TaskForm):
        with database() as conn:
            project_settings(conn, project)
            source = insert_task(conn, project, form)
            return public(lookup(conn, project, source))

    @app.get(route + '/{source}')
    def get(project: Identifier, source: Identifier):
        with database() as conn:
            return public(lookup(conn, project, source))

    @app.put(route + '/{source}')
    def update(project: Identifier, source: Identifier, form: TaskSettings):
        import time
        with database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            task = lookup(conn, project, source)
            now = time.time()
            if task['lease_until'] > now:
                raise HTTPException(409, '该任务正在处理，请完成后再修改')
            label = (form.collector_label or '') if 'collector_label' in form.model_fields_set else task['collector_label']
            if (task['limit'], task['minutes'], task['collector_label']) != (form.limit, form.minutes, label):
                due = task['due'] if task['minutes'] == form.minutes else now + form.minutes * 60
                conn.execute('''UPDATE collection_tasks SET "limit"=?, minutes=?, due=?, status='ready',
                    attempt_token=NULL, signature=NULL, receipt=NULL, last_error='', lease_until=0,
                    collector_label=?, current_partial=NULL, last_run_id=? WHERE project_id=? AND id=?''',
                    (form.limit, form.minutes, due, label, public(task)['last_run_id'], project, source))
            return public(lookup(conn, project, source))

    @app.delete(route + '/{source}')
    def delete(project: Identifier, source: Identifier):
        with database() as conn:
            if not conn.execute('DELETE FROM collection_tasks WHERE project_id=? AND id=?', (project, source)).rowcount:
                raise HTTPException(404, '项目中找不到采集任务')
            return {'deleted': True}

    def commit(project, source, token, items, expected, *, historical=False):
        with database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            task = lookup(conn, project, source)
            if task['attempt_token'] != token:
                raise HTTPException(409, '采集请求已过期')
            if not historical and len(items) > task['limit']:
                raise HTTPException(422, '返回条数超出任务上限')
            records = [normalized(task['platform'], item) for item in items]
            signature = digest(records)
            if task['status'] == 'completed' and task['signature'] == signature:
                return json.loads(task['receipt'])
            if task['status'] != expected:
                raise HTTPException(409, '采集任务状态已变更')
            materials, hashes = [], {}
            updated = skipped_quality = 0
            for record in records:
                key = record['external_id']
                from collection_depth import LEVELS
                depth_rank = LEVELS[record['content_level']]
                from content_media import media_fingerprint
                fingerprint = digest({k: record[k] for k in ('title', 'body', 'author', 'content_level', 'segments',
                                                              'analysis_status', 'analysis_fingerprint', 'image_assets', 'frame_assets', 'processing_status')} | {'media': media_fingerprint(record)})
                if key in hashes:
                    if hashes[key] != fingerprint:
                        raise HTTPException(422, '同一作品在本批次中有多份不同内容')
                    continue
                hashes[key] = fingerprint
                identity = (project, task['platform'], key)
                quality = sum(bool(r.get('text')) for k in ('image_texts', 'image_observations', 'visual_frames') for r in record[k]) + (100 if record['analysis_status'] == 'ready' else 0)
                old = conn.execute('SELECT hash,depth_rank,quality FROM collection_posts WHERE project_id=? AND platform=? AND external_id=?', identity).fetchone()
                if old and old['hash'] == fingerprint:
                    continue
                if old and (old['depth_rank'] > depth_rank or (old['depth_rank'] == depth_rank and old['quality'] > quality)):
                    skipped_quality += 1
                    continue
                conn.execute('INSERT OR REPLACE INTO collection_posts(project_id,platform,external_id,hash,depth_rank,quality) VALUES(?,?,?,?,?,?)', (*identity, fingerprint, depth_rank, quality))
                updated += int(old is not None)
                materials.append({'id': len(materials) + 1, 'title': record['title'] or record['body'][:100],
                                  'body': record['body'] or record['title'], 'raw': record})
            name = f"{NAMES[task['platform']]} · {task['query']}"
            config = {'project_id': project, 'format': 'platform', 'filename': name,
                      'platform': task['platform'], 'collection_task_id': source}
            run_id = None
            if materials:
                run = insert_run(conn, name, config, materials)
                if clean and persist:
                    run['payload']['cleaned'] = clean(materials, run['payload']['config'])
                    run['stage'] = 2
                    persist(conn, run)
                run_id = run['id']
            added, duplicates = len(materials) - updated, len(records) - len(materials)
            depth_counts = {label: sum(r['content_level'] == label for r in records)
                            for label in ('title_only', 'summary', 'visible_text', 'detail_text', 'subtitle', 'transcript')}
            depth_message = f"详情 {depth_counts['detail_text']} · 字幕 {depth_counts['subtitle']} · 转写 {depth_counts['transcript']} · 仅列表 {depth_counts['title_only'] + depth_counts['summary'] + depth_counts['visible_text']}"
            processing_counts = {'complete': sum(r['processing_status'] == 'complete' for r in records),
                                 'partial': sum(r['processing_status'] != 'complete' for r in records)}
            result = {'browser': False, 'added': added, 'updated': updated, 'duplicates': duplicates,
                      'received': len(records), 'imported': len(materials), 'source_added': added,
                      'source_updated': updated, 'source_duplicates': duplicates - skipped_quality, 'skipped_quality': skipped_quality,
                      'batch_partial': sum(item['raw']['processing_status'] == 'partial' for item in materials),
                      'processing_counts': processing_counts, 'depth_counts': depth_counts,
                      'run_id': run_id, 'message': f'读取 {len(records)} · 新增 {added} · 更新 {updated} · 重复 {duplicates - skipped_quality} · 低质量跳过 {skipped_quality} · {depth_message} · 回传已解析 {processing_counts["complete"]} · 回传部分完成 {processing_counts["partial"]}'}
            conn.execute('INSERT INTO batches(project_id,run_id,name,received,added,updated,duplicates) VALUES(?,?,?,?,?,?,?)',
                         (project, run_id, name, len(records), added, updated, duplicates))
            conn.execute("UPDATE collection_tasks SET status='completed',signature=?,receipt=?,current_partial=NULL,lease_until=0,last_error='',last_run_id=COALESCE(?,last_run_id) WHERE id=?",
                         (signature, json.dumps(result, ensure_ascii=False), run_id, source))
            return result

    @app.post(route + '/{source}/results')
    def results(project: Identifier, source: Identifier, form: Results):
        # Claim this attempt before external work so duplicate callbacks cannot run models twice.
        with database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            task = lookup(conn, project, source)
            if task['attempt_token'] != form.attempt_token:
                raise HTTPException(409, '采集请求已过期')
            if task['status'] == 'completed' and task['receipt']:
                return json.loads(task['receipt'])
            if task['status'] != 'browser':
                raise HTTPException(409, '采集任务状态已变更')
            if task['collector_label'] and not form.collector_confirmed:
                raise HTTPException(422, '请先确认任务指定的采集账号标签')
            records = [normalized(task['platform'], item) for item in form.items]
            if len(records) > task['limit']:
                raise HTTPException(422, '返回条数超出任务上限')
            import time
            conn.execute("UPDATE collection_tasks SET status='reading',lease_until=? WHERE id=?", (time.time()+1800, source))
        try:
            enriched = process_content(task, records)
            return commit(project, source, form.attempt_token, [Item.model_validate(row) for row in enriched], 'reading')
        except Exception as exc:
            with database() as conn:
                conn.execute("UPDATE collection_tasks SET status='browser',lease_until=0,last_error='内容处理未完成，可再次提交' WHERE id=? AND attempt_token=? AND status='reading'", (source,form.attempt_token))
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(502, '内容处理未完成，可再次提交') from exc

    @app.post(route + '/{source}/reprocess')
    def reprocess(project: Identifier, source: Identifier):
        import time
        token = secrets.token_urlsafe(32)
        with database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            task = lookup(conn, project, source)
            if task['lease_until'] > time.time():
                raise HTTPException(409, '该任务正在处理，请稍后刷新')
            run_row = conn.execute('SELECT payload FROM runs WHERE id=? AND project_id=?',
                                   (task['last_run_id'], project)).fetchone()
            if not run_row:
                raise HTTPException(404, '暂无可继续解析的素材，请先采集')
            rows = [m['raw'] for m in json.loads(run_row['payload']).get('materials', []) if m.get('raw')]
            conn.execute("UPDATE collection_tasks SET status='reading',attempt_token=?,lease_until=? WHERE id=?",
                         (token, time.time() + 1800, source))
        try:
            rows = process_content(task, rows)
            return commit(project, source, token, [Item.model_validate(row) for row in rows], 'reading', historical=True)
        except Exception as exc:
            with database() as conn:
                conn.execute("UPDATE collection_tasks SET status='failed',lease_until=0,last_error='继续解析失败，原素材已保留' WHERE id=? AND attempt_token=?", (source, token))
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(502, '继续解析失败，原素材已保留') from exc

    def due_tasks(stop):
        import time
        with database() as conn:
            rows = conn.execute("SELECT project_id,id FROM collection_tasks WHERE minutes>0 AND due<=? AND lease_until<=? AND status!='browser' ORDER BY due LIMIT 3",
                                (time.time(), time.time())).fetchall()
        for task in rows:
            if stop.is_set():
                return
            try:
                execute(task['project_id'], task['id'], scheduled=True)
            except Exception:
                # The task preserves its failure state and next due time. Other tasks can continue.
                continue

    app.state.collect_due = due_tasks

    @app.post(route + '/{source}/run')
    def run(project: Identifier, source: Identifier):
        return execute(project, source)

    def execute(project, source, *, scheduled=False):
        token = secrets.token_urlsafe(32)
        with database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            task = lookup(conn, project, source)
            now = time.time()
            if scheduled and (not task['minutes'] or task['due'] > now or task['status'] == 'browser'):
                return
            if task['lease_until'] > now:
                return {'in_progress': True, 'task': public(task), 'message': '该任务已在执行，刷新可查看进度和结果'}
            conn.execute("UPDATE collection_tasks SET status='browser',attempt_token=?,signature=NULL,last_error=?,lease_until=0,due=? WHERE project_id=? AND id=?",
                         (token, PLUGIN_PENDING, now + max(1, task['minutes']) * 60, project, source))
            return {'browser': True, 'task': public(lookup(conn, project, source)), 'message': PLUGIN_PENDING}
