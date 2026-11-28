"""Structured readers with a bounded, explicit handoff to a browser."""
import html
import json
import re
import shutil
import subprocess
from urllib.parse import urlsplit

import httpx


class BrowserRequired(Exception):
    pass


class CollectionBlocked(Exception):
    pass


def _item(platform, title, body, url, author=''):
    patterns = {'bilibili': r'/video/(BV[A-Za-z0-9]{10}|av\d+)',
                'xiaohongshu': r'/(?:explore|discovery/item)/([a-fA-F0-9]{24})',
                'x': r'/\w+/status/(\d+)',
                'instagram': r'/(?:p|reel)/([\w-]+)'}
    hosts = {'bilibili': 'www.bilibili.com', 'xiaohongshu': 'www.xiaohongshu.com',
             'x': 'x.com', 'instagram': 'www.instagram.com'}
    try:
        parsed = urlsplit(url)
    except (ValueError, TypeError):
        return None
    host = hosts[platform]
    if parsed.scheme != 'https' or parsed.netloc not in {host, host.removeprefix('www.')}:
        return None
    match = re.fullmatch(patterns[platform] + '/?', parsed.path)
    if not match:
        return None
    clean = lambda value: html.unescape(re.sub('<[^>]+>', '', str(value or ''))).strip()
    return {'external_id': match[1], 'title': clean(title)[:500], 'body': clean(body)[:50000],
            'url': f'https://{host}{parsed.path.rstrip("/")}', 'author': clean(author)[:256]}


def read(platform, mode, query, url, limit):
    if mode == 'topic' and platform == 'bilibili':
        try:
            response = httpx.get('https://api.bilibili.com/x/web-interface/search/type',
                                 params={'search_type': 'video', 'keyword': query, 'page': 1},
                                 timeout=10, follow_redirects=False)
            if response.status_code == 429:
                raise CollectionBlocked('平台限制了读取频率，请稍后再试')
            if response.is_success and 'json' in response.headers.get('content-type', ''):
                data = response.json()
                if data.get('code') == 0:
                    rows = data.get('data', {}).get('result', [])
                    if not isinstance(rows, list):
                        raise BrowserRequired('搜索响应发生变化')
                    result = {}
                    for row in rows:
                        item = _item(platform, row.get('title'), row.get('description'),
                                     'https://www.bilibili.com/video/' + str(row.get('bvid', '')), row.get('author'))
                        if item:
                            result[item['external_id']] = item
                    # Larger tasks use browser pagination rather than silently cap at page one.
                    if len(rows) >= 20 and limit > len(result):
                        raise BrowserRequired('需要继续翻页读取作品')
                    return list(result.values())[:limit]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            pass
    # Only the documented search action is used. Unsupported account actions stay in the browser.
    channel = {'xiaohongshu': 'xiaohongshu', 'x': 'twitter', 'instagram': 'instagram'}.get(platform)
    executable = shutil.which('agent-reach')
    if mode == 'topic' and channel and executable:
        try:
            result = subprocess.run([executable, channel, 'search', query, '--limit', str(limit)],
                                    capture_output=True, text=True, timeout=30)
            if len(result.stdout) > 4_000_000:
                raise BrowserRequired('平台返回数据过大')
            data = json.loads(result.stdout)
            error = data.get('error') or {}
            if error.get('code') in ('RATE_LIMITED', 'PLATFORM_BLOCKED', 'NOT_FOUND', 'BAD_INPUT'):
                raise CollectionBlocked('平台暂停读取或请求无效，请查看平台状态后重试')
            if data.get('ok') is True and data.get('schema_version') == 1 and isinstance(data.get('items'), list):
                items = {}
                for row in data['items']:
                    item = _item(platform, row.get('title'), row.get('text'), row.get('url'), row.get('author'))
                    if item and (item['title'] or item['body']):
                        items[item['external_id']] = item
                if items or not data['items']:
                    return list(items.values())[:limit]
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
            pass
    raise BrowserRequired('需要通过浏览器读取平台页面')
