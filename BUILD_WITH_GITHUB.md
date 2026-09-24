# 用 GitHub 生成 Windows exe（免安装 Python、免 Windows 电脑）

本机是 macOS，而 PyInstaller **不能跨平台打包**，所以在 Mac 上永远打不出 `.exe`。
GitHub Actions 提供免费的 Windows 云主机，可以在那里打包，再把 exe 下载回来。

**全程在 GitHub 网页操作，不用敲任何命令，也不用安装 git。**

---

## 第一步：创建仓库

1. 打开 <https://github.com/new>
2. Repository name 填：`bili-downloader`
3. 建议选 **Private（私有）** —— 保护你的代码和 cookie
4. 勾选 **Add a README file**
5. 点 **Create repository**

## 第二步：上传代码

在仓库页面点 **Add file → Upload files**，把 **`bilibili_downloader/github_upload/`** 里的文件**全部拖进去**。

> 我已经准备好了一个干净的上传包：`github_upload/`（约 100K）。
> **所有文件都平铺在根目录，没有子文件夹** —— 因为 GitHub 网页上传不支持子目录，
> 界面已内联进 `web_assets.py`，不再需要 `web/` 文件夹。

```
必传（缺一不可）：
  core.py
  server.py
  web_assets.py            ← 网页界面（已内联，替代 web/index.html）
  requirements.txt
  bilibili_downloader.spec

建议一起传：
  build_assets.py          ← 改界面后重新生成 web_assets.py 用
  .gitignore
  README.md
  run.sh  run.bat  build.bat  build.sh
```

### ⚠️ 千万别上传这几个

| 文件 | 原因 |
|---|---|
| **`cookies.txt`** | **里面有你的 B站登录凭证，上传等于把账号交给别人** |
| `config.json` | 同样含登录配置 |
| `downloads/` | 你下载的视频，体积大且无关 |
| `dist/` `build/` | 打包产物，几百 MB |
| `__pycache__/` | 缓存文件 |

> 网页上传时请手动确认没有勾上 `cookies.txt`。仓库若已误传，立刻删掉并去 B站修改密码。

5. 点 **Commit changes**

## 第三步：创建构建流程

1. 点仓库顶部的 **Actions** 标签
2. 如果看到 "set up a workflow yourself" 就点它；否则点左侧 **New workflow** → 再点 **set up a workflow yourself**
3. 把文件名改成 `build.yml`
4. **删掉默认内容**，粘贴下面这段：

```yaml
name: 打包 B站下载器

on:
  push:
    branches: [main, master]
  workflow_dispatch:

jobs:
  build-windows:
    name: 生成 Windows exe
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: 安装依赖
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install pyinstaller
      - name: 打包
        run: pyinstaller bilibili_downloader.spec --clean --noconfirm
      - name: 上传 exe
        uses: actions/upload-artifact@v4
        with:
          name: B站下载器-Windows-exe
          path: dist/*.exe
          if-no-files-found: error
```

5. 点 **Commit changes**（右上角）

> 这个文件我也放在了 `.github/workflows/build.yml`，如果你会用 git 命令行，直接 `git push` 即可，不用手动创建。

## 第四步：等构建完成

提交后会自动开始构建。点 **Actions** 标签，能看到一个正在转圈的运行记录（约 **3–6 分钟**）。

绿色 ✅ = 成功，红色 ❌ = 失败（点进去看日志，把错误发给我）。

## 第五步：下载 exe

1. 点进那次成功的运行记录
2. 页面最下方 **Artifacts** 区域
3. 点 **`B站下载器-Windows-exe`** 下载（是个 zip）
4. 解压得到 **`B站下载器.exe`**

双击即可运行，会自动打开浏览器界面。下载的视频保存在 exe 旁边的 `downloads` 文件夹。

---

## 常见问题

**Q：要钱吗？**
免费。GitHub 对公开仓库完全免费；私有仓库每月有 2000 分钟额度，打一次包约 5 分钟，够用很多次。

**Q：以后改了代码怎么重新打包？**
在 Actions 页面点 **Run workflow** 就能手动再构建一次，不用重新上传。

**Q：exe 会被杀毒软件报毒吗？**
PyInstaller 打包的程序常被误报，属普遍现象。加到杀软白名单即可（源码都在仓库里，可自行审查）。

**Q：界面文件在哪？我没看到 `web` 文件夹。**
界面已经内联进 `web_assets.py` 了，这样 GitHub 网页上传才不会丢文件（网页上传不支持子文件夹）。
要改界面，就改本地的 `web/index.html`，然后运行 `python build_assets.py` 重新生成 `web_assets.py` 再上传。
程序运行时：有 `web/index.html` 就用它，没有就用内联的那份。

**Q：能同时打 Mac 版吗？**
`.github/workflows/build.yml` 里已包含一个 macOS 构建任务，会额外产出 `B站下载器-macOS-app`。不需要的话可以不管。
