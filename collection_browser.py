"""Optional public browser fallback; signed-in sessions stay in the companion."""
from pathlib import Path
import time
from urllib.parse import parse_qs, urlsplit

from collection_reader import BrowserRequired


def read(platform, mode, query, url, limit):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserRequired('需要在登录后的浏览器中继续采集') from exc
    scraper = Path(__file__).parent / 'tools/publish-extension/collect-page.mjs'
    if not scraper.is_file():
        raise BrowserRequired('浏览器采集组件尚未就绪')
    source = scraper.read_text(encoding='utf-8').replace('export function collectPage', 'function collectPage', 1)
    script = 'args => { ' + source + '; return collectPage(...args); }'
    task = dict(platform=platform, mode=mode, query=query, url=url, limit=limit)
    items = {}
    deadline = time.monotonic() + 38
    try:
        with sync_playwright() as api:
            try:
                browser = api.chromium.launch(channel='chrome', headless=True)
            except Exception:
                browser = api.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until='domcontentloaded', timeout=16000)
                idle = 0
                for _ in range(10):
                    if time.monotonic() >= deadline or len(items) >= limit:
                        break
                    current = page.url
                    # Keep the current browser page bound to the configured search or account.
                    original, actual = urlsplit(url), urlsplit(current)
                    if original.netloc != actual.netloc:
                        raise BrowserRequired('平台要求登录，请在浏览器继续')
                    if platform == 'bilibili' and mode == 'topic':
                        if actual.path not in ('/all', '/video') or parse_qs(actual.query).get('keyword') != parse_qs(original.query).get('keyword'):
                            raise BrowserRequired('平台页面已跳转，请在浏览器继续')
                    elif actual.path.rstrip('/') != original.path.rstrip('/') or actual.query != original.query:
                        raise BrowserRequired('平台页面已跳转，请在浏览器继续')
                    result = page.evaluate(script, [task, current, list(items), False])
                    previous = len(items)
                    for item in result.get('items', []):
                        items[item['external_id']] = item
                    idle = idle + 1 if previous == len(items) else 0
                    if len(items) >= limit:
                        break
                    if result.get('next') and platform == 'bilibili' and mode == 'topic':
                        following = urlsplit(result['next'])
                        if (following.scheme != 'https' or following.netloc != original.netloc
                                or following.path not in ('/all', '/video')
                                or parse_qs(following.query).get('keyword') != parse_qs(original.query).get('keyword')):
                            break
                        page.goto(result['next'], wait_until='domcontentloaded', timeout=10000)
                    elif idle >= 3:
                        break
                    else:
                        page.wait_for_timeout(850)
            finally:
                browser.close()
    except BrowserRequired:
        raise
    except Exception as exc:
        if not items:
            raise BrowserRequired('公开页面未能读取，请在浏览器继续采集') from exc
    if not items:
        raise BrowserRequired('公开页面没有可读作品，请登录后继续采集')
    return list(items.values())[:limit]


def detail(platform, item):
    """Open the individual work in a fresh public browser, never an unrelated feed."""
    from playwright.sync_api import sync_playwright
    path = Path(__file__).parent / 'tools/publish-extension/collect-detail.mjs'
    script = path.read_text(encoding='utf-8').replace('export function collectDetail', 'function collectDetail', 1)
    with sync_playwright() as api:
        try:
            browser = api.chromium.launch(channel='chrome', headless=True)
        except Exception:
            browser = api.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(item.get('access_url') or item['url'], wait_until='domcontentloaded', timeout=12000)
            page.wait_for_timeout(900)
            evaluate = 'args => { ' + script + '; return collectDetail(...args); }'
            page.evaluate(evaluate, [{'platform': platform}, item])
            page.wait_for_timeout(350)
            return page.evaluate(evaluate, [{'platform': platform}, item])
        finally:
            browser.close()
