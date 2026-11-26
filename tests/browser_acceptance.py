"""Run with Playwright and Chrome installed; all business data stays in a temporary directory."""

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright


def verify(page, url, captures):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url)
    expect(page.locator("#accountRows")).to_contain_text("还没有账号日报")
    expect(page.locator("#workflow")).to_be_hidden()
    page.locator("#workNav").click()
    expect(page.locator("#focusSpotlight")).to_contain_text("导入第一份素材")
    expect(page.locator("#recentPanel")).to_be_hidden()
    page.locator("#home").click()
    page.locator("#homeWelcome").click()
    page.locator("#initialSource summary").click()
    page.locator("#welcomeForm [name=platform]").fill("测试博客")
    page.locator("#welcomeForm [name=source_address]").fill("https://example.org/feed")
    page.locator("#welcomeForm [name=name]").fill("编辑部 · 验收项目")
    page.locator("#welcomeForm [value=overview]").click()
    expect(page.locator("#accountScope")).to_contain_text("编辑部 · 验收项目")
    page.locator("#platformSetupBtn").click()
    setup = page.locator("#platformSetup")
    setup.locator("[name=platform]").fill("微信公众号")
    setup.locator("[name=account]").fill("阅读手记")
    setup.locator("#platformSave").click()
    account = page.locator(".platformCard").filter(has_text="阅读手记")
    expect(account).to_contain_text("0/1 已报")
    account.get_by_role("button", name="接入设置").click()
    expect(setup.locator("[name=account]")).to_have_value("阅读手记")
    setup.locator("[name=mode]").select_option("csv")
    csv = "日期,粉丝,阅读或播放,互动,发布\n2025-12-01,1200,3100,30,1\n"
    setup.locator("[type=file]").set_input_files({"name":"日报.csv", "mimeType":"text/csv", "buffer": csv.encode()})
    setup.locator("#platformSave").click()
    expect(page.locator("#accountMetrics")).to_contain_text("3,100")
    page.reload()
    expect(account).to_contain_text("阅读手记")
    for platform, account, followers, views in [("微信公众号", "阅读手记", 1238, 3240), ("小红书", "社区观察", 872, 4680)]:
        page.locator("#addReport").click()
        for key, value in dict(platform=platform, account=account, followers=followers, views=views, engagements=36, published=1).items():
            page.locator(f"#reportForm [name={key}]").fill(str(value))
        page.locator("#reportForm .primary").click()
        expect(page.locator("#accountRows")).to_contain_text(account)
    expect(page.locator(".platformCard")).to_have_count(2)
    wechat = page.locator(".platformCard").filter(has_text="微信公众号")
    expect(wechat.locator(".platformReach")).to_contain_text("3,240")
    assert wechat.locator("img").evaluate("img => img.complete && img.naturalWidth > 0")
    wechat.locator("summary").click()
    wechat.get_by_role("button", name="编辑").click()
    expect(page.locator("#reportForm [name=platform]")).to_have_value("微信公众号")
    page.locator("#closeReport").click()
    expect(page.locator("#accountMetrics")).to_contain_text("2,110")
    page.reload()
    expect(page.locator("#accountMetrics")).to_contain_text("7,920")
    if captures:
        page.screenshot(path=str(captures / "account-overview.png"))
    page.locator("#channelNav").click()
    for media, extension in [("text", ".csv"), ("pdf", ".pdf"), ("audio", ".mp3")]:
        page.locator(f"[data-media={media}]").click()
        expect(page.locator("#importForm")).to_be_visible()
        assert extension in page.locator("#file").get_attribute("accept").split(",")
        page.locator("#channelNav").click()
    page.locator(".channelPanel [data-config]").click()
    expect(page.locator("#source")).to_be_visible()
    page.locator("#home").click()
    page.locator("#sourceLightBtn").click()
    expect(page.locator("#sources")).to_contain_text("测试博客")
    page.locator(".kindCard[data-kind=api]").click()
    expect(page.locator("#apiFields")).to_be_visible()
    page.locator("#heroImportBtn").click()
    page.locator(".mediaModeCard[data-accept='.pdf']").click()
    expect(page.locator("#file")).to_have_attribute("accept", ".pdf")
    for filename, title, body in [
        ("访谈记录.csv", "周末服务需求", "读者希望周末增加开馆时间。"),
        ("实地观察.csv", "社区阅读观察", "读者提到周末工作人员数量不足。"),
    ]:
        page.locator("#newRun").click()
        page.locator("#file").set_input_files(
            {
                "name": filename,
                "mimeType": "text/csv",
                "buffer": f"title,body\n{title},{body}\n".encode(),
            }
        )
        expect(page.locator("#importForm [name=name]")).to_have_value(filename)
        page.locator("#previewImport").click()
        expect(page.locator("#previewResult")).to_contain_text("保留 1 条")
        page.get_by_role("button", name="导入并创建运行").click()
        expect(page.locator("#detail")).to_be_visible()
    page.locator("#projectSettings").click()
    page.locator("#connectorKind").select_option("drafts")
    page.locator("#connectorForm [name=url]").fill("https://example.org/approved-drafts")
    page.locator("#connectorForm button").click()
    expect(page.locator("#connectorState")).to_contain_text("连接已保存")
    page.locator("#connectorKind").select_option("reports")
    page.locator("#connectorForm [name=url]").fill("https://example.org/reports")
    page.locator("#connectorForm [name=records_path]").fill("data.items")
    page.locator("#connectorForm [name=map_views]").fill("stats.reads")
    page.locator("#connectorForm button").click()
    expect(page.locator("#connectorPreview")).to_be_enabled()
    expect(page.locator("#connectorForm [name=map_views]")).to_have_value("stats.reads")
    if captures:
        page.locator("#thirdParty").screenshot(path=str(captures / "third-party-settings.png"))
    page.locator("#rules [name=topics]").fill("周末")
    page.locator("#rules button").click()
    expect(page.locator("#notice")).to_contain_text("已保存")
    page.locator("#materialsNav").click()
    checks = page.locator("#library input[type=checkbox]")
    expect(checks).to_have_count(2)
    page.get_by_text("按批次日期筛选（UTC）").click()
    page.get_by_label("结束日期").fill("2000-01-01")
    expect(checks).to_have_count(0)
    page.get_by_label("结束日期").fill("")
    expect(checks).to_have_count(2)
    page.get_by_text("按批次日期筛选（UTC）").click()
    checks.nth(0).check()
    checks.nth(1).check()
    page.get_by_label("新批次名称").fill("社区阅读服务专题")
    page.get_by_role("button", name="合并为新批次").click()
    expect(page.locator("#runName")).to_have_text("社区阅读服务专题")
    for action in ["执行清洗", "提取证据摘录"]:
        page.get_by_role("button", name=action, exact=True).click()
    page.locator("#workNav").click()
    page.locator("#focusSpotlight button").click()
    expect(page.locator("#runName")).to_have_text("社区阅读服务专题")
    page.locator("#workNav").click()
    page.locator("[data-shelf=candidateShelf]").click()
    expect(page.locator("#candidateShelf")).to_contain_text("周末")
    if captures:
        page.screenshot(path=str(captures / "focus-overview.png"))
    page.locator("#candidateShelf button").first.click()
    expect(page.locator(".lib-detail")).to_contain_text("2 个来源")
    expect(page.locator(".lib-evidence")).to_have_count(2)
    if captures:
        page.screenshot(path=str(captures / "topics.png"))
    page.locator(".lib-open").click()
    page.get_by_role("button", name="确认选题", exact=True).click()
    page.get_by_role("button", name="生成草稿", exact=True).click()
    edit = page.locator("#task label textarea").first
    expect(edit).to_be_visible()
    original = edit.input_value()
    edit.fill(original + "\n\n复核：两份原始记录均已核对。")
    page.get_by_role("button", name="保存版本").click()
    expect(page.locator("#task")).to_contain_text("编辑 Markdown · 版本 2")
    page.get_by_text("审核稿件", exact=True).click()
    page.get_by_role("button", name="通过", exact=True).click()
    page.get_by_text("发送到第三方接口", exact=True).click()
    page.get_by_role("button", name="预览发送内容").click()
    expect(page.locator("#detail pre").filter(has_text="https://example.org/approved-drafts")).to_contain_text("draft.approved")
    page.get_by_text("查看已保存版本", exact=True).click()
    first = page.get_by_role("link", name="导出版本 1").get_attribute("href")
    latest = page.get_by_role("link", name="导出已保存版本").get_attribute("href")
    assert page.request.get(url + first).text() == original
    assert "均已核对" in page.request.get(url + latest).text()
    page.reload()
    page.locator("#workNav").click()
    expect(page.locator("#dailyRuns button")).to_have_count(3)
    expect(page.locator("#totalRunsVal")).to_have_text("3 个批次")
    page.locator("#pipeline button").last.click()
    expect(page.locator("#runRows button")).to_have_count(1)
    page.locator(".runsDrawer summary").click()
    page.locator("[data-shelf=outputs]").click()
    expect(page.locator("#outputs")).to_contain_text("版本 2 · 通过")
    expect(page.locator("#batchHistory button")).to_have_count(2)
    if captures:
        page.screenshot(path=str(captures / "overview.png"))
    page.set_viewport_size({"width": 320, "height": 740})
    page.locator("#outputs button").first.click()
    expect(page.locator(".lib-detail")).to_contain_text("社区阅读观察")
    page.get_by_role("button", name="打开原始素材").first.click()
    expect(page.locator(".lib-detail")).to_be_visible()
    page.get_by_text("展开关联选题与稿件").click()
    expect(page.locator(".lib-references")).to_contain_text("关联稿件（1）")
    expect(page.locator(".lib-references button").last).to_contain_text("周末")
    page.locator(".lib-references button").last.click()
    expect(page.locator(".lib-detail")).to_contain_text("版本 2")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if captures:
        page.screenshot(path=str(captures / "mobile-draft.png"))
    page.get_by_role("button", name="返回结果列表").click()
    expect(page.locator(".lib-item").first).to_be_visible()
    page.locator("#projectSettings").click()
    page.locator("#newProject input").fill("独立空项目")
    page.locator("#newProject button").click()
    expect(page.locator("#accountRows")).to_contain_text("还没有账号日报")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.locator("#workNav").click()
    expect(page.locator("#focusSpotlight")).to_contain_text("导入第一份素材")
    expect(page.locator("#recentPanel")).to_be_hidden()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if captures:
        page.screenshot(path=str(captures / "empty-mobile.png"))
    assert not errors, errors


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    captures = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if captures:
        captures.mkdir(parents=True, exist_ok=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory() as folder:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app:app",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=root,
            env=dict(os.environ, DM_DATA_DIR=folder),
        )
        try:
            for attempt in range(50):
                try:
                    httpx.get(url + "/api/setup").raise_for_status()
                    break
                except httpx.ConnectError:
                    if proc.poll() is not None or attempt == 49:
                        raise
                    time.sleep(0.1)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="chrome")
                page = browser.new_page(viewport={"width": 1360, "height": 940})
                page.set_default_timeout(7000)
                verify(page, url, captures)
                print("Browser workflow passed")
        finally:
            proc.terminate()
            proc.wait(timeout=15)
