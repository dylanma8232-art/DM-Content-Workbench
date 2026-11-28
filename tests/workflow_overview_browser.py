"""Run with Playwright and Chrome. The server uses a temporary data directory."""
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import httpx
from playwright.sync_api import expect, sync_playwright


def verify(page, url):
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(url)
    expect(page.locator('#overview')).to_be_visible()
    page.locator('#homeWelcome').click()
    expect(page.locator('#welcomeForm')).to_be_visible()
    page.locator('#skipWelcome').click()
    expect(page.locator('#overview')).to_be_visible()
    page.route('**/library/topics?*', lambda route: route.fulfill(status=503, json={}))
    page.locator('#workNav').click()
    expect(page.locator('#pipeline button')).to_have_count(5)
    expect(page.locator('#startWithFile')).to_be_visible()
    for width in [320, 390, 768, 1179, 1180, 1440]:
        page.set_viewport_size({'width': width, 'height': 900})
        assert page.locator('#startWithFile').bounding_box()['y'] < page.locator('#pipeline').bounding_box()['y']
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.locator('#startWithFile').click()
    expect(page.locator('#setup')).to_be_visible()
    with httpx.Client(base_url=url) as api:
        def post(path, **data):
            response = api.post(path, json=data)
            response.raise_for_status()
            return response.json()

        def source(name):
            return post('/api/runs', name=name, filename=name, format='csv', content='title,body\n访谈,周末读者希望延长开放时间。\n重复,周末读者希望延长开放时间。')['id']

        ids = [source('第一批.csv'), source('第二批.csv')]
        merged = post('/api/projects/1/collections', name='周末专题', request_key=str(uuid4()),
                      items=[{'run_id': i, 'material_id': 1} for i in ids])['id']
        for stage in range(1, 5):
            post(f'/api/runs/{merged}/advance', stage=stage, topic_id=1)
        post(f'/api/runs/{merged}/review', version=1, decision='通过')
        overview = api.get('/api/projects/1/overview').json()
        assert set(overview['runs'][0]['source_ids']) == set(ids)
        assert overview['by_stage'] == [2, 0, 0, 0, 1]
        page.locator('#workNav').click()
        expect(page.locator('#focusSpotlight button')).to_have_text('查看已审核稿件')
        expect(page.locator('#focusSpotlight')).to_contain_text('第一批.csv')
        expect(page.locator('#progressCaption')).to_contain_text('2 个源批次已用于成稿专题')
        page.locator('[data-shelf=candidateShelf]').click()
        expect(page.locator('#candidateShelf')).to_contain_text('摘要暂时无法读取')
        page.locator('[data-shelf=outputs]').click()
        expect(page.locator('#outputs button')).to_have_count(1)
        page.locator('#pipeline button').first.click()
        rows = page.locator('#runRows button')
        expect(rows).to_have_count(2)
        rows.filter(has_text='第一批.csv').click()
        page.locator('#workflowRelations').get_by_role('button', name='关联专题 · 周末专题').click()
        page.locator('#workflowRelations').get_by_role('button', name='源批次 · 第一批.csv').click()
        page.locator('#task').get_by_role('button', name='执行清洗', exact=True).click()
        expect(page.locator('#runInfo')).to_contain_text('2 / 5')
        page.reload()
        expect(page.locator('#runInfo')).to_contain_text('2 / 5')
        page.get_by_role('button', name='返回阶段列表').click()
        expect(page.locator('#runFilter')).to_have_value('1')
        expect(rows).to_have_count(1)
        expect(rows.first).to_contain_text('第二批.csv')
        expect(page.locator('#editorialQueue .rowList button')).to_have_count(0)
        expect(page.locator('[data-shelf=outputs]')).to_have_attribute('aria-pressed', 'true')
        source('独立任务.csv')
        page.locator('#refreshHomeBtn').click()
        expect(page.locator('#focusSpotlight h3')).to_have_text('独立任务.csv')
        # Delay the detail response until a later navigation has completed.
        pending = []
        run_url = re.compile(r'/api/runs/\d+$')
        page.route(run_url, lambda intercepted: pending.append(intercepted))
        with page.expect_request(run_url):
            page.locator('#focusSpotlight button').click()
        page.locator('#draftsNav').click()
        assert len(pending) == 1
        with page.expect_event('requestfinished', predicate=lambda req: req.url == pending[0].request.url):
            pending[0].continue_()
        page.unroute(run_url)
        expect(page.locator('#library h1')).to_have_text('成稿库')
        page.go_back()
        expect(page.locator('#workflow')).to_be_visible()
        page.go_forward()
        expect(page.locator('#library h1')).to_have_text('成稿库')
        other = post('/api/projects', name='另一个项目')['id']
        page.reload()
        expect(page.locator('#library h1')).to_have_text('成稿库')
        page.locator('#project').select_option(str(other), force=True)
        expect(page.locator('#draftsNav')).to_have_attribute('aria-current', 'page')
        expect(page.locator('#library h1')).to_have_text('成稿库')
        page.go_back()
        expect(page.locator('#project')).to_have_value('1')
        expect(page.locator('#history time').first).to_have_text(page.locator('#history time').first.evaluate("e => new Date(e.dateTime).toLocaleString('zh-CN', {hour12:false})"))
        page.set_viewport_size({'width':390, 'height':844})
        toggle = page.locator('#mobileNavToggle')
        toggle.press('Enter')
        page.locator('#workNav').press('Escape')
        expect(toggle).to_be_focused()
        toggle.press('Enter')
        page.locator('#workNav').press('Enter')
        expect(page.locator('#workflow h1').first).to_be_focused()
        for n in range(8):
            source(f'历史批次{n}.csv')
        page.locator('#refreshHomeBtn').click()
        page.locator('.historyDropdown summary').click()
        expect(page.locator('#history time')).to_have_count(10)
        page.locator('#history').get_by_role('button', name='查看全部 12 个批次').click()
        expect(rows).to_have_count(12)
    assert not errors, errors


