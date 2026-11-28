"""Exercise the collection task and material detail controls with local fixtures."""
import unittest
import app
from urllib.parse import urlsplit
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright

from test_collection_tasks import PluginCollectionFixture
from online_libraries import Remote
from browser_acceptance import verify_duplicate_collection


class CollectionBrowserTests(PluginCollectionFixture):
    def browser_page(self, playwright):
        browser = playwright.chromium.launch(channel='chrome')
        page = browser.new_page()
        page.set_default_timeout(5000)

        def serve(route):
            request = route.request
            url = urlsplit(request.url)
            if url.hostname != 'workbench.test':
                route.abort()
                return
            result = self.client.request(request.method, url.path+'?'+url.query, content=request.post_data_buffer,
                                         headers={'content-type':request.headers.get('content-type','')})
            route.fulfill(status=result.status_code, content_type=result.headers.get('content-type'), body=result.content)

        page.route('**/*', serve)
        page.goto('http://workbench.test')
        return browser, page

    def test_online_check_and_import_on_same_connection(self):
        base = '/api/projects/1/online-libraries'
        connection = dict(provider='notion',name='模拟资料库',address='a'*32,title_field='标题',body_field='正文',token='fixture-only-key')
        self.client.post(base,json=connection).raise_for_status()
        result = dict(fields=['标题','正文'],items=[dict(title='模拟标题',body='模拟正文',source_id='row-1',raw={})],partial=False)
        with sync_playwright() as playwright, patch.object(Remote,'read',return_value=result):
            browser, page = self.browser_page(playwright)
            page.evaluate("window.openConfigSection('onlineLibraries')")
            card = page.locator('.onlineLibraryEntry').filter(has_text='模拟资料库')
            card.get_by_role('button',name='检测读取权限').click()
            expect(card).to_contain_text('可读取字段：标题、正文')
            expect(card).not_to_contain_text('最近导入')
            card.get_by_role('button',name='导入到素材库').click()
            expect(card).to_contain_text('最近导入 1 条')
            card.get_by_role('button',name='查看最近导入批次').click()
            expect(page.locator('.lib-detail:visible')).to_contain_text('模拟正文')
            browser.close()

    def test_old_and_new_receipt_skip_counts(self):
        with sync_playwright() as playwright:
            browser, page = self.browser_page(playwright)
            verify_duplicate_collection(page)
            page.route('**/collection-tasks/1/run',lambda route:route.fulfill(json=dict(imported=0,duplicates=3,source_duplicates=1,skipped_quality=2,run_id=None)))
            page.locator('#taskList').get_by_role('button',name='立即采集').click()
            expect(page.locator('#taskNotice')).to_contain_text('跳过重复 1 条，跳过低质量 2 条')
            browser.close()

    def test_text_and_legacy_import_without_content_level(self):
        run = self.create()
        with app.database() as conn:
            legacy = app.insert_run(conn,'旧图片',{'format':'platform','platform':'x'},[{
                'id':1,'title':'旧图片','body':'原文','raw':{'media_kind':'image','body':'原文',
                'image_texts':[{'index':1,'status':'missing','text':''}],'processing_status':'partial'}}])['id']
        with sync_playwright() as playwright:
            browser, page = self.browser_page(playwright)
            page.evaluate("run => window.openLibrary('materials',{run_id:run})",run)
            panel = page.locator('.lib-detail:visible')
            expect(panel).to_contain_text('正文 · 已取得')
            expect(panel).not_to_contain_text('字幕或转写')
            expect(panel).not_to_contain_text('关键画面')
            page.evaluate("run => window.openLibrary('materials',{run_id:run})",legacy)
            expect(panel).to_contain_text('图片文字 · 未取得')
            expect(panel).to_contain_text('图片 1 未取得识别文字，原记录未保存具体错误')
            panel.get_by_role('button',name='重新导入原文件或正文').click()
            expect(page.locator('#importForm')).to_be_visible()
            browser.close()

    def test_partial_filter_and_targeted_speech(self):
        base = '/api/projects/1/collection-tasks'
        task = self.client.post(base, json=dict(platform='bilibili', mode='topic', query='模拟话题', limit=2)).json()
        record = dict(external_id='av1', title='模拟视频', body='简介', author='作者', media_kind='video',
                      url='https://www.bilibili.com/video/av1', content_level='detail_text', missing=['speech_unavailable'])
        with patch('content_understanding.analyze', return_value={'analysis_status':'ready'}):
            batch = self.collect(f'{base}/{task["id"]}', [record,record])

        with sync_playwright() as playwright, patch('collection_depth.media_text',return_value={'content_level':'transcript','body':'模拟转写'}) as speech:
            browser, page = self.browser_page(playwright)
            page.evaluate("window.openConfigSection('collectionTasks')")
            expect(page.locator('#taskList')).to_contain_text('读取 2 · 本批收录 1')
            page.get_by_role('button',name='查看缺项素材（1）').click()
            expect(page.locator('.lib-detail:visible')).to_contain_text('模拟视频')
            panel = page.locator('.lib-detail:visible')
            expect(panel.get_by_role('heading', name='媒体提取结果')).to_be_visible()
            panel.get_by_role('button',name='仅补本条字幕或转写').click()
            expect(page.locator('#notice')).to_contain_text('未取得 → 已取得')
            expect(page.locator('.lib-row')).to_have_count(0)
            expect(page.locator('#library')).not_to_contain_text('未取得完整讲话内容')
            page.evaluate("run=>window.openLibrary('materials',{run_id:run})",batch['run_id'])
            expect(panel).to_contain_text('内容解析 · 已完成')
            expect(panel).not_to_contain_text('未取得完整讲话内容')
            panel.get_by_text('查看字幕或转写',exact=True).click()
            expect(panel).to_contain_text('模拟转写')
            speech.assert_called_once()
            browser.close()

    def test_image_gap_remains_after_speech_retry_and_recovery_reaches_task(self):
        base = '/api/projects/1/collection-tasks'
        task = self.client.post(base, json=dict(platform='bilibili',mode='topic',query='图文恢复',limit=1)).json()
        raw = dict(external_id='av2',title='图片失败的作品',body='简介',author='作者',url='https://www.bilibili.com/video/av2',
                   media_kind='video',missing=['speech_unavailable','image_unavailable'],content_level='detail_text')
        with patch('content_understanding.analyze',return_value={'analysis_status':'ready'}):
            batch = self.collect(f'{base}/{task["id"]}', [raw])
        with sync_playwright() as playwright, patch('collection_depth.media_text',return_value={'content_level':'transcript','body':'已补讲话'}):
            browser, page = self.browser_page(playwright)
            page.evaluate("run=>window.openLibrary('materials',{run_id:run,partial_only:true})",batch['run_id'])
            detail = page.locator('.lib-detail:visible')
            detail.get_by_role('button',name='仅补本条字幕或转写').click()
            expect(page.locator('#notice')).to_contain_text('剩余缺项 1 项')
            expect(page.locator('.lib-row')).to_have_count(1)
            expect(detail).not_to_contain_text('未取得完整讲话内容')
            expect(detail).to_contain_text('部分配图未能下载')
            expect(detail.get_by_role('button',name='仅补本条字幕或转写')).to_have_count(0)
            detail.get_by_role('button',name='前往原任务重新解析本批').click()
            original=page.locator(f'#taskList [data-task-id="{task["id"]}"]')
            expect(original.get_by_role('button',name='重新解析上一批素材')).to_be_focused()
            browser.close()


if __name__ == '__main__':
    unittest.main()
