# -*- coding: utf-8 -*-
"""
B站下载器 —— 本地 Web 服务
================================
用 Python 标准库起一个本地 HTTP 服务（无需额外 Web 框架），
启动后自动打开浏览器，提供可视化界面进行单链接 / 批量下载。

API:
  GET  /                -> 前端页面
  GET  /api/tasks       -> 所有任务状态
  POST /api/add         -> 批量添加任务 {"urls":[...], "options":{...}}
  POST /api/cancel      -> 取消任务 {"id": "1"}
  POST /api/clear       -> 清空已完成/失败/取消的任务
  GET/POST /api/open    -> 在文件管理器中打开输出目录
"""

import json
import os
import sys
import webbrowser
import threading
import subprocess
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import core

WEB_DIR = os.path.join(core.resource_dir(), "web")
DEFAULT_PORT = 9876

# 可写数据（cookie / 配置 / 下载）一律放 exe 旁边，避免打包后被写入临时目录丢失
DATA_DIR = core.app_dir()
os.makedirs(os.path.join(DATA_DIR, "downloads"), exist_ok=True)

manager = core.DownloadManager(max_workers=2,
                               default_output=os.path.join(DATA_DIR, "downloads"))


CONFIG_PATH = os.path.join(DATA_DIR, "config.json")


def load_config():
    """读取已保存的默认设置（cookie / 输出目录等）。"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(data):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def find_free_port(start):
    port = start
    while port < start + 100:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
        port += 1
    return start


def open_folder(path):
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        elif sys.platform == "win32":
            os.startfile(path)  # type: ignore
        else:
            subprocess.run(["xdg-open", path], check=False)
        return True, ""
    except Exception as e:  # noqa
        return False, str(e)


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send_file(os.path.join(WEB_DIR, "index.html"),
                            "text/html; charset=utf-8")
        elif self.path == "/api/config":
            self._send_json(load_config())
        elif self.path == "/api/tasks":
            self._send_json({"tasks": manager.list_tasks(),
                             "ffmpeg": bool(manager.ffmpeg),
                             "output": manager.default_output})
        elif self.path in ("/api/open", "/api/open/"):
            ok, err = open_folder(manager.default_output)
            self._send_json({"ok": ok, "path": manager.default_output, "error": err})
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except Exception:
            body = {}

        if self.path == "/api/add":
            urls = body.get("urls") or []
            options = body.get("options") or {}
            if options.get("output"):
                manager.set_default_output(options["output"])
            ids = []
            for u in urls:
                u = (u or "").strip()
                if u:
                    ids.append(manager.add(u, options))
            self._send_json({"ids": ids})
        elif self.path == "/api/cancel":
            tid = body.get("id")
            ok = manager.cancel(tid) if tid else False
            self._send_json({"ok": ok})
        elif self.path == "/api/up/list":
            raw_url = (body.get("url") or "").strip()
            try:
                limit = int(body.get("limit", 30))
            except Exception:
                limit = 30
            limit = max(1, min(limit, 200))
            up_opts = body.get("options") or {}
            cfg = load_config()   # 表单没填时，回落到已保存的 cookie
            self._send_json(core.list_up_videos(
                raw_url, limit,
                cookies=up_opts.get("cookies") or cfg.get("cookies"),
                cookies_from_browser=up_opts.get("cookies_from_browser") or cfg.get("cookies_from_browser"),
            ))
        elif self.path == "/api/config":
            self._send_json({"ok": save_config(body)})
        elif self.path == "/api/cookies":
            # 用户直接粘贴 Cookie 文本 -> 存成 cookies.txt 并写入默认配置
            res = core.save_cookie_text(body.get("text", ""))
            if res.get("ok"):
                cfg = load_config()
                cfg["cookies"] = res["path"]
                save_config(cfg)
                res["config_saved"] = True
            self._send_json(res)
        elif self.path == "/api/clear":
            manager.clear_finished()
            self._send_json({"ok": True})
        elif self.path in ("/api/open", "/api/open/"):
            ok, err = open_folder(manager.default_output)
            self._send_json({"ok": ok, "path": manager.default_output, "error": err})
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass


def main():
    port = find_free_port(DEFAULT_PORT)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"✅ B站下载器已启动：{url}")
    print(f"📁 输出目录：{manager.default_output}")
    print("   按 Ctrl+C 停止服务")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
        server.shutdown()


if __name__ == "__main__":
    main()
