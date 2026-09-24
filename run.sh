#!/bin/bash
# B站下载器（网页版）启动脚本 —— macOS / Linux
# 首次运行会自动创建虚拟环境并安装依赖，之后双击/运行即可打开浏览器使用。
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/.venv"

if [ ! -x "$VENV/bin/python" ]; then
  echo "首次运行：创建虚拟环境并安装依赖（yt-dlp / imageio-ffmpeg）…"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q -r "$DIR/requirements.txt"
fi

exec "$VENV/bin/python" "$DIR/server.py" "$@"
