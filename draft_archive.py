"""Explicit, project-scoped archival of reviewed drafts. No background writes.

Call mount(app, database, get_run, project_settings) from the application.
A persisted sending/uncertain receipt must be verified remotely before any retry.
"""
import hashlib
import json
import re
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException, Request

import online_libraries as libraries

MAX_CONTENT = 100_000


def _schema(conn):
    libraries._schema(conn)
    conn.execute('''CREATE TABLE IF NOT EXISTS draft_archives (
        id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, run_id INTEGER NOT NULL,
        version INTEGER NOT NULL, library_id INTEGER NOT NULL, draft_hash TEXT NOT NULL,
        provider TEXT NOT NULL, state TEXT NOT NULL, remote_id TEXT NOT NULL DEFAULT '',
        remote_url TEXT NOT NULL DEFAULT '', message TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(run_id, version, library_id))''')
    if 'target_name' not in {row[1] for row in conn.execute('PRAGMA table_info(draft_archives)')}:
        conn.execute("ALTER TABLE draft_archives ADD COLUMN target_name TEXT NOT NULL DEFAULT ''")


def _approved(run, version):
    data = run['payload']
    drafts, reviews = data.get('drafts', []), data.get('reviews', [])
    if (run.get('stage') != 5 or not drafts or version != len(drafts)
            or not reviews or reviews[-1].get('version') != version
            or reviews[-1].get('decision') != '通过'):
        raise HTTPException(409, '请返回“审核稿件”，审核最新保存版本并选择“通过”后再归档')
    content = drafts[-1]
    if not isinstance(content, str) or not content.strip():
        raise HTTPException(422, '草稿正文为空，无法归档')
    if len(content) > MAX_CONTENT:
        raise HTTPException(422, '稿件超过 100000 字符，请导出 Markdown 后手动归档')
    return content


def _receipt(row):
    return {key: row[key] for key in (
        'id', 'project_id', 'run_id', 'version', 'library_id', 'draft_hash',
        'provider', 'target_name', 'state', 'remote_id', 'remote_url', 'message', 'created_at', 'updated_at')}


class RemoteError(Exception):
    def __init__(self, uncertain=False):
        self.uncertain = uncertain


