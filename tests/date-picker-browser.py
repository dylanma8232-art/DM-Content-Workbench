"""Standalone component checks: .venv/bin/python tests/date-picker-browser.py."""
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        for timezone in ("America/Los_Angeles", "Asia/Shanghai"):
            context = browser.new_context(viewport={"width": 320, "height": 720}, timezone_id=timezone)
            page = context.new_page()
            page.set_content('''<!doctype html><html lang="zh"><body style="margin:8px">
              <form id="form"><label style="display:block;text-align:right">统计日期
              <input id="day" name="day" type="date" value="2024-02-28"
                min="2024-02-15" max="2024-03-03"></label></form>
              <form id="range"><input type="hidden" id="accountStartDate" value="2024-02-26">
              <input type="date" id="accountEndDate" data-range-start="accountStartDate" value="2024-02-29"></form>
              <button id="outside" style="margin-top:480px">其他操作</button></body></html>''')
            page.add_style_tag(path=str(ROOT / "web/chestnut-theme.css"))
            page.add_style_tag(path=str(ROOT / "web/date-picker.css"))
            page.add_script_tag(path=str(ROOT / "web/date-picker.js"))
            page.evaluate('''() => {
              window.events = [];
              for (const name of ['input', 'change']) document.querySelector('#form')
                .addEventListener(name, e => events.push([e.type, e.target.id, e.target.value]));
            }''')
            field = page.locator("#day")
            trigger = page.locator("#form").get_by_role("button", name="选择日期", exact=True)
            panel = page.get_by_role("dialog", name="选择日期")
            expect(field).to_be_visible()
            assert field.get_attribute("type") == "date"
            field.click()
            expect(panel).to_be_visible()
            expect(panel.get_by_role("gridcell", name="2024年2月14日", exact=True)).to_be_disabled()
            expect(panel.get_by_role("button", name="上个月")).to_be_disabled()
            panel.get_by_role("gridcell", name="2024年2月29日", exact=True).click()
            expect(field).to_have_value("2024-02-29")
            expect(trigger).to_be_focused()
            assert page.evaluate("events") == [["input", "day", "2024-02-29"], ["change", "day", "2024-02-29"]]
            assert page.evaluate("new FormData(document.querySelector('form')).get('day')") == "2024-02-29"
            trigger.click()
            page.keyboard.press("ArrowRight")
            expect(panel.get_by_role("gridcell", name="2024年3月1日", exact=True)).to_be_focused()
            page.keyboard.press("Enter")
            expect(field).to_have_value("2024-03-01")
            trigger.click()
            expect(panel.get_by_role("gridcell", name="2024年3月4日", exact=True)).to_be_disabled()
            expect(panel.get_by_role("button", name="下个月")).to_be_disabled()
            page.keyboard.press("ArrowUp")
            expect(panel.get_by_role("gridcell", name="2024年2月23日", exact=True)).to_be_focused()
            page.keyboard.press("Escape")
            expect(trigger).to_be_focused()
            expect(panel).not_to_be_visible()
            # Existing Playwright fill and native events still work without custom APIs.
            field.fill("2024-03-02")
            field.press("Tab")
            expect(field).to_have_value("2024-03-02")
            trigger.click()
            expect(panel.locator('[aria-selected="true"]')).to_have_attribute("data-date", "2024-03-02")
            panel.get_by_label("月份", exact=True).select_option("1")
            expect(panel.get_by_role("gridcell", name="2024年2月29日", exact=True)).to_be_visible()
            box = panel.bounding_box()
            assert 0 <= box["x"] and box["x"] + box["width"] <= 320
            assert page.evaluate("document.documentElement.scrollWidth <= 320")
            page.screenshot(path=f"/tmp/dm-date-picker-{timezone.split('/')[-1]}.png")
            page.locator("#outside").click()
            expect(panel).not_to_be_visible()
            expect(page.locator("#outside")).to_be_focused()
            # New nodes are automatically enhanced; selection does not spill into other fields.
            page.evaluate('''() => {
              const input = document.createElement('input');
              input.type = 'date'; input.id = 'dynamic'; input.value = '2023-02-28';
              document.body.append(input);
            }''')
            dynamic = page.locator(".dm-date-field").filter(has=page.locator("#dynamic"))
            dynamic.get_by_role("button", name="选择日期").click()
            assert page.locator(".dm-date-picker").count() == 1
            expect(panel.get_by_role("gridcell", name="2023年2月29日", exact=True)).to_have_count(0)
            panel.get_by_label("年份", exact=True).select_option("2024")
            panel.get_by_role("gridcell", name="2024年2月29日", exact=True).click()
            expect(page.locator("#dynamic")).to_have_value("2024-02-29")
            expect(field).to_have_value("2024-03-02")
            page.set_viewport_size({"width": 1280, "height": 800})
            dynamic.get_by_role("button", name="选择日期").click()
            page.screenshot(path=f"/tmp/dm-date-picker-desktop-{timezone.split('/')[-1]}.png")
            panel.get_by_role("button", name="今天", exact=True).click()
            local_today = page.evaluate("() => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; }")
            expect(page.locator("#dynamic")).to_have_value(local_today)
            field.focus()
            field.press("Alt+ArrowDown")
            expect(panel).to_be_visible()
            page.keyboard.press("Escape")
            # Same date does not emit a spurious change, including in delegated handlers.
            before = page.evaluate("events.length")
            trigger.click()
            panel.get_by_role("gridcell", name="2024年3月2日", exact=True).click()
            assert page.evaluate("events.length") == before
            trigger.click()
            page.evaluate("document.querySelector('#day').closest('.dm-date-field').remove()")
            expect(panel).not_to_be_visible()
            start = page.locator('#accountStartDate')
            end = page.locator('#accountEndDate')
            button = page.locator('#range').get_by_role('button')
            dialog = page.get_by_role('dialog', name='选择日期范围', exact=True)
            expect(button).to_have_text('2024-02-26 至 2024-02-29')
            expect(end).to_be_hidden()
            page.evaluate('''() => {
              window.ranges = [];
              document.querySelector('#range').addEventListener('change', e =>
                ranges.push([e.target.id, accountStartDate.value, accountEndDate.value]));
            }''')
            button.press('Enter')
            feb29 = dialog.locator('[data-date="2024-02-29"]')
            expect(feb29).to_be_focused()
            expect(dialog.locator('[aria-selected="true"]')).to_have_count(4)
            feb29.press('Space')
            expect(feb29).to_be_focused()
            expect(start).to_have_value('2024-02-26')
            page.keyboard.press('Escape')
            expect(button).to_be_focused()
            expect(dialog).to_be_hidden()
            assert page.evaluate('ranges') == []
            button.click(position={'x': 5, 'y': 10})
            feb29.press('ArrowRight')
            page.keyboard.press('Enter')
            expect(end).to_have_value('2024-02-29')
            page.keyboard.press('ArrowLeft')
            page.keyboard.press('Enter')
            expect(button).to_be_focused()
            expect(button).to_have_text('2024-02-29 至 2024-03-01')
            assert page.evaluate('ranges') == [['accountEndDate', '2024-02-29', '2024-03-01']]
            button.click()
            march2 = dialog.locator('[data-date="2024-03-02"]')
            march2.click()
            march2.click()
            expect(button).to_have_text('2024-03-02')
            assert page.evaluate('ranges[1]') == ['accountEndDate', '2024-03-02', '2024-03-02']
            button.click()
            march2.click()
            dialog.get_by_role('button', name='今天').click()
            assert page.evaluate('ranges[2]') == ['accountEndDate', local_today, local_today]
            end.evaluate('''input => {
              accountStartDate.value = '2025-01-02'; input.value = '2025-01-08';
              window.refreshDateRange(input);
            }''')
            expect(button).to_have_accessible_name('选择日期范围，2025-01-02 至 2025-01-08')
            assert page.evaluate('ranges.length') == 3
            context.close()
        browser.close()
    print("PASS: leap day, local timezones, min/max, month/year, keyboard, events, dynamic inputs, inclusive ranges, cancellation, today, label refresh, 320px")


if __name__ == "__main__":
    run()
