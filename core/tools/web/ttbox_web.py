#!/usr/bin/env python3
"""ttbox-web — TTBox 轻量管理控制台 (标准库, 零依赖)

端口 :8080。功能:
  GET  /                         控制台页面
  GET  /api/state                系统/服务/推理状态 + 完整监控指标
  GET  /api/models               模型列表 (registry)
  POST /api/models/upload        上传模型 → staging (.rknn / .onnx)
  POST /api/models/convert       ONNX → RKNN 转换（异步，状态轮询）
  POST /api/models/activate     激活模型
  POST /api/models/remove        删除模型 (active 禁止)
  POST /api/inference            推理 start/stop
  GET  /api/profile              读取 RuntimeProfile（热更新参数）
  POST /api/profile              写入 RuntimeProfile（热更新生效）
  GET  /api/hid                  HID 健康状态
  POST /api/edid                 重新注入 EDID
  GET  /api/hwmon                温度/频率监控

推理走 C++ runtime (test_worker_hw, systemd 服务 ttbox-infer.service)。
本进程仅做管理与状态展示, 不承载 AI 逻辑。
"""
from __future__ import annotations

import cgi
import json
import os
import re
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

TTBOX = Path("/opt/ttbox")
MODELS_REG = TTBOX / "models" / "registry"
PLATFORM_MODEL_DIR = TTBOX / "models" / "current"
MODELS_INSTALLED = MODELS_REG / "installed"
MODELS_STAGING = MODELS_REG / "staging"
MODELS_MANIFESTS = MODELS_REG / "manifests"        # 模型元数据（类别名/游戏标签/描述）
ACTIVE_MODEL_FILE = TTBOX / "models" / "active_model.txt"
REMOTE_CONFIG_FILE = TTBOX / "config" / "remote.json"   # 远端推理连接配置（设备端先行）
REMOTE_DEVICE_CODE_FILE = TTBOX / "config" / "device_code.txt"
REMOTE_INFER_PORT = 8642   # 自研 Windows 推理服务约定端口（远端推理接入待后续）
HID_ROOT = TTBOX / "hid"
INFER_LOG = TTBOX / "runtime" / "infer.log"
EDID_SCRIPT = TTBOX / "edid" / "inject_edid.sh"
HID_HEALTH = TTBOX / "runtime" / "ttbox-hid-health"
HID_CONFIG = TTBOX / "hid" / "config" / "hid_config.json"
PROFILE_FILE = TTBOX / "run" / "runtime_profile.json"
FEATURES_CONF = Path("/run/ttbox-features.conf")   # 功能配置扁平化（C 桥轮询解析）
PRESETS_DIR = TTBOX / "config" / "presets"         # 预设参数目录
CONVERT_SCRIPT = TTBOX / "scripts" / "convert_onnx_to_rknn.py"
CONVERT_DIR = TTBOX / "run" / "convert"
MOUSE_STATS_FILE = Path("/run/ttbox-mouse-stats.json")  # A10：C 桥 AI 注入统计
TARGET_STATE_FILE = Path("/run/ttbox-target.json")      # A10.3：C++ AimThread 高频目标状态
MOUSE_HW_FILE = TTBOX / "config" / "hardware_mouse.json"   # USB 鼠标身份配置（合成模式自定义）
# Platform V1 status paths：与真实 RK3588 inference service 的部署布局一致
PLATFORM_MODEL = TTBOX / "models" / "current" / "model.rknn"
PLATFORM_INFER_CONFIG = TTBOX / "config" / "current" / "infer.json"
PLATFORM_CORE_SERVICE = "ttbox-core.service"
PLATFORM_INFER_SERVICE = "ttbox-infer.service"
PLATFORM_SUPERVISOR_SERVICE = "ttbox-supervisor.service"
MOUSE_APPLY_SCRIPT = TTBOX / "scripts" / "apply_mouse_identity.sh"
# 前端页面：优先读取同目录 index.html（设计稿对接版），缺失回退内嵌 INDEX_HTML
INDEX_FILE = Path(__file__).resolve().parent / "index.html"
LOCK = threading.Lock()  # 转换任务互斥

# ---- 转换任务状态（内存 + 磁盘 json）----
def convert_state_path(task_id: str) -> Path:
    return CONVERT_DIR / f"{task_id}.json"


def convert_state(task_id: str) -> dict:
    try:
        return json.loads(convert_state_path(task_id).read_text())
    except Exception:  # noqa: BLE001
        return {"task_id": task_id, "state": "unknown", "ok": False}


def convert_save(task_id: str, data: dict):
    CONVERT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = convert_state_path(task_id).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    os.replace(tmp, convert_state_path(task_id))


def _run(cmd: list[str], timeout: float = 15.0) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return 1, str(exc)


def _sys(path: str, default: str = "") -> str:
    try:
        return Path(path).read_text().strip() or default
    except Exception:  # noqa: BLE001
        return default


# systemctl 探测需派生子进程，昂贵；前端每 200ms 轮询 /api/state × 7 服务
# = 每秒约 35 次子进程派生（实测 web CPU 53%+）。缓存 1.5s 消除该开销。
_SVC_TTL = 1.5
_svc_cache: dict[str, tuple[float, bool]] = {}


def _svc(name: str, check_failed: bool) -> bool:
    now = time.monotonic()
    hit = _svc_cache.get(name)
    if hit and now - hit[0] < _SVC_TTL:
        return hit[1]
    if check_failed:
        _, out = _run(["systemctl", "is-failed", name], timeout=5)
        ok = "failed" not in out
    else:
        code, out = _run(["systemctl", "is-active", name], timeout=5)
        ok = code == 0 and "inactive" not in out and "failed" not in out
    _svc_cache[name] = (now, ok)
    return ok


def svc_active(name: str) -> bool:
    return _svc(name, False)


def svc_ok(name: str) -> bool:
    """oneshot 服务成功完成（dead/inactive）也算正常；仅 failed 视为异常。"""
    return _svc(name, True)


def invalidate_svc(name: str) -> None:
    """服务启停后立即失效缓存，让下一次 /api/state 反映真实状态。"""
    _svc_cache.pop(name, None)


def hid_config_state() -> dict:
    """读取 hid_config.json 的透传开关状态 + forwarder 服务状态。"""
    try:
        data = json.loads(HID_CONFIG.read_text())
    except Exception:  # noqa: BLE001
        data = {}
    return {
        "mouse_enabled": bool(data.get("mouse", {}).get("enabled", True)),
        "keyboard_enabled": bool(data.get("keyboard", {}).get("enabled", True)),
        "forward": svc_active("ttbox-hid-forward"),
    }


def hid_set(device: str, enabled: bool) -> tuple[bool, str]:
    """写入透传开关并重启 forwarder 使其生效。"""
    try:
        data = json.loads(HID_CONFIG.read_text())
    except Exception as exc:  # noqa: BLE001
        return False, f"读取配置失败: {exc}"
    data.setdefault(device, {})["enabled"] = bool(enabled)
    try:
        tmp = HID_CONFIG.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        os.replace(tmp, HID_CONFIG)
    except Exception as exc:  # noqa: BLE001
        return False, f"写入配置失败: {exc}"
    code, out = _run(["sudo", "systemctl", "restart", "ttbox-hid-forward"], timeout=30)
    if code != 0:
        return False, f"重启 forwarder 失败: {out.strip()}"
    return True, f"{'鼠标' if device == 'mouse' else '键盘'}透传已{'开启' if enabled else '关闭'}"


_KERNEL = None


def kernel_version() -> str:
    global _KERNEL
    if _KERNEL is None:
        code, ver = _run(["uname", "-r"], timeout=5)
        _KERNEL = ver.strip()
    return _KERNEL


def read_infer_metrics() -> dict:
    """从 infer.log 尾部解析 [METRICS] JSON 行（完整监控指标）。"""
    try:
        with open(INFER_LOG, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 60000))
            txt = f.read().decode(errors="ignore")
    except Exception:  # noqa: BLE001
        txt = ""
    # 找到最后一个 [METRICS] JSON 行
    m = None
    for line in txt[-60000:].splitlines():
        line = line.strip()
        if line.startswith("[METRICS] "):
            try:
                m = json.loads(line[len("[METRICS] "):])
            except Exception:  # noqa: BLE001
                m = None
    if not m:
        return {}
    return m


def read_infer_fps() -> dict:
    """兼容旧接口：从 infer.log 尾部解析实时 [REPORT] 或最近一次汇总。"""
    try:
        txt = INFER_LOG.read_text(errors="ignore")
    except Exception:  # noqa: BLE001
        txt = ""
    tail = txt[-30000:]
    fps = re.findall(r"pool_fps=([0-9.]+)", tail)  # 实时定期报告
    if not fps:
        fps = re.findall(r"总吞吐 FPS=([0-9.]+)", tail)  # 结束汇总
    err = re.findall(r"错误=(\d+)", tail)
    poll = re.findall(r"poll_timeouts?=(\d+)", tail)
    return {
        "fps": float(fps[-1]) if fps else None,
        "errors": int(err[-1]) if err else None,
        "poll_timeouts": int(poll[-1]) if poll else None,
    }


def _model_manifest(name: str) -> dict:
    """读模型元数据（类别名/游戏标签/描述等）。"""
    try:
        return json.loads((MODELS_MANIFESTS / (name + ".json")).read_text())
    except Exception:  # noqa: BLE001
        return {}


