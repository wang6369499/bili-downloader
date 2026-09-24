# -*- coding: utf-8 -*-
"""
B站下载核心（供 Web 服务调用）
================================
- 任务管理（队列 + 并发线程）
- 进度回调（写入线程安全的任务状态）
- 复用「优先 H.264 + merge 成 MP4」的选流策略

ffmpeg 由 imageio-ffmpeg 自动提供（首次使用按平台下载），无需本机安装。
"""

import os
import re
import sys
import json
import time
import hashlib
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor


HERE = os.path.dirname(os.path.abspath(__file__))


def resource_dir():
    """只读资源目录（web 页面等）：打包后指向 PyInstaller 的临时解压目录。"""
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return HERE


def app_dir():
    """可写数据目录（cookies / config / downloads）。

    打包后必须落在 exe（或 .app）旁边，不能用 __file__ 所在目录 ——
    onefile 模式下那是临时目录，程序退出即被清空，cookie 和下载的视频会全部丢失。
    """
    if getattr(sys, "frozen", False):
        exe = os.path.abspath(sys.executable)
        if sys.platform == "darwin" and os.path.basename(os.path.dirname(exe)) == "MacOS":
            # .app 内：<...>/X.app/Contents/MacOS/X -> 取 .app 所在目录
            return os.path.dirname(os.path.dirname(os.path.dirname(exe)))
        # 单文件 exe：就放 exe 旁边
        return os.path.dirname(exe)
    return HERE


def locate_ffmpeg():
    """按优先级定位 ffmpeg：本地 bin/ > imageio-ffmpeg > 系统 PATH。"""
    local = os.path.join(HERE, "bin", "ffmpeg")
    if os.path.isfile(local):
        return local
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:
        pass
    import shutil
    p = shutil.which("ffmpeg")
    if p:
        return p
    return None


def normalize_url(arg):
    """把 BV号 / av号 / 短链接补全成完整播放页 URL。"""
    a = arg.strip()
    if a.startswith("http://") or a.startswith("https://"):
        return a
    if "bilibili.com" in a or "b23.tv" in a:
        return ("https://" + a) if a.startswith("//") else a
    if re.match(r"(?i)^BV[0-9A-Za-z]+$", a) or re.match(r"(?i)^av\d+$", a):
        return "https://www.bilibili.com/video/" + a
    return a


def parse_rate(s):
    """把 2M / 500K / 1.5M 解析为字节/秒整数。"""
    if not s:
        return None
    s = s.strip().upper()
    mult = 1
    if s.endswith("K"):
        mult = 1024
        s = s[:-1]
    elif s.endswith("M"):
        mult = 1024 * 1024
        s = s[:-1]
    elif s.endswith("G"):
        mult = 1024 ** 3
        s = s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return None


class _Cancel(Exception):
    """在进度回调中抛出以中止当前下载。"""
    pass


class _RiskControl(Exception):
    """B站风控/限流（352 / 401 / 412）：命中后不宜继续重试，避免加重限流。"""
    pass


class Task:
    def __init__(self, tid, url):
        self.id = tid
        self.url = url
        self.title = "(等待解析…)"
        self.status = "queued"  # queued|downloading|merging|finished|error|canceled
        self.progress = 0.0
        self.downloaded = 0
        self.total = 0
        self.speed = 0
        self.eta = 0
        self.filename = ""
        self.error = ""

    def to_dict(self):
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "status": self.status,
            "progress": round(self.progress, 1),
            "downloaded": self.downloaded,
            "total": self.total,
            "speed": self.speed,
            "eta": self.eta,
            "filename": self.filename,
            "error": self.error,
        }


