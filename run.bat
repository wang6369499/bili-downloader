@echo off
REM B站下载器（网页版）启动脚本 —— Windows
REM 首次运行会自动创建虚拟环境并安装依赖，之后双击即可打开浏览器使用。
setlocal
set "DIR=%~dp0"
set "VENV=%DIR%.venv"

if not exist "%VENV%\Scripts\python.exe" (
  echo 首次运行：创建虚拟环境并安装依赖（yt-dlp / imageio-ffmpeg）…
  python -m venv "%VENV%"
  if errorlevel 1 (
    echo [错误] 未找到 python。请先到 https://www.python.org 安装 Python 3.10+ 并勾选 "Add to PATH"。
    pause
    exit /b 1
  )
  "%VENV%\Scripts\pip.exe" install --upgrade pip
  "%VENV%\Scripts\pip.exe" install -r "%DIR%requirements.txt"
)

"%VENV%\Scripts\python.exe" "%DIR%server.py" %*
endlocal
