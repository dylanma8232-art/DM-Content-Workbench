"""Read each discovered work; preserve shallow evidence when detail is inaccessible."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit

import httpx

LEVELS = {'title_only': 0, 'summary': 1, 'visible_text': 1, 'detail_text': 2, 'subtitle': 3, 'transcript': 3}
HOSTS = {'bilibili': {'www.bilibili.com'}, 'instagram': {'www.instagram.com'},
         'tiktok': {'www.tiktok.com'}, 'douyin': {'www.douyin.com'},
         'xiaohongshu': {'www.xiaohongshu.com'}, 'x': {'x.com'}}


def public_json(url, params=None):
    import connections
    connections.public_endpoint(url)
    with httpx.stream('GET', url, params=params, timeout=8, follow_redirects=False) as response:
        response.raise_for_status()
        parts, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > 2_000_000:
                raise ValueError('详情响应过大')
            parts.append(chunk)
    return json.loads(b''.join(parts))


def bili(item):
    key = 'bvid' if item['external_id'].startswith('BV') else 'aid'
    value = item['external_id'] if key == 'bvid' else item['external_id'][2:]
    data = public_json('https://api.bilibili.com/x/web-interface/view', {key: value})
    if data.get('code') != 0 or not isinstance(data.get('data'), dict):
        raise ValueError('平台未返回作品详情')
    data = data['data']
    if str(data.get(key)) != str(value):
        raise ValueError('详情与目标作品不一致')
    item.update(title=(data.get('title') or item['title'])[:500], author=(data.get('owner', {}).get('name') or item.get('author', ''))[:256], media_kind='video')
    description = data.get('desc')
    if isinstance(description, str) and description.strip():
        item.update(body=description[:50000], description_text=description[:50000], content_level='detail_text', depth_status='detail_read',
                    detail_error='', detail_source='bilibili_view')
    segments = []
    # Keep the first part explicit: multipart videos must not be represented as a full transcript.
    pages = data.get('pages') or []
    item['media_parts'] = len(pages) or 1
    cid = data.get('cid')
    if cid:
        try:
            player = public_json('https://api.bilibili.com/x/player/v2', {'aid': data['aid'], 'cid': cid})
            tracks = player.get('data', {}).get('subtitle', {}).get('subtitles', [])
            tracks = sorted(tracks, key=lambda t: not str(t.get('lan', '')).startswith('zh'))
            if tracks:
                track = tracks[0]
                address = track.get('subtitle_url', '')
                if address.startswith('//'):
                    address = 'https:' + address
                if urlsplit(address).scheme != 'https':
                    raise ValueError('字幕地址无效')
                captions = public_json(address)
                for row in captions.get('body', []):
                    start, end = float(row['from']), float(row['to'])
                    text = str(row.get('content') or '').strip()
                    if text and 0 <= start < end:
                        segments.append({'start': start, 'end': end, 'text': text})
                if segments:
                    item.update(body='\n'.join(s['text'] for s in segments)[:50000], segments=segments[:2000],
                                content_level='subtitle', depth_status='subtitle_read',
                                detail_error='', language=track.get('lan', ''), media_part=1,
                                detail_source='bilibili_subtitle')
        except Exception:
            pass
    return item


def _vtt(text):
    pattern = re.compile(r'(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})[^\n]*\n(.*?)(?=\n\s*\n|\Z)', re.S)
    segments = []
    for m in pattern.finditer(text):
        values = m.groups()
        stamp = lambda p: int(p[0] or 0) * 3600 + int(p[1]) * 60 + int(p[2]) + int(p[3]) / 1000
        body = re.sub('<[^>]+>', '', values[8]).strip()
        start, end = stamp(values[:4]), stamp(values[4:8])
        if body and end > start:
            segments.append({'start': start, 'end': end, 'text': body})
    return segments


def media_text(item, deadline):
    sibling = Path(sys.executable).with_name('yt-dlp.exe' if os.name == 'nt' else 'yt-dlp')
    executable = str(sibling) if sibling.is_file() else shutil.which('yt-dlp')
    if not executable or time.monotonic() >= deadline:
        return item
    # Native subtitles first; only use configured ASR when no usable subtitle was returned.
    try:
        result = subprocess.run([executable, '--ignore-config', '--dump-single-json', '--skip-download', '--no-playlist',
                                 '--socket-timeout', '6', '--retries', '0', '--', item.get('access_url') or item['url']],
                                capture_output=True, text=True, timeout=min(20, max(1, deadline-time.monotonic())))
        if result.returncode or len(result.stdout) > 4_000_000:
            return item
        info = json.loads(result.stdout)
        if str(info.get('id')) != item['external_id']:
            return item
        tracks = {**(info.get('automatic_captions') or {}), **(info.get('subtitles') or {})}
        for language in sorted(tracks, key=lambda value: (not value.startswith('zh'), not value.startswith('en'))):
            for track in tracks[language]:
                if track.get('ext') != 'vtt':
                    continue
                import connections
                connections.public_endpoint(track['url'])
                with httpx.stream('GET', track['url'], timeout=8, follow_redirects=False) as response:
                    response.raise_for_status()
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 2_000_000:
                            raise ValueError('字幕过大')
                segments = _vtt(raw.decode('utf-8'))
                if segments:
                    item['description_text'] = item.get('description_text') or item.get('body', '')
                    item.update(body='\n'.join(s['text'] for s in segments)[:50000], segments=segments[:2000],
                                content_level='subtitle', depth_status='subtitle_read', language=language,
                                detail_source='yt_dlp_subtitle', detail_error='')
                    return item
        if not os.getenv('DM_ASR_BASE_URL') or not os.getenv('DM_ASR_MODEL') or time.monotonic() >= deadline:
            return item
        with tempfile.TemporaryDirectory() as folder:
            template = str(Path(folder) / 'audio.%(ext)s')
            command = [executable, '--ignore-config', '--no-playlist', '--socket-timeout', '6', '--retries', '0',
                       '--max-filesize', '24M', '--hls-prefer-native', '--format', 'worstaudio/worst',
                       '--output', template, '--', item.get('access_url') or item['url']]
            with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as process:
                download_deadline = min(deadline, time.monotonic() + 45)
                while process.poll() is None:
                    size = sum(p.stat().st_size for p in Path(folder).glob('audio.*') if p.is_file())
                    if size > 24_000_000 or time.monotonic() >= download_deadline:
                        process.kill()
                        process.wait()
                        return item
                    try:
                        process.wait(timeout=0.2)
                    except subprocess.TimeoutExpired:
                        pass
                if process.returncode:
                    return item
            files = list(Path(folder).glob('audio.*'))
            if len(files) != 1 or not 0 < files[0].stat().st_size <= 24_000_000:
                return item
            import extraction
            if files[0].suffix not in extraction.MEDIA:
                return item
            transcription = extraction.transcript(files[0].read_bytes(), files[0].suffix, with_segments=True)
            text = transcription['text']
            if isinstance(text, str) and text.strip():
                item['description_text'] = item.get('description_text') or item.get('body', '')
                item.update(body=text[:50000], content_level='transcript', depth_status='transcribed',
                            detail_source='configured_asr', detail_error='', segments=transcription['segments'], language=transcription['language'])
    except Exception:
        pass
    return item


def enrich(platform, rows, browser=True):
    result = []
    deadline = time.monotonic() + 90
    for row in rows:
        item = dict(row)
        item.setdefault('content_level', 'summary' if item.get('body') else 'title_only')
        item.setdefault('depth_status', 'detail_unavailable')
        item.setdefault('detail_error', '详情未能读取，保留现有文字')
        parsed = urlsplit(item['url'])
        if parsed.scheme != 'https' or parsed.netloc not in HOSTS[platform]:
            raise ValueError('详情链接不属于任务平台')
        if time.monotonic() >= deadline:
            item['detail_error'] = '本轮详情读取预算已用完，再次采集可重试'
            result.append(item)
            continue
        if platform == 'bilibili':
            try:
                item = bili(item)
            except Exception:
                pass
        if LEVELS.get(item['content_level'], 0) < 2 and browser:
            try:
                from collection_browser import detail
                item = detail(platform, item)
            except Exception:
                pass
        if LEVELS.get(item['content_level'], 0) < 3 and item.get('media_kind') not in ('image', 'text'):
            item = media_text(item, deadline)
        if LEVELS.get(item['content_level'], 0) >= 2:
            item['detail_error'] = ''
        item['content_truncated'] = len(item.get('body', '')) >= 50000 or len(item.get('segments', [])) >= 2000
        if item['content_truncated']:
            item['detail_error'] = '内容达到保存上限，当前是已截取的文本或字幕'
        result.append(item)
    return result
