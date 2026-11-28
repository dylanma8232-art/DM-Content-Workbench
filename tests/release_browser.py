"""Smoke-test the installed archive using an isolated workspace and Chrome."""

import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from zipfile import ZipFile

import httpx
from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import build_release


def restore_old_workspace(root):
    old = root / 'legacy'
    old.mkdir()
    material = {'id': 1, 'title': '旧库素材', 'body': '旧库原文'}
    draft = {'materials': [material], 'config': {'filename': 'old.txt'},
             'cleaned': [{**material, 'reason': '保留'}], 'insights': [{'id': 1, 'quote': '旧库原文'}],
             'topic': {'title': '旧选题', 'ids': [1]}, 'drafts': ['归档旧稿第一版', '归档旧稿第二版']}
    with closing(sqlite3.connect(old / 'workbench.sqlite3')) as conn, conn:
        conn.execute('CREATE TABLE runs(id INTEGER PRIMARY KEY, name TEXT NOT NULL, stage INTEGER NOT NULL, payload TEXT NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP)')
        conn.execute('INSERT INTO runs(id,name,stage,payload) VALUES(17,?,5,?)', ('旧库批次', json.dumps(draft)))
    backup = root / 'snapshot.sqlite3'
    command = [sys.executable, str(root / 'install/DM-Content-Workbench/backup.py')]
    for args in [('export', backup, '--directory', old), ('verify', backup), ('restore', backup, '--directory', root / 'data')]:
        subprocess.run([*command, *args], check=True, env=dict(os.environ, DM_DATA_DIR=str(old)))