def _save_model_manifest(name: str, meta: dict) -> None:
    try:
        MODELS_MANIFESTS.mkdir(parents=True, exist_ok=True)
        tmp = MODELS_MANIFESTS / (name + ".json.tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        os.replace(tmp, MODELS_MANIFESTS / (name + ".json"))
    except Exception:  # noqa: BLE001
        pass


def models_list() -> list[dict]:
    """模型列表（对齐 YU /api/models 契约：backend/class_names/game_profile 等）。"""
    out: list[dict] = []
    active = ""
    try:
        active = ACTIVE_MODEL_FILE.read_text().strip()
    except Exception:  # noqa: BLE001
        pass

    def entry(f: Path, status: str, backend: str) -> dict:
        meta = _model_manifest(f.name)
        return {
            "id": f.name,
            "name": meta.get("name", f.stem),
            "file_name": f.name,
            "backend": backend,
            "status": status,
            "size": f.stat().st_size,
            "active": (f.name == active),
            "class_count": int(meta.get("class_count", 0) or 0) or len(meta.get("class_names", [])),
            "class_names": meta.get("class_names", []),
            "game_profile": meta.get("game_profile", ""),
            "preset_name": meta.get("preset_name", ""),
            "description": meta.get("description", ""),
            "hailo_pipeline_depth": int(meta.get("hailo_pipeline_depth", 0) or 0),
            "rknn_concurrency": int(meta.get("rknn_concurrency", 1) or 1),
            "remote_host": meta.get("remote_host", ""),
            "remote_model_id": meta.get("remote_model_id", ""),
            "remote_engine_name": meta.get("remote_engine_name", ""),
            "remote_frame_format": meta.get("remote_frame_format", "jpeg"),
            "remote_available": meta.get("remote_available", True),
        }

    if MODELS_INSTALLED.is_dir():
        for f in sorted(MODELS_INSTALLED.iterdir()):
            if f.suffix.lower() == ".rknn" and f.is_file():
                out.append(entry(f, "installed", "rknn"))
    # Platform V1 active deployment layout: /opt/ttbox/models/current/model.rknn
    active_platform=PLATFORM_MODEL_DIR / "model.rknn"
    if active_platform.is_file() and not any(x.get("file_name")==active_platform.name for x in out):
        out.append({"id":"model.rknn","name":"model","file_name":"model.rknn","backend":"rknn","status":"active","size":active_platform.stat().st_size,"active":True,"path":str(active_platform),"class_count":None,"class_names":[],"game_profile":"","description":"Platform active model","rknn_concurrency":3})
    if MODELS_STAGING.is_dir():
        for f in sorted(MODELS_STAGING.iterdir()):
            low = f.suffix.lower()
            if low in (".rknn", ".onnx") and f.is_file():
                out.append(entry(f, "staging", "rknn" if low == ".rknn" else "onnx"))
    return out


def _parse_labels_file(files: dict) -> list[str]:
    """类别标签文件 → 类别名列表（txt 每行一个）。"""
    item = files.get("labels_file")
    if not item:
        return []
    data = item[1] if isinstance(item, tuple) else item
    try:
        text = data.decode("utf-8", errors="ignore") if isinstance(data, bytes) else str(data)
        return [ln.strip() for ln in text.splitlines() if ln.strip()]
    except Exception:  # noqa: BLE001
        return []


def model_import(fields: dict, files: dict) -> tuple[bool, str, dict]:
    """模型导入（对齐 YU /api/models/import）。

    RKNN 直接安装可用；ONNX 存 staging 标注转换待接入（板端无 rknn-toolkit2）；
    HEF / 远端 ONNX 标注待接入（Hailo / Windows 端）。
    """
    import shutil
    model_type = str(fields.get("model_type") or "rknn")
    item = files.get("file")
    if not item:
        return False, "未选择模型文件", {}
    fname = os.path.basename(str(item[0]))
    data = item[1]
    if not fname or not data:
        return False, "模型文件为空", {}
    ext = os.path.splitext(fname)[1].lower()
    MODELS_STAGING.mkdir(parents=True, exist_ok=True)
    safe = fname
    dst = MODELS_STAGING / safe
    dst.write_bytes(data if isinstance(data, bytes) else data.encode())
    meta = {
        "name": str(fields.get("name") or "").strip() or os.path.splitext(safe)[0],
        "game_profile": str(fields.get("game_profile") or "").strip(),
        "description": str(fields.get("description") or "").strip(),
        "class_names": _parse_labels_file(files),
    }
    _save_model_manifest(safe, meta)
    if model_type == "rknn" and ext in (".rknn", ".enc", ".rknn.enc"):
        MODELS_INSTALLED.mkdir(parents=True, exist_ok=True)
        if not (MODELS_INSTALLED / safe).exists():
            shutil.copy2(dst, MODELS_INSTALLED / safe)
        try:
            dst.unlink()
        except Exception:  # noqa: BLE001
            pass
        return True, f"模型已导入: {safe}", {"model": safe, "status": "installed"}
    if model_type in ("onnx", "remote_onnx") and ext == ".onnx":
        return True, "ONNX 已上传到暂存区，转换 RKNN 待接入（板端无 rknn-toolkit2）", \
            {"model": safe, "status": "staging"}
    if model_type == "hef" and ext in (".hef", ".enc", ".hef.enc"):
        return True, "HEF 已上传到暂存区，Hailo 推理待接入（未检测到 Hailo 硬件）", \
            {"model": safe, "status": "staging"}
    try:
        dst.unlink()
    except Exception:  # noqa: BLE001
        pass
    return False, f"不支持的文件类型或导入方式: {ext} / {model_type}", {}


def _infer_log_failed() -> bool:
    """infer.log 尾部出现解码/适配失败或异常终止 → 模型不兼容。"""
    try:
        tail = INFER_LOG.read_text(errors="ignore")[-4000:]
        return ("[FAIL]" in tail and "adapter" in tail) or "terminate called" in tail
    except Exception:  # noqa: BLE001
        return False


def _infer_ok() -> bool:
    return svc_active("ttbox-infer") and not _infer_log_failed()


def model_select(model_id) -> tuple[bool, str]:
    """切换当前模型（写 active_model.txt + infer.json，重启推理）。

    带回滚防呆：模型与推理后端不兼容（解码失败导致 infer 起不来/崩溃）时，
    自动恢复原模型，避免系统推理停摆。
    """
    name = os.path.basename(str(model_id or ""))
    if not name:
        return False, "模型 ID 为空"
    MODELS_INSTALLED.mkdir(parents=True, exist_ok=True)
    src = None
    for p in (MODELS_INSTALLED / name, MODELS_STAGING / name):
        if p.exists():
            src = p
            break
    if not src:
        return False, f"模型不存在: {name}"
    if src.suffix.lower() not in (".rknn",):
        return False, "仅支持选择 .rknn 模型（当前推理后端为 RKNN NPU）"
    if src.parent != MODELS_INSTALLED:
        if not (MODELS_INSTALLED / name).exists():
            shutil.copy2(src, MODELS_INSTALLED / name)
        src = MODELS_INSTALLED / name
    prev = ""
    try:
        prev = ACTIVE_MODEL_FILE.read_text().strip()
    except Exception:  # noqa: BLE001
        pass
    try:
        ACTIVE_MODEL_FILE.write_text(name)
    except Exception as exc:  # noqa: BLE001
        return False, f"写入失败: {exc}"
    infer_cfg = TTBOX / "config" / "infer.json"
    old_model = None
    try:
        infer = json.loads(infer_cfg.read_text())
        old_model = infer.get("model")
        infer["model"] = str(src)
        infer_cfg.write_text(json.dumps(infer, ensure_ascii=False, indent=2))
    except Exception:  # noqa: BLE001
        pass
    _run(["sudo", "systemctl", "restart", "ttbox-infer.service"], timeout=30)
    invalidate_svc("ttbox-infer")
    time.sleep(3.0)
    running = _infer_ok()
    if not running:
        # 模型加载可能稍慢，二次确认后再判定不兼容
        time.sleep(2.0)
        invalidate_svc("ttbox-infer")
        running = _infer_ok()
    if not running:
        # 回滚：模型不兼容（如输出结构不支持解码），恢复原模型
        if prev:
            try:
                ACTIVE_MODEL_FILE.write_text(prev)
            except Exception:  # noqa: BLE001
                pass
        if old_model:
            try:
                infer = json.loads(infer_cfg.read_text())
                infer["model"] = old_model
                infer_cfg.write_text(json.dumps(infer, ensure_ascii=False, indent=2))
            except Exception:  # noqa: BLE001
                pass
        _run(["sudo", "systemctl", "restart", "ttbox-infer.service"], timeout=30)
        invalidate_svc("ttbox-infer")
        return False, f"模型 {name} 与当前推理后端不兼容（解码失败），已回滚到原模型"
    return True, f"已切换模型并重启推理: {name}"


def model_delete(model_id) -> tuple[bool, str]:
    """删除本地模型（当前使用中禁止删除）。"""
    name = os.path.basename(str(model_id or ""))
    if not name:
        return False, "模型 ID 为空"
    try:
        active = ACTIVE_MODEL_FILE.read_text().strip()
    except Exception:  # noqa: BLE001
        active = ""
    if name == active:
        return False, "当前使用中的模型不能删除，请先切换其他模型"
    removed = False
    for p in (MODELS_INSTALLED / name, MODELS_STAGING / name):
        if p.exists():
            try:
                p.unlink()
                removed = True
            except Exception as exc:  # noqa: BLE001
                return False, f"删除失败: {exc}"
    man = MODELS_MANIFESTS / (name + ".json")
    if man.exists():
        try:
            man.unlink()
        except Exception:  # noqa: BLE001
            pass
    return (True, f"模型已删除: {name}") if removed else (False, f"模型不存在: {name}")


# ===== 设备端远端推理（设备端先行，推理接入待后续） =====
def remote_config() -> dict:
    try:
        return json.loads(REMOTE_CONFIG_FILE.read_text())
    except Exception:  # noqa: BLE001
        return {}


def _port_open(host: str, port: int) -> bool:
    import socket
    try:
        s = socket.create_connection((host, port), timeout=3)
        s.close()
        return True
    except Exception:  # noqa: BLE001
        return False


def device_code() -> str:
    """设备码（持久化，首次生成；YU 用于 ONNX 加密配对）。"""
    import random
    import string
    code = ""
    try:
        code = REMOTE_DEVICE_CODE_FILE.read_text().strip()
    except Exception:  # noqa: BLE001
        pass
    if code:
        return code
    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
    try:
        REMOTE_DEVICE_CODE_FILE.parent.mkdir(parents=True, exist_ok=True)
        REMOTE_DEVICE_CODE_FILE.write_text(code)
    except Exception:  # noqa: BLE001
        pass
    return code


def remote_connect(host) -> tuple[bool, str, dict]:
    """保存远端主机 + 探测 Windows 推理服务（设备端先行：推理接入待后续）。"""
    import ipaddress
    host = str(host or "").strip()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False, "IP 地址格式不正确", {}
    cfg = remote_config()
    cfg["host"] = host
    cfg["port"] = REMOTE_INFER_PORT
    try:
        REMOTE_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        REMOTE_CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001
        return False, f"保存失败: {exc}", {}
    reachable = _port_open(host, REMOTE_INFER_PORT)
    detail = (f"已保存主机 {host}，Windows 端推理服务未响应（{host}:{REMOTE_INFER_PORT}），"
              "远端推理接入待后续实现" if not reachable
              else f"已连接 {host}:{REMOTE_INFER_PORT}，远端推理接入待后续实现")
    return True, detail, {"config": cfg, "connected": reachable, "models": [],
                          "selected_model_id": ""}


def remote_models() -> dict:
    """远端模型列表（设备端先行：真实探测连通性，模型列表/推理待接入）。"""
    cfg = remote_config()
    host = str(cfg.get("host") or "")
    if not host:
        return {"config": cfg, "models": [], "selected_model_id": "", "connected": False,
                "detail": "尚未连接远端主机（模型库 → 连接远端推理）"}
    reachable = _port_open(host, int(cfg.get("port") or REMOTE_INFER_PORT))
    return {"config": cfg, "models": [], "selected_model_id": "", "connected": reachable,
            "detail": ("远端模型列表待接入（Windows 端推理服务接入后可用）" if reachable
                       else "Windows 端推理服务未响应，远端推理接入待后续实现")}


def hwmon() -> dict:
    z0 = _sys("/sys/class/thermal/thermal_zone0/temp")
    # DDR devfreq 常见路径
    ddr = _sys("/sys/class/devfreq/dmc/cur_freq") or _sys(
        "/sys/class/devfreq/fd8c0000.dmc/cur_freq")
    return {
        "soc_temp_c": round(int(z0) / 1000.0, 1) if z0.lstrip("-").isdigit() else None,
        "cpu4_freq_hz": _sys("/sys/devices/system/cpu/cpu4/cpufreq/scaling_cur_freq"),
        "cpu4_governor": _sys("/sys/devices/system/cpu/cpu4/cpufreq/scaling_governor"),
        "gpu_freq_hz": _sys("/sys/class/devfreq/fb000000.gpu/cur_freq"),
        "npu_freq_hz": _sys("/sys/class/devfreq/fdab0000.npu/cur_freq"),
        "ddr_freq_hz": ddr,
        "loadavg": _sys("/proc/loadavg"),
    }


def read_mouse_stats() -> dict:
    """A10：读 C 桥 AI 注入统计（/run/ttbox-mouse-stats.json）。"""
    try:
        return json.loads(MOUSE_STATS_FILE.read_text())
    except Exception:  # noqa: BLE001
        return {}


def _read_usb_dev_dir(dev: Path) -> dict:
    """从 USB 设备 sysfs 目录读取真实设备全部参数。

    全部取真实设备值：sysfs 不暴露 bcdUSB（用 version 如 "2.00" 换算）、
    bMaxPower 是带单位字符串（"98mA"，已是实际毫安值）、serial 属性可能
    不存在（设备无 iSerialNumber，此时为空字符串，不做兜底）。
    """
    import re

    def rd(name: str) -> str:
        try:
            return (dev / name).read_text().strip()
        except Exception:  # noqa: BLE001
            return ""

    out = {
        "vid": ("0x" + rd("idVendor")) if rd("idVendor") else "",
        "pid": ("0x" + rd("idProduct")) if rd("idProduct") else "",
        "manufacturer": rd("manufacturer"),
        "product": rd("product"),
        "serial": rd("serial"),
        "bcd_usb": "",
        "bcd_device": ("0x" + rd("bcdDevice")) if rd("bcdDevice") else "",
        "configuration": rd("configuration"),
    }
    # bcdUSB：sysfs 无此属性，从 version（如 "2.00"）换算为 0x0200
    ver = rd("version")
    m = re.match(r"\s*(\d+)\.(\d+)", ver)
    if m:
        out["bcd_usb"] = "0x%04X" % (int(m.group(1)) * 0x100 + int(m.group(2)) * 0x10)
    # bMaxPower 形如 "98mA"（实际毫安值），取数字部分
    m = re.match(r"\s*(\d+)", rd("bMaxPower"))
    if m:
        out["max_power"] = int(m.group(1))
    for k, key in (("bDeviceClass", "device_class"),
                   ("bDeviceSubClass", "device_subclass"),
                   ("bDeviceProtocol", "device_protocol")):
        try:
            out[key] = int(rd(k))
        except Exception:  # noqa: BLE001
            pass
    # 鼠标 HID 接口信息（接口目录形如 <dev>:1.1）
    for iface in sorted(dev.glob("*.1")):
        if not (iface / "bInterfaceClass").exists():
            continue
        try:
            out["hid_class"] = int((iface / "bInterfaceClass").read_text().strip())
        except Exception:  # noqa: BLE001
            pass
        try:
            out["hid_protocol"] = int((iface / "bInterfaceProtocol").read_text().strip())
        except Exception:  # noqa: BLE001
            pass
        try:
            out["hid_subclass"] = int((iface / "bInterfaceSubClass").read_text().strip())
        except Exception:  # noqa: BLE001
            pass
        try:
            out["hid_interval"] = int((iface / "bInterval").read_text().strip())
        except Exception:  # noqa: BLE001
            pass
        try:
            out["hid_num_endpoints"] = int((iface / "bNumEndpoints").read_text().strip())
        except Exception:  # noqa: BLE001
            pass
        break
    # 其余全部 sysfs 属性原样带上（真实设备完整参数）
    raw = {}
    for a in ("bMaxPacketSize0", "bConfigurationValue", "bNumConfigurations",
              "bNumInterfaces", "bmAttributes", "speed", "version",
              "busnum", "devnum", "devpath"):
        v = rd(a)
        if v:
            raw[a] = v
    out["raw"] = raw
    return out


def _run_bytes(cmd: list[str], timeout: float = 15.0) -> tuple[int, bytes]:
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return p.returncode, p.stdout
    except Exception:  # noqa: BLE001
        return 1, b""


def _find_hid_gadget() -> Path | None:
    """动态发现 configfs 里的 HID gadget（兼容改名前后的目录名）。"""
    base = Path("/sys/kernel/config/usb_gadget")
    try:
        for g in base.iterdir():
            if (g / "functions" / "hid.usb1").exists():
                return g
    except Exception:  # noqa: BLE001
        pass
    return None


def read_real_mouse_hw() -> dict:
    """从 sysfs 读真实鼠标（HID_PHYS 以 /input1 结尾，与 C 桥一致）的 USB 硬件信息。

    真实透传模式下前端展示的就是这套信息（C 桥 3 路双向透传的真实设备）。
    HID 报告描述符等取 configfs gadget 里克隆的真实值（需 sudo 读取）。
    """
    out: dict = {}
    try:
        for h in sorted(Path("/sys/class/hidraw").iterdir()):
            if not h.name.startswith("hidraw"):
                continue
            try:
                uev = (h / "device" / "uevent").read_text(errors="ignore")
            except Exception:  # noqa: BLE001
                continue
            phys = ""
            for line in uev.splitlines():
                if line.startswith("HID_PHYS="):
                    phys = line[9:]
            if not phys.endswith("/input1"):
                continue
            try:
                real = os.path.realpath(h / "device")
            except Exception:  # noqa: BLE001
                continue
            cur = Path(real)
            while cur != cur.parent:
                if (cur / "idVendor").exists():
                    out = _read_usb_dev_dir(cur)
                    break
                cur = cur.parent
            if out:
                break
    except Exception:  # noqa: BLE001
        pass
    # HID 层信息：报告描述符/报告长度取 configfs 克隆的真实值（configfs 默认
    # root 拥有，需 sudo）。hid_protocol/subclass/configuration 等一律用真实
    # 设备 sysfs 值（_read_usb_dev_dir 已读），不覆盖。
    g = _find_hid_gadget()
    if g is not None:
        rc, blob = _run_bytes(["sudo", "cat", str(g / "functions" / "hid.usb1" / "report_desc")], timeout=5)
        if rc == 0 and blob:
            out["hid_report_desc_hex"] = blob.hex()
        rc, txt = _run(["sudo", "cat", str(g / "functions" / "hid.usb1" / "report_length")], timeout=5)
        if rc == 0:
            try:
                out["hid_report_length"] = int(txt.strip())
            except (TypeError, ValueError):
                pass
    return out


def read_mouse_config() -> dict:
    """读 USB 鼠标身份配置（hardware_mouse.json）+ profile 里的模式兜底。"""
    cfg: dict = {}
    try:
        cfg = json.loads(MOUSE_HW_FILE.read_text())
    except Exception:  # noqa: BLE001
        pass
    mo = (read_profile() or {}).get("mouse") or {}
    cfg.setdefault("proxy_mode", mo.get("proxy_mode", "full_passthrough"))
    cfg.setdefault("settle_delay_sec", 1)
    cfg.setdefault("identity_change_settle_delay_sec", 0.5)
    return cfg


def mouse_hw_state() -> dict:
    """GET /api/mouse —— 完整透传真实硬件信息 + 合成身份配置 + 透传状态。"""
    cfg = read_mouse_config()
    return {
        "proxy_mode": cfg.get("proxy_mode", "full_passthrough"),
        "settle_delay_sec": cfg.get("settle_delay_sec", 1),
        "identity_change_settle_delay_sec": cfg.get("identity_change_settle_delay_sec", 0.5),
        "identity": cfg.get("identity") or {},
        "identity_set": bool(cfg.get("identity")),
        "real": read_real_mouse_hw(),
        "passthrough": read_mouse_stats(),
    }


def mouse_randomize() -> tuple[bool, str, dict]:
    """生成随机鼠标身份（仅返回候选值填表单，不落盘；「保存并应用」才生效）。"""
    import random
    cons = "BCDFGHJKLMNPQRSTVWXYZ"
    vow = "AEIOU"
    man = (cons[random.randrange(len(cons))] + vow[random.randrange(len(vow))]
           + cons[random.randrange(len(cons))] + " Inc.")
    words = ("Gaming", "Pro", "Air", "Max", "Ultra", "Classic", "Stealth",
             "Neon", "Cyber", "Swift", "Vortex", "Phantom")
    prd = random.choice(words) + " Mouse " + str(random.randrange(10, 99))
    vid = random.randrange(1, 0x10000)
    while vid in (0x1d6b, 0x046d, 0x045e, 0x1b1c, 0x1532, 0x0951, 0x258a, 0x0461):
        vid = random.randrange(1, 0x10000)
    return True, "已生成随机身份，点击「保存并应用」后生效", {
        "vid": f"0x{vid:04X}",
        "pid": f"0x{random.randrange(1, 0x10000):04X}",
        "manufacturer": man,
        "product": prd,
        "serial": "TT" + str(random.randrange(100, 1000)) + str(random.randrange(10, 99)),
        "configuration": "TTBOX HID Forwarder",
    }


def mouse_save(data: dict) -> tuple[bool, str]:
    """保存鼠标模式/等待时间/合成身份 → 落盘 hardware_mouse.json → 同步 profile
    → 重建 gadget 应用身份 + 重启透传使生效。"""
    cfg = read_mouse_config()
    mode = str(data.get("proxy_mode") or cfg.get("proxy_mode") or "full_passthrough")
    if mode not in ("full_passthrough", "synthetic"):
        mode = "full_passthrough"
    cfg["proxy_mode"] = mode
    for k in ("settle_delay_sec", "identity_change_settle_delay_sec"):
        if data.get(k) is not None:
            try:
                cfg[k] = float(data[k])
            except (TypeError, ValueError):
                pass
    idn = data.get("identity") or {}
    identity = dict(cfg.get("identity") or {})
    for k in ("vid", "pid", "manufacturer", "product", "serial", "configuration"):
        if idn.get(k) is not None:
            identity[k] = str(idn[k]).strip()
    if mode == "synthetic":
        # 合成身份必填校验：VID/PID 必须，字符串全非空（serial 空会让主机枚举卡死）
        if not identity.get("vid") or not identity.get("pid"):
            return False, "合成模式必须填写 VID 与 PID"
        for k in ("manufacturer", "product", "serial"):
            if not identity.get(k):
                identity[k] = "TTBOX"
    else:
        identity = {}
    cfg["identity"] = identity
    try:
        MOUSE_HW_FILE.parent.mkdir(parents=True, exist_ok=True)
        MOUSE_HW_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001
        return False, f"写入失败: {exc}"
    # 同步 proxy_mode / enabled 到 RuntimeProfile（C 桥透传模式联动）
    prof = read_profile()
    prof.setdefault("mouse", {})
    prof["mouse"]["proxy_mode"] = mode
    prof["mouse"]["enabled"] = (mode == "synthetic")
    write_profile(prof)
    if MOUSE_APPLY_SCRIPT.exists():
        code, out = _run(["sudo", "bash", str(MOUSE_APPLY_SCRIPT)], timeout=60)
        if code != 0:
            return False, f"应用失败: {out[-300:] or '未知错误'}"
        return True, "鼠标身份已保存并应用（主机将重新枚举）"
    return True, "鼠标配置已保存（应用脚本缺失，重启后生效）"


def read_model_input() -> dict:
    """活跃模型推理输入尺寸。

    infer.json 的 in_w/in_h 是 ttbox-infer.sh → test_worker_hw 的唯一输入尺寸来源
    （与运行时完全一致）。前端"截取尺寸"下拉据此生成选项（只能 ≤ 模型输入）。
    """
    try:
        d = json.loads(Path("/opt/ttbox/config/infer.json").read_text())
        w, h = int(d.get("in_w", 0)), int(d.get("in_h", 0))
        if w > 0 and h > 0:
            return {"width": w, "height": h, "source": "infer.json"}
    except Exception:  # noqa: BLE001
        pass
    return {}


def _uptime_seconds(timestamp: str|None):
    if not timestamp: return None
    try:
        import datetime
        dt=datetime.datetime.strptime(timestamp.strip(), "%a %Y-%m-%d %H:%M:%S %Z")
        return max(0.0,(datetime.datetime.utcnow()-dt.replace(tzinfo=None)).total_seconds())
    except Exception: return None

def _service_show(name: str) -> dict:
    """读取 systemd show 的真实状态；失败时返回 unavailable，不填充假数据。"""
    code, out = _run(["systemctl", "show", name, "--no-page", "--property=LoadState,ActiveState,SubState,MainPID,Result,NRestarts,ActiveEnterTimestamp"], timeout=5)
    if code != 0:
        return {"status": "UNKNOWN", "message": out.strip() or "service unavailable", "metrics": {}, "last_update": time.time()}
    d={}
    for line in out.splitlines():
        if "=" in line:
            k,v=line.split("=",1); d[k]=v
    try: pid=int(d.get("MainPID","0")) or None
    except ValueError: pid=None
    active=d.get("ActiveState")
    status="HEALTHY" if active=="active" else ("FAILED" if active=="failed" else "DEGRADED")
    return {"status":status,"message":d.get("Result","") or active or "unknown","metrics":{"load_state":d.get("LoadState"),"active_state":active,"sub_state":d.get("SubState"),"main_pid":pid,"restart_count":int(d.get("NRestarts","0") or 0),"active_enter_timestamp":d.get("ActiveEnterTimestamp")},"last_update":time.time()}


def _platform_model_status() -> dict:
    import hashlib
    p=PLATFORM_MODEL
    if not p.is_file(): return {"status":"UNKNOWN","message":"active model unavailable","metrics":{},"last_update":time.time()}
    h=hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""): h.update(chunk)
    return {"status":"HEALTHY","message":"active model present","metrics":{"model_id":p.stem,"active_version":p.parent.name,"path":str(p),"size":p.stat().st_size,"sha256":h.hexdigest(),"validation":"file-present"},"last_update":time.time()}


