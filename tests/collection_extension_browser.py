"""Load the downloadable extension in Edge against a local simulated X account."""
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from zipfile import ZipFile

import httpx
from playwright.sync_api import sync_playwright


def exercise(client, folder):
    extension = folder / 'extension'
    archive = client.get('/api/publishing/extension.zip')
    archive.raise_for_status()
    with ZipFile(io.BytesIO(archive.content)) as bundle:
        bundle.extractall(extension)
    manifest = extension / 'manifest.json'
    config = json.loads(manifest.read_text())
    # Pregrant this fixture site in the temporary profile for headless operation.
    config['optional_host_permissions'].remove('https://x.com/*')
    config['host_permissions'].append('https://x.com/*')
    manifest.write_text(json.dumps(config))

    base = '/api/projects/1/collection-tasks'
    task = client.post(base, json=dict(platform='x', mode='account', query='source_author',
                                       limit=1, collector_label='editor')).raise_for_status().json()
    path = f'{base}/{task["id"]}'
    client.post(path + '/run').raise_for_status()
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(folder / 'profile', channel='msedge',
            args=[f'--disable-extensions-except={extension}', f'--load-extension={extension}'])
        worker = (context.service_workers or [context.wait_for_event('serviceworker')])[0]
        article = '<article data-testid="tweet"><a href="/source_author/status/123"><time>today</time></a><p data-testid="tweetText">Fixture content</p></article>'
        authenticated = False

        def platform(route):
            if route.request.url.endswith('/status/123') and not authenticated:
                html = '<div role="dialog">请先登录</div>'
            else:
                html = '<main data-testid="primaryColumn">' + article + '</main>'
            route.fulfill(content_type='text/html;charset=utf-8', body=html)

        context.route('https://x.com/**', platform)
        page = context.pages[0]
        page.goto(str(client.base_url))
        page.wait_for_function("document.documentElement.dataset.dmCollectExtension === '1'")
        # A real user gesture reaches the workbench content script and service worker.
        page.evaluate('''source => document.body.addEventListener('click', () => window.postMessage(
          {type:'dm-collect-open',project:1,source},location.origin), {once:true})''', task['id'])
        with context.expect_page() as opened:
            page.mouse.click(2, 2)
        target = opened.value
        target.wait_for_url(task['url'])
        run = '''async () => {
          const [tab] = await chrome.tabs.query({active:true,lastFocusedWindow:true});
          const job = (await chrome.storage.session.get(String(tab.id)))[tab.id];
          const {collectTab} = await import(chrome.runtime.getURL('collection-popup.mjs'));
          return collectTab(tab.id,{...job,deepPermission:true},()=>{},true);
        }'''
        extension_page = context.new_page()
        extension_page.goto(worker.url.replace('background.js', 'popup.html'))
        target.bring_to_front()
        try:
            extension_page.evaluate(run)
        except Exception as exc:
            assert '登录' in str(exc), exc
        else:
            raise AssertionError('Login wall submitted a result')
        pending = client.get(path).json()
        assert pending['status'] == 'browser' and not pending['result'], pending
        authenticated = True
        target.goto(task['url'])
        extension_page.evaluate(run)
        result = client.get(path).json()
        assert result['attempt_token'] == pending['attempt_token']
        assert result['status'] == 'completed' and result['result']['imported'] == 1, result
        assert result['result']['depth_counts']['detail_text'] == 1, result
        context.close()


def main():
    with tempfile.TemporaryDirectory() as temp, socket.socket() as sock:
        folder = Path(temp)
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
        sock.close()
        server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:app', '--port', str(port), '--log-level', 'warning'],
            cwd=Path(__file__).resolve().parents[1], env=dict(os.environ, DM_DATA_DIR=str(folder/'data'), DM_MODEL_BASE_URL='', DM_MODEL_NAME=''))
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}') as client:
                for attempt in range(60):
                    try:
                        client.get('/api/projects').raise_for_status()
                        break
                    except httpx.ConnectError:
                        if server.poll() is not None or attempt == 59:
                            raise
                        time.sleep(.1)
                exercise(client, folder)
        finally:
            server.terminate()
            server.wait(timeout=10)


if __name__ == '__main__':
    main()
