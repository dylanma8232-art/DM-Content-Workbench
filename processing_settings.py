"""Persist processing configuration without exposing saved credentials."""
import ipaddress
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit

import httpx
from dotenv import load_dotenv, set_key
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from local_ai import ASR_MODEL, VISION_MODEL

LOCAL_BASE = 'http://127.0.0.1:8770/v1'
_LOCK = threading.RLock()


def _base(value: str) -> str:
    value = value.strip().rstrip('/')
    if not value:
        return value
    try:
        url = urlsplit(value)
        host = url.hostname
        port = url.port
        if not host or url.username is not None or url.password is not None or '?' in value or '#' in value:
            raise ValueError()
        if any(c.isspace() or ord(c) < 32 for c in value) or '\\' in value:
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        loopback = host.lower() == 'localhost' or (address is not None and address.is_loopback)
        public = address.is_global if address else host.lower() != 'localhost' and '.' in host and not host.lower().endswith(('.localhost', '.local', '.internal'))
        if not ((url.scheme == 'http' and loopback) or (url.scheme == 'https' and public)):
            raise ValueError()
        if port is not None and not 1 <= port <= 65535:
            raise ValueError()
    except ValueError:
        raise ValueError('请使用本机 HTTP 地址或公网 HTTPS 地址，且不要包含账号、查询参数或片段') from None
    return value


class ScanSettings(BaseModel):
    enabled: bool
    language: str = Field(min_length=1, max_length=120, pattern=r'^[A-Za-z0-9_]+(\+[A-Za-z0-9_]+)*$')


class CustomSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    model_base: str
    model_name: str
    model_key: str | None = None
    asr_base: str = ''
    asr_name: str = ''
    asr_key: str | None = None

    @field_validator('model_base', 'asr_base')
    @classmethod
    def validate_base(cls, value):
        return _base(value)

    @field_validator('model_name', 'asr_name')
    @classmethod
    def trim_name(cls, value):
        value = value.strip()
        if any(ord(c) < 32 for c in value):
            raise ValueError('模型名称不能包含控制字符')
        return value

    @model_validator(mode='after')
    def validate_pairs(self):
        if not self.model_base or not self.model_name:
            raise ValueError('请填写模型地址和模型名称')
        if bool(self.asr_base) != bool(self.asr_name):
            raise ValueError('转写地址和模型名称必须同时填写，或同时留空以停用')
        return self


async def _custom_form(request: Request):
    # FastAPI's default validation response includes the submitted input (and keys).
    try:
        return CustomSettings.model_validate(await request.json())
    except (ValidationError, ValueError):
        raise HTTPException(422, '设置无效：请检查模型名称、地址格式和转写地址/名称配对') from None


def _status():
    return {
        'mode': 'local' if os.getenv('DM_MODEL_BASE_URL', '').rstrip('/') == LOCAL_BASE else 'custom',
        'model_base': os.getenv('DM_MODEL_BASE_URL', ''),
        'model_name': os.getenv('DM_MODEL_NAME', ''),
        'asr_base': os.getenv('DM_ASR_BASE_URL', ''),
        'asr_name': os.getenv('DM_ASR_MODEL', ''),
        'model_key_saved': bool(os.getenv('DM_MODEL_KEY')),
        'asr_key_saved': bool(os.getenv('DM_ASR_KEY')),
        'ocr_enabled': os.getenv('DM_OCR_ENABLED') == '1',
        'ocr_language': os.getenv('DM_OCR_LANG') or 'eng',
    }


def _persist(root: Path, values: dict):
    path = root / '.dm.env'
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix='.dm.env-', dir=root)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            if path.exists():
                stream.write(path.read_text(encoding='utf-8'))
            stream.flush()
        for key, value in values.items():
            set_key(temporary, key, value)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except (OSError, ValueError):
        raise HTTPException(500, '设置保存失败，请检查工作台目录的写入权限') from None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    os.environ.update(values)
    return _status()


def _detect(client):
    try:
        response = client.get('http://127.0.0.1:8770/health')
        response.raise_for_status()
    except httpx.ConnectError:
        return False
    except httpx.HTTPError:
        raise HTTPException(503, '本机 8770 端口服务不可用，请检查本地引擎日志') from None
    try:
        if response.json().get('service') != 'dm-local-ai':
            raise ValueError()
        response = client.get(LOCAL_BASE + '/models')
        response.raise_for_status()
        ids = {item['id'] for item in response.json()['data']}
        if not {VISION_MODEL, ASR_MODEL} <= ids:
            raise ValueError()
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(503, '8770 端口不是兼容的 DM 本地引擎，请停止占用服务并启动正确的本地引擎') from None
    return True


def _local(root):
    deadline = time.monotonic() + 4
    with httpx.Client(timeout=0.5, trust_env=False, follow_redirects=False) as client:
        if _detect(client):
            return
        python = root / '.venv-local-ai/bin/python'
        if not python.is_file() or not os.access(python, os.X_OK):
            raise HTTPException(503, '请先安装本地引擎环境 .venv-local-ai，再启用本地处理')
        try:
            log = root / '.dm-data/engine-service.log'
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open('ab') as output:
                subprocess.Popen(
                    [str(python), '-m', 'uvicorn', 'local_ai:app', '--host', '127.0.0.1', '--port', '8770'],
                    cwd=root, stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
                )
        except OSError:
            raise HTTPException(503, '本地引擎启动失败，请检查 .dm-data/engine-service.log 和环境权限') from None
        while time.monotonic() < deadline:
            if _detect(client):
                return
            time.sleep(0.1)
        raise HTTPException(503, '本地引擎未能及时启动，请查看 .dm-data/engine-service.log 后重试')


def mount(app: FastAPI, root: Path):
    root = Path(root)
    with _LOCK:
        load_dotenv(root / '.dm.env', override=True)

    @app.get('/api/processing-settings')
    def get_settings():
        with _LOCK:
            return _status()

    @app.post('/api/processing-settings/custom')
    def custom_settings(form: CustomSettings = Depends(_custom_form)):
        with _LOCK:
            values = {
                'DM_MODEL_BASE_URL': form.model_base, 'DM_MODEL_NAME': form.model_name,
                'DM_ASR_BASE_URL': form.asr_base, 'DM_ASR_MODEL': form.asr_name,
            }
            for prefix, value in [('DM_MODEL', form.model_key), ('DM_ASR', form.asr_key)]:
                base_key = prefix + '_BASE_URL'
                if value is not None:
                    values[prefix + '_KEY'] = value
                elif values[base_key] != os.getenv(base_key, '').rstrip('/'):
                    values[prefix + '_KEY'] = ''
            return _persist(root, values)

    @app.post('/api/processing-settings/ocr')
    def scan_settings(form: ScanSettings):
        with _LOCK:
            return _persist(root, {'DM_OCR_ENABLED': '1' if form.enabled else '0', 'DM_OCR_LANG': form.language})

    @app.post('/api/processing-settings/local')
    def local_settings():
        with _LOCK:
            _local(root)
            return _persist(root, {
                'DM_MODEL_BASE_URL': LOCAL_BASE, 'DM_MODEL_NAME': VISION_MODEL,
                'DM_ASR_BASE_URL': LOCAL_BASE, 'DM_ASR_MODEL': ASR_MODEL,
                'DM_MODEL_KEY': '', 'DM_ASR_KEY': '',
            })