def verify_editorial(page, url, folder):
    errors = []
    page.route('**/api/connections', lambda route: route.fulfill(json={'model':None}))
    page.on('pageerror', lambda error: errors.append(str(error)))
    with httpx.Client(base_url=url) as api:
        def post(path, **data):
            response = api.post(path, json=data)
            response.raise_for_status()
            return response.json()

        project = post('/api/setup', name='验收项目', audience='社区居民')['project_id']
        def visit(view, run=None):
            page.goto(f'{url}/?project={project}&view={view}' + (f'&run={run}' if run else ''))

        visit('welcome')
        page.get_by_role('button', name='用示例开始导入').click()
        expect(page.locator('#importForm [name=content]')).to_have_value(re.compile('重复访谈'))
        page.locator('#importForm button[type=submit]').click()
        expect(page.locator('#runInfo')).to_contain_text('1 / 5')
        visit('welcome')
        expect(page.locator('#resumeWelcome')).to_contain_text('社区居民')
        page.get_by_role('button', name='继续内容工作流').click()
        expect(page.locator('#workflow')).to_be_visible()
        single = post('/api/runs', name='单候选', filename='访谈.txt', project_id=project, format='txt', content='社区访谈唯一候选')['id']
        for stage in (1, 2):
            post(f'/api/runs/{single}/advance', stage=stage)
        visit('detail', single)
        page.get_by_role('button', name='确认选题', exact=True).click()
        expect(page.locator('.primaryStage')).to_contain_text('写稿依据')
        run = post('/api/runs', name='复核样本', filename='样本.csv', project_id=project,
                   format='csv', min_length=4, content='title,body\n访谈,社区希望开放夜读。\n重复,社区希望开放夜读。\n短文,短\n空白,\n新书,读者希望补充新书。')['id']
        path = f'/api/runs/{run}'
        post(path + '/advance', stage=1)
        visit('workflow')
        queue = page.locator('#editorialQueue')
        queue.locator(':scope > summary').click()
        queue.get_by_role('button', name=re.compile('复核样本')).click()
        fold = page.locator('#currentCleaningReview')
        select = fold.locator('.formActions select')
        for reason in ['空正文', '低于最短正文长度', '正文重复']:
            select.select_option(reason, force=True)
            expect(fold.locator('.cleaningChoice')).to_have_count(1)
        fold.get_by_text('对照两条原文').click()
        expect(fold.locator('.pair section')).to_have_count(2)
        decision = fold.locator('select[aria-label="素材 2 的清洗决定"]')
        decision.select_option('keep', force=True)
        fold.get_by_role('button', name='确认本批复核完成').click()
        expect(page.locator('#notice')).to_contain_text('已保存')
        visit('workflow')
        expect(page.locator('#pipeline button')).to_have_count(5)
        expect(queue.get_by_role('button', name=re.compile('复核样本'))).to_have_count(0)
        visit('detail', run)
        fold.locator('summary').first.click()
        expect(decision).to_have_value('keep')
        page.get_by_role('button', name='提取证据摘录', exact=True).click()
        expect(page.get_by_role('combobox', name='对照候选')).to_be_attached()
        expect(page.locator('.rationaleBox')).to_contain_text('共同引用素材')
        page.locator('input[placeholder*=沿用候选]').fill('人工选题')
        page.get_by_role('button', name='确认选题', exact=True).click()
        expect(page.locator('.primaryStage')).to_contain_text('写稿依据')
        expect(page.locator('.primaryStage h2')).to_have_text('人工选题')
        page.get_by_role('button', name='生成草稿', exact=True).click()
        review = page.locator('#currentDraftReview')
        expect(review).to_contain_text('首次审核')
        assert api.post(path+'/advance', json={'stage':4}).status_code == 409
        post(path+'/review', version=1, decision='通过', note='保留引用')
        api.put(path+'/draft', json={'version':1, 'content':'修改后的正文'}).raise_for_status()
        assert api.post(path+'/review', json={'version':1,'decision':'通过'}).status_code == 409
        visit('detail', run)
        expect(review.locator('.draftDiff')).to_have_attribute('open', '')
        expect(review).to_contain_text('保留引用')
        variant_path = path + '/variant'
        variant_calls = []
        def lose_first_reply(route):
            response = route.fetch()
            assert response.ok
            variant_calls.append((route.request.post_data_json, response.json()['id']))
            if len(variant_calls) == 1:
                route.abort()
            else:
                route.fulfill(response=response)
        page.route('**' + variant_path, lose_first_reply)
        new_variant = page.get_by_role('button', name='用同一选题制作另一份稿件')
        new_variant.click()
        expect(page.locator('#notice')).to_contain_text('连接工作台失败')
        visit('detail', run)
        new_variant.click()
        expect(page.locator('#workflowRelations')).to_contain_text('来源稿版本 2')
        assert variant_calls[0] == variant_calls[1]
        visit('detail', run)
        new_variant.click()
        expect(page.locator('#workflowRelations')).to_contain_text('来源稿版本 2')
        assert variant_calls[2][0]['request_key'] != variant_calls[0][0]['request_key']
        assert variant_calls[2][1] != variant_calls[0][1]
        intent = {'request_key':str(uuid4()), 'source_version':2}
        history = lambda: api.get('/api/runs', params={'project_id':project}).json()
        count = len(history())
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(post, variant_path, **intent) for _ in range(2)]
            assert futures[0].result()['id'] == futures[1].result()['id']
        assert len(history()) == count + 1
        assert api.post(variant_path, json={**intent,'source_version':1}).status_code == 409
        assert api.post(variant_path, json={'request_key':str(uuid4()),'source_version':1}).status_code == 409
        engine = page.locator('#task label').filter(has_text='生成引擎选择').locator('select')
        engine.select_option('model', force=True)
        page.get_by_role('button', name='生成草稿', exact=True).click()
        expect(page.locator('#notice')).to_contain_text('稿件生成失败')
        assert api.get(path).json()['payload']['drafts'][-1] == '修改后的正文'
        engine.select_option('local', force=True)
        page.get_by_role('button', name='生成草稿', exact=True).click()
        expect(page.locator('#runInfo')).to_contain_text('5 / 5')
        visit('detail', run)
        replay = page.get_by_role('button', name='按当前项目默认规则重新运行')
        before = history()
        messages = []
        def cancel(dialog):
            messages.append(dialog.message)
            dialog.dismiss()
        page.once('dialog', cancel)
        replay.click()
        expect(replay).to_be_enabled()
        assert history() == before and '4 → 1' in messages[0]
        page.once('dialog', lambda dialog: dialog.accept())
        replay.click()
        expect(page.locator('#workflowRelations')).to_contain_text('重算副本')
        copy = api.get('/api/runs/' + str(history()[0]['id'])).json()['payload']
        assert copy['parent'] == run and copy['config']['min_length'] == 1 and 'manual_decisions' not in copy['config']
        assert api.get(path).json()['payload']['config']['min_length'] == 4

        # Seed offline states directly in this server's temporary database.
        with sqlite3.connect(Path(folder)/'workbench.sqlite3') as db:
            insert_task = 'INSERT INTO collection_tasks(project_id,platform,mode,query,url,"limit",status) VALUES(?,?,?,?,?,?,?)'
            target_ids = []
            for index in range(22):
                same_name = index in (0, 21)
                cursor = db.execute(insert_task, (project,'bilibili','topic','离线样本' if same_name else str(index),
                                                  '',5,'browser' if same_name else 'ready'))
                if same_name:
                    target_ids.append(cursor.lastrowid)
            post_id = db.execute('INSERT INTO publishing_posts(project_id,request_id,fingerprint,title,body) VALUES(?,?,?,?,?)',
                                 (project,str(uuid4()),'fixture','模拟目标','合成正文')).lastrowid
            db.execute('INSERT INTO publishing_targets(post_id,platform,account,status) VALUES(?,?,?,?)',
                       (post_id,'bilibili','测试账号','failed'))
        visit('workflow')
        queue.locator(':scope > summary').click()
        failed = queue.locator('summary').filter(has_text='模拟目标')
        failed.click()
        queue.get_by_role('button', name=re.compile('恢复为待发布')).click()
        expect(failed).to_have_count(0)
        for task_id in target_ids:
            if task_id != target_ids[0]:
                visit('workflow')
                queue.locator(':scope > summary').click()
            expect(queue.get_by_role('button', name=re.compile('离线样本'))).to_have_count(2)
            queue.locator(f'[data-work-key="task-{task_id}"]').click()
            card = page.locator(f'#taskList article[data-task-id="{task_id}"]')
            expect(card.get_by_role('button', name='去登录并继续')).to_be_focused()
        assert not errors, errors


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as folder, socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        url = f'http://127.0.0.1:{listener.getsockname()[1]}'
        offline_server = """import sys, uvicorn
def offline(event, args):
    if event == 'socket.connect':
        raise OSError('External connections disabled in workflow browser test')
sys.addaudithook(offline)
import connections
def unavailable(*args, **kwargs):
    raise RuntimeError('Simulated model failure')
connections.generate = unavailable
uvicorn.run('app:app', fd=int(sys.argv[1]), log_level='warning')"""
        server = subprocess.Popen([sys.executable, '-c', offline_server, str(listener.fileno())],
                                  pass_fds=(listener.fileno(),), cwd=Path(__file__).resolve().parents[1],
                                  env=dict(os.environ, DM_DATA_DIR=folder))
        try:
            for attempt in range(50):
                try:
                    httpx.get(url + '/api/setup', timeout=1).raise_for_status()
                    break
                except httpx.TransportError:
                    if server.poll() is not None or attempt == 49:
                        raise
                    time.sleep(.1)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel='chrome')
                verify(browser.new_page(viewport={'width': 1440, 'height': 900}), url)
                verify_editorial(browser.new_page(), url, folder)
                print('Workflow overview browser checks passed')
        finally:
            server.terminate()
            server.wait(timeout=15)