def _platform_inference_status() -> dict:
    m=read_infer_metrics(); svc=_service_show(PLATFORM_INFER_SERVICE)
    cfg={}
    try: cfg=json.loads(PLATFORM_INFER_CONFIG.read_text(encoding="utf-8"))
    except Exception: pass
    if not m:
        return {"status":"UNKNOWN","message":"inference metrics unavailable","metrics":{"service":svc,"workers":cfg.get("workers"),"core_mask":cfg.get("cores"),"model":cfg.get("model")},"last_update":time.time()}
    errors=m.get("errors")
    status="HEALTHY" if svc["status"]=="HEALTHY" and errors==0 else ("FAILED" if errors else "DEGRADED")
    return {"status":status,"message":"live inference metrics","metrics":{"service":svc,"workers":cfg.get("workers"),"core_mask":cfg.get("cores"),"model":cfg.get("model"),"capture_fps":m.get("capture_fps"),"inference_fps":m.get("pipeline_fps"),"rknn_latency_us":m.get("run_us"),"rga_latency_us":m.get("rga_us"),"decode_latency_us":m.get("decode_us"),"e2e_latency_us":m.get("e2e_us"),"processed":m.get("processed"),"captured":m.get("captured"),"dropped":m.get("dropped_latest"),"skipped":m.get("skipped"),"errors":errors,"npu_core0":m.get("npu0"),"npu_core1":m.get("npu1"),"npu_core2":m.get("npu2"),"resolution":"2560x1440","format":"BGR3","v4l2_errors":m.get("v4l2_errors"),"poll_timeouts":m.get("poll_timeouts")},"last_update":time.time()}


def platform_health() -> dict:
    runtime=_service_show(PLATFORM_CORE_SERVICE); inference=_platform_inference_status(); model=_platform_model_status()
    layers={"SYSTEM":_service_show(PLATFORM_SUPERVISOR_SERVICE),"RUNTIME":runtime,"CORE":runtime,"INFERENCE":inference,"CAPTURE":{"status":inference["status"],"message":inference["message"],"metrics":{k:inference["metrics"].get(k) for k in ("resolution","format","capture_fps","v4l2_errors","poll_timeouts")},"last_update":inference["last_update"]},"NPU":{"status":inference["status"],"message":inference["message"],"metrics":{k:inference["metrics"].get(k) for k in ("npu_core0","npu_core1","npu_core2")},"last_update":inference["last_update"]},"MODEL":model,"STORAGE":{"status":"HEALTHY","message":"filesystem usage","metrics":system_status().get("storage",{}),"last_update":time.time()}}
    statuses=[v.get("status") for v in layers.values() if isinstance(v,dict) and "status" in v]
    aggregate="FAILED" if "FAILED" in statuses else ("DEGRADED" if "DEGRADED" in statuses or "UNKNOWN" in statuses else "HEALTHY")
    return {"status":aggregate,"layers":layers,"last_update":time.time()}


def system_status() -> dict:
    """系统状态（授权/存储/更新/风扇）—— 全部真实读取。"""
    auth = {"valid": True, "type": "永久授权"}
    storage = {}
    try:
        st = shutil.disk_usage("/")
        storage = {"total": st.total, "used": st.used, "free": st.free,
                   "used_pct": round(st.used / st.total * 100, 1) if st.total else 0}
    except Exception:  # noqa: BLE001
        pass
    update = {"available": False, "version": "V0.01", "notes": "暂无更新信息"}
    temp = 0.0
    try:
        temp = float(hwmon().get("soc_temp_c") or 0)
    except Exception:  # noqa: BLE001
        pass
    fan = {"available": False, "reason": "未检测到PWM接口",
           "temperature_source": "-", "temp_c": temp, "pwm": 0, "rpm": 0,
           "error": "未检测到PWM接口"}
    return {"authorization": auth, "storage": storage, "update": update, "fan": fan}


def network_status() -> dict:
    """网络状态（Wi-Fi/AP/LAN 黑名单）—— nmcli 真实读取；无 nmcli 返回未连接。"""
    st = {"wifi_connected": False, "wifi_ssid": "", "ip": "", "ap_enabled": False,
          "hostname": _sys("/etc/hostname", "ttbox"), "nmcli": False}
    try:
        code, text = _run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"], timeout=10)
        if code == 0:
            st["nmcli"] = True
            for line in text.splitlines():
                parts = line.split(":")
                if len(parts) >= 4 and parts[1] in ("wifi", "ethernet") and parts[2] == "connected":
                    st["wifi_connected"] = True
                    st["wifi_ssid"] = parts[3]
        code2, ip = _run(["hostname", "-I"], timeout=5)
        if code2 == 0 and ip.strip():
            st["ip"] = ip.strip().split()[0]
    except Exception:  # noqa: BLE001
        pass
    return st


def hailo_status() -> dict:
    """真实检测 Hailo-8 PCIe 加速卡（lspci/lsmod）。"""
    out = {"detected": False, "pcie": "", "driver": "", "runtime": ""}
    code, txt = _run(["lspci", "-nn"], timeout=5)
    if code == 0:
        for line in txt.splitlines():
            low = line.lower()
            if "1e60" in low or ("hailo" in low and "pci" in low):
                out["detected"] = True
                out["pcie"] = line.strip()
                break
    code, txt = _run(["lsmod"], timeout=5)
    if "hailo" in txt.lower():
        out["driver"] = "hailo" if "hailo" in txt.lower() else ""
        if "hailo8" in txt.lower():
            out["driver"] = "hailo8"
    code, txt = _run(["python3", "-c", "import hailo_platform; print(hailo_platform.__version__)"],
                     timeout=8)
    if code == 0 and txt.strip():
        out["runtime"] = txt.strip()
    return out


def system_power(action: str) -> tuple[bool, str]:
    """重启 / 关机（sudo systemctl）。"""
    if action == "reboot":
        _run(["sudo", "systemctl", "reboot"], timeout=5)
        return True, "设备正在重启，请稍候重新连接"
    if action == "poweroff":
        _run(["sudo", "systemctl", "poweroff"], timeout=5)
        return True, "设备已关机"
    return False, "action 必须为 reboot|poweroff"


def set_hostname(name) -> tuple[bool, str]:
    """修改主机名（hostnamectl + /etc/hostname 兜底）。"""
    import re as _re
    name = _re.sub(r"[^a-zA-Z0-9.-]", "", str(name or "")).strip()
    if not name:
        return False, "主机名不能为空（仅限字母/数字/./-）"
    _run(["sudo", "hostnamectl", "set-hostname", name], timeout=10)
    _run(["sudo", "sh", "-c", 'echo "%s" > /etc/hostname' % name], timeout=10)
    return True, f"主机名已更新为 {name}（网络刷新后生效）"


def kmbox_save(cfg: dict) -> tuple[bool, str]:
    """保存键鼠盒子配置（写入 profile.features.mouse_output.kmboxnet，C 桥热更新）。"""
    prof = read_profile()
    k = prof.setdefault("features", {}).setdefault("mouse_output", {}).setdefault("kmboxnet", {})
    for key in ("enabled", "ip", "port", "uuid", "monitor_port", "timeout_ms", "encrypted"):
        if key in cfg:
            k[key] = cfg[key]
    return write_profile(prof)


def wifi_action(action: str, req: dict = None) -> tuple[bool, str, dict]:
    """Wi-Fi 操作（板端无 nmcli → 如实返回未检测；后续接入网络管理再实现）。"""
    req = req or {}
    nmcli_ok = bool(_run(["which", "nmcli"], timeout=5)[0] == 0)
    if not nmcli_ok:
        detail = {"scan": "系统未安装 nmcli，Wi-Fi 扫描不可用",
                  "connect": "系统未安装 nmcli，Wi-Fi 连接不可用",
                  "fallback": "系统未安装 nmcli，重置默认 Wi-Fi 不可用",
                  "ap_apply": "系统未安装 nmcli，AP 热点不可用",
                  "client_activate": "系统未安装 nmcli，切回 Wi-Fi 不可用",
                  "default": "系统未安装 nmcli，Wi-Fi 管理待接入"}.get(
            action, "系统未安装 nmcli，Wi-Fi 管理待接入")
        return False, detail, {"nmcli": False}
    return False, "Wi-Fi 管理待接入（nmcli 命令暂未对接）", {"nmcli": True}


def state() -> dict:
    s = {
        "hostname": _sys("/etc/hostname", "ttbox"),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "services": {
            "runtime": svc_active("ttbox-runtime"),
            "web": svc_active("ttbox-web"),
            "hid": svc_active("ttbox-hid"),
            "inference": svc_active("ttbox-infer"),
            "hid-forward": svc_active("ttbox-hid-forward"),
            "hid-watchdog": svc_active("ttbox-hid-watchdog"),
            "edid": svc_ok("ttbox-edid-apply"),
        },
        "inference": read_infer_fps(),
        "metrics": read_infer_metrics(),
        "mouse": read_mouse_stats(),  # A10：物理/AI/最终增量 + HID TX
        "hwmon": hwmon(),
        "profile": read_profile(),
        "system": system_status(),
        "network": network_status(),
    }
    s["kernel"] = kernel_version()
    return s


def mouse_state_api() -> dict:
    """A10.1：/api/state.mouse —— AI 鼠标注入完整实时状态（真实数据源）。"""
    m = read_infer_metrics()
    ms = read_mouse_stats()
    prof = read_profile()
    mo = (prof or {}).get("mouse", {})
    out = {
        "state": m.get("mouse_state", "IDLE"),
        "enabled": bool(mo.get("enabled", False)),
        "proxy_mode": mo.get("proxy_mode", "full_passthrough"),
        "aiming": bool(ms.get("aiming")),
        "hotkey": ms.get("hotkey", mo.get("aim_hotkey", 2)),
        "target_found": (m.get("mouse_class", -1) >= 0),
        "target_class": m.get("mouse_class", -1),
        "target_confidence": m.get("mouse_conf", 0.0),
        "target_x": m.get("mouse_target_x", 0.0),
        "target_y": m.get("mouse_target_y", 0.0),
        "aim_x": m.get("mouse_aim_x", 0.0),
        "aim_y": m.get("mouse_aim_y", 0.0),
        "error_x": m.get("mouse_err_x", 0.0),
        "error_y": m.get("mouse_err_y", 0.0),
        "velocity_x": m.get("mouse_vel_x", 0.0),
        "velocity_y": m.get("mouse_vel_y", 0.0),
        "prediction_x": m.get("mouse_pred_x", 0.0),
        "prediction_y": m.get("mouse_pred_y", 0.0),
        "ai_dx": m.get("mouse_ai_dx", 0),
        "ai_dy": m.get("mouse_ai_dy", 0),
        "physical_dx": ms.get("phys_dx", 0),
        "physical_dy": ms.get("phys_dy", 0),
        "final_dx": ms.get("final_dx", 0),
        "final_dy": ms.get("final_dy", 0),
        "mouse_frames": m.get("mouse_frames", 0),
        "hid_tx": ms.get("hid_tx", 0),
        "blocked_x": bool(mo.get("block_physical_x", False)),
        "blocked_y": bool(mo.get("block_physical_y", False)),
        "detection_count": m.get("mouse_det_count", 0),
        "detections": m.get("detections", 0),
    }
    # A10.3：优先用 C++ 200ms 高频目标状态（比 [METRICS] 5s 更实时）
    tgt = _read_target_state()
    if tgt is not None and _target_fresh():
        out["state"] = tgt.get("state", out["state"])
        out["target_found"] = bool(tgt.get("found"))
        out["target_class"] = tgt.get("cls", -1)
        out["target_confidence"] = tgt.get("conf", 0.0)
        out["target_x"] = tgt.get("x", 0.0)
        out["target_y"] = tgt.get("y", 0.0)
        out["aim_x"] = tgt.get("aim_x", 0.0)
        out["aim_y"] = tgt.get("aim_y", 0.0)
        out["error_x"] = tgt.get("err_x", 0.0)
        out["error_y"] = tgt.get("err_y", 0.0)
        out["ai_dx"] = tgt.get("ai_dx", 0)
        out["ai_dy"] = tgt.get("ai_dy", 0)
        out["detection_count"] = tgt.get("dets", out["detection_count"])
    return out


def read_profile() -> dict:
    try:
        return json.loads(PROFILE_FILE.read_text())
    except Exception:  # noqa: BLE001
        return {}


def _sync_ai_controller_to_mouse(data: dict) -> None:
    """前端 collectProfile 把 YU controller 参数放在 features.ai_controller；
    C++ AimThread 从 profile.mouse 读取。这里做双写，保证两条链都有。"""
    try:
        feats = (data or {}).get("features") or {}
        ac = feats.get("ai_controller") or {}
        if not isinstance(ac, dict):
            return
        mo = data.setdefault("mouse", {})
        mo["predict_x"] = float(ac.get("predict_x") or mo.get("predict_x") or 0.5)
        mo["predict_y"] = float(ac.get("predict_y") or mo.get("predict_y") or 0.4)
        if "smooth_x" in ac: mo["smooth_x"] = float(ac["smooth_x"])
        if "smooth_y" in ac: mo["smooth_y"] = float(ac["smooth_y"])
        if "output_deadzone" in ac: mo["output_deadzone"] = float(ac["output_deadzone"])
        if "selector_search_radius" in ac: mo["selector_search_radius"] = float(ac["selector_search_radius"])
        mo["aim_fire_lock_y"] = bool(ac.get("aim_fire_lock_y", False))
        mo["y_axis_fire_hotkey"] = int(aim_hk_bit(ac.get("y_axis_fire_hotkey") or "left") or 1)
        if "y_axis_fire_release_delay_sec" in ac:
            mo["y_axis_fire_release_delay_sec"] = float(ac["y_axis_fire_release_delay_sec"])
        # 插件配置（pull_curve / continuous_lead / humanize）
        mo["pull_curve"] = {
            "enabled": bool(ac.get("pull_curve_enabled", True)),
            "strength": float(ac.get("pull_curve_strength", 0.8) or 0.8),
            "jitter_px": float(ac.get("pull_curve_jitter_px", 3.0) or 3.0),
            "min_distance": float(ac.get("pull_curve_min_distance", 80.0) or 80.0),
        }
        mo["continuous_lead"] = {
            "enabled": bool(ac.get("continuous_lead_enabled", False)),
            "enter_distance": float(ac.get("continuous_lead_enter_distance", 150.0) or 150.0),
            "scale": float(ac.get("continuous_lead_scale", 0.5) or 0.5),
            "fade_in_ms": float(ac.get("continuous_lead_fade_in_ms", 300.0) or 300.0),
            "fade_out_ms": float(ac.get("continuous_lead_fade_out_ms", 300.0) or 300.0),
            "near_disable_ratio": float(ac.get("continuous_lead_near_disable_ratio", 0.66) or 0.66),
        }
        # humanize：recoil 卡片收集（recoil.humanize_*），双写到 mouse.humanize
        rc = feats.get("recoil") or {}
        mo["humanize"] = {
            "enabled": bool(rc.get("humanize_enabled", True)),
            "curve_strength": float(rc.get("humanize_curve_strength", 0.45) or 0.45),
            "jitter_px": float(rc.get("humanize_jitter_px", 0.25) or 0.25),
            "jitter_frequency": float(rc.get("humanize_jitter_frequency", 8.0) or 8.0),
        }
    except Exception:  # noqa: BLE001
        pass


def write_profile(data: dict, protect_enabled: bool = True) -> tuple[bool, str]:
    """写入 RuntimeProfile JSON（推理进程 watcher 检测后热更新）。
    protect_enabled=True：存在已激活 aim 方案时，mouse.enabled 由"应用方案"管理，
    前端保存任意参数（可能带旧缓存 enabled=false）不得改它；apply_aim_profile 显式设
    protect_enabled=False 以真正启用/关闭自瞄。"""
    if not isinstance(data, dict):
        return False, "body 必须为 JSON object"
    # 把 features.ai_controller（前端收集的 YU controller 插件参数）同步到 mouse 段，
    # 供 C++ RuntimeProfile（AimThread 控制链）读取
    _sync_ai_controller_to_mouse(data)
    try:
        if protect_enabled and "mouse" in data and isinstance(data["mouse"], dict) \
                and "enabled" in data["mouse"]:
            active = int(ACTIVE_AIM_PROFILE_FILE.read_text().strip())
            if active >= 0 and PROFILE_FILE.exists():
                cur_enabled = json.loads(PROFILE_FILE.read_text()).get("mouse", {}).get("enabled", False)
                data["mouse"]["enabled"] = cur_enabled
    except Exception:  # noqa: BLE001
        pass
    try:
        tmp = PROFILE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        os.replace(tmp, PROFILE_FILE)
    except Exception as exc:  # noqa: BLE001
        return False, f"写入失败: {exc}"
    features_to_conf(data)
    return True, "RuntimeProfile 已更新（推理进程自动热更新）"


