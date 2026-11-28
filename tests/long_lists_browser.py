"""Local Chrome timing samples and interaction regressions for project lists."""
import unittest
from time import perf_counter

from playwright.sync_api import expect

import app
from library_browser import LocalBrowserFixture


class LongListTests(LocalBrowserFixture):
    def setUp(self):
        super().setUp()
        run = self.create()
        self.other = self.client.post('/api/projects', json={'name':'空项目'}).json()['id']
        with app.database() as conn:
            for index in range(200):
                if index:
                    conn.execute('INSERT INTO runs(project_id,name,stage,payload) SELECT project_id,?,stage,payload FROM runs WHERE id=?',
                                 (f'样本批次{index}', run))
                conn.execute('INSERT INTO collection_tasks(project_id,platform,mode,query,url,"limit",status) VALUES(1,?,?,?,?,10,?)',
                             ('bilibili','topic',f'样本任务{index}','https://example.org/','failed'))
                post = conn.execute('INSERT INTO publishing_posts(project_id,request_id,fingerprint,title,body) VALUES(1,?,?,?,?)',
                                    (str(index),'sample',f'发布样本{index}','正文' * 512)).lastrowid
                for platform in ('xiaohongshu','bilibili'):
                    conn.execute('INSERT INTO publishing_targets(post_id,platform,account) VALUES(?,?,?)',(post,platform,'样本账号'))

    def test_open_samples(self):
        with self.workspace() as page:
            page.set_viewport_size({'width':1440,'height':900})
            for name, nav, selector in [('发布','publishingNav','.publishPost'), ('工作流','workNav','#runRows .overviewRow'),
                                        ('采集任务','channelNav','#taskList article')]:
                start = perf_counter()
                page.locator('#'+nav).click()
                rows = page.locator(selector)
                rows.first.wait_for(state='attached')
                print(f'{name} 200 条，首批 {rows.count()} 条，点击至列表就绪 {(perf_counter()-start)*1000:.0f} ms')
                expect(rows).to_have_count(20)

    def test_refresh_inputs_focus_and_project_switch(self):
        with self.workspace() as page:
            page.set_viewport_size({'width':1440,'height':900})
            def background():
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")

            page.evaluate("document.documentElement.dataset.dmPublishExtension = '1'")

            for nav, panel, path in [('publishingNav','publishPosts','publishing?'),
                                     ('workNav','runRows','/overview'),('channelNav','taskList','collection-tasks?')]:
                page.locator('#'+nav).click()
                root = page.locator('#'+panel)
                if panel == 'runRows':
                    page.locator('#pipeline button').first.click()
                root.get_by_role('button',name='下一页').click()
                expect(root.get_by_role('navigation')).to_contain_text('21–40')
                if panel == 'publishPosts':
                    article = root.locator('article').first
                    check = article.get_by_role('checkbox').first
                    check.uncheck()
                    focus = article.locator('input[type=url]').first
                    focus.fill('https://example.org/unfinished')
                elif panel == 'taskList':
                    row = root.locator('article').first
                    row.get_by_role('button',name='修改采集设置').click()
                    focus = row.locator('input[name=limit]')
                    focus.fill('17')
                else:
                    focus = root.locator('.overviewRow').first
                    focus.focus()
                for index in range(2):
                    marker = f'已刷新{index}'
                    with app.database() as conn:
                        if panel == 'publishPosts':
                            conn.execute('UPDATE publishing_posts SET body=? WHERE id=180',(marker,))
                        elif panel == 'taskList':
                            conn.execute('UPDATE collection_tasks SET last_error=? WHERE id=180',(marker,))
                        else:
                            conn.execute('UPDATE runs SET name=? WHERE id=180',(marker,))
                    background()
                    expect(root).to_contain_text(marker)
                    expect(focus).to_be_focused()
                    if panel == 'publishPosts':
                        expect(focus).to_have_value('https://example.org/unfinished')
                        expect(check).not_to_be_checked()
                    elif panel == 'taskList':
                        expect(focus).to_have_value('17')
                    else:
                        expect(page.locator('#runFilter')).to_have_value('1')

                page.evaluate('''path => {
                    const original = window.fetch;
                    window.fetch = async (...args) => {
                        const response = await original(...args);
                        if (String(args[0]).includes('/projects/1/') && String(args[0]).includes(path)) {
                            window.fetch = original;
                            await new Promise(resolve => { window.releaseOldList = resolve; });
                        }
                        return response;
                    };
                    delete window.releaseOldList;
                }''',path)
                background()
                page.wait_for_function('!!window.releaseOldList')
                page.locator('#project').select_option(str(self.other),force=True)
                expect(root).to_contain_text({'publishPosts':'当前项目还没有发布任务', 'taskList':'当前项目还没有平台采集任务',
                                              'runRows':'暂无符合当前筛选阶段的批次'}[panel])
                page.evaluate('async () => { window.releaseOldList(); await new Promise(requestAnimationFrame); }')
                expect(root.locator('article,.overviewRow')).to_have_count(0)
                page.locator('#project').select_option('1',force=True)

    def test_workflow_task_link_opens_its_page(self):
        with self.workspace() as page:
            page.set_viewport_size({'width':1440,'height':900})
            page.locator('#workNav').click()
            queue = page.locator('#editorialQueue')
            queue.locator(':scope > summary').click()
            queue.get_by_role('button',name='下一页').click()
            task = queue.get_by_role('button',name='B站 · 样本任务179 查看采集失败任务')
            task.click()
            target = page.locator('#taskList article[data-task-id="180"]')
            expect(target.get_by_role('button',name='立即采集')).to_be_focused()
            expect(page.locator('#taskList nav')).to_contain_text('21–40')

    def test_paged_apis_and_legacy_callers(self):
        for kind, key in [('publishing','posts'),('collection-tasks','tasks')]:
            url = '/api/projects/1/' + kind
            self.assertEqual(len(self.client.get(url).json()[key]),200)
            listed = self.client.get(url,params={'offset':20,'limit':20}).json()
            self.assertEqual([row['id'] for row in listed[key]],list(range(180,160,-1)))
            self.assertEqual((listed['total'],listed['offset']),(200,20))
            self.assertEqual(self.client.get(url,params={'limit':101}).status_code,422)
            anchored = self.client.get(url,params={'limit':20,('target_id' if key=='posts' else 'task_id'):1}).json()
            self.assertEqual(anchored['offset'],180)
            empty = self.client.get(f'/api/projects/{self.other}/{kind}',params={'limit':20}).json()
            self.assertEqual((empty[key],empty['total']),([],0))


if __name__ == '__main__':
    unittest.main()