class DownloadManager:
    def __init__(self, max_workers=2, default_output="downloads"):
        self.tasks = {}
        self.order = []
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.default_output = os.path.abspath(os.path.expanduser(default_output))
        os.makedirs(self.default_output, exist_ok=True)
        self.ffmpeg = locate_ffmpeg()
        self._seq = 0

    # ---------- 任务管理 ----------
    def add(self, url, options):
        with self.lock:
            self._seq += 1
            tid = str(self._seq)
            t = Task(tid, url)
            self.tasks[tid] = t
            self.order.append(tid)
        self.executor.submit(self._run, tid, url, options)
        return tid

    def cancel(self, tid):
        with self.lock:
            t = self.tasks.get(tid)
            if t and t.status in ("queued", "downloading", "merging"):
                t.status = "canceled"
                return True
            return False

    def clear_finished(self):
        with self.lock:
            remove = [tid for tid, t in self.tasks.items()
                      if t.status in ("finished", "error", "canceled")]
            for tid in remove:
                del self.tasks[tid]
                self.order.remove(tid)

    def set_default_output(self, path):
        with self.lock:
            self.default_output = os.path.abspath(os.path.expanduser(path))
            os.makedirs(self.default_output, exist_ok=True)

    def list_tasks(self):
        with self.lock:
            return [self.tasks[tid].to_dict() for tid in self.order if tid in self.tasks]

    # ---------- 下载执行 ----------
    def _make_hook(self, tid):
        def hook(d):
            with self.lock:
                t = self.tasks.get(tid)
                if not t:
                    return
                if t.status == "canceled":
                    raise _Cancel()
                status = d.get("status")
                info = d.get("info_dict") or {}
                if info.get("title"):
                    t.title = info["title"]
                if status == "downloading":
                    t.status = "downloading"
                    t.downloaded = d.get("downloaded_bytes", 0) or 0
                    t.total = d.get("total_bytes") or d.get("total_bytes_estimate", 0) or 0
                    t.speed = d.get("speed") or 0
                    t.eta = d.get("eta") or 0
                    if t.total:
                        t.progress = min(100.0, t.downloaded / t.total * 100)
                    if d.get("filename"):
                        t.filename = d.get("filename")
                elif status == "finished":
                    t.status = "merging"
                    if d.get("filename"):
                        t.filename = d.get("filename")
        return hook

    def _build_opts(self, tid, options):
        out = options.get("output") or self.default_output
        out = os.path.abspath(os.path.expanduser(out))
        os.makedirs(out, exist_ok=True)
        noplaylist = options.get("noplaylist", True)
        # 单视频不拼 playlist_index，否则 yt-dlp 会把缺失占位渲染成字面量 "NA"
        if noplaylist:
            outtmpl = os.path.join(out, "%(title)s [%(id)s].%(ext)s")
        else:
            outtmpl = os.path.join(out, "%(playlist_index)02d - %(title)s [%(id)s].%(ext)s")
        opts = {
            "outtmpl": outtmpl,
            "ffmpeg_location": self.ffmpeg,
            "merge_output_format": "mp4",
            "restrictfilenames": False,
            "noplaylist": noplaylist,
            "writethumbnail": False,
            "writeinfojson": False,
            "progress_hooks": [self._make_hook(tid)],
            "retries": 5,
            "fragment_retries": 5,
            "socket_timeout": 30,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
            "noprogress": True,
        }

        if options.get("limit"):
            r = parse_rate(options["limit"])
            if r:
                opts["ratelimit"] = r
        if options.get("cookies"):
            opts["cookiefile"] = os.path.expanduser(options["cookies"])
        if options.get("cookies_from_browser"):
            opts["cookiesfrombrowser"] = (options["cookies_from_browser"],)

        # 字幕（默认不下载，勾选才下，减小体积）
        if not options.get("no_sub", True):
            opts["writesubtitles"] = True
            opts["writeautomaticsub"] = True
            opts["subtitleslangs"] = ["zh-Hans", "zh-CN", "chi", "zh"]
            opts["postprocessors"] = [{
                "key": "FFmpegEmbedSubtitle",
                "already_have_subtitle": False,
            }]
        else:
            opts["postprocessors"] = []

        audio_only = options.get("audio_only", False)
        quality = options.get("quality")
        fmt = options.get("format")
        if audio_only:
            opts["format"] = "ba/bestaudio"
            opts["postprocessors"].append({
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            })
            opts["merge_output_format"] = None
        elif fmt:
            opts["format"] = fmt
        elif quality:
            # vcodec 过滤必须放在 height 之前，否则会先固化成 AV1 最佳画质导致失效
            opts["format"] = (f"bestvideo[vcodec~=avc1][height<={quality}]+bestaudio"
                              f"/bestvideo[height<={quality}]+bestaudio/best")
        else:
            opts["format"] = "bestvideo[vcodec~=avc1]+bestaudio/bestvideo+bestaudio/best"
        return opts

    def _run(self, tid, url, options):
        try:
            import yt_dlp
        except ImportError:
            with self.lock:
                t = self.tasks.get(tid)
                if t:
                    t.status = "error"
                    t.error = "未安装 yt-dlp，请先运行启动脚本安装依赖。"
            return

        if not self.ffmpeg:
            with self.lock:
                t = self.tasks.get(tid)
                if t:
                    t.status = "error"
                    t.error = "未找到 ffmpeg，无法合成 MP4。"
            return

        try:
            ydl_opts = self._build_opts(tid, options)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([normalize_url(url)])
            with self.lock:
                t = self.tasks.get(tid)
                if t and t.status != "canceled":
                    t.status = "finished"
                    t.progress = 100.0
                    t.speed = 0
                    t.eta = 0
        except _Cancel:
            with self.lock:
                t = self.tasks.get(tid)
                if t:
                    t.status = "canceled"
        except yt_dlp.utils.DownloadError as e:
            with self.lock:
                t = self.tasks.get(tid)
                if t and t.status != "canceled":
                    t.status = "error"
                    msg = str(e).splitlines()[-1] if str(e) else "下载失败"
                    if "cookies" in msg.lower() or "login" in msg.lower():
                        msg += "（高画质/会员视频需要登录态，请在设置里添加 cookie）"
                    t.error = msg
        except Exception as e:
            with self.lock:
                t = self.tasks.get(tid)
                if t and t.status != "canceled":
                    t.status = "error"
                    t.error = str(e)


