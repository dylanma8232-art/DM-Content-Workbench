"""Start the workbench and its local AI engine. Bind both services to this Mac only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


def get(url):
    try:
        response = httpx.get(url, timeout=2)
        if response.status_code == 200:
            return response.json()
    except (httpx.HTTPError, ValueError):
        pass
    return None


def start_service(folder, name, python, application, port, health_path, matches):
    url = f'http://127.0.0.1:{port}'
    health = get(url + health_path)
    if health is not None:
        if not isinstance(health, dict) or not matches(health):
            raise RuntimeError(f'端口 {port} 已由其他服务使用')
        return {'url': url, 'status': 'running'}
    if not python.is_file():
        raise RuntimeError(f'缺少 {python.relative_to(ROOT)}，请按 README 安装运行环境')
    log_path = folder / f'{name}-service.log'
    with log_path.open('ab') as log:
        process = subprocess.Popen([str(python), '-m', 'uvicorn', application,
            '--host', '127.0.0.1', '--port', str(port), '--workers', '1'],
            cwd=ROOT, env=os.environ.copy(), stdout=log, stderr=log, start_new_session=True)
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'{name} 启动失败，请查看 {log_path.name}')
        health = get(url + health_path)
        if isinstance(health, dict) and matches(health):
            return {'url': url, 'status': 'running', 'pid': process.pid}
        time.sleep(.3)
    raise RuntimeError(f'{name} 启动超时，请查看 {log_path.name}')


def start():
    load_dotenv(ROOT / '.dm.env', override=True)
    folder = ROOT / '.dm-data'
    folder.mkdir(exist_ok=True)
    receipt = {'workbench': start_service(folder, 'workbench', ROOT / '.venv/bin/python',
        'app:app', 8765, '/api/setup', lambda health: 'project_id' in health and 'complete' in health)}
    try:
        receipt['engine'] = start_service(folder, 'engine', ROOT / '.venv-local-ai/bin/python',
            'local_ai:app', 8770, '/health', lambda health: health.get('service') == 'dm-local-ai')
    except (RuntimeError, OSError) as exc:
        receipt['engine'] = {'status': 'unavailable', 'detail': str(exc)}
        print(f'工作台已就绪，可先导入文字素材。本机 AI 引擎暂不可用：{exc}', file=sys.stderr)
    (folder / 'local-services.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return receipt


if __name__ == '__main__':
    start()
