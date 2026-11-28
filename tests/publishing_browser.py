"""Run with PYTHONPATH=.:tests .venv/bin/python -m unittest publishing_browser."""
import json
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright

from test_workbench import WorkbenchFixture


class PublishingBrowserTests(WorkbenchFixture):
    def test_resume_and_verify_each_target(self):
        targets = [{'platform': platform, 'account': name} for platform, name in [('bilibili', 'A'), ('douyin', 'B')]]
        base = '/api/projects/1/publishing'
        self.client.post(base, data={'request_id':str(uuid4()), 'title':'双平台稿', 'body':'测试稿',
                                     'targets':json.dumps(targets)}).raise_for_status()
        outside = []
        def local(route):
            req = route.request
            address = urlsplit(req.url)
            if address.netloc != 'localhost':
                outside.append(req.url)
                route.abort()
                return
            response = self.client.request(req.method, req.url, headers=req.headers, content=req.post_data_buffer)
            headers = dict(response.headers)
            headers.pop('content-encoding', None)
            headers.pop('content-length', None)
            route.fulfill(status=response.status_code, headers=headers, body=response.content)

        with sync_playwright() as driver:
            browser = driver.chromium.launch(channel='chrome')
            page = browser.new_page(viewport={'width':1440,'height':900})
            page.route('**/*', local)
            page.goto('http://localhost/')
            page.locator('#publishingNav').click()
            post = page.locator('#publishPosts article')
            first, second = (post.locator('.publishTarget').filter(has_text=' · ' + name) for name in ('A','B'))
            expect(post).to_contain_text('独立录入稿')
            first.get_by_text('安装扩展以辅助填写').click()
            expect(first.get_by_text('下载扩展 ZIP')).to_have_attribute('href','/api/publishing/extension.zip')
            page.evaluate("""() => { document.documentElement.dataset.dmPublishExtension = '1';
                window.addEventListener('message', e => { if(e.data.type==='dm-publish-open') window.assisted=e.data; }); }""")
            first.get_by_role('button',name='工作台辅助填写').click()
            page.wait_for_function('window.assisted?.target === 1')
            expect(post).to_contain_text('已确认发布 0/2')
            page.reload()
            page.get_by_role('button',name='返回上次目标').click()
            expect(first.locator('input[type=url]')).to_be_focused()
            url = 'https://example.org/works/a'
            first.locator('input[type=url]').fill(url)
            first.get_by_text('确认已发布').click()
            second.get_by_text('标记失败').click()
            expect(second).to_contain_text('发布失败')
            second.get_by_text('重新尝试').click()
            expect(second).to_contain_text('待发布')
            page.reload()
            expect(first.locator('.ph small')).to_have_text('已发布')
            expect(first.get_by_text('查看作品')).to_have_attribute('href',url)
            expect(second.locator('.ph small')).to_have_text('待发布')
            expect(post).to_contain_text('已确认发布 1/2')
            second.locator('input[type=url]').fill('https://example.org/works/b')
            second.get_by_text('确认已发布').click()
            expect(post).to_contain_text('已确认发布 2/2')
            first.get_by_text('作品复盘').click()
            expect(first).to_contain_text('暂无作品数据')
            first.get_by_label('阅读／播放').fill('7')
            first.get_by_label('互动次数').fill('0')
            first.get_by_label('数据来源').fill('作品明细')
            first.get_by_text('保存作品数据').click()
            expect(first).to_contain_text('阅读／播放 7 · 互动 0 · 作品明细')
            expect(second).to_contain_text('暂无作品数据')
            browser.close()
        self.assertEqual(outside, [])
