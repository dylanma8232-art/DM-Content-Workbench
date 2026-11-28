"""Back up all projects or restore into a fresh workspace directory."""

from contextlib import closing, contextmanager
import argparse
import os
import sqlite3
from pathlib import Path


@contextmanager
def workspace_path(exclusive=False):
    root = Path(os.getenv('DM_DATA_DIR', Path(__file__).parent / '.dm-data')).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    registry = sqlite3.connect(root / 'storage-settings.sqlite3', timeout=30)
    try:
        with registry:
            registry.execute('CREATE TABLE IF NOT EXISTS location (id INTEGER PRIMARY KEY, folder TEXT NOT NULL)')
            registry.execute('BEGIN EXCLUSIVE' if exclusive else 'BEGIN')
            row = registry.execute('SELECT folder FROM location WHERE id=1').fetchone()
            folder = Path(row[0]) if row else root
            if row and not (folder / 'workbench.sqlite3').is_file():
                raise FileNotFoundError('已配置的数据卷或数据库不可用，请检查存储目录')
            yield folder, registry
    finally:
        registry.close()


def open_source(source):
    if not source.is_file():
        raise ValueError("找不到源数据库")
    return sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)


def verify_database(source):
    with closing(open_source(source)) as conn:
        if any(row[0] != 'ok' for row in conn.execute('PRAGMA quick_check')):
            raise ValueError('备份完整性校验失败')
        columns = {row[1] for row in conn.execute('PRAGMA table_info(runs)')}
        if not {'id', 'name', 'stage', 'payload', 'created'} <= columns:
            raise ValueError('数据库缺少工作台运行记录字段')


def copy_database(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        with closing(open_source(source)) as origin, closing(sqlite3.connect(destination)) as replica:
            origin.backup(replica)
        verify_database(destination)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["export", "restore", "verify", "location"])
    parser.add_argument("file", type=Path, nargs='?')
    parser.add_argument(
        "--directory",
        type=Path,
        default=None,
    )
    args = parser.parse_args()
    if args.action != 'location' and args.file is None:
        parser.error('请指定备份文件路径')

    def transfer(directory):
        local = directory / 'workbench.sqlite3'
        if args.action == 'export':
            copy_database(local, args.file)
        else:
            copy_database(args.file, local)

    try:
        if args.action == 'verify':
            verify_database(args.file)
        elif args.action == 'location':
            with workspace_path() as (active, _):
                print(active / 'workbench.sqlite3')
        elif args.directory:
            transfer(args.directory)
        else:
            with workspace_path() as (active, _):
                transfer(active)
        if args.action != 'location':
            print('数据库完整性与运行记录字段校验通过')
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f'操作失败：{exc}\n')
