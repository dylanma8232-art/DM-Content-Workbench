"""Build a source distribution from explicit public files, without Git metadata or user data."""
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
FILES = '''
README.md README_EN.md CHANGELOG.md PRODUCT.md DESIGN.md documents/工作台全面强化计划.md LICENSE NOTICE assets/overview.png assets/evidence.png assets/draft-review.png requirements.txt requirements-local-ai.txt local_ai.py local_asr.py processing_settings.py online_libraries.py tools/start_local_workbench.py 启动工作台.command Dockerfile compose.yaml .dockerignore .dm.env.example .gitignore
.github/workflows/check.yml tools/build_release.py tests/release_browser.py tests/workflow_overview_browser.py tests/library_browser.py tests/test_platform_account_management.py
tests/collection_media_browser.py tests/collection_extension_browser.py tests/publishing_browser.py tests/long_lists_browser.py
documents/2026-11-28-工作台需求实施进度.xlsx
workflow_engine.py service_credentials.py draft_archive.py account_reporting.py
app.py analysis.py backup.py collection.py connections.py extraction.py integrations.py references.py publishing.py collection_tasks.py collection_reader.py collection_browser.py collection_depth.py content_media.py content_understanding.py content_pipeline.py content_image_store.py tools/image-ocr.py
web/source-workspace.js web/flow-navigation.css web/form-layout.css web/ui-icons.js web/component-polish.css web/date-picker.js web/date-picker.css web/select-control.js web/select-control.css
web/brand/chestnut.webp web/brand/chestnut-avatar.webp web/chestnut-theme.css web/configuration-center.js web/workflow-settings.js web/stage-tools.js web/service-credentials.js web/draft-archive.js
web/index.html web/online-libraries.js web/platform-picker.js web/processing-settings.js web/operator-settings.css web/accounts.js web/dashboard.js web/integrations.js web/library.js web/publishing.js web/publishing.css web/collection-tasks.js web/collection-tasks.css
tools/publish-extension/manifest.json tools/publish-extension/bridge.js tools/publish-extension/background.js
tools/publish-extension/workbench.mjs tools/publish-extension/fill.mjs tools/publish-extension/popup.html tools/publish-extension/popup.js
tools/publish-extension/README.md tools/publish-extension/smoke.test.mjs
tools/publish-extension/douyin-daily.mjs tools/publish-extension/douyin-page.mjs tools/publish-extension/douyin-connect.html tools/publish-extension/douyin-connect.mjs tools/publish-extension/douyin-daily.test.mjs
tools/publish-extension/collection.mjs tools/publish-extension/collection-popup.mjs tools/publish-extension/collect-page.mjs tools/publish-extension/collection.test.mjs tools/publish-extension/collect-detail.mjs tools/publish-extension/detail.test.mjs
web/style.css web/overview.css web/account-overview.css web/workspace.css web/library.css web/workbench-next.css web/workbench-ux.js
web/icons/instagram.svg web/icons/tiktok.svg web/icons/x.svg web/icons/wechat.svg web/icons/xhs.svg web/icons/douyin.svg web/icons/bilibili.svg web/icons/youtube.svg
tests/date-picker-browser.py tests/test_workflow_engine.py tests/test_workflow_settings.py tests/test_service_credentials.py tests/test_draft_archive.py
tests/test_account_daily.py tests/test_workbench.py tests/test_extensions.py tests/test_collection.py tests/test_library.py
tests/test_references.py tests/test_integrations.py tests/test_publishing.py tests/test_collection_tasks.py tests/test_collection_reader.py tests/test_collection_depth.py tests/test_content_media.py tests/test_content_understanding.py tests/test_content_pipeline.py tests/test_local_ai.py tests/test_local_asr.py tests/test_processing_settings.py tests/test_online_libraries.py tests/test_onboarding_collection.py tests/browser_acceptance.py
'''.split()


class EntryAssets(HTMLParser):
    def handle_starttag(self, tag, attrs):
        attribute = {'script': 'src', 'link': 'href', 'img': 'src'}.get(tag)
        url = urlsplit(dict(attrs).get(attribute, ''))
        if url.path and not url.scheme and not url.netloc:
            asset = 'web/' + url.path.lstrip('/')
            if asset not in FILES:
                raise ValueError(f'Entry asset missing from release: {asset}')


def build():
    EntryAssets().feed((ROOT / 'web/index.html').read_text(encoding='utf-8'))
    out = ROOT / 'dist' / 'DM-Content-Workbench.zip'
    out.parent.mkdir(exist_ok=True)
    stage = out.with_suffix('.tmp')
    with ZipFile(stage, 'w') as archive:
        for name in sorted(FILES):
            path = ROOT / name
            if path.resolve() != path or not path.is_file():
                raise ValueError(f'Invalid release file: {name}')
            entry = ZipInfo('DM-Content-Workbench/' + name)
            entry.external_attr = (0o100755 if name.endswith('.command') else 0o100644) << 16
            archive.writestr(entry, path.read_bytes(), compress_type=ZIP_DEFLATED)
    stage.replace(out)
    print(out)


if __name__ == '__main__':
    build()