# ---------------------------------------------------------------------------
# UP 主视频列表：直连 B站空间 API（WBI 签名）
# 说明：yt-dlp 的空间提取器在 extract_flat 模式下只返回 url_result（无 title），
#       所以旧实现拿不到标题。这里改为直接调用 x/space/wbi/arc/search，
#       一次请求即可拿到 vlist 的完整字段（title / length / play / created）。
# ---------------------------------------------------------------------------

# B站 WBI 签名用的置换表（64 位）
_WBI_MIXIN_TAB = (
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
)

_BILI_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Referer": "https://space.bilibili.com/",
    "Origin": "https://space.bilibili.com",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _wbi_mixin_key(img_url, sub_url):
    """由 nav 接口的 img_url/sub_url 推导 mixinKey（32 位）。"""
    raw = (os.path.basename(img_url).split(".")[0]
           + os.path.basename(sub_url).split(".")[0])
    return "".join(raw[i] for i in _WBI_MIXIN_TAB)[:32]


def _wbi_signed_query(params, img_url, sub_url):
    """对查询参数做 WBI 签名，返回最终 query string（含 wts 与 w_rid）。"""
    key = _wbi_mixin_key(img_url, sub_url)
    p = dict(params)
    p["wts"] = int(time.time())
    items = sorted(p.items())
    # 值里需剔除 ! ' ( ) 四个字符，再做 urlencode
    qs = urllib.parse.urlencode(
        [(k, re.sub(r"[!'()]", "", str(v))) for k, v in items])
    w_rid = hashlib.md5((qs + key).encode("utf-8")).hexdigest()
    return qs + "&w_rid=" + w_rid


def _parse_duration(s):
    """把 'MM:SS' / 'HH:MM:SS' 解析为秒；失败返回 None。"""
    if not s:
        return None
    try:
        parts = [int(x) for x in str(s).split(":")]
    except (ValueError, TypeError):
        return None
    sec = 0
    for x in parts:
        sec = sec * 60 + x
    return sec


def _fetch_up_list_via_api(mid, limit, ydl):
    """直连 B站空间 API 拉取视频列表（含标题）。

    复用传入 ydl 的 cookie/请求栈（ydl.urlopen），因此设置里的 cookie 会生效。
    成功返回 (videos, uploader)；任何异常上抛，由调用方回退到 yt-dlp 方案。
    """
    try:
        from yt_dlp.networking.common import Request as _YDRequest
    except Exception:
        _YDRequest = None

    def _get_json(url):
        if _YDRequest is not None:
            req = _YDRequest(url, headers=_BILI_HEADERS)
        else:
            req = urllib.request.Request(url, headers=_BILI_HEADERS)
        try:
            resp = ydl.urlopen(req)
        except Exception as e:  # HTTP 412 等会被网络层抛出
            if "412" in str(e) or "blocked" in str(e).lower():
                raise _RiskControl("B站风控拦截（HTTP 412），请稍后重试或配置 cookie")
            raise
        return json.loads(resp.read().decode("utf-8"))

    nav = _get_json("https://api.bilibili.com/x/web-interface/nav")
    wbi = (nav.get("data") or {}).get("wbi_img") or {}
    img_url, sub_url = wbi.get("img_url"), wbi.get("sub_url")
    if not img_url or not sub_url:
        raise RuntimeError("nav 接口未返回 wbi_img，无法签名")

    uploader = ((nav.get("data") or {}).get("uname") or "")
    videos = []
    page = 1
    while len(videos) < limit and page <= 20:
        query = _wbi_signed_query({
            "mid": mid, "ps": 30, "pn": page, "tid": 0, "keyword": "",
            "order": "pubdate", "order_avoided": "true", "platform": "web",
            "web_location": "333.1387", "special_type": "", "index": 0,
        }, img_url, sub_url)
        data = _get_json(
            "https://api.bilibili.com/x/space/wbi/arc/search?" + query)
        code = data.get("code")
        if code == -352:
            raise _RiskControl("B站风控拒绝了该请求（错误 352）")
        if code == -401:
            raise _RiskControl("B站风控拦截（错误 401），请稍后重试")
        if code != 0:
            raise RuntimeError(
                f"接口返回错误 {code}：{data.get('message') or '未知'}")
        lst = (data.get("data") or {}).get("list") or {}
        vlist = lst.get("vlist") or []
        if not vlist:
            break
        for v in vlist:
            if v.get("is_union_video") in (1,) or not v.get("bvid"):
                continue
            bvid = v["bvid"]
            created = v.get("created")
            videos.append({
                "bvid": bvid,
                "title": v.get("title") or "(无标题)",
                "duration": _parse_duration(v.get("length")),
                "view_count": v.get("play"),
                "upload_date": (time.strftime("%Y%m%d", time.localtime(created))
                                if created else ""),
                "url": f"https://www.bilibili.com/video/{bvid}",
            })
            if len(videos) >= limit:
                break
        if data.get("data", {}).get("page", {}).get("count", 0) <= page * 30:
            break
        page += 1

    return videos, uploader


