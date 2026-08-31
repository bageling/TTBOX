from __future__ import annotations

import ast
import base64
import hashlib
import hmac
import io
import json
import math
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from flask import Flask, Response, jsonify, make_response, render_template, request, send_file, url_for
from waitress import serve

from theme_manager import ThemeError, ThemeManager
from wifi_manager import WifiError, activate_client_wifi, apply_ap_hotspot, connect_wifi, reset_to_default_wifi, wifi_status


class DaemonError(RuntimeError):
    def __init__(self, message: str, payload: dict[str, Any] | None = None, status_code: int | None = None):
        super().__init__(message)
        self.payload = payload or {}
        self.status_code = status_code


class LicenseServerError(DaemonError):
    def __init__(self, message: str, payload: dict[str, Any] | None = None, status_code: int | None = None):
        super().__init__(message)
        self.payload = payload or {}
        self.status_code = status_code


class ScriptError(DaemonError):
    def __init__(self, message: str, payload: dict[str, Any] | None = None):
        super().__init__(message)
        self.payload = payload or {}


ROOT_DIR = Path(os.environ.get("AIASSISTANCE_ROOT", Path(__file__).resolve().parents[1])).resolve()
RUN_DIR = ROOT_DIR / "run"
SOCKET_PATH = Path(os.environ.get("AIASSISTANCE_SOCKET", RUN_DIR / "daemon.sock"))
PREVIEW_PATH = RUN_DIR / "preview.jpg"
PREVIEW_UDP_HOST = os.environ.get("AIASSISTANCE_PREVIEW_UDP_HOST", "127.0.0.1")
PREVIEW_UDP_PORT = int(os.environ.get("AIASSISTANCE_PREVIEW_UDP_PORT", "8091"))
PREVIEW_PACKET = struct.Struct("!4sIHHH")
PREVIEW_MAGIC = b"AIPV"
PREVIEW_MAX_CHUNKS = 512
PREVIEW_FRAME_TTL_SEC = 1.0
RKNN_CONVERTER_WORKSPACE = Path(os.environ.get("AIASSISTANCE_RKNN_CONVERTER_WORKSPACE", "/home/orangepi/aiassistance-rknn")).expanduser()
RKNN_CONVERTER_PYTHON = os.environ.get("AIASSISTANCE_RKNN_CONVERTER_PYTHON", "")
RKNN_CONVERTER_SCRIPT = os.environ.get("AIASSISTANCE_RKNN_CONVERTER_SCRIPT", "")
RKNN_TARGET_PLATFORM = os.environ.get("AIASSISTANCE_RKNN_TARGET_PLATFORM", "rk3588")
RKNN_CONVERTER_TIMEOUT_SEC = int(os.environ.get("AIASSISTANCE_RKNN_CONVERTER_TIMEOUT_SEC", "900"))
RKNN_DATASET_MAX_IMAGES = int(os.environ.get("AIASSISTANCE_RKNN_DATASET_MAX_IMAGES", "8"))
RKNN_ZIP_MAX_FILES = int(os.environ.get("AIASSISTANCE_RKNN_ZIP_MAX_FILES", "256"))
RKNN_ZIP_MAX_UNPACKED_BYTES = int(os.environ.get("AIASSISTANCE_RKNN_ZIP_MAX_UNPACKED_BYTES", str(512 * 1024 * 1024)))
RKNN_DEFAULT_CALIBRATION_DIR = Path(
    os.environ.get("AIASSISTANCE_RKNN_DEFAULT_CALIBRATION_DIR", ROOT_DIR / "test_images" / "General")
).expanduser()
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
XCSH_BACKGROUND_DIR = Path(
    os.environ.get("AIASSISTANCE_XCSH_BACKGROUND_DIR", ROOT_DIR / "config" / "xcsh-background")
).resolve()
XCSH_BACKGROUND_MAX_BYTES = int(os.environ.get("AIASSISTANCE_XCSH_BACKGROUND_MAX_BYTES", str(8 * 1024 * 1024)))
XCSH_BACKGROUND_MAX_DIMENSION = int(os.environ.get("AIASSISTANCE_XCSH_BACKGROUND_MAX_DIMENSION", "8192"))
XCSH_BACKGROUND_MAX_PIXELS = int(os.environ.get("AIASSISTANCE_XCSH_BACKGROUND_MAX_PIXELS", str(40 * 1024 * 1024)))
XCSH_BACKGROUND_DEFAULT_OVERLAY = 0.58
XCSH_BACKGROUND_MAX_OVERLAY = 0.9
XCSH_BACKGROUND_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}
MODEL_LABEL_SUFFIXES = {".txt", ".labels", ".names", ".json", ".csv"}
MODEL_LABEL_MAX_BYTES = int(os.environ.get("AIASSISTANCE_MODEL_LABEL_MAX_BYTES", str(64 * 1024)))
MODEL_LABEL_MAX_COUNT = int(os.environ.get("AIASSISTANCE_MODEL_LABEL_MAX_COUNT", "30"))
MODEL_LABEL_MAX_NAME_CHARS = int(os.environ.get("AIASSISTANCE_MODEL_LABEL_MAX_NAME_CHARS", "80"))
MODEL_PRESET_MAX_BYTES = int(os.environ.get("AIASSISTANCE_MODEL_PRESET_MAX_BYTES", str(2 * 1024 * 1024)))
MOTION_SAMPLE_MAX_BYTES = 256 * 1024
MOTION_PROFILE_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
REMOTE_CONFIG_PATH = ROOT_DIR / "config" / "remote_inference.json"
AUTO_START_CONFIG_PATH = ROOT_DIR / "config" / "startup.json"
AUTO_START_BOOT_MARKER_PATH = RUN_DIR / "auto-start-boot.json"
AUTO_START_INITIAL_DELAY_SEC = float(os.environ.get("AIASSISTANCE_AUTO_START_DELAY_SEC", "20"))
AUTO_START_POLL_INTERVAL_SEC = float(os.environ.get("AIASSISTANCE_AUTO_START_POLL_SEC", "2"))
AUTO_START_TIMEOUT_SEC = float(os.environ.get("AIASSISTANCE_AUTO_START_TIMEOUT_SEC", "180"))
AUTO_START_STABLE_CHECKS = max(1, int(os.environ.get("AIASSISTANCE_AUTO_START_STABLE_CHECKS", "3")))
REMOTE_CONTROL_PORT = int(os.environ.get("AIASSISTANCE_REMOTE_CONTROL_PORT", "39100"))
REMOTE_FRAME_PORT = int(os.environ.get("AIASSISTANCE_REMOTE_FRAME_PORT", "39101"))
REMOTE_RESULT_PORT = int(os.environ.get("AIASSISTANCE_REMOTE_RESULT_PORT", "39102"))
REMOTE_ONNX_DEFAULT_FRAME_FORMAT = "h264"
REMOTE_CONTROL_TIMEOUT_SEC = float(os.environ.get("AIASSISTANCE_REMOTE_CONTROL_TIMEOUT_SEC", "1.0"))
REMOTE_UPLOAD_TIMEOUT_SEC = float(os.environ.get("AIASSISTANCE_REMOTE_UPLOAD_TIMEOUT_SEC", "900"))
CLOUD_ENCRYPTED_MODEL_TIMEOUT_SEC = float(os.environ.get("AIASSISTANCE_CLOUD_ENCRYPTED_MODEL_TIMEOUT_SEC", "180"))
CLOUD_ENCRYPTED_RECOVERY_WAIT_SEC = float(os.environ.get("AIASSISTANCE_CLOUD_ENCRYPTED_RECOVERY_WAIT_SEC", "45"))
CONTROL_START_DAEMON_TIMEOUT_SEC = float(os.environ.get("AIASSISTANCE_CONTROL_START_DAEMON_TIMEOUT_SEC", "30"))
LICENSE_DIR = ROOT_DIR / "license"
LICENSE_PATH = LICENSE_DIR / "license.json"
DEVICE_PATH = LICENSE_DIR / "device.json"
DEVICE_RECOVERY_PATH = LICENSE_DIR / "device_recovery.json"
CORE_DIR = ROOT_DIR / "core"
CORE_ENC_PATH = CORE_DIR / "libai_core.so.enc"
CORE_ACTIVATION_PATH = LICENSE_DIR / "core_activation.json"
ONLINE_GRANT_PATH = LICENSE_DIR / "online_grant.json"
LICENSE_SERVER_URL_PATH = LICENSE_DIR / "server_url.txt"
LICENSE_KEY_CACHE_PATH = LICENSE_DIR / "activation_key.json"
REVOKED_PATH = LICENSE_DIR / "revoked.json"
MODEL_KEY_PATH = LICENSE_DIR / "model_key.bin"
MODEL_KEY_ACTIVATION_PATH = LICENSE_DIR / "model_key_activation.json"
MODEL_KEY_BYTES = 32
UPDATES_DIR = ROOT_DIR / "updates"
RELEASES_DIR = ROOT_DIR / "releases"
VERSION_PATH = ROOT_DIR / "VERSION"
BUNDLED_VERSION_PATH = ROOT_DIR / "web" / "VERSION"
LAST_INSTALLED_PATH = UPDATES_DIR / "last-installed.json"
LAST_UPDATE_CHECK_PATH = UPDATES_DIR / "last-update-check.json"
UPDATE_STATUS_PATH = UPDATES_DIR / "update-status.json"
HAILO_STATUS_PATH = RUN_DIR / "hailo-install-status.json"
HAILO_PACKAGE_DIR = UPDATES_DIR / "hailo"
HAILO_INSTALL_SCRIPT = ROOT_DIR / "scripts" / "install_hailo_from_package.py"
HAILO_EXPECTED_VERSION = os.environ.get("AIASSISTANCE_HAILO_VERSION", "4.23.0")
HAILO_INSTALL_STALE_SEC = int(os.environ.get("AIASSISTANCE_HAILO_INSTALL_STALE_SEC", "300"))
DEFAULT_LICENSE_SERVER_URL = os.environ.get("AIASSISTANCE_LICENSE_SERVER", "")
XCSH_MIN_UPDATE_VERSION = "2026.06.29.1"
UPDATE_DOWNLOAD_TIMEOUT_SEC = int(os.environ.get("AIASSISTANCE_UPDATE_DOWNLOAD_TIMEOUT_SEC", "120"))
USB_PROXY_PROFILE_DIR = Path(os.environ.get("AIASSISTANCE_USB_PROXY_PROFILE_DIR", "/opt/usb-proxy/profiles")).expanduser()
USB_PROXY_ROOT = Path(os.environ.get("AIASSISTANCE_USB_PROXY_ROOT", "/opt/usb-proxy")).expanduser()
USB_PROXY_ENV_PATH = Path(os.environ.get("AIASSISTANCE_USB_PROXY_ENV_PATH", "/etc/default/usb-proxy")).expanduser()
USB_PROXY_ENC_PATH = USB_PROXY_ROOT / "bin" / "usb-proxy.enc"
USB_PROXY_ACTIVATION_PATH = USB_PROXY_ROOT / "license" / "usb_proxy_activation.json"
USB_PROXY_RUNTIME_STATUS_PATH = Path(os.environ.get(
    "AIASSISTANCE_USB_RUNTIME_STATUS_FILE",
    "/run/aiassistance-usb-runtime.status",
)).expanduser()
WEB_PORT_OVERRIDE_PATH = Path(os.environ.get(
    "AIASSISTANCE_WEB_PORT_OVERRIDE_PATH",
    "/etc/systemd/system/aiassistance-web.service.d/10-port.conf",
)).expanduser()
DEFAULT_WEB_PORT = int(os.environ.get("AIASSISTANCE_DEFAULT_WEB_PORT", "8080"))
USB_DIAGNOSTIC_COMMAND_TIMEOUT_SEC = int(os.environ.get("AIASSISTANCE_USB_DIAGNOSTIC_COMMAND_TIMEOUT_SEC", "8"))
USB_DIAGNOSTIC_MAX_COMMAND_BYTES = int(os.environ.get("AIASSISTANCE_USB_DIAGNOSTIC_MAX_COMMAND_BYTES", str(256 * 1024)))
USB_DIAGNOSTIC_MAX_PROFILE_BYTES = int(os.environ.get("AIASSISTANCE_USB_DIAGNOSTIC_MAX_PROFILE_BYTES", str(512 * 1024)))
USB_DIAGNOSTIC_MAX_PROFILE_FILES = int(os.environ.get("AIASSISTANCE_USB_DIAGNOSTIC_MAX_PROFILE_FILES", "12"))
MOUSE_PROXY_MODE_SWITCH_TIMEOUT_SEC = float(os.environ.get("AIASSISTANCE_MOUSE_PROXY_MODE_SWITCH_TIMEOUT_SEC", "180"))
USB_PROXY_SETTLE_DELAY_MAX_SEC = 30.0
ROOTFS_EXPAND_SCRIPT = ROOT_DIR / "scripts" / "expand_rootfs.py"
ROOTFS_EXPAND_STATUS_TIMEOUT_SEC = float(os.environ.get("AIASSISTANCE_ROOTFS_EXPAND_STATUS_TIMEOUT_SEC", "8"))
ROOTFS_EXPAND_APPLY_TIMEOUT_SEC = float(os.environ.get("AIASSISTANCE_ROOTFS_EXPAND_APPLY_TIMEOUT_SEC", "240"))
ROOTFS_EXPAND_STATUS_CACHE_SEC = float(os.environ.get("AIASSISTANCE_ROOTFS_EXPAND_STATUS_CACHE_SEC", "12"))
LAN_BLOCKLIST_SCRIPT = ROOT_DIR / "scripts" / "lan_blocklist.py"
LAN_BLOCKLIST_TIMEOUT_SEC = float(os.environ.get("AIASSISTANCE_LAN_BLOCKLIST_TIMEOUT_SEC", "20"))
RUNTIME_SCRIPT_FALLBACK_DIR = ROOT_DIR / "web" / "runtime_scripts"
RUNTIME_SCRIPT_FALLBACKS = ("toggle_aim_trace_button.sh",)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _restore_missing_runtime_scripts() -> None:
    scripts_dir = ROOT_DIR / "scripts"
    for name in RUNTIME_SCRIPT_FALLBACKS:
        source = RUNTIME_SCRIPT_FALLBACK_DIR / name
        target = scripts_dir / name
        if target.exists() or not source.exists():
            continue
        try:
            scripts_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            target.chmod(target.stat().st_mode | 0o111)
        except OSError:
            pass


SHOW_AIM_TRACE_BUTTON = _env_flag("AIASSISTANCE_SHOW_AIM_TRACE_BUTTON", False)
HOSTNAME_PATTERN = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")

app = Flask(__name__, template_folder=str(ROOT_DIR / "web" / "templates"), static_folder=str(ROOT_DIR / "web" / "static"))
theme_manager = ThemeManager(ROOT_DIR)

_preview_condition = threading.Condition()
_preview_latest_jpeg: bytes | None = None
_preview_latest_seq = 0
_preview_receiver_started = False
_preview_receiver_lock = threading.Lock()
_cpu_sample_lock = threading.Lock()
_cpu_last_sample: tuple[int, int] | None = None
_model_conversion_lock = threading.Lock()
_rootfs_status_lock = threading.Lock()
_rootfs_status_cache: tuple[float, dict[str, Any]] | None = None
_lan_blocklist_lock = threading.Lock()
_lan_blocklist_cache: tuple[float, set[str]] | None = None
_update_status_lock = threading.Lock()
_hailo_install_lock = threading.Lock()
_hailo_install_thread: threading.Thread | None = None
_license_recovery_lock = threading.Lock()
_model_key_install_lock = threading.Lock()
_license_recovery_last_attempt = 0.0
_license_recovery_last_error = ""
_LICENSE_RECOVERY_RETRY_SEC = 300.0
_auto_core_update_lock = threading.Lock()
_auto_core_update_thread: threading.Thread | None = None
_auto_core_update_last_attempt = 0.0
_auto_core_update_last_error = ""
_AUTO_CORE_UPDATE_RETRY_SEC = 300.0
_activation_identity_reset_lock = threading.Lock()
_activation_identity_reset_last_attempt = 0.0
_ACTIVATION_IDENTITY_RESET_RETRY_SEC = 45.0
_activation_full_recovery_lock = threading.Lock()
_activation_full_recovery_last_attempt = 0.0
_ACTIVATION_FULL_RECOVERY_RETRY_SEC = 30.0
_auto_start_settings_lock = threading.Lock()
_auto_start_marker_lock = threading.Lock()
_auto_start_thread: threading.Thread | None = None
_xcsh_background_lock = threading.RLock()


def _prune_preview_frames(frames: dict[int, dict[str, Any]]) -> None:
    now = time.monotonic()
    stale = [frame_id for frame_id, entry in frames.items() if now - entry["created"] > PREVIEW_FRAME_TTL_SEC]
    for frame_id in stale:
        frames.pop(frame_id, None)


def _publish_preview_frame(frame: bytes) -> None:
    global _preview_latest_jpeg, _preview_latest_seq
    with _preview_condition:
        _preview_latest_jpeg = frame
        _preview_latest_seq += 1
        _preview_condition.notify_all()


def _preview_udp_receiver() -> None:
    frames: dict[int, dict[str, Any]] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((PREVIEW_UDP_HOST, PREVIEW_UDP_PORT))
    sock.settimeout(0.5)

    while True:
        try:
            packet, _addr = sock.recvfrom(65535)
        except socket.timeout:
            _prune_preview_frames(frames)
            continue
        except OSError:
            time.sleep(0.2)
            continue

        if len(frames) > PREVIEW_MAX_CHUNKS:
            _prune_preview_frames(frames)
        if len(packet) < PREVIEW_PACKET.size:
            continue
        magic, frame_id, chunk_index, chunk_count, payload_size = PREVIEW_PACKET.unpack_from(packet)
        if magic != PREVIEW_MAGIC or chunk_count == 0 or chunk_count > PREVIEW_MAX_CHUNKS:
            continue
        if chunk_index >= chunk_count:
            continue
        payload = packet[PREVIEW_PACKET.size:PREVIEW_PACKET.size + payload_size]
        if len(payload) != payload_size:
            continue

        if chunk_count == 1:
            _publish_preview_frame(payload)
            continue

        entry = frames.get(frame_id)
        if entry is None or entry["chunk_count"] != chunk_count:
            entry = {
                "chunk_count": chunk_count,
                "received": 0,
                "chunks": [None] * chunk_count,
                "created": time.monotonic(),
            }
            frames[frame_id] = entry

        if entry["chunks"][chunk_index] is None:
            entry["chunks"][chunk_index] = payload
            entry["received"] += 1

        if entry["received"] == chunk_count:
            chunks = entry["chunks"]
            if all(chunk is not None for chunk in chunks):
                _publish_preview_frame(b"".join(chunks))
            frames.pop(frame_id, None)


def ensure_preview_receiver_started() -> None:
    global _preview_receiver_started
    if _preview_receiver_started:
        return
    with _preview_receiver_lock:
        if _preview_receiver_started:
            return
        thread = threading.Thread(target=_preview_udp_receiver, name="preview-udp-receiver", daemon=True)
        thread.start()
        _preview_receiver_started = True


def daemon_call(command: str, *, timeout: float = 8.0, **payload: Any) -> Any:
    message = json.dumps({"command": command, **payload}, ensure_ascii=False) + "\n"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(str(SOCKET_PATH))
            client.sendall(message.encode("utf-8"))
            chunks: list[bytes] = []
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if b"\n" in chunk:
                    break
    except OSError as exc:
        raise DaemonError(f"daemon socket unavailable: {exc}") from exc

    raw = b"".join(chunks).decode("utf-8").strip()
    if not raw:
        raise DaemonError("daemon returned empty response")
    response = json.loads(raw)
    if not response.get("ok"):
        raise DaemonError(
            response.get("error", "daemon command failed"),
            payload=response.get("result") or response.get("data") or {},
            status_code=response.get("status_code"),
        )
    return response.get("result")


def _is_transient_daemon_socket_busy(error: BaseException | str) -> bool:
    message = str(error).lower()
    return (
        "daemon socket unavailable" in message
        and (
            "timed out" in message
            or "resource temporarily unavailable" in message
            or "errno 11" in message
        )
    )


def api_ok(data: Any):
    return jsonify({"ok": True, "data": data})


def api_error(message: str, status: int = 400, data: Any | None = None, status_code: int | None = None):
    payload: dict[str, Any] = {"ok": False, "error": message}
    if data is not None:
        payload["data"] = data
    return jsonify(payload), status_code or status


def _read_version_file(path: Path) -> str:
    try:
        version = path.read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError):
        return ""
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", version):
        return ""
    return version


