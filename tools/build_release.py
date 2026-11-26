"""Build a source distribution from explicit public files, without Git metadata or user data."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
FILES = '''
README.md README_EN.md LICENSE NOTICE assets/evidence.png requirements.txt Dockerfile compose.yaml .dockerignore .dm.env.example .gitignore
.github/workflows/check.yml tools/build_release.py
app.py analysis.py backup.py collection.py connections.py extraction.py integrations.py references.py
web/index.html web/accounts.js web/dashboard.js web/integrations.js web/library.js
web/style.css web/overview.css web/workspace.css web/library.css
web/icons/wechat.svg web/icons/xhs.svg web/icons/douyin.svg web/icons/bilibili.svg web/icons/youtube.svg
tests/test_workbench.py tests/test_extensions.py tests/test_collection.py tests/test_library.py
tests/test_references.py tests/test_integrations.py tests/browser_acceptance.py
'''.split()


def build():
    out = ROOT / 'dist' / 'DM-Content-Workbench.zip'
    out.parent.mkdir(exist_ok=True)
    stage = out.with_suffix('.tmp')
    with ZipFile(stage, 'w') as archive:
        for name in sorted(FILES):
            path = ROOT / name
            if path.resolve() != path or not path.is_file():
                raise ValueError(f'Invalid release file: {name}')
            entry = ZipInfo('DM-Content-Workbench/' + name)
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, path.read_bytes(), compress_type=ZIP_DEFLATED)
    stage.replace(out)
    print(out)


if __name__ == '__main__':
    build()
