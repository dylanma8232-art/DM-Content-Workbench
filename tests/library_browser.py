"""Run directly with Playwright and Chrome against a temporary workbench database."""
import unittest
import json
from base64 import b64decode
from contextlib import contextmanager
from time import perf_counter
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

from test_workbench import WorkbenchFixture
import app
from content_image_store import ImageCache


class LocalBrowserFixture(WorkbenchFixture):
    @contextmanager
    def workspace(self):
        def serve(route):
            req = route.request
            url = urlsplit(req.url)
            if url.hostname != 'workbench.test':
                return route.abort()
            response = self.client.request(req.method, url.path + '?' + url.query, content=req.post_data_buffer,
                                           headers={'content-type': req.headers.get('content-type', '')})
            headers = {key:response.headers[key] for key in ('content-type', 'content-disposition') if key in response.headers}
            route.fulfill(status=response.status_code, headers=headers, body=response.content)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='chrome')
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            page.route('**/*', serve)
            page.goto('http://workbench.test')
            yield page
            browser.close()


class LibraryBrowserTests(LocalBrowserFixture):
    def test_second_page_return_evidence_selection_and_late_refresh(self):
        for source in ['甲来源', '乙来源']:
            form = {**self.form, 'filename':source, 'content':'名称,正文\n' + '\n'.join(
                f'{source}{index},样本文字{index}' for index in range(100))}
            response = self.client.post('/api/runs', json=form)
            response.raise_for_status()
            run = response.json()['id']
        pixel = b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/a9sAAAAASUVORK5CYII=')
        asset = ImageCache(app.database, 1).put('fixture:sample', pixel, 'image/png')
        with app.database() as conn:
            payload = json.loads(conn.execute('SELECT payload FROM runs WHERE id=?',(run,)).fetchone()[0])
            payload['materials'][20]['raw'] = {
                'processing_status':'partial', 'analysis_status':'ready',
                'analysis':{'claims':[{'text':'样本结论', 'evidence_ids':['subtitle:2','image:1','image:3']}]},
                'image_texts':[{'index':4,'text':'原图文字'}], 'image_assets':[{'index':4,'id':asset}],
                'segments':[{}, {'start':72,'text':'对应字幕原句'}],
                'evidence':[{'id':'subtitle:2','text':'对应字幕原句'}, {'id':'image:1','text':'原图文字'}]}
            conn.execute('UPDATE runs SET payload=? WHERE id=?',(json.dumps(payload),run))
        other = self.client.post('/api/projects', json={'name':'另一项目'}).json()['id']
        with self.workspace() as page:
            start = perf_counter()
            page.locator('#materialsNav').click()
            expect(page.locator('.lib-item')).to_have_count(20)
            print(f'200 条素材首屏 20 条（点击至渲染）{(perf_counter()-start)*1000:.0f} ms')
            query = page.get_by_placeholder('搜索标题、正文或来源')
            query.fill('样本')
            expect(page.locator('.lib-applied')).to_contain_text('搜索：样本')
            page.get_by_text('按批次日期筛选（本地）').click()
            page.get_by_label('起始日期').fill('2020-01-01')
            expect(page.locator('.lib-applied')).to_contain_text('2020-01-01')
            page.get_by_role('button',name='下一页').click()
            row = page.locator('.lib-item').first
            expect(row).to_contain_text('乙来源20')
            page.locator('.lib-row input').first.check()
            row.click()
            page.get_by_text('样本结论 · 依据缺失，待复核').click()
            evidence = page.locator('.lib-evidence[open]')
            expect(evidence).to_contain_text('1:12')
            expect(evidence).to_contain_text('对应字幕原句')
            image = evidence.get_by_role('img', name='image:1 对应画面')
            expect(image).to_have_attribute('src', f'/api/projects/1/collection-images/{asset}')
            image.evaluate('image => image.decode()')
            expect(evidence).to_contain_text('image:3 · 引用已失效')
            page.get_by_role('button',name='打开所属批次').click()
            page.get_by_role('button',name='返回素材库').click()
            expect(row).to_be_focused()
            expect(row).to_contain_text('乙来源20')
            expect(query).to_have_value('样本')
            expect(page.locator('.lib-applied')).to_contain_text('2020-01-01')
            page.locator('.lib-filters select').select_option('甲来源', force=True)
            expect(row).to_contain_text('甲来源0')
            page.locator('.lib-row input').first.check()
            page.locator('.lib-review summary').click()
            expect(page.locator('.lib-review li')).to_have_count(2)
            expect(page.locator('.lib-review')).to_contain_text('乙来源20')
            name = page.get_by_label('新批次名称')
            name.fill('保留输入')
            self.client.post('/api/runs', json={**self.form, 'filename':'甲来源',
                'content':'名称,正文\n新增,样本新素材'}).raise_for_status()
            for unused in range(2):
                with page.expect_response(lambda response: '/library/materials?' in response.url):
                    page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                expect(name).to_be_focused()
                expect(name).to_have_value('保留输入')
                expect(page.get_by_role('checkbox', name='选择素材 甲来源0', exact=True)).to_be_checked()
            remove = page.locator('.lib-review li').filter(has_text='乙来源20').get_by_role('button',name='移除')
            remove.focus()
            for total in (102,103):
                self.client.post('/api/runs', json={**self.form,'filename':'甲来源',
                    'content':f'名称,正文\n新增{total},样本{total}'}).raise_for_status()
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                expect(page.locator('.lib-message')).to_have_text(f'共 {total} 条')
                expect(remove).to_be_focused()
            remove.press('Enter')
            expect(page.locator('.lib-review li')).to_have_count(1)
            expect(page.locator('.lib-review li')).to_contain_text('甲来源0')
            page.get_by_role('button',name='清空选择').click()
            expect(page.locator('.lib-message')).to_have_text('共 103 条')
            page.evaluate('''() => {
                const original = window.fetch;
                window.fetch = async (...args) => {
                    const response = await original(...args);
                    if (String(args[0]).includes('/projects/1/library/materials?')) {
                        await new Promise(resolve => {window.releaseLibrary = resolve});
                    }
                    return response;
                };
                document.dispatchEvent(new Event('visibilitychange'));
            }''')
            page.wait_for_function('!!window.releaseLibrary')
            page.locator('#project').select_option(str(other), force=True)
            page.locator('#materialsNav').click()
            page.evaluate('window.releaseLibrary()')
            expect(page.locator('.lib-list')).to_contain_text('项目里还没有素材')
            expect(query).to_have_value('')
            expect(page.locator('.lib-row')).to_have_count(0)

    def test_read_select_restore_and_empty_actions(self):
        run = self.create()
        other = self.client.post('/api/projects', json={'name': '另一个项目'}).json()['id']
        for stage in range(1, 5):
            self.client.post(f'/api/runs/{run}/advance', json={'stage': stage, 'topic_id': 1}).raise_for_status()
        body = '# 采访\n\n1. **第一条**\n2. `第二条`\n\n> 引用\n\n```html\n<b>示例</b>\n```\n\n<script>bad()</script>\n[资料](https://example.com)\n[危险](javascript:bad())\n全文结尾'
        self.client.put(f'/api/runs/{run}/draft', json={'version': 1, 'content': body}).raise_for_status()

        with self.workspace() as page:
            page.locator('#materialsNav').click()
            check = page.locator('.lib-row input').first
            name = page.get_by_label('新批次名称')
            check.check()
            name.fill('访谈合辑')
            page.reload()
            page.locator('#materialsNav').click()
            expect(check).to_be_checked()
            expect(name).to_have_value('访谈合辑')
            page.get_by_placeholder('搜索标题、正文或来源').fill('没有这样的素材')
            empty = page.locator('.lib-list .lib-empty-detail')
            empty.get_by_role('button', name='清除全部筛选').click()
            expect(check).to_be_checked()
            page.locator('#project').select_option(str(other), force=True)
            page.locator('#materialsNav').click()
            expect(name).to_have_value('素材合辑')
            empty.get_by_role('button', name='导入新素材').click()
            expect(page.locator('#setup')).to_be_visible()
            page.locator('#project').select_option('1', force=True)
            page.locator('#draftsNav').click()
            search = page.get_by_placeholder('搜索标题、正文或来源')
            search.fill('访谈')
            expect(page.locator('.lib-applied')).to_contain_text('搜索：访谈')
            page.locator('.lib-item').first.click()
            article = page.locator('.lib-detail .lib-prose .lib-markdown')
            expect(article.get_by_role('heading', name='采访')).to_be_visible()
            expect(article.locator('ol li strong')).to_have_text('第一条')
            expect(article.locator('pre code')).to_have_text('<b>示例</b>')
            expect(article.locator('blockquote')).to_have_text('引用')
            expect(article.locator('a')).to_have_count(1)
            expect(article.locator('script')).to_have_count(0)
            expect(article).to_contain_text('全文结尾')
            page.get_by_role('combobox', name='稿件版本').click()
            page.get_by_role('option', name='v1', exact=True).click()
            first_version = self.client.get(f'/api/runs/{run}/export?version=1').text
            with page.expect_download() as download:
                page.get_by_role('link', name='导出 v1 (.md)').click()
            self.assertEqual(download.value.path().read_text(), first_version)
            expect(article).not_to_contain_text('全文结尾')
            page.get_by_role('button',name='打开原始素材').first.click()
            expect(page.locator('.lib-detail')).to_contain_text('阅读材料')
            page.get_by_role('button',name='返回来源稿件').click()
            expect(search).to_have_value('访谈')
            expect(page.get_by_role('link',name='导出 v1 (.md)')).to_be_visible()
            page.locator('#materialsNav').click()
            expect(check).to_be_checked()
            expect(name).to_have_value('访谈合辑')
            page.get_by_role('button', name='合并为新批次').click()
            expect(page.locator('#library')).to_be_hidden()
            page.locator('#materialsNav').click()
            expect(name).to_have_value('素材合辑')
            expect(page.locator('.lib-row input:checked')).to_have_count(0)


if __name__ == '__main__':
    unittest.main()
