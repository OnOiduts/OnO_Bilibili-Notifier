#!/bin/bash
cd "$(dirname "$0")"

echo "========================================================"
echo " B 站提醒机器人 - Mac / Linux 一键启动"
echo "========================================================"

PY=python3
command -v python3 >/dev/null 2>&1 || PY=python

if ! command -v $PY >/dev/null 2>&1; then
  echo "[X] 没检测到 Python，请先安装 Python 3.8 以上版本"
  exit 1
fi

echo "[1/3] 安装/检查依赖..."
$PY -m pip install -q -r src/requirements.txt

echo "[2/3] 检查配置文件..."
# 配置与数据都在程序目录之外（默认 ~/.bili-notify），删文件夹不会丢

echo "[3/3] 启动中... 浏览器打开 http://127.0.0.1:8088"
echo "--------------------------------------------------------"
$PY src/start.py
