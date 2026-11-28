"""Machine-wide service credentials; HTTP responses contain metadata only."""
import json
import os
from pathlib import Path
import re

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request

import processing_settings

_MAX_BODY = 16 * 1024
_MAX_TOKEN = 8192
_MAX_ALIAS = 100
_ALIAS = re.compile(r'DM_API_TOKEN_[A-Z0-9_]+', re.ASCII)
_INVALID = '凭据格式无效，请检查名称和密钥后重试'


def _status():
    return {
        'notion_saved': bool(os.getenv('DM_NOTION_TOKEN')),
        'api_aliases': sorted(name for name, value in os.environ.items()
                              if value and len(name) <= _MAX_ALIAS and _ALIAS.fullmatch(name)),
    }


async def _form(request: Request, *, api: bool):
    # Parse manually: FastAPI validation errors can include submitted secrets.
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > _MAX_BODY:
            raise HTTPException(413, '凭据内容过长，请缩短后重试')
        body.extend(chunk)
    try:
        form = json.loads(body)
        allowed = {'alias', 'token'} if api else {'token'}
        if not isinstance(form, dict) or form.keys() - allowed:
            raise ValueError()
        token = form.get('token', '')
        if not isinstance(token, str) or len(token) > _MAX_TOKEN:
            raise ValueError()
        if any(ord(char) < 32 or ord(char) == 127 for char in token):
            raise ValueError()
        alias = form.get('alias') if api else 'DM_NOTION_TOKEN'
        if api and (not isinstance(alias, str) or len(alias) > _MAX_ALIAS or not _ALIAS.fullmatch(alias)):
            raise ValueError()
        return alias, token.strip()
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(422, _INVALID) from None


def mount(app: FastAPI, root: Path):
    """Mount settings shared by every project on this workbench machine."""
    root = Path(root)
    with processing_settings._LOCK:
        load_dotenv(root / '.dm.env', override=True)

    def save(alias, token):
        with processing_settings._LOCK:
            if token:
                try:
                    # _persist returns model settings; never forward that response.
                    processing_settings._persist(root, {alias: token})
                except (HTTPException, OSError, ValueError):
                    raise HTTPException(500, '凭据保存失败，请检查工作台目录的写入权限后重试') from None
            return _status()

    @app.get('/api/service-credentials')
    def get_credentials():
        with processing_settings._LOCK:
            return _status()

    @app.post('/api/service-credentials/notion')
    async def notion_credentials(request: Request):
        return save(*await _form(request, api=False))

    @app.post('/api/service-credentials/api')
    async def api_credentials(request: Request):
        return save(*await _form(request, api=True))
