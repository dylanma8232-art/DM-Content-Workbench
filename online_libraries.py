"""Project-scoped, read-only Notion/Feishu imports. No environment-file access.

API: GET collection -> Connection[]; POST collection -> Connection;
POST /{id}/check -> {status, fields, count, titles, partial};
POST /{id}/import -> {status, run_id, stage, added, skipped, partial, deferred}.
Connection: id/provider/name/address/title_field/body_field/status/key_saved/app_id/table_id.
Notion: <=10 query pages, <=2000 records; block fallback for <=20 pages,
<=2 batches of 100 top-level blocks each (nested/unread blocks mark partial).
Unchanged fallback bodies are cached on successful imports; pages exceeding the
fallback budget are deferred so later imports can continue without empty placeholders.
Feishu: <=20 batches of 100 records. A check previews <=5 records.
"""
import hashlib
import json
import re
from typing import Literal
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_BYTES = 2 * 1024 * 1024


class ConnectionForm(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: int | None = Field(default=None, ge=1)
    provider: Literal["notion", "feishu"]
    name: str = Field(min_length=1, max_length=100)
    address: str = Field(min_length=1, max_length=2048)
    title_field: str = Field(min_length=1, max_length=200)
    body_field: str = Field(default="", max_length=200)
    token: str | None = Field(default=None, max_length=4096)
    app_id: str = Field(default="", max_length=200)
    app_secret: str | None = Field(default=None, max_length=4096)
    table_id: str = Field(default="", max_length=200)


def _fail(message="在线库读取失败，请检查凭据、地址和读取权限", code=400):
    raise HTTPException(code, message)


def _schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS online_library_results (
        library_id INTEGER PRIMARY KEY, fields TEXT, receipt TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS online_libraries (
        id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, provider TEXT NOT NULL,
        name TEXT NOT NULL, address TEXT NOT NULL, title_field TEXT NOT NULL,
        body_field TEXT NOT NULL, app_id TEXT NOT NULL, table_id TEXT NOT NULL,
        status TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS online_library_secrets (
        library_id INTEGER PRIMARY KEY, token TEXT NOT NULL, app_secret TEXT NOT NULL)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS online_library_fingerprints (
        library_id INTEGER NOT NULL, source_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
        PRIMARY KEY(library_id, source_id))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS online_library_body_cache (
        library_id INTEGER NOT NULL, page_id TEXT NOT NULL, metadata TEXT NOT NULL,
        item TEXT NOT NULL, partial INTEGER NOT NULL,
        PRIMARY KEY(library_id, page_id))''')


def _target(form):
    address = form["address"]
    if form["provider"] == "notion":
        if re.fullmatch(r"[a-fA-F0-9]{32}", address):
            return address.lower(), ""
        parsed = urlsplit(address)
        host = parsed.hostname or ""
        if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port
                or not any(host == h or host.endswith("." + h) for h in ("notion.so", "notion.site"))):
            _fail("请输入有效的 Notion HTTPS 数据库地址或 32 位数据库编号", 422)
        tail = parsed.path.rstrip("/").split("/")[-1].replace("-", "")
        match = re.search(r"([a-fA-F0-9]{32})$", tail)
        if not match:
            _fail("Notion 地址中缺少数据库编号", 422)
        return match.group(1).lower(), ""
    if address.startswith("https://"):
        parsed = urlsplit(address)
        if (not (parsed.hostname or "").endswith(".feishu.cn") or parsed.username
                or parsed.password or parsed.port):
            _fail("请输入有效的飞书多维表格 HTTPS 地址", 422)
        match = re.fullmatch(r"/base/([A-Za-z0-9]+)(?:/)?", parsed.path)
        if not match:
            _fail("飞书地址中缺少多维表格编号", 422)
        app_token = match.group(1)
        table = parse_qs(parsed.query).get("table", [form["table_id"]])[0]
    else:
        app_token, table = address, form["table_id"]
    if not re.fullmatch(r"[A-Za-z0-9]+", app_token) or not re.fullmatch(r"tbl[A-Za-z0-9]+", table):
        _fail("请填写有效的飞书 app_token 和 table_id", 422)
    if not form["body_field"]:
        _fail("请选择飞书正文和标题字段", 422)
    return app_token, table


def _public(row):
    return {key: row[key] for key in (
        "id", "provider", "name", "address", "title_field", "body_field", "status",
        "app_id", "table_id"
    )} | {"key_saved": bool(row["token"] if row["provider"] == "notion" else row["app_secret"])}


def _load(conn, project, library_id):
    # Callers may persist a reference after validation (e.g. the default archive target).
    # Serialize that validation and its dependent write with disconnect.
    if not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    row = conn.execute('''SELECT l.*, s.token, s.app_secret FROM online_libraries l
        JOIN online_library_secrets s ON s.library_id=l.id WHERE l.project_id=? AND l.id=?''',
        (project, library_id)).fetchone()
    if not row:
        _fail("此连接已断开或在当前项目中不存在", 404)
    return dict(row)


def _text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (_text(v) for v in value)))
    if isinstance(value, dict):
        if "plain_text" in value:
            return _text(value["plain_text"])
        for key in ("title", "rich_text", "text", "content", "name", "url", "number", "select", "multi_select", "date", "start", "value"):
            if key in value and value[key] is not None:
                return _text(value[key])
        return ""
    return "" if value is None else str(value)


def _notion_text(value):
    # Notion rich-text spans form one continuous paragraph.
    if isinstance(value, list):
        return "".join(_text(v) for v in value)
    if isinstance(value, dict):
        for key in ("title", "rich_text"):
            if key in value:
                return _notion_text(value[key])
    return _text(value)


def _scrub(value, secrets):
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[已隐藏]")
        return value
    if isinstance(value, list):
        return [_scrub(v, secrets) for v in value]
    if isinstance(value, dict):
        return {_scrub(k, secrets): _scrub(v, secrets) for k, v in value.items()}
    return value


class Remote:
    def __init__(self, row, cache=None):
        self.row = row
        self.cache = cache or {}
        self.secrets = [row["token"], row["app_secret"]]
        self.client = httpx.Client(timeout=httpx.Timeout(15, connect=5), follow_redirects=False, trust_env=False)

    def request(self, method, path, **kwargs):
        try:
            with self.client.stream(method, path, **kwargs) as response:
                if response.status_code in (401, 403):
                    _fail('读取授权不足，请在服务平台确认资料库已共享给此应用，再检查连接凭据')
                if response.status_code != 200:
                    _fail()
                parts, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        _fail("在线库响应过大，请缩小数据范围")
                    parts.append(chunk)
                data = json.loads(b"".join(parts))
                if not isinstance(data, dict):
                    _fail()
                return data
        except HTTPException:
            raise
        except Exception:
            _fail()

    def read(self, preview=False):
        try:
            return self._notion(preview) if self.row["provider"] == "notion" else self._feishu(preview)
        except HTTPException:
            raise
        except Exception:
            _fail()
        finally:
            self.client.close()

    def _notion(self, preview):
        database_id, _ = _target(self.row)
        headers = {"Authorization": "Bearer " + self.row["token"], "Notion-Version": "2022-06-28"}
        base = "https://api.notion.com/v1"
        schema = self.request("GET", f"{base}/databases/{database_id}", headers=headers)
        fields = list(schema["properties"])
        self._fields(fields)
        items, cursor, partial, fallback = [], None, False, 0
        cache_updates, deferred = [], 0
        for _ in range(1 if preview else 10):
            body = {"page_size": 5 if preview else 100}
            if cursor:
                body["start_cursor"] = cursor
            data = self.request("POST", f"{base}/databases/{database_id}/query", headers=headers, json=body)
            for page in data["results"][:5 if preview else 100]:
                props = page["properties"]
                self._fields(list(props))
                title = _notion_text(props[self.row["title_field"]])
                text = _notion_text(props.get(self.row["body_field"], {}))
                metadata, page_partial = None, False
                if not text:
                    metadata = hashlib.sha256(json.dumps([
                        page["id"], page.get("last_edited_time"), props, page.get("url"),
                        self.row["provider"], self.row["address"], self.row["title_field"],
                        self.row["body_field"], self.row["token"],
                    ], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                    cached = self.cache.get(page["id"])
                    if cached and cached["metadata"] == metadata:
                        items.append(cached["item"])
                        partial |= cached["partial"]
                        continue
                    if fallback >= 20:
                        partial = True
                        deferred += 1
                        continue
                    else:
                        fallback += 1
                        page_id = page["id"]
                        if not re.fullmatch(r"[a-fA-F0-9-]{32,36}", page_id):
                            _fail()
                        blocks, block_cursor = [], None
                        for _ in range(2):
                            params = {"page_size": 100}
                            if block_cursor:
                                params["start_cursor"] = block_cursor
                            children = self.request("GET", f"{base}/blocks/{page_id}/children", headers=headers, params=params)
                            for block in children["results"][:100]:
                                blocks.append(_notion_text(block.get(block.get("type"), {}).get("rich_text", [])))
                                page_partial |= bool(block.get("has_children"))
                            block_cursor = children.get("next_cursor")
                            if not children.get("has_more"):
                                break
                            if not block_cursor:
                                _fail()
                        else:
                            page_partial = True
                        partial |= page_partial
                        text = "\n".join(filter(None, blocks))
                        page = {**page, "imported_blocks_text": text}
                item = {"source_id": page["id"], "title": title, "body": text,
                        "raw": {**page, "source_url": page.get("url") or "https://www.notion.so/" + page["id"].replace("-", "")}}
                items.append(item)
                if metadata is not None:
                    cache_updates.append({"page_id": page["id"], "metadata": metadata,
                                          "item": item, "partial": page_partial})
            cursor = data.get("next_cursor")
            if not data.get("has_more"):
                break
            if not cursor:
                _fail()
        else:
            partial = True
        result = self._result(fields, items, partial)
        result["cache_updates"] = _scrub(cache_updates, self.secrets)
        result["deferred"] = deferred
        return result

    def _fields(self, fields):
        for key, label in (("title_field", "标题"), ("body_field", "正文")):
            if self.row[key] and self.row[key] not in fields:
                _fail(label+"字段不存在，请修改连接中的"+label+"列名称")

    def _feishu(self, preview):
        app_token, table = _target(self.row)
        base = "https://open.feishu.cn/open-apis"
        auth = self.request("POST", base + "/auth/v3/tenant_access_token/internal",
                            json={"app_id": self.row["app_id"], "app_secret": self.row["app_secret"]})
        if auth.get("code") != 0 or not auth.get("tenant_access_token"):
            _fail()
        self.secrets.append(auth["tenant_access_token"])
        headers = {"Authorization": "Bearer " + auth["tenant_access_token"]}
        url = f"{base}/bitable/v1/apps/{app_token}/tables/{table}"
        fields, cursor = [], None
        for _ in range(20):
            params = {"page_size": 100}
            if cursor:
                params["page_token"] = cursor
            data = self.request("GET", url + "/fields", headers=headers, params=params)
            if data.get("code") != 0:
                _fail()
            fields.extend(v["field_name"] for v in data["data"]["items"])
            if not data["data"].get("has_more"):
                break
            cursor = data["data"].get("page_token")
            if not cursor:
                _fail()
        else:
            _fail("在线库字段过多，请缩小数据范围")
        self._fields(fields)
        items, cursor, partial = [], None, False
        for _ in range(1 if preview else 20):
            params = {"page_size": 100}
            if cursor:
                params["page_token"] = cursor
            data = self.request("GET", url + "/records", headers=headers, params=params)
            if data.get("code") != 0:
                _fail()
            partial |= len(data["data"]["items"]) > (5 if preview else 100)
            for record in data["data"]["items"][:5 if preview else 100]:
                values = record["fields"]
                # Feishu omits empty cells: schema validation above distinguishes missing columns.
                source_url = f"https://www.feishu.cn/base/{app_token}?table={table}&record={record['record_id']}"
                items.append({"source_id": record["record_id"], "title": _text(values.get(self.row["title_field"])),
                              "body": _text(values.get(self.row["body_field"])),
                              "raw": {**record, "source_url": source_url}})
            if not data["data"].get("has_more"):
                break
            cursor = data["data"].get("page_token")
            if not cursor:
                _fail()
        else:
            partial = True
        return self._result(fields, items, partial)

    def _result(self, fields, items, partial):
        return _scrub({"fields": fields, "items": items, "partial": partial}, self.secrets)


def mount(app, database, project_settings, insert_run, root=None):
    """Register routes. `root` is accepted for compatibility and never read."""
    prefix = "/api/projects/{project}/online-libraries"

    @app.get(prefix)
    def listing(project: int):
        with database() as conn:
            project_settings(conn, project)
            _schema(conn)
            rows = conn.execute('''SELECT l.*, s.token, s.app_secret FROM online_libraries l
                JOIN online_library_secrets s ON s.library_id=l.id WHERE project_id=? ORDER BY l.id''', (project,)).fetchall()
            entries = []
            for row in rows:
                entry = _public(row)
                result = conn.execute('SELECT fields,receipt FROM online_library_results WHERE library_id=?', (row['id'],)).fetchone()
                if result:
                    entry.update(fields=json.loads(result['fields'] or '[]'), last_import=json.loads(result['receipt'] or 'null'))
                entries.append(entry)
            return entries

    @app.delete(prefix + '/{library_id}')
    def disconnect(project: int, library_id: int):
        with database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            settings = project_settings(conn, project)
            _schema(conn)
            target = _load(conn, project, library_id)
            revoke = ('如需撤销此资料库的授权，请在 Notion 数据库页面的「···」→「连接」中移除该集成。'
                      if target['provider'] == 'notion' else
                      '如需撤销此资料库的授权，请在飞书多维表格的「分享」→「协作者」中移除该应用。')
            if settings.get('archive_library_id') == library_id:
                _fail('此连接是项目的默认归档资料库。请先在项目设置中更换或清空归档目标并保存。' + revoke, 409)
            archives = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='draft_archives'").fetchone()
            if archives:
                receipt = conn.execute('''SELECT state FROM draft_archives WHERE project_id=? AND library_id=?
                    ORDER BY (state='sending') DESC LIMIT 1''', (project, library_id)).fetchone()
                if receipt and receipt['state'] == 'sending':
                    _fail('归档请求已登记，发送结果尚未确认。请先在远端核实归档结果。' + revoke, 409)
                if receipt:
                    _fail('此连接已有稿件归档记录。归档面板需要通过此连接查看回执，无法断开。' + revoke, 409)
            # Keep the library ID and metadata for historical references. Active
            # lookups join the secrets table, so removing credentials retires it.
            conn.execute('DELETE FROM online_library_secrets WHERE library_id=?', (library_id,))
            conn.execute('DELETE FROM online_library_body_cache WHERE library_id=?', (library_id,))
            conn.execute('DELETE FROM online_library_fingerprints WHERE library_id=?', (library_id,))
            conn.execute("UPDATE online_libraries SET status='disconnected' WHERE id=?", (library_id,))
            return {'status': 'disconnected'}

    @app.post(prefix)
    async def save(project: int, request: Request):
        # Parse manually: FastAPI/Pydantic's default error includes rejected secret input.
        try:
            if int(request.headers.get("content-length", "0")) > 32768:
                _fail("配置内容过长", 422)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 32768:
                    _fail("配置内容过长", 422)
            form = ConnectionForm.model_validate(json.loads(body)).model_dump()
            _target(form)
        except HTTPException:
            raise
        except (ValueError, TypeError, ValidationError):
            _fail("配置格式有误，请检查必填项和字段长度", 422)
        with database() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project_settings(conn, project)
            _schema(conn)
            previous = _load(conn, project, form["id"]) if form["id"] else None
            if previous and previous["provider"] != form["provider"]:
                _fail("修改服务类型时请新建连接", 422)
            token = form.pop("token") or (previous["token"] if previous else "")
            secret = form.pop("app_secret") or (previous["app_secret"] if previous else "")
            if (form["provider"] == "notion" and not token) or (form["provider"] == "feishu" and (not secret or not form["app_id"])):
                _fail("请填写在线库读取凭据", 422)
            # Disallow accidental disclosure of credentials pasted into public metadata.
            if any(value and value in str(form) for value in (token, secret)):
                _fail("请将凭据仅填写在密钥栏中", 422)
            values = tuple(form[k] for k in ("provider", "name", "address", "title_field", "body_field", "app_id", "table_id"))
            if previous:
                library_id = previous["id"]
                conn.execute('DELETE FROM online_library_results WHERE library_id=?', (library_id,))
                conn.execute('''UPDATE online_libraries SET provider=?,name=?,address=?,title_field=?,body_field=?,
                    app_id=?,table_id=?,status='configured' WHERE id=? AND project_id=?''', (*values, library_id, project))
            else:
                library_id = conn.execute('''INSERT INTO online_libraries
                    (provider,name,address,title_field,body_field,app_id,table_id,status,project_id)
                    VALUES(?,?,?,?,?,?,?,'configured',?)''', (*values, project)).lastrowid
            conn.execute("INSERT OR REPLACE INTO online_library_secrets VALUES(?,?,?)", (library_id, token, secret))
            return _public(_load(conn, project, library_id))

    def read(project, library_id, preview):
        with database() as conn:
            project_settings(conn, project)
            _schema(conn)
            row = _load(conn, project, library_id)
            cache = {entry["page_id"]: {"metadata": entry["metadata"],
                     "item": json.loads(entry["item"]), "partial": bool(entry["partial"])}
                     for entry in conn.execute(
                         "SELECT * FROM online_library_body_cache WHERE library_id=?", (library_id,))}
        return row, Remote(row, cache).read(preview)

    @app.post(prefix + "/{library_id}/check")
    def check(project: int, library_id: int):
        row, result = read(project, library_id, True)
        with database() as conn:
            project_settings(conn, project)
            current = _load(conn, project, library_id)
            if current != row:
                _fail('配置已变化，请重新检测', 409)
            conn.execute("UPDATE online_libraries SET status='ready' WHERE id=? AND project_id=?", (library_id, project))
            conn.execute('INSERT INTO online_library_results(library_id,fields) VALUES(?,?) ON CONFLICT(library_id) DO UPDATE SET fields=excluded.fields', (library_id,json.dumps(result['fields'])))
        return {"status": "ready", "fields": result["fields"], "count": len(result["items"]),
                "titles": [v["title"] for v in result["items"]], "partial": result["partial"]}

    @app.post(prefix + "/{library_id}/import")
    def import_records(project: int, library_id: int):
        row, result = read(project, library_id, False)
        with database() as conn:
            conn.execute("BEGIN IMMEDIATE")
            project_settings(conn, project)
            if _load(conn, project, library_id) != row:
                _fail("配置已变化，请重新导入", 409)
            materials, fingerprints, seen = [], [], set()
            updated = 0
            for item in result["items"][:2000]:
                source_id = item["source_id"]
                identity = hashlib.sha256(json.dumps([row["provider"], row["address"], row["table_id"], source_id], ensure_ascii=False).encode()).hexdigest()
                fingerprint = hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                old = conn.execute("SELECT fingerprint FROM online_library_fingerprints WHERE library_id=? AND source_id=?", (library_id, identity)).fetchone()
                if identity in seen or (old and old["fingerprint"] == fingerprint):
                    continue
                seen.add(identity)
                updated += int(old is not None)
                materials.append({"id": len(materials) + 1, "title": item["title"], "body": item["body"], "raw": item["raw"]})
                fingerprints.append((library_id, identity, fingerprint))
            run = None
            if materials:
                run = insert_run(conn, row["name"], {"project_id": project, "format": "online_library",
                    "filename": row["name"], "online_library_id": library_id,
                    "notion_database": "",  # Prevent insert_run's optional outbound snapshot queue.
                    "online_library_partial": result["partial"]}, materials)
                conn.executemany("INSERT OR REPLACE INTO online_library_fingerprints VALUES(?,?,?)", fingerprints)
            conn.executemany('''INSERT OR REPLACE INTO online_library_body_cache
                (library_id,page_id,metadata,item,partial) VALUES(?,?,?,?,?)''',
                [(library_id, entry["page_id"], entry["metadata"],
                  json.dumps(entry["item"], ensure_ascii=False), int(entry["partial"]))
                 for entry in result.get("cache_updates", [])])
            conn.execute("UPDATE online_libraries SET status='ready' WHERE id=? AND project_id=?", (library_id, project))
            receipt = {"status": "imported" if run else "unchanged", "run_id": run["id"] if run else None,
                    "stage": 1 if run else None, "added": len(materials),
                    "source_added": len(materials) - updated, "source_updated": updated,
                    "skipped": len(result["items"]) - len(materials), "partial": result["partial"],
                    "deferred": result.get("deferred", 0)}
            conn.execute('INSERT OR REPLACE INTO online_library_results(library_id,fields,receipt) VALUES(?,?,?)',
                         (library_id, json.dumps(result['fields']), json.dumps(receipt)))
            return receipt