def check_pages(url, root):
    with sync_playwright() as runner:
        browser = runner.chromium.launch(channel='chrome')
        page = browser.new_page()
        loaded = set()
        page.on('response', lambda response: loaded.add(response.url.split('?')[0]) if response.status == 200 else None)
        scripts = []
        page.route('**/library.js?*', lambda route: scripts.append(route))
        with page.expect_request('**/library.js?*'):
            page.goto(url + '/?project=1&view=drafts', wait_until='commit')
        page.wait_for_selector('#draftsNav', state='attached')
        scripts.pop().continue_()
        expect(page.locator('#library')).to_be_visible()
        expect(page.locator('#draftsNav')).to_have_attribute('aria-current', 'page')
        page.unroute('**/library.js?*')
        for width in (320, 390, 1440):
            page.set_viewport_size({'width': width, 'height': 850})
            page.goto(url)
            trigger = page.locator('#dmCmdTrigger')
            trigger.click()
            search = page.locator('.cmdHead input')
            search.fill('素材库')
            search.dispatch_event('keydown', {'key': 'Enter', 'isComposing': True})
            expect(search).to_be_visible()
            search.press('Escape')
            expect(trigger).to_be_focused()
            start = time.perf_counter()
            trigger.click()
            search.fill('素材库')
            search.press('Enter')
            expect(page.locator('#library')).to_be_visible()
            expect(page.locator('#library')).to_contain_text('旧库素材')
            print(f'{width}px 总览→快搜→旧素材：1 次点击、输入并回车，{(time.perf_counter()-start)*1000:.0f} ms')
            for asset in ('workbench-next.css','workbench-ux.js','brand/chestnut.webp'):
                assert url + '/' + asset in loaded, asset
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.set_viewport_size({'width': 320, 'height': 780})
        page.emulate_media(reduced_motion='reduce')
        start = time.perf_counter()
        trigger.click()
        search.fill('填写账号日报')
        search.press('Enter')
        form = page.locator('#reportForm')
        expect(form).to_be_visible()
        form.locator('[data-platform=bilibili]').click()
        for field, value in dict(account='手机验收', followers=0, views=0, engagements=0, published=0).items():
            form.locator(f'[name={field}]').fill(str(value))
        form.get_by_role('button', name='保存日报').click()
        expect(form).to_be_hidden()
        expect(page.locator('#accountRows')).to_contain_text('手机验收')
        expect(page.locator('#notice')).to_contain_text('账号日报已保存')
        print(f'320px 素材库→快搜→填写并保存日报：3 次点击，{(time.perf_counter()-start)*1000:.0f} ms（自动填表）')
        assert page.locator('.brand img').evaluate('img => img.complete && img.naturalWidth > 0')
        assert page.locator('#accountMetrics').evaluate("el => getComputedStyle(el, '::before').animationName") == 'none'
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.set_viewport_size({'width': 1440, 'height': 900})
        page.locator('#channelNav').click()
        page.locator('button[role=tab][data-config=sourcesCard]').click()
        source = page.locator('#source')
        source.locator('[name=name]').fill('来源试采')
        source.locator('[name=address]').fill('https://example.org/one')
        requests = []
        page.route('**/api/sources/preview', lambda route: requests.append(route))
        with page.expect_request('**/api/sources/preview'):
            page.locator('#previewSource').click()
        source.locator('[name=address]').fill('https://example.org/two')
        requests.pop().fulfill(json={'count': 1, 'sample': [{'title': '旧地址', 'body': '旧内容'}]})
        expect(page.locator('#previewSource')).to_be_enabled()
        expect(page.locator('#sourcePreview')).to_be_empty()
        page.unroute('**/api/sources/preview')
        page.route('**/api/sources/preview', lambda route: route.fulfill(json={'count': 1, 'sample': [{'title': '新地址', 'body': '新内容'}]}))
        page.locator('#previewSource').click()
        expect(page.locator('#sourcePreview')).to_contain_text('新内容')
        source.locator('[name=address]').fill('https://example.org/three')
        expect(page.locator('#sourcePreview')).to_be_empty()
        page.unroute('**/api/sources/preview')
        page.route('**/api/sources/preview', lambda route: route.fulfill(status=422, json={'detail':'请输入公开 HTTPS 来源地址'}))
        page.locator('#previewSource').click()
        expect(source.locator('[name=address]')).to_be_focused()
        expect(page.locator('#sourcePreview')).to_contain_text('试采失败')
        page.unroute('**/api/sources/preview')
        page.locator('#sourceKindCards [data-kind=api]').click()
        source.locator('[name=token_alias]').fill('DM_API_TOKEN_DM41_UNCONFIGURED')
        page.locator('#previewSource').click()
        expect(source.locator('[name=token_alias]')).to_be_focused()
        expect(source.locator('[name=token_alias]')).to_have_attribute('aria-invalid', 'true')
        assert page.request.get(url + '/api/sources?project_id=1').json() == []
        page.goto(url)
        page.locator('#headerStorageBtn').click()
        page.route('**/api/connections/check', lambda route: route.fulfill(status=503, json={'detail':'模拟模型离线'}))
        model = page.locator('.envCard').filter(has=page.locator('[data-connection-status="model"]'))
        model.get_by_role('button', name='检测连接').click()
        expect(model.locator('[data-connection-summary]')).to_contain_text('模拟模型离线')
        expect(model.locator('[data-connection-summary]')).to_contain_text(time.strftime('%Y'))
        model.get_by_role('button',name='查看对应配置').click()
        expect(page.locator('#processingConnection')).to_be_in_viewport()
        page.locator('a[href="#storageSettings"]').click()
        storage = page.locator('#storageSettings')
        storage.locator('summary').click()
        storage.locator('[name=directory]').fill(str(root / 'legacy'))
        storage.get_by_role('button').click()
        expect(page.locator('#storageProgress')).to_contain_text('已有同名数据库')
        storage.locator('[name=directory]').fill(str(root / 'moved'))
        storage.get_by_role('button').click()
        expect(page.locator('#storageProgress')).to_contain_text('后续读写使用所选目录')
        assert (root / 'moved/workbench.sqlite3').is_file()
        assert page.request.get(url + '/api/runs/17/export').text() == '归档旧稿第二版'
        page.goto(url)
        day = page.locator('#accountDate').input_value()
        for name, views, engagements in [('A', 100, 10), ('B', 10, 9)]:
            response = page.request.put(url + '/api/projects/1/account-daily', data=dict(
                platform='bilibili', account=name, day=day, followers=1,
                views=views, engagements=engagements, published=0))
            assert response.ok, response.text()
        page.reload()
        page.locator('.platformSummary').first.click()
        expect(page.locator('.accountRoster .fieldHelp').first).to_contain_text('17.27%')
        response = page.request.put(url + '/api/projects/1/platform-accounts', data=dict(platform='bilibili', account='C'))
        assert response.ok, response.text()
        page.reload()
        start = time.perf_counter()
        page.locator('#unreportedAccounts summary').click()
        fillers = page.locator('#unreportedAccounts button')
        expect(fillers).to_have_count(1)
        fillers.click()
        expect(page.locator('#reportForm [name=account]')).to_have_value('C')
        expect(page.locator('#reportForm [name=day]')).to_have_value(day)
        print(f'1440px 总览→缺报→指定账号补填：2 次点击，{(time.perf_counter()-start)*1000:.0f} ms')
        browser.close()


def main():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder).resolve()
        source = root / 'source'
        for name in build_release.FILES:
            dest = source / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(build_release.ROOT / name, dest)
        build_release.ROOT = source
        build_release.build()
        with ZipFile(source / 'dist/DM-Content-Workbench.zip') as archive:
            archive.extractall(root / 'install')
        restore_old_workspace(root)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        url = f'http://127.0.0.1:{port}'
        server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:app', '--port', str(port), '--log-level', 'warning'],
                                  cwd=root / 'install/DM-Content-Workbench', env=dict(os.environ, DM_DATA_DIR=str(root / 'data')))
        try:
            for attempt in range(50):
                try:
                    httpx.get(url + '/api/projects').raise_for_status()
                    break
                except httpx.ConnectError:
                    if server.poll() is not None or attempt == 49:
                        raise
                    time.sleep(.1)
            for suffix, expected in [('?version=1', '归档旧稿第一版'), ('', '归档旧稿第二版')]:
                response = httpx.get(url + '/api/runs/17/export' + suffix)
                response.raise_for_status()
                assert response.text == expected
            check_pages(url, root)
        finally:
            server.terminate()
            server.wait(timeout=15)


if __name__ == '__main__':
    main()