class ArchiveRemote:
    """Fixed API hosts, bounded responses, no redirects or environment proxies."""
    def __init__(self, target):
        self.target = target
        self.secrets = [target['token'], target['app_secret']]
        self.client = httpx.Client(timeout=httpx.Timeout(20, connect=5),
                                   follow_redirects=False, trust_env=False)

    def request(self, method, url, *, writing=False, **kwargs):
        try:
            with self.client.stream(method, url, **kwargs) as response:
                if not 200 <= response.status_code < 300:
                    # A gateway/server error may follow a committed creation.
                    raise RemoteError(writing and (response.status_code >= 500 or response.status_code == 408))
                parts, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > libraries.MAX_BYTES:
                        raise RemoteError(writing)
                    parts.append(chunk)
                result = json.loads(b''.join(parts))
                if not isinstance(result, dict):
                    raise RemoteError(writing)
                return result
        except RemoteError:
            raise
        except Exception:
            # Do not retain request headers, remote bodies or exception strings.
            raise RemoteError(writing) from None

    def prepare(self, title, content):
        target = self.target
        resource, table = libraries._target(target)
        if len(title) > 2000:
            raise HTTPException(422, '标题超过 2000 字符，请缩短标题或导出 Markdown 后手动归档')
        if target['provider'] == 'notion':
            self.headers = {'Authorization': 'Bearer ' + target['token'], 'Notion-Version': '2022-06-28'}
            schema = self.request('GET', f'https://api.notion.com/v1/databases/{resource}', headers=self.headers)
            fields = schema.get('properties', {})
            if (schema.get('archived') or schema.get('in_trash')
                    or fields.get(target['title_field'], {}).get('type') != 'title'
                    or (target['body_field'] and (target['body_field'] == target['title_field']
                        or fields.get(target['body_field'], {}).get('type') != 'rich_text'))):
                raise HTTPException(422, '请确认数据库可写、标题字段为 title、正文字段为 rich_text；也可导出 Markdown 手动归档')
            properties = {target['title_field']: {'title': self.rich_text(title)}}
            if target['body_field']:
                properties[target['body_field']] = {'rich_text': self.rich_text(content)}
            children = [{'object': 'block', 'type': 'paragraph',
                         'paragraph': {'rich_text': self.rich_text(content[i:i + 2000])}}
                        for i in range(0, len(content), 2000)]
            self.body = {'parent': {'database_id': resource}, 'properties': properties, 'children': children}
            if len(children) > 100 or len(json.dumps(self.body, ensure_ascii=False).encode()) > 500_000:
                raise HTTPException(422, '稿件超出 Notion 单次归档容量，请导出 Markdown 后手动归档')
            self.url = 'https://api.notion.com/v1/pages'
        elif target['provider'] == 'feishu':
            base = 'https://open.feishu.cn/open-apis'
            auth = self.request('POST', base + '/auth/v3/tenant_access_token/internal',
                                json={'app_id': target['app_id'], 'app_secret': target['app_secret']})
            token = auth.get('tenant_access_token')
            if auth.get('code') != 0 or not isinstance(token, str) or not token:
                raise RemoteError()
            self.secrets.append(token)
            self.headers = {'Authorization': 'Bearer ' + token}
            self.url = f'{base}/bitable/v1/apps/{resource}/tables/{table}/records'
            fields, cursor = {}, None
            for _ in range(20):
                params = {'page_size': 100}
                if cursor:
                    params['page_token'] = cursor
                response = self.request('GET', self.url.removesuffix('/records') + '/fields',
                                        headers=self.headers, params=params)
                if response.get('code') != 0:
                    raise RemoteError()
                data = response.get('data', {})
                fields.update({f['field_name']: f for f in data.get('items', [])})
                if not data.get('has_more'):
                    break
                cursor = data.get('page_token')
                if not isinstance(cursor, str) or not cursor:
                    raise RemoteError()
            else:
                raise HTTPException(422, '飞书字段过多，请导出 Markdown 后手动归档')
            names = (target['title_field'], target['body_field'])
            if (not names[1] or names[0] == names[1] or any(
                    fields.get(name, {}).get('type') != 1
                    or fields.get(name, {}).get('is_read_only')
                    or fields.get(name, {}).get('is_readonly') for name in names)):
                raise HTTPException(422, '请选择已有且可写的飞书标题、正文文本字段；正文必填')
            self.body = {'fields': {names[0]: title, names[1]: content}}
        else:
            raise HTTPException(422, '不支持的归档服务')

    @staticmethod
    def rich_text(text):
        spans = [{'type': 'text', 'text': {'content': text[i:i + 2000]}}
                 for i in range(0, len(text), 2000)]
        if len(spans) > 100:
            raise HTTPException(422, '稿件超出 Notion 字段容量，请导出 Markdown 后手动归档')
        return spans

    def create(self):
        data = self.request('POST', self.url, writing=True, headers=self.headers, json=self.body)
        if self.target['provider'] == 'notion':
            remote_id = data.get('id', '')
            if not isinstance(remote_id, str) or not re.fullmatch(r'[a-fA-F0-9]{32}|[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}', remote_id):
                raise RemoteError(True)
            remote_url = self.notion_url(data.get('url', ''), remote_id)
        else:
            # Documented API errors mean no record was created.
            if 'code' not in data:
                raise RemoteError(True)
            if data['code'] != 0:
                raise RemoteError()
            remote_id = data.get('data', {}).get('record', {}).get('record_id', '')
            if not isinstance(remote_id, str) or not re.fullmatch(r'rec[A-Za-z0-9]{1,100}', remote_id):
                raise RemoteError(True)
            resource, table = libraries._target(self.target)
            host = urlsplit(self.target['address']).hostname if self.target['address'].startswith('https://') else 'www.feishu.cn'
            remote_url = f'https://{host}/base/{resource}?table={table}&record={remote_id}'
        if any(secret and (secret in remote_url or secret in remote_id) for secret in self.secrets):
            raise RemoteError(True)
        return remote_id, remote_url

    @staticmethod
    def notion_url(value, remote_id):
        try:
            parsed = urlsplit(value)
            host = parsed.hostname or ''
            if (parsed.scheme == 'https' and not parsed.username and not parsed.password
                    and not parsed.port and not parsed.query and not parsed.fragment
                    and any(host == h or host.endswith('.' + h) for h in ('notion.so', 'notion.site'))
                    and re.fullmatch(r'/[A-Za-z0-9_/-]+', parsed.path)
                    and parsed.path.replace('-', '').endswith(remote_id.replace('-', ''))):
                return value
        except (ValueError, TypeError):
            pass
        return ''