def _flatten(obj, prefix: str, out: dict) -> None:
    """递归扁平化 dict → key=value（列表 join 逗号）。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flatten(v, prefix + k + ".", out)
    elif isinstance(obj, list):
        out[prefix.rstrip(".")] = ",".join(str(x) for x in obj)
    else:
        out[prefix.rstrip(".")] = ("1" if obj else "0") if isinstance(obj, bool) else str(obj)


def features_to_conf(data: dict) -> None:
    """把 profile.features 扁平化写 /run/ttbox-features.conf（C 桥轮询解析）。
    附送 mouse.* 运行时值（kp 用于 px→count 换算、block_physical 等）。"""
    try:
        feats = (data or {}).get("features") or {}
        mo = (data or {}).get("mouse") or {}
        out = {}
        _flatten(feats, "", out)
        out["mouse.kp_x"] = str(mo.get("kp_x", 17))
        out["mouse.kp_y"] = str(mo.get("kp_y", 10))
        out["mouse.aim_hotkey"] = str(mo.get("aim_hotkey", 2))
        out["mouse.enabled"] = "1" if mo.get("enabled") else "0"
        out["mouse.block_physical_x"] = "1" if mo.get("block_physical_x") else "0"
        out["mouse.block_physical_y"] = "1" if mo.get("block_physical_y") else "0"
        out["mouse.calibrating"] = "1" if mo.get("calibrating") else "0"
        lines = [f"{k}={out[k]}" for k in sorted(out)]
        tmp = FEATURES_CONF.with_suffix(".conf.tmp")
        tmp.write_text("\n".join(lines) + "\n")
        os.replace(tmp, FEATURES_CONF)
    except Exception:  # noqa: BLE001
        pass


# ---- 预设参数（YU preset-page：保存/应用/删除） ----
def presets_list() -> dict:
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for f in sorted(PRESETS_DIR.glob("*.json")):
        try:
            meta = json.loads(f.read_text()).get("_meta", {})
        except Exception:  # noqa: BLE001
            meta = {}
        items.append({"name": f.stem, "time": meta.get("saved_at", ""),
                      "model_id": meta.get("model_id", "")})
    return {"presets": items}


def preset_save(name: str) -> tuple[bool, str]:
    name = os.path.basename(name or "").strip()
    if not name:
        return False, "预设名称不能为空"
    prof = read_profile()
    prof["_meta"] = {"saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "model_id": read_active_model()}
    PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        (PRESETS_DIR / f"{name}.json").write_text(
            json.dumps(prof, ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001
        return False, f"保存失败: {exc}"
    return True, f"预设「{name}」已保存"


def preset_apply(name: str) -> tuple[bool, str]:
    name = os.path.basename(name or "").strip()
    f = PRESETS_DIR / f"{name}.json"
    if not f.exists():
        return False, f"预设「{name}」不存在"
    try:
        prof = json.loads(f.read_text())
    except Exception as exc:  # noqa: BLE001
        return False, f"读取失败: {exc}"
    prof.pop("_meta", None)
    return write_profile(prof)


def preset_delete(name: str) -> tuple[bool, str]:
    name = os.path.basename(name or "").strip()
    f = PRESETS_DIR / f"{name}.json"
    if not f.exists():
        return False, f"预设「{name}」不存在"
    f.unlink()
    return True, f"预设「{name}」已删除"


# ===== 待接入功能补齐：主题商店 / 串口盒子 / USB 诊断 / 更新检查 / 局域网 / 预设清理 / 画圈 =====

THEMES_DIR = TTBOX / "web" / "themes"
UPDATES_DIR = TTBOX / "updates"


def themes_list() -> dict:
    """主题商店：扫描本地主题目录（themes/*.css，html[data-theme="ID"] 变量覆盖）。"""
    try:
        THEMES_DIR.mkdir(parents=True, exist_ok=True)
        themes = []
        for f in sorted(THEMES_DIR.glob("*.css")):
            try:
                txt = f.read_text(errors="ignore")
            except Exception:  # noqa: BLE001
                txt = ""
            mt = re.search(r'data-theme="([^"]+)"', txt)
            tid = mt.group(1) if mt else f.stem
            mt2 = re.search(r"/\*\s*title:\s*(.+?)\s*\*/", txt)
            title = mt2.group(1).strip() if mt2 else f.stem
            themes.append({"id": tid, "file": f.name, "title": title})
        return {"themes": themes, "dir": str(THEMES_DIR)}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "themes": []}


def serial_devices() -> dict:
    """串口盒子：扫描 /dev/ttyUSB* /dev/ttyACM* 并按 VID 识别芯片。"""
    devs = []
    paths = sorted(Path("/dev").glob("ttyUSB*")) + sorted(Path("/dev").glob("ttyACM*"))
    for dev in paths:
        name = dev.name
        chip = "未知芯片"
        try:
            vid = _sys(f"/sys/class/tty/{name}/device/../idVendor", "").lower()
            pid = _sys(f"/sys/class/tty/{name}/device/../idProduct", "").lower()
            chip = {"1a86": "CH34x (CH340/CH341)", "10c4": "CP210x (Silicon Labs)",
                    "067b": "Prolific PL2303", "0403": "FTDI",
                    "1a6e": "WCH CH32 / CH9102"}.get(vid, f"USB {vid}:{pid}")
        except Exception:  # noqa: BLE001
            pass
        devs.append({"dev": str(dev), "chip": chip})
    return {"devices": devs, "count": len(devs)}


def usb_diagnostics() -> dict:
    """USB 诊断：收集 lsusb / hidraw / input / dmesg 汇总文本。"""
    parts = []
    code, out = _run(["lsusb"], timeout=8)
    parts.append("== lsusb ==")
    parts.append(out)
    code, out = _run(["ls", "-l", "/sys/class/hidraw/"], timeout=5)
    parts.append("== hidraw ==")
    parts.append(out)
    code, out = _run(["ls", "-l", "/dev/hidg0", "/dev/hidg1", "/dev/hidg2"], timeout=5)
    parts.append("== hidg ==")
    parts.append(out)
    code, out = _run(["cat", "/proc/bus/input/devices"], timeout=5)
    parts.append("== input devices ==")
    parts.append(out)
    code, out = _run(["bash", "-c", "sudo dmesg 2>/dev/null | tail -80"], timeout=10)
    parts.append("== dmesg (tail 80) ==")
    parts.append(out)
    return {"text": "\n".join(parts), "generated": time.strftime("%Y-%m-%d %H:%M:%S")}


def update_check() -> dict:
    """更新检查：检测本地更新包目录。无包 → 如实反馈未配置更新源。"""
    try:
        UPDATES_DIR.mkdir(parents=True, exist_ok=True)
        pkgs = sorted(f.name for f in UPDATES_DIR.iterdir() if f.is_file())
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "available": False, "packages": []}
    return {"current": "V0.01", "available": bool(pkgs), "packages": pkgs,
            "source": str(UPDATES_DIR),
            "notes": f"检测到 {len(pkgs)} 个更新包" if pkgs else "未配置更新源（目录为空）"}


def lan_action(req: dict) -> tuple[bool, str, dict]:
    """局域网扫描 / 拉黑 / 清黑名单（真实 ip/iptables 操作）。"""
    action = str(req.get("action", ""))
    if action == "scan":
        devices, blocked = [], []
        code, out = _run(["ip", "neigh"], timeout=8)
        if code != 0:
            code, out = _run(["bash", "-c", "arp -a 2>/dev/null"], timeout=8)
        seen = set()
        for line in out.splitlines():
            ip = ""
            for p in line.split():
                if p.count(".") == 3 and p.replace(".", "").isdigit():
                    ip = p
                    break
            if not ip or ip in seen:
                continue
            seen.add(ip)
            mac = ""
            low = line.lower()
            for p in line.split():
                if ":" in p and len(p) == 17 and all(c in "0123456789abcdef" for c in p.replace(":", "")):
                    mac = p
                    break
            devices.append({"ip": ip, "mac": mac or "unknown"})
        code, out = _run(["sudo", "iptables", "-L", "INPUT", "-n", "--line-numbers"], timeout=8)
        for line in out.splitlines():
            if "DROP" not in line:
                continue
            for p in line.split():
                if p.count(".") == 3 and p.replace(".", "").isdigit():
                    blocked.append(p)
                    break
        return True, f"发现 {len(devices)} 台设备（已拉黑 {len(blocked)}）", {"devices": devices, "blocked": blocked}
    if action == "block":
        ip = str(req.get("ip", "")).strip()
        if ip.count(".") != 3 or not ip.replace(".", "").isdigit():
            return False, "IP 地址无效", {}
        code, _ = _run(["sudo", "iptables", "-C", "INPUT", "-s", ip, "-j", "DROP"], timeout=8)
        if code == 0:
            return True, f"{ip} 已在黑名单", {}
        code, out = _run(["sudo", "iptables", "-I", "INPUT", "-s", ip, "-j", "DROP"], timeout=8)
        return code == 0, (f"已拉黑 {ip}" if code == 0 else f"拉黑失败: {out[:200]}"), {}
    if action == "clear":
        code, out = _run(["sudo", "iptables", "-F", "INPUT"], timeout=8)
        return code == 0, ("已清空 INPUT 拉黑规则" if code == 0 else f"清空失败: {out[:200]}"), {}
    return False, "action 必须为 scan|block|clear", {}


def presets_cleanup() -> tuple[bool, str, dict]:
    """清理未使用预设：仅保留被 aim 方案引用（preset_name）的预设。"""
    refs = set()
    for p in read_aim_profiles():
        pn = p.get("preset_name", "")
        if pn:
            refs.add(pn)
    removed = []
    try:
        PRESETS_DIR.mkdir(parents=True, exist_ok=True)
        for f in sorted(PRESETS_DIR.glob("*.json")):
            if f.stem not in refs:
                try:
                    f.unlink()
                    removed.append(f.stem)
                except Exception:  # noqa: BLE001
                    pass
    except Exception as exc:  # noqa: BLE001
        return False, f"清理失败: {exc}", {}
    return True, f"已清理 {len(removed)} 个未使用预设", {"removed": removed}


def draw_circle_test(req: dict) -> tuple[bool, str]:
    """测试画圈：向 hidg1 注入正弦移动（9 字节 HID 报告，与 C 桥 send_move 一致）。"""
    import math as _math
    try:
        radius = min(max(int(req.get("radius", 40)), 1), 100)
        rounds = min(max(int(req.get("rounds", 2)), 1), 5)
        steps = min(max(int(req.get("steps", 120)), 20), 400)
    except Exception:  # noqa: BLE001
        return False, "参数无效"
    try:
        fd = os.open("/dev/hidg1", os.O_WRONLY | os.O_NONBLOCK)
    except Exception as exc:  # noqa: BLE001
        return False, f"无法打开 /dev/hidg1（HID 鼠标未就绪）: {exc}"
    try:
        prev_x = prev_y = 0.0
        for _ in range(rounds):
            for i in range(steps):
                ang = 2.0 * _math.pi * (i / steps)
                x = _math.cos(ang) * radius
                y = _math.sin(ang) * radius
                dx = int(round(x - prev_x))
                dy = int(round(y - prev_y))
                prev_x, prev_y = x, y
                # [ReportID, buttons(2), dx(int16 LE), dy(int16 LE), wheel, pan]
                buf = bytes([0x02, 0, 0, dx & 0xFF, (dx >> 8) & 0xFF,
                             dy & 0xFF, (dy >> 8) & 0xFF, 0, 0])
                os.write(fd, buf)
                time.sleep(0.004)
        return True, f"已画 {rounds} 圈（半径 {radius}，{steps} 步/圈）"
    except Exception as exc:  # noqa: BLE001
        return False, f"画圈中断: {exc}"
    finally:
        try:
            os.close(fd)
        except Exception:  # noqa: BLE001
            pass


# ===== A10.2：aim_profiles 多方案 + 自动标定（对齐 YU 实测契约） =====
AIM_PROFILES_FILE = TTBOX / "config" / "aim_profiles.json"
ACTIVE_AIM_PROFILE_FILE = TTBOX / "config" / "active_aim_profile.txt"
CALIBRATION_FILE = TTBOX / "config" / "calibration.json"
AIM_FIFO = Path("/run/ttbox-aim.fifo")


def aim_hk_bit(name: str) -> int:
    """热键名 → 位掩码（与 C 桥 hk_bit / C++ aim_hotkey 一致）。"""
    return {"left": 1, "right": 2, "middle": 4, "back": 8, "forward": 16}.get(name or "", 0)


def read_active_model() -> str:
    try:
        return ACTIVE_MODEL_FILE.read_text().strip()
    except Exception:  # noqa: BLE001
        return ""


# ---- aim_profiles 多方案（YU：hotkey/类别/偏移/灵敏度/FOV缩放/class_offsets） ----
def read_aim_profiles() -> list:
    try:
        data = json.loads(AIM_PROFILES_FILE.read_text())
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("profiles"), list):
            return data["profiles"]
    except Exception:  # noqa: BLE001
        pass
    return []


def write_aim_profiles(profiles: list) -> tuple[bool, str]:
    if not isinstance(profiles, list):
        return False, "aim_profiles 必须为数组"
    AIM_PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp = AIM_PROFILES_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(profiles, ensure_ascii=False, indent=2))
        os.replace(tmp, AIM_PROFILES_FILE)
    except Exception as exc:  # noqa: BLE001
        return False, f"写入失败: {exc}"
    return True, f"已保存 {len(profiles)} 个瞄准方案"


def apply_aim_profile(profiles: list, index: int) -> tuple[bool, str]:
    """把选中的方案映射到 RuntimeProfile（真实热更新生效，C++ 零改动）。
    YU 归一化 offset(0~1) → TTBox px；sensitivity/fov_scale 直映射；class_filter_mask → class_filter；
    主/副热键 + 触发方式 → aim_hotkey/aim_hotkey2/aim_hotkey_mode（C++ 控制帧 + C 桥生效）。"""
    if not (0 <= index < len(profiles)):
        return False, f"方案索引 {index} 越界"
    p = profiles[index]
    prof = read_profile()
    mo = prof.setdefault("mouse", {})
    inf = prof.setdefault("inference", {})
    pv = prof.setdefault("preview", {})
    crop = float(pv.get("roi_w") or pv.get("width") or 320)
    if crop <= 0:
        crop = 320
    # 应用方案 = 启用自瞄（按住方案热键即触发 AI 瞄准，与鼠标身份模式解耦）
    mo["enabled"] = True
    # 热键：主键 + 副键 + 触发方式（any=任一 / all=同时按下）
    mo["aim_hotkey"] = aim_hk_bit(p.get("hotkey")) or 2
    mo["aim_hotkey2"] = aim_hk_bit(p.get("hotkey2"))
    mo["aim_hotkey_mode"] = "all" if str(p.get("hotkey_mode", "any")) == "all" else "any"
    if "sensitivity" in p:
        mo["sensitivity"] = max(0.1, min(3.0, float(p.get("sensitivity") or 1)))
    if "fov_scale" in p:
        mo["fov_range"] = max(0.05, min(4.0, float(p.get("fov_scale") or 1)))
    if "offset_x" in p:
        mo["aim_offset_x"] = round((float(p.get("offset_x") or 0.5) - 0.5) * crop, 2)
    if "offset_y" in p:
        mo["aim_offset_y"] = round((float(p.get("offset_y") or 0.5) - 0.5) * crop, 2)
    mask = int(p.get("class_filter_mask") or 0)
    inf["class_filter"] = [c for c in range(32) if mask & (1 << c)]
    co_list = []
    for co in p.get("class_offsets") or []:
        try:
            co_list.append({
                "class_id": int(co.get("class_id", 0)),
                "offset_x": round((float(co.get("offset_x") or 0.5) - 0.5) * crop, 2),
                "offset_y": round((float(co.get("offset_y") or 0.5) - 0.5) * crop, 2),
                "priority": int(co.get("priority", 0)),
                "force_priority_switch": bool(co.get("force_priority_switch", False)),
                "force_switch_delay_ms": int(co.get("force_switch_delay_ms", 30)),
            })
        except Exception:  # noqa: BLE001
            pass
    mo["class_offsets"] = co_list
    ok, detail = write_profile(prof, protect_enabled=False)
    # 记录当前激活方案（刷新后 UI 标记）
    if ok:
        try:
            ACTIVE_AIM_PROFILE_FILE.parent.mkdir(parents=True, exist_ok=True)
            ACTIVE_AIM_PROFILE_FILE.write_text(str(index))
        except Exception:  # noqa: BLE001
            pass
    return ok, (detail + f"；已激活方案[{index}]「{p.get('hotkey', '')}」")


