#!/bin/bash
# B站下载器 - 打包成 macOS 应用
# 用法：./build.sh
set -e
cd "$(dirname "$0")"

echo "=========================================="
echo "  B站下载器 - 打包成 macOS 应用"
echo "=========================================="
echo

PY=python3
command -v python3 >/dev/null 2>&1 || { echo "[错误] 未找到 python3"; exit 1; }
"$PY" --version

echo
echo "[1/3] 安装依赖..."
"$PY" -m pip install --upgrade pip
"$PY" -m pip install -r requirements.txt
"$PY" -m pip install pyinstaller

echo
echo "[2/3] 正在打包，首次约需 3-8 分钟..."
"$PY" -m PyInstaller bilibili_downloader.spec --clean --noconfirm

echo
echo "[3/3] 检查结果..."
if [ -d "dist/B站下载器.app" ]; then
    echo
    echo "=========================================="
    echo "  打包成功！"
    echo "  产物：dist/B站下载器.app"
    echo "  双击即可运行；下载内容保存在 .app 同目录的 downloads 文件夹。"
    echo "=========================================="
    open dist
else
    echo "[失败] 未找到产物，请查看上方错误信息。"
    exit 1
fi