def mount(app, database, get_run, project_settings):
    @app.get('/api/projects/{project}/draft-archives')
    def listing(project: int):
        with database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            project_settings(conn, project)
            _schema(conn)
            return [_receipt(row) for row in conn.execute(
                'SELECT * FROM draft_archives WHERE project_id=? ORDER BY id DESC', (project,))]

    @app.post('/api/runs/{run_id}/archive')
    async def archive(run_id: int, request: Request):
        try:
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 4096:
                    raise ValueError()
            form = json.loads(raw)
            if (not isinstance(form, dict) or set(form) - {'version', 'library_id'}
                    or type(form.get('version')) is not int or form['version'] < 1
                    or ('library_id' in form and (type(form['library_id']) is not int or form['library_id'] < 1))):
                raise ValueError()
        except (ValueError, TypeError):
            raise HTTPException(422, '请提供有效的版本和资料库编号') from None
        version = form['version']
        with database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            run = get_run(conn, run_id)
            content = _approved(run, version)
            project = run['project_id']
            settings = project_settings(conn, project)
            library_id = form.get('library_id', settings.get('archive_library_id'))
            if type(library_id) is not int or library_id < 1:
                raise HTTPException(422, '请在项目设置中选择归档资料库，或在归档面板选择目标')
            _schema(conn)
            target = libraries._load(conn, project, library_id)
            libraries._target(target)
            digest = hashlib.sha256(content.encode()).hexdigest()
            previous = conn.execute('SELECT * FROM draft_archives WHERE run_id=? AND version=? AND library_id=?',
                                    (run_id, version, library_id)).fetchone()
            if previous:
                if previous['draft_hash'] != digest:
                    raise HTTPException(409, '稿件内容已变化，请保存新版本并重新审核')
                if previous['state'] != 'failed':
                    return _receipt(previous)
                receipt_id = previous['id']
                conn.execute("UPDATE draft_archives SET state='sending',message='',updated_at=CURRENT_TIMESTAMP WHERE id=?", (receipt_id,))
            else:
                receipt_id = conn.execute('''INSERT INTO draft_archives
                    (project_id,run_id,version,library_id,draft_hash,provider,target_name,state)
                    VALUES(?,?,?,?,?,?,?,'sending')''',
                    (project, run_id, version, library_id, digest, target['provider'], target['name'])).lastrowid
            title = str((run['payload'].get('topic') or {}).get('title') or run.get('name') or '已审核稿件')
        remote = ArchiveRemote(target)
        state, message, remote_id, remote_url = 'failed', '', '', ''
        creating = False
        try:
            remote.prepare(title, content)
            # Recheck after schema reads. Hold the write lock through creation so
            # review revocation/draft editing cannot race the outgoing write.
            with database() as conn:
                conn.execute('BEGIN IMMEDIATE')
                current = get_run(conn, run_id)
                current_content = _approved(current, version)
                if (current['project_id'] != project or current_content != content
                        or libraries._load(conn, project, library_id) != target):
                    raise HTTPException(409, '稿件或资料库配置已变化，请重新核对')
                creating = True
                remote_id, remote_url = remote.create()
                state, message = 'sent', '已归档当前审核版本'
                conn.execute('''UPDATE draft_archives SET state=?,remote_id=?,remote_url=?,message=?,
                    updated_at=CURRENT_TIMESTAMP WHERE id=?''', (state, remote_id, remote_url, message, receipt_id))
        except HTTPException:
            with database() as conn:
                conn.execute("UPDATE draft_archives SET state='failed',message='归档校验未通过，请核对版本、字段及写入权限',updated_at=CURRENT_TIMESTAMP WHERE id=?", (receipt_id,))
            raise
        except RemoteError as error:
            state = 'uncertain' if error.uncertain else 'failed'
            message = ('发送结果不确定，请先在远端核实是否已创建；已阻止重复发送，请勿盲目重试'
                       if error.uncertain else '归档失败，请检查字段及独立的写入权限；可重试或导出 Markdown 手动归档')
        except Exception:
            # Once a create may have happened, never invite a blind retry.
            state = 'uncertain' if creating else 'failed'
            message = ('发送结果不确定，请先在远端核实；已阻止重复发送' if creating
                       else '归档校验失败，请检查目标字段及写入权限；可导出 Markdown 手动归档')
        finally:
            remote.client.close()
        with database() as conn:
            conn.execute('''UPDATE draft_archives SET state=?,remote_id=?,remote_url=?,message=?,
                updated_at=CURRENT_TIMESTAMP WHERE id=?''', (state, remote_id, remote_url, message, receipt_id))
            return _receipt(conn.execute('SELECT * FROM draft_archives WHERE id=?', (receipt_id,)).fetchone())
