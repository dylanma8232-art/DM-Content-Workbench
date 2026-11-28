"""OpenAI-compatible local vision/text and Whisper service for Apple Silicon.

Run in .venv-local-ai; one worker and one inference lock keep model memory bounded.
"""
import base64
import gc
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

VISION_MODEL = os.getenv('DM_LOCAL_VISION_MODEL', 'mlx-community/Qwen3-VL-2B-Instruct-4bit')
ASR_MODEL = os.getenv('DM_LOCAL_ASR_MODEL', 'mlx-community/whisper-small-mlx')
app = FastAPI(title='DM 本地内容引擎')
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', 'testserver'])
lock = threading.Lock()
vision = None
last_error = ''


@app.middleware('http')
async def local_only(request: Request, call_next):
    origin = request.headers.get('origin')
    if origin and origin not in ('http://127.0.0.1:8770', 'http://localhost:8770'):
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse('仅供本机工作台调用', status_code=403)
    if request.headers.get('content-length', '').isdigit() and int(request.headers['content-length']) > 32_000_000:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse('请求过大', status_code=413)
    return await call_next(request)


class Chat(BaseModel):
    model: str
    messages: list[dict] = Field(min_length=1, max_length=40)
    max_tokens: int = Field(default=800, ge=1, le=4000)
    temperature: float = Field(default=0, ge=0, le=2)
    response_format: dict | None = None
    stream: bool = False


def evidence_schema(messages):
    """A local schema constrains references to actual input evidence IDs."""
    for message in reversed(messages):
        if message.get('role') != 'user' or not isinstance(message.get('content'), str):
            continue
        try:
            data = json.loads(message['content'])
        except ValueError:
            continue
        if not isinstance(data, dict) or not isinstance(data.get('evidence'), dict):
            continue
        refs = list(data['evidence'])
        if not refs:
            break
        text = {'type': 'string', 'minLength': 1, 'maxLength': 180}
        strings = {'type': 'array', 'items': {'type': 'string', 'minLength': 1, 'maxLength': 80}, 'maxItems': 5}
        properties = {
            'summary': text, 'topics': strings,
            'claims': {'type': 'array', 'minItems': 1, 'maxItems': 3, 'items': {
                'type': 'object', 'additionalProperties': False, 'required': ['text', 'evidence_ids'],
                'properties': {'text': text, 'evidence_ids': {'type': 'array', 'minItems': 1, 'maxItems': 6,
                                                          'items': {'type': 'string', 'enum': refs}}}}},
            'steps': strings, 'relevance': {'type': 'object', 'additionalProperties': False,
                'required': ['matches', 'reason'], 'properties': {'matches': {'type': 'boolean'}, 'reason': text}},
            'missing': strings,
        }
        return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}
    return {'type': 'object'}


def prepare_messages(messages):
    from PIL import Image
    cleaned, images = [], []
    text_size = 0
    for message in messages:
        role = message.get('role')
        if role not in ('system', 'user', 'assistant'):
            raise HTTPException(422, '只支持文字与图片消息')
        content = message.get('content', '')
        if isinstance(content, list):
            texts = []
            for part in content:
                if not isinstance(part, dict):
                    raise HTTPException(422, '消息格式无效')
                if part.get('type') == 'text':
                    texts.append(str(part.get('text', '')))
                elif part.get('type') == 'image_url':
                    address = part.get('image_url', {}).get('url', '')
                    # The workbench supplies downloaded image bytes. Never fetch arbitrary URLs.
                    if not isinstance(address, str) or not address.startswith('data:image/') or ';base64,' not in address:
                        raise HTTPException(422, '图片必须由工作台作为图片数据提交')
                    try:
                        raw = base64.b64decode(address.split(',', 1)[1], validate=True)
                        if len(raw) > 8_000_000 or len(images) >= 4:
                            raise ValueError('limit')
                        image = Image.open(io.BytesIO(raw))
                        if image.width * image.height > 32_000_000:
                            raise ValueError('pixels')
                        image = image.convert('RGB')
                        image.thumbnail((896, 896))
                        images.append(image)
                    except Exception as exc:
                        raise HTTPException(422, '图片格式或大小无效') from exc
                else:
                    raise HTTPException(422, '不支持此消息类型')
            content = '\n'.join(texts)
        if not isinstance(content, str):
            raise HTTPException(422, '消息需要文字')
        text_size += len(content)
        cleaned.append({'role': role, 'content': content})
    if text_size > 90000:
        raise HTTPException(422, '请分批解析长内容')
    return cleaned, images


