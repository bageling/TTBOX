#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YU 本地模拟原厂服务器 (破解环境修复: 检查更新跳回主页)
=============================================================
原厂 yuyuyu.store 只允许 GET/HEAD, POST 全 405;
且破解 license 在服务器上不活跃 -> web 检查更新报 400
"license is not active on this server" -> 前端跳回主页。

本服务监听 127.0.0.1:8099, 模拟原厂 /aiassistance-api 端点,
返回"已是最新版本"等正常响应, 让前端检查更新正常显示。

使用: 把 server_url.txt 改为 http://127.0.0.1:8099/aiassistance-api
端点覆盖: license-check / check-update / update-versions /
          activate / repair / hailo/package / themes/*
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ============ 配置 ============
CURRENT_VERSION = "2026.08.03.1"     # 当前安装版本
HOST = "127.0.0.1"
PORT = 8099
BASE = "/aiassistance-api/v1/"

# 原厂已知版本列表 (文档实测)
KNOWN_VERSIONS = [
    "2026.07.21.1",
    "2026.07.23.1",
    "2026.07.28.1",
    "2026.07.28.2",
    "2026.07.29.1",
    "2026.07.31.2",
    "2026.08.03.1",
]


def _base_response(**extra):
    d = {
        "ok": True,
        "server_time": int(time.time()),
    }
    d.update(extra)
    return d


def _no_update():
    """检查更新: 已是最新"""
    return _base_response(
        update_available=False,
        latest_version=CURRENT_VERSION,
        versions=[{"version": v, "published_at": "", "size": 0,
                   "notes": ""} for v in KNOWN_VERSIONS],
        components={
            "core": {"latest_version": CURRENT_VERSION, "current_version": CURRENT_VERSION, "update_available": False},
            "usb_proxy": {"latest_version": CURRENT_VERSION, "current_version": CURRENT_VERSION, "update_available": False},
        },
        package=None,
    )


def _license_check():
    """license-check: 返回当前 license 状态 (不吊销、不更新)"""
    return _base_response(
        license=None,
        online_grant=None,
        model_key=None,
        revoked=False,
    )


def _hailo_package():
    return _base_response(hailo=None, package=None)


def _themes_catalog():
    return _base_response(themes=[], catalog=[])


def _themes_redeem():
    return _base_response(redeemed=False)


def _themes_package():
    return _base_response(theme=None, package=None)


def _generic_ok():
    return _base_response()


ROUTES = {
    "license-check": _license_check,
    "check-update": _no_update,
    "update-versions": _no_update,
    "activate": _generic_ok,
    "repair": _generic_ok,
    "hailo/package": _hailo_package,
    "themes/catalog": _themes_catalog,
    "themes/redeem": _themes_redeem,
    "themes/package": _themes_package,
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[mock-server] %s\n" % (fmt % args))

    def _handle(self):
        path = self.path
        # 去掉 query string
        if "?" in path:
            path = path.split("?", 1)[0]
        if not path.startswith(BASE):
            self._json(404, {"ok": False, "error": "not found"})
            return
        suffix = path[len(BASE):].strip("/")
        handler = ROUTES.get(suffix)
        if handler is None:
            self._json(404, {"ok": False, "error": f"unknown endpoint: {suffix}"})
            return
        # 读 body (忽略内容, 模拟原厂不校验)
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self._json(200, handler())

    def do_POST(self):
        self._handle()

    def do_GET(self):
        self._handle()

    def do_HEAD(self):
        self._handle()

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    print(f"[mock-server] listening on {HOST}:{PORT}{BASE}", flush=True)
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.serve_forever()


if __name__ == "__main__":
    main()
