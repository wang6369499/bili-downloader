# -*- mode: python ; coding: utf-8 -*-
"""B站下载器 —— PyInstaller 打包配置（Windows / macOS 通用）

用法：
    pyinstaller bilibili_downloader.spec --clean --noconfirm

产物：
    Windows -> dist/B站下载器.exe
    macOS   -> dist/B站下载器.app

注意：
    PyInstaller 不能跨平台打包 —— 要得到 Windows 的 .exe，必须在 Windows 上执行；
    要得到 macOS 的 .app，必须在 macOS 上执行。
"""
import sys
from PyInstaller.utils.hooks import collect_all

datas = [('web', 'web')]
binaries = []
hiddenimports = []

# yt-dlp 的 extractor 是懒加载的，必须整体收集，否则运行时报 "Unsupported URL"
# imageio-ffmpeg 自带 ffmpeg 二进制（合并 MP4 必需），也要一起打包
for _pkg in ('yt_dlp', 'imageio_ffmpeg'):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h


a = Analysis(
    ['server.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'PySide6', 'PyQt5', 'PyQt6', 'IPython', 'pytest', 'notebook'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='B站下载器',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # 不依赖 upx，避免环境差异导致失败
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,            # 保留控制台窗口，显示服务地址与运行日志
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# macOS 下再包一层 .app，方便双击运行
if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='B站下载器.app',
        icon=None,
        bundle_identifier='local.bili.downloader',
    )