def unload_vision():
    global vision
    vision = None
    gc.collect()
    import mlx.core as mx
    mx.clear_cache()


def complete(form):
    global vision
    from mlx_vlm import apply_chat_template, generate, load
    from huggingface_hub import snapshot_download
    import local_asr
    messages, images = prepare_messages(form.messages)
    if form.response_format:
        messages.insert(0, {'role': 'system', 'content': '输出精简 JSON：摘要 100 字以内，最多 3 条核心观点，每条 80 字以内。不要大段复制证据。步骤最多 5 条，无步骤就空数组。'})
    local_asr.unload()
    if vision is None:
        path = snapshot_download(VISION_MODEL, local_files_only=True)
        vision = load(path)
    model, processor = vision
    prompt = apply_chat_template(processor, model.config, messages, num_images=len(images), enable_thinking=False)
    options = dict(max_tokens=form.max_tokens, temperature=form.temperature, verbose=False)
    if form.response_format and form.response_format.get('type') in ('json_object', 'json_schema'):
        from mlx_vlm.structured import build_json_schema_logits_processor
        schema = form.response_format.get('json_schema', {}).get('schema') or evidence_schema(form.messages)
        options['logits_processors'] = [build_json_schema_logits_processor(processor.tokenizer, schema)]
    result = generate(model, processor, prompt, image=images or None, **options)
    text = result.text.strip()
    if not text:
        raise RuntimeError('模型未返回文字')
    return text


@app.get('/health')
def health():
    return {'ok': True, 'service': 'dm-local-ai', 'vision_model': VISION_MODEL, 'asr_model': ASR_MODEL,
            'busy': lock.locked(), 'vision_loaded': vision is not None, 'last_error': last_error}


@app.get('/v1/models')
def models():
    return {'object': 'list', 'data': [{'id': model, 'object': 'model', 'owned_by': 'local'} for model in (VISION_MODEL, ASR_MODEL)]}


@app.post('/v1/chat/completions')
def chat(form: Chat):
    global last_error
    if form.model != VISION_MODEL:
        raise HTTPException(404, '本地视觉模型名称不匹配')
    if form.stream:
        raise HTTPException(422, '请使用非流式请求')
    if not lock.acquire(timeout=5):
        raise HTTPException(429, '本地模型正在处理其他内容，请稍后重试')
    try:
        text = complete(form)
        last_error = ''
        return {'id': 'local-' + uuid.uuid4().hex, 'object': 'chat.completion', 'created': int(time.time()),
                'model': VISION_MODEL, 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': text}, 'finish_reason': 'stop'}]}
    except HTTPException:
        raise
    except Exception as exc:
        last_error = type(exc).__name__
        import logging
        logging.getLogger(__name__).exception('Local vision request failed')
        raise HTTPException(503, '本地模型处理失败，原始素材已保留') from exc
    finally:
        lock.release()


@app.post('/v1/audio/transcriptions')
def transcriptions(file: UploadFile = File(...), model: str = Form(...)):
    global last_error
    if model != ASR_MODEL:
        raise HTTPException(404, '本地语音模型名称不匹配')
    content = file.file.read(25_000_001)
    if not content or len(content) > 25_000_000:
        raise HTTPException(413, '音频为空或超过 25 MB')
    if not lock.acquire(timeout=5):
        raise HTTPException(429, '本地模型正在处理其他内容，请稍后重试')
    try:
        unload_vision()
        import local_asr
        with tempfile.TemporaryDirectory() as folder:
            suffix = Path(file.filename or 'audio.mp3').suffix.lower()
            if suffix not in ('.mp3', '.wav', '.m4a', '.mp4', '.aiff', '.flac', '.ogg', '.webm'):
                suffix = '.mp3'
            path = Path(folder) / ('audio' + suffix)
            path.write_bytes(content)
            result = local_asr.transcribe(str(path), ASR_MODEL)
        last_error = ''
        return result
    except Exception as exc:
        last_error = type(exc).__name__
        import logging
        logging.getLogger(__name__).exception('Local transcription failed')
        raise HTTPException(503, '本地音频转写失败，原始素材已保留') from exc
    finally:
        lock.release()