# ---- 自动标定状态机（对齐 YU AutoCalibrationSession） ----
_cal = {
    "phase": "idle",        # idle|stabilize|moving|measuring|done|error|cancelled
    "status": "idle",       # idle|running|success|failed
    "ready": False,
    "reason": "idle",
    "total_rounds": 10,
    "round": 0,
    "progress": 0.0,
    "candidate_count": 0,
    "stable_ms": 0,
    "round_gains": [],
    "thread": None,
}
_cal_lock = threading.Lock()


def read_calibration() -> dict:
    try:
        return json.loads(CALIBRATION_FILE.read_text())
    except Exception:  # noqa: BLE001
        return {}


def write_calibration(data: dict) -> tuple[bool, str]:
    CALIBRATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp = CALIBRATION_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        os.replace(tmp, CALIBRATION_FILE)
    except Exception as exc:  # noqa: BLE001
        return False, f"写入失败: {exc}"
    return True, "标定参数已保存"


def clear_calibration() -> None:
    try:
        CALIBRATION_FILE.unlink()
    except FileNotFoundError:
        pass


def calibration_state() -> dict:
    with _cal_lock:
        st = {k: _cal[k] for k in ("phase", "status", "ready", "reason", "total_rounds",
                                   "round", "progress", "candidate_count", "stable_ms")}
        st["calibration"] = read_calibration() or None
        st["running"] = bool(_cal["thread"] and _cal["thread"].is_alive())
    return st


def _read_target_state() -> dict | None:
    """读 C++ AimThread 高频目标状态（/run/ttbox-target.json）。"""
    try:
        return json.loads(TARGET_STATE_FILE.read_text())
    except Exception:  # noqa: BLE001
        return None


def _target_fresh(age_s: float = 1.2) -> bool:
    """目标状态文件是否新鲜（C++ 每 200ms 更新；超过 age_s 未更新 = 推理已停止）。"""
    try:
        return (time.time() - TARGET_STATE_FILE.stat().st_mtime) < age_s
    except Exception:  # noqa: BLE001
        return False


def _infer_fresh(age_s: float = 8.0) -> bool:
    """推理日志最近是否在更新（[METRICS] 每 5s 一行；>8s 未更新 = 已停止）。"""
    try:
        return (time.time() - INFER_LOG.stat().st_mtime) < age_s
    except Exception:  # noqa: BLE001
        return False


def _calib_target():
    """真实目标中心（crop 系 px），来自 C++ 8ms 高频目标状态。
    文件缺失/过期（推理未运行）→ None，杜绝"陈旧日志假装有目标"导致没目标也能标定。
    target.json 格式：{"state":"IDLE|SELECTING|AIMING","found":true,"x":N,"y":N,"conf":N,...}"""
    st = _read_target_state()
    if st is not None:
        if not _target_fresh():
            return None
        if not st.get("found"):
            return None
        return (float(st.get("x", 0.0)), float(st.get("y", 0.0)),
                float(st.get("conf", 0.0)))
    # 兼容旧二进制：回退 [METRICS]，仍要求日志新鲜
    if not _infer_fresh():
        return None
    st = mouse_state_api()
    if not st.get("target_found"):
        return None
    return (st["target_x"], st["target_y"], st["target_confidence"])


def _fifo_inject(dx: int, dy: int) -> bool:
    """写 AI 移动帧到 C 桥 FIFO（type=0x01 + dx/dy int16 LE，5 字节）。"""
    try:
        with open(AIM_FIFO, "wb", buffering=0) as f:
            f.write(bytes([0x01]) + int(dx).to_bytes(2, "little", signed=True)
                    + int(dy).to_bytes(2, "little", signed=True))
        return True
    except Exception:  # noqa: BLE001
        return False


def _calib_sample_center(n: int = 3):
    """采样 n 次目标中心取平均（真实检测值）；无目标返回 None。"""
    pts = []
    for _ in range(n):
        t = _calib_target()
        if t is not None:
            pts.append(t[:2])
        time.sleep(0.05)
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _calib_set(**kw) -> None:
    with _cal_lock:
        _cal.update(kw)


def start_auto_calibration() -> tuple[bool, str]:
    with _cal_lock:
        if _cal["thread"] and _cal["thread"].is_alive():
            return False, "标定已在运行中"
    if not AIM_FIFO.exists():
        return False, "AI 鼠标 FIFO 不存在（请先启动推理服务）"
    if not _target_fresh():
        return False, "推理服务未运行或目标反馈未就绪（请先启动推理）"
    if _calib_target() is None:
        return False, "未识别到目标，无法开始标定（请将准星对准画面中的目标，等待检测框稳定出现）"
    th = threading.Thread(target=_calib_worker, daemon=True)
    with _cal_lock:
        _cal["thread"] = th
    th.start()
    return True, "标定已启动"


def cancel_auto_calibration() -> None:
    _calib_set(status="idle", phase="cancelled", ready=False, reason="已取消")


def _calib_worker() -> None:
    """标定包装：标定期间临时启用 AI 注入通道 + 标定模式（C 桥无条件注入 AI 移动帧），
    结束（成功/失败/取消）后恢复原 enabled/calibrating。"""
    prof0 = read_profile()
    was_enabled = bool((prof0.get("mouse") or {}).get("enabled"))
    mo0 = prof0.setdefault("mouse", {})
    mo0["enabled"] = True
    mo0["calibrating"] = True
    write_profile(prof0, protect_enabled=False)
    try:
        _calib_worker_inner()
    finally:
        try:
            prof = read_profile()
            prof.setdefault("mouse", {})
            if not was_enabled:
                prof["mouse"]["enabled"] = False
            prof["mouse"]["calibrating"] = False
            write_profile(prof, protect_enabled=False)
        except Exception:  # noqa: BLE001
            pass


