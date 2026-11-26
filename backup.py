"""Back up all projects or restore into a fresh workspace directory."""

from contextlib import contextmanager
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


def copy_database(source, destination):
    if not source.is_file():
        raise ValueError("找不到源数据库")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    try:
        origin = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            if not origin.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'"
            ).fetchone():
                raise ValueError("数据库缺少运行记录表")
            replica = sqlite3.connect(destination)
            try:
                origin.backup(replica)
                if replica.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("备份完整性校验失败")
            finally:
                replica.close()
        finally:
            origin.close()
    except Exception:
        destination.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["export", "restore"])
    parser.add_argument("file", type=Path)
    parser.add_argument(
        "--directory",
        type=Path,
        default=None,
    )
    args = parser.parse_args()
    with workspace_path() as (active, _):
        local = (args.directory or active) / 'workbench.sqlite3'
        copy_database(local, args.file) if args.action == 'export' else copy_database(args.file, local)
