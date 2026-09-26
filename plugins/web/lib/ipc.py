"""lib/ipc.py — TTBOX Core IPC 客户端**单点实现**（B-CONST-1 / V-13）。

为什么需要本模块（无补丁治理）：
  plugins/web/bin/ttbox-web.py 与 plugins/web/framework_api.py 曾**各写一份**
  _ipc_request（超时 / 错误码 / 返回结构各自维护），一处改一处忘 ⇒ IPC 行为不一致。
  现收敛为本模块**唯一实现**，两侧一律 `from lib import ipc`。

契约（真值以 core/src/ipc/IpcServer.cpp 为准）：
  · NDJSON 行协议：send(json+"\\n") → recv 一行 → close；**一连接一请求**，
    绝不复用连接、绝不流水线（Core 的 handle_connection 只读一行、回一行即关 fd）。
  · 传输按 socket 形态自动选择（协议一致）：
      - tcp:host:port / host:port / 设 TTBOX_IPC_TCP ⇒ TCP（Windows 本机 win_core_main）；
      - 其它 ⇒ Unix domain socket（板端）。
  · 统一错误响应 {'status': 3, 'error': <原因>}（与 Core 的 status 语义对齐：3=INTERNAL）。
"""
from __future__ import annotations

import json
import os
import socket

from . import paths as _paths

# 单次请求默认超时（秒）。Core 侧无超时保护，必须客户端兜底（见 api_v1.py 超时分级）。
DEFAULT_TIMEOUT = 5.0


def resolve_socket_path(socket_path: str | None = None) -> str:
    """解析目标 socket：显式入参 > lib.paths.ipc_socket()（含 TTBOX_IPC_SOCKET 覆盖）。"""
    if socket_path:
        return socket_path
    return _paths.ipc_socket()


def request(req_type: str, params: dict | None = None, timeout: float = DEFAULT_TIMEOUT,
            socket_path: str | None = None) -> dict:
    """向 TTBOX Core IPC 发送请求，返回解析后的响应 dict。

    Args:
      req_type: IPC 命令名（如 "GET_STATUS" / "GET_CONFIG"）。
      params:   可选参数对象。
      timeout:  接收超时（秒）；默认 DEFAULT_TIMEOUT。
      socket_path: 覆盖目标 socket；默认取 lib.paths.ipc_socket()。

    Returns:
      Core 返回的 JSON dict；任何传输/解析失败一律返回 {'status': 3, 'error': ...}。
    """
    payload: dict = {'type': req_type}
    if params is not None:
        payload['params'] = params

    sock_spec = os.environ.get('TTBOX_IPC_TCP', '')
    base = resolve_socket_path(socket_path)
    use_tcp = base.startswith('tcp:') or bool(sock_spec)

    try:
        if use_tcp:
            spec = sock_spec or base.removeprefix('tcp:')
            host, _, port = spec.rpartition(':')
            if not host:
                host = '127.0.0.1'
            # ★ port 解析失败（空串/尾随空格/非数字）是配置错误，要给结构化错误，
            #   不能穿透成未处理异常（对照 api_v1.py 同名实现捕获 ValueError）。
            try:
                port_num = int(port)
            except ValueError:
                raise ValueError(f'非法 TTBOX_IPC_TCP 端口: {port!r}')
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            target = (host, port_num)
        else:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            target = base
    except (AttributeError, OSError, ValueError) as exc:
        mode = 'TCP' if use_tcp else 'Unix'
        return {'status': 3, 'error': f'IPC socket 创建失败（{mode}）: {exc}'}

    s.settimeout(timeout)
    try:
        s.connect(target)
        s.sendall(json.dumps(payload).encode() + b'\n')
        buf = b''
        while b'\n' not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
            # 防御：对端异常回包无换行时不要无限吃内存（正常响应远小于此）
            if len(buf) > (16 << 20):
                return {'status': 3, 'error': 'IPC 响应超长（>16MB）'}
        if not buf:
            return {'status': 3, 'error': 'IPC 无响应（Core 未运行?）'}
        return json.loads(buf.decode())
    except socket.timeout:
        # ★ 必须在 OSError 之前：socket.timeout 是 OSError 子类（3.10 起 = TimeoutError），
        #   排在后面就是死代码，超时永远被误报成"无法连接"。
        return {'status': 3, 'error': 'IPC 响应超时'}
    except (UnicodeDecodeError, json.JSONDecodeError):
        # 对照 api_v1.py 同名实现：Core 返回非 UTF-8/非 JSON 时给结构化错误而非 500
        return {'status': 3, 'error': 'IPC 响应非合法 JSON'}
    except (FileNotFoundError, ConnectionRefusedError, AttributeError, OSError):
        return {'status': 3, 'error': '无法连接 Core IPC'}
    finally:
        s.close()
