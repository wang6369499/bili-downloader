@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ==========================================
echo   B站下载器 - 打包成 exe (Windows)
echo ==========================================
echo.

REM ---- 1. 检查 Python ----
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 没找到 Python。
    echo        请先安装 Python 3.10 以上版本，安装时务必勾选 "Add Python to PATH"。
    echo        官网：https://www.python.org/downloads/
    pause
    exit /b 1
)
python --version

REM ---- 2. 安装依赖 ----
echo.
echo [1/3] 安装依赖（yt-dlp / imageio-ffmpeg / pyinstaller）...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller
if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络后重试。
    pause
    exit /b 1
)

REM ---- 3. 打包 ----
echo.
echo [2/3] 正在打包，首次约需 3-8 分钟，请耐心等待...
python -m PyInstaller bilibili_downloader.spec --clean --noconfirm

REM ---- 4. 结果 ----
echo.
echo [3/3] 检查结果...
if exist "dist\B站下载器.exe" (
    echo.
    echo ==========================================
    echo   打包成功！
    echo   产物：dist\B站下载器.exe
    echo.
    echo   双击它即可运行，会自动打开浏览器界面。
    echo   下载的视频保存在 exe 同目录的 downloads 文件夹。
    echo   把这个 exe 复制到任何 Windows 电脑都能直接运行。
    echo ==========================================
    explorer "dist"
) else (
    echo [失败] 没有找到产物，请把上面的错误信息发给我。
)

echo.
pause
