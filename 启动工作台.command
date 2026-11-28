#!/bin/bash
set -e
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo '尚未安装工作台运行环境，请按 README 完成安装。'
  read -r -p '按回车关闭。' _dm_close
  exit 1
fi
.venv/bin/python tools/start_local_workbench.py
open 'http://127.0.0.1:8765'
