import io
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx
from fastapi import HTTPException
from pypdf import PdfReader
from pypdf.errors import PdfReadError

MEDIA = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".mp4", ".mov", ".mkv", ".webm"}
MEDIA_FORMATS = {
    ".m4a": "mov",
    ".mp4": "mov",
    ".mov": "mov",
    ".mkv": "matroska",
    ".webm": "matroska",
}


def scan_pdf(content, pages):
    renderer, recognizer = shutil.which("pdftoppm"), shutil.which("tesseract")
    if not renderer or not recognizer:
        raise HTTPException(503, "扫描 PDF 需要安装 Poppler 与 Tesseract")
    if len(pages) > 20:
        raise HTTPException(422, "单次最多识别 20 张扫描页，请分段导入")
    language = os.getenv("DM_OCR_LANG", "eng")
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "source.pdf"
        target = Path(folder) / "page"
        source.write_bytes(content)
        for row in pages:
            page = str(row["page"])
            try:
                subprocess.run(
                    [
                        renderer,
                        "-f",
                        page,
                        "-l",
                        page,
                        "-scale-to",
                        "2200",
                        "-singlefile",
                        "-png",
                        str(source),
                        str(target),
                    ],
                    check=True,
                    timeout=15,
                    capture_output=True,
                )
                result = subprocess.run(
                    [recognizer, str(target) + ".png", "stdout", "-l", language],
                    check=True,
                    timeout=20,
                    capture_output=True,
                    encoding="utf-8",
                )
            except (subprocess.SubprocessError, OSError, UnicodeError) as exc:
                raise HTTPException(
                    422, "OCR 未完成，请检查扫描件和已安装的 OCR 语言"
                ) from exc
            row["body"] = result.stdout.strip()
            row["extraction"] = "ocr"


def pdf_rows(content):
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted or len(reader.pages) > 200:
            raise HTTPException(422, "PDF 必须未加密且不超过 200 页")
        rows = [
            {"body": page.extract_text().strip(), "page": index}
            for index, page in enumerate(reader.pages, 1)
        ]
    except (PdfReadError, ValueError, OSError) as exc:
        raise HTTPException(422, "PDF 无法解析") from exc
    unrecognized = [row for row in rows if not row["body"]]
    if unrecognized and os.getenv("DM_OCR_ENABLED") == "1":
        scan_pdf(content, unrecognized)
    return rows


def transcript(content, suffix, with_segments=False):
    base = os.getenv("DM_ASR_BASE_URL", "").rstrip("/")
    model = os.getenv("DM_ASR_MODEL", "")
    if not base or not model:
        raise HTTPException(503, "请先配置转写服务 DM_ASR_BASE_URL 和 DM_ASR_MODEL")
    executable = shutil.which("ffmpeg")
    if not executable:
        raise HTTPException(503, "音视频转写需要安装 FFmpeg")
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / ("source" + suffix)
        audio = Path(folder) / "audio.mp3"
        source.write_bytes(content)
        try:
            subprocess.run(
                [
                    executable,
                    "-nostdin",
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-f",
                    MEDIA_FORMATS.get(suffix, suffix[1:]),
                    "-i",
                    str(source),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-b:a",
                    "48k",
                    "-fs",
                    "24000001",
                    str(audio),
                ],
                check=True,
                timeout=90,
                capture_output=True,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            raise HTTPException(422, "无法提取音轨，请检查文件格式与音轨") from exc
        if audio.stat().st_size > 24_000_000:
            raise HTTPException(422, "提取后的音轨超过 24 MB，请分段导入")
        token = os.getenv("DM_ASR_KEY")
        try:
            with audio.open("rb") as stream:
                response = httpx.post(
                    base + "/audio/transcriptions",
                    files={"file": ("audio.mp3", stream, "audio/mpeg")},
                    data={"model": model},
                    headers={"Authorization": "Bearer " + token} if token else {},
                    timeout=120,
                )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data.get('text'), str) or not data['text'].strip():
                raise ValueError('转写没有返回有效文字')
            if with_segments:
                segments = []
                for row in data.get('segments', [])[:2000]:
                    start, end = float(row['start']), float(row['end'])
                    if 0 <= start < end and isinstance(row.get('text'), str) and row['text'].strip():
                        segments.append({'start': start, 'end': end, 'text': row['text'][:5000]})
                return {'text': data['text'], 'segments': segments, 'language': str(data.get('language') or '')[:40]}
            return data['text']
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise HTTPException(502, "转写服务未返回可用文字") from exc