def _calib_worker_inner() -> None:
    """真实标定闭环：稳定检测 → X 轴往返注入(count) → 目标位移(px) → gain=px/count。
    对齐 YU 实测：10 轮、幅度 8→32、中值去抖、Y 轴复用 X。"""
    _calib_set(status="running", phase="stabilize", reason="检测目标稳定性",
               round=0, progress=0.0, round_gains=[], candidate_count=0, stable_ms=0)
    # 1) stabilize：目标 jitter<1px 持续 800ms（对齐 YU stable_ms≈799）
    win, stable_start = [], None
    deadline = time.time() + 12.0
    while time.time() < deadline:
        if _cal["status"] != "running":
            _calib_set(phase="cancelled", reason="已取消")
            return
        t = _calib_target()
        if t is None:
            win.clear()
            stable_start = None
            _calib_set(reason="no_target", candidate_count=0, stable_ms=0)
            time.sleep(0.1)
            continue
        win.append(t[:2])
        if len(win) > 10:
            win.pop(0)
        with _cal_lock:
            _cal["candidate_count"] = len(win)
        if len(win) >= 6:
            jx = max(p[0] for p in win) - min(p[0] for p in win)
            jy = max(p[1] for p in win) - min(p[1] for p in win)
            if jx < 1.0 and jy < 1.0:
                if stable_start is None:
                    stable_start = time.time()
                stable_ms = int((time.time() - stable_start) * 1000)
                _calib_set(stable_ms=stable_ms, ready=True, reason="目标稳定")
                if stable_ms >= 800:
                    break
            else:
                stable_start = None
                _calib_set(ready=False, reason="target_unstable", stable_ms=0)
        time.sleep(0.05)
    else:
        _calib_set(status="failed", phase="error", reason="目标稳定检测超时", ready=False)
        return
    # 2) rounds：X 轴往返移动（幅度 8→32，对齐 YU），闭环测 px/count。
    #    反馈源=C++ 200ms 高频目标文件 → 注入后 1s 采样窗口足以捕获位移。
    total, gains, delays = 10, [], []
    for r in range(total):
        if _cal["status"] != "running":
            _calib_set(phase="cancelled", reason="已取消")
            return
        amp = int(8 + r * (24 / 9))
        _calib_set(phase="moving", round=r + 1, progress=r / total)
        base = _calib_sample_center(3)
        if base is None:
            _calib_set(reason="no_target")
            time.sleep(0.2)
            continue
        # 正向注入 + 高频采样（取窗口内最大位移；目标短暂丢失不影响）
        t_inj = time.time()
        _fifo_inject(amp, 0)
        max_dx, moved = 0.0, False
        for _ in range(20):
            time.sleep(0.05)
            c = _calib_sample_center(1)
            if c is None:
                continue
            d = abs(c[0] - base[0])
            if d > max_dx:
                max_dx = d
            if not moved and d > 0.3:
                delays.append((time.time() - t_inj) * 1000)
                moved = True
        if max_dx >= 1.0:
            gains.append(max_dx / amp)
        _calib_set(phase="measuring", round_gains=gains[:])
        time.sleep(0.1)
        # 反向注入
        base2 = _calib_sample_center(3)
        if base2 is not None:
            _fifo_inject(-amp, 0)
            max_dx2 = 0.0
            for _ in range(20):
                time.sleep(0.05)
                c = _calib_sample_center(1)
                if c is None:
                    continue
                d = abs(base2[0] - c[0])
                if d > max_dx2:
                    max_dx2 = d
            if max_dx2 >= 1.0:
                gains.append(max_dx2 / amp)
        _calib_set(round_gains=gains[:])
    # 质量门槛：至少 5 个有效测量才接受。
    # 防"没目标/单次假阳也能标定"——目标必须真实随注入移动且多次一致。
    if len(gains) < 5:
        _calib_set(status="failed", phase="error",
                   reason=f"有效测量不足（{len(gains)}/20）：目标未随注入移动、已丢失或场景无真实目标",
                   ready=False)
        return
    _calib_set(progress=1.0, phase="done")
    g = sorted(gains)
    gain_x = g[len(g) // 2]
    gain_y = gain_x  # YU：Y 轴复用 X 值
    mean = sum(gains) / len(gains)
    conf = round(max(0.0, min(1.0, 1.0 - (sum(abs(v - mean) for v in gains)
                                           / len(gains)) / max(mean, 1e-6))), 3)
    delay_ms = round(sorted(delays)[len(delays) // 2], 2) if delays else 0.0
    calib = {
        "mouse_gain_x_px_per_count": round(gain_x, 4),
        "mouse_gain_y_px_per_count": round(gain_y, 4),
        "mouse_response_delay_ms": delay_ms,
        "mouse_calibration_applied": True,
        "valid": True,
        "confidence": conf,
        "calibrated_at": time.strftime("%Y%m%d_%H%M%S"),
        "model_id": read_active_model(),
        "capture": {"crop_size": int((read_profile().get("preview") or {}).get("roi_w") or 320)},
        "rounds": len(gains),
    }
    ok, _ = write_calibration(calib)
    # 应用：完整链路 out=err×kp×rate×sens×scale（counts）→ 游戏移动 G px/count
    # YU 实测 P 增益 = 1/7 = 0.142857（每帧修正 ~14% 误差），非一步到位
    # 收敛需 kp×rate×sens×scale×G = K_LOOP → kp = K_LOOP/(G×rate×sens×scale)
    K_LOOP = 0.142857  # 1/7, 对齐 YU 原机 P 增益
    if ok:
        prof = read_profile()
        mo = prof.setdefault("mouse", {})
        sx = (float(mo.get("rate_x", 1) or 1) * float(mo.get("sensitivity", 1) or 1)
              * float(mo.get("output_scale", 1) or 1))
        sy = (float(mo.get("rate_y", 1) or 1) * float(mo.get("sensitivity", 1) or 1)
              * float(mo.get("output_scale", 1) or 1))
        mo["kp_x"] = round(K_LOOP / max(gain_x * sx, 1e-6), 4)
        mo["kp_y"] = round(K_LOOP / max(gain_y * sy, 1e-6), 4)
        write_profile(prof)
    _calib_set(status="success", reason="标定完成", ready=True)


def save_calibration_manual(req: dict) -> tuple[bool, str]:
    """手动保存标定值（真实数据，来自用户输入）。"""
    try:
        gain_x = float(req.get("mouse_gain_x_px_per_count") or 0)
        gain_y = float(req.get("mouse_gain_y_px_per_count") or 0)
        delay = float(req.get("mouse_response_delay_ms") or 0)
    except (TypeError, ValueError):
        return False, "参数格式错误"
    if gain_x <= 0 or gain_y <= 0:
        return False, "增益必须 > 0"
    calib = {
        "mouse_gain_x_px_per_count": gain_x,
        "mouse_gain_y_px_per_count": gain_y,
        "mouse_response_delay_ms": delay,
        "mouse_calibration_applied": True,
        "valid": True,
        "confidence": float(req.get("confidence") or 0),
        "calibrated_at": time.strftime("%Y%m%d_%H%M%S"),
        "model_id": read_active_model(),
    }
    ok, detail = write_calibration(calib)
    if ok:
        prof = read_profile()
        mo = prof.setdefault("mouse", {})
        # 与自动标定一致：kp = K_LOOP/(G×rate×sens×scale)，K_LOOP=1/7 对齐 YU
        K_LOOP = 0.142857
        sx = (float(mo.get("rate_x", 1) or 1) * float(mo.get("sensitivity", 1) or 1)
              * float(mo.get("output_scale", 1) or 1))
        sy = (float(mo.get("rate_y", 1) or 1) * float(mo.get("sensitivity", 1) or 1)
              * float(mo.get("output_scale", 1) or 1))
        mo["kp_x"] = round(K_LOOP / max(gain_x * sx, 1e-6), 4)
        mo["kp_y"] = round(K_LOOP / max(gain_y * sy, 1e-6), 4)
        write_profile(prof)
    return ok, detail


# ---- 准星找色（真实图像闭环：frame.bmp → 颜色质心 → FIFO 注入） ----
CH_THREAD = None
_CH_STOP = threading.Event()


def _bgr_color_match(b: int, g: int, r: int, color: str) -> bool:
    if color == "red": return r > 140 and g < 90 and b < 90
    if color == "green": return g > 140 and r < 90 and b < 90
    if color == "blue": return b > 140 and r < 90 and g < 90
    if color == "cyan": return g > 140 and b > 140 and r < 90
    if color == "yellow": return r > 140 and g > 140 and b < 90
    if color == "white": return r > 170 and g > 170 and b > 170
    if color == "black": return r < 70 and g < 70 and b < 70
    return False


def _crosshair_worker() -> None:
    while not _CH_STOP.is_set():
        try:
            prof = read_profile()
            ch = (prof.get("features") or {}).get("crosshair") or {}
            if not ch.get("detection_enabled"):
                time.sleep(0.5)
                continue
            roi_w = max(2, int(ch.get("roi_w") or 80))
            roi_h = max(2, int(ch.get("roi_h") or 80))
            slots = ch.get("slots") or []
            colors = [s.get("color") for s in slots if s.get("enabled")]
            if not colors:
                time.sleep(0.3)
                continue
            bmp = Path("/run/ttbox-frame.bmp")
            if not bmp.exists():
                time.sleep(0.3)
                continue
            data = bmp.read_bytes()
            if len(data) < 54 + 320 * 320 * 3:
                time.sleep(0.3)
                continue
            w = h = 320
            off = 54
            row = w * 3
            x0 = (w - roi_w) // 2
            y0 = (h - roi_h) // 2
            cxs = cys = 0.0
            cnt = 0
            for y in range(y0, y0 + roi_h):
                base = off + y * row + x0 * 3
                for x in range(roi_w):
                    b = data[base]
                    g = data[base + 1]
                    r = data[base + 2]
                    if any(_bgr_color_match(b, g, r, c) for c in colors):
                        cxs += x0 + x
                        cys += y0 + y
                        cnt += 1
                    base += 3
            if cnt > 0:
                cx = cxs / cnt
                cy = cys / cnt
                err_x = cx - w * 0.5
                err_y = cy - h * 0.5
                mo = prof.get("mouse") or {}
                kp_x = float(mo.get("kp_x") or 17)
                kp_y = float(mo.get("kp_y") or 10)
                dx = int(-err_x * kp_x * 0.5)
                dy = int(-err_y * kp_y * 0.5)
                if dx or dy:
                    _fifo_inject(dx, dy)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)


def start_crosshair() -> None:
    global CH_THREAD
    if CH_THREAD and CH_THREAD.is_alive():
        return
    _CH_STOP.clear()
    CH_THREAD = threading.Thread(target=_crosshair_worker, daemon=True)
    CH_THREAD.start()


# ---- 个性曲线训练（真实采集：C 桥 phys 移动 + 目标位置 → 样本 → 个人曲线） ----
MOTION_FILE = TTBOX / "config" / "motion_samples.json"
MOTION_CURVE_FILE = TTBOX / "config" / "motion_curve.json"
_motion = {"status": "idle", "samples": 0, "list": [], "quality": 0,
           "lock": threading.Lock(), "thread": None}


def motion_state() -> dict:
    with _motion["lock"]:
        return {"status": _motion["status"], "samples": _motion["samples"],
                "quality": _motion["quality"]}


def auto_start_state() -> dict:
    """开机自启动（ttbox-infer 是否 enable）。"""
    code, out = _run(["systemctl", "is-enabled", "ttbox-infer.service"], timeout=5)
    return {"enabled": code == 0 and "enabled" in out}


def set_auto_start(enabled: bool) -> tuple[bool, str]:
    action = "enable" if enabled else "disable"
    code, out = _run(["sudo", "systemctl", action, "ttbox-infer.service"], timeout=10)
    return code == 0, (out.strip() or "ok")


# ---- 显示器 EDID 身份（显示与鼠标页：首选模式 / 身份字段 / 随机身份 / 保存并应用） ----
DISPLAY_CONFIG = TTBOX / "config" / "hardware_display.json"
EDID_PROFILE_MAP = {
    "1080p60": "boot-safe-full", "1080p90": "boot-safe-full",
    "1080p120": "boot-safe-full", "1080p144": "boot-safe-full",
    "1080p240": "single-1080p240-compat",
    "1440p60": "rk3588-full", "1440p120": "rk3588-full",
    "1440p144": "rk3588-full", "2160p60": "rk3588-full",
}


def read_display_config() -> dict:
    try:
        return json.loads(DISPLAY_CONFIG.read_text())
    except Exception:  # noqa: BLE001
        return {}


def display_state() -> dict:
    cfg = read_display_config()
    st = {}
    try:
        st = json.loads((TTBOX / "run" / "hdmirx_edid_state.json").read_text())
    except Exception:  # noqa: BLE001
        pass
    log = []
    try:
        log = (TTBOX / "edid" / "apply.log").read_text().splitlines()[-4:]
    except Exception:  # noqa: BLE001
        pass
    return {"config": cfg, "edid": st, "log": log}


def display_apply(cfg: dict) -> tuple[bool, str]:
    DISPLAY_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    try:
        DISPLAY_CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    except Exception as exc:  # noqa: BLE001
        return False, f"写入失败: {exc}"
    code, out = _run(["sudo", "bash", str(TTBOX / "scripts/edid/edid_apply.sh")], timeout=60)
    applied = False
    try:
        applied = bool(json.loads((TTBOX / "run" / "hdmirx_edid_state.json").read_text()).get("applied", False))
    except Exception:  # noqa: BLE001
        pass
    return (code == 0 and applied), (out or "").strip()[-300:] or "EDID 已重新生成并注入"


def display_save(data: dict) -> tuple[bool, str]:
    cfg = read_display_config()
    for k in ("name", "vendor", "product_id", "serial"):
        if data.get(k) is not None:
            v = str(data[k]).strip()
            if v:
                cfg[k] = v
    nm = data.get("native_mode")
    if nm:
        cfg["native_mode"] = nm
        cfg["profile"] = EDID_PROFILE_MAP.get(nm, cfg.get("profile", "boot-safe-full"))
    if "native_only" in data:
        cfg["native_only"] = bool(data["native_only"])
    if data.get("pixel_format"):
        cfg["loopout_pixel_format"] = data["pixel_format"]
    return display_apply(cfg)


def display_randomize() -> tuple[bool, str, dict]:
    """生成随机 EDID 身份（仅生成候选值返回前端表单，不写入/不注入；
    点击「保存并应用」后 display_save 才落盘并注入）。"""
    import random
    cons = "BCDFGHJKLMNPQRSTVWXYZ"
    vow = "AEIOU"
    _bad_vendors = {"SEX", "ASS", "KKK", "FUK", "FUC", "GAY", "KILL", "DIE",
                    "PEE", "POO", "TIT", "BUT", "SUS", "XXX", "SSS", "KAK"}
    vendor = ""
    for _ in range(12):
        v = (cons[random.randrange(len(cons))] + vow[random.randrange(len(vow))]
             + cons[random.randrange(len(cons))])
        if v not in _bad_vendors:
            vendor = v
            break
    if not vendor:
        vendor = "TTX"
    return True, "已生成随机身份，点击「保存并应用」后生效", {
        "name": "TT" + str(random.randrange(100, 1000)),
        "vendor": vendor,
        "product_id": f"0x{random.randrange(1, 0x10000):04X}",
        "serial": f"0x{random.randrange(1, 0x100000000):08X}",
    }


def _motion_worker() -> None:
    while _motion["status"] == "running":
        try:
            ms = read_mouse_stats()
            st = mouse_state_api()
            with _motion["lock"]:
                _motion["list"].append({
                    "t": time.time(),
                    "dx": ms.get("phys_dx", 0), "dy": ms.get("phys_dy", 0),
                    "tx": st.get("target_x"), "ty": st.get("target_y"),
                    "aiming": int(st.get("aiming") or 0),
                    "err_x": st.get("error_x"), "err_y": st.get("error_y"),
                })
                if len(_motion["list"]) > 6000:
                    _motion["list"] = _motion["list"][-6000:]
                _motion["samples"] = len(_motion["list"])
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.05)


def motion_start() -> tuple[bool, str]:
    with _motion["lock"]:
        if _motion["status"] == "running":
            return False, "已在采集中"
        _motion["status"] = "running"
        _motion["list"] = []
        _motion["samples"] = 0
    _motion["thread"] = threading.Thread(target=_motion_worker, daemon=True)
    _motion["thread"].start()
    return True, "采集已开始（请保持移动鼠标瞄准目标）"


def motion_stop() -> tuple[bool, str]:
    with _motion["lock"]:
        if _motion["status"] != "running":
            return False, "未在采集"
        _motion["status"] = "idle"
        samples = list(_motion["list"])
        n = len(samples)
    MOTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    MOTION_FILE.write_text(json.dumps(samples, ensure_ascii=False))
    # 生成个人曲线（真实统计：反应延迟 / 速度分布 / 误差分布）
    quality = 0
    curve = {"enabled": False, "reaction_ms": 0.0, "speed_mean": 0.0,
             "speed_std": 0.0, "err_mean": 0.0, "samples": n}
    if n > 50:
        aiming_idx = [i for i, s in enumerate(samples) if s["aiming"]]
        if aiming_idx:
            first = aiming_idx[0]
            t0 = samples[0]["t"]
            curve["reaction_ms"] = round((samples[first]["t"] - t0) * 1000, 1)
        speeds = [abs(s["dx"]) + abs(s["dy"]) for s in samples]
        mean = sum(speeds) / len(speeds)
        var = sum((v - mean) ** 2 for v in speeds) / len(speeds)
        curve["speed_mean"] = round(mean, 2)
        curve["speed_std"] = round(var ** 0.5, 2)
        errs = [abs(s["err_x"] or 0) for s in samples if s.get("err_x") is not None]
        if errs:
            curve["err_mean"] = round(sum(errs) / len(errs), 2)
        quality = min(100, int(n / 20))
        curve["enabled"] = quality >= 40
    MOTION_CURVE_FILE.write_text(json.dumps(curve, ensure_ascii=False, indent=2))
    with _motion["lock"]:
        _motion["quality"] = quality
    return True, f"采集完成：{n} 样本，质量 {quality}%（≥40% 自动生成并启用个人曲线）"


def rknn_toolkit_available() -> bool:
    code, _ = _run([sys_python(), "-c", "import rknn"], timeout=10)
    return code == 0


def sys_python() -> str:
    return "python3"


def start_convert(onnx_name: str, model_id: str, dtype: str) -> tuple[bool, str, dict]:
    """异步启动 ONNX→RKNN 转换任务（后台线程，状态写入 run/convert/）。"""
    if not CONVERT_SCRIPT.exists():
        return False, f"转换脚本缺失: {CONVERT_SCRIPT}", {}
    if not rknn_toolkit_available():
        return False, "板端未安装 rknn-toolkit2（pip install rknn-toolkit2 后方可转换）", {}
    onnx_path = MODELS_STAGING / onnx_name
    if not onnx_path.exists():
        return False, f"ONNX 不存在: {onnx_name}", {}
    task_id = model_id or (onnx_name.removesuffix(".onnx") + ".convert")
    out_rknn = MODELS_STAGING / f"{model_id}.rknn"
    if model_id.endswith(".rknn"):
        out_rknn = MODELS_STAGING / model_id
    with LOCK:
        # 防重复任务
        st = convert_state(task_id)
        if st.get("state") in ("running",):
            return False, f"转换任务已在运行: {task_id}", st
        CONVERT_DIR.mkdir(parents=True, exist_ok=True)
        convert_save(task_id, {
            "task_id": task_id,
            "state": "running",
            "ok": False,
            "onnx": onnx_name,
            "out": out_rknn.name,
            "dtype": dtype,
            "started_at": int(time.time()),
            "finished_at": None,
            "report": {},
            "error": "",
        })

        def worker():
            cmd = [sys_python(), str(CONVERT_SCRIPT),
                   "--onnx", str(onnx_path),
                   "--out", str(out_rknn),
                   "--dtype", dtype,
                   "--input-size", "1,3,320,320",
                   "--color", "BGR",
                   "--model-id", model_id,
                   "--registry-root", str(MODELS_REG),
                   "--report", str(convert_state_path(task_id).with_suffix(".report.json"))]
            code, out = _run(cmd, timeout=1800)
            report = {}
            rp = convert_state_path(task_id).with_suffix(".report.json")
            try:
                report = json.loads(rp.read_text())
            except Exception:  # noqa: BLE001
                pass
            convert_save(task_id, {
                "task_id": task_id,
                "state": "done" if code == 0 else "failed",
                "ok": code == 0,
                "onnx": onnx_name,
                "out": out_rknn.name,
                "dtype": dtype,
                "started_at": None,
                "finished_at": int(time.time()),
                "report": report,
                "error": "" if code == 0 else out[-3000:],
            })

        threading.Thread(target=worker, daemon=True).start()
        return True, f"转换任务已启动: {task_id}", {"task_id": task_id, "state": "running"}


def convert_tasks() -> list[dict]:
    if not CONVERT_DIR.is_dir():
        return []
    out = []
    for f in sorted(CONVERT_DIR.glob("*.json")):
        if f.name.endswith(".report.json"):
            continue
        try:
            out.append(json.loads(f.read_text()))
        except Exception:  # noqa: BLE001
            continue
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 安静日志
        pass

    def _json(self, obj, code: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, ctype: str):
        """发送静态文件（限 web 目录内，防目录穿越；/run/ttbox-frame.bmp 实时预览帧除外）。"""
        try:
            path = path.resolve()
            base = Path(__file__).resolve().parent
            is_preview_frame = str(path) == "/run/ttbox-frame.bmp"
            if not is_preview_frame and not str(path).startswith(str(base)):
                self._json({"error": "forbidden"}, 403)
                return
            body = path.read_bytes()
        except Exception:  # noqa: BLE001
            self._json({"error": "not found"}, 404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _html(self):
        try:
            body = INDEX_FILE.read_bytes()
        except Exception:  # noqa: BLE001
            body = INDEX_HTML.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # 实时控制台必须禁止缓存：旧版 JS 高频轮询曾压垮 web（CPU 120%）
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self, max_mb: int = 512) -> bytes:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0 or length > max_mb * 1024 * 1024:
            return b""
        return self.rfile.read(length)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            self._html()
        elif u.path == "/api/v1/status":
            self._json({"status":platform_health(),"runtime":_service_show(PLATFORM_CORE_SERVICE),"core":_service_show(PLATFORM_CORE_SERVICE),"inference":_platform_inference_status(),"model":_platform_model_status()})
        elif u.path == "/api/v1/health":
            self._json(platform_health())
        elif u.path == "/api/v1/runtime":
            core=_service_show(PLATFORM_CORE_SERVICE)
            infer=_service_show(PLATFORM_INFER_SERVICE)
            self._json({"status": "HEALTHY" if core["status"]=="HEALTHY" and infer["status"]=="HEALTHY" else "DEGRADED", "core":core, "inference_service":infer, "metrics": {"state":core["metrics"].get("active_state"), "pid":core["metrics"].get("main_pid"), "uptime":_uptime_seconds(core["metrics"].get("active_enter_timestamp")), "health":core["status"], "restart_count":core["metrics"].get("restart_count")}, "last_update":time.time()})
        elif u.path == "/api/v1/inference":
            self._json(_platform_inference_status())
        elif u.path == "/api/v1/model":
            self._json(_platform_model_status())
        elif u.path == "/api/v1/models":
            import hashlib
            root=TTBOX/"models"; versions=root/"versions"; current=root/"current"; items=[]
            for d in sorted(versions.iterdir()) if versions.exists() else []:
                if d.name == "current-base": continue
                f=d/"model.rknn"
                if not f.is_file(): continue
                meta={}
                try: meta=json.loads((d/"validation.json").read_text(encoding="utf-8"))
                except Exception: pass
                items.append({"id":d.name,"version":d.name,"path":str(f),"size":f.stat().st_size,"sha256":hashlib.sha256(f.read_bytes()).hexdigest(),"validation":"VALID" if meta.get("ok") else "UNAVAILABLE","installed":True,"active":current.is_symlink() and current.resolve()==d.resolve()})
            self._json({"models":items,"active":_platform_model_status()})
        elif u.path.startswith("/api/v1/models/"):
            mid=os.path.basename(u.path); v=TTBOX/"models"/"versions"/mid; st=TTBOX/"models"/"staging"/mid; self._json({"id":mid,"installed":(v/"model.rknn").is_file(),"staged":(st/"model.rknn").is_file(),"active":(TTBOX/"models"/"current").is_symlink() and (TTBOX/"models"/"current").resolve()==v})
        elif u.path == "/api/state":
            self._json(state())
        elif u.path == "/api/state.mouse":
            self._json(mouse_state_api())
        elif u.path == "/api/models":
            self._json({"models": models_list(), "model_input": read_model_input()})
        elif u.path == "/api/remote/models":
            self._json(remote_models())
        elif u.path == "/api/remote/device-code":
            self._json({"device_code": device_code()})
        elif u.path == "/api/profile":
            self._json({"profile": read_profile()})
        elif u.path == "/api/aim_profiles":
            try:
                _active_aim = int(ACTIVE_AIM_PROFILE_FILE.read_text().strip())
            except Exception:  # noqa: BLE001
                _active_aim = -1
            self._json({"profiles": read_aim_profiles(), "active": _active_aim})
        elif u.path == "/api/control/calibration":
            self._json(calibration_state())
        elif u.path == "/api/presets":
            self._json(presets_list())
        elif u.path == "/api/motion":
            self._json(motion_state())
        elif u.path == "/api/auto-start":
            self._json(auto_start_state())
        elif u.path == "/api/display":
            self._json(display_state())
        elif u.path == "/api/mouse":
            self._json(mouse_hw_state())
        elif u.path == "/api/system":
            self._json(system_status())
        elif u.path == "/api/hailo/status":
            self._json(hailo_status())
        elif u.path == "/api/convert":
            self._json({"tasks": convert_tasks(), "toolkit_available": rknn_toolkit_available()})
        elif u.path == "/api/hid":
            code, out = _run(["sudo", str(HID_HEALTH), "--root", str(HID_ROOT)], timeout=30)
            self._json({"rc": code, "output": out})
        elif u.path == "/api/hid/config":
            self._json(hid_config_state())
        elif u.path == "/api/hwmon":
            self._json(hwmon())
        elif u.path == "/api/themes":
            self._json(themes_list())
        elif u.path == "/api/serial/devices":
            self._json(serial_devices())
        elif u.path == "/api/diagnostics/usb":
            self._json(usb_diagnostics())
        elif u.path == "/api/update/check":
            self._json(update_check())
        elif u.path.startswith("/static/"):
            # YU 1:1 界面静态资源（style.css / motion_training.css / adapter.js）
            name = os.path.basename(u.path)
            f = Path(__file__).resolve().parent / "static" / name
            ctype = ("application/javascript" if name.endswith(".js")
                     else "text/css" if name.endswith(".css")
                     else "application/octet-stream")
            self._file(f, ctype)
        elif u.path.startswith("/themes/"):
            # 主题商店：本地主题 CSS（themes/*.css）
            name = os.path.basename(u.path)
            f = THEMES_DIR / name
            ctype = "text/css" if name.endswith(".css") else "application/octet-stream"
            self._file(f, ctype)
        elif u.path in ("/frame.bmp", "/frame.png", "/frame.ppm"):
            # 实时画面帧由 test_worker_hw 写入 tmpfs（写盘快，eMMC 会拖慢预览）
            f = Path("/run/ttbox-frame.bmp") if u.path.endswith(".bmp") else (
                Path(__file__).resolve().parent / Path(u.path).name)
            ctype = "image/bmp" if u.path.endswith(".bmp") else (
                "image/png" if u.path.endswith(".png") else "image/x-portable-pixmap")
            self._file(f, ctype)
        elif u.path == "/frame.json":
            # 帧同步画面数据（test_worker_hw 每帧写入 /run/ttbox-frame.json）
            try:
                body = Path("/run/ttbox-frame.json").read_bytes()
            except Exception:  # noqa: BLE001
                self._json({"frame_dets": 0}, 200)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        else:
            self._json({"error": "not found"}, 404)

    def _platform_model_request(self, action: str, model_id: str):
        """Platform V1 模型控制路由；暂不改写旧模型接口。"""
        from pathlib import Path as _Path
        safe=os.path.basename(model_id)
        if not safe or safe != model_id or safe in ('.','..'):
            self._json({"ok":False,"error":"invalid model id"},400); return
        root=TTBOX/"models"; versions=root/"versions"; staging=root/"staging"; current=root/"current"
        versions.mkdir(parents=True,exist_ok=True); staging.mkdir(parents=True,exist_ok=True)
        v=versions/safe; st=staging/safe
        if action=="validate":
            f=st/"model.rknn"
            if not f.is_file() or f.stat().st_size==0: self._json({"ok":False,"error":"staged model unavailable"},400); return
            import hashlib
            h=hashlib.sha256(f.read_bytes()).hexdigest(); meta={"ok":True,"model_id":safe,"size":f.stat().st_size,"sha256":h}
            (st/"validation.json").write_text(json.dumps(meta),encoding="utf-8"); self._json({"ok":True,"state":"VALID","metadata":meta}); return
        if action=="install":
            if not (st/"validation.json").is_file(): self._json({"ok":False,"error":"validate first"},400); return
            import shutil
            tmp=versions/('.'+safe+'.tmp'); shutil.rmtree(tmp,ignore_errors=True); shutil.copytree(st,tmp)
            backup=versions/('.'+safe+'.backup')
            if v.exists(): shutil.rmtree(backup,ignore_errors=True); os.replace(v,backup)
            os.replace(tmp,v); shutil.rmtree(backup,ignore_errors=True); self._json({"ok":True,"state":"INSTALLED","id":safe}); return
        if action=="activate":
            if not (v/"model.rknn").is_file(): self._json({"ok":False,"error":"installed model unavailable"},400); return
            tmp=root/('.current.tmp');
            if tmp.exists() or tmp.is_symlink(): tmp.unlink()
            old=current.resolve().name if current.is_symlink() and current.exists() else None
            # 兼容 Phase 3/6 的旧目录式 current：先保留为受控版本，再切换为 symlink。
            if current.exists() and not current.is_symlink():
                legacy=versions/'current-base'
                if not legacy.exists(): os.replace(current,legacy)
                else: shutil.rmtree(current)
                old='current-base'
            tmp.symlink_to(v,target_is_directory=True)
            try: os.replace(tmp,current)
            except PermissionError:
                if current.is_symlink(): current.unlink()
                os.replace(tmp,current)
            if old and old!=safe: (root/'previous').write_text(old,encoding='utf-8')
            code,out=_run(["sudo","-n","systemctl","restart","ttbox-infer.service"],timeout=30); invalidate_svc("ttbox-infer")
            self._json({"ok":code==0,"state":"ACTIVE" if code==0 else "FAILED","detail":out,"id":safe},200 if code==0 else 500); return
        if action=="deactivate":
            if current.is_symlink(): current.unlink()
            elif current.exists(): shutil.rmtree(current)
            self._json({"ok":True,"state":"DEACTIVATED","id":safe}); return
        if action=="rollback":
            prev=root/'previous'
            if not prev.is_file(): self._json({"ok":False,"error":"no previous model"},400); return
            pid=os.path.basename(prev.read_text(encoding='utf-8').strip()); pv=versions/pid
            if not pid or pid!=prev.read_text(encoding='utf-8').strip() or not (pv/"model.rknn").is_file(): self._json({"ok":False,"error":"invalid previous model"},400); return
            tmp=root/('.current.tmp');
            if tmp.exists() or tmp.is_symlink(): tmp.unlink()
            cur=current.resolve().name if current.is_symlink() and current.exists() else ''; tmp.symlink_to(pv,target_is_directory=True)
            try: os.replace(tmp,current)
            except PermissionError:
                if current.is_symlink(): current.unlink()
                os.replace(tmp,current)
            if cur: prev.write_text(cur,encoding='utf-8')
            code,out=_run(["sudo","-n","systemctl","restart","ttbox-infer.service"],timeout=30); invalidate_svc("ttbox-infer")
            self._json({"ok":code==0,"state":"ACTIVE" if code==0 else "FAILED","detail":out,"id":pid},200 if code==0 else 500); return
        self._json({"ok":False,"error":"unsupported action"},400)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path == "/api/v1/models/upload":
            import re as _re
            ctype=self.headers.get("Content-Type",""); _,pd=cgi.parse_header(ctype); boundary=pd.get("boundary","")
            if not boundary: self._json({"ok":False,"error":"multipart required"},400); return
            length=int(self.headers.get("Content-Length",0) or 0); body=self.rfile.read(length); parts=body.split(("--"+boundary).encode()); fname=None; data=None
            for part in parts:
                if b"Content-Disposition" not in part: continue
                m=_re.search(rb'filename="([^"]+)"',part)
                if m:
                    fname=os.path.basename(m.group(1).decode(errors="ignore")); _,sep,data=part.partition(b"\r\n\r\n"); data=data[:-2] if sep and data.endswith(b"\r\n") else data; break
            if not fname or not data or fname != os.path.basename(fname) or not fname.endswith('.rknn'): self._json({"ok":False,"error":"invalid model file"},400); return
            mid=os.path.splitext(fname)[0]; st=TTBOX/"models"/"staging"/mid; st.mkdir(parents=True,exist_ok=True); (st/'model.rknn').write_bytes(data); self._json({"ok":True,"state":"UPLOADING","id":mid,"size":len(data)}); return
        if u.path.startswith("/api/v1/models/"):
            seg=u.path.split('/'); mid=seg[4] if len(seg)>4 else ''; action=seg[5] if len(seg)>5 else ''
            self._platform_model_request(action,mid); return
        if u.path == "/api/models/import":
            # 通用 multipart：收集表单字段 + 文件（对齐 YU 导入对话框）
            import re as _re
            ctype = self.headers.get("Content-Type", "")
            _, pdict = cgi.parse_header(ctype)
            boundary = pdict.get("boundary", "")
            if not boundary:
                self._json({"error": "multipart required"}, 400)
                return
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > 1024 * 1024 * 1024:
                self._json({"error": "bad Content-Length"}, 400)
                return
            body = self.rfile.read(length)
            fields: dict[str, str] = {}
            files: dict[str, tuple[str, bytes]] = {}
            for part in body.split(("--" + boundary).encode()):
                if b"Content-Disposition" not in part:
                    continue
                m = _re.search(rb'name="([^"]+)"', part)
                if not m:
                    continue
                name = m.group(1).decode(errors="ignore")
                _, sep, content = part.partition(b"\r\n\r\n")
                if not sep:
                    continue
                fm = _re.search(rb'filename="([^"]*)"', part)
                if fm:
                    fname = fm.group(1).decode(errors="ignore")
                    data = content
                    if data.endswith(b"\r\n"):
                        data = data[:-2]
                    files[name] = (fname, data)
                else:
                    val = content.decode(errors="ignore")
                    if val.endswith("\r\n"):
                        val = val[:-2]
                    fields[name] = val
            ok, detail, extra = model_import(fields, files)
            self._json({"ok": ok, "detail": detail, **extra}, 200 if ok else 400)
        elif u.path == "/api/models/select":
            try:
                req = json.loads(self._read_body() or b"{}")
                model_id = str(req.get("model_id") or "")
            except Exception:  # noqa: BLE001
                self._json({"error": "bad json"}, 400)
                return
            ok, detail = model_select(model_id)
            self._json({"ok": ok, "detail": detail, "model_id": model_id}, 200 if ok else 400)
        elif u.path == "/api/remote/connect":
            try:
                req = json.loads(self._read_body() or b"{}")
                host = str(req.get("host") or "")
            except Exception:  # noqa: BLE001
                self._json({"error": "bad json"}, 400)
                return
            ok, detail, payload = remote_connect(host)
            self._json({"ok": ok, "detail": detail, **payload}, 200 if ok else 400)
        elif u.path == "/api/models/upload":
            import re as _re
            ctype = self.headers.get("Content-Type", "")
            _, pdict = cgi.parse_header(ctype)
            boundary = pdict.get("boundary", "")
            if not boundary:
                self._json({"error": "multipart required"}, 400)
                return
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > 1024 * 1024 * 1024:
                self._json({"error": "bad Content-Length"}, 400)
                return
            body = self.rfile.read(length)
            parts = body.split(("--" + boundary).encode())
            fname, data = None, None
            for part in parts:
                if b"Content-Disposition" not in part:
                    continue
                m = _re.search(rb'filename="([^"]+)"', part)
                if not m:
                    continue
                fname = m.group(1).decode(errors="ignore")
                _, sep, content = part.partition(b"\r\n\r\n")
                if not sep:
                    continue
                data = content
                if data.endswith(b"\r\n"):
                    data = data[:-2]
                break
            if not fname or data is None:
                self._json({"error": "no file field"}, 400)
                return
            if not fname.lower().endswith((".rknn", ".onnx")):
                self._json({"error": "仅支持 .rknn / .onnx 模型文件"}, 400)
                return
            MODELS_STAGING.mkdir(parents=True, exist_ok=True)
            dst = MODELS_STAGING / os.path.basename(fname)
            dst.write_bytes(data)
            self._json({"ok": True, "name": dst.name, "size": dst.stat().st_size,
                        "kind": "onnx" if fname.lower().endswith(".onnx") else "rknn"})
        elif u.path == "/api/models/convert":
            try:
                req = json.loads(self._read_body() or b"{}")
                onnx_name = os.path.basename(str(req.get("onnx", "")))
                model_id = str(req.get("model_id", ""))
                dtype = str(req.get("dtype", "int8"))
            except Exception:  # noqa: BLE001
                self._json({"error": "bad json"}, 400)
                return
            if dtype not in ("fp16", "int8"):
                self._json({"error": "dtype must be fp16|int8"}, 400)
                return
            if not model_id:
                model_id = onnx_name.removesuffix(".onnx") + ".rknn"
            ok, msg, st = start_convert(onnx_name, model_id, dtype)
            self._json({"ok": ok, "detail": msg, "task": st}, 200 if ok else 500)
        elif u.path == "/api/models/activate":
            try:
                req = json.loads(self._read_body() or b"{}")
                name = os.path.basename(str(req.get("name", "")))
            except Exception:  # noqa: BLE001
                self._json({"error": "bad json"}, 400)
                return
            if not name.endswith(".rknn"):
                self._json({"error": "bad name"}, 400)
                return
            src = MODELS_STAGING / name
            dst = MODELS_INSTALLED / name
            if src.exists():
                dst.write_bytes(src.read_bytes())
                src.unlink()  # 激活后移除 staging 副本
            if not dst.exists():
                self._json({"error": f"模型不存在: {name}"}, 404)
                return
            ACTIVE_MODEL_FILE.write_text(name)
            # 重启推理以加载新模型
            _run(["sudo", "systemctl", "restart", "ttbox-infer"], timeout=30)
            self._json({"ok": True, "active": name})
        elif u.path == "/api/models/remove":
            try:
                req = json.loads(self._read_body() or b"{}")
                name = os.path.basename(str(req.get("name", "")))
            except Exception:  # noqa: BLE001
                self._json({"error": "bad json"}, 400)
                return
            if ACTIVE_MODEL_FILE.exists() and ACTIVE_MODEL_FILE.read_text().strip() == name:
                self._json({"error": "禁止删除 active 模型"}, 400)
                return
            removed = False
            for d in (MODELS_INSTALLED, MODELS_STAGING):
                f = d / name
                if f.exists():
                    f.unlink()
                    removed = True
            self._json({"ok": removed, "name": name})
        elif u.path == "/api/profile":
            try:
                req = json.loads(self._read_body() or b"{}")
            except Exception:  # noqa: BLE001
                self._json({"error": "bad json"}, 400)
                return
            ok, detail = write_profile(req)
            self._json({"ok": ok, "detail": detail}, 200 if ok else 500)
        elif u.path == "/api/aim_profiles":
            try:
                req = json.loads(self._read_body() or b"{}")
            except Exception:  # noqa: BLE001
                self._json({"error": "bad json"}, 400)
                return
            profiles = req.get("profiles")
            ok, detail = write_aim_profiles(profiles)
            if ok and "activate" in req:
                try:
                    idx = int(req["activate"])
                except (TypeError, ValueError):
                    idx = -1
                if 0 <= idx < len(profiles or []):
                    ok2, detail2 = apply_aim_profile(profiles, idx)
                    self._json({"ok": ok2, "detail": detail2, "active": idx}, 200 if ok2 else 500)
                    return
            self._json({"ok": ok, "detail": detail}, 200 if ok else 500)
        elif u.path == "/api/control/calibration/start":
            ok, detail = start_auto_calibration()
            self._json({"ok": ok, "detail": detail}, 200 if ok else 500)
        elif u.path == "/api/control/calibration/cancel":
            cancel_auto_calibration()
            self._json({"ok": True, "detail": "已取消"})
        elif u.path == "/api/presets":
            try:
                req = json.loads(self._read_body() or b"{}")
                action = str(req.get("action", ""))
                name = str(req.get("name", ""))
            except Exception:  # noqa: BLE001
                self._json({"error": "bad json"}, 400)
                return
            if action == "save":
                ok, detail = preset_save(name)
            elif action == "apply":
                ok, detail = preset_apply(name)
            elif action == "delete":
                ok, detail = preset_delete(name)
            else:
                self._json({"error": "action must be save|apply|delete"}, 400)
                return
            self._json({"ok": ok, "detail": detail}, 200 if ok else 500)
        elif u.path == "/api/presets/cleanup":
            ok, detail, extra = presets_cleanup()
            self._json({"ok": ok, "detail": detail, **extra}, 200 if ok else 500)
        elif u.path == "/api/motion":
            try:
                req = json.loads(self._read_body() or b"{}")
                action = str(req.get("action", ""))
            except Exception:  # noqa: BLE001
                self._json({"error": "bad json"}, 400)
                return
            if action == "start":
                ok, detail = motion_start()
            elif action == "stop":
                ok, detail = motion_stop()
            else:
                self._json({"error": "action must be start|stop"}, 400)
                return
            self._json({"ok": ok, "detail": detail}, 200 if ok else 500)
        elif u.path == "/api/auto-start":
            try:
                req = json.loads(self._read_body() or b"{}")
                enabled = bool(req.get("enabled"))
            except Exception:  # noqa: BLE001
                self._json({"error": "bad json"}, 400)
                return
            ok, detail = set_auto_start(enabled)
            self._json({"ok": ok, "detail": detail, "enabled": enabled}, 200 if ok else 500)
        elif u.path == "/api/display":
            try:
                req = json.loads(self._read_body() or b"{}")
                action = str(req.get("action", ""))
            except Exception:  # noqa: BLE001
                self._json({"error": "bad json"}, 400)
                return
            if action == "save":
                ok, detail = display_save(req.get("config") or {})
            elif action == "randomize":
                ok, detail, ident = display_randomize()
                self._json({"ok": ok, "detail": detail, "identity": ident}, 200 if ok else 500)
                return
            elif action == "apply":
                ok, detail = display_apply(read_display_config())
            else:
                self._json({"error": "action must be save|randomize|apply"}, 400)
                return
            self._json({"ok": ok, "detail": detail, "config": read_display_config()}, 200 if ok else 500)
        elif u.path == "/api/mouse":
            try:
                req = json.loads(self._read_body() or b"{}")
                action = str(req.get("action", ""))
            except Exception:  # noqa: BLE001
                self._json({"error": "bad json"}, 400)
                return
            if action == "save":
                ok, detail = mouse_save(req)
            elif action == "randomize":
                ok, detail, ident = mouse_randomize()
                self._json({"ok": ok, "detail": detail, "identity": ident}, 200 if ok else 500)
                return
            else:
                self._json({"error": "action must be save|randomize"}, 400)
                return
            self._json({"ok": ok, "detail": detail, "config": read_mouse_config()}, 200 if ok else 500)
        elif u.path == "/api/system":
            try:
                req = json.loads(self._read_body() or b"{}")
                action = str(req.get("action", ""))
            except Exception:  # noqa: BLE001
                self._json({"error": "bad json"}, 400)
                return
            if action == "reboot":
                ok, detail = system_power("reboot")
            elif action == "poweroff":
                ok, detail = system_power("poweroff")
            elif action == "hostname":
                ok, detail = set_hostname(req.get("hostname"))
            else:
                self._json({"error": "action must be reboot|poweroff|hostname"}, 400)
                return
            self._json({"ok": ok, "detail": detail}, 200 if ok else 400)
        elif u.path == "/api/network/wifi":
            try:
                req = json.loads(self._read_body() or b"{}")
                action = str(req.get("action", ""))
            except Exception:  # noqa: BLE001
                self._json({"error": "bad json"}, 400)
                return
            ok, detail, extra = wifi_action(action, req)
            self._json({"ok": ok, "detail": detail, **extra}, 200 if ok else 400)
        elif u.path == "/api/network/lan":
            try:
                req = json.loads(self._read_body() or b"{}")
            except Exception:  # noqa: BLE001
                self._json({"error": "bad json"}, 400)
                return
            ok, detail, extra = lan_action(req)
            self._json({"ok": ok, "detail": detail, **extra}, 200 if ok else 400)
        elif u.path == "/api/kmbox/save":
            try:
                req = json.loads(self._read_body() or b"{}")
            except Exception:  # noqa: BLE001
                self._json({"error": "bad json"}, 400)
                return
            ok, detail = kmbox_save(req)
            self._json({"ok": ok, "detail": detail}, 200 if ok else 400)
        elif u.path == "/api/aim/draw_circle":
            try:
                req = json.loads(self._read_body() or b"{}")
            except Exception:  # noqa: BLE001
                self._json({"error": "bad json"}, 400)
                return
            ok, detail = draw_circle_test(req)
            self._json({"ok": ok, "detail": detail}, 200 if ok else 500)
        elif u.path.startswith("/api/v1/runtime/"):
            action=u.path.rsplit("/",1)[-1]
            if action not in ("start","stop","restart"):
                self._json({"error":"action must be start|stop|restart"},400); return
            code,out=_run(["sudo","-n","systemctl",action,"ttbox-supervisor.service"],timeout=30)
            if code==0:
                code2,out2=_run(["sudo","-n","systemctl",action,"ttbox-infer.service"],timeout=30)
                out += out2
            invalidate_svc("ttbox-supervisor"); invalidate_svc("ttbox-infer")
            self._json({"ok":code==0,"action":action,"detail":out,"status":platform_health()},200 if code==0 else 500)
        elif u.path == "/api/inference":
            try:
                req = json.loads(self._read_body() or b"{}")
                action = str(req.get("action", ""))
            except Exception:  # noqa: BLE001
                self._json({"error": "bad json"}, 400)
                return
            if action in ("start", "stop"):
                code, out = _run(["sudo", "systemctl", action, "ttbox-infer.service"], timeout=30)
                invalidate_svc("ttbox-infer")
                self._json({"ok": code == 0, "action": action, "detail": out})
            else:
                self._json({"error": "action must be start|stop"}, 400)
        elif u.path == "/api/edid":
            try:
                req = json.loads(self._read_body() or b"{}")
                action = str(req.get("action", "reload"))
            except Exception:  # noqa: BLE001
                action = "reload"
            if action == "reload" and EDID_SCRIPT.exists():
                code, out = _run(["sudo", "bash", str(EDID_SCRIPT)], timeout=60)
                self._json({"ok": code == 0, "detail": out[-2000:]})
            else:
                self._json({"error": "edid script missing"}, 404)
        elif u.path == "/api/hid/set":
            try:
                req = json.loads(self._read_body() or b"{}")
                device = str(req.get("device", ""))
                enabled = bool(req.get("enabled"))
            except Exception:  # noqa: BLE001
                self._json({"error": "bad json"}, 400)
                return
            if device not in ("mouse", "keyboard"):
                self._json({"error": "device must be mouse|keyboard"}, 400)
                return
            ok, detail = hid_set(device, enabled)
            self._json({"ok": ok, "detail": detail}, 200 if ok else 500)
        else:
            self._json({"error": "not found"}, 404)

    def do_PUT(self):
        """手动保存标定值（对齐 YU PUT /api/control/calibration）。"""
        u = urlparse(self.path)
        if u.path == "/api/control/calibration":
            try:
                req = json.loads(self._read_body() or b"{}")
            except Exception:  # noqa: BLE001
                self._json({"error": "bad json"}, 400)
                return
            ok, detail = save_calibration_manual(req)
            self._json({"ok": ok, "detail": detail}, 200 if ok else 500)
        else:
            self._json({"error": "not found"}, 404)

    def do_DELETE(self):
        """删除模型/清除标定。"""
        u = urlparse(self.path)
        if u.path.startswith("/api/v1/models/"):
            mid=os.path.basename(u.path); v=TTBOX/"models"/"versions"/mid; current=TTBOX/"models"/"current"
            if current.is_symlink() and current.resolve()==v: self._json({"ok":False,"error":"active model cannot be removed"},400); return
            if not mid or mid in (".","..") or mid != os.path.basename(mid): self._json({"ok":False,"error":"invalid model id"},400); return
            import shutil
            if v.is_dir(): shutil.rmtree(v); self._json({"ok":True,"state":"REMOVED","id":mid}); return
            self._json({"ok":False,"error":"model not installed"},404); return
        if u.path == "/api/models/delete":
            try:
                req = json.loads(self._read_body() or b"{}")
                model_id = str(req.get("model_id") or "")
            except Exception:  # noqa: BLE001
                self._json({"error": "bad json"}, 400)
                return
            ok, detail = model_delete(model_id)
            self._json({"ok": ok, "detail": detail}, 200 if ok else 400)
        elif u.path == "/api/remote/delete":
            # 设备端先行：远端模型删除待接入（Windows 端推理服务接入后可用）
            self._json({"ok": True, "detail": "远端模型删除待接入（Windows 端推理服务接入后可用）"})
        elif u.path == "/api/control/calibration":
            clear_calibration()
            self._json({"ok": True, "detail": "标定已清除"})
        else:
            self._json({"error": "not found"}, 404)


INDEX_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TTBox 控制台</title>
<style>
body{font-family:system-ui,sans-serif;background:#101418;color:#d8dee6;margin:0;padding:16px}
h1{font-size:20px;margin:4px 0 12px;color:#fff}
h2{font-size:15px;margin:18px 0 8px;color:#9fd7ff}
.card{background:#1a2028;border:1px solid #2a3340;border-radius:8px;padding:12px 16px;margin-bottom:10px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px}
.metric{background:#141a22;border-radius:6px;padding:8px 10px}
.metric b{display:block;font-size:19px;color:#7fe0a0}
.metric span{font-size:11px;color:#8b95a3}
.ok{color:#7fe0a0}.off{color:#e0a87f}.err{color:#ff7a7a}
.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:6px 0}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #232c36}
button{background:#2563eb;color:#fff;border:none;border-radius:6px;padding:6px 14px;margin:2px;cursor:pointer}
button:disabled{opacity:.4}
button.danger{background:#dc2626}
#up{display:none}
pre{background:#10141a;padding:10px;border-radius:6px;font-size:12px;overflow:auto;max-height:260px;white-space:pre-wrap}
input,select{background:#141a22;color:#d8dee6;border:1px solid #2a3340;border-radius:6px;padding:6px 8px;margin:2px;font-size:13px}
label{font-size:12px;color:#8b95a3;margin-right:4px}
</style>
</head>
<body>
<h1>TTBox 控制台 <small id="kernel" style="color:#8b95a3"></small></h1>

<div class="card">
  <h2>实时监控</h2>
  <div class="grid" id="mets"></div>
  <div class="row" id="stage" style="font-size:12px;color:#8b95a3"></div>
</div>

<div class="card grid" id="hw"></div>

<div class="card">
  <h2>服务</h2>
  <div id="svc"></div>
</div>

<div class="card">
  <h2>推理控制</h2>
  <button onclick="infer('start')">启动推理</button>
  <button class="danger" onclick="infer('stop')">停止推理</button>
  <span id="inf"></span>
</div>

<div class="card">
  <h2>模型</h2>
  <button onclick="document.getElementById('up').click()">上传模型</button>
  <input type="file" id="up" accept=".rknn,.onnx" onchange="upload()">
  <table><thead><tr><th>模型</th><th>状态</th><th>大小</th><th>操作</th></tr></thead><tbody id="models"></tbody></table>
  <h2>ONNX → RKNN 转换</h2>
  <div class="row">
    <select id="convSel"></select>
    <select id="convDtype"><option value="int8">INT8</option><option value="fp16">FP16</option></select>
    <button onclick="convert()">开始转换</button>
    <span id="toolkit"></span>
  </div>
  <pre id="convTasks"></pre>
</div>

<div class="card">
  <h2>调参（RuntimeProfile 热更新）</h2>
  <div class="row">
    <label>Confidence</label><input id="conf" type="number" min="0" max="1" step="0.01" style="width:70px">
    <label>IoU</label><input id="iou" type="number" min="0" max="1" step="0.01" style="width:70px">
    <label>max_detections</label><input id="maxdet" type="number" min="0" step="1" style="width:80px">
  </div>
  <div class="row">
    <label>FOV</label><input id="fovEn" type="checkbox" style="width:auto">
    <label>Center X</label><input id="fovX" type="number" min="0" max="1" step="0.01" style="width:70px">
    <label>Center Y</label><input id="fovY" type="number" min="0" max="1" step="0.01" style="width:70px">
    <label>Radius</label><input id="fovR" type="number" min="0" max="1" step="0.01" style="width:70px">
  </div>
  <div class="row">
    <label>ROI X</label><input id="roiX" type="number" min="0" style="width:70px">
    <label>Y</label><input id="roiY" type="number" min="0" style="width:70px">
    <label>W</label><input id="roiW" type="number" min="0" style="width:70px">
    <label>H</label><input id="roiH" type="number" min="0" style="width:70px">
    <button onclick="saveProfile()">保存并热更新</button>
    <span id="profMsg"></span>
  </div>
  <div style="font-size:11px;color:#8b95a3">class_filter 用逗号分隔（如 0,1）；0 表示使用模型默认</div>
  <div class="row"><label>class_filter</label><input id="clsFilter" style="width:140px"></div>
</div>

<div class="card">
  <h2>HID 透传</h2>
  <div class="row">
    <button id="msBtn" onclick="toggleHid('mouse')">鼠标透传</button>
    <span id="msSt" class="ok">已开启</span>
    <button id="kbBtn" onclick="toggleHid('keyboard')">键盘透传</button>
    <span id="kbSt" class="ok">已开启</span>
    <span id="hidMsg"></span>
  </div>
</div>
<div class="card"><h2>HID 健康</h2><button onclick="hid()">刷新</button><pre id="hid"></pre></div>
<div class="card"><h2>EDID</h2><button onclick="edid()">重新注入 EDID</button><span id="edid"></span></div>
<script>
const $=s=>document.querySelector(s);
async function j(u,o){const r=await fetch(u,o);return r.json()}
function num(v,dec=1){return v===undefined||v===null?'-':(typeof v==='number'?v.toFixed(dec):v)}
function mets(){j('/api/state').then(s=>{
  $('#kernel').textContent=s.kernel;
  const m=s.metrics||{},hw=s.hwmon||{};
  const cf=num(m.capture_fps),pf=num(m.pipeline_fps);
  $('#mets').innerHTML=[
    ['Capture FPS',cf],['Pipeline FPS',pf],['errors',m.errors||0],['skipped',m.skipped||0],
    ['NPU0/1/2',num(m.npu0)+'/'+num(m.npu1)+'/'+num(m.npu2)],
    ['run',num(m.run_us)+'us'],['rga',num(m.rga_us)+'us'],['e2e',num(m.e2e_us)+'us'],
    ['decode',num(m.decode_us)+'us'],['detections',m.detections||0],
    ['dropped_latest',m.dropped_latest||0],['poll_timeout',m.poll_timeouts||0]
  ].map(x=>'<div class="metric"><b>'+x[1]+'</b><span>'+x[0]+'</span></div>').join('');
  $('#stage').textContent=(m.t!==undefined?'t='+m.t.toFixed(1)+'s ':'')+(s.inference&&s.inference.fps?'| [REPORT] FPS='+s.inference.fps:'');
  $('#hw').innerHTML=[
    ['SOC 温度',num(hw.soc_temp_c,1)+' °C'],['CPU4',(parseInt(hw.cpu4_freq_hz||0)/1e6).toFixed(2)+' GHz '+hw.cpu4_governor],
    ['GPU',(parseInt(hw.gpu_freq_hz||0)/1e9).toFixed(2)+' GHz'],['NPU',(parseInt(hw.npu_freq_hz||0)/1e9).toFixed(2)+' GHz'],
    ['DDR',(parseInt(hw.ddr_freq_hz||0)/1e9).toFixed(2)+' GHz'],['Load',hw.loadavg]
  ].map(m=>'<div class="metric"><b>'+m[1]+'</b><span>'+m[0]+'</span></div>').join('');
  let h='';
  for(const[k,v]of Object.entries(s.services))h+='<span class="'+(v?'ok':'off')+'">'+k+(v?' ✓':' ✗')+'</span> ';
  $('#svc').innerHTML=h;
  const i=s.inference;
  $('#inf').textContent=(s.services.inference?'运行中':'已停止')+(i.fps?' | FPS='+i.fps+' | error='+i.errors+' | poll_timeout='+i.poll_timeouts:'');
  loadProfileInto(s.profile);
})}
function loadProfileInto(p){
  if(!p||!p.inference)return;
  $('#conf').value=p.inference.confidence||0;
  $('#iou').value=p.inference.iou||0;
  $('#maxdet').value=p.inference.max_detections||0;
  $('#clsFilter').value=(p.inference.class_filter||[]).join(',');
  const f=p.fov||{};
  $('#fovEn').checked=!!f.enabled;
  $('#fovX').value=f.center_x||0.5; $('#fovY').value=f.center_y||0.5; $('#fovR').value=f.radius||0.5;
  const c=p.capture||{};
  $('#roiX').value=c.offset_x||0; $('#roiY').value=c.offset_y||0;
  $('#roiW').value=c.width||0; $('#roiH').value=c.height||0;
}
function saveProfile(){
  const p={
    model_id:'',
    capture:{width:+$('#roiW').value||0,height:+$('#roiH').value||0,offset_x:+$('#roiX').value||0,offset_y:+$('#roiY').value||0},
    inference:{confidence:+$('#conf').value||0,iou:+$('#iou').value||0,
      class_filter:$('#clsFilter').value.split(',').map(x=>parseInt(x)).filter(x=>!isNaN(x)),max_detections:+$('#maxdet').value||0},
    fov:{enabled:$('#fovEn').checked,shape:0,radius:+$('#fovR').value||0.5,center_x:+$('#fovX').value||0.5,center_y:+$('#fovY').value||0.5}
  };
  j('/api/profile',{method:'POST',body:JSON.stringify(p)}).then(r=>{$('#profMsg').textContent=r.ok?('✓ '+r.detail):('✗ '+r.error)});
}
function models(){j('/api/models').then(d=>{
  $('#models').innerHTML=d.models.map(m=>
    '<tr><td>'+m.name+(m.active?' <b class="ok">[active]</b>':'')+'</td><td>'+m.status+'</td><td>'+(m.size/1048576).toFixed(1)+' MB</td><td>'+
    (!m.active?'<button data-act="'+m.name+'">激活</button> <button class="danger" data-del="'+m.name+'">删除</button>':'')+'</td></tr>').join('');
  const onnx=d.models.filter(m=>m.name.endsWith('.onnx'));
  $('#convSel').innerHTML=onnx.map(m=>'<option value="'+m.name+'">'+m.name+'</option>').join('')||'<option value="">无 ONNX</option>';
})}
function convList(){j('/api/convert').then(d=>{
  $('#toolkit').textContent=d.toolkit_available?'toolkit: 可用':'toolkit: 未安装(需 rknn-toolkit2)';
  $('#convTasks').textContent=(d.tasks.length?JSON.stringify(d.tasks,null,1):'暂无转换任务');
})}
document.addEventListener('click',function(e){
  var t=e.target.closest('[data-act],[data-del]');
  if(!t)return;
  if(t.getAttribute('data-act'))act(t.getAttribute('data-act'));
  if(t.getAttribute('data-del'))del(t.getAttribute('data-del'));
});
async function infer(a){const r=await j('/api/inference',{method:'POST',body:JSON.stringify({action:a})});if(!r.ok)alert(r.detail);mets()}
async function act(n){const r=await j('/api/models/activate',{method:'POST',body:JSON.stringify({name:n})});if(!r.ok)alert(r.error);models()}
async function del(n){const r=await j('/api/models/remove',{method:'POST',body:JSON.stringify({name:n})});if(!r.ok)alert(r.error);models()}
async function upload(){const f=document.getElementById('up').files[0];if(!f)return;
  const fd=new FormData();fd.append('file',f);const r=await fetch('/api/models/upload',{method:'POST',body:fd}).then(x=>x.json());
  if(r.ok){alert('已上传到 staging: '+r.name);models()}else{alert(r.error)}}
async function convert(){
  const onnx=$('#convSel').value;if(!onnx){alert('请先上传 .onnx');return}
  const r=await j('/api/models/convert',{method:'POST',body:JSON.stringify({onnx,model_id:onnx.replace(/\\.onnx$/,'')+'.rknn',dtype:$('#convDtype').value})});
  alert(r.ok?('✓ '+r.detail):('✗ '+r.detail));convList();models();
}
async function hid(){const r=await j('/api/hid');$('#hid').textContent=r.output||'无输出'}
function hidCfg(){j('/api/hid/config').then(s=>{
  $('#msBtn').textContent=s.mouse_enabled?'关闭鼠标透传':'开启鼠标透传';
  $('#msSt').textContent=s.mouse_enabled?'已开启':'已关闭';
  $('#msSt').className=s.mouse_enabled?'ok':'off';
  $('#kbBtn').textContent=s.keyboard_enabled?'关闭键盘透传':'开启键盘透传';
  $('#kbSt').textContent=s.keyboard_enabled?'已开启':'已关闭';
  $('#kbSt').className=s.keyboard_enabled?'ok':'off';
  if(!s.forward)$('#hidMsg').textContent='forwarder 服务未运行';
})}
async function toggleHid(dev){
  const st=document.getElementById(dev==='mouse'?'msSt':'kbSt');
  const want=st.textContent!=='已开启';
  const btn=document.getElementById(dev==='mouse'?'msBtn':'kbBtn');
  btn.disabled=true;$('#hidMsg').textContent='切换中…';
  const r=await j('/api/hid/set',{method:'POST',body:JSON.stringify({device:dev,enabled:want})});
  btn.disabled=false;
  $('#hidMsg').textContent=r.ok?(r.detail+'，forwarder 已重启'):'失败: '+r.detail;
  if(!r.ok)alert(r.detail);
  hidCfg();
}
async function edid(){const r=await j('/api/edid',{method:'POST',body:JSON.stringify({action:'reload'})});$('#edid').textContent=r.ok?'完成':'失败: '+r.detail}
mets();models();hidCfg();convList();setInterval(()=>{mets();convList()},3000);
</script>
</body>
</html>"""


def main() -> int:
    port = int(os.environ.get("TTBOX_WEB_PORT", "8080"))
    MODELS_INSTALLED.mkdir(parents=True, exist_ok=True)
    MODELS_STAGING.mkdir(parents=True, exist_ok=True)
    CONVERT_DIR.mkdir(parents=True, exist_ok=True)
    start_crosshair()  # 准星找色常驻线程（读 features.crosshair 配置，启用即生效）
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"[ttbox-web] http://0.0.0.0:{port}", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