def _read_last_installed() -> dict[str, Any]:
    try:
        installed = json.loads(LAST_INSTALLED_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return installed if isinstance(installed, dict) and installed.get("installed") else {}


def _installed_version_field(field: str) -> str:
    version = str(_read_last_installed().get(field) or "").strip()
    return version if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", version) else ""


def _current_app_version(fallback: str = "") -> str:
    bundled = _read_version_file(BUNDLED_VERSION_PATH)
    if bundled:
        return bundled
    installed_app = _installed_version_field("app_version")
    if installed_app:
        return installed_app
    legacy = _read_version_file(VERSION_PATH) or _installed_version_field("version")
    return legacy or fallback


def _current_release_version(fallback: str = "") -> str:
    installed = _read_version_file(VERSION_PATH) or _installed_version_field("version")
    return installed or _current_app_version(fallback)


def _compare_version_text(left: Any, right: Any) -> int:
    left_parts = re.findall(r"\d+|[A-Za-z]+|[^A-Za-z\d]+", str(left or ""))
    right_parts = re.findall(r"\d+|[A-Za-z]+|[^A-Za-z\d]+", str(right or ""))
    length = max(len(left_parts), len(right_parts))
    for index in range(length):
        left_part = left_parts[index] if index < len(left_parts) else ""
        right_part = right_parts[index] if index < len(right_parts) else ""
        if left_part == right_part:
            continue
        if left_part.isdigit() and right_part.isdigit():
            return int(left_part) - int(right_part)
        return -1 if left_part < right_part else 1
    return 0


def _version_is_before(left: Any, right: Any) -> bool:
    return _compare_version_text(left, right) < 0


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json_object_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


XCSH_BACKGROUND_SETTINGS_NAME = "settings.json"
XCSH_BACKGROUND_FILENAME_RE = re.compile(r"background-[0-9a-f]{16}\.(?:jpg|png|webp)")
XCSH_BACKGROUND_ACCENT_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
XCSH_BACKGROUND_DEFAULT_ACCENT = "#7b5aae"


def _xcsh_background_settings_path() -> Path:
    return XCSH_BACKGROUND_DIR / XCSH_BACKGROUND_SETTINGS_NAME


def _xcsh_background_overlay(value: Any, *, strict: bool = False) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        if strict:
            raise DaemonError("背景暗度必须是 0 到 90 之间的数值")
        return XCSH_BACKGROUND_DEFAULT_OVERLAY
    if not math.isfinite(parsed) or parsed < 0 or parsed > XCSH_BACKGROUND_MAX_OVERLAY:
        if strict:
            raise DaemonError("背景暗度必须是 0 到 90 之间的数值")
        return XCSH_BACKGROUND_DEFAULT_OVERLAY
    return round(parsed, 3)


def _xcsh_background_accent(value: Any, *, strict: bool = False) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    normalized = str(value).strip().lower()
    if not XCSH_BACKGROUND_ACCENT_RE.fullmatch(normalized):
        if strict:
            raise DaemonError("主题色必须是 6 位十六进制颜色")
        return None
    return normalized


def _xcsh_background_palette(accent: str | None) -> dict[str, str | bool]:
    effective = accent or XCSH_BACKGROUND_DEFAULT_ACCENT
    red = int(effective[1:3], 16)
    green = int(effective[3:5], 16)
    blue = int(effective[5:7], 16)
    strong = tuple(round(channel * 0.42 + 255 * 0.58) for channel in (red, green, blue))
    strong_hex = "#%02x%02x%02x" % strong
    luminance = (red * 299 + green * 587 + blue * 114) / 1000
    contrast = "#09060f" if luminance >= 158 else "#ffffff"
    return {
        "accent_color": effective,
        "custom_accent": accent is not None,
        "accent_strong": strong_hex,
        "accent_rgb": f"{red}, {green}, {blue}",
        "accent_strong_rgb": f"{strong[0]}, {strong[1]}, {strong[2]}",
        "accent_contrast": contrast,
    }


def _xcsh_background_dimensions(data: bytes, content_type: str) -> tuple[int, int] | None:
    if content_type == "image/png":
        if len(data) < 24 or data[12:16] != b"IHDR":
            return None
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")

    if content_type == "image/jpeg":
        if len(data) < 4 or data[:2] != b"\xff\xd8":
            return None
        position = 2
        sof_markers = {
            0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
            0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
        }
        while position + 3 < len(data):
            while position < len(data) and data[position] != 0xFF:
                position += 1
            while position < len(data) and data[position] == 0xFF:
                position += 1
            if position >= len(data):
                break
            marker = data[position]
            position += 1
            if marker in {0xD8, 0xD9, 0x01} or 0xD0 <= marker <= 0xD7:
                continue
            if position + 2 > len(data):
                break
            segment_length = int.from_bytes(data[position:position + 2], "big")
            if segment_length < 2 or position + segment_length > len(data):
                break
            if marker in sof_markers and segment_length >= 7:
                height = int.from_bytes(data[position + 3:position + 5], "big")
                width = int.from_bytes(data[position + 5:position + 7], "big")
                return width, height
            position += segment_length
        return None

    if content_type == "image/webp":
        if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
            return None
        chunk_type = data[12:16]
        if chunk_type == b"VP8 ":
            if len(data) < 30 or data[23:26] != b"\x9d\x01\x2a":
                return None
            return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF
        if chunk_type == b"VP8L":
            if len(data) < 25 or data[20] != 0x2F:
                return None
            bits = int.from_bytes(data[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if chunk_type == b"VP8X":
            if len(data) < 30 or data[20] & 0x02:
                return None
            width = 1 + int.from_bytes(data[24:27] + b"\x00", "little")
            height = 1 + int.from_bytes(data[27:30] + b"\x00", "little")
            return width, height
    return None


def _validate_xcsh_background_image(data: bytes) -> tuple[str, int, int]:
    if not data:
        raise DaemonError("背景图片不能为空")
    if len(data) > XCSH_BACKGROUND_MAX_BYTES:
        raise DaemonError(
            f"背景图片不能超过 {XCSH_BACKGROUND_MAX_BYTES // (1024 * 1024)} MB",
            status_code=413,
        )
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        content_type = "image/png"
    elif data.startswith(b"\xff\xd8\xff"):
        content_type = "image/jpeg"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        content_type = "image/webp"
    else:
        raise DaemonError("仅支持 JPG、PNG 或 WebP 背景图片")

    dimensions = _xcsh_background_dimensions(data, content_type)
    if not dimensions or any(value <= 0 for value in dimensions):
        raise DaemonError("背景图片格式无效或无法读取尺寸")
    width, height = dimensions
    if width > XCSH_BACKGROUND_MAX_DIMENSION or height > XCSH_BACKGROUND_MAX_DIMENSION:
        raise DaemonError(f"背景图片尺寸不能超过 {XCSH_BACKGROUND_MAX_DIMENSION}×{XCSH_BACKGROUND_MAX_DIMENSION}")
    if width * height > XCSH_BACKGROUND_MAX_PIXELS:
        raise DaemonError("背景图片像素数量过大")
    return content_type, width, height


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with temp_path.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _xcsh_background_record() -> dict[str, Any]:
    settings = _read_json_object(_xcsh_background_settings_path())
    filename = str(settings.get("filename") or "")
    image_path = XCSH_BACKGROUND_DIR / filename
    valid_filename = bool(XCSH_BACKGROUND_FILENAME_RE.fullmatch(filename))
    has_image = valid_filename and image_path.is_file()
    content_type = str(settings.get("content_type") or "")
    if content_type not in XCSH_BACKGROUND_TYPES:
        content_type = ""
        has_image = False
    accent = _xcsh_background_accent(settings.get("accent_color"))
    palette = _xcsh_background_palette(accent)
    revision = str(settings.get("revision") or "")
    if not re.fullmatch(r"[0-9a-f]{16}", revision):
        revision = ""
    if has_image and not revision:
        try:
            revision = hashlib.sha256(image_path.read_bytes()).hexdigest()[:16]
        except OSError:
            has_image = False
    return {
        "settings": settings,
        "filename": filename if has_image else "",
        "image_path": image_path if has_image else None,
        "has_image": has_image,
        "content_type": content_type if has_image else "",
        "revision": revision if has_image else "",
        "overlay_opacity": _xcsh_background_overlay(settings.get("overlay_opacity")),
        **palette,
    }


def _xcsh_background_public_state() -> dict[str, Any]:
    with _xcsh_background_lock:
        record = _xcsh_background_record()
    has_image = bool(record["has_image"])
    revision = str(record["revision"] or "")
    return {
        "enabled": bool(record["settings"].get("enabled", False)) and has_image,
        "has_image": has_image,
        "image_url": url_for("get_xcsh_background_image", v=revision) if has_image else "",
        "revision": revision,
        "content_type": record["content_type"],
        "width": int(record["settings"].get("width") or 0) if has_image else 0,
        "height": int(record["settings"].get("height") or 0) if has_image else 0,
        "size": int(record["settings"].get("size") or 0) if has_image else 0,
        "overlay_opacity": record["overlay_opacity"],
        "accent_color": record["accent_color"],
        "custom_accent": bool(record["custom_accent"]),
        "accent_strong": record["accent_strong"],
        "accent_rgb": record["accent_rgb"],
        "accent_strong_rgb": record["accent_strong_rgb"],
        "accent_contrast": record["accent_contrast"],
    }


def _require_xcsh_background_access() -> None:
    if _current_ui_brand() != "xcsh":
        raise DaemonError("网页背景仅对 XCSH 系统开放", status_code=403)


def _reconcile_release_version_from_update_check(payload: Any, license_payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict) or payload.get("update_available") is not False:
        return False
    latest_version = str(payload.get("latest_version") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", latest_version):
        return False
    package = payload.get("package")
    if isinstance(package, dict) and package.get("url"):
        return False
    components = payload.get("components")
    if not isinstance(components, dict):
        return False
    for name in ("core", "usb_proxy"):
        component = components.get(name)
        if not isinstance(component, dict):
            return False
        target = str(component.get("latest_version") or "").strip()
        current = str(component.get("current_version") or "").strip()
        if target and current != target:
            return False
    if _current_release_version("") == latest_version:
        return False

    app_version = str(license_payload.get("app_version") or _current_app_version("")).strip()
    installed = _read_last_installed()
    installed.update({
        "installed": True,
        "version": latest_version,
        "app_version": app_version,
        "release_reconciled_at": int(time.time()),
    })
    version_temp = VERSION_PATH.with_name(f".{VERSION_PATH.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        VERSION_PATH.parent.mkdir(parents=True, exist_ok=True)
        version_temp.write_text(latest_version + "\n", encoding="utf-8")
        os.replace(version_temp, VERSION_PATH)
        _write_json_object_atomic(LAST_INSTALLED_PATH, installed)
    except OSError:
        return False
    finally:
        version_temp.unlink(missing_ok=True)
    payload["current_version"] = latest_version
    payload["current_version_reconciled"] = True
    return True


def _read_auto_start_settings() -> dict[str, Any]:
    with _auto_start_settings_lock:
        payload = _read_json_object(AUTO_START_CONFIG_PATH)
    return {
        "enabled": payload.get("enabled") is True,
        "updated_at": int(payload.get("updated_at") or 0),
    }


def _save_auto_start_settings(enabled: bool) -> dict[str, Any]:
    payload = {
        "enabled": bool(enabled),
        "updated_at": int(time.time()),
    }
    with _auto_start_settings_lock:
        _write_json_object_atomic(AUTO_START_CONFIG_PATH, payload)
    return payload


def _current_boot_id() -> str:
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return boot_id if re.fullmatch(r"[A-Za-z0-9-]{8,64}", boot_id) else ""


def _write_auto_start_marker(boot_id: str, status: str, message: str = "") -> None:
    marker = {
        "boot_id": boot_id,
        "status": status,
        "message": str(message or "")[:500],
        "updated_at": int(time.time()),
    }
    with _auto_start_marker_lock:
        _write_json_object_atomic(AUTO_START_BOOT_MARKER_PATH, marker)


def _auto_start_settings_payload() -> dict[str, Any]:
    settings = _read_auto_start_settings()
    if not settings["enabled"]:
        return {**settings, "status": "disabled", "message": ""}

    marker = _read_json_object(AUTO_START_BOOT_MARKER_PATH)
    boot_id = _current_boot_id()
    if not boot_id or marker.get("boot_id") != boot_id:
        return {**settings, "status": "next_boot", "message": "将在下次开机时自动启动"}
    return {
        **settings,
        "status": str(marker.get("status") or "waiting"),
        "message": str(marker.get("message") or ""),
    }


def _auto_start_runtime_ready(payload: Any) -> tuple[bool, bool]:
    if not isinstance(payload, dict):
        return False, False
    runtime = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    if runtime.get("running") or runtime.get("status") in {"starting", "reconnecting"}:
        return True, True
    license_state = runtime.get("license") if isinstance(runtime.get("license"), dict) else {}
    core_state = runtime.get("core") if isinstance(runtime.get("core"), dict) else {}
    ready = (
        license_state.get("valid") is True
        and core_state.get("loaded") is True
        and runtime.get("status") in {"stopped", "idle"}
    )
    return ready, False


def _auto_start_worker(boot_id: str) -> None:
    if AUTO_START_INITIAL_DELAY_SEC > 0:
        time.sleep(AUTO_START_INITIAL_DELAY_SEC)

    deadline = time.monotonic() + max(1.0, AUTO_START_TIMEOUT_SEC)
    stable_checks = 0
    last_error = ""
    while time.monotonic() < deadline:
        if not _read_auto_start_settings()["enabled"]:
            _write_auto_start_marker(boot_id, "cancelled", "开机自启动已关闭")
            return
        try:
            payload = daemon_call("get_state", timeout=min(5.0, max(1.0, AUTO_START_POLL_INTERVAL_SEC)))
            ready, already_running = _auto_start_runtime_ready(payload)
            if already_running:
                _write_auto_start_marker(boot_id, "already_running", "采集和推理已经在运行")
                return
            stable_checks = stable_checks + 1 if ready else 0
            last_error = ""
            if stable_checks >= AUTO_START_STABLE_CHECKS:
                if not _read_auto_start_settings()["enabled"]:
                    _write_auto_start_marker(boot_id, "cancelled", "开机自启动已关闭")
                    return
                _write_auto_start_marker(boot_id, "starting", "服务已稳定，正在启动采集和推理")
                _state_with_license_recovery("get_license")
                daemon_call("start", timeout=CONTROL_START_DAEMON_TIMEOUT_SEC)
                _write_auto_start_marker(boot_id, "started", "已自动启动采集和推理")
                return
        except (DaemonError, OSError, ValueError) as exc:
            stable_checks = 0
            last_error = str(exc)
        time.sleep(max(0.1, AUTO_START_POLL_INTERVAL_SEC))

    message = "等待服务稳定超时"
    if last_error:
        message = f"{message}：{last_error}"
    _write_auto_start_marker(boot_id, "timed_out", message)


def _schedule_auto_start_on_boot() -> bool:
    global _auto_start_thread
    if not _read_auto_start_settings()["enabled"]:
        return False
    boot_id = _current_boot_id()
    if not boot_id:
        return False
    with _auto_start_marker_lock:
        marker = _read_json_object(AUTO_START_BOOT_MARKER_PATH)
        if marker.get("boot_id") == boot_id:
            return False
        _write_json_object_atomic(AUTO_START_BOOT_MARKER_PATH, {
            "boot_id": boot_id,
            "status": "waiting",
            "message": "正在等待服务稳定",
            "updated_at": int(time.time()),
        })
    _auto_start_thread = threading.Thread(
        target=_auto_start_worker,
        args=(boot_id,),
        name="boot-auto-start",
        daemon=True,
    )
    _auto_start_thread.start()
    return True


def _normalize_ui_brand(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"yu", "xh", "xcsh"} else "yu"


def _current_ui_brand() -> str:
    license_doc = _read_json_object(LICENSE_PATH)
    if license_doc.get("ui_brand"):
        return _normalize_ui_brand(license_doc.get("ui_brand"))
    revoked_doc = _read_json_object(REVOKED_PATH)
    if revoked_doc.get("ui_brand"):
        return _normalize_ui_brand(revoked_doc.get("ui_brand"))
    cached_key_doc = _read_json_object(LICENSE_KEY_CACHE_PATH)
    if cached_key_doc.get("ui_brand"):
        return _normalize_ui_brand(cached_key_doc.get("ui_brand"))
    return "yu"


def _brand_payload(brand: str | None = None) -> dict[str, Any]:
    ui_brand = _normalize_ui_brand(brand if brand is not None else _current_ui_brand())
    if ui_brand == "xh":
        return {
            "ui_brand": "xh",
            "brand_title": "XH 系统",
            "brand_name": "XH 系统",
            "brand_eyebrow": "XH SYSTEM",
            "brand_mark": "XH",
            "app_title": "XH 系统",
            "allow_theme_switch": False,
            "default_theme": "dark",
            "default_local_name": "xh",
            "default_hotspot_ssid": "XH",
        }
    if ui_brand == "xcsh":
        return {
            "ui_brand": "xcsh",
            "brand_title": "XCSH 系统",
            "brand_name": "XCSH 系统",
            "brand_eyebrow": "XCSH SYSTEM",
            "brand_mark": "XC",
            "app_title": "XCSH 系统",
            "allow_theme_switch": False,
            "default_theme": "dark",
            "default_local_name": "xcsh",
            "default_hotspot_ssid": "XCSH",
        }
    return {
        "ui_brand": "yu",
        "brand_title": "YU 控制台",
        "brand_name": "YU",
        "brand_eyebrow": "AIASSISTANCE",
        "brand_mark": "AI",
        "app_title": "YU 控制台",
        "allow_theme_switch": True,
        "default_theme": "dark",
        "default_local_name": "aiassistance",
        "default_hotspot_ssid": "YUAI",
    }


def _template_context() -> dict[str, Any]:
    brand = _brand_payload()
    license_doc = _read_json_object(LICENSE_PATH)
    active_manifest = theme_manager.active_manifest(
        license_doc,
        str(brand.get("ui_brand") or "yu"),
        _current_app_version(""),
    )
    active_theme = {
        "id": "default",
        "version": "built-in",
        "color_scheme": "system",
        "styles": [],
    }
    if active_manifest:
        theme_id = str(active_manifest.get("id") or "")
        version = str(active_manifest.get("version") or "")
        active_theme = {
            "id": theme_id,
            "version": version,
            "color_scheme": str(active_manifest.get("color_scheme") or "system"),
            "styles": [
                url_for("theme_asset", theme_id=theme_id, version=version, filename=str(style))
                for style in active_manifest.get("styles", [])
            ],
        }
    xcsh_background = _xcsh_background_public_state() if brand.get("ui_brand") == "xcsh" else {
        "enabled": False,
        "has_image": False,
        "image_url": "",
        "revision": "",
        "content_type": "",
        "width": 0,
        "height": 0,
        "size": 0,
        "overlay_opacity": XCSH_BACKGROUND_DEFAULT_OVERLAY,
        **_xcsh_background_palette(None),
    }
    return {
        "show_aim_trace_button": SHOW_AIM_TRACE_BUTTON,
        "asset_version": _current_app_version("local"),
        "active_visual_theme": active_theme,
        "xcsh_background": xcsh_background,
        **brand,
    }


def _brand_template_name(template: str, ui_brand: str) -> str:
    if ui_brand in {"xh", "xcsh"} and template in {"index.html", "mobile.html"}:
        return f"{ui_brand}/{template}"
    return template


def _filter_release_notes_min_version(notes: Any, min_version: str) -> str:
    text = str(notes or "")
    if not text.strip():
        return ""
    lines = text.splitlines()
    blocks: list[tuple[str | None, list[str]]] = []
    current_version: str | None = None
    current_lines: list[str] = []
    heading_pattern = re.compile(r"^\s*([0-9]{4}(?:\.[0-9]{1,2}){2}(?:\.[0-9A-Za-z_-]+)?)\s*[：:]")
    for line in lines:
        match = heading_pattern.match(line)
        if match:
            if current_lines:
                blocks.append((current_version, current_lines))
            current_version = match.group(1)
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_lines:
        blocks.append((current_version, current_lines))
    if not blocks:
        return text.strip()
    kept: list[str] = []
    for version, block_lines in blocks:
        if version and _version_is_before(version, min_version):
            continue
        if kept and any(line.strip() for line in block_lines):
            kept.append("")
        kept.extend(block_lines)
    return "\n".join(kept).strip()


def _xcsh_filter_update_versions(payload: Any) -> Any:
    if not isinstance(payload, dict) or _current_ui_brand() != "xcsh":
        return payload
    filtered = dict(payload)
    versions = filtered.get("versions")
    filtered_versions: list[Any] = []
    if isinstance(versions, list):
        filtered_versions = [
            item
            for item in versions
            if not _version_is_before(
                item.get("version") if isinstance(item, dict) else item,
                XCSH_MIN_UPDATE_VERSION,
            )
        ]
        filtered["versions"] = filtered_versions
    for key in ("release_notes", "notes"):
        if key in filtered:
            filtered[key] = _filter_release_notes_min_version(filtered.get(key), XCSH_MIN_UPDATE_VERSION)
    if filtered.get("latest_version") and _version_is_before(filtered.get("latest_version"), XCSH_MIN_UPDATE_VERSION):
        latest_visible = next(
            (
                str(item.get("version") if isinstance(item, dict) else item)
                for item in filtered_versions
                if (item.get("version") if isinstance(item, dict) else item)
            ),
            "",
        )
        filtered["latest_version"] = latest_visible or _current_app_version(str(filtered.get("latest_version") or ""))
    package = filtered.get("package")
    package_version = package.get("version") if isinstance(package, dict) else filtered.get("latest_version")
    if package_version and _version_is_before(package_version, XCSH_MIN_UPDATE_VERSION):
        filtered["package"] = None
        components = filtered.get("components") if isinstance(filtered.get("components"), dict) else {}
        component_update_available = any(
            isinstance(component, dict) and bool(component.get("update_available"))
            for component in components.values()
        )
        filtered["update_available"] = component_update_available
    return filtered


def _xcsh_reject_old_update_version(version: Any) -> None:
    target = str(version or "").strip()
    if _current_ui_brand() == "xcsh" and target and _version_is_before(target, XCSH_MIN_UPDATE_VERSION):
        raise DaemonError(f"XCSH 系统不能切换到 {XCSH_MIN_UPDATE_VERSION} 之前的版本")


def _activation_aad_matches_current_device(
    activation: dict[str, Any],
    license_payload: dict[str, Any],
    *,
    version_field: str,
) -> bool:
    try:
        aad = json.loads(str(activation.get("aad") or ""))
    except json.JSONDecodeError:
        return False
    if not isinstance(aad, dict):
        return False
    license_state = license_payload.get("license") if isinstance(license_payload.get("license"), dict) else {}
    return (
        str(aad.get("license_id") or "") == str(license_state.get("license_id") or "")
        and str(aad.get("device_id") or "") == str(license_state.get("device_id") or "")
        and str(aad.get("device_fingerprint_hash") or "") == str(license_state.get("device_fingerprint_hash") or "")
        and bool(str(aad.get(version_field) or "").strip())
    )


def _current_core_version(license_payload: dict[str, Any]) -> str:
    activation = _read_json_object(CORE_ACTIVATION_PATH)
    if activation and not _activation_aad_matches_current_device(activation, license_payload, version_field="core_version"):
        return ""
    version = str(activation.get("version") or "").strip()
    if version:
        return version
    core = license_payload.get("core") if isinstance(license_payload.get("core"), dict) else {}
    return str(core.get("version") or "").strip()


def _current_usb_proxy_state(license_payload: dict[str, Any]) -> dict[str, str]:
    activation = _read_json_object(USB_PROXY_ACTIVATION_PATH)
    if activation and not _activation_aad_matches_current_device(activation, license_payload, version_field="usb_proxy_version"):
        return {"version": "", "format": ""}
    return {
        "version": str(activation.get("version") or "").strip(),
        "format": str(activation.get("format") or "aiusbproxy1").strip(),
    }


def _current_usb_proxy_version(license_payload: dict[str, Any]) -> str:
    return _current_usb_proxy_state(license_payload)["version"]


def _read_proc_cpuinfo_fields() -> dict[str, str]:
    fields: dict[str, str] = {}
    try:
        lines = Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return fields
    for line in lines:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = re.sub(r"\s+", "", key)
        value = value.strip()
        if key in {"Serial", "Hardware", "Revision"} and value:
            fields[key] = value
    return fields


def _looks_like_board_ethernet_name(name: str) -> bool:
    if not name or name == "lo":
        return False
    excluded_prefixes = ("docker", "br-", "veth", "virbr", "tap", "tun", "wg", "wl", "usb", "enx")
    if any(name.startswith(prefix) for prefix in excluded_prefixes):
        return False
    return name.startswith(("eth", "enP", "enp", "eno", "ens", "end"))


def _system_mac_addresses() -> list[str]:
    net_dir = Path("/sys/class/net")
    addresses: list[str] = []
    try:
        names = sorted(path.name for path in net_dir.iterdir())
    except OSError:
        return addresses
    for name in names:
        if not _looks_like_board_ethernet_name(name):
            continue
        address = _read_sysfs_value(net_dir / name / "address").strip().lower()
        if address and address != "00:00:00:00:00:00" and re.fullmatch(r"[0-9a-f]{2}(:[0-9a-f]{2}){5}", address):
            addresses.append(address)
    return addresses


def _cache_secret_material(device: dict[str, Any] | None = None) -> str:
    serials: list[str] = []
    serial = _read_sysfs_value(Path("/proc/device-tree/serial-number")).strip()
    if serial and serial != "missing":
        serials.append(serial)
    cpuinfo = _read_proc_cpuinfo_fields()
    for key in ("Serial",):
        value = cpuinfo.get(key, "").strip()
        if value:
            serials.append(value)
    if serials:
        unique = sorted(set(serials))
        return "\n".join(unique)
    unique = sorted(set(_system_mac_addresses()))
    return "\n".join(unique)


def _license_key_cache_key(device: dict[str, Any] | None = None) -> bytes:
    material = _cache_secret_material(device).encode("utf-8", "replace")
    return hashlib.sha256(b"aiassistance-license-key-cache-v1\n" + material).digest()


def _cache_keystream(secret: bytes, nonce: bytes, length: int) -> bytes:
    chunks: list[bytes] = []
    counter = 0
    while sum(len(chunk) for chunk in chunks) < length:
        counter_bytes = counter.to_bytes(4, "big")
        chunks.append(hmac.new(secret, nonce + counter_bytes, hashlib.sha256).digest())
        counter += 1
    return b"".join(chunks)[:length]


def _xor_bytes(data: bytes, stream: bytes) -> bytes:
    return bytes(byte ^ stream[index] for index, byte in enumerate(data))


def _store_cached_license_key(license_key: str, device: dict[str, Any] | None = None, ui_brand: str | None = None) -> None:
    text = str(license_key or "").strip()
    if not text:
        return
    if not _cache_secret_material(device):
        return
    LICENSE_DIR.mkdir(parents=True, exist_ok=True)
    nonce = os.urandom(16)
    secret = _license_key_cache_key(device)
    plaintext = text.encode("utf-8")
    ciphertext = _xor_bytes(plaintext, _cache_keystream(secret, nonce, len(plaintext)))
    tag = hmac.new(secret, nonce + ciphertext, hashlib.sha256).digest()
    payload = {
        "format": "aiassistance-license-key-cache-v1",
        "nonce_b64": base64.b64encode(nonce).decode("ascii"),
        "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
        "tag_b64": base64.b64encode(tag).decode("ascii"),
        "updated_at": int(time.time()),
    }
    if ui_brand:
        payload["ui_brand"] = _normalize_ui_brand(ui_brand)
    tmp = LICENSE_KEY_CACHE_PATH.with_suffix(LICENSE_KEY_CACHE_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(LICENSE_KEY_CACHE_PATH)
    try:
        LICENSE_KEY_CACHE_PATH.chmod(0o600)
    except OSError:
        pass


def _read_cached_license_key(device: dict[str, Any] | None = None) -> str:
    if not _cache_secret_material(device):
        return ""
    payload = _read_json_object(LICENSE_KEY_CACHE_PATH)
    if payload.get("format") != "aiassistance-license-key-cache-v1":
        return ""
    try:
        nonce = base64.b64decode(str(payload.get("nonce_b64") or ""), validate=True)
        ciphertext = base64.b64decode(str(payload.get("ciphertext_b64") or ""), validate=True)
        expected_tag = base64.b64decode(str(payload.get("tag_b64") or ""), validate=True)
    except (ValueError, TypeError):
        return ""
    if not nonce or not ciphertext or not expected_tag:
        return ""
    secret = _license_key_cache_key(device)
    actual_tag = hmac.new(secret, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(actual_tag, expected_tag):
        return ""
    try:
        plaintext = _xor_bytes(ciphertext, _cache_keystream(secret, nonce, len(ciphertext)))
        return plaintext.decode("utf-8").strip()
    except UnicodeDecodeError:
        return ""


def _string_list(values: Any, *, limit: int = 16) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _device_fingerprint_values(device: dict[str, Any] | None, license_doc: dict[str, Any] | None = None) -> list[str]:
    values: list[str] = []
    for source, keys in (
        (device or {}, ("fingerprint_hash", "previous_fingerprint_hash", "legacy_fingerprint_hash", "device_fingerprint_hash")),
        (license_doc or {}, ("device_fingerprint_hash",)),
    ):
        for key in keys:
            value = str(source.get(key) or "").strip()
            if value and value not in values:
                values.append(value)
    for value in _string_list((device or {}).get("fingerprint_aliases"), limit=32):
        if value not in values:
            values.append(value)
    return values


def _write_device_recovery(device: dict[str, Any] | None = None, license_doc: dict[str, Any] | None = None) -> None:
    device = device if isinstance(device, dict) else _read_json_object(DEVICE_PATH)
    license_doc = license_doc if isinstance(license_doc, dict) else _read_json_object(LICENSE_PATH)
    fingerprints = _device_fingerprint_values(device, license_doc)
    device_id = str((device or {}).get("device_id") or (license_doc or {}).get("device_id") or "").strip()
    license_id = str((license_doc or {}).get("license_id") or "").strip()
    if not device_id and not fingerprints and not license_id:
        return
    existing = _read_json_object(DEVICE_RECOVERY_PATH)
    aliases = _string_list(existing.get("fingerprints"), limit=64)
    for value in fingerprints:
        if value not in aliases:
            aliases.append(value)
    payload = {
        "format": "aiassistance-device-recovery-v1",
        "device_id": device_id or str(existing.get("device_id") or ""),
        "license_id": license_id or str(existing.get("license_id") or ""),
        "fingerprints": aliases[:64],
        "updated_at": int(time.time()),
    }
    if isinstance(device, dict):
        binding = device.get("binding_hardware")
        if isinstance(binding, dict):
            payload["binding_hardware"] = binding
        current_binding = device.get("binding_hardware_current")
        if isinstance(current_binding, dict):
            payload["binding_hardware_current"] = current_binding
        hardware = device.get("hardware")
        if isinstance(hardware, dict):
            payload["hardware"] = hardware
    LICENSE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DEVICE_RECOVERY_PATH.with_suffix(DEVICE_RECOVERY_PATH.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(DEVICE_RECOVERY_PATH)
    try:
        DEVICE_RECOVERY_PATH.chmod(0o600)
    except OSError:
        pass


def _merge_recovery_into_device(device: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(device, dict):
        device = {}
    merged = dict(device)
    recovery = _read_json_object(DEVICE_RECOVERY_PATH)
    aliases = _device_fingerprint_values(merged)
    for value in _string_list(recovery.get("fingerprints"), limit=64):
        if value not in aliases:
            aliases.append(value)
    current_hash = str(merged.get("fingerprint_hash") or "").strip()
    alias_values = [value for value in aliases if value and value != current_hash]
    if alias_values:
        merged["fingerprint_aliases"] = alias_values[:64]
    for key in ("device_id",):
        if not str(merged.get(key) or "").strip() and str(recovery.get(key) or "").strip():
            merged[key] = str(recovery.get(key) or "").strip()
    recovery_payload = {
        "device_id": str(recovery.get("device_id") or "").strip(),
        "license_id": str(recovery.get("license_id") or "").strip(),
        "fingerprints": _string_list(recovery.get("fingerprints"), limit=64),
    }
    for key in ("binding_hardware", "binding_hardware_current", "hardware"):
        value = recovery.get(key)
        if isinstance(value, dict):
            recovery_payload[key] = value
    if any(recovery_payload.get(key) for key in ("device_id", "license_id", "fingerprints")):
        merged["recovery"] = recovery_payload
    return merged


def _with_current_version(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    enriched = dict(payload)
    fallback = str(enriched.get("version", ""))
    enriched["app_version"] = _current_app_version(fallback)
    enriched["version"] = _current_release_version(enriched["app_version"])
    enriched["auto_start"] = _auto_start_settings_payload()
    brand = _brand_payload()
    enriched["ui_brand"] = brand["ui_brand"]
    enriched["ui"] = brand
    license_state = enriched.get("license")
    if isinstance(license_state, dict):
        license_copy = dict(license_state)
        license_copy.setdefault("ui_brand", brand["ui_brand"])
        enriched["license"] = license_copy
    state_payload = enriched.get("state")
    if isinstance(state_payload, dict):
        state_copy = dict(state_payload)
        state_license = state_copy.get("license")
        if isinstance(state_license, dict):
            state_license_copy = dict(state_license)
            state_license_copy.setdefault("ui_brand", brand["ui_brand"])
            state_copy["license"] = state_license_copy
        enriched["state"] = state_copy
    return enriched


def _redact_public_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"server_base_url", "server_url", "download_url", "url"}:
                continue
            redacted[key] = _redact_public_payload(item)
        return redacted
    if isinstance(value, list):
        return [_redact_public_payload(item) for item in value]
    return value


def _api_ok_public(data: Any):
    return api_ok(_redact_public_payload(data))


def _safe_ascii(value: Any, limit: int, fallback: str = "") -> str:
    text = "".join(ch for ch in str(value or "") if 32 <= ord(ch) <= 126)[:limit]
    return text or fallback


def _parse_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return int(text, 16 if text.lower().startswith("0x") else 10)
    except ValueError:
        return None


def _hex_key(value: Any, width: int) -> str:
    number = _parse_int(value)
    if number is None or number <= 0 or number >= (1 << (width * 4)):
        return ""
    return f"{number:0{width}x}"


def _bounded_int(value: Any, minimum: int, maximum: int, fallback: int) -> int:
    number = _parse_int(value)
    if number is None or number < minimum or number > maximum:
        return fallback
    return number


def _descriptor_hex(device: dict[str, Any], hex_key: str, int_key: str, width: int, fallback: Any) -> str:
    value = device.get(hex_key, device.get(int_key))
    number = _parse_int(value)
    if number is None or number < 0 or number >= (1 << (width * 4)):
        return str(fallback or "")
    return f"0x{number:0{width}x}"


def _first_profile_configuration(profile: dict[str, Any]) -> dict[str, Any]:
    descriptors = profile.get("original_descriptors")
    if not isinstance(descriptors, dict):
        return {}
    configurations = descriptors.get("configurations")
    if not isinstance(configurations, list) or not configurations:
        return {}
    first = configurations[0]
    return first if isinstance(first, dict) else {}


def _read_small_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return ""


def _read_usb_proxy_env() -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        lines = USB_PROXY_ENV_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return env
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _usb_proxy_delay_from_env(env: dict[str, str], key: str, fallback: float) -> float:
    try:
        value = float(env.get(key, ""))
    except (TypeError, ValueError):
        return fallback
    if not 0.0 <= value <= USB_PROXY_SETTLE_DELAY_MAX_SEC:
        return fallback
    return value


def _usb_proxy_timing_from_env(env: dict[str, str] | None = None) -> dict[str, float]:
    env = env if env is not None else _read_usb_proxy_env()
    legacy_delay = _usb_proxy_delay_from_env(env, "USB_PROXY_REENUMERATE_DELAY_SEC", 1.0)
    return {
        "mouse_settle_delay_sec": _usb_proxy_delay_from_env(
            env,
            "USB_PROXY_MOUSE_SETTLE_DELAY",
            legacy_delay,
        ),
        "identity_change_settle_delay_sec": _usb_proxy_delay_from_env(
            env,
            "USB_PROXY_IDENTITY_CHANGE_SETTLE_DELAY",
            0.5,
        ),
        "max_delay_sec": USB_PROXY_SETTLE_DELAY_MAX_SEC,
    }


def _validated_usb_proxy_timing(body: Any) -> dict[str, float]:
    if not isinstance(body, dict):
        raise ValueError("request body must be an object")
    timing: dict[str, float] = {}
    for key in ("mouse_settle_delay_sec", "identity_change_settle_delay_sec"):
        raw = body.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError(f"{key} must be a number")
        value = float(raw)
        if not 0.0 <= value <= USB_PROXY_SETTLE_DELAY_MAX_SEC:
            raise ValueError(f"{key} must be between 0 and 30 seconds")
        timing[key] = round(value, 3)
    return timing


def _env_value_truthy(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _mouse_proxy_mode_from_env(env: dict[str, str] | None = None) -> str:
    env = env if env is not None else _read_usb_proxy_env()
    return "synthetic" if not _env_value_truthy(env.get("USB_PROXY_FULL_PASSTHROUGH"), True) else "full_passthrough"


def _usb_proxy_service_active() -> bool:
    try:
        return subprocess.run(
            ["systemctl", "is-active", "--quiet", "usb-proxy.service"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        ).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _usb_proxy_service_text(command: str) -> str:
    try:
        completed = subprocess.run(
            ["systemctl", command, "usb-proxy.service"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return completed.stdout.strip() or "unknown"


def _usb_interface_device_dir(iface: Path) -> Path:
    dev_name = iface.name.split(":", 1)[0]
    return iface.parent / dev_name


def _parse_sysfs_hex_int(value: str, fallback: int = 0) -> int:
    text = value.strip().lower()
    if not text:
        return fallback
    if text.startswith("0x"):
        text = text[2:]
    text = re.sub(r"[^0-9a-f]", "", text)
    if not text:
        return fallback
    try:
        return int(text, 16)
    except ValueError:
        return fallback


def _parse_sysfs_decimal_int(value: str, fallback: int = 0) -> int:
    match = re.search(r"\d+", value or "")
    if not match:
        return fallback
    try:
        return int(match.group(0), 10)
    except ValueError:
        return fallback


def _sysfs_bcd_to_hex(value: str, width: int = 4) -> str:
    text = value.strip()
    if not text:
        return ""
    if "." in text:
        major, minor = (text.split(".", 1) + [""])[:2]
        major_digits = re.sub(r"\D", "", major)[-2:].zfill(2)
        minor_digits = re.sub(r"\D", "", minor)[:2].ljust(2, "0")
        digits = major_digits + minor_digits
        return f"0x{int(digits, 16):0{width}x}"
    return f"0x{_parse_sysfs_hex_int(text):0{width}x}"


def _sysfs_hex_to_prefixed(value: str, width: int = 4) -> str:
    number = _parse_sysfs_hex_int(value)
    if number <= 0:
        return ""
    return f"0x{number:0{width}x}"


def _usb_device_is_hub(dev_dir: Path) -> bool:
    return _read_small_text(dev_dir / "bDeviceClass").lower() == "09"


def _usb_device_name(dev_dir: Path) -> str:
    return " ".join(part for part in [
        _read_small_text(dev_dir / "manufacturer"),
        _read_small_text(dev_dir / "product"),
    ] if part).strip()


def _mouse_candidate_score(iface: Path, dev_dir: Path) -> int:
    subclass = _read_small_text(iface / "bInterfaceSubClass").lower()
    protocol = _read_small_text(iface / "bInterfaceProtocol").lower()
    if subclass == "01" and protocol == "02":
        return 0
    name = _usb_device_name(dev_dir).lower()
    if any(token in name for token in ("mouse", "logitech", "g502", "g304", "g305", "gpro", "receiver", "unifying", "lightspeed")):
        return 1
    return 2


def _find_usb_mouse_interface() -> tuple[Path, Path] | None:
    candidates: list[tuple[int, str, Path, Path]] = []
    for iface in sorted(Path("/sys/bus/usb/devices").glob("*:*")):
        if _read_small_text(iface / "bInterfaceClass").lower() != "03":
            continue
        dev_dir = _usb_interface_device_dir(iface)
        if _usb_device_is_hub(dev_dir):
            continue
        vid = _read_small_text(dev_dir / "idVendor")
        pid = _read_small_text(dev_dir / "idProduct")
        if not vid or not pid:
            continue
        candidates.append((_mouse_candidate_score(iface, dev_dir), iface.name, iface, dev_dir))
    if not candidates:
        return None
    _, _, iface, dev_dir = sorted(candidates, key=lambda item: (item[0], item[1]))[0]
    return iface, dev_dir


def _best_interrupt_in_endpoint(iface: Path) -> tuple[int, int]:
    best_packet = 0
    best_interval = 0
    for ep in sorted(iface.glob("ep_*")):
        if _read_small_text(ep / "direction").lower() != "in":
            continue
        if _read_small_text(ep / "type").lower() != "interrupt":
            continue
        packet = _parse_sysfs_hex_int(_read_small_text(ep / "wMaxPacketSize"))
        interval = _parse_sysfs_hex_int(_read_small_text(ep / "bInterval"))
        if interval <= 0:
            interval = _parse_sysfs_decimal_int(_read_small_text(ep / "interval"))
        if packet > best_packet:
            best_packet = packet
            best_interval = interval
    return best_packet, best_interval


def _read_hid_report_descriptor_hex(vid: str, pid: str, iface: Path) -> str:
    for report_path in sorted(iface.rglob("report_descriptor")):
        try:
            data = report_path.read_bytes()
        except OSError:
            continue
        if data:
            return data.hex()
    vid_upper = vid.upper()
    pid_upper = pid.upper()
    for hid_dir in sorted(Path("/sys/bus/hid/devices").glob(f"*:{vid_upper}:{pid_upper}.*")):
        report_path = hid_dir / "report_descriptor"
        try:
            data = report_path.read_bytes()
        except OSError:
            continue
        if data:
            return data.hex()
    return ""


def _physical_usb_mouse_config_from_sysfs() -> tuple[dict[str, Any], dict[str, Any]] | None:
    found = _find_usb_mouse_interface()
    if found is None:
        return None
    iface, dev_dir = found
    vid_raw = _read_small_text(dev_dir / "idVendor")
    pid_raw = _read_small_text(dev_dir / "idProduct")
    packet_size, interval = _best_interrupt_in_endpoint(iface)
    product = _safe_ascii(_read_small_text(dev_dir / "product"), 64, "USB Mouse")
    manufacturer = _safe_ascii(_read_small_text(dev_dir / "manufacturer"), 48, "")
    serial = _safe_ascii(_read_small_text(dev_dir / "serial"), 64, "")
    configuration = _safe_ascii(_read_small_text(dev_dir / "configuration"), 32, "Mouse")
    vid = _sysfs_hex_to_prefixed(vid_raw, 4)
    pid = _sysfs_hex_to_prefixed(pid_raw, 4)
    config = {
        "usb_vid": vid,
        "usb_pid": pid,
        "usb_bcd_usb": _sysfs_bcd_to_hex(_read_small_text(dev_dir / "bcdUSB") or _read_small_text(dev_dir / "version"), 4) or "0x0200",
        "usb_bcd_device": _sysfs_bcd_to_hex(_read_small_text(dev_dir / "bcdDevice"), 4) or "0x0100",
        "usb_device_class": _parse_sysfs_hex_int(_read_small_text(dev_dir / "bDeviceClass")),
        "usb_device_subclass": _parse_sysfs_hex_int(_read_small_text(dev_dir / "bDeviceSubClass")),
        "usb_device_protocol": _parse_sysfs_hex_int(_read_small_text(dev_dir / "bDeviceProtocol")),
        "usb_max_power": _parse_sysfs_decimal_int(_read_small_text(dev_dir / "bMaxPower"), 0),
        "hid_protocol": _parse_sysfs_hex_int(_read_small_text(iface / "bInterfaceProtocol"), 2),
        "hid_subclass": _parse_sysfs_hex_int(_read_small_text(iface / "bInterfaceSubClass"), 1),
        "hid_report_length": packet_size or 1,
        "hid_interval": interval or 1,
        "usb_manufacturer": manufacturer,
        "usb_product": product,
        "usb_serial": serial,
        "usb_configuration": configuration,
        "hid_report_desc_hex": _read_hid_report_descriptor_hex(vid_raw, pid_raw, iface),
    }
    meta = {
        "device": dev_dir.name,
        "interface": iface.name,
        "name": _usb_device_name(dev_dir) or product,
    }
    return config, meta


def _latest_mouse_profile_for_config(config: dict[str, Any]) -> Path | None:
    vid = _hex_key(config.get("usb_vid"), 4)
    pid = _hex_key(config.get("usb_pid"), 4)
    if not vid or not pid:
        return None
    try:
        candidates = [path for path in USB_PROXY_PROFILE_DIR.glob(f"{vid}_{pid}_*.json") if path.is_file()]
        if not candidates:
            return None
        return max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name))
    except OSError:
        return None


def _full_passthrough_profile_config(config: dict[str, Any], profile_path: Path) -> dict[str, Any] | None:
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(profile, dict):
        return None

    descriptors = profile.get("original_descriptors")
    device = descriptors.get("device") if isinstance(descriptors, dict) else None
    if not isinstance(device, dict):
        return None

    strings = profile.get("strings")
    if not isinstance(strings, dict):
        strings = {}
    configuration = _first_profile_configuration(profile)

    display_config = dict(config)
    display_config.update({
        "usb_vid": _descriptor_hex(device, "idVendor_hex", "idVendor", 4, config.get("usb_vid", "")),
        "usb_pid": _descriptor_hex(device, "idProduct_hex", "idProduct", 4, config.get("usb_pid", "")),
        "usb_bcd_usb": _descriptor_hex(device, "bcdUSB_hex", "bcdUSB", 4, config.get("usb_bcd_usb", "")),
        "usb_bcd_device": _descriptor_hex(device, "bcdDevice_hex", "bcdDevice", 4, config.get("usb_bcd_device", "")),
        "usb_device_class": _bounded_int(device.get("bDeviceClass"), 0, 255, _bounded_int(config.get("usb_device_class"), 0, 255, 0)),
        "usb_device_subclass": _bounded_int(device.get("bDeviceSubClass"), 0, 255, _bounded_int(config.get("usb_device_subclass"), 0, 255, 0)),
        "usb_device_protocol": _bounded_int(device.get("bDeviceProtocol"), 0, 255, _bounded_int(config.get("usb_device_protocol"), 0, 255, 0)),
        "usb_max_power": _bounded_int(configuration.get("MaxPower"), 0, 500, _bounded_int(config.get("usb_max_power"), 0, 500, 0)),
        "usb_manufacturer": _safe_ascii(strings.get("manufacturer"), 48, str(config.get("usb_manufacturer", ""))),
        "usb_product": _safe_ascii(strings.get("product"), 64, str(config.get("usb_product", ""))),
        "usb_serial": _safe_ascii(strings.get("serial"), 64, str(config.get("usb_serial", ""))),
    })

    display_config["usb_configuration"] = _safe_ascii(
        strings.get("configuration") or strings.get("config"),
        32,
        str(config.get("usb_configuration", "")),
    )
    return display_config


def _enrich_mouse_hardware_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    enriched = dict(payload)
    enriched["timing"] = _usb_proxy_timing_from_env()
    if payload.get("mode") != "full_passthrough":
        return enriched
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    source = ""
    physical = _physical_usb_mouse_config_from_sysfs()
    if physical is not None:
        config, meta = physical
        source = "sysfs_usb_mouse"
    else:
        meta = {}
    profile_path = _latest_mouse_profile_for_config(config)
    display_config = config
    if profile_path is not None:
        display_config = _full_passthrough_profile_config(config, profile_path) or config
        source = "original_mouse_profile"
    enriched["config"] = display_config
    enriched["connected"] = bool(display_config.get("usb_vid") and display_config.get("usb_pid"))
    if source:
        enriched["config_source"] = source
    if profile_path is not None:
        enriched["config_profile"] = profile_path.name
    if meta:
        enriched["physical_mouse"] = meta
    return enriched


def _full_passthrough_mouse_fallback_payload(error: str | None = None) -> dict[str, Any] | None:
    env = _read_usb_proxy_env()
    if _mouse_proxy_mode_from_env(env) != "full_passthrough":
        return None
    service_active_text = _usb_proxy_service_text("is-active")
    service_enabled_text = _usb_proxy_service_text("is-enabled")
    payload: dict[str, Any] = {
        "connected": False,
        "config": {},
        "mode": "full_passthrough",
        "service_active": service_active_text == "active" or _usb_proxy_service_active(),
        "service_enabled": service_enabled_text == "enabled",
        "service_active_text": service_active_text,
        "service_enabled_text": service_enabled_text,
        "set_config_supported": False,
    }
    if error:
        payload["daemon_error"] = error
    return _enrich_mouse_hardware_payload(payload)


USB_DIAGNOSTIC_DMESG_PATTERN = re.compile(
    r"(usb|dwc3|udc|gadget|raw-gadget|raw_gadget|typec|tcpm|ep0|fc[0-9a-f]+\.usb|usbdrd)",
    re.IGNORECASE,
)


def _decode_limited_bytes(data: bytes | None, max_bytes: int) -> str:
    if not data:
        return ""
    truncated = len(data) > max_bytes
    if truncated:
        data = data[:max_bytes]
    text = data.decode("utf-8", errors="replace").replace("\x00", "\n")
    if truncated:
        text += f"\n\n[truncated to {max_bytes} bytes]\n"
    return text


def _diagnostic_command_text(command: list[str], timeout: int | None = None, max_bytes: int | None = None) -> str:
    timeout = timeout or USB_DIAGNOSTIC_COMMAND_TIMEOUT_SEC
    max_bytes = max_bytes or USB_DIAGNOSTIC_MAX_COMMAND_BYTES
    command_label = " ".join(command)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        elapsed = time.monotonic() - started
        output = _decode_limited_bytes(completed.stdout, max_bytes)
        return f"$ {command_label}\nexit_code={completed.returncode}\nelapsed_sec={elapsed:.2f}\n\n{output}"
    except FileNotFoundError as exc:
        elapsed = time.monotonic() - started
        return f"$ {command_label}\nstatus=not_found\nelapsed_sec={elapsed:.2f}\n\n{exc}\n"
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - started
        output = _decode_limited_bytes(getattr(exc, "output", b"") or b"", max_bytes)
        return f"$ {command_label}\nstatus=timeout\ntimeout_sec={timeout}\nelapsed_sec={elapsed:.2f}\n\n{output}"
    except OSError as exc:
        elapsed = time.monotonic() - started
        return f"$ {command_label}\nstatus=os_error\nelapsed_sec={elapsed:.2f}\n\n{exc}\n"


def _filtered_usb_dmesg_text() -> str:
    raw = _diagnostic_command_text(["dmesg", "-T"], timeout=8, max_bytes=1024 * 1024)
    header: list[str] = []
    matches: list[str] = []
    for line in raw.splitlines():
        if line.startswith(("$ ", "exit_code=", "status=", "timeout_sec=", "elapsed_sec=", "[truncated ")):
            header.append(line)
        elif USB_DIAGNOSTIC_DMESG_PATTERN.search(line):
            matches.append(line)
    if not matches:
        matches.append("[no matching USB/DWC3/UDC dmesg lines]")
    return "\n".join([
        *header,
        "filter=usb|dwc3|udc|gadget|raw-gadget|typec|tcpm|ep0|fc*.usb|usbdrd",
        "",
        *matches,
        "",
    ])


def _read_limited_file(path: Path, max_bytes: int = USB_DIAGNOSTIC_MAX_COMMAND_BYTES) -> str:
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return f"{path}: missing\n"
    except OSError as exc:
        return f"{path}: read failed: {exc}\n"
    return _decode_limited_bytes(data, max_bytes)


def _read_sysfs_value(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8", errors="replace").replace("\x00", "\n").strip()
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        return f"read failed: {exc}"


def _safe_hailo_part(value: Any, fallback: str = "") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or fallback or "").strip()).strip("-")
    return text or fallback


def _board_model_name() -> str:
    for path in (
        Path("/proc/device-tree/model"),
        Path("/sys/firmware/devicetree/base/model"),
    ):
        try:
            value = path.read_bytes().decode("utf-8", errors="replace").replace("\x00", " ").strip()
        except (FileNotFoundError, OSError):
            continue
        if value:
            return value
    return ""


def _hailo_board_id(model_name: str | None = None) -> str:
    override = os.environ.get("AIASSISTANCE_HAILO_BOARD_ID", "").strip()
    if override:
        return _safe_hailo_part(override)
    model = (model_name if model_name is not None else _board_model_name()).lower()
    if "nanopc-t6" in model or "nanopc t6" in model:
        return "nanopc-t6"
    if "orange pi 5 plus" in model or "orangepi 5 plus" in model:
        return "orangepi"
    return ""


def _link_target(path: Path) -> str:
    try:
        return os.path.realpath(path)
    except OSError as exc:
        return f"resolve failed: {exc}"


def _zip_text(archive: zipfile.ZipFile, name: str, text: str) -> None:
    if not text.endswith("\n"):
        text += "\n"
    archive.writestr(name, text)


def _usb_diagnostic_summary() -> str:
    lines = [
        f"generated_at={time.strftime('%Y-%m-%dT%H:%M:%S%z')}",
        f"hostname={socket.gethostname()}",
        f"pid={os.getpid()}",
        f"euid={os.geteuid() if hasattr(os, 'geteuid') else 'unknown'}",
        f"root_dir={ROOT_DIR}",
        f"socket_path={SOCKET_PATH}",
        f"usb_proxy_profile_dir={USB_PROXY_PROFILE_DIR}",
        "",
        "[/proc/device-tree/model]",
        _read_limited_file(Path("/proc/device-tree/model"), 16 * 1024).strip(),
        "",
        "[/etc/os-release]",
        _read_limited_file(Path("/etc/os-release"), 16 * 1024).strip(),
        "",
        "[/proc/cmdline]",
        _read_limited_file(Path("/proc/cmdline"), 16 * 1024).strip(),
        "",
        "[/proc/uptime]",
        _read_limited_file(Path("/proc/uptime"), 16 * 1024).strip(),
    ]
    return "\n".join(lines) + "\n"


def _udc_sysfs_snapshot() -> str:
    root = Path("/sys/class/udc")
    lines = [f"root={root}"]
    if not root.exists():
        return "\n".join([*lines, "status=missing", ""])

    try:
        udcs = sorted([path for path in root.iterdir() if path.exists()], key=lambda path: path.name)
    except OSError as exc:
        return "\n".join([*lines, f"status=list failed: {exc}", ""])

    if not udcs:
        lines.append("status=no UDC entries")

    for udc in udcs:
        lines.extend(["", f"[{udc.name}]"])
        for name in ("state", "current_speed", "maximum_speed", "is_a_peripheral", "is_otg", "soft_connect"):
            path = udc / name
            if path.exists():
                lines.append(f"{name}={_read_sysfs_value(path)}")
        for link_name in ("device", "device/driver"):
            link = udc / link_name
            if link.exists():
                lines.append(f"{link_name}_realpath={_link_target(link)}")
        uevent = udc / "uevent"
        if uevent.exists():
            lines.extend(["uevent<<", _read_limited_file(uevent, 32 * 1024).strip(), ">>"])
    return "\n".join(lines) + "\n"


def _debug_usb_mode_snapshot() -> str:
    root = Path("/sys/kernel/debug/usb")
    lines = [f"root={root}"]
    if not root.exists():
        return "\n".join([*lines, "status=missing or debugfs not mounted", ""])

    try:
        mode_paths = sorted(root.glob("*/mode"), key=lambda path: str(path))
    except OSError as exc:
        return "\n".join([*lines, f"status=list failed: {exc}", ""])

    if not mode_paths:
        lines.append("status=no mode files")
    for mode_path in mode_paths:
        lines.append(f"{mode_path}={_read_sysfs_value(mode_path)}")
    return "\n".join(lines) + "\n"


def _configfs_usb_gadget_snapshot() -> str:
    root = Path("/sys/kernel/config/usb_gadget")
    lines = [f"root={root}"]
    if not root.exists():
        return "\n".join([*lines, "status=missing or configfs not mounted", ""])

    try:
        gadgets = sorted([path for path in root.iterdir() if path.is_dir()], key=lambda path: path.name)
    except OSError as exc:
        return "\n".join([*lines, f"status=list failed: {exc}", ""])

    if not gadgets:
        lines.append("status=no gadgets")

    for gadget in gadgets:
        lines.extend(["", f"[{gadget.name}]"])
        for name in ("UDC", "idVendor", "idProduct", "bcdUSB", "bcdDevice", "bDeviceClass", "bDeviceSubClass", "bDeviceProtocol"):
            path = gadget / name
            if path.exists():
                lines.append(f"{name}={_read_sysfs_value(path)}")

        strings_dir = gadget / "strings" / "0x409"
        if strings_dir.exists():
            try:
                string_paths = sorted(strings_dir.iterdir(), key=lambda item: item.name)
            except OSError as exc:
                lines.append(f"strings/0x409=list failed: {exc}")
                string_paths = []
            for path in string_paths:
                if path.is_file():
                    lines.append(f"strings/0x409/{path.name}={_read_sysfs_value(path)}")

        configs_dir = gadget / "configs"
        if configs_dir.exists():
            try:
                config_dirs = sorted(configs_dir.iterdir(), key=lambda item: item.name)
            except OSError as exc:
                lines.append(f"configs=list failed: {exc}")
                config_dirs = []
            for config_dir in config_dirs:
                if not config_dir.is_dir():
                    continue
                lines.append(f"config={config_dir.name}")
                for name in ("MaxPower", "bmAttributes"):
                    path = config_dir / name
                    if path.exists():
                        lines.append(f"  {name}={_read_sysfs_value(path)}")
                config_strings = config_dir / "strings" / "0x409"
                if config_strings.exists():
                    try:
                        config_string_paths = sorted(config_strings.iterdir(), key=lambda item: item.name)
                    except OSError as exc:
                        lines.append(f"  strings/0x409=list failed: {exc}")
                        config_string_paths = []
                    for path in config_string_paths:
                        if path.is_file():
                            lines.append(f"  strings/0x409/{path.name}={_read_sysfs_value(path)}")
    return "\n".join(lines) + "\n"


def _add_recent_usb_profiles(archive: zipfile.ZipFile) -> None:
    profile_root = USB_PROXY_PROFILE_DIR
    index_lines = [f"root={profile_root}", f"max_files={USB_DIAGNOSTIC_MAX_PROFILE_FILES}"]
    if not profile_root.exists():
        _zip_text(archive, "profiles/_index.txt", "\n".join([*index_lines, "status=missing", ""]))
        return

    files: list[tuple[float, Path]] = []
    try:
        for path in profile_root.rglob("*"):
            try:
                if path.is_file() and not path.is_symlink():
                    files.append((path.stat().st_mtime, path))
            except OSError:
                continue
    except OSError as exc:
        _zip_text(archive, "profiles/_index.txt", "\n".join([*index_lines, f"status=list failed: {exc}", ""]))
        return

    files.sort(key=lambda item: (item[0], item[1].name), reverse=True)
    selected = files[:USB_DIAGNOSTIC_MAX_PROFILE_FILES]
    if not selected:
        _zip_text(archive, "profiles/_index.txt", "\n".join([*index_lines, "status=no files", ""]))
        return

    for mtime, path in selected:
        try:
            rel_path = path.relative_to(profile_root)
        except ValueError:
            rel_path = Path(path.name)
        zip_name = PurePosixPath("profiles", *rel_path.parts).as_posix()
        index_lines.append(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z', time.localtime(mtime))} {rel_path}")
        _zip_text(archive, zip_name, _read_limited_file(path, USB_DIAGNOSTIC_MAX_PROFILE_BYTES))
    _zip_text(archive, "profiles/_index.txt", "\n".join(index_lines) + "\n")


def _diagnostic_file_sha256(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        return f"unavailable: {exc}"


def _usb_proxy_runtime_snapshot() -> str:
    activation = _read_json_object(USB_PROXY_ACTIVATION_PATH)
    installed = _read_json_object(LAST_INSTALLED_PATH)
    lines = [
        f"app_version={_current_app_version()}",
        f"installed_release_version={installed.get('version', '')}",
        f"installed_app_version={installed.get('app_version', '')}",
        f"usb_proxy_version={activation.get('version', '')}",
        f"usb_proxy_format={activation.get('format', 'aiusbproxy1') if activation else ''}",
    ]
    specs = [
        (
            "supervisor",
            ROOT_DIR / "assets" / "usb-proxy" / "board" / "run_usb_proxy.sh",
            USB_PROXY_ROOT / "board" / "run_usb_proxy.sh",
        ),
        (
            "finder",
            ROOT_DIR / "assets" / "usb-proxy" / "board" / "find_usb_mouse.sh",
            USB_PROXY_ROOT / "board" / "find_usb_mouse.sh",
        ),
        (
            "preflight",
            ROOT_DIR / "assets" / "usb-proxy" / "board" / "prepare_usb_proxy.sh",
            USB_PROXY_ROOT / "board" / "prepare_usb_proxy.sh",
        ),
        (
            "board_helper",
            ROOT_DIR / "assets" / "usb-proxy" / "board" / "usb_proxy_board.sh",
            USB_PROXY_ROOT / "board" / "usb_proxy_board.sh",
        ),
        (
            "udc_checker",
            ROOT_DIR / "assets" / "usb-proxy" / "board" / "wait_udc_attached.sh",
            USB_PROXY_ROOT / "board" / "wait_udc_attached.sh",
        ),
        (
            "control_ready",
            ROOT_DIR / "assets" / "usb-proxy" / "board" / "wait_mouse_control_ready.sh",
            USB_PROXY_ROOT / "board" / "wait_mouse_control_ready.sh",
        ),
        (
            "loader",
            ROOT_DIR / "assets" / "usb-proxy" / "bin" / "usb-proxy-loader",
            USB_PROXY_ROOT / "bin" / "usb-proxy-loader",
        ),
        (
            "synthetic_entry",
            ROOT_DIR / "assets" / "usb-proxy" / "bin" / "usb-proxy-synthetic",
            USB_PROXY_ROOT / "bin" / "usb-proxy-synthetic",
        ),
    ]
    for label, source, target in specs:
        source_sha = _diagnostic_file_sha256(source)
        target_sha = _diagnostic_file_sha256(target)
        try:
            target_mode = f"{target.stat().st_mode & 0o777:03o}"
        except OSError:
            target_mode = "unavailable"
        lines.extend([
            "",
            f"[{label}]",
            f"source={source}",
            f"target={target}",
            f"source_sha256={source_sha}",
            f"target_sha256={target_sha}",
            f"target_mode={target_mode}",
            f"matches={source_sha == target_sha and target_mode == '755'}",
        ])

    expected_enc_sha = str(activation.get("sha256") or "").strip().lower()
    actual_enc_sha = _diagnostic_file_sha256(USB_PROXY_ENC_PATH)
    lines.extend([
        "",
        "[encrypted_payload]",
        f"path={USB_PROXY_ENC_PATH}",
        f"expected_sha256={expected_enc_sha}",
        f"actual_sha256={actual_enc_sha}",
        f"matches={bool(expected_enc_sha) and expected_enc_sha == actual_enc_sha}",
    ])
    return "\n".join(lines) + "\n"


def _build_usb_proxy_diagnostics_zip() -> tuple[io.BytesIO, str]:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    safe_host = re.sub(r"[^A-Za-z0-9_.-]+", "-", socket.gethostname()).strip("-") or "orangepi"
    filename = f"usb-proxy-diagnostics-{safe_host}-{timestamp}.zip"
    buffer = io.BytesIO()

    commands: list[tuple[str, list[str], int | None, int | None]] = [
        ("commands/uname.txt", ["uname", "-a"], None, None),
        ("commands/systemctl-active.txt", ["systemctl", "is-active", "usb-proxy.service", "aiassistance-daemon.service", "aiassistance-web.service"], None, None),
        ("commands/systemctl-usb-proxy.txt", ["systemctl", "status", "usb-proxy.service", "--no-pager", "-l"], 10, None),
        ("commands/systemctl-aiassistance.txt", ["systemctl", "status", "aiassistance-daemon.service", "aiassistance-web.service", "--no-pager", "-l"], 10, None),
        ("commands/systemctl-cat-usb-proxy.txt", ["systemctl", "cat", "usb-proxy.service", "--no-pager"], None, None),
        ("commands/journal-usb-proxy.txt", ["journalctl", "-u", "usb-proxy.service", "-n", "300", "--no-pager", "-o", "short-iso"], 10, 512 * 1024),
        ("commands/journal-aiassistance.txt", ["journalctl", "-u", "aiassistance-daemon.service", "-u", "aiassistance-web.service", "-n", "200", "--no-pager", "-o", "short-iso"], 10, 384 * 1024),
        ("commands/lsusb.txt", ["lsusb"], None, None),
        ("commands/lsusb-tree.txt", ["lsusb", "-t"], None, None),
        ("commands/findmnt-debugfs.txt", ["findmnt", "/sys/kernel/debug"], None, None),
        ("commands/findmnt-configfs.txt", ["findmnt", "/sys/kernel/config"], None, None),
        ("commands/ip-addr.txt", ["ip", "addr"], None, None),
    ]

    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        _zip_text(archive, "summary.txt", _usb_diagnostic_summary())
        for name, command, timeout, max_bytes in commands:
            _zip_text(archive, name, _diagnostic_command_text(command, timeout=timeout, max_bytes=max_bytes))
        _zip_text(archive, "commands/dmesg-usb.txt", _filtered_usb_dmesg_text())
        _zip_text(archive, "sysfs/udc.txt", _udc_sysfs_snapshot())
        _zip_text(archive, "sysfs/debug-usb-mode.txt", _debug_usb_mode_snapshot())
        _zip_text(archive, "sysfs/configfs-usb-gadgets.txt", _configfs_usb_gadget_snapshot())
        _zip_text(archive, "etc/default-usb-proxy.txt", _read_limited_file(Path("/etc/default/usb-proxy"), 128 * 1024))
        _zip_text(archive, "runtime/verification.txt", _usb_proxy_runtime_snapshot())
        _zip_text(archive, "runtime/last-startup-check.txt", _read_limited_file(USB_PROXY_RUNTIME_STATUS_PATH, 128 * 1024))
        _add_recent_usb_profiles(archive)

    buffer.seek(0)
    return buffer, filename


def _write_last_update_check(payload: Any) -> None:
    if not isinstance(payload, dict):
        return
    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    LAST_UPDATE_CHECK_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _update_status_payload(
    *,
    status: str,
    stage: str,
    message: str,
    progress: int,
    version: str = "",
    package_type: str = "",
    unit: str = "",
    error: str = "",
    started_at: int | None = None,
    completed_at: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = int(time.time())
    payload: dict[str, Any] = {
        "status": status,
        "stage": stage,
        "message": message,
        "progress": max(0, min(100, int(progress))),
        "version": str(version or ""),
        "type": str(package_type or ""),
        "unit": str(unit or ""),
        "error": str(error or ""),
        "started_at": started_at if started_at is not None else now,
        "updated_at": now,
    }
    if completed_at is not None:
        payload["completed_at"] = completed_at
    if extra:
        payload.update(extra)
    return payload


def _write_update_status(payload: dict[str, Any]) -> dict[str, Any]:
    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    with _update_status_lock:
        tmp = UPDATE_STATUS_PATH.with_suffix(UPDATE_STATUS_PATH.suffix + f".tmp-{os.getpid()}")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(UPDATE_STATUS_PATH)
    return payload


def _read_update_status() -> dict[str, Any]:
    try:
        payload = json.loads(UPDATE_STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _update_status_payload(
            status="idle",
            stage="idle",
            message="暂无更新任务",
            progress=0,
        )
    if not isinstance(payload, dict):
        return _update_status_payload(
            status="idle",
            stage="idle",
            message="暂无更新任务",
            progress=0,
        )
    return payload


def _running_update_units() -> list[str]:
    if not shutil.which("systemctl"):
        return []
    completed = subprocess.run(
        [
            "systemctl",
            "list-units",
            "aiassistance-update-*",
            "--all",
            "--no-legend",
            "--no-pager",
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=6,
    )
    if completed.returncode not in (0, 1):
        details = (completed.stderr or completed.stdout or "").strip()
        raise DaemonError(details or "failed to inspect update services")
    units: list[str] = []
    for raw_line in completed.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(None, 4)
        unit = parts[0] if parts else ""
        active_state = parts[2] if len(parts) > 2 else ""
        sub_state = parts[3] if len(parts) > 3 else ""
        if (
            unit.startswith("aiassistance-update-")
            and unit.endswith((".service", ".timer"))
            and active_state in {"active", "activating", "reloading"}
            and sub_state not in {"dead", "failed", "exited"}
        ):
            units.append(unit)
    return units


def _cleanup_stuck_update_status() -> dict[str, Any]:
    running_units = _running_update_units()
    if running_units:
        raise DaemonError("发现更新安装任务仍在运行，请等待任务结束后再清理")

    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    previous_status = _read_update_status()
    backup_path = ""
    deleted: list[str] = []
    timestamp = time.strftime("%Y%m%d-%H%M%S")

    with _update_status_lock:
        if UPDATE_STATUS_PATH.exists():
            backup = UPDATES_DIR / f"update-status.stuck.{timestamp}.json"
            shutil.copy2(UPDATE_STATUS_PATH, backup)
            backup_path = str(backup)
            UPDATE_STATUS_PATH.unlink(missing_ok=True)
        patterns = (
            "aiassistance-*.tar.gz",
            "aiassistance-*.tar.zst",
            "update-metadata-*.json",
            "*.tmp-*",
        )
        for path in sorted(UPDATES_DIR.iterdir()):
            if not path.is_file():
                continue
            if not any(path.match(pattern) for pattern in patterns):
                continue
            try:
                path.unlink()
                deleted.append(str(path))
            except OSError as exc:
                raise DaemonError(f"failed to delete {path}: {exc}") from exc

    return {
        "cleaned": True,
        "backup": backup_path,
        "deleted": deleted,
        "previous_status": previous_status,
        "status": _read_update_status(),
    }


def _hailo_install_status_payload(
    *,
    status: str,
    stage: str,
    message: str,
    progress: int,
    error: str = "",
    log: list[str] | None = None,
    started_at: int | None = None,
    completed_at: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = int(time.time())
    payload: dict[str, Any] = {
        "status": status,
        "stage": stage,
        "message": message,
        "progress": max(0, min(100, int(progress))),
        "error": str(error or ""),
        "log": list(log or [])[-400:],
        "started_at": started_at if started_at is not None else now,
        "updated_at": now,
    }
    if completed_at is not None:
        payload["completed_at"] = completed_at
    if extra:
        payload.update(extra)
    return payload


def _write_hailo_install_status(payload: dict[str, Any]) -> dict[str, Any]:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    tmp = HAILO_STATUS_PATH.with_suffix(HAILO_STATUS_PATH.suffix + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(HAILO_STATUS_PATH)
    return payload


def _read_hailo_install_status() -> dict[str, Any]:
    try:
        payload = json.loads(HAILO_STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _hailo_install_status_payload(
            status="idle",
            stage="idle",
            message="暂无安装任务",
            progress=0,
        )
    if not isinstance(payload, dict):
        return _hailo_install_status_payload(
            status="idle",
            stage="idle",
            message="暂无安装任务",
            progress=0,
        )
    if payload.get("status") == "running":
        thread = _hailo_install_thread
        has_live_thread = thread is not None and thread.is_alive()
        try:
            updated_at = int(payload.get("updated_at") or 0)
        except (TypeError, ValueError):
            updated_at = 0
        stale = updated_at <= 0 or int(time.time()) - updated_at >= HAILO_INSTALL_STALE_SEC
        if stale and not has_live_thread:
            failed_payload = _hailo_install_status_payload(
                status="failed",
                stage=str(payload.get("stage") or "interrupted"),
                message="Hailo 安装任务已中断，请重试",
                progress=int(payload.get("progress") or 0),
                error="安装进程已退出或 Web 服务曾重启",
                log=payload.get("log") if isinstance(payload.get("log"), list) else [],
                started_at=int(payload.get("started_at") or time.time()),
                completed_at=int(time.time()),
            )
            _write_hailo_install_status(failed_payload)
            return failed_payload
    return payload


def _uname_release() -> str:
    try:
        return subprocess.check_output(["uname", "-r"], text=True, timeout=2).strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _run_status_command(command: list[str], timeout: float = 5.0) -> dict[str, Any]:
    try:
        completed = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "exit_code": -1, "output": str(exc)}
    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part and part.strip())
    return {
        "ok": completed.returncode == 0,
        "exit_code": completed.returncode,
        "output": output[-4000:],
    }


def _hailo_pcie_devices() -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    for vendor_path in sorted(Path("/sys/bus/pci/devices").glob("*/vendor")):
        vendor = _read_sysfs_value(vendor_path).strip().lower()
        if vendor != "0x1e60":
            continue
        device_dir = vendor_path.parent
        devices.append({
            "pci_address": device_dir.name,
            "vendor": vendor,
            "device": _read_sysfs_value(device_dir / "device").strip().lower(),
            "class": _read_sysfs_value(device_dir / "class").strip().lower(),
            "driver": device_dir.joinpath("driver").resolve().name if (device_dir / "driver").exists() else "",
        })
    return devices


def _hailo_cli_path() -> str:
    return shutil.which("hailortcli") or ("/usr/local/bin/hailortcli" if Path("/usr/local/bin/hailortcli").exists() else "")


def _hailo_status() -> dict[str, Any]:
    pcie_devices = _hailo_pcie_devices()
    cli_path = _hailo_cli_path()
    board_model = _board_model_name()
    board_id = _hailo_board_id(board_model)
    version_result = _run_status_command([cli_path, "--version"], timeout=4) if cli_path else {
        "ok": False,
        "exit_code": -1,
        "output": "hailortcli not found",
    }
    scan_result = _run_status_command([cli_path, "scan"], timeout=8) if cli_path else {
        "ok": False,
        "exit_code": -1,
        "output": "hailortcli not found",
    }
    dev_nodes = sorted(str(path) for path in Path("/dev").glob("hailo*"))
    lib_candidates = [
        Path("/usr/local/lib/libhailort.so.4"),
        Path("/usr/local/lib/libhailort.so"),
        Path(f"/opt/hailort-{HAILO_EXPECTED_VERSION}/lib/libhailort.so.{HAILO_EXPECTED_VERSION}"),
    ]
    runtime_installed = bool(cli_path) and any(path.exists() for path in lib_candidates)
    driver_loaded = Path("/sys/module/hailo_pci").exists()
    ready = bool(pcie_devices) and driver_loaded and runtime_installed and bool(dev_nodes) and bool(scan_result.get("ok"))
    return {
        "board_id": board_id,
        "board_model": board_model,
        "pcie": {
            "present": bool(pcie_devices),
            "devices": pcie_devices,
        },
        "kernel_release": _uname_release(),
        "driver": {
            "loaded": driver_loaded,
            "module_path": str(Path("/sys/module/hailo_pci")) if driver_loaded else "",
        },
        "runtime": {
            "installed": runtime_installed,
            "expected_version": HAILO_EXPECTED_VERSION,
            "hailortcli": cli_path,
            "version": version_result,
        },
        "device": {
            "nodes": dev_nodes,
            "scan": scan_result,
        },
        "install": _read_hailo_install_status(),
        "ready": ready,
    }


def _load_cached_update_check() -> dict[str, Any]:
    try:
        cached = json.loads(LAST_UPDATE_CHECK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DaemonError("please check update before installing") from exc
    if not isinstance(cached, dict):
        raise DaemonError("cached update check is invalid")
    return cached


def _resolve_update_plan(requested: Any) -> dict[str, Any]:
    if isinstance(requested, dict) and requested.get("url"):
        return {"update_available": True, "latest_version": requested.get("version", ""), "package": dict(requested)}
    cached = _load_cached_update_check()
    if not cached.get("update_available"):
        raise DaemonError("cached update check does not contain an available update")
    if isinstance(requested, dict) and requested.get("prefer_full"):
        cached_package = cached.get("package") if isinstance(cached.get("package"), dict) else {}
        if not cached_package.get("url"):
            raise DaemonError("full update package is not available")
    if isinstance(requested, dict):
        requested_package = requested.get("package") if isinstance(requested.get("package"), dict) else requested
        cached_package = cached.get("package") if isinstance(cached.get("package"), dict) else {}
        for field in ("version", "type", "sha256"):
            requested_value = str(requested_package.get(field, "")).strip()
            cached_value = str(cached_package.get(field, "")).strip()
            if requested_value and cached_value and requested_value != cached_value:
                raise DaemonError("cached update package does not match the selected update")
    return cached


def _resolve_update_package(requested: Any) -> dict[str, Any]:
    if not isinstance(requested, dict):
        raise DaemonError("package is required")
    if requested.get("url"):
        return dict(requested)
    cached = _load_cached_update_check()
    cached_package = cached.get("package") if isinstance(cached, dict) else None
    if not isinstance(cached_package, dict) or not cached_package.get("url"):
        raise DaemonError("cached update package is invalid")
    for field in ("version", "type", "sha256"):
        requested_value = str(requested.get(field, "")).strip()
        cached_value = str(cached_package.get(field, "")).strip()
        if requested_value and cached_value and requested_value != cached_value:
            raise DaemonError("cached update package does not match the selected update")
    return dict(cached_package)


def _update_server_request_payload(license_payload: dict[str, Any], body: dict[str, Any] | None = None) -> dict[str, Any]:
    body = body or {}
    license_state = license_payload.get("license") or {}
    if not license_state.get("valid"):
        raise DaemonError("device is not activated")
    usb_proxy_state = _current_usb_proxy_state(license_payload)
    payload: dict[str, Any] = {
        "version": license_payload.get("version", ""),
        "app_version": license_payload.get("app_version") or _current_app_version(str(license_payload.get("version") or "")),
        "components": {
            "core": {"version": _current_core_version(license_payload)},
            "usb_proxy": usb_proxy_state,
        },
        "component_capabilities": {
            "usb_proxy_formats": ["aiusbproxy1", "aiusbproxy2"],
        },
        "themes": theme_manager.installed_for_update() if _current_ui_brand() == "yu" else {"active_theme_id": "default", "installed": []},
        "license": license_state,
        "device": license_payload.get("device") if isinstance(license_payload.get("device"), dict) else _read_json_object(DEVICE_PATH),
        "device_id": license_state.get("device_id", ""),
        "device_fingerprint_hash": license_state.get("device_fingerprint_hash", ""),
        "model_key": _local_model_key_package(),
        "channel": body.get("channel", "stable"),
        "prefer_full": bool(body.get("prefer_full") or body.get("prefer_full_package")),
    }
    target_version = str(body.get("target_version") or body.get("requested_version") or "").strip()
    if target_version:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", target_version):
            raise DaemonError("target_version is invalid")
        _xcsh_reject_old_update_version(target_version)
        payload["target_version"] = target_version
    return payload


def _read_cpu_sample() -> tuple[int, int] | None:
    try:
        fields = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()
    except (OSError, IndexError):
        return None
    if not fields or fields[0] != "cpu":
        return None
    values = [int(value) for value in fields[1:] if value.isdigit()]
    if len(values) < 4:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return idle, sum(values)


def _cpu_percent() -> float | None:
    global _cpu_last_sample
    sample = _read_cpu_sample()
    if sample is None:
        return None
    with _cpu_sample_lock:
        previous = _cpu_last_sample
        _cpu_last_sample = sample
    if previous is None:
        return 0.0
    idle_delta = sample[0] - previous[0]
    total_delta = sample[1] - previous[1]
    if total_delta <= 0:
        return 0.0
    return max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0))


def _memory_status() -> dict[str, float | int | None]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        return {"total": None, "used": None, "available": None, "percent": None}
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if not total or available is None:
        return {"total": total, "used": None, "available": available, "percent": None}
    used = max(0, total - available)
    return {
        "total": total,
        "used": used,
        "available": available,
        "percent": used / total * 100.0,
    }


def _cpu_temperature() -> dict[str, float | str | None]:
    readings: list[tuple[float, str]] = []
    for temp_path in sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp")):
        try:
            raw = temp_path.read_text(encoding="utf-8").strip()
            value = float(raw)
            celsius = value / 1000.0 if value > 1000 else value
            label_path = temp_path.with_name("type")
            label = label_path.read_text(encoding="utf-8").strip() if label_path.exists() else temp_path.parent.name
        except (OSError, ValueError):
            continue
        if -40.0 <= celsius <= 140.0:
            readings.append((celsius, label))
    if not readings:
        return {"celsius": None, "label": None}
    celsius, label = max(readings, key=lambda item: item[0])
    return {"celsius": celsius, "label": label}


def _rootfs_expand_status(action: str = "status", *, force: bool = False) -> dict[str, Any]:
    global _rootfs_status_cache
    if action not in {"status", "apply"}:
        raise DaemonError("unsupported storage action")
    if action == "status" and not force:
        with _rootfs_status_lock:
            if _rootfs_status_cache is not None:
                cached_at, cached_payload = _rootfs_status_cache
                if time.monotonic() - cached_at <= ROOTFS_EXPAND_STATUS_CACHE_SEC:
                    return dict(cached_payload)
    if not ROOTFS_EXPAND_SCRIPT.exists():
        payload: dict[str, Any] = {
            "ok": False,
            "supported": False,
            "expandable": False,
            "reason": "script_missing",
            "message": "扩容脚本未安装，请先更新完整系统包",
        }
        if action == "apply":
            raise ScriptError(payload["message"], payload)
        return payload
    timeout = ROOTFS_EXPAND_APPLY_TIMEOUT_SEC if action == "apply" else ROOTFS_EXPAND_STATUS_TIMEOUT_SEC
    try:
        completed = subprocess.run(
            [str(ROOTFS_EXPAND_SCRIPT), action],
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        payload = {
            "ok": False,
            "supported": False,
            "expandable": False,
            "reason": "timeout",
            "message": "扩容检测或执行超时，请稍后刷新状态",
        }
        if action == "apply":
            raise ScriptError(payload["message"], payload) from exc
        return payload
    except OSError as exc:
        payload = {
            "ok": False,
            "supported": False,
            "expandable": False,
            "reason": "run_failed",
            "message": f"扩容脚本无法运行：{exc}",
        }
        if action == "apply":
            raise ScriptError(payload["message"], payload) from exc
        return payload

    raw = (completed.stdout or "").strip()
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        details = (completed.stderr or completed.stdout or "").strip()[-1600:]
        payload = {
            "ok": False,
            "supported": False,
            "expandable": False,
            "reason": "invalid_output",
            "message": details or "扩容脚本返回了无法解析的结果",
        }
        if action == "apply":
            raise ScriptError(payload["message"], payload) from exc
        return payload
    if not isinstance(payload, dict):
        payload = {
            "ok": False,
            "supported": False,
            "expandable": False,
            "reason": "invalid_output",
            "message": "扩容脚本返回结果格式错误",
        }
    if completed.returncode != 0 and action == "apply":
        raise ScriptError(str(payload.get("message") or "扩容失败"), payload)
    if action == "status":
        with _rootfs_status_lock:
            _rootfs_status_cache = (time.monotonic(), dict(payload))
    return payload


def _storage_status(*, force_rootfs: bool = False) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(ROOT_DIR)
    except OSError:
        usage_payload: dict[str, Any] = {"path": str(ROOT_DIR), "total": None, "used": None, "free": None, "percent": None}
    else:
        usage_payload = {
            "path": str(ROOT_DIR),
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": usage.used / usage.total * 100.0 if usage.total else None,
        }
    usage_payload["rootfs"] = _rootfs_expand_status("status", force=force_rootfs)
    rootfs_usage = usage_payload["rootfs"].get("usage") if isinstance(usage_payload["rootfs"], dict) else None
    if isinstance(rootfs_usage, dict):
        usage_payload["root_total"] = rootfs_usage.get("total")
        usage_payload["root_used"] = rootfs_usage.get("used")
        usage_payload["root_free"] = rootfs_usage.get("free")
        usage_payload["root_percent"] = rootfs_usage.get("percent")
    return usage_payload


def _lan_ipv4() -> str:
    candidates: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("1.1.1.1", 80))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass

    try:
        completed = subprocess.run(["hostname", "-I"], text=True, capture_output=True, check=False)
        candidates.extend(completed.stdout.split())
    except OSError:
        pass

    for candidate in candidates:
        candidate = candidate.strip()
        if candidate and not candidate.startswith("127."):
            return candidate
    return ""


def _validate_lan_hostname(value: Any) -> str:
    hostname = str(value or "").strip().lower()
    if not HOSTNAME_PATTERN.match(hostname):
        raise DaemonError("局域网名称只能包含字母、数字和连字符，长度 1-63，且不能以连字符开头或结尾")
    return hostname


def _validate_web_port(value: Any) -> int:
    try:
        port = int(str(value or "").strip())
    except (TypeError, ValueError):
        raise DaemonError("访问端口必须是 1024-65535 的数字")
    if port < 1024 or port > 65535:
        raise DaemonError("访问端口必须在 1024-65535 之间")
    return port


def _run_lan_blocklist(*args: str, required: bool = True) -> dict[str, Any]:
    if not LAN_BLOCKLIST_SCRIPT.exists():
        payload = {
            "blocked_ips": [],
            "devices": [],
            "supported": False,
            "message": "局域网黑名单脚本未安装，请先更新完整系统包",
        }
        if required:
            raise ScriptError(payload["message"], payload)
        return payload
    try:
        completed = subprocess.run(
            [str(LAN_BLOCKLIST_SCRIPT), *args],
            text=True,
            capture_output=True,
            timeout=LAN_BLOCKLIST_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        payload = {
            "blocked_ips": [],
            "devices": [],
            "supported": False,
            "message": f"局域网黑名单执行失败：{exc}",
        }
        if required:
            raise ScriptError(payload["message"], payload) from exc
        return payload
    raw = (completed.stdout or "").strip()
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        details = (completed.stderr or completed.stdout or "").strip()[-1600:]
        payload = {
            "blocked_ips": [],
            "devices": [],
            "supported": False,
            "message": details or "局域网黑名单返回了无法解析的结果",
        }
        if required:
            raise ScriptError(payload["message"], payload) from exc
        return payload
    if not isinstance(payload, dict):
        payload = {"blocked_ips": [], "devices": [], "supported": False, "message": "局域网黑名单返回结果格式错误"}
    if completed.returncode != 0 and required:
        raise ScriptError(str(payload.get("error") or payload.get("message") or "局域网黑名单应用失败"), payload)
    return payload


def _apply_lan_blocklist_on_startup() -> None:
    result = _run_lan_blocklist("apply", required=False)
    if result.get("blocked_ips") and result.get("supported"):
        app.logger.info("LAN blocklist applied: %s", ", ".join(str(item) for item in result.get("blocked_ips", [])))
    _lan_blocked_ips_cached(force=True)


def _lan_blocked_ips_cached(force: bool = False) -> set[str]:
    global _lan_blocklist_cache
    with _lan_blocklist_lock:
        if not force and _lan_blocklist_cache is not None:
            cached_at, cached_ips = _lan_blocklist_cache
            if time.monotonic() - cached_at <= 5.0:
                return set(cached_ips)
    payload = _run_lan_blocklist("status", required=False)
    ips = {str(item) for item in payload.get("blocked_ips", []) if isinstance(item, str)}
    with _lan_blocklist_lock:
        _lan_blocklist_cache = (time.monotonic(), set(ips))
    return ips


def _client_remote_ip() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",", 1)[0].strip()


@app.before_request
def _reject_lan_blocked_client():
    remote_ip = _client_remote_ip()
    if remote_ip and remote_ip in _lan_blocked_ips_cached():
        return jsonify({"ok": False, "error": "this device is blocked"}), 403


def _current_web_port() -> int:
    value = os.environ.get("AIASSISTANCE_PORT", "")
    try:
        port = int(value)
    except (TypeError, ValueError):
        return DEFAULT_WEB_PORT
    return port if 1 <= port <= 65535 else DEFAULT_WEB_PORT


def _network_urls(hostname: str, lan_ip: str, port: int | None = None) -> dict[str, str | int]:
    safe_port = port or _current_web_port()
    return {
        "web_port": safe_port,
        "lan_url": f"http://{lan_ip}:{safe_port}/" if lan_ip else "",
        "mdns_url": f"http://{hostname}.local:{safe_port}/",
    }


def _write_web_port_override(port: int) -> None:
    WEB_PORT_OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    WEB_PORT_OVERRIDE_PATH.write_text(f"[Service]\nEnvironment=AIASSISTANCE_PORT={port}\n", encoding="utf-8")


def _restart_web_service_delayed() -> None:
    command = (
        "sleep 1; "
        "systemctl daemon-reload >/dev/null 2>&1 || true; "
        "systemctl restart aiassistance-web.service >/dev/null 2>&1 || true"
    )
    subprocess.Popen(
        ["sh", "-c", command],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )


def _write_hostname_files(hostname: str) -> None:
    Path("/etc/hostname").write_text(hostname + "\n", encoding="utf-8")
    hosts_path = Path("/etc/hosts")
    try:
        lines = hosts_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []

    replaced = False
    next_lines: list[str] = []
    for line in lines:
        if re.match(r"^127\.0\.1\.1\s+", line):
            if not replaced:
                next_lines.append(f"127.0.1.1 {hostname}")
                replaced = True
            continue
        next_lines.append(line)
    if not replaced:
        if next_lines and next_lines[-1].strip():
            next_lines.append("")
        next_lines.append(f"127.0.1.1 {hostname}")
    hosts_path.write_text("\n".join(next_lines).rstrip() + "\n", encoding="utf-8")


def _set_avahi_option(section: str, key: str, value: str) -> None:
    config = Path("/etc/avahi/avahi-daemon.conf")
    if not config.exists():
        return
    lines = config.read_text(encoding="utf-8").splitlines()
    section_index = -1
    next_section_index = len(lines)
    for index, line in enumerate(lines):
        if line.strip() == f"[{section}]":
            section_index = index
            continue
        if section_index >= 0 and index > section_index and re.match(r"^\s*\[", line):
            next_section_index = index
            break
    if section_index < 0:
        lines.extend(["", f"[{section}]", f"{key}={value}"])
        config.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return

    key_pattern = re.compile(rf"^\s*#?\s*{re.escape(key)}\s*=")
    for index in range(section_index + 1, next_section_index):
        if key_pattern.match(lines[index]):
            lines[index] = f"{key}={value}"
            config.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
            return
    lines.insert(section_index + 1, f"{key}={value}")
    config.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _configure_avahi_for_hostname() -> None:
    _set_avahi_option("server", "use-ipv4", "yes")
    _set_avahi_option("server", "use-ipv6", "no")
    _set_avahi_option("publish", "publish-aaaa-on-ipv4", "no")
    _set_avahi_option("publish", "publish-a-on-ipv6", "no")
    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "enable", "avahi-daemon.service"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        subprocess.run(["systemctl", "restart", "avahi-daemon.service"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def _set_lan_hostname(hostname: str) -> None:
    if os.geteuid() != 0:
        raise DaemonError("需要以 root 运行 Web 服务才能修改局域网名称")
    if shutil.which("hostnamectl"):
        completed = subprocess.run(["hostnamectl", "set-hostname", hostname], text=True, capture_output=True, check=False)
        if completed.returncode != 0:
            details = re.sub(r"\s+", " ", (completed.stderr or completed.stdout or "").strip())
            raise DaemonError(details or "修改局域网名称失败")
    else:
        _write_hostname_files(hostname)
        subprocess.run(["hostname", hostname], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    _write_hostname_files(hostname)
    _configure_avahi_for_hostname()


def _set_web_port(port: int) -> None:
    if os.geteuid() != 0:
        raise DaemonError("需要以 root 运行 Web 服务才能修改访问端口")
    _write_web_port_override(port)
    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "daemon-reload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def _uptime_seconds() -> float | None:
    try:
        return float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return None


def save_uploaded_file(field_name: str, suffix: str) -> tuple[Path, Any]:
    uploaded = request.files.get(field_name)
    if uploaded is None or uploaded.filename == "":
        raise DaemonError(f"missing upload field: {field_name}")
    temp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        uploaded.save(temp.name)
        temp.close()
        return Path(temp.name), uploaded
    except Exception:
        temp.close()
        Path(temp.name).unlink(missing_ok=True)
        raise


def uploaded_basename(uploaded: Any, fallback: str) -> str:
    raw_name = PurePosixPath(str(uploaded.filename or fallback).replace("\\", "/")).name
    return raw_name or fallback


def validate_uploaded_suffix(uploaded: Any, allowed_suffixes: set[str], label: str) -> str:
    file_name = uploaded_basename(uploaded, label)
    suffix = Path(file_name).suffix.lower()
    if suffix not in allowed_suffixes:
        allowed = ", ".join(sorted(allowed_suffixes))
        raise DaemonError(f"{label} must be one of: {allowed}")
    return file_name


def _normalize_remote_host(host: str) -> str:
    value = str(host or "").strip()
    if not value:
        raise DaemonError("请输入 Windows 电脑局域网 IP")
    try:
        socket.inet_aton(value)
    except OSError as exc:
        raise DaemonError("Windows IP 格式不正确") from exc
    return value


def _remote_config() -> dict[str, Any]:
    try:
        data = json.loads(REMOTE_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {
        "host": str(data.get("host", "")).strip(),
        "control_port": int(data.get("control_port", REMOTE_CONTROL_PORT) or REMOTE_CONTROL_PORT),
        "frame_port": int(data.get("frame_port", REMOTE_FRAME_PORT) or REMOTE_FRAME_PORT),
        "result_port": int(data.get("result_port", REMOTE_RESULT_PORT) or REMOTE_RESULT_PORT),
    }


def _save_remote_config(config: dict[str, Any]) -> None:
    REMOTE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    REMOTE_CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _remote_call(
    command: str,
    payload: dict[str, Any] | None = None,
    body: bytes | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    config = _remote_config()
    request_payload = dict(payload or {})
    host = _normalize_remote_host(str(request_payload.pop("host", "") or config.get("host", "")))
    control_port = int(request_payload.pop("control_port", config.get("control_port", REMOTE_CONTROL_PORT)))
    request_payload.update({
        "command": command,
        "frame_port": int(request_payload.get("frame_port", config.get("frame_port", REMOTE_FRAME_PORT))),
        "result_port": int(request_payload.get("result_port", config.get("result_port", REMOTE_RESULT_PORT))),
    })
    if body is not None:
        request_payload["body_bytes"] = len(body)
    line = json.dumps(request_payload, ensure_ascii=False).encode("utf-8") + b"\n"
    try:
        with socket.create_connection((host, control_port), timeout=timeout or REMOTE_CONTROL_TIMEOUT_SEC) as sock:
            sock.settimeout(timeout or REMOTE_CONTROL_TIMEOUT_SEC)
            sock.sendall(line)
            if body:
                sock.sendall(body)
            reply = bytearray()
            while len(reply) < 4 * 1024 * 1024:
                chunk = sock.recv(1)
                if not chunk:
                    break
                if chunk == b"\n":
                    break
                reply.extend(chunk)
    except OSError as exc:
        raise DaemonError("连接失败，请重新输入局域网IP或检查Windows端程序是否启动") from exc
    if not reply:
        raise DaemonError("远端服务没有返回数据")
    try:
        data = json.loads(reply.decode("utf-8"))
    except Exception as exc:
        raise DaemonError("远端服务返回格式错误") from exc
    if not isinstance(data, dict):
        raise DaemonError("远端服务返回格式错误")
    if not data.get("ok", False):
        raise DaemonError(str(data.get("error") or "远端服务请求失败"))
    data["_remote_host"] = host
    data["_remote_control_port"] = control_port
    return data


def _sync_remote_models_from_list(
    host: str,
    models: list[dict[str, Any]],
    *,
    control_port: int = REMOTE_CONTROL_PORT,
    frame_port: int = REMOTE_FRAME_PORT,
    result_port: int = REMOTE_RESULT_PORT,
) -> dict[str, Any]:
    return daemon_call(
        "sync_remote_models",
        host=host,
        control_port=control_port,
        frame_port=frame_port,
        result_port=result_port,
        models=models,
    )


def _find_model_entry(model_id: str) -> dict[str, Any] | None:
    try:
        payload = daemon_call("list_models")
    except DaemonError:
        return None
    models = payload.get("models", [])
    if not isinstance(models, list):
        return None
    for model in models:
        if isinstance(model, dict) and str(model.get("id", "")) == model_id:
            return model
    return None


def _sync_remote_model_class_names(model: dict[str, Any], class_names: list[str]) -> list[str]:
    if str(model.get("backend", "")).lower() != "remote":
        return class_names
    if model.get("remote_available") is False:
        raise DaemonError("远端模型丢失，无法同步类别名称")
    remote_model_id = str(model.get("remote_model_id") or model.get("id") or "").strip()
    if not remote_model_id:
        raise DaemonError("远端模型缺少 remote_model_id，无法同步类别名称")
    config = _remote_config()
    host = _normalize_remote_host(str(model.get("remote_host") or config.get("host", "")))
    control_port = int(model.get("remote_control_port") or config.get("control_port", REMOTE_CONTROL_PORT))
    payload = _remote_call(
        "update_model_class_names",
        {
            "host": host,
            "control_port": control_port,
            "model_id": remote_model_id,
            "class_names": class_names,
        },
    )
    remote_model = payload.get("model", {})
    if isinstance(remote_model, dict):
        remote_class_names = remote_model.get("class_names", [])
        if isinstance(remote_class_names, list):
            return _normalize_model_class_names(remote_class_names)
    return class_names


def converter_python_path() -> Path:
    candidates: list[Path] = []
    if RKNN_CONVERTER_PYTHON:
        candidates.append(Path(RKNN_CONVERTER_PYTHON).expanduser())
    candidates.extend([
        RKNN_CONVERTER_WORKSPACE / "venv" / "bin" / "python",
        ROOT_DIR / "rknn-converter" / "venv" / "bin" / "python",
    ])
    for candidate in candidates:
        if candidate.exists() and os.access(candidate, os.X_OK):
            return candidate
    raise DaemonError("RKNN converter venv is not ready")


def converter_script_path() -> Path:
    candidates: list[Path] = []
    if RKNN_CONVERTER_SCRIPT:
        candidates.append(Path(RKNN_CONVERTER_SCRIPT).expanduser())
    candidates.extend([
        ROOT_DIR / "Python" / "convert_onnx_to_rknn.py",
        RKNN_CONVERTER_WORKSPACE / "convert_onnx_to_rknn.py",
    ])
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise DaemonError("convert_onnx_to_rknn.py was not found on the board")


def safe_output_stem(file_name: str) -> str:
    stem = Path(file_name).stem.strip()
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")
    return stem or "imported_model"


def save_uploaded_to(uploaded: Any, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    uploaded.save(str(destination))


def extract_calibration_zip(zip_path: Path, destination: Path) -> int:
    destination.mkdir(parents=True, exist_ok=True)
    total_unpacked = 0
    destination_resolved = destination.resolve()

    try:
        with zipfile.ZipFile(zip_path) as archive:
            infos = archive.infolist()
            if len(infos) > RKNN_ZIP_MAX_FILES:
                raise DaemonError(f"calibration zip contains too many files, max {RKNN_ZIP_MAX_FILES}")
            image_infos: list[zipfile.ZipInfo] = []
            for info in infos:
                if info.is_dir():
                    continue
                pure_name = PurePosixPath(info.filename)
                if pure_name.is_absolute() or ".." in pure_name.parts:
                    raise DaemonError("calibration zip contains unsafe paths")
                if Path(pure_name.name).suffix.lower() not in IMAGE_SUFFIXES:
                    continue
                image_infos.append(info)

            if not image_infos:
                raise DaemonError("calibration zip must contain image files")
            if len(image_infos) > RKNN_DATASET_MAX_IMAGES:
                step = len(image_infos) / RKNN_DATASET_MAX_IMAGES
                image_infos = [image_infos[min(int(index * step), len(image_infos) - 1)] for index in range(RKNN_DATASET_MAX_IMAGES)]

            for info in image_infos:
                pure_name = PurePosixPath(info.filename)
                total_unpacked += info.file_size
                if total_unpacked > RKNN_ZIP_MAX_UNPACKED_BYTES:
                    raise DaemonError("calibration zip is too large after unpacking")
                target = (destination / pure_name).resolve()
                if destination_resolved != target and destination_resolved not in target.parents:
                    raise DaemonError("calibration zip contains unsafe paths")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
    except zipfile.BadZipFile as exc:
        raise DaemonError("calibration file must be a valid zip archive") from exc

    return len(image_infos)


def count_calibration_images(dataset_dir: Path) -> int:
    if not dataset_dir.exists() or not dataset_dir.is_dir():
        raise DaemonError(f"default calibration image directory is not ready: {dataset_dir}")
    count = sum(1 for path in dataset_dir.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)
    if count <= 0:
        raise DaemonError(f"default calibration image directory has no images: {dataset_dir}")
    return count


def parse_converter_class_names(output: str) -> list[str]:
    for line in output.splitlines():
        match = re.match(r"^Classes\s+\(\d+\):\s*(.+)$", line.strip())
        if not match:
            continue
        try:
            parsed = ast.literal_eval(match.group(1))
        except Exception:
            return []
        if isinstance(parsed, dict):
            normalized: dict[int, str] = {}
            for key, value in parsed.items():
                try:
                    index = int(key)
                except Exception:
                    continue
                if index < 0:
                    continue
                normalized[index] = str(value)
            if not normalized:
                return []
            return [normalized.get(index, "") for index in range(max(normalized) + 1)]
        if isinstance(parsed, list):
            return [str(value) for value in parsed]
        return []
    return []


def run_onnx_converter_command(command: list[str], workspace: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=str(workspace),
            check=False,
            capture_output=True,
            text=True,
            timeout=RKNN_CONVERTER_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired as exc:
        raise DaemonError("ONNX conversion timed out") from exc


def onnx_converter_details(completed: subprocess.CompletedProcess[str]) -> str:
    details = "\n".join(part.strip() for part in (completed.stderr, completed.stdout) if part and part.strip())
    return details[-1200:] if details else f"exit code {completed.returncode}"


def should_retry_onnx_graph_fallback(completed: subprocess.CompletedProcess[str]) -> bool:
    details = "\n".join(part for part in (completed.stderr, completed.stdout) if part).lower()
    return (
        completed.returncode in {-6, 134}
        or "stl_vector.h" in details
        or "std::vector" in details
        or "__n < this->size()" in details
        or "shape_inference" in details
        or "shape inference" in details
    )


def _normalize_model_class_name(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").replace("\x00", "")).strip()
    if len(text) > MODEL_LABEL_MAX_NAME_CHARS:
        text = text[:MODEL_LABEL_MAX_NAME_CHARS].strip()
    return text


def _normalize_model_class_names(values: list[Any]) -> list[str]:
    names = [_normalize_model_class_name(value) for value in values[:MODEL_LABEL_MAX_COUNT]]
    while names and not names[-1]:
        names.pop()
    return names


def _model_class_names_from_mapping(values: dict[Any, Any]) -> list[str]:
    indexed: dict[int, str] = {}
    for key, value in values.items():
        try:
            index = int(key)
        except Exception:
            continue
        if 0 <= index < MODEL_LABEL_MAX_COUNT:
            indexed[index] = _normalize_model_class_name(value)
    if not indexed:
        return []
    max_index = min(max(indexed), MODEL_LABEL_MAX_COUNT - 1)
    return _normalize_model_class_names([indexed.get(index, "") for index in range(max_index + 1)])


def _model_class_names_from_parsed(value: Any) -> list[str]:
    if isinstance(value, dict):
        return _model_class_names_from_mapping(value)
    if isinstance(value, list):
        return _normalize_model_class_names(value)
    return []


def parse_model_label_text(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []

    if stripped[0] in "[{":
        for parser in (json.loads, ast.literal_eval):
            try:
                names = _model_class_names_from_parsed(parser(stripped))
            except Exception:
                continue
            if names:
                return names

    indexed: dict[int, str] = {}
    sequential: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^(\d+)(?:\s*[:=,]\s*|\s+)(.+)$", line)
        if match:
            index = int(match.group(1))
            if 0 <= index < MODEL_LABEL_MAX_COUNT:
                indexed[index] = _normalize_model_class_name(match.group(2))
            continue
        sequential.append(line)

    if indexed:
        max_index = min(max(indexed), MODEL_LABEL_MAX_COUNT - 1)
        names = [indexed.get(index, "") for index in range(max_index + 1)]
        names.extend(sequential)
        return _normalize_model_class_names(names)
    return _normalize_model_class_names(sequential)


def decode_model_label_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def read_uploaded_model_labels(field_name: str = "labels_file") -> list[str]:
    uploaded = request.files.get(field_name)
    if uploaded is None or uploaded.filename == "":
        return []
    validate_uploaded_suffix(uploaded, MODEL_LABEL_SUFFIXES, "label file")
    data = uploaded.read(MODEL_LABEL_MAX_BYTES + 1)
    if len(data) > MODEL_LABEL_MAX_BYTES:
        raise DaemonError(f"label file is too large, max {MODEL_LABEL_MAX_BYTES} bytes")
    return parse_model_label_text(decode_model_label_bytes(data))


def save_uploaded_model_preset(field_name: str = "preset_file") -> tuple[Path | None, str]:
    uploaded = request.files.get(field_name)
    if uploaded is None or uploaded.filename == "":
        return None, ""
    file_name = validate_uploaded_suffix(uploaded, {".json"}, "preset file")
    temp_path, uploaded = save_uploaded_file(field_name, ".json")
    if temp_path.stat().st_size > MODEL_PRESET_MAX_BYTES:
        temp_path.unlink(missing_ok=True)
        raise DaemonError(f"preset file is too large, max {MODEL_PRESET_MAX_BYTES} bytes")
    preset_name = Path(uploaded_basename(uploaded, file_name)).stem.strip()
    if not preset_name:
        temp_path.unlink(missing_ok=True)
        raise DaemonError("preset file name is invalid")
    return temp_path, preset_name


def convert_onnx_upload_to_rknn(uploaded_model: Any, uploaded_dataset: Any | None, workspace: Path) -> tuple[Path, str, int, list[str]]:
    onnx_file_name = validate_uploaded_suffix(uploaded_model, {".onnx"}, "ONNX model")

    source_dir = workspace / "source"
    output_dir = workspace / "output"
    onnx_path = source_dir / onnx_file_name
    output_path = output_dir / f"{safe_output_stem(onnx_file_name)}_raw_int8_{RKNN_TARGET_PLATFORM}.rknn"

    save_uploaded_to(uploaded_model, onnx_path)
    if uploaded_dataset is not None and uploaded_dataset.filename:
        validate_uploaded_suffix(uploaded_dataset, {".zip"}, "calibration zip")
        dataset_dir = workspace / "calibration_images"
        zip_path = source_dir / "calibration.zip"
        save_uploaded_to(uploaded_dataset, zip_path)
        image_count = extract_calibration_zip(zip_path, dataset_dir)
    else:
        dataset_dir = RKNN_DEFAULT_CALIBRATION_DIR
        image_count = count_calibration_images(dataset_dir)

    command = [
        str(converter_python_path()),
        str(converter_script_path()),
        "--onnx",
        str(onnx_path),
        "--dataset-root",
        str(dataset_dir),
        "--dataset-count",
        str(image_count),
        "--target-platform",
        RKNN_TARGET_PLATFORM,
        "--output",
        str(output_path),
    ]
    completed = run_onnx_converter_command(command, workspace)
    if completed.returncode != 0 and should_retry_onnx_graph_fallback(completed):
        fallback_command = command + ["--skip-shape-inference", "--output-layout", "graph"]
        fallback_completed = run_onnx_converter_command(fallback_command, workspace)
        if fallback_completed.returncode == 0:
            completed = fallback_completed
        else:
            fallback_details = onnx_converter_details(fallback_completed)
            initial_details = onnx_converter_details(completed)
            raise DaemonError(
                "ONNX conversion failed: "
                f"{fallback_details}\nInitial conversion also failed: {initial_details[-600:]}"
            )

    if completed.returncode != 0:
        raise DaemonError(f"ONNX conversion failed: {onnx_converter_details(completed)}")
    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise DaemonError("ONNX conversion did not produce an RKNN file")
    return output_path, onnx_file_name, image_count, parse_converter_class_names(completed.stdout)


def import_rknn_into_daemon(
    source_path: Path,
    file_name: str,
    default_description: str,
    class_names: list[str] | None = None,
    preset_source_path: Path | None = None,
    preset_name: str = "",
) -> Any:
    return daemon_call(
        "import_model",
        source_path=str(source_path),
        file_name=file_name,
        description=request.form.get("description", default_description),
        game_profile=request.form.get("game_profile", "generic"),
        class_names=class_names or [],
        preset_source_path=str(preset_source_path) if preset_source_path is not None else "",
        preset_name=preset_name,
    )


def import_model_file_into_daemon(
    source_path: Path,
    file_name: str,
    default_description: str,
    class_names: list[str] | None = None,
    preset_source_path: Path | None = None,
    preset_name: str = "",
) -> Any:
    return import_rknn_into_daemon(
        source_path,
        file_name,
        default_description,
        class_names,
        preset_source_path,
        preset_name,
    )


def _normalize_cloud_model_name(value: Any) -> str:
    name = re.sub(r"\s+", " ", str(value or "").replace("\\", "/")).strip()
    if not name:
        raise DaemonError("云端模型名不能为空")
    pure = PurePosixPath(name)
    if pure.is_absolute() or ".." in pure.parts:
        raise DaemonError("云端模型名不能使用绝对路径或上级目录")
    if len(name) > 180:
        raise DaemonError("云端模型名过长")
    if not name.lower().endswith(".rknn"):
        raise DaemonError("云端模型名必须以 .rknn 结尾")
    return name


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except Exception:
        return default
    return max(minimum, min(maximum, number))


def _cloud_class_names(body: dict[str, Any]) -> list[str]:
    raw = body.get("class_names", [])
    if isinstance(raw, str):
        return parse_model_label_text(raw)
    if isinstance(raw, list):
        return _normalize_model_class_names(raw)
    return []


def _cloud_encrypted_model_matches(model: Any, cloud_model_name: str) -> bool:
    if not isinstance(model, dict):
        return False
    backend = str(model.get("backend", "")).lower().replace("-", "_")
    return backend == "cloud_encrypted" and str(model.get("file_name") or model.get("cloud_model_name") or "") == cloud_model_name


def _recover_cloud_encrypted_model_after_timeout(cloud_model_name: str) -> dict[str, Any] | None:
    deadline = time.monotonic() + max(CLOUD_ENCRYPTED_RECOVERY_WAIT_SEC, 0.0)
    while True:
        try:
            payload = daemon_call("list_models", timeout=10.0)
            models = payload.get("models", []) if isinstance(payload, dict) else []
            if isinstance(models, list):
                for model in models:
                    if _cloud_encrypted_model_matches(model, cloud_model_name):
                        return payload
            return None
        except DaemonError as exc:
            if not _is_transient_daemon_socket_busy(exc) or time.monotonic() >= deadline:
                return None
            time.sleep(1.0)
        if time.monotonic() >= deadline:
            return None


def _json_post(url: str, payload: dict[str, Any], timeout: int = 20) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_obj = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "aiAssistance-updater/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request_obj, timeout=timeout) as response:
            raw = response.read(2 * 1024 * 1024)
    except urllib.error.HTTPError as exc:
        raw_error = exc.read(64 * 1024)
        details = raw_error.decode("utf-8", "replace")
        try:
            decoded_error = json.loads(details)
        except json.JSONDecodeError:
            decoded_error = {}
        if isinstance(decoded_error, dict):
            data_obj = decoded_error.get("data")
            if isinstance(data_obj, dict) and data_obj.get("revoked"):
                message = str(decoded_error.get("error") or data_obj.get("reason") or "license revoked")
                if _is_destructive_license_revocation(message, data_obj):
                    raise LicenseServerError(message, data_obj, exc.code) from exc
                raise DaemonError(message) from exc
            if decoded_error.get("ok") is False:
                raise DaemonError(str(decoded_error.get("error") or f"server returned HTTP {exc.code}")) from exc
        raise DaemonError(f"server returned HTTP {exc.code}: {details}") from exc
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", None) or str(exc)
        raise DaemonError(f"failed to connect license server: {reason}") from exc
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise DaemonError("license server returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise DaemonError("license server response must be an object")
    if decoded.get("ok") is False:
        data_obj = decoded.get("data")
        if isinstance(data_obj, dict) and data_obj.get("revoked"):
            message = str(decoded.get("error") or data_obj.get("reason") or "license revoked")
            if _is_destructive_license_revocation(message, data_obj):
                raise LicenseServerError(message, data_obj)
            raise DaemonError(message)
        raise DaemonError(str(decoded.get("error") or "server rejected request"))
    data_obj = decoded.get("data", decoded)
    if not isinstance(data_obj, dict):
        raise DaemonError("license server response data must be an object")
    return data_obj


def _json_get(url: str, timeout: int = 10) -> dict[str, Any]:
    request_obj = urllib.request.Request(
        url,
        headers={"User-Agent": "aiAssistance-web/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request_obj, timeout=timeout) as response:
            raw = response.read(512 * 1024)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
        raise DaemonError(f"failed to connect license server: {getattr(exc, 'reason', None) or exc}") from exc
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise DaemonError("license server returned invalid JSON") from exc
    if not isinstance(decoded, dict):
        raise DaemonError("license server response must be an object")
    if decoded.get("ok") is False:
        raise DaemonError(str(decoded.get("error") or "server rejected request"))
    data_obj = decoded.get("data", decoded)
    if not isinstance(data_obj, dict):
        raise DaemonError("license server response data must be an object")
    return data_obj


def _normalize_server_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        value = DEFAULT_LICENSE_SERVER_URL
    value = value.strip()
    if not value:
        raise DaemonError("license server URL is required")
    if not value.startswith(("https://", "http://")):
        raise DaemonError("license server URL must start with http:// or https://")
    return value.rstrip("/")


def _license_server_url() -> str:
    if LICENSE_SERVER_URL_PATH.exists():
        saved = LICENSE_SERVER_URL_PATH.read_text(encoding="utf-8").strip()
        if saved:
            return saved.rstrip("/")
    return _normalize_server_url(DEFAULT_LICENSE_SERVER_URL)


def _license_url(server_url: str, suffix: str) -> str:
    return f"{server_url.rstrip('/')}/v1/{suffix.lstrip('/')}"


def _safe_update_file_name(url: str, fallback: str = "update.tar.zst") -> str:
    raw = url.rsplit("/", 1)[-1].split("?", 1)[0].strip()
    raw = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
    return raw or fallback


NON_DESTRUCTIVE_LICENSE_REASONS = {
    "valid license is required",
    "license is not active on this server",
    "license key is already bound to another device",
    "current hardware evidence does not match previous device",
    "current device does not contain previous license fingerprint evidence",
}


def _is_destructive_license_revocation(message: str, payload: dict[str, Any] | None = None) -> bool:
    reason = str(message or (payload or {}).get("reason") or "").strip().lower()
    if reason in NON_DESTRUCTIVE_LICENSE_REASONS:
        return False
    return bool(payload and payload.get("revoked"))


def _sha256_file(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_file(url: str, destination: Path, progress_callback: Any | None = None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=UPDATE_DOWNLOAD_TIMEOUT_SEC) as response, destination.open("wb") as output:
            total = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                if progress_callback:
                    progress_callback(downloaded, total)
    except urllib.error.URLError as exc:
        destination.unlink(missing_ok=True)
        raise DaemonError(f"failed to download update package: {exc.reason}") from exc


def _model_key_metadata(key: bytes, **extra: Any) -> dict[str, Any]:
    payload = {
        "format": "aimk1",
        "key_b64": base64.b64encode(key).decode("ascii"),
        "sha256": hashlib.sha256(key).hexdigest(),
        "size": len(key),
    }
    payload.update(extra)
    return payload


def _read_local_model_key() -> bytes | None:
    try:
        key = MODEL_KEY_PATH.read_bytes()
    except OSError:
        return None
    return key if len(key) == MODEL_KEY_BYTES else None


def _write_model_key_activation(payload: dict[str, Any]) -> None:
    LICENSE_DIR.mkdir(parents=True, exist_ok=True)
    doc = dict(payload)
    doc.setdefault("format", "aiassistance-model-key-activation-v1")
    doc["updated_at"] = int(time.time())
    tmp_path = MODEL_KEY_ACTIVATION_PATH.with_suffix(MODEL_KEY_ACTIVATION_PATH.suffix + ".tmp")
    tmp_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(MODEL_KEY_ACTIVATION_PATH)
    try:
        MODEL_KEY_ACTIVATION_PATH.chmod(0o600)
    except OSError:
        pass


def _local_model_key_package() -> dict[str, Any]:
    key = _read_local_model_key()
    if key is None:
        return {}
    activation = _read_json_object(MODEL_KEY_ACTIVATION_PATH)
    source = str(activation.get("source") or "").strip()
    sha = hashlib.sha256(key).hexdigest()
    return _model_key_metadata(
        key,
        source=source if source in {"server", "legacy_local"} and activation.get("sha256") == sha else "legacy_local",
    )


def _install_model_key_package(model_key: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(model_key, dict) or not str(model_key.get("key_b64") or "").strip():
        existing = _read_local_model_key()
        if existing is not None:
            return _model_key_metadata(existing, preserved_existing=True, missing_from_server=True)
        raise DaemonError("activation response missing model key")
    if str(model_key.get("format") or "").strip().lower() != "aimk1":
        raise DaemonError("activation response model key format is invalid")
    try:
        key = base64.b64decode(str(model_key.get("key_b64") or ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise DaemonError("activation response model key is invalid") from exc
    if len(key) != MODEL_KEY_BYTES:
        raise DaemonError("activation response model key length is invalid")
    expected_sha = str(model_key.get("sha256") or "").strip().lower()
    actual_sha = hashlib.sha256(key).hexdigest()
    if expected_sha and expected_sha != actual_sha:
        raise DaemonError("activation response model key sha256 mismatch")
    existing = _read_local_model_key()
    if existing is not None:
        existing_meta = _model_key_metadata(existing)
        if existing != key:
            existing_meta["preserved_existing"] = True
            existing_meta["server_sha256"] = actual_sha
            _write_model_key_activation({
                "source": "legacy_local",
                "sha256": existing_meta["sha256"],
                "server_sha256": actual_sha,
                "preserved_existing": True,
            })
            return existing_meta
        try:
            MODEL_KEY_PATH.chmod(0o600)
        except OSError:
            pass
        _write_model_key_activation({
            "source": "server",
            "sha256": actual_sha,
            "server_sha256": actual_sha,
            "preserved_existing": False,
        })
        return existing_meta
    LICENSE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = MODEL_KEY_PATH.with_suffix(MODEL_KEY_PATH.suffix + ".tmp")
    tmp_path.write_bytes(key)
    tmp_path.replace(MODEL_KEY_PATH)
    try:
        MODEL_KEY_PATH.chmod(0o600)
    except OSError:
        pass
    _write_model_key_activation({
        "source": "server",
        "sha256": actual_sha,
        "server_sha256": actual_sha,
        "preserved_existing": False,
    })
    return _model_key_metadata(key, code=str(model_key.get("code") or ""))


def _ensure_model_key_available() -> dict[str, Any]:
    existing = _read_local_model_key()
    if existing is not None:
        return _model_key_metadata(existing, source=_local_model_key_package().get("source", "legacy_local"))

    with _model_key_install_lock:
        existing = _read_local_model_key()
        if existing is not None:
            return _model_key_metadata(existing, source=_local_model_key_package().get("source", "legacy_local"))
        server_url = _license_server_url()
        license_payload = _state_with_license_recovery("get_license")
        response = _json_post(_license_url(server_url, "license-check"), _update_server_request_payload(license_payload, {}))
        online_grant = response.get("online_grant")
        if isinstance(online_grant, dict):
            ONLINE_GRANT_PATH.write_text(json.dumps(online_grant, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        license_doc = response.get("license")
        if isinstance(license_doc, dict):
            LICENSE_PATH.write_text(json.dumps(license_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            try:
                LICENSE_PATH.chmod(0o600)
            except OSError:
                pass
        model_key = response.get("model_key")
        if isinstance(model_key, dict):
            return _install_model_key_package(model_key)
        raise DaemonError("model encryption key is missing; activate this device before copying the model device code or using encrypted models")


def _absolute_download_url(server_url: str, url: str) -> str:
    url = str(url or "").strip()
    if not url:
        raise DaemonError("core download url is required")
    if url.startswith(("http://", "https://")):
        return url
    return urllib.parse.urljoin(server_url.rstrip("/") + "/", url.lstrip("/"))


def _component_activation_doc(component: dict[str, Any], expected_sha: str, size: int) -> dict[str, Any]:
    return {
        "format": component.get("format", ""),
        "version": component.get("version", ""),
        "sha256": expected_sha,
        "size": size,
        "key_b64": component.get("key_b64", ""),
        "nonce_b64": component.get("nonce_b64", ""),
        "tag_b64": component.get("tag_b64", ""),
        "salt_b64": component.get("salt_b64", ""),
        "aad": component.get("aad", ""),
        "downloaded_at": int(time.time()),
    }


def _stage_component_package(
    name: str,
    component: dict[str, Any],
    server_url: str,
    progress_callback: Any | None = None,
) -> dict[str, Any]:
    if not isinstance(component, dict) or component.get("mode") != "download":
        return {}
    required_fields = ("download_url", "sha256", "key_b64", "nonce_b64", "tag_b64", "aad")
    missing = [field for field in required_fields if not str(component.get(field, "")).strip()]
    if missing:
        raise DaemonError(f"update component {name} metadata is missing: {', '.join(missing)}")
    expected_sha = str(component.get("sha256", "")).strip().lower()
    version = re.sub(r"[^A-Za-z0-9._-]+", "_", str(component.get("version", "")).strip()).strip("._") or "unknown"
    destination = UPDATES_DIR / "components" / f"{name}-{version}-{expected_sha[:12]}.enc"
    _download_file(_absolute_download_url(server_url, str(component.get("download_url", ""))), destination, progress_callback)
    actual_sha = _sha256_file(destination).lower()
    if actual_sha != expected_sha:
        destination.unlink(missing_ok=True)
        raise DaemonError(f"downloaded {name} component sha256 mismatch")
    expected_size = int(component.get("size") or 0)
    if expected_size > 0 and destination.stat().st_size != expected_size:
        destination.unlink(missing_ok=True)
        raise DaemonError(f"downloaded {name} component size mismatch")
    return {
        "path": str(destination),
        "activation": _component_activation_doc(component, expected_sha, destination.stat().st_size),
    }


def _stage_hailo_package(
    hailo: dict[str, Any],
    server_url: str,
    progress_callback: Any | None = None,
) -> Path:
    if not isinstance(hailo, dict) or hailo.get("mode") != "download":
        raise DaemonError("license server did not provide a downloadable Hailo package")
    required_fields = ("download_url", "sha256", "version", "kernel_release")
    missing = [field for field in required_fields if not str(hailo.get(field, "")).strip()]
    if missing:
        raise DaemonError(f"Hailo package metadata is missing: {', '.join(missing)}")
    expected_sha = str(hailo.get("sha256", "")).strip().lower()
    board_id = _safe_hailo_part(hailo.get("board_id"), "orangepi")
    version = _safe_hailo_part(hailo.get("version"), "unknown")
    kernel = _safe_hailo_part(hailo.get("kernel_release"), "kernel")
    source_name = str(hailo.get("file") or hailo.get("download_url") or "")
    suffix = ".tar.gz" if source_name.endswith(".tar.gz") else ".tar.zst"
    destination = HAILO_PACKAGE_DIR / f"hailo-{board_id}-{kernel}-{version}-{expected_sha[:12]}{suffix}"
    _download_file(_absolute_download_url(server_url, str(hailo.get("download_url", ""))), destination, progress_callback)
    actual_sha = _sha256_file(destination).lower()
    if actual_sha != expected_sha:
        destination.unlink(missing_ok=True)
        raise DaemonError("downloaded Hailo package sha256 mismatch")
    expected_size = int(hailo.get("size") or 0)
    if expected_size > 0 and destination.stat().st_size != expected_size:
        destination.unlink(missing_ok=True)
        raise DaemonError("downloaded Hailo package size mismatch")
    return destination


def _run_hailo_install_worker() -> None:
    started_at = int(time.time())
    logs: list[str] = []

    def append_log(line: str) -> None:
        text = str(line or "").rstrip()
        if text:
            logs.append(text)

    def set_status(stage: str, message: str, progress: int, *, status: str = "running", error: str = "", **extra: Any) -> None:
        _write_hailo_install_status(_hailo_install_status_payload(
            status=status,
            stage=stage,
            message=message,
            progress=progress,
            error=error,
            log=logs,
            started_at=started_at,
            completed_at=int(time.time()) if status in {"ready", "failed"} else None,
            extra=extra or None,
        ))

    try:
        set_status("detect", "正在检测 Hailo-8 PCIe 设备", 5)
        current_status = _hailo_status()
        if not current_status.get("pcie", {}).get("present"):
            raise DaemonError("未检测到 Hailo-8 PCIe 设备")
        if not HAILO_INSTALL_SCRIPT.exists():
            raise DaemonError("Hailo 安装脚本未安装，请先更新完整系统包")

        kernel_release = str(current_status.get("kernel_release") or _uname_release()).strip()
        board_id = str(current_status.get("board_id") or "").strip()
        server_url = _license_server_url()
        license_payload = _state_with_license_recovery("get_license")
        request_payload = _update_server_request_payload(license_payload, {
            "kernel_release": kernel_release,
            "hailo_version": HAILO_EXPECTED_VERSION,
            "board_id": board_id,
        })
        request_payload["kernel_release"] = kernel_release
        request_payload["hailo_version"] = HAILO_EXPECTED_VERSION
        request_payload["board_id"] = board_id
        set_status("request", "正在向授权服务器请求 Hailo 依赖包", 12)
        response = _json_post(_license_url(server_url, "hailo/package"), request_payload, timeout=30)
        hailo = response.get("hailo") if isinstance(response.get("hailo"), dict) else {}
        if not hailo:
            raise DaemonError("授权服务器未返回 Hailo 依赖包信息")
        if str(hailo.get("kernel_release") or "") != kernel_release:
            raise DaemonError(f"Hailo 依赖包内核不匹配：服务器={hailo.get('kernel_release')}, 本机={kernel_release}")
        response_board_id = str(hailo.get("board_id") or "").strip()
        if board_id and response_board_id and response_board_id != board_id:
            raise DaemonError(f"Hailo 依赖包板型不匹配：服务器={response_board_id}, 本机={board_id}")

        def download_progress(downloaded: int, total: int) -> None:
            percent = int((downloaded / total) * 100) if total > 0 else 0
            progress = 18 + int(min(1.0, downloaded / total) * 42) if total > 0 else 35
            set_status(
                "download",
                f"正在下载 Hailo 依赖包 {percent}%" if total > 0 else "正在下载 Hailo 依赖包",
                progress,
                downloaded_bytes=downloaded,
                total_bytes=total,
            )

        package_path = _stage_hailo_package(hailo, server_url, download_progress)
        set_status("install", "依赖包已校验，正在安装驱动和 HailoRT", 64, package=str(package_path))
        command = [sys.executable, str(HAILO_INSTALL_SCRIPT), str(package_path), "--json"]
        process = subprocess.Popen(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            append_log(line)
            set_status("install", "正在安装驱动和 HailoRT", 76, package=str(package_path))
        exit_code = process.wait(timeout=15)
        if exit_code != 0:
            raise DaemonError(f"Hailo 安装脚本失败，退出码 {exit_code}")
        final_status = _hailo_status()
        if not final_status.get("ready"):
            scan_output = final_status.get("device", {}).get("scan", {}).get("output", "")
            raise DaemonError(scan_output or "Hailo 依赖已安装，但 hailortcli scan 未就绪")
        set_status("ready", "Hailo-8 已就绪", 100, status="ready", result=final_status)
    except LicenseServerError as exc:
        if exc.payload.get("revoked"):
            _apply_license_revocation(exc.payload, server_url if "server_url" in locals() else "")
        append_log(str(exc))
        set_status("failed", "Hailo 安装失败", 100, status="failed", error=str(exc))
    except Exception as exc:
        append_log(str(exc))
        set_status("failed", "Hailo 安装失败", 100, status="failed", error=str(exc))


def _start_hailo_install() -> dict[str, Any]:
    global _hailo_install_thread
    with _hailo_install_lock:
        if _hailo_install_thread is not None and _hailo_install_thread.is_alive():
            return {"started": False, "already_running": True, "status": _read_hailo_install_status()}
        _hailo_install_thread = threading.Thread(target=_run_hailo_install_worker, name="hailo-install", daemon=True)
        _hailo_install_thread.start()
        return {"started": True, "status": _read_hailo_install_status()}


def _stage_application_package(package: dict[str, Any], progress_callback: Any | None = None) -> Path:
    url = str(package.get("url", "")).strip()
    expected_sha = str(package.get("sha256", "")).strip().lower()
    if not url or not expected_sha:
        raise DaemonError("package url and sha256 are required")
    file_name = _safe_update_file_name(url)
    package_path = UPDATES_DIR / file_name
    _download_file(url, package_path, progress_callback)
    actual_sha = _sha256_file(package_path)
    if actual_sha.lower() != expected_sha:
        package_path.unlink(missing_ok=True)
        raise DaemonError("downloaded update package sha256 mismatch")
    return package_path


def _install_core_package(core: dict[str, Any], server_url: str) -> dict[str, Any]:
    if not isinstance(core, dict) or core.get("mode") != "download":
        raise DaemonError("activation response did not include a downloadable core module")
    required_fields = ("download_url", "sha256", "key_b64", "nonce_b64", "tag_b64", "aad")
    missing = [field for field in required_fields if not str(core.get(field, "")).strip()]
    if missing:
        raise DaemonError(f"activation response core metadata is missing: {', '.join(missing)}")

    CORE_DIR.mkdir(parents=True, exist_ok=True)
    LICENSE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = CORE_ENC_PATH.with_suffix(CORE_ENC_PATH.suffix + ".tmp")
    _download_file(_absolute_download_url(server_url, str(core.get("download_url", ""))), tmp_path)
    expected_sha = str(core.get("sha256", "")).strip().lower()
    actual_sha = _sha256_file(tmp_path).lower()
    if actual_sha != expected_sha:
        tmp_path.unlink(missing_ok=True)
        raise DaemonError("downloaded core module sha256 mismatch")
    expected_size = int(core.get("size") or 0)
    if expected_size > 0 and tmp_path.stat().st_size != expected_size:
        tmp_path.unlink(missing_ok=True)
        raise DaemonError("downloaded core module size mismatch")

    activation_doc = {
        "format": core.get("format", "aicore1"),
        "version": core.get("version", ""),
        "sha256": expected_sha,
        "size": tmp_path.stat().st_size,
        "key_b64": core.get("key_b64", ""),
        "nonce_b64": core.get("nonce_b64", ""),
        "tag_b64": core.get("tag_b64", ""),
        "salt_b64": core.get("salt_b64", ""),
        "aad": core.get("aad", ""),
        "downloaded_at": int(time.time()),
    }
    activation_tmp = CORE_ACTIVATION_PATH.with_suffix(CORE_ACTIVATION_PATH.suffix + ".tmp")
    activation_tmp.write_text(json.dumps(activation_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(CORE_ENC_PATH)
    activation_tmp.replace(CORE_ACTIVATION_PATH)
    try:
        CORE_ACTIVATION_PATH.chmod(0o600)
        CORE_ENC_PATH.chmod(0o600)
    except OSError:
        pass
    return activation_doc


def _install_usb_proxy_package(usb_proxy: dict[str, Any], server_url: str) -> dict[str, Any]:
    if not isinstance(usb_proxy, dict) or usb_proxy.get("mode") != "download":
        return {}
    required_fields = ("download_url", "sha256", "key_b64", "nonce_b64", "tag_b64", "aad")
    missing = [field for field in required_fields if not str(usb_proxy.get(field, "")).strip()]
    if missing:
        raise DaemonError(f"activation response usb-proxy metadata is missing: {', '.join(missing)}")

    USB_PROXY_ENC_PATH.parent.mkdir(parents=True, exist_ok=True)
    USB_PROXY_ACTIVATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = USB_PROXY_ENC_PATH.with_suffix(USB_PROXY_ENC_PATH.suffix + ".tmp")
    _download_file(_absolute_download_url(server_url, str(usb_proxy.get("download_url", ""))), tmp_path)
    expected_sha = str(usb_proxy.get("sha256", "")).strip().lower()
    actual_sha = _sha256_file(tmp_path).lower()
    if actual_sha != expected_sha:
        tmp_path.unlink(missing_ok=True)
        raise DaemonError("downloaded usb-proxy binary sha256 mismatch")
    expected_size = int(usb_proxy.get("size") or 0)
    if expected_size > 0 and tmp_path.stat().st_size != expected_size:
        tmp_path.unlink(missing_ok=True)
        raise DaemonError("downloaded usb-proxy binary size mismatch")

    activation_doc = {
        "format": usb_proxy.get("format", "aiusbproxy1"),
        "version": usb_proxy.get("version", ""),
        "sha256": expected_sha,
        "size": tmp_path.stat().st_size,
        "key_b64": usb_proxy.get("key_b64", ""),
        "nonce_b64": usb_proxy.get("nonce_b64", ""),
        "tag_b64": usb_proxy.get("tag_b64", ""),
        "salt_b64": usb_proxy.get("salt_b64", ""),
        "aad": usb_proxy.get("aad", ""),
        "downloaded_at": int(time.time()),
    }
    activation_tmp = USB_PROXY_ACTIVATION_PATH.with_suffix(USB_PROXY_ACTIVATION_PATH.suffix + ".tmp")
    activation_tmp.write_text(json.dumps(activation_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(USB_PROXY_ENC_PATH)
    activation_tmp.replace(USB_PROXY_ACTIVATION_PATH)
    try:
        (USB_PROXY_ROOT / "bin" / "usb-proxy").unlink(missing_ok=True)
    except OSError:
        pass
    try:
        USB_PROXY_ACTIVATION_PATH.chmod(0o600)
        USB_PROXY_ENC_PATH.chmod(0o600)
    except OSError:
        pass
    return activation_doc


def _restart_usb_proxy_service() -> None:
    try:
        subprocess.run(
            ["systemctl", "restart", "usb-proxy.service"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    except Exception:
        pass


def _apply_license_revocation(payload: dict[str, Any], server_url: str = "") -> dict[str, Any]:
    LICENSE_DIR.mkdir(parents=True, exist_ok=True)
    reason = str(payload.get("reason") or "授权已撤销")
    previous_brand = _current_ui_brand()
    revoked_brand = _normalize_ui_brand(payload.get("ui_brand") or previous_brand)
    revoked_doc = {
        "revoked": True,
        "reason": reason,
        "license_id": str(payload.get("license_id", "")),
        "device_id": str(payload.get("device_id", "")),
        "device_fingerprint_hash": str(payload.get("device_fingerprint_hash", "")),
        "server_url": server_url,
        "ui_brand": revoked_brand,
        "revoked_at": int(time.time()),
    }
    (REVOKED_PATH.with_suffix(REVOKED_PATH.suffix + ".tmp")).write_text(
        json.dumps(revoked_doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (REVOKED_PATH.with_suffix(REVOKED_PATH.suffix + ".tmp")).replace(REVOKED_PATH)
    for path in (LICENSE_PATH, LICENSE_KEY_CACHE_PATH, CORE_ACTIVATION_PATH, ONLINE_GRANT_PATH, CORE_ENC_PATH, USB_PROXY_ACTIVATION_PATH, USB_PROXY_ENC_PATH, theme_manager.entitlements_path, theme_manager.state_path):
        path.unlink(missing_ok=True)
    for temp_core in (ROOT_DIR / "run").glob("libai_core_*.so"):
        temp_core.unlink(missing_ok=True)
    shutil.rmtree("/run/usb-proxy", ignore_errors=True)
    _restart_usb_proxy_service()
    try:
        refreshed = daemon_call("refresh_license")
    except DaemonError:
        refreshed = {}
    return {"revoked": revoked_doc, "state": refreshed}


def _schedule_activation_services_restart() -> bool:
    command = (
        "sleep 1; "
        "systemctl restart aiassistance-daemon.service >/dev/null 2>&1 || true; "
        "systemctl restart usb-proxy.service >/dev/null 2>&1 || true; "
        "systemctl restart aiassistance-web.service >/dev/null 2>&1 || true"
    )
    try:
        subprocess.Popen(
            ["sh", "-c", command],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
        return True
    except Exception:
        return False


def _restart_activation_services_without_identity_reset(reason: str = "") -> dict[str, Any]:
    scheduled = _schedule_activation_services_restart()
    message = (
        "授权服务无响应，已安排服务重启；本地授权文件已保留，请稍后刷新页面"
        if scheduled
        else "授权服务无响应，且自动重启服务失败；请手动重启设备后再试"
    )
    try:
        marker = {
            "restart_at": int(time.time()),
            "reason": str(reason or ""),
            "source": "activation_recovery",
            "identity_reset": False,
            "restart_scheduled": scheduled,
        }
        (LICENSE_DIR / "last_activation_service_restart.json").write_text(
            json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    return {
        "reset": False,
        "restart_scheduled": scheduled,
        "message": message,
        "reason": str(reason or ""),
    }


def _reset_local_activation_identity(reason: str = "") -> dict[str, Any]:
    global _activation_identity_reset_last_attempt
    with _activation_identity_reset_lock:
        now = time.monotonic()
        if (
            _activation_identity_reset_last_attempt
            and now - _activation_identity_reset_last_attempt < _ACTIVATION_IDENTITY_RESET_RETRY_SEC
        ):
            return {
                "reset": False,
                "throttled": True,
                "restart_scheduled": True,
                "message": "授权身份重置已触发，请等待服务重启后刷新页面",
            }
        _activation_identity_reset_last_attempt = now

    LICENSE_DIR.mkdir(parents=True, exist_ok=True)
    removed: list[str] = []
    for path in (
        LICENSE_PATH,
        DEVICE_PATH,
        CORE_ACTIVATION_PATH,
        ONLINE_GRANT_PATH,
        REVOKED_PATH,
        LICENSE_DIR / "trial_lock.json",
        MODEL_KEY_PATH,
        MODEL_KEY_ACTIVATION_PATH,
        CORE_ENC_PATH,
        CORE_DIR / "libai_core.so",
        USB_PROXY_ACTIVATION_PATH,
        USB_PROXY_ENC_PATH,
        theme_manager.entitlements_path,
        theme_manager.state_path,
    ):
        try:
            if path.exists() or path.is_symlink():
                path.unlink(missing_ok=True)
                removed.append(str(path))
        except OSError:
            continue
    try:
        for temp_core in RUN_DIR.glob("libai_core_*.so"):
            temp_core.unlink(missing_ok=True)
            removed.append(str(temp_core))
    except OSError:
        pass
    shutil.rmtree("/run/usb-proxy", ignore_errors=True)
    shutil.rmtree("/run/orangepi-mouse-passthrough", ignore_errors=True)
    try:
        marker = {
            "reset_at": int(time.time()),
            "reason": str(reason or ""),
            "removed_count": len(removed),
            "source": "activation_recovery",
        }
        (LICENSE_DIR / "last_activation_identity_reset.json").write_text(
            json.dumps(marker, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    restart_scheduled = _schedule_activation_services_restart()
    return {
        "reset": True,
        "restart_scheduled": restart_scheduled,
        "message": "已清理本地授权和硬件身份，服务正在重启；请稍后刷新页面并重新输入激活码",
        "removed_count": len(removed),
    }


def _activation_identity_reset_allowed() -> tuple[bool, str]:
    try:
        payload = _with_current_version(daemon_call("get_license", timeout=2.0))
    except DaemonError as exc:
        if _is_transient_daemon_socket_busy(exc):
            return False, str(exc)
        return False, str(exc)
    license_state = payload.get("license") if isinstance(payload.get("license"), dict) else {}
    if not license_state.get("valid"):
        return True, str(license_state.get("message") or "设备未激活")
    return False, "当前授权状态正常，已拒绝重置本地授权身份"


def _activation_full_recovery_allowed() -> tuple[bool, str]:
    try:
        payload = _with_current_version(daemon_call("get_license", timeout=2.0))
    except DaemonError as exc:
        return True, str(exc)
    license_state = payload.get("license") if isinstance(payload.get("license"), dict) else {}
    if not license_state.get("valid"):
        return True, str(license_state.get("message") or "设备未激活")
    return False, "当前授权和 daemon 状态正常，已拒绝本地全量恢复"


def _public_recovery_status(payload: dict[str, Any], *, allowed: bool, reason: str) -> dict[str, Any]:
    return {
        "available": bool(payload.get("available")),
        "allowed": bool(allowed),
        "version": str(payload.get("version") or ""),
        "size": int(payload.get("size") or 0),
        "saved_at": str(payload.get("saved_at") or ""),
        "source": str(payload.get("source") or ""),
        "reason": str(reason or ""),
    }


def _device_needs_license_recovery(payload: dict[str, Any]) -> bool:
    license_state = payload.get("license") if isinstance(payload.get("license"), dict) else {}
    core_state = payload.get("core") if isinstance(payload.get("core"), dict) else {}
    core_message = str(core_state.get("message") or "")
    status_values = {
        str(license_state.get("status") or ""),
        str(core_state.get("status") or ""),
    }
    return (
        "device_mismatch" in status_values
        or "abi_mismatch" in status_values
        or "不属于当前设备" in str(license_state.get("message") or core_message)
        or "ABI 版本不匹配" in core_message
    )


def _core_device_mismatch(payload: dict[str, Any]) -> bool:
    core_state = payload.get("core") if isinstance(payload.get("core"), dict) else {}
    status = str(core_state.get("status") or "")
    message = str(core_state.get("message") or "")
    return status == "device_mismatch" or "不属于当前设备" in message


def _update_plan_has_installable_update(plan: dict[str, Any]) -> bool:
    package = plan.get("package") if isinstance(plan.get("package"), dict) else {}
    if package.get("url"):
        return True
    components = plan.get("components") if isinstance(plan.get("components"), dict) else {}
    for name in ("core", "usb_proxy"):
        component = components.get(name) if isinstance(components.get(name), dict) else {}
        component_package = component.get("package") if isinstance(component.get("package"), dict) else {}
        if component.get("update_available") and component_package:
            return True
    return False


def _write_auto_core_update_failure(message: str, *, started_at: int | None = None) -> None:
    _write_update_status(_update_status_payload(
        status="failed",
        stage="failed",
        message="自动修复更新失败",
        progress=100,
        error=message,
        started_at=started_at,
        completed_at=int(time.time()),
        extra={
            "automatic": True,
            "reason": "core_device_mismatch",
        },
    ))


def _auto_core_update_worker(license_payload: dict[str, Any]) -> None:
    global _auto_core_update_last_error
    started_at = int(time.time())
    try:
        _write_update_status(_update_status_payload(
            status="running",
            stage="check",
            message="核心授权不匹配，正在自动检查更新",
            progress=2,
            started_at=started_at,
            extra={
                "automatic": True,
                "reason": "core_device_mismatch",
            },
        ))
        server_url = _license_server_url()
        request_payload = _update_server_request_payload(license_payload, {"prefer_full": True})
        response = _json_post(_license_url(server_url, "check-update"), request_payload, timeout=30)
        response = _xcsh_filter_update_versions(response)
        LICENSE_SERVER_URL_PATH.write_text(server_url + "\n", encoding="utf-8")
        online_grant = response.get("online_grant")
        if isinstance(online_grant, dict):
            ONLINE_GRANT_PATH.write_text(json.dumps(online_grant, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _write_last_update_check(response)
        if not _update_plan_has_installable_update(response):
            raise DaemonError("核心授权不匹配，但服务器未提供可安装的修复更新")
        _install_update_plan(response, started_at=started_at, automatic=True, reason="core_device_mismatch")
        with _auto_core_update_lock:
            _auto_core_update_last_error = ""
    except LicenseServerError as exc:
        if exc.payload.get("revoked"):
            try:
                _apply_license_revocation(exc.payload, server_url if "server_url" in locals() else "")
            except DaemonError:
                pass
        message = str(exc)
        with _auto_core_update_lock:
            _auto_core_update_last_error = message
        _write_auto_core_update_failure(message, started_at=started_at)
    except DaemonError as exc:
        message = str(exc)
        with _auto_core_update_lock:
            _auto_core_update_last_error = message
        _write_auto_core_update_failure(message, started_at=started_at)
    except Exception as exc:
        message = str(exc)
        with _auto_core_update_lock:
            _auto_core_update_last_error = message
        _write_auto_core_update_failure(message, started_at=started_at)


def _schedule_auto_core_update(payload: dict[str, Any], trigger_message: str = "") -> dict[str, Any]:
    if not _core_device_mismatch(payload):
        return {"scheduled": False, "message": ""}
    global _auto_core_update_thread, _auto_core_update_last_attempt

    running_units: list[str] = []
    try:
        running_units = _running_update_units()
    except DaemonError:
        running_units = []
    if running_units:
        return {
            "scheduled": False,
            "already_running": True,
            "message": "已存在更新安装任务，等待当前任务完成",
            "units": running_units,
        }

    current_status = _read_update_status()
    status_updated_at = int(current_status.get("updated_at") or 0)
    if (
        current_status.get("status") == "running"
        and status_updated_at
        and int(time.time()) - status_updated_at < _AUTO_CORE_UPDATE_RETRY_SEC
    ):
        return {
            "scheduled": False,
            "already_running": True,
            "message": "自动修复更新任务正在运行",
            "status": current_status,
        }

    with _auto_core_update_lock:
        if _auto_core_update_thread is not None and _auto_core_update_thread.is_alive():
            return {
                "scheduled": False,
                "already_running": True,
                "message": "自动修复更新任务正在运行",
            }
        now = time.monotonic()
        if (
            _auto_core_update_last_attempt
            and now - _auto_core_update_last_attempt < _AUTO_CORE_UPDATE_RETRY_SEC
        ):
            return {
                "scheduled": False,
                "throttled": True,
                "message": _auto_core_update_last_error or "自动修复更新已尝试，请稍后再试",
            }
        _auto_core_update_last_attempt = now
        worker_payload = json.loads(json.dumps(payload, ensure_ascii=False))
        _auto_core_update_thread = threading.Thread(
            target=_auto_core_update_worker,
            args=(worker_payload,),
            name="auto-core-update",
            daemon=True,
        )
        _auto_core_update_thread.start()

    return {
        "scheduled": True,
        "message": "已自动检查更新并尝试安装核心修复包",
        "trigger": trigger_message,
    }


def _license_recovery_failure(payload: dict[str, Any], message: str) -> tuple[dict[str, Any], bool, str]:
    global _license_recovery_last_error
    recovery: dict[str, Any] = {
        "recovered": False,
        "message": message,
    }
    if _core_device_mismatch(payload):
        auto_update = _schedule_auto_core_update(payload, message)
        recovery["auto_update"] = auto_update
        auto_update_message = str(auto_update.get("message") or "")
        if auto_update_message:
            message = f"{message}；{auto_update_message}"
            recovery["message"] = message
    with _license_recovery_lock:
        _license_recovery_last_error = message
    recovered_payload = dict(payload)
    recovered_payload["recovery"] = recovery
    return recovered_payload, False, message


def _save_activation_response(response: dict[str, Any], server_url: str) -> dict[str, Any]:
    license_doc = response.get("license")
    if not isinstance(license_doc, dict):
        raise DaemonError("activation response missing license")
    core = response.get("core")
    if not isinstance(core, dict):
        raise DaemonError("activation response missing core module")
    LICENSE_DIR.mkdir(parents=True, exist_ok=True)
    REVOKED_PATH.unlink(missing_ok=True)
    (LICENSE_DIR / "trial_lock.json").unlink(missing_ok=True)
    LICENSE_PATH.write_text(json.dumps(license_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        LICENSE_PATH.chmod(0o600)
    except OSError:
        pass
    LICENSE_SERVER_URL_PATH.write_text(server_url + "\n", encoding="utf-8")
    online_grant = response.get("online_grant")
    if license_doc.get("plan") == "trial":
        if not isinstance(online_grant, dict):
            raise DaemonError("trial activation response missing online grant")
        ONLINE_GRANT_PATH.write_text(json.dumps(online_grant, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        ONLINE_GRANT_PATH.unlink(missing_ok=True)
    model_key_activation = _install_model_key_package(response.get("model_key", {}))
    core_activation = _install_core_package(core, server_url)
    usb_proxy_activation = _install_usb_proxy_package(response.get("usb_proxy", {}), server_url)
    if usb_proxy_activation:
        _restart_usb_proxy_service()
    refreshed = daemon_call("refresh_license")
    if not refreshed.get("valid"):
        license_state = refreshed.get("license") if isinstance(refreshed.get("license"), dict) else refreshed
        raise DaemonError(license_state.get("message") or "activation license was saved but did not validate")
    refreshed_license = refreshed.get("license", refreshed)
    refreshed_core = refreshed.get("core", {})
    if not isinstance(refreshed_core, dict) or not refreshed_core.get("loaded"):
        raise DaemonError(refreshed_core.get("message") or "core module was saved but did not load")
    _write_device_recovery(_read_json_object(DEVICE_PATH), license_doc)
    return _with_current_version({
        "license": refreshed_license,
        "core": refreshed_core,
        "core_activation": {
            "version": core_activation.get("version", ""),
            "sha256": core_activation.get("sha256", ""),
            "size": core_activation.get("size", 0),
        },
        "usb_proxy_activation": {
            "version": usb_proxy_activation.get("version", ""),
            "sha256": usb_proxy_activation.get("sha256", ""),
            "size": usb_proxy_activation.get("size", 0),
        } if usb_proxy_activation else {},
        "model_key_activation": {
            "format": model_key_activation.get("format", ""),
            "sha256": model_key_activation.get("sha256", ""),
            "size": model_key_activation.get("size", 0),
        },
        "version": response.get("version"),
        "update": response.get("update"),
    })


def _post_activation_request(server_url: str, license_key: str, license_payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    identity_error = _device_identity_error(license_payload)
    if identity_error:
        raise DaemonError(identity_error, status_code=503)
    device = license_payload.get("device") if isinstance(license_payload.get("device"), dict) else {}
    device = _merge_recovery_into_device(device)
    response = _json_post(_license_url(server_url, "activate"), {
        "license_key": license_key,
        "device": device,
        "device_id": device.get("device_id", ""),
        "device_fingerprint_hash": device.get("fingerprint_hash", ""),
        "model_key": _local_model_key_package(),
        "component_capabilities": {"usb_proxy_formats": ["aiusbproxy1", "aiusbproxy2"]},
        "version": license_payload.get("version", ""),
    })
    return response, device


def _post_license_repair_request(server_url: str, license_payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    identity_error = _device_identity_error(license_payload)
    if identity_error:
        raise DaemonError(identity_error, status_code=503)
    device = license_payload.get("device") if isinstance(license_payload.get("device"), dict) else {}
    device = _merge_recovery_into_device(device)
    license_doc = _read_json_object(LICENSE_PATH)
    response = _json_post(_license_url(server_url, "repair"), {
        "license": license_doc,
        "device": device,
        "device_id": device.get("device_id", ""),
        "device_fingerprint_hash": device.get("fingerprint_hash", ""),
        "model_key": _local_model_key_package(),
        "component_capabilities": {"usb_proxy_formats": ["aiusbproxy1", "aiusbproxy2"]},
        "version": license_payload.get("version", ""),
    })
    return response, device


def _device_identity_error(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return "正在准备设备身份，请稍后刷新或重启设备"
    license_state = payload.get("license") if isinstance(payload.get("license"), dict) else {}
    device = payload.get("device") if isinstance(payload.get("device"), dict) else {}
    if str(license_state.get("status") or "") == "device_identity_pending":
        return str(license_state.get("message") or "正在准备设备身份，请稍后刷新或重启设备")
    device_id = str(device.get("device_id") or license_state.get("device_id") or "").strip()
    fingerprint = str(device.get("fingerprint_hash") or license_state.get("device_fingerprint_hash") or "").strip()
    if not device_id or not fingerprint:
        return "正在准备设备身份，请稍后刷新或重启设备"
    return ""


def _auto_recover_license_if_needed(payload: dict[str, Any], *, force: bool = False) -> tuple[dict[str, Any], bool, str]:
    if not _device_needs_license_recovery(payload):
        return payload, False, ""
    global _license_recovery_last_attempt, _license_recovery_last_error
    with _license_recovery_lock:
        now = time.monotonic()
        if not force and _license_recovery_last_attempt and now - _license_recovery_last_attempt < _LICENSE_RECOVERY_RETRY_SEC:
            return payload, False, _license_recovery_last_error
        _license_recovery_last_attempt = now
        _license_recovery_last_error = ""
    try:
        server_url = _license_server_url()
        try:
            response, repaired_device = _post_license_repair_request(server_url, payload)
            saved = _save_activation_response(response, server_url)
            _write_device_recovery(repaired_device, saved.get("license") if isinstance(saved.get("license"), dict) else None)
            refreshed = _with_current_version(daemon_call("get_license"))
            refreshed["auto_recovered"] = True
            refreshed["recovery"] = {
                "recovered": True,
                "method": "repair",
                "message": "已自动修复当前设备授权",
                "license": saved.get("license"),
            }
            with _license_recovery_lock:
                _license_recovery_last_error = ""
            return refreshed, True, ""
        except LicenseServerError as exc:
            if exc.payload.get("revoked"):
                result = _apply_license_revocation(exc.payload, server_url)
                refreshed = _with_current_version(daemon_call("get_license"))
                refreshed["recovery"] = {
                    "recovered": False,
                    "message": str(exc),
                    "revoked": result.get("revoked", {}),
                }
                with _license_recovery_lock:
                    _license_recovery_last_error = str(exc)
                return refreshed, False, str(exc)
            repair_error = str(exc)
        except DaemonError as exc:
            repair_error = str(exc)
        device = payload.get("device") if isinstance(payload.get("device"), dict) else {}
        license_key = _read_cached_license_key(device)
        if not license_key:
            raise DaemonError(f"{repair_error}；本机没有可用于自动恢复的历史卡密")
        response, device = _post_activation_request(server_url, license_key, payload)
        saved = _save_activation_response(response, server_url)
        _store_cached_license_key(
            license_key,
            device,
            saved.get("ui_brand") or (saved.get("license") if isinstance(saved.get("license"), dict) else {}).get("ui_brand"),
        )
        refreshed = _with_current_version(daemon_call("get_license"))
        refreshed["auto_recovered"] = True
        refreshed["recovery"] = {
            "recovered": True,
            "method": "cached_key",
            "message": "已自动修复当前设备授权",
            "license": saved.get("license"),
        }
        with _license_recovery_lock:
            _license_recovery_last_error = ""
        return refreshed, True, ""
    except LicenseServerError as exc:
        if exc.payload.get("revoked"):
            result = _apply_license_revocation(exc.payload, server_url if "server_url" in locals() else "")
            refreshed = _with_current_version(daemon_call("get_license"))
            refreshed["recovery"] = {
                "recovered": False,
                "message": str(exc),
                "revoked": result.get("revoked", {}),
            }
            with _license_recovery_lock:
                _license_recovery_last_error = str(exc)
            return refreshed, False, str(exc)
        return _license_recovery_failure(payload, str(exc))
    except DaemonError as exc:
        return _license_recovery_failure(payload, str(exc))


def _state_with_license_recovery(command: str) -> dict[str, Any]:
    timeout = CONTROL_START_DAEMON_TIMEOUT_SEC if command == "get_license" else 8.0
    payload = _with_current_version(daemon_call(command, timeout=timeout))
    if command == "get_state":
        license_payload = {
            "license": payload.get("state", {}).get("license") if isinstance(payload.get("state"), dict) else {},
            "core": payload.get("state", {}).get("core") if isinstance(payload.get("state"), dict) else {},
            "device": _read_json_object(DEVICE_PATH),
            "version": payload.get("version", ""),
        }
        if isinstance(license_payload.get("license"), dict):
            _write_device_recovery(license_payload.get("device"), license_payload.get("license"))
        recovered_license, recovered, message = _auto_recover_license_if_needed(license_payload)
        if recovered:
            payload = _with_current_version(daemon_call(command))
            payload["auto_recovered"] = True
            payload["recovery"] = recovered_license.get("recovery", {})
        elif message:
            payload["recovery"] = {"recovered": False, "message": message}
        return payload
    if isinstance(payload.get("device"), dict) and isinstance(payload.get("license"), dict):
        _write_device_recovery(payload.get("device"), payload.get("license"))
    recovered_payload, recovered, _message = _auto_recover_license_if_needed(payload)
    if recovered:
        return recovered_payload
    return recovered_payload


def _page_license_is_valid() -> bool:
    try:
        payload = _state_with_license_recovery("get_license")
    except DaemonError:
        return False
    license_state = payload.get("license") if isinstance(payload.get("license"), dict) else {}
    return bool(license_state.get("valid"))


def _render_activation_template():
    response = make_response(render_template("activate.html", **_template_context()))
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


def _prepare_activation_network() -> dict[str, Any]:
    status = wifi_status(force_scan=False)
    ap = status.get("ap") if isinstance(status.get("ap"), dict) else {}
    if status.get("mode") == "ap" or ap.get("active"):
        next_status = activate_client_wifi()
        return {
            "attempted": True,
            "changed": True,
            "status": next_status,
        }
    return {
        "attempted": True,
        "changed": False,
        "status": status,
    }


def _render_control_template(template: str):
    if request.args.get("activation") == "1":
        return _render_activation_template()
    if not _page_license_is_valid():
        return _render_activation_template()
    context = _template_context()
    template = _brand_template_name(template, str(context.get("ui_brand") or "yu"))
    return render_template(template, **context)


def _run_updater(*args: str) -> dict[str, Any]:
    script = ROOT_DIR / "scripts" / "aiassistance_updater.py"
    if not script.exists():
        raise DaemonError("updater script is not installed")
    command = [str(script), "--root", str(ROOT_DIR), *args]
    completed = subprocess.run(command, text=True, capture_output=True, timeout=300)
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "").strip()[-1600:]
        raise DaemonError(details or f"updater failed with exit code {completed.returncode}")
    try:
        return json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise DaemonError("updater returned invalid JSON") from exc


def _schedule_updater(*args: str) -> dict[str, Any]:
    script = ROOT_DIR / "scripts" / "aiassistance_updater.py"
    if not script.exists():
        raise DaemonError("updater script is not installed")
    unit_name = f"aiassistance-update-{int(time.time())}"
    command = [
        "systemd-run",
        "--unit", unit_name,
        "--collect",
        "--on-active=2s",
        str(script),
        "--root", str(ROOT_DIR),
        *args,
    ]
    completed = subprocess.run(command, text=True, capture_output=True, timeout=15)
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "").strip()[-1600:]
        raise DaemonError(details or f"failed to schedule updater with exit code {completed.returncode}")
    return {
        "scheduled": True,
        "unit": f"{unit_name}.service",
        "message": (completed.stdout or completed.stderr or "").strip(),
    }


@app.get("/")
def index():
    view = request.args.get("view", "").lower()
    if view == "desktop":
        return _render_control_template("index.html")
    if view == "mobile":
        return _render_control_template("mobile.html")

    user_agent = request.headers.get("User-Agent", "").lower()
    mobile_tokens = ("android", "iphone", "ipad", "ipod", "mobile")
    template = "mobile.html" if any(token in user_agent for token in mobile_tokens) else "index.html"
    return _render_control_template(template)


@app.get("/api/health/frontend")
def frontend_health():
    context = _template_context()
    ui_brand = str(context.get("ui_brand") or "yu")
    render_template(_brand_template_name("index.html", ui_brand), **context)
    render_template(_brand_template_name("mobile.html", ui_brand), **context)
    return api_ok({"rendered": True})


@app.get("/desktop")
def desktop():
    return _render_control_template("index.html")


@app.get("/mobile")
def mobile():
    return _render_control_template("mobile.html")


@app.get("/api/state")
def get_state():
    try:
        return _api_ok_public(_state_with_license_recovery("get_state"))
    except DaemonError as exc:
        return api_error(str(exc), 503)


@app.get("/api/license")
def get_license():
    try:
        return _api_ok_public(_state_with_license_recovery("get_license"))
    except DaemonError as exc:
        return api_error(str(exc), 503)


@app.post("/api/activation/network/prepare")
def prepare_activation_network():
    try:
        return api_ok(_prepare_activation_network())
    except WifiError as exc:
        return api_error(str(exc), data=exc.payload)


@app.post("/api/activation/reset-local-identity")
def reset_activation_local_identity():
    allowed, reason = _activation_identity_reset_allowed()
    if not allowed:
        if _is_transient_daemon_socket_busy(reason):
            return api_ok(_restart_activation_services_without_identity_reset(reason))
        return api_error(reason, status=409)
    result = _reset_local_activation_identity(reason)
    return api_ok(result)


@app.get("/api/activation/full-recovery")
def get_activation_full_recovery():
    allowed, reason = _activation_full_recovery_allowed()
    try:
        status = _run_updater("recovery-status")
    except DaemonError:
        status = {"available": False}
    return api_ok(_public_recovery_status(status, allowed=allowed, reason=reason))


@app.post("/api/activation/full-recovery")
def start_activation_full_recovery():
    global _activation_full_recovery_last_attempt
    if not request.is_json:
        return api_error("本地全量恢复需要 JSON 确认", status=415)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return api_error("本地全量恢复确认格式无效", status=400)
    if body.get("confirm") != "restore-local-full-ota":
        return api_error("请确认执行本地全量恢复", status=400)

    allowed, reason = _activation_full_recovery_allowed()
    if not allowed:
        return api_error(reason, status=409)
    try:
        running_units = _running_update_units()
    except DaemonError as exc:
        return api_error(str(exc), status=409)
    if running_units:
        return api_error("已有更新或恢复任务正在运行，请稍后重试", status=409)

    with _activation_full_recovery_lock:
        now = time.monotonic()
        if (
            _activation_full_recovery_last_attempt
            and now - _activation_full_recovery_last_attempt < _ACTIVATION_FULL_RECOVERY_RETRY_SEC
        ):
            return api_error("本地全量恢复任务已触发，请等待服务重启", status=409)
        try:
            status = _run_updater("recovery-status")
        except DaemonError as exc:
            return api_error(str(exc), status=409)
        if not status.get("available"):
            return api_error("设备上没有通过校验的本地全量恢复包", status=409)

        version = str(status.get("version") or "")
        started_at = int(time.time())
        _write_update_status(_update_status_payload(
            status="running",
            stage="recovery_scheduled",
            message="本地全量恢复任务已安排，服务即将重启",
            progress=3,
            version=version,
            package_type="recovery",
            started_at=started_at,
            extra={"recovery": True},
        ))
        try:
            scheduled = _schedule_updater("recover")
        except DaemonError as exc:
            _write_update_status(_update_status_payload(
                status="failed",
                stage="recovery_schedule_failed",
                message="本地全量恢复任务启动失败",
                progress=100,
                version=version,
                package_type="recovery",
                error=str(exc),
                started_at=started_at,
                completed_at=int(time.time()),
                extra={"recovery": True},
            ))
            return api_error(str(exc), status=500)
        _activation_full_recovery_last_attempt = now

    return api_ok({
        **scheduled,
        "version": version,
        "recovery": True,
        "message": "本地全量恢复已启动，完成后请重新输入激活码",
    })


@app.get("/api/announcement")
def get_announcement():
    try:
        server_url = _license_server_url()
        query = urllib.parse.urlencode({"ui_brand": _current_ui_brand()})
        announcement = _json_get(f"{_license_url(server_url, 'announcement')}?{query}")
        return _api_ok_public({
            "enabled": bool(announcement.get("enabled")),
            "title": str(announcement.get("title") or ""),
            "content": str(announcement.get("content") or ""),
            "version": str(announcement.get("version") or ""),
            "updated_at": str(announcement.get("updated_at") or ""),
        })
    except DaemonError as exc:
        return api_error(str(exc), 503)


def _yu_theme_license() -> tuple[dict[str, Any], dict[str, Any]]:
    if _current_ui_brand() != "yu":
        raise ThemeError("主题商店仅对 YU 系统开放")
    payload = _state_with_license_recovery("get_license")
    license_state = payload.get("license") if isinstance(payload.get("license"), dict) else {}
    if not license_state.get("valid") or not license_state.get("license_id"):
        raise ThemeError("设备激活后才能使用主题商店")
    return payload, license_state


def _theme_server_payload(license_payload: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = _update_server_request_payload(license_payload, {})
    payload.update(extra or {})
    return payload


def _sync_theme_catalog(license_payload: dict[str, Any], license_state: dict[str, Any]) -> dict[str, Any]:
    server_url = _license_server_url()
    catalog = _json_post(
        _license_url(server_url, "themes/catalog"),
        _theme_server_payload(license_payload),
    )
    return theme_manager.apply_catalog(
        catalog,
        str(license_state.get("license_id") or ""),
        _current_app_version(""),
    )


def _public_theme_state(state: dict[str, Any]) -> dict[str, Any]:
    public = json.loads(json.dumps(state, ensure_ascii=False))
    for theme in public.get("themes", []):
        theme_id = str(theme.get("id") or "")
        previews = theme.get("previews") if isinstance(theme.get("previews"), list) else []
        theme["previews"] = [
            {
                "label": str(item.get("label") or "预览"),
                "url": url_for("theme_preview", theme_id=theme_id, index=index),
            }
            for index, item in enumerate(previews)
            if isinstance(item, dict)
        ]
    return public


@app.get("/api/themes")
def get_themes():
    try:
        license_payload, license_state = _yu_theme_license()
        try:
            state = _sync_theme_catalog(license_payload, license_state)
        except DaemonError as exc:
            state = theme_manager.public_state(
                None,
                str(license_state.get("license_id") or ""),
                _current_app_version(""),
            )
            state["sync_error"] = str(exc)
        return api_ok(_public_theme_state(state))
    except (DaemonError, ThemeError) as exc:
        return api_error(str(exc), status=403)


@app.get("/api/themes/<theme_id>/previews/<int:index>")
def theme_preview(theme_id: str, index: int):
    try:
        _license_payload, _license_state = _yu_theme_license()
        catalog = theme_manager.cached_catalog()
        theme = next((item for item in catalog.get("themes", []) if item.get("id") == theme_id), None)
        previews = theme.get("previews") if isinstance(theme, dict) and isinstance(theme.get("previews"), list) else []
        if index < 0 or index >= len(previews):
            return api_error("主题预览不存在", status=404)
        source_url = str(previews[index].get("url") or "")
        if not source_url.startswith(("https://", "http://")):
            return api_error("主题预览地址无效", status=404)
        request_obj = urllib.request.Request(source_url, headers={"User-Agent": "aiAssistance-theme-preview/1.0"})
        with urllib.request.urlopen(request_obj, timeout=20) as response:
            content_type = str(response.headers.get("Content-Type") or "image/jpeg").split(";", 1)[0]
            if content_type not in {"image/png", "image/jpeg", "image/webp"}:
                raise ThemeError("主题预览格式无效")
            image = response.read(5 * 1024 * 1024 + 1)
        if len(image) > 5 * 1024 * 1024:
            raise ThemeError("主题预览文件过大")
        return Response(image, mimetype=content_type, headers={"Cache-Control": "public, max-age=3600"})
    except (DaemonError, ThemeError, urllib.error.URLError, OSError) as exc:
        return api_error(str(exc), status=404)


@app.post("/api/themes/redeem")
def redeem_theme():
    body = request.get_json(silent=True) or {}
    theme_id = str(body.get("theme_id") or "").strip()
    theme_key = str(body.get("theme_key") or "").strip()
    if not theme_id or not theme_key:
        return api_error("请输入主题卡密")
    try:
        license_payload, license_state = _yu_theme_license()
        server_url = _license_server_url()
        redemption = _json_post(
            _license_url(server_url, "themes/redeem"),
            _theme_server_payload(license_payload, {"theme_id": theme_id, "theme_key": theme_key}),
        )
        try:
            _sync_theme_catalog(license_payload, license_state)
            package = redemption.get("package") if isinstance(redemption.get("package"), dict) else {}
            package["download_url"] = _absolute_download_url(server_url, str(package.get("download_url") or ""))
            installed = theme_manager.install_package(
                package,
                str(license_state.get("license_id") or ""),
                activate=True,
            )
        except (DaemonError, ThemeError) as exc:
            return api_error(str(exc), status=502, data={"redeemed": True, "theme_id": theme_id})
        state = theme_manager.public_state(
            theme_manager.cached_catalog(),
            str(license_state.get("license_id") or ""),
            _current_app_version(""),
        )
        return api_ok({"redeemed": True, "installed": installed, "state": _public_theme_state(state)})
    except (DaemonError, ThemeError) as exc:
        return api_error(str(exc), status=getattr(exc, "status_code", None) or 400)


@app.post("/api/themes/<theme_id>/install")
def install_theme(theme_id: str):
    try:
        license_payload, license_state = _yu_theme_license()
        server_url = _license_server_url()
        _sync_theme_catalog(license_payload, license_state)
        response = _json_post(
            _license_url(server_url, "themes/package"),
            _theme_server_payload(license_payload, {"theme_id": theme_id}),
        )
        package = response.get("package") if isinstance(response.get("package"), dict) else {}
        package["download_url"] = _absolute_download_url(server_url, str(package.get("download_url") or ""))
        active_theme_id = str(theme_manager.installed_for_update().get("active_theme_id") or "default")
        installed = theme_manager.install_package(
            package,
            str(license_state.get("license_id") or ""),
            activate=active_theme_id == theme_id,
        )
        return api_ok(installed)
    except (DaemonError, ThemeError) as exc:
        return api_error(str(exc), status=getattr(exc, "status_code", None) or 400)


@app.put("/api/themes/current")
def select_theme():
    body = request.get_json(silent=True) or {}
    try:
        _license_payload, license_state = _yu_theme_license()
        state = theme_manager.set_active(
            str(body.get("theme_id") or ""),
            str(license_state.get("license_id") or ""),
            _current_app_version(""),
        )
        return api_ok({"active_theme_id": state.get("active_theme_id"), "active_version": state.get("active_version")})
    except (DaemonError, ThemeError) as exc:
        return api_error(str(exc), status=getattr(exc, "status_code", None) or 400)


@app.get("/theme-assets/<theme_id>/<version>/<path:filename>")
def theme_asset(theme_id: str, version: str, filename: str):
    try:
        if _current_ui_brand() != "yu":
            raise ThemeError("主题资源不可用")
        target = theme_manager.asset_path(theme_id, version, filename, _read_json_object(LICENSE_PATH))
        response = send_file(target, conditional=True)
        response.headers["Cache-Control"] = "public, max-age=86400, immutable"
        return response
    except ThemeError as exc:
        return api_error(str(exc), status=404)


@app.post("/api/license/activate")
def activate_license():
    body = request.get_json(silent=True) or {}
    license_key = str(body.get("license_key", "")).strip()
    if not license_key:
        return api_error("license_key is required")
    try:
        server_url = _license_server_url()
        license_payload = _with_current_version(daemon_call("get_license"))
        response, device = _post_activation_request(server_url, license_key, license_payload)
        result = _save_activation_response(response, server_url)
        _store_cached_license_key(
            license_key,
            device,
            result.get("ui_brand") or (result.get("license") if isinstance(result.get("license"), dict) else {}).get("ui_brand"),
        )
        return _api_ok_public(result)
    except LicenseServerError as exc:
        if exc.payload.get("revoked"):
            result = _apply_license_revocation(exc.payload, server_url if "server_url" in locals() else "")
            return api_error(str(exc), data=result, status_code=403)
        return api_error(str(exc), status_code=exc.status_code or 400)
    except DaemonError as exc:
        if _is_transient_daemon_socket_busy(exc):
            restart_result = _restart_activation_services_without_identity_reset(str(exc))
            return api_error(restart_result.get("message") or str(exc), status=503, data={
                "activation_identity_reset": restart_result,
            })
        return api_error(str(exc), status_code=exc.status_code or 400)


@app.post("/api/update/check")
def check_update():
    body = request.get_json(silent=True) or {}
    try:
        server_url = _license_server_url()
        license_payload = _state_with_license_recovery("get_license")
        response = _json_post(_license_url(server_url, "check-update"), _update_server_request_payload(license_payload, body))
        response = _xcsh_filter_update_versions(response)
        _reconcile_release_version_from_update_check(response, license_payload)
        LICENSE_SERVER_URL_PATH.write_text(server_url + "\n", encoding="utf-8")
        online_grant = response.get("online_grant")
        if isinstance(online_grant, dict):
            ONLINE_GRANT_PATH.write_text(json.dumps(online_grant, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _write_last_update_check(response)
        return _api_ok_public(response)
    except LicenseServerError as exc:
        if exc.payload.get("revoked"):
            result = _apply_license_revocation(exc.payload, server_url if "server_url" in locals() else "")
            return api_error(str(exc), data=result, status_code=403)
        return api_error(str(exc), status_code=exc.status_code or 400)
    except DaemonError as exc:
        return api_error(str(exc))


@app.post("/api/update/versions")
def list_update_versions():
    body = request.get_json(silent=True) or {}
    try:
        server_url = _license_server_url()
        license_payload = _state_with_license_recovery("get_license")
        response = _json_post(_license_url(server_url, "update-versions"), _update_server_request_payload(license_payload, body))
        response = _xcsh_filter_update_versions(response)
        LICENSE_SERVER_URL_PATH.write_text(server_url + "\n", encoding="utf-8")
        online_grant = response.get("online_grant")
        if isinstance(online_grant, dict):
            ONLINE_GRANT_PATH.write_text(json.dumps(online_grant, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return _api_ok_public(response)
    except LicenseServerError as exc:
        if exc.payload.get("revoked"):
            result = _apply_license_revocation(exc.payload, server_url if "server_url" in locals() else "")
            return api_error(str(exc), data=result, status_code=403)
        return api_error(str(exc), status_code=exc.status_code or 400)
    except DaemonError as exc:
        return api_error(str(exc))


@app.get("/api/update/status")
def get_update_status():
    return api_ok(_read_update_status())


@app.post("/api/update/cleanup-stuck")
def cleanup_stuck_update():
    try:
        return api_ok(_cleanup_stuck_update_status())
    except DaemonError as exc:
        message = str(exc)
        return api_error(message, status=409 if "仍在运行" in message else 400)


def _install_update_plan(
    plan: dict[str, Any],
    *,
    started_at: int | None = None,
    automatic: bool = False,
    reason: str = "",
) -> dict[str, Any]:
    started_at = started_at if started_at is not None else int(time.time())
    package = plan.get("package") if isinstance(plan.get("package"), dict) else {}
    theme_updates = plan.get("theme_updates") if isinstance(plan.get("theme_updates"), list) else []
    theme_fallback = plan.get("theme_fallback") if isinstance(plan.get("theme_fallback"), dict) else {}
    theme_fallback_id = str(theme_fallback.get("theme_id") or "").strip().lower()
    if theme_fallback_id and not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,47}", theme_fallback_id):
        raise DaemonError("theme fallback id is invalid")
    target_version = str(plan.get("latest_version") or package.get("version") or "")
    _xcsh_reject_old_update_version(target_version)
    package_type = str(package.get("type") or "")

    def set_status(stage: str, message: str, progress: int, **extra: Any) -> None:
        status_extra = dict(extra)
        if automatic:
            status_extra["automatic"] = True
            status_extra["reason"] = reason
        _write_update_status(_update_status_payload(
            status="running",
            stage=stage,
            message=message,
            progress=progress,
            version=target_version,
            package_type=package_type,
            started_at=started_at,
            extra=status_extra or None,
        ))

    try:
        server_url = _license_server_url()
        license_payload, license_state = _yu_theme_license() if theme_updates else ({}, {})
        theme_transaction = f"update-{int(time.time())}-{os.getpid()}"
        set_status("prepare", "正在准备自动修复更新任务" if automatic else "正在准备更新任务", 3)

        def app_download_progress(downloaded: int, total: int) -> None:
            percent = int((downloaded / total) * 100) if total > 0 else 0
            progress = 8 + int(min(1.0, downloaded / total) * 28) if total > 0 else 18
            set_status(
                "download",
                f"正在下载应用更新包 {percent}%" if total > 0 else "正在下载应用更新包",
                progress,
                downloaded_bytes=downloaded,
                total_bytes=total,
            )

        package_path = _stage_application_package(package, app_download_progress) if package.get("url") else None
        if package_path is not None:
            set_status("verify", "应用更新包已下载，正在校验", 38)
        components = plan.get("components") if isinstance(plan.get("components"), dict) else {}
        staged_components: dict[str, Any] = {}
        component_names = [
            name
            for name in ("core", "usb_proxy")
            if isinstance(components.get(name), dict)
            and components.get(name, {}).get("update_available")
            and isinstance(components.get(name, {}).get("package"), dict)
        ]
        for component_name in ("core", "usb_proxy"):
            component = components.get(component_name) if isinstance(components.get(component_name), dict) else {}
            component_package = component.get("package") if isinstance(component.get("package"), dict) else {}
            if component.get("update_available") and component_package:
                component_index = component_names.index(component_name) if component_name in component_names else 0
                component_span = 18 / max(1, len(component_names))

                def component_download_progress(downloaded: int, total: int, *, name: str = component_name, index: int = component_index) -> None:
                    percent = int((downloaded / total) * 100) if total > 0 else 0
                    fraction = min(1.0, downloaded / total) if total > 0 else 0.5
                    progress = 40 + int((index + fraction) * component_span)
                    label = "核心模块" if name == "core" else "USB 组件"
                    set_status(
                        "download_components",
                        f"正在下载{label} {percent}%" if total > 0 else f"正在下载{label}",
                        progress,
                        downloaded_bytes=downloaded,
                        total_bytes=total,
                    )

                staged = _stage_component_package(component_name, component_package, server_url, component_download_progress)
                if staged:
                    staged_components[component_name] = staged
                    set_status("verify_components", f"{'核心模块' if component_name == 'core' else 'USB 组件'}已下载，正在校验", 58)
        staged_themes: list[dict[str, Any]] = []
        has_system_update = package_path is not None or bool(staged_components)
        for index, theme_update in enumerate(theme_updates):
            if not isinstance(theme_update, dict):
                continue
            expected_theme_id = str(theme_update.get("theme_id") or "")
            expected_theme_version = str(theme_update.get("version") or "")
            fresh_response = _json_post(
                _license_url(server_url, "themes/package"),
                _theme_server_payload(license_payload, {
                    "theme_id": expected_theme_id,
                    "app_version": str(package.get("app_version") or plan.get("target_app_version") or _current_app_version("")),
                }),
            )
            theme_package = fresh_response.get("package") if isinstance(fresh_response.get("package"), dict) else {}
            if (
                str(theme_package.get("theme_id") or "") != expected_theme_id
                or str(theme_package.get("version") or "") != expected_theme_version
            ):
                raise ThemeError("主题更新版本已变化，请重新检查更新")
            theme_package = dict(theme_package)
            theme_package["download_url"] = _absolute_download_url(server_url, str(theme_package.get("download_url") or ""))

            def theme_download_progress(downloaded: int, total: int, *, title: str = str(theme_update.get("title") or theme_update.get("theme_id") or "主题")) -> None:
                percent = int((downloaded / total) * 100) if total > 0 else 0
                set_status(
                    "download_themes",
                    f"正在下载主题 {title} {percent}%" if total > 0 else f"正在下载主题 {title}",
                    59 + min(3, index),
                    downloaded_bytes=downloaded,
                    total_bytes=total,
                )

            staged_themes.append(theme_manager.stage_package(
                theme_package,
                str(license_state.get("license_id") or ""),
                theme_transaction,
                theme_download_progress,
            ))
        if package_path is None and not staged_components and not staged_themes:
            raise DaemonError("no update package, components, or themes are available to install")
        if not has_system_update:
            installed_themes = theme_manager.promote_staged(staged_themes)
            _write_update_status(_update_status_payload(
                status="success",
                stage="complete",
                message="主题更新安装成功",
                progress=100,
                version=_current_app_version(""),
                package_type="themes",
                started_at=started_at,
                completed_at=int(time.time()),
            ))
            return {
                "scheduled": False,
                "theme_only": True,
                "themes": installed_themes,
                "version": _current_app_version(""),
                "type": "themes",
                "message": "主题更新安装成功",
            }
        metadata = {
            "package": package,
            "components": staged_components,
            "themes": staged_themes,
            "theme_fallback_to_default": theme_fallback_id,
            "version": str(plan.get("latest_version") or package.get("version") or ""),
            "app_version": str(package.get("app_version") or _current_app_version("")),
            "type": str(package.get("type") or ("components" if staged_components else "")),
            "sha256": str(package.get("sha256", "")).strip().lower(),
            "signature": str(package.get("signature", "")).strip(),
            "checked_at": int(time.time()),
            "automatic": bool(automatic),
            "reason": reason,
        }
        metadata_path = UPDATES_DIR / f"update-metadata-{int(time.time())}.json"
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        updater_args = ["install"]
        if package_path is not None:
            updater_args.append(str(package_path))
        updater_args.extend(["--metadata", str(metadata_path)])
        set_status("schedule", "更新文件已准备完成，正在启动安装服务", 62)
        result = _schedule_updater(*updater_args)
        result["version"] = str(metadata.get("version", ""))
        result["type"] = str(metadata.get("type", ""))
        unit = str(result.get("unit", ""))
        _write_update_status(_update_status_payload(
            status="running",
            stage="scheduled",
            message="安装服务已启动，正在等待系统接管更新",
            progress=65,
            version=target_version,
            package_type=package_type,
            unit=unit,
            started_at=started_at,
            extra={
                "automatic": True,
                "reason": reason,
            } if automatic else None,
        ))
        return result
    except (DaemonError, ThemeError) as exc:
        if "theme_transaction" in locals():
            theme_manager.cleanup_staging(theme_transaction)
        _write_update_status(_update_status_payload(
            status="failed",
            stage="failed",
            message="自动修复更新失败" if automatic else "更新失败",
            progress=100,
            version=target_version,
            package_type=package_type,
            error=str(exc),
            started_at=started_at,
            completed_at=int(time.time()),
            extra={
                "automatic": True,
                "reason": reason,
            } if automatic else None,
        ))
        if isinstance(exc, ThemeError):
            raise DaemonError(str(exc)) from exc
        raise


@app.post("/api/update/install")
def install_update():
    body = request.get_json(silent=True) or {}
    started_at = int(time.time())
    try:
        requested_plan = body.get("plan") if "plan" in body else body.get("package", body)
        plan = _resolve_update_plan(requested_plan)
        return api_ok(_install_update_plan(plan, started_at=started_at))
    except DaemonError as exc:
        return api_error(str(exc))


@app.get("/api/hailo/status")
def get_hailo_status():
    return api_ok(_hailo_status())


@app.post("/api/hailo/install")
def install_hailo_dependencies():
    try:
        status = _hailo_status()
        if not status.get("pcie", {}).get("present"):
            return api_error("未检测到 Hailo-8 PCIe 设备", data=status)
        if status.get("ready"):
            return api_ok({"started": False, "ready": True, "status": status})
        return api_ok(_start_hailo_install())
    except DaemonError as exc:
        return api_error(str(exc))


@app.get("/api/system")
def get_system_status():
    try:
        load_average = os.getloadavg()
    except OSError:
        load_average = None
    hostname = socket.gethostname()
    lan_ip = _lan_ipv4()
    urls = _network_urls(hostname, lan_ip)
    return api_ok({
        "cpu_percent": _cpu_percent(),
        "load_average": list(load_average) if load_average is not None else None,
        "memory": _memory_status(),
        "temperature": _cpu_temperature(),
        "storage": _storage_status(),
        "uptime_seconds": _uptime_seconds(),
        "hostname": hostname,
        "lan_ipv4": lan_ip,
        **urls,
    })


@app.get("/api/system/storage")
def get_storage_status():
    force = str(request.args.get("force", "")).strip().lower() in {"1", "true", "yes"}
    return api_ok(_storage_status(force_rootfs=force))


@app.get("/api/xcsh/background")
def get_xcsh_background():
    try:
        _require_xcsh_background_access()
        return api_ok(_xcsh_background_public_state())
    except DaemonError as exc:
        return api_error(str(exc), status_code=exc.status_code or 400)


@app.post("/api/xcsh/background")
def upload_xcsh_background():
    try:
        _require_xcsh_background_access()
        uploaded = request.files.get("file")
        if uploaded is None or uploaded.filename == "":
            raise DaemonError("请选择背景图片")
        data = uploaded.stream.read(XCSH_BACKGROUND_MAX_BYTES + 1)
        content_type, width, height = _validate_xcsh_background_image(data)
        overlay = _xcsh_background_overlay(
            request.form.get("overlay_opacity", XCSH_BACKGROUND_DEFAULT_OVERLAY),
            strict=True,
        )
        accent_value = request.form.get("accent_color")
        accent = _xcsh_background_accent(accent_value, strict=True) if accent_value is not None else None
        digest = hashlib.sha256(data).hexdigest()
        revision = digest[:16]
        filename = f"background-{revision}.{XCSH_BACKGROUND_TYPES[content_type]}"
        target = XCSH_BACKGROUND_DIR / filename

        with _xcsh_background_lock:
            previous = _xcsh_background_record()
            if accent_value is None:
                accent = _xcsh_background_accent(previous["settings"].get("accent_color"))
            settings = {
                "enabled": True,
                "filename": filename,
                "content_type": content_type,
                "width": width,
                "height": height,
                "size": len(data),
                "revision": revision,
                "overlay_opacity": overlay,
                "updated_at": int(time.time()),
            }
            if accent is not None:
                settings["accent_color"] = accent
            _write_bytes_atomic(target, data)
            try:
                _write_json_object_atomic(_xcsh_background_settings_path(), settings)
            except Exception:
                if filename != previous["filename"]:
                    target.unlink(missing_ok=True)
                raise
            for item in XCSH_BACKGROUND_DIR.glob("background-*"):
                if item.name != filename and item.is_file() and XCSH_BACKGROUND_FILENAME_RE.fullmatch(item.name):
                    item.unlink(missing_ok=True)
        return api_ok(_xcsh_background_public_state())
    except DaemonError as exc:
        return api_error(str(exc), status_code=exc.status_code or 400)
    except OSError as exc:
        return api_error(f"保存网页背景失败：{exc}", 500)


@app.patch("/api/xcsh/background")
def update_xcsh_background():
    try:
        _require_xcsh_background_access()
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise DaemonError("背景设置格式无效")
        recognized = {"enabled", "overlay_opacity", "accent_color"}
        if not recognized.intersection(body):
            raise DaemonError("没有可更新的背景设置")
        with _xcsh_background_lock:
            record = _xcsh_background_record()
            settings = dict(record["settings"])
            if "enabled" in body:
                if not isinstance(body["enabled"], bool):
                    raise DaemonError("背景启用状态无效")
                if body["enabled"] and not record["has_image"]:
                    raise DaemonError("请先上传背景图片")
                settings["enabled"] = body["enabled"]
            if "overlay_opacity" in body:
                settings["overlay_opacity"] = _xcsh_background_overlay(body["overlay_opacity"], strict=True)
            if "accent_color" in body:
                accent = _xcsh_background_accent(body["accent_color"], strict=True)
                if accent is None:
                    settings.pop("accent_color", None)
                else:
                    settings["accent_color"] = accent
            settings["updated_at"] = int(time.time())
            _write_json_object_atomic(_xcsh_background_settings_path(), settings)
        return api_ok(_xcsh_background_public_state())
    except DaemonError as exc:
        return api_error(str(exc), status_code=exc.status_code or 400)
    except OSError as exc:
        return api_error(f"保存网页背景设置失败：{exc}", 500)


@app.delete("/api/xcsh/background")
def delete_xcsh_background():
    try:
        _require_xcsh_background_access()
        with _xcsh_background_lock:
            record = _xcsh_background_record()
            accent = _xcsh_background_accent(record["settings"].get("accent_color"))
            remaining_settings: dict[str, Any] = {
                "enabled": False,
                "overlay_opacity": record["overlay_opacity"],
                "updated_at": int(time.time()),
            }
            if accent is not None:
                remaining_settings["accent_color"] = accent
                _write_json_object_atomic(_xcsh_background_settings_path(), remaining_settings)
            else:
                _xcsh_background_settings_path().unlink(missing_ok=True)
            if XCSH_BACKGROUND_DIR.exists():
                for item in XCSH_BACKGROUND_DIR.glob("background-*"):
                    if item.is_file() and XCSH_BACKGROUND_FILENAME_RE.fullmatch(item.name):
                        item.unlink(missing_ok=True)
        return api_ok(_xcsh_background_public_state())
    except DaemonError as exc:
        return api_error(str(exc), status_code=exc.status_code or 400)
    except OSError as exc:
        return api_error(f"恢复默认背景失败：{exc}", 500)


@app.get("/api/xcsh/background/image")
def get_xcsh_background_image():
    try:
        _require_xcsh_background_access()
        with _xcsh_background_lock:
            record = _xcsh_background_record()
            image_path = record["image_path"]
            if image_path is None:
                return api_error("尚未设置背景图片", 404)
            response = send_file(
                image_path,
                mimetype=record["content_type"],
                conditional=True,
                etag=record["revision"],
                max_age=0,
            )
        response.headers["Cache-Control"] = "private, no-cache, must-revalidate"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Disposition"] = "inline"
        return response
    except DaemonError as exc:
        return api_error(str(exc), status_code=exc.status_code or 400)
    except OSError as exc:
        return api_error(f"读取网页背景失败：{exc}", 500)


@app.post("/api/system/storage/expand")
def expand_storage():
    try:
        payload = _rootfs_expand_status("apply")
        storage = _storage_status(force_rootfs=True)
        storage["rootfs"] = payload
        return api_ok(storage)
    except ScriptError as exc:
        return api_error(str(exc), data=exc.payload or None)


@app.put("/api/system/hostname")
def update_system_hostname():
    body = request.get_json(silent=True) or {}
    try:
        hostname = _validate_lan_hostname(body.get("hostname"))
        _set_lan_hostname(hostname)
        lan_ip = _lan_ipv4()
        urls = _network_urls(hostname, lan_ip)
        return api_ok({
            "hostname": hostname,
            "lan_ipv4": lan_ip,
            **urls,
        })
    except DaemonError as exc:
        return api_error(str(exc))


@app.put("/api/system/web-port")
def update_system_web_port():
    body = request.get_json(silent=True) or {}
    try:
        port = _validate_web_port(body.get("port"))
        _set_web_port(port)
        hostname = socket.gethostname()
        lan_ip = _lan_ipv4()
        urls = _network_urls(hostname, lan_ip, port)
        _restart_web_service_delayed()
        return api_ok({
            "hostname": hostname,
            "lan_ipv4": lan_ip,
            "restart_scheduled": True,
            **urls,
        })
    except DaemonError as exc:
        return api_error(str(exc))


@app.get("/api/system/lan-blocklist")
def get_lan_blocklist():
    try:
        return api_ok(_run_lan_blocklist("status"))
    except ScriptError as exc:
        return api_error(str(exc), data=exc.payload or None)


@app.post("/api/system/lan-blocklist/scan")
def scan_lan_blocklist_devices():
    try:
        return api_ok(_run_lan_blocklist("scan"))
    except ScriptError as exc:
        return api_error(str(exc), data=exc.payload or None)


@app.post("/api/system/lan-blocklist")
def set_lan_blocklist():
    body = request.get_json(silent=True) or {}
    ip = str(body.get("ip", "")).strip()
    if not ip:
        return api_error("请选择或输入要拉黑的局域网 IP")
    if ip == _client_remote_ip():
        return api_error("不能拉黑当前正在访问页面的设备")
    try:
        payload = _run_lan_blocklist("set", ip)
        _lan_blocked_ips_cached(force=True)
        return api_ok(payload)
    except ScriptError as exc:
        return api_error(str(exc), data=exc.payload or None)


@app.delete("/api/system/lan-blocklist")
def clear_lan_blocklist():
    try:
        payload = _run_lan_blocklist("clear")
        _lan_blocked_ips_cached(force=True)
        return api_ok(payload)
    except ScriptError as exc:
        return api_error(str(exc), data=exc.payload or None)


@app.get("/api/network/wifi")
def get_wifi_status():
    return api_ok(wifi_status(force_scan=False))


@app.post("/api/network/wifi/scan")
def scan_wifi_networks():
    return api_ok(wifi_status(force_scan=True))


@app.post("/api/network/wifi/connect")
def connect_wifi_network():
    body = request.get_json(silent=True) or {}
    try:
        return api_ok(connect_wifi(str(body.get("ssid", "")), str(body.get("password", ""))))
    except WifiError as exc:
        return api_error(str(exc), data=exc.payload)


@app.post("/api/network/wifi/fallback")
def fallback_wifi_network():
    try:
        return api_ok(reset_to_default_wifi())
    except WifiError as exc:
        return api_error(str(exc), data=exc.payload)


@app.post("/api/network/wifi/ap/apply")
def apply_wifi_ap_hotspot():
    if not _page_license_is_valid():
        return api_error("设备未激活时禁止开启 AP 模式，请先连接 Wi-Fi 完成激活", status=403)
    body = request.get_json(silent=True) or {}
    try:
        return api_ok(apply_ap_hotspot(body.get("ssid"), body.get("password")))
    except WifiError as exc:
        return api_error(str(exc), data=exc.payload)


@app.post("/api/network/wifi/client/activate")
def activate_wifi_client_mode():
    try:
        return api_ok(activate_client_wifi())
    except WifiError as exc:
        return api_error(str(exc), data=exc.payload)


def _launch_system_action(action: str):
    command = ["systemctl", action]
    subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )
    return api_ok({"action": action, "scheduled": True})


@app.post("/api/system/reactivate")
def reactivate_device():
    try:
        license_payload = _with_current_version(daemon_call("refresh_device_identity"))
        identity_error = _device_identity_error(license_payload)
        if identity_error:
            return api_error(identity_error, status_code=503)
        repaired_payload, recovered, message = _auto_recover_license_if_needed(license_payload, force=True)
        if recovered:
            return _api_ok_public({
                "scheduled": False,
                "recovered": True,
                "message": "授权修复成功，请刷新页面后继续使用",
                "license": repaired_payload.get("license", {}),
                "core": repaired_payload.get("core", {}),
                "recovery": repaired_payload.get("recovery", {}),
            })
        if message:
            return api_error(message)
        return api_error("当前授权状态正常，无需修复授权")
    except DaemonError as exc:
        return api_error(str(exc))


@app.post("/api/system/master-reactivate")
def master_reactivate_device():
    script = ROOT_DIR / "scripts" / "prepare_master_image.sh"
    if not script.exists():
        return api_error("母盘清理脚本未安装，请先更新完整系统包")
    unit_name = f"aiassistance-reactivate-{int(time.time())}"
    command = [
        "systemd-run",
        "--unit", unit_name,
        "--collect",
        "--on-active=2s",
        str(script),
        "--yes",
        "--keep-models",
        "--keep-presets",
        "--keep-ssh-identity",
        "--reboot",
    ]
    completed = subprocess.run(command, text=True, capture_output=True, timeout=15)
    if completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "").strip()[-1600:]
        return api_error(details or "授权重置任务启动失败")
    return api_ok({
        "scheduled": True,
        "unit": f"{unit_name}.service",
        "message": "已安排母盘清理并重启，重启后请重新输入卡密激活。",
    })


@app.post("/api/system/reboot")
def reboot_system():
    return _launch_system_action("reboot")


@app.post("/api/system/poweroff")
def poweroff_system():
    return _launch_system_action("poweroff")


@app.get("/api/events")
def events():
    try:
        payload = json.dumps(_redact_public_payload(_with_current_version(daemon_call("get_state"))), ensure_ascii=False)
        event = f"event: state\ndata: {payload}\n\n"
    except DaemonError as exc:
        error_payload = json.dumps({"error": str(exc)}, ensure_ascii=False)
        event = f"event: error\ndata: {error_payload}\n\n"
    return Response(
        event,
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Connection": "close",
        },
    )


@app.put("/api/config")
def update_config():
    patch = request.get_json(silent=True)
    if not isinstance(patch, dict):
        return api_error("request body must be a JSON object")
    try:
        return api_ok(daemon_call("put_config", patch=patch))
    except DaemonError as exc:
        return api_error(str(exc))


@app.get("/api/motion-profiles")
def list_motion_profiles():
    try:
        return api_ok(daemon_call("list_motion_profiles"))
    except DaemonError as exc:
        return api_error(str(exc), 503)


@app.post("/api/motion-profiles")
def create_motion_profile():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
        return api_error("name must be a string")
    try:
        return api_ok(daemon_call("create_motion_profile", name=payload["name"]))
    except DaemonError as exc:
        return api_error(str(exc))


@app.patch("/api/motion-profiles/<profile_id>")
def rename_motion_profile(profile_id: str):
    if not MOTION_PROFILE_ID_PATTERN.fullmatch(profile_id):
        return api_error("profile_id is invalid")
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
        return api_error("name must be a string")
    try:
        return api_ok(daemon_call(
            "rename_motion_profile",
            profile_id=profile_id,
            name=payload["name"],
        ))
    except DaemonError as exc:
        return api_error(str(exc))


@app.delete("/api/motion-profiles/<profile_id>")
def delete_motion_profile(profile_id: str):
    if not MOTION_PROFILE_ID_PATTERN.fullmatch(profile_id):
        return api_error("profile_id is invalid")
    try:
        return api_ok(daemon_call("delete_motion_profile", profile_id=profile_id))
    except DaemonError as exc:
        return api_error(str(exc))


@app.get("/api/motion-profiles/<profile_id>/export")
def export_motion_profile(profile_id: str):
    if not MOTION_PROFILE_ID_PATTERN.fullmatch(profile_id):
        return api_error("profile_id is invalid")
    try:
        result = daemon_call("export_motion_profile", profile_id=profile_id)
    except DaemonError as exc:
        return api_error(str(exc))
    source = Path(str(result.get("path", ""))).resolve()
    profiles_root = (ROOT_DIR / "config" / "motion-profiles").resolve()
    if source.parent != profiles_root or not source.is_dir():
        return api_error("motion profile export path is invalid", 500)
    export_path = RUN_DIR / f"motion-profile-{profile_id}.zip"
    export_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(export_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in sorted(source.rglob("*")):
            if file_path.is_file():
                archive.write(file_path, Path(profile_id) / file_path.relative_to(source))
    return send_file(
        export_path,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"motion-profile-{profile_id}.zip",
        conditional=True,
    )


@app.post("/api/motion-training/sessions")
def start_motion_training_session():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("profile_id"), str):
        return api_error("profile_id must be a string")
    if not MOTION_PROFILE_ID_PATTERN.fullmatch(payload["profile_id"]):
        return api_error("profile_id is invalid")
    try:
        return api_ok(daemon_call(
            "start_motion_training_session",
            profile_id=payload["profile_id"],
        ))
    except DaemonError as exc:
        return api_error(str(exc), 409)


@app.put("/api/motion-training/sessions/<session_id>/heartbeat")
def heartbeat_motion_training_session(session_id: str):
    try:
        return api_ok(daemon_call(
            "heartbeat_motion_training_session",
            session_id=session_id,
        ))
    except DaemonError as exc:
        return api_error(str(exc), 409)


@app.post("/api/motion-training/sessions/<session_id>/samples")
def append_motion_training_sample(session_id: str):
    if request.content_length is not None and request.content_length > MOTION_SAMPLE_MAX_BYTES:
        return api_error("motion sample exceeds 256KB", 413)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return api_error("sample must be a JSON object")
    encoded_size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if encoded_size > MOTION_SAMPLE_MAX_BYTES:
        return api_error("motion sample exceeds 256KB", 413)
    try:
        return api_ok(daemon_call(
            "append_motion_training_sample",
            timeout=10.0,
            session_id=session_id,
            sample=payload,
        ))
    except DaemonError as exc:
        message = str(exc)
        if "motion training lease" in message.lower():
            return api_error(message, 409)
        if message.lower().startswith(("motion sample", "unsupported motion sample")):
            return api_error(message, 422)
        return api_error(message, 503)


@app.delete("/api/motion-training/sessions/<session_id>")
def stop_motion_training_session(session_id: str):
    try:
        return api_ok(daemon_call("stop_motion_training_session", session_id=session_id))
    except DaemonError as exc:
        return api_error(str(exc), 409)


@app.post("/api/motion-profiles/<profile_id>/train")
def train_motion_profile(profile_id: str):
    if not MOTION_PROFILE_ID_PATTERN.fullmatch(profile_id):
        return api_error("profile_id is invalid")
    try:
        return api_ok(daemon_call("train_motion_profile", timeout=30.0, profile_id=profile_id))
    except DaemonError as exc:
        return api_error(str(exc))


@app.post("/api/motion-profiles/<profile_id>/activate")
def activate_motion_profile(profile_id: str):
    if not MOTION_PROFILE_ID_PATTERN.fullmatch(profile_id):
        return api_error("profile_id is invalid")
    payload = request.get_json(silent=True)
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return api_error("request body must be a JSON object")
    values: dict[str, float | int | str] = {}
    if "preset_name" in payload:
        if not isinstance(payload["preset_name"], str):
            return api_error("preset_name must be a string")
        values["preset_name"] = payload["preset_name"]
    limits = {
        "curve_blend": (0.0, 1.0),
        "speed_blend": (0.0, 1.0),
        "reaction_blend": (0.0, 1.0),
        "max_reaction_delay_ms": (0, 1000),
    }
    for key, (minimum, maximum) in limits.items():
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            return api_error(f"{key} must be a finite number")
        if value < minimum or value > maximum:
            return api_error(f"{key} must be between {minimum} and {maximum}")
        values[key] = int(value) if key == "max_reaction_delay_ms" else float(value)
    try:
        return api_ok(daemon_call("activate_motion_profile", profile_id=profile_id, **values))
    except DaemonError as exc:
        return api_error(str(exc))


@app.delete("/api/motion-profiles/active")
def deactivate_motion_profile():
    payload = request.get_json(silent=True)
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return api_error("request body must be a JSON object")
    preset_name = payload.get("preset_name", "")
    if not isinstance(preset_name, str):
        return api_error("preset_name must be a string")
    try:
        values = {"preset_name": preset_name} if preset_name else {}
        return api_ok(daemon_call("deactivate_motion_profile", **values))
    except DaemonError as exc:
        return api_error(str(exc))


@app.delete("/api/motion-profiles/<profile_id>/samples")
def clear_motion_profile_samples(profile_id: str):
    if not MOTION_PROFILE_ID_PATTERN.fullmatch(profile_id):
        return api_error("profile_id is invalid")
    try:
        return api_ok(daemon_call("clear_motion_profile_samples", profile_id=profile_id))
    except DaemonError as exc:
        return api_error(str(exc))


@app.get("/api/control/calibration")
def get_auto_calibration():
    try:
        return api_ok(daemon_call("get_auto_calibration"))
    except DaemonError as exc:
        return api_error(str(exc), 503)


@app.put("/api/control/calibration")
def update_auto_calibration():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return api_error("request body must be a JSON object")

    limits = {
        "gain_x_px_per_count": (0.03, 8.0),
        "gain_y_px_per_count": (0.03, 8.0),
        "response_delay_ms": (0.0, 50.0),
    }
    values: dict[str, float] = {}
    for name, (minimum, maximum) in limits.items():
        value = payload.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return api_error(f"{name} must be a finite number")
        try:
            numeric_value = float(value)
        except (OverflowError, ValueError):
            return api_error(f"{name} must be a finite number")
        if not math.isfinite(numeric_value):
            return api_error(f"{name} must be a finite number")
        if numeric_value < minimum or numeric_value > maximum:
            return api_error(f"{name} must be between {minimum} and {maximum}")
        values[name] = numeric_value

    try:
        return api_ok(daemon_call("set_auto_calibration", **values))
    except DaemonError as exc:
        return api_error(str(exc))


@app.post("/api/control/calibration/start")
def start_auto_calibration():
    try:
        return api_ok(daemon_call("start_auto_calibration"))
    except DaemonError as exc:
        return api_error(str(exc))


@app.post("/api/control/calibration/cancel")
def cancel_auto_calibration():
    try:
        return api_ok(daemon_call("cancel_auto_calibration"))
    except DaemonError as exc:
        return api_error(str(exc))


@app.delete("/api/control/calibration")
def clear_auto_calibration():
    try:
        return api_ok(daemon_call("clear_auto_calibration"))
    except DaemonError as exc:
        return api_error(str(exc))


@app.get("/api/settings/auto-start")
def get_auto_start_setting():
    return api_ok(_auto_start_settings_payload())


@app.put("/api/settings/auto-start")
def update_auto_start_setting():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
        return api_error("enabled must be a boolean")
    try:
        _save_auto_start_settings(body["enabled"])
        return api_ok(_auto_start_settings_payload())
    except OSError as exc:
        return api_error(f"保存开机自启动设置失败: {exc}", 500)


@app.get("/api/makcu/devices")
def list_makcu_devices():
    try:
        return api_ok(daemon_call("list_makcu_devices"))
    except DaemonError as exc:
        return api_error(str(exc), 503)


@app.get("/api/ferrum/devices")
def list_ferrum_devices():
    try:
        return api_ok(daemon_call("list_ferrum_devices"))
    except DaemonError as exc:
        return api_error(str(exc), 503)


@app.get("/api/kmboxb/devices")
def list_kmboxb_devices():
    try:
        return api_ok(daemon_call("list_kmboxb_devices"))
    except DaemonError as exc:
        return api_error(str(exc), 503)


@app.post("/api/mouse-output/test-circle")
def test_mouse_output_circle():
    try:
        return api_ok(daemon_call("test_mouse_circle", timeout=5.0))
    except DaemonError as exc:
        return api_error(str(exc), 503)


@app.post("/api/control/start")
def start_control():
    try:
        _state_with_license_recovery("get_license")
        return api_ok(daemon_call("start", timeout=CONTROL_START_DAEMON_TIMEOUT_SEC))
    except DaemonError as exc:
        return api_error(str(exc))


@app.post("/api/control/stop")
def stop_control():
    try:
        return api_ok(daemon_call("stop"))
    except DaemonError as exc:
        return api_error(str(exc))


@app.post("/api/diagnostics/aim-trace")
def start_aim_trace():
    body = request.get_json(silent=True) or {}
    duration_sec = body.get("duration_sec", 10)
    try:
        return api_ok(daemon_call("start_aim_trace", duration_sec=duration_sec))
    except DaemonError as exc:
        return api_error(str(exc))


@app.get("/api/diagnostics/usb-proxy.zip")
def download_usb_proxy_diagnostics():
    archive, filename = _build_usb_proxy_diagnostics_zip()
    return send_file(
        archive,
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
        max_age=0,
    )


@app.get("/api/hardware/mouse")
def get_mouse_hardware():
    try:
        return api_ok(_enrich_mouse_hardware_payload(daemon_call("get_mouse_hardware")))
    except DaemonError as exc:
        fallback = _full_passthrough_mouse_fallback_payload(str(exc))
        if fallback is not None:
            return api_ok(fallback)
        return api_error(str(exc), 503)


@app.put("/api/hardware/mouse")
def update_mouse_hardware():
    body = request.get_json(silent=True) or {}
    config = body.get("config", body)
    if not isinstance(config, dict):
        return api_error("config must be an object")
    try:
        return api_ok(daemon_call(
            "set_mouse_hardware",
            config=config,
            apply_now=bool(body.get("apply_now", True)),
        ))
    except DaemonError as exc:
        return api_error(str(exc))


@app.put("/api/hardware/mouse/mode")
def update_mouse_proxy_mode():
    body = request.get_json(silent=True) or {}
    mode = str(body.get("mode", "")).strip()
    if mode not in {"full_passthrough", "synthetic"}:
        return api_error("mode must be full_passthrough or synthetic")
    try:
        return api_ok(daemon_call(
            "set_mouse_proxy_mode",
            timeout=MOUSE_PROXY_MODE_SWITCH_TIMEOUT_SEC,
            mode=mode,
            apply_now=bool(body.get("apply_now", True)),
        ))
    except DaemonError as exc:
        return api_error(str(exc))


@app.put("/api/hardware/mouse/timing")
def update_mouse_proxy_timing():
    try:
        timing = _validated_usb_proxy_timing(request.get_json(silent=True) or {})
    except ValueError as exc:
        return api_error(str(exc))
    try:
        return api_ok(daemon_call(
            "set_mouse_proxy_timing",
            timeout=30.0,
            **timing,
            apply_now=True,
        ))
    except DaemonError as exc:
        return api_error(str(exc))


@app.get("/api/hardware/display")
def get_display_hardware():
    try:
        return api_ok(daemon_call("get_display_hardware"))
    except DaemonError as exc:
        return api_error(str(exc), 503)


@app.put("/api/hardware/display")
def update_display_hardware():
    body = request.get_json(silent=True) or {}
    config = body.get("config", body)
    if not isinstance(config, dict):
        return api_error("config must be an object")
    try:
        return api_ok(daemon_call(
            "set_display_hardware",
            config=config,
            apply=bool(body.get("apply", True)),
            patch_boot_image=bool(body.get("patch_boot_image", False)),
            reboot_after_apply=False,
        ))
    except DaemonError as exc:
        return api_error(str(exc))


@app.get("/api/models")
def list_models():
    try:
        return api_ok(daemon_call("list_models"))
    except DaemonError as exc:
        return api_error(str(exc))


@app.get("/api/models/device-code")
def model_device_code():
    try:
        _ensure_model_key_available()
        return api_ok(daemon_call("get_model_device_code"))
    except DaemonError as exc:
        return api_error(str(exc))


@app.post("/api/models/cloud-encrypted")
def add_cloud_encrypted_model():
    body = request.get_json(silent=True) or {}
    try:
        if not isinstance(body, dict):
            raise DaemonError("请求格式错误")
        class_names = _cloud_class_names(body)
        class_count = _bounded_int(body.get("class_count"), 0, 0, MODEL_LABEL_MAX_COUNT)
        if class_names:
            class_count = max(class_count, min(len(class_names), MODEL_LABEL_MAX_COUNT))
        model = {
            "model_name": _normalize_cloud_model_name(body.get("model_name", "")),
            "display_name": str(body.get("display_name", "") or "").strip(),
            "game_profile": str(body.get("game_profile", "generic") or "generic").strip() or "generic",
            "description": str(body.get("description", "") or "").strip() or "AI Matrix 云加密 RKNN 模型",
            "input_width": _bounded_int(body.get("input_width"), 0, 0, 2048),
            "input_height": _bounded_int(body.get("input_height"), 0, 0, 2048),
            "output_count": _bounded_int(body.get("output_count"), 0, 0, 9),
            "class_count": class_count,
            "class_names": class_names,
        }
        try:
            return api_ok(daemon_call(
                "upsert_cloud_encrypted_model",
                timeout=CLOUD_ENCRYPTED_MODEL_TIMEOUT_SEC,
                model=model
            ))
        except DaemonError as exc:
            if not _is_transient_daemon_socket_busy(exc):
                raise
            recovered = _recover_cloud_encrypted_model_after_timeout(model["model_name"])
            if recovered is not None:
                recovered["recovered_after_timeout"] = True
                return api_ok(recovered)
            raise DaemonError(
                "云加密模型首次加载仍在初始化，请稍后刷新模型列表后重试",
                payload={"original_error": str(exc)},
                status_code=503,
            ) from exc
    except DaemonError as exc:
        return api_error(str(exc), data=exc.payload or None, status_code=exc.status_code or 400)


@app.post("/api/remote/connect")
def remote_connect():
    body = request.get_json(silent=True) or {}
    try:
        host = _normalize_remote_host(str(body.get("host", "")))
        control_port = int(body.get("control_port", REMOTE_CONTROL_PORT) or REMOTE_CONTROL_PORT)
        payload = _remote_call("hello", {
            "host": host,
            "control_port": control_port,
            "frame_port": REMOTE_FRAME_PORT,
            "result_port": REMOTE_RESULT_PORT,
        })
        config = {
            "host": host,
            "control_port": control_port,
            "frame_port": REMOTE_FRAME_PORT,
            "result_port": REMOTE_RESULT_PORT,
        }
        _save_remote_config(config)
        models_payload = _remote_call("list_models", {"host": host, "control_port": control_port})
        models = models_payload.get("models", [])
        if not isinstance(models, list):
            models = []
        sync_payload = _sync_remote_models_from_list(
            host,
            [m for m in models if isinstance(m, dict)],
            control_port=config["control_port"],
            frame_port=config["frame_port"],
            result_port=config["result_port"],
        )
        return api_ok({
            "connected": True,
            "remote": payload,
            "config": config,
            "models": sync_payload.get("models", []),
            "selected_model_id": sync_payload.get("selected_model_id", ""),
        })
    except DaemonError as exc:
        return api_error(str(exc), 503)


@app.get("/api/remote/models")
def remote_models():
    try:
        config = _remote_config()
        host = _normalize_remote_host(config.get("host", ""))
        payload = _remote_call("list_models", {"host": host, "control_port": config["control_port"]})
        models = payload.get("models", [])
        if not isinstance(models, list):
            models = []
        sync_payload = _sync_remote_models_from_list(
            host,
            [m for m in models if isinstance(m, dict)],
            control_port=config["control_port"],
            frame_port=config["frame_port"],
            result_port=config["result_port"],
        )
        return api_ok({
            "connected": True,
            "config": config,
            "remote_models": models,
            "models": sync_payload.get("models", []),
            "selected_model_id": sync_payload.get("selected_model_id", ""),
        })
    except DaemonError as exc:
        return api_error(str(exc), 503)


@app.post("/api/remote/import")
def remote_import():
    preset_temp_path: Path | None = None
    try:
        uploaded = request.files.get("file")
        if uploaded is None or uploaded.filename == "":
            raise DaemonError("missing upload field: file")
        file_name = validate_uploaded_suffix(uploaded, {".onnx", ".enc"}, "ONNX model")
        original_file_name = uploaded_basename(uploaded, file_name)
        lower_file_name = original_file_name.lower()
        if lower_file_name.endswith(".enc") and not lower_file_name.endswith(".onnx.enc"):
            raise DaemonError("加密 ONNX 文件名必须以 .onnx.enc 结尾")
        original_display_name = (
            original_file_name[:-len(".onnx.enc")]
            if lower_file_name.endswith(".onnx.enc")
            else Path(original_file_name).stem
        ) or original_file_name
        model_bytes = uploaded.read()
        if not model_bytes:
            raise DaemonError("ONNX 文件为空")
        encrypted_model = model_bytes.startswith(b"AIRKNNE1")
        if lower_file_name.endswith(".enc") and not encrypted_model:
            raise DaemonError("加密 ONNX 文件头无效")
        model_key_code = ""
        if encrypted_model:
            _ensure_model_key_available()
            key_payload = daemon_call("get_model_device_code")
            model_key_code = str(key_payload.get("code") or "").strip()
            if not model_key_code.startswith("AIMK1_"):
                raise DaemonError("开发板设备模型码不可用")
        uploaded_class_names = read_uploaded_model_labels("labels_file")
        preset_temp_path, preset_name = save_uploaded_model_preset()
        config = _remote_config()
        host = _normalize_remote_host(request.form.get("remote_host", "") or config.get("host", ""))
        payload = _remote_call(
            "upload_onnx",
            {
                "host": host,
                "control_port": config["control_port"],
                "file_name": original_file_name,
                "game_profile": request.form.get("game_profile", "generic"),
                "description": "Remote ONNX TensorRT model",
                "class_names": uploaded_class_names,
                "model_key_code": model_key_code,
            },
            body=model_bytes,
            timeout=REMOTE_UPLOAD_TIMEOUT_SEC,
        )
        model = payload.get("model", {})
        if not isinstance(model, dict):
            raise DaemonError("远端导入成功但没有返回模型信息")
        game_profile = request.form.get("game_profile", "generic").strip() or "generic"
        description = request.form.get("description", "").strip() or "Remote TensorRT YOLO model"
        if original_display_name:
            model["name"] = original_display_name
            model["display_name"] = original_display_name
        model["original_file_name"] = original_file_name
        model["game_profile"] = game_profile
        model["description"] = description
        model["remote_frame_format"] = REMOTE_ONNX_DEFAULT_FRAME_FORMAT
        if uploaded_class_names:
            model["class_names"] = uploaded_class_names
            model["class_count"] = max(int(model.get("class_count") or 0), len(uploaded_class_names))
        if preset_temp_path is not None:
            model["preset_source_path"] = str(preset_temp_path)
            model["preset_name"] = preset_name
        result = daemon_call(
            "upsert_remote_model",
            host=host,
            control_port=config["control_port"],
            frame_port=config["frame_port"],
            result_port=config["result_port"],
            model=model,
        )
        imported_model = result.get("model", {}) if isinstance(result, dict) else {}
        imported_model_id = str(imported_model.get("id", "") if isinstance(imported_model, dict) else "").strip()
        if imported_model_id:
            result = daemon_call(
                "update_model_remote_frame_format",
                model_id=imported_model_id,
                remote_frame_format=REMOTE_ONNX_DEFAULT_FRAME_FORMAT,
            )
        return api_ok(result)
    except DaemonError as exc:
        return api_error(str(exc))
    finally:
        if preset_temp_path is not None:
            preset_temp_path.unlink(missing_ok=True)


@app.post("/api/remote/delete")
def remote_delete():
    body = request.get_json(silent=True) or {}
    model_id = str(body.get("model_id", "")).strip()
    remote_model_id = str(body.get("remote_model_id", "")).strip()
    if not model_id and not remote_model_id:
        return api_error("model_id is required")
    try:
        config = _remote_config()
        host = _normalize_remote_host(str(body.get("remote_host", "") or config.get("host", "")))
        skip_remote_delete = bool(body.get("remote_available") is False or body.get("remote_missing") is True)
        if remote_model_id and not skip_remote_delete:
            try:
                _remote_call("delete_model", {
                    "host": host,
                    "control_port": config["control_port"],
                    "model_id": remote_model_id,
                })
            except DaemonError as exc:
                if "not found" not in str(exc).lower() and "不存在" not in str(exc):
                    raise
        if model_id:
            result = daemon_call("delete_model", model_id=model_id)
        else:
            result = {"remote_model_id": remote_model_id}
        return api_ok(result)
    except DaemonError as exc:
        return api_error(str(exc), 503)


@app.post("/api/models/import")
def import_model():
    temp_path: Path | None = None
    temp_dir: Path | None = None
    preset_temp_path: Path | None = None
    try:
        import_type = request.form.get("model_type", "rknn").strip().lower()
        if import_type in {"remote", "remote_onnx"}:
            return remote_import()
        uploaded_class_names = read_uploaded_model_labels("labels_file")
        preset_temp_path, preset_name = save_uploaded_model_preset()
        if import_type == "onnx":
            uploaded_model = request.files.get("file")
            uploaded_dataset = request.files.get("calibration_zip")
            if uploaded_model is None or uploaded_model.filename == "":
                raise DaemonError("missing upload field: file")
            _ensure_model_key_available()
            if not _model_conversion_lock.acquire(blocking=False):
                return api_error("another ONNX conversion is running", 409)
            try:
                temp_dir = Path(tempfile.mkdtemp(prefix="aiassistance_onnx_import_"))
                rknn_path, onnx_file_name, image_count, class_names = convert_onnx_upload_to_rknn(
                    uploaded_model,
                    uploaded_dataset,
                    temp_dir,
                )
                result = import_rknn_into_daemon(
                    rknn_path,
                    Path(onnx_file_name).with_suffix(".rknn").name,
                    f"Converted from ONNX with {image_count} calibration images",
                    uploaded_class_names or class_names,
                    preset_temp_path,
                    preset_name,
                )
            finally:
                _model_conversion_lock.release()
        elif import_type == "hef":
            temp_path, uploaded = save_uploaded_file("file", ".hef")
            file_name = validate_uploaded_suffix(uploaded, {".hef", ".enc"}, "HEF model")
            if not uploaded_basename(uploaded, file_name).lower().endswith(".enc"):
                _ensure_model_key_available()
            result = import_model_file_into_daemon(
                temp_path,
                uploaded_basename(uploaded, temp_path.name),
                "Imported Hailo HEF from web upload",
                uploaded_class_names,
                preset_temp_path,
                preset_name,
            )
        else:
            temp_path, uploaded = save_uploaded_file("file", ".rknn")
            file_name = validate_uploaded_suffix(uploaded, {".rknn", ".enc"}, "RKNN model")
            if not uploaded_basename(uploaded, file_name).lower().endswith(".enc"):
                _ensure_model_key_available()
            result = import_model_file_into_daemon(
                temp_path,
                uploaded_basename(uploaded, temp_path.name),
                "Imported from web upload",
                uploaded_class_names,
                preset_temp_path,
                preset_name,
            )
        return api_ok(result)
    except DaemonError as exc:
        return api_error(str(exc))
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)
        if preset_temp_path is not None:
            preset_temp_path.unlink(missing_ok=True)


@app.post("/api/models/delete")
def delete_model():
    body = request.get_json(silent=True) or {}
    model_id = body.get("model_id", "")
    if not model_id:
        return api_error("model_id is required")
    if body.get("backend") == "remote" or body.get("remote_model_id"):
        return remote_delete()
    try:
        return api_ok(daemon_call("delete_model", model_id=model_id))
    except DaemonError as exc:
        return api_error(str(exc))


@app.post("/api/models/select")
def select_model():
    body = request.get_json(silent=True) or {}
    model_id = body.get("model_id", "")
    if not model_id:
        return api_error("model_id is required")
    try:
        return api_ok(daemon_call("select_model", model_id=model_id))
    except DaemonError as exc:
        return api_error(str(exc))


@app.post("/api/models/bind-preset")
def bind_model_preset():
    body = request.get_json(silent=True) or {}
    model_id = body.get("model_id", "")
    if not model_id:
        return api_error("model_id is required")
    try:
        return api_ok(daemon_call(
            "bind_model_preset",
            model_id=model_id,
            preset_name=body.get("preset_name", ""),
        ))
    except DaemonError as exc:
        return api_error(str(exc))


@app.post("/api/models/game-profile")
def update_model_game_profile():
    body = request.get_json(silent=True) or {}
    model_id = str(body.get("model_id", "")).strip()
    game_profile = str(body.get("game_profile", "generic")).strip() or "generic"
    if not model_id:
        return api_error("model_id is required")
    try:
        return api_ok(daemon_call(
            "update_model_game_profile",
            model_id=model_id,
            game_profile=game_profile,
        ))
    except DaemonError as exc:
        return api_error(str(exc))


@app.post("/api/models/remote-frame-format")
def update_model_remote_frame_format():
    body = request.get_json(silent=True) or {}
    model_id = str(body.get("model_id", "")).strip()
    remote_frame_format = str(body.get("remote_frame_format", "jpeg")).strip().lower()
    if not model_id:
        return api_error("model_id is required")
    if remote_frame_format not in {"jpeg", "nv12", "h264"}:
        remote_frame_format = "jpeg"
    try:
        return api_ok(daemon_call(
            "update_model_remote_frame_format",
            model_id=model_id,
            remote_frame_format=remote_frame_format,
        ))
    except DaemonError as exc:
        return api_error(str(exc))


@app.post("/api/models/rknn-concurrency")
def update_model_rknn_concurrency():
    body = request.get_json(silent=True) or {}
    model_id = str(body.get("model_id", "")).strip()
    if not model_id:
        return api_error("model_id is required")
    try:
        rknn_concurrency = int(body.get("rknn_concurrency", 1))
    except (TypeError, ValueError):
        rknn_concurrency = 1
    rknn_concurrency = max(1, min(3, rknn_concurrency))
    try:
        return api_ok(daemon_call(
            "update_model_rknn_concurrency",
            model_id=model_id,
            rknn_concurrency=rknn_concurrency,
        ))
    except DaemonError as exc:
        return api_error(str(exc))


@app.post("/api/models/hailo-pipeline-depth")
def update_model_hailo_pipeline_depth():
    body = request.get_json(silent=True) or {}
    model_id = str(body.get("model_id", "")).strip()
    if not model_id:
        return api_error("model_id is required")
    try:
        hailo_pipeline_depth = int(body.get("hailo_pipeline_depth", 3))
    except (TypeError, ValueError):
        hailo_pipeline_depth = 3
    hailo_pipeline_depth = max(1, min(4, hailo_pipeline_depth))
    try:
        return api_ok(daemon_call(
            "update_model_hailo_pipeline_depth",
            model_id=model_id,
            hailo_pipeline_depth=hailo_pipeline_depth,
        ))
    except DaemonError as exc:
        return api_error(str(exc))


@app.post("/api/models/class-names")
def update_model_class_names():
    body = request.get_json(silent=True) or {}
    model_id = str(body.get("model_id", "")).strip()
    class_names = body.get("class_names", [])
    if not model_id:
        return api_error("model_id is required")
    if not isinstance(class_names, list):
        return api_error("class_names must be an array")
    try:
        normalized_class_names = _normalize_model_class_names(class_names)
        model = _find_model_entry(model_id)
        if model is not None:
            normalized_class_names = _sync_remote_model_class_names(model, normalized_class_names)
        return api_ok(daemon_call(
            "update_model_class_names",
            model_id=model_id,
            class_names=normalized_class_names,
        ))
    except DaemonError as exc:
        return api_error(str(exc))


@app.get("/api/presets")
def list_presets():
    try:
        return api_ok(daemon_call("list_presets"))
    except DaemonError as exc:
        return api_error(str(exc))


@app.post("/api/presets")
def save_or_delete_preset():
    body = request.get_json(silent=True) or {}
    name = body.get("name", "")
    if not name:
        return api_error("name is required")
    try:
        if body.get("action") == "delete":
            return api_ok(daemon_call("delete_preset", name=name))
        if body.get("action") == "rename":
            new_name = body.get("new_name", "")
            if not new_name:
                return api_error("new_name is required")
            return api_ok(daemon_call("rename_preset", name=name, new_name=new_name))
        payload = {"name": name}
        if isinstance(body.get("config"), dict):
            payload["config"] = body["config"]
        return api_ok(daemon_call("save_preset", **payload))
    except DaemonError as exc:
        return api_error(str(exc))


@app.post("/api/presets/load")
def load_preset():
    body = request.get_json(silent=True) or {}
    name = body.get("name", "")
    if not name:
        return api_error("name is required")
    try:
        return api_ok(daemon_call("load_preset", name=name))
    except DaemonError as exc:
        return api_error(str(exc))


@app.post("/api/presets/import")
def import_preset():
    temp_path = None
    try:
        temp_path, uploaded = save_uploaded_file("file", ".json")
        fallback_name = Path(uploaded.filename).stem
        requested_name = (request.form.get("name") or "").strip() or fallback_name
        return api_ok(daemon_call("import_preset", source_path=str(temp_path), name=requested_name))
    except DaemonError as exc:
        return api_error(str(exc))
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


@app.get("/api/presets/<name>/export")
def export_preset(name: str):
    try:
        result = daemon_call("export_preset", name=name)
    except DaemonError as exc:
        return api_error(str(exc))
    payload = json.dumps(result["config"], ensure_ascii=False, indent=2) + "\n"
    return send_file(
        io.BytesIO(payload.encode("utf-8")),
        mimetype="application/json",
        as_attachment=True,
        download_name=f'{result["name"]}.json',
        max_age=0,
    )


@app.get("/api/preview.jpg")
def preview():
    ensure_preview_receiver_started()
    with _preview_condition:
        frame = _preview_latest_jpeg
    if frame is not None:
        return Response(
            frame,
            mimetype="image/jpeg",
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
            },
        )
    if PREVIEW_PATH.exists():
        return send_file(PREVIEW_PATH, mimetype="image/jpeg", max_age=0, etag=False)
    return api_error("preview is not ready", 404)


@app.get("/api/preview.mjpg")
def preview_stream():
    ensure_preview_receiver_started()

    def stream():
        last_seq = -1
        while True:
            with _preview_condition:
                _preview_condition.wait_for(
                    lambda: _preview_latest_seq != last_seq and _preview_latest_jpeg is not None,
                    timeout=2.0,
                )
                frame = _preview_latest_jpeg
                last_seq = _preview_latest_seq

            if frame is None:
                continue
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Cache-Control: no-cache\r\n"
                b"Content-Length: " + str(len(frame)).encode("ascii") + b"\r\n\r\n" +
                frame +
                b"\r\n"
            )

    return Response(
        stream(),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    _restore_missing_runtime_scripts()
    _apply_lan_blocklist_on_startup()
    _schedule_auto_start_on_boot()
    host = os.environ.get("AIASSISTANCE_BIND", "0.0.0.0")
    port = _current_web_port()
    threads = int(os.environ.get("AIASSISTANCE_WEB_THREADS", "32"))
    serve(app, host=host, port=port, threads=threads)