def list_up_videos(url, limit=30, cookies=None, cookies_from_browser=None):
    """列出 UP 主空间的视频条目（仅提取信息，不下载），供前端勾选后批量下载。

    返回 {"videos":[{bvid,title,duration,view_count,upload_date,url}], "uploader":..., "error":...}
    支持传入：空间链接 / 空间 UID（纯数字）/ 带 /video 的列表页链接 / 合集 / 收藏夹 / 系列。

    注意：B站对空间列表有风控（错误 352），未登录会被拒绝，通常需要带 cookie。
    """
    try:
        import yt_dlp
    except ImportError:
        return {"videos": [], "uploader": "", "error": "未安装 yt-dlp，请先运行启动脚本安装依赖。"}

    raw = (url or "").strip()
    if not raw:
        return {"videos": [], "uploader": "", "error": "请填写 UP 主空间链接或 UID。"}

    url = normalize_url(raw)
    # 补全成视频列表页：纯 UID 或空间首页
    if url.strip().isdigit():
        url = f"https://space.bilibili.com/{url.strip()}/video"
    elif "space.bilibili.com" in url and "/video" not in url:
        m = re.search(r"(\d+)", url)
        if m:
            url = f"https://space.bilibili.com/{m.group(1)}/video"

    cookie_opts = {}
    if cookies:
        cookie_opts["cookiefile"] = os.path.expanduser(cookies)
    if cookies_from_browser:
        cookie_opts["cookiesfrombrowser"] = (cookies_from_browser,)

    # ---------- 通道 A：直连空间 API（一次请求即得含标题的完整列表） ----------
    mid_match = re.search(r"space\.bilibili\.com/(\d+)", url)
    is_plain_space = bool(mid_match) and "/lists/" not in url and "/favlist" not in url
    api_error = ""
    if is_plain_space:
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True,
                                   "skip_download": True, **cookie_opts}) as ydl:
                videos, uploader = _fetch_up_list_via_api(
                    mid_match.group(1), limit, ydl)
            if videos:
                return {"videos": videos, "uploader": uploader, "error": ""}
            api_error = "没有解析到视频条目。"
        except _RiskControl as e:
            # 命中风控就直接反馈，不再回退重试，避免加重限流
            return {"videos": [], "uploader": "",
                    "error": f"{e}。获取 UP 主视频列表通常需要登录态："
                             "请在上方「下载设置」里填写 cookie 文件或选择浏览器登录态后重试。"}
        except Exception as e:
            api_error = str(e)

    # ---------- 通道 B：回退 yt-dlp 全量解析（能拿到标题，但逐条解析较慢） ----------
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,    # 必须真正解析条目，否则条目无 title（旧 bug 根源）
        "playlistend": limit,
        "ignoreerrors": False,    # 让 352 等风控错误抛出，便于给出准确提示
        "skip_download": True,
        "socket_timeout": 30,
        **cookie_opts,
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raw_err = str(e)
        low = raw_err.lower()
        if "352" in raw_err or "412" in raw_err or "rejected" in low or "blocked" in low:
            return {"videos": [], "uploader": "",
                    "error": "B站风控/限流拒绝了该请求。获取 UP 主视频列表通常需要登录态："
                             "请在上方「下载设置」里填写 cookie 文件或选择浏览器登录态后重试；"
                             "若已配置仍失败，多半是请求太频繁，请稍后再试。"}
        return {"videos": [], "uploader": "", "error": "获取失败：" + raw_err.splitlines()[-1]}
    except Exception as e:
        return {"videos": [], "uploader": "", "error": f"获取失败：{e}"}

    if not info:
        return {"videos": [], "uploader": "",
                "error": "未获取到内容。B站空间列表通常需要登录态，请配置 cookie 后重试。"}

    uploader = info.get("uploader") or info.get("title") or ""
    videos = []
    for e in (info.get("entries") or []):
        if not e:
            continue
        bvid = e.get("id") or ""
        vurl = e.get("webpage_url") or e.get("url") or ""
        if not vurl and str(bvid).upper().startswith("BV"):
            vurl = f"https://www.bilibili.com/video/{bvid}"
        d = e.get("duration")
        videos.append({
            "bvid": bvid,
            "title": e.get("title") or "(无标题)",
            "duration": int(d) if isinstance(d, (int, float)) else None,
            "view_count": e.get("view_count"),
            "upload_date": e.get("upload_date") or "",
            "url": vurl,
        })
        if len(videos) >= limit:
            break

    if not videos:
        return {"videos": [], "uploader": uploader,
                "error": api_error or
                         "没有解析到视频条目。若UP主空间被风控，请配置 cookie 后重试；"
                         "也可直接粘贴单个视频链接用「视频链接」模式下载。"}
    return {"videos": videos, "uploader": uploader, "error": ""}


def save_cookie_text(text, domain=".bilibili.com", merge=True):
    """把用户粘贴的 cookie 文本保存成 Netscape 格式 cookies.txt，供 yt-dlp 使用。

    支持三种输入：
      1) Netscape cookies.txt 全文（含 #HttpOnly_ 前缀行也能识别）
      2) 浏览器请求头里的 Cookie 串：name=value; name=value; ...
      3) 每行一个 name=value

    merge=True 时与已有 cookies.txt 按 name 合并（新值覆盖旧值，旧的其余条目保留），
    便于用户分次补充（例如先只有 document.cookie，之后再补 SESSDATA）。

    返回 {"ok":bool, "path":str, "count":int, "has_sessdata":bool, "error":str}
    """
    import time

    path = os.path.join(app_dir(), "cookies.txt")

    # 已有条目：name -> [domain, flag, path, secure, exp, name, value]
    old_rows = {}
    if merge and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    p = line.split("\t")
                    if len(p) >= 7:
                        old_rows[p[5]] = p[:7]
        except Exception:
            pass

    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "Cookie 内容为空。"}

    netscape_lines = []
    pairs = []
    is_netscape = False

    for raw_line in text.splitlines():
        s = raw_line.strip()
        if not s:
            continue
        # Netscape 里 HttpOnly 的写法是 "#HttpOnly_.bilibili.com\tTRUE\t..."
        if s.startswith("#HttpOnly_"):
            s2 = s[len("#HttpOnly_"):]
            if "\t" in s2:
                netscape_lines.append(s2)
                is_netscape = True
                continue
        if s.startswith("#"):
            if "Netscape HTTP Cookie File" in s:
                is_netscape = True
            continue
        if "\t" in s:
            parts = s.split("\t")
            if len(parts) >= 7:
                netscape_lines.append(s)
                is_netscape = True
                continue
        # 其余按 name=value; name=value 处理
        for seg in s.split(";"):
            seg = seg.strip()
            if "=" not in seg:
                continue
            k, _, v = seg.partition("=")
            k, v = k.strip(), v.strip()
            if k:
                pairs.append((k, v))

    exp = int(time.time()) + 365 * 24 * 3600
    rows = {}

    if is_netscape and netscape_lines:
        for line in netscape_lines:
            p = line.split("\t")
            if len(p) >= 7:
                rows[p[5]] = p[:7]
    elif pairs:
        for k, v in pairs:
            rows[k] = [domain, "TRUE", "/", "TRUE", str(exp), k, v]
    else:
        return {"ok": False,
                "error": "没能解析出 name=value 形式的 Cookie，请检查粘贴的内容。"}

    # 旧条目中本次没提供的 name 原样保留
    added = 0
    for name, old in old_rows.items():
        if name not in rows:
            rows[name] = old
            added += 1

    content = "# Netscape HTTP Cookie File\n" + \
        "\n".join("\t".join(r) for r in rows.values()) + "\n"
    count = len(rows)

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        return {"ok": False, "error": f"写入 cookies.txt 失败：{e}"}

    return {"ok": True, "path": path, "count": count,
            "merged_from_old": added,
            "has_sessdata": "SESSDATA" in content}
