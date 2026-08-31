#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


SERVICE_DAEMON = "aiassistance-daemon.service"
SERVICE_WEB = "aiassistance-web.service"
SERVICE_WIFI_BOOTSTRAP = "aiassistance-wifi-bootstrap.service"
SERVICE_PERFORMANCE = "aiassistance-performance.service"
SERVICE_KMBOXNET_USB = "aiassistance-kmboxnet-usb.service"
SERVICE_MAKCU = "aiassistance-makcu.service"
SERVICE_USB_PROXY = "usb-proxy.service"
SERVICE_SELFHEAL = "aiassistance-selfheal.service"
TIMER_SELFHEAL = "aiassistance-selfheal.timer"
USB_PROXY_ROOT = Path(os.environ.get("AIASSISTANCE_USB_PROXY_ROOT", "/opt/usb-proxy"))
USB_PROXY_ENV_PATH = Path(os.environ.get("AIASSISTANCE_USB_PROXY_ENV_PATH", "/etc/default/usb-proxy"))
DEFAULT_WEB_PORT = int(os.environ.get("AIASSISTANCE_DEFAULT_WEB_PORT", "8080"))
WEB_PORT_OVERRIDE_PATH = Path(os.environ.get(
    "AIASSISTANCE_WEB_PORT_OVERRIDE_PATH",
    "/etc/systemd/system/aiassistance-web.service.d/10-port.conf",
))
NON_RUNTIME_SCRIPTS = {
    "batch_convert_encrypt_models.py",
    "build_rk3588.sh",
    "check_nanopc_t6_image.sh",
    "collect_customer_debug_bundle.sh",
    "configure_lan_discovery.sh",
    "deploy_full_orangepi.sh",
    "deploy_orangepi.sh",
    "make_update_package.sh",
    "package_dist.sh",
    "setup_rknn_converter_venv.sh",
    "test_dwc3_gadget.sh",
    "test_raw_gadget_minimal.sh",
    "test_raw_gadget_usbproxy_like.sh",
}
ROLLBACK_BACKUP_KEEP_COUNT = max(1, int(os.environ.get("AIASSISTANCE_ROLLBACK_BACKUP_KEEP", "1")))
UPDATE_CACHE_FILE_PATTERNS = (
    "aiassistance-*.tar.gz",
    "aiassistance-*.tar.zst",
    "update-metadata-*.json",
    "*.tmp-*",
)
RECOVERY_MANIFEST_NAME = "full-ota.json"
RECOVERY_PACKAGE_PATTERNS = (
    "aiassistance-full-*.tar.gz",
    "aiassistance-full-*.tar.zst",
)
RECOVERY_REPLACE_DIRS = ("bin", "lib", "web", "scripts", "assets", "Python", "systemd")
RECOVERY_REQUIRED_DIRS = (*RECOVERY_REPLACE_DIRS, "license")
USB_PROXY_RUNTIME_DIR = Path(os.environ.get("AIASSISTANCE_USB_PROXY_RUNTIME_DIR", "/run/usb-proxy"))
MOUSE_RUNTIME_DIR = Path(os.environ.get("AIASSISTANCE_MOUSE_RUNTIME_DIR", "/run/orangepi-mouse-passthrough"))
BOOT_ENV_PATH = Path(os.environ.get("AIASSISTANCE_BOOT_ENV_PATH", "/boot/orangepiEnv.txt"))


def emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def run(command: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, text=True, capture_output=True)
    if check and completed.returncode != 0:
        details = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(details or f"{' '.join(command)} failed with exit code {completed.returncode}")
    return completed


def http_get_ok(url: str, timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read(1024).decode("utf-8", errors="replace")
            return subprocess.CompletedProcess([url], 0, body, "")
    except Exception as exc:
        return subprocess.CompletedProcess([url], 1, "", str(exc))


def http_get_json(url: str, timeout: float = 5.0) -> tuple[dict[str, Any] | None, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read(1024 * 1024).decode("utf-8", errors="replace")
    except Exception as exc:
        return None, str(exc)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON from {url}: {exc}"
    if not isinstance(payload, dict):
        return None, f"unexpected JSON from {url}"
    return payload, ""


def current_web_port() -> int:
    value = os.environ.get("AIASSISTANCE_PORT", "")
    if WEB_PORT_OVERRIDE_PATH.exists():
        try:
            text = WEB_PORT_OVERRIDE_PATH.read_text(encoding="utf-8")
        except OSError:
            text = ""
        match = re.search(r"(?m)^\s*Environment\s*=\s*AIASSISTANCE_PORT\s*=\s*(\d+)\s*$", text)
        if match:
            value = match.group(1)
    try:
        port = int(value)
    except (TypeError, ValueError):
        return DEFAULT_WEB_PORT
    return port if 1 <= port <= 65535 else DEFAULT_WEB_PORT


def safe_member_path(root: Path, name: str) -> Path:
    target = (root / name).resolve()
    root_resolved = root.resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise RuntimeError(f"unsafe package path: {name}")
    return target


def validate_archive_member(root: Path, member: tarfile.TarInfo) -> None:
    target = safe_member_path(root, member.name)
    if member.ischr() or member.isblk() or member.isfifo():
        raise RuntimeError(f"unsafe special file in package: {member.name}")
    if member.issym():
        link_target = (target.parent / member.linkname).resolve()
        root_resolved = root.resolve()
        if link_target != root_resolved and root_resolved not in link_target.parents:
            raise RuntimeError(f"unsafe package symlink: {member.name} -> {member.linkname}")
    elif member.islnk():
        safe_member_path(root, member.linkname)


def extract_package(package_path: Path, workspace: Path) -> Path:
    extract_dir = workspace / "package"
    extract_dir.mkdir(parents=True, exist_ok=True)
    mode = "r:*"
    with tarfile.open(package_path, mode) as archive:
        for member in archive.getmembers():
            validate_archive_member(extract_dir, member)
        try:
            archive.extractall(extract_dir, filter="data")
        except TypeError:  # Python < 3.12
            archive.extractall(extract_dir)
    manifest_path = extract_dir / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError("update package missing manifest.json")
    return extract_dir


def load_manifest(package_dir: Path) -> dict:
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    return validate_manifest(manifest)


def validate_manifest(manifest: Any) -> dict:
    if not isinstance(manifest, dict):
        raise RuntimeError("manifest.json must be an object")
    package_type = manifest.get("type", "full")
    if package_type not in {"web", "daemon", "core", "models", "full"}:
        raise RuntimeError(f"unsupported update type: {package_type}")
    files = manifest.get("files", [])
    if not isinstance(files, list):
        raise RuntimeError("manifest files must be a list")
    return manifest


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_package_manifest(package_path: Path) -> dict:
    if not package_path.is_file() or package_path.is_symlink():
        raise RuntimeError(f"recovery package is missing or unsafe: {package_path}")
    try:
        with tarfile.open(package_path, "r:*") as archive:
            manifest_members = []
            for member in archive.getmembers():
                normalized_name = member.name
                while normalized_name.startswith("./"):
                    normalized_name = normalized_name[2:]
                if normalized_name == "manifest.json" and member.isfile():
                    manifest_members.append(member)
            if len(manifest_members) != 1:
                raise RuntimeError("recovery package must contain exactly one manifest.json")
            extracted = archive.extractfile(manifest_members[0])
            if extracted is None:
                raise RuntimeError("recovery package manifest is unreadable")
            raw = extracted.read(1024 * 1024 + 1)
    except (tarfile.TarError, OSError) as exc:
        raise RuntimeError(f"recovery package is invalid: {exc}") from exc
    if len(raw) > 1024 * 1024:
        raise RuntimeError("recovery package manifest is too large")
    try:
        return validate_manifest(json.loads(raw.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"recovery package manifest is invalid: {exc}") from exc


def load_metadata(metadata_path: Path | None) -> dict:
    if metadata_path is None:
        return {}
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"update metadata is invalid: {exc}") from exc
    return payload if isinstance(payload, dict) else {}


def resolve_install_versions(metadata: dict, manifest: dict) -> tuple[str, str]:
    manifest_version = str(manifest.get("version") or "").strip()
    release_version = str(metadata.get("version") or manifest_version).strip()
    app_version = str(metadata.get("app_version") or manifest.get("app_version") or manifest_version or release_version).strip()
    return release_version, app_version


def write_json_file(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_text_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def recovery_dir(root: Path) -> Path:
    override = str(os.environ.get("AIASSISTANCE_RECOVERY_DIR", "")).strip()
    return Path(override).resolve() if override else (root / "recovery").resolve()


def recovery_manifest_path(root: Path) -> Path:
    return recovery_dir(root) / RECOVERY_MANIFEST_NAME


def _safe_recovery_filename(value: str) -> str:
    name = Path(str(value or "")).name
    if name != value or not re.fullmatch(r"aiassistance-full-[A-Za-z0-9._-]+\.tar\.(?:gz|zst)", name):
        raise RuntimeError("recovery package filename is invalid")
    return name


def _recovery_record(root: Path) -> dict[str, Any]:
    manifest_path = recovery_manifest_path(root)
    try:
        record = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"local recovery manifest is unavailable: {exc}") from exc
    if not isinstance(record, dict):
        raise RuntimeError("local recovery manifest is invalid")
    if record.get("format") != "aiassistance-recovery-full-ota-v1":
        raise RuntimeError("local recovery manifest format is unsupported")
    filename = _safe_recovery_filename(str(record.get("file") or ""))
    expected_sha = str(record.get("sha256") or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{64}", expected_sha):
        raise RuntimeError("local recovery package SHA-256 is invalid")
    package_path = recovery_dir(root) / filename
    actual_sha = sha256_file(package_path)
    if actual_sha != expected_sha:
        raise RuntimeError("local recovery package SHA-256 mismatch")
    manifest = read_package_manifest(package_path)
    if manifest.get("type") != "full":
        raise RuntimeError("local recovery package is not a full OTA")
    version = str(manifest.get("version") or "").strip()
    if not version or version != str(record.get("version") or "").strip():
        raise RuntimeError("local recovery package version mismatch")
    return {
        "available": True,
        "version": version,
        "file": filename,
        "sha256": actual_sha,
        "size": package_path.stat().st_size,
        "saved_at": str(record.get("saved_at") or ""),
        "source": str(record.get("source") or "successful_full_update"),
        "package_path": str(package_path),
    }


def retain_recovery_package(
    root: Path,
    package_path: Path,
    expected_sha256: str,
    *,
    source: str = "successful_full_update",
) -> dict[str, Any]:
    expected_sha = str(expected_sha256 or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{64}", expected_sha):
        raise RuntimeError("full OTA SHA-256 is missing or invalid")
    actual_sha = sha256_file(package_path)
    if actual_sha != expected_sha:
        raise RuntimeError("full OTA SHA-256 changed before recovery retention")
    manifest = read_package_manifest(package_path)
    if manifest.get("type") != "full":
        raise RuntimeError("only a full OTA can be retained for local recovery")
    version = str(manifest.get("version") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", version):
        raise RuntimeError("full OTA version is invalid")
    suffix = ".tar.gz" if package_path.name.endswith(".tar.gz") else ".tar.zst" if package_path.name.endswith(".tar.zst") else ""
    if not suffix:
        raise RuntimeError("full OTA archive extension is unsupported")

    target_dir = recovery_dir(root)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_dir.chmod(0o700)
    target_name = _safe_recovery_filename(f"aiassistance-full-{version}{suffix}")
    target_path = target_dir / target_name
    temp_path = target_dir / f".{target_name}.tmp-{os.getpid()}"
    try:
        with package_path.open("rb") as source_handle, temp_path.open("wb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        temp_path.chmod(0o600)
        if sha256_file(temp_path) != expected_sha:
            raise RuntimeError("retained recovery package failed post-copy verification")
        temp_path.replace(target_path)
    finally:
        temp_path.unlink(missing_ok=True)

    record = {
        "format": "aiassistance-recovery-full-ota-v1",
        "version": version,
        "file": target_name,
        "sha256": expected_sha,
        "size": target_path.stat().st_size,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": str(source or "successful_full_update"),
    }
    write_json_file(recovery_manifest_path(root), record)
    recovery_manifest_path(root).chmod(0o600)
    for pattern in RECOVERY_PACKAGE_PATTERNS:
        for old_path in target_dir.glob(pattern):
            if old_path != target_path:
                remove_path(old_path)
    return {"available": True, **record, "package_path": str(target_path)}


def _metadata_package_candidates(root: Path, metadata: dict[str, Any]) -> list[Path]:
    updates_dir = root / "updates"
    package = metadata.get("package") if isinstance(metadata.get("package"), dict) else {}
    names: list[str] = []
    file_value = str(package.get("file") or "").strip()
    if file_value:
        names.append(Path(file_value).name)
    url_value = str(package.get("url") or "").strip()
    if url_value:
        names.append(Path(urllib.parse.unquote(urllib.parse.urlparse(url_value).path)).name)
    candidates: list[Path] = []
    for name in names:
        if name and name == Path(name).name:
            candidate = updates_dir / name
            if candidate not in candidates:
                candidates.append(candidate)
    for pattern in RECOVERY_PACKAGE_PATTERNS:
        for candidate in sorted(updates_dir.glob(pattern)):
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def adopt_successful_update_cache(root: Path) -> dict[str, Any] | None:
    updates_dir = root / "updates"
    if not updates_dir.is_dir():
        return None
    try:
        installed = json.loads((updates_dir / "last-installed.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(installed, dict) or not installed.get("installed"):
        return None
    metadata_value = str(installed.get("metadata") or "").strip()
    if not metadata_value:
        return None
    metadata_path = Path(metadata_value).resolve()
    updates_resolved = updates_dir.resolve()
    if metadata_path.parent != updates_resolved or not metadata_path.name.startswith("update-metadata-"):
        return None
    metadata_paths = [metadata_path]
    for metadata_path in metadata_paths:
        try:
            metadata = load_metadata(metadata_path)
        except RuntimeError:
            continue
        package = metadata.get("package") if isinstance(metadata.get("package"), dict) else {}
        if str(metadata.get("type") or package.get("type") or "") != "full":
            continue
        installed_version = str(installed.get("app_version") or installed.get("version") or "").strip()
        metadata_version = str(metadata.get("app_version") or metadata.get("version") or package.get("version") or "").strip()
        if installed_version and metadata_version and installed_version != metadata_version:
            continue
        expected_sha = str(metadata.get("sha256") or package.get("sha256") or "").strip().lower()
        if not re.fullmatch(r"[a-f0-9]{64}", expected_sha):
            continue
        for candidate in _metadata_package_candidates(root, metadata):
            try:
                if not candidate.is_file() or candidate.is_symlink() or sha256_file(candidate) != expected_sha:
                    continue
                return retain_recovery_package(
                    root,
                    candidate,
                    expected_sha,
                    source="adopted_successful_update_cache",
                )
            except (OSError, RuntimeError):
                continue
    return None


def recovery_status(root: Path, *, adopt_updates: bool = True) -> dict[str, Any]:
    error = ""
    try:
        return _recovery_record(root)
    except (OSError, RuntimeError) as exc:
        error = str(exc)
    if adopt_updates:
        adopted = adopt_successful_update_cache(root)
        if adopted is not None:
            return adopted
    return {"available": False, "error": error or "no verified local full OTA is available"}


def read_update_status(root: Path) -> dict:
    try:
        payload = json.loads((root / "updates" / "update-status.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_update_status(
    root: Path,
    *,
    status: str,
    stage: str,
    message: str,
    progress: int,
    version: str = "",
    package_type: str = "",
    error: str = "",
    extra: dict | None = None,
) -> None:
    now = int(time.time())
    previous = read_update_status(root)
    payload = {
        "status": status,
        "stage": stage,
        "message": message,
        "progress": max(0, min(100, int(progress))),
        "version": str(version or previous.get("version", "")),
        "type": str(package_type or previous.get("type", "")),
        "unit": str(previous.get("unit", "")),
        "error": str(error or ""),
        "started_at": int(previous.get("started_at") or now),
        "updated_at": now,
    }
    if status in {"success", "failed"}:
        payload["completed_at"] = now
    if extra:
        payload.update(extra)
    write_json_file(root / "updates" / "update-status.json", payload)


def write_installed_version(root: Path, version: str) -> None:
    version = str(version or "").strip()
    if not version:
        return
    write_text_file(root / "VERSION", version + "\n")


def copy_tree_contents(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        target = destination / item.name
        if item.is_dir():
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(item, target, symlinks=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)


def component_file_specs(root: Path) -> list[tuple[str, Path]]:
    return [
        ("device", root / "license" / "device.json"),
        ("device_recovery", root / "license" / "device_recovery.json"),
        ("license", root / "license" / "license.json"),
        ("online_grant", root / "license" / "online_grant.json"),
        ("model_key", root / "license" / "model_key.bin"),
        ("core_enc", root / "core" / "libai_core.so.enc"),
        ("core_plain", root / "core" / "libai_core.so"),
        ("core_activation", root / "license" / "core_activation.json"),
        ("usb_proxy_enc", USB_PROXY_ROOT / "bin" / "usb-proxy.enc"),
        ("usb_proxy_loader", USB_PROXY_ROOT / "bin" / "usb-proxy-loader"),
        ("usb_proxy_synthetic", USB_PROXY_ROOT / "bin" / "usb-proxy-synthetic"),
        ("usb_proxy_activation", USB_PROXY_ROOT / "license" / "usb_proxy_activation.json"),
        ("usb_proxy_run_script", USB_PROXY_ROOT / "board" / "run_usb_proxy.sh"),
        ("usb_proxy_env", USB_PROXY_ENV_PATH),
    ]


def backup_component_files(root: Path, backup_dir: Path) -> None:
    component_dir = backup_dir / "component-backup"
    component_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for name, destination in component_file_specs(root):
        backup_path = component_dir / name
        existed = destination.exists()
        entries.append({
            "name": name,
            "destination": str(destination),
            "backup": name,
            "existed": existed,
        })
        if existed:
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, backup_path)
    write_json_file(component_dir / "manifest.json", {"files": entries})


def restore_component_files(backup_dir: Path) -> None:
    manifest_path = backup_dir / "component-backup" / "manifest.json"
    if not manifest_path.exists():
        return
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in payload.get("files", []):
        destination = Path(str(entry.get("destination", "")))
        backup_path = manifest_path.parent / str(entry.get("backup", ""))
        if entry.get("existed"):
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup_path, destination)
        else:
            destination.unlink(missing_ok=True)


def backup_current(root: Path, manifest: dict) -> Path:
    releases_dir = root / "releases"
    releases_dir.mkdir(parents=True, exist_ok=True)
    backup_dir = releases_dir / f"previous-{int(time.time())}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for name in ["bin", "web", "scripts", "systemd", "lib", "license", "Python", "assets"]:
        source = root / name
        if source.exists():
            shutil.copytree(source, backup_dir / name, symlinks=True)
    if manifest.get("type") in {"models", "full"} and (root / "models").exists():
        shutil.copytree(root / "models", backup_dir / "models", symlinks=True)
    backup_component_files(root, backup_dir)
    latest = releases_dir / "previous"
    if latest.exists() or latest.is_symlink():
        if latest.is_dir() and not latest.is_symlink():
            shutil.rmtree(latest)
        else:
            latest.unlink()
    latest.symlink_to(backup_dir.name)
    return backup_dir


def restore_backup(root: Path, backup_dir: Path) -> None:
    for item in backup_dir.iterdir():
        if item.name in {"manifest.json", "component-backup"}:
            continue
        target = root / item.name
        if target.exists():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        if item.is_dir():
            shutil.copytree(item, target, symlinks=True)
        else:
            shutil.copy2(item, target)
    restore_component_files(backup_dir)


def safe_update_path(root: Path, value: str) -> Path:
    updates_dir = (root / "updates").resolve()
    path = Path(value).resolve()
    if path != updates_dir and updates_dir not in path.parents:
        raise RuntimeError(f"unsafe staged update path: {value}")
    if not path.exists() or not path.is_file():
        raise RuntimeError(f"staged update file is missing: {path}")
    return path


def install_component_files(root: Path, metadata: dict) -> None:
    components = metadata.get("components", {})
    if not isinstance(components, dict):
        return
    core = components.get("core", {})
    if isinstance(core, dict) and core.get("path") and isinstance(core.get("activation"), dict):
        source = safe_update_path(root, str(core["path"]))
        destination = root / "core" / "libai_core.so.enc"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        destination.chmod(0o600)
        activation_path = root / "license" / "core_activation.json"
        write_json_file(activation_path, core["activation"])
        activation_path.chmod(0o600)
        (root / "core" / "libai_core.so").unlink(missing_ok=True)
        run_dir = root / "run"
        if run_dir.exists():
            for temp_core in run_dir.glob("libai_core_*.so"):
                temp_core.unlink(missing_ok=True)

    usb_proxy = components.get("usb_proxy", {})
    if isinstance(usb_proxy, dict) and usb_proxy.get("path") and isinstance(usb_proxy.get("activation"), dict):
        source = safe_update_path(root, str(usb_proxy["path"]))
        destination = USB_PROXY_ROOT / "bin" / "usb-proxy.enc"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        destination.chmod(0o600)
        activation_path = USB_PROXY_ROOT / "license" / "usb_proxy_activation.json"
        write_json_file(activation_path, usb_proxy["activation"])
        activation_path.chmod(0o600)
        (USB_PROXY_ROOT / "bin" / "usb-proxy").unlink(missing_ok=True)
        ensure_usb_proxy_runtime_env()


def ensure_usb_proxy_runtime_env() -> None:
    defaults = {
        "USB_PROXY_RUNNING_UDC_DETACHED_RECOVERY_SEC": "2",
        "USB_PROXY_FULL_AUTO_REMAP_ENDPOINTS": "1",
        "USB_PROXY_FULL_SET_CONFIG_ACK_BEFORE_CONFIGURE": "1",
    }
    USB_PROXY_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = USB_PROXY_ENV_PATH.read_text(encoding="utf-8").splitlines() if USB_PROXY_ENV_PATH.exists() else []
    forbidden = {
        "USB_PROXY_BIN",
        "USB_PROXY_DISABLE_ENCRYPTED_BIN",
        "USB_PROXY_DISABLE_MEMFD_EXEC",
        "USB_PROXY_MEMFD_TEMP_FALLBACK",
        "USB_PROXY_SKIP_BINDING_CHECK",
        "USB_PROXY_ALLOW_PLAIN_BIN",
    }
    filtered_lines = []
    for line in lines:
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match and match.group(1) in forbidden:
            continue
        filtered_lines.append(line)
    changed = filtered_lines != lines
    lines = filtered_lines
    for key, desired in defaults.items():
        pattern = re.compile(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=")
        found = False
        for index, line in enumerate(lines):
            if not pattern.match(line):
                continue
            found = True
            raw_value = line.split("=", 1)[1].split("#", 1)[0].strip().strip("\"'")
            if raw_value != desired:
                lines[index] = f"{key}={desired}"
                changed = True
            break
        if not found:
            lines.append(f"{key}={desired}")
            changed = True
    if changed:
        USB_PROXY_ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        USB_PROXY_ENV_PATH.chmod(0o644)


def install_usb_proxy_runtime_payload(package_dir: Path) -> None:
    payload_root = package_dir / "payload" / "assets" / "usb-proxy"
    if not USB_PROXY_ROOT.exists():
        return
    runtime_files = [
        (payload_root / "board" / "run_usb_proxy.sh", USB_PROXY_ROOT / "board" / "run_usb_proxy.sh"),
        (payload_root / "board" / "find_usb_mouse.sh", USB_PROXY_ROOT / "board" / "find_usb_mouse.sh"),
        (payload_root / "board" / "prepare_usb_proxy.sh", USB_PROXY_ROOT / "board" / "prepare_usb_proxy.sh"),
        (payload_root / "board" / "usb_proxy_board.sh", USB_PROXY_ROOT / "board" / "usb_proxy_board.sh"),
        (payload_root / "board" / "wait_udc_attached.sh", USB_PROXY_ROOT / "board" / "wait_udc_attached.sh"),
        (payload_root / "board" / "wait_mouse_control_ready.sh", USB_PROXY_ROOT / "board" / "wait_mouse_control_ready.sh"),
        (payload_root / "bin" / "usb-proxy-loader", USB_PROXY_ROOT / "bin" / "usb-proxy-loader"),
        (payload_root / "bin" / "usb-proxy-synthetic", USB_PROXY_ROOT / "bin" / "usb-proxy-synthetic"),
    ]
    installed_any = False
    for source, destination in runtime_files:
        if not source.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        destination.chmod(0o755)
        installed_any = True
    if installed_any:
        ensure_usb_proxy_runtime_env()


def sync_display_boot_edid(root: Path) -> None:
    firmware_edid = Path("/lib/firmware/aiassistance/hdmirx_edid.bin")
    runtime_edid = root / "run" / "hdmirx_custom_edid.bin"
    if not firmware_edid.exists() and runtime_edid.exists():
        firmware_edid.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(runtime_edid, firmware_edid)
        firmware_edid.chmod(0o644)


def apply_boot_cma_policy(manifest: dict) -> tuple[Path | None, str]:
    """Apply a package-declared CMA setting and return the old text for rollback."""
    if (
        str(manifest.get("type") or "").strip() != "full"
        or str(manifest.get("boot_cma") or "").strip() != "1G"
    ):
        return None, "not_requested"
    if not BOOT_ENV_PATH.exists():
        return None, "boot_config_missing"

    original = BOOT_ENV_PATH.read_text(encoding="utf-8")
    lines = original.splitlines()
    changed = False
    found_extraargs = False
    for index, line in enumerate(lines):
        match = re.match(r"^(\s*extraargs\s*=)(.*)$", line)
        if not match:
            continue
        found_extraargs = True
        value = match.group(2)
        replaced, count = re.subn(r"(?<!\S)cma=\S+", "cma=1G", value, count=1)
        if count == 0:
            separator = "" if not value or value.endswith((" ", "\t")) else " "
            replaced = f"{value}{separator}cma=1G"
        if replaced != value:
            lines[index] = f"{match.group(1)}{replaced}"
            changed = True
        break

    if not found_extraargs:
        lines.append("extraargs=cma=1G")
        changed = True

    if not changed:
        return None, "already_configured"

    updated = "\n".join(lines) + ("\n" if original.endswith("\n") else "")
    temporary = BOOT_ENV_PATH.with_name(f".{BOOT_ENV_PATH.name}.aiassistance-{os.getpid()}.tmp")
    mode = BOOT_ENV_PATH.stat().st_mode & 0o7777
    try:
        temporary.write_text(updated, encoding="utf-8")
        temporary.chmod(mode)
        with temporary.open("rb") as stream:
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, BOOT_ENV_PATH)
    finally:
        temporary.unlink(missing_ok=True)
    return BOOT_ENV_PATH, original


def restore_boot_cma_policy(backup: tuple[Path | None, str]) -> None:
    path, original = backup
    if path is None:
        return
    temporary = path.with_name(f".{path.name}.aiassistance-rollback-{os.getpid()}.tmp")
    mode = path.stat().st_mode & 0o7777 if path.exists() else 0o755
    try:
        temporary.write_text(original, encoding="utf-8")
        temporary.chmod(mode)
        with temporary.open("rb") as stream:
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def install_files(root: Path, package_dir: Path, manifest: dict) -> None:
    payload = package_dir / "payload"
    if not payload.exists():
        raise RuntimeError("update package missing payload directory")
    package_type = manifest.get("type", "full")
    allowed = {
        "web": ["web", "Python"],
        "daemon": ["bin", "scripts", "systemd", "lib", "license"],
        "core": ["lib", "core", "bin"],
        "models": ["models"],
        "full": ["bin", "web", "scripts", "systemd", "lib", "license", "Python", "models", "assets"],
    }[package_type]
    for name in allowed:
        copy_tree_contents(payload / name, root / name)
    for script in (root / "scripts").glob("*.sh"):
        script.chmod(script.stat().st_mode | 0o111)
    for script in (root / "scripts").glob("*.py"):
        script.chmod(script.stat().st_mode | 0o111)
    install_usb_proxy_runtime_payload(package_dir)


def promote_staged_themes(root: Path, metadata: dict) -> list[dict[str, Any]]:
    records = metadata.get("themes") if isinstance(metadata.get("themes"), list) else []
    fallback_theme_id = str(metadata.get("theme_fallback_to_default") or "").strip().lower()
    if fallback_theme_id and not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,47}", fallback_theme_id):
        raise RuntimeError("theme fallback id is invalid")
    if not records and not fallback_theme_id:
        return []
    theme_root = (root / "themes").resolve()
    staging_root = (theme_root / ".staging").resolve()
    state_path = root / "config" / "ui_theme.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        state = {}
    installed = state.get("installed") if isinstance(state.get("installed"), dict) else {}
    active_theme_id = str(state.get("active_theme_id") or "default")
    promoted: list[dict[str, Any]] = []
    rollbacks: list[tuple[Path, Path | None]] = []
    try:
        for record in records:
            if not isinstance(record, dict) or record.get("installed_directly"):
                continue
            theme_id = str(record.get("theme_id") or "")
            version = str(record.get("version") or "")
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,47}", theme_id):
                raise RuntimeError("staged theme id is invalid")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}", version):
                raise RuntimeError("staged theme version is invalid")
            minimum_app_version = str(record.get("min_app_version") or "").strip()
            maximum_app_version = str(record.get("max_app_version") or "").strip()
            if any(
                value and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value)
                for value in (minimum_app_version, maximum_app_version)
            ):
                raise RuntimeError("staged theme compatibility version is invalid")
            staged = Path(str(record.get("staged_path") or "")).resolve()
            if staging_root not in staged.parents or not staged.is_dir():
                raise RuntimeError("staged theme path is unsafe or missing")
            manifest_path = staged / "theme.json"
            try:
                theme_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"staged theme manifest is invalid: {exc}") from exc
            if theme_manifest.get("id") != theme_id or theme_manifest.get("version") != version:
                raise RuntimeError("staged theme identity changed before promotion")
            target_parent = theme_root / theme_id
            target_parent.mkdir(parents=True, exist_ok=True)
            target = target_parent / version
            backup = target_parent / f".{version}.update-backup"
            remove_path(backup)
            previous: Path | None = None
            if target.exists():
                target.replace(backup)
                previous = backup
            try:
                staged.replace(target)
            except Exception:
                if previous and previous.exists() and not target.exists():
                    previous.replace(target)
                raise
            rollbacks.append((target, previous))
            installed[theme_id] = {
                "version": version,
                "color_scheme": str(record.get("color_scheme") or "system"),
                "min_app_version": minimum_app_version,
                "max_app_version": maximum_app_version,
            }
            if record.get("activate") and active_theme_id == theme_id:
                state["active_version"] = version
            promoted.append({"theme_id": theme_id, "version": version})
        if fallback_theme_id and active_theme_id == fallback_theme_id:
            state["active_theme_id"] = "default"
            state["active_version"] = ""
        state["installed"] = installed
        state.setdefault("active_theme_id", active_theme_id)
        write_json_file(state_path, state)
    except Exception:
        for target, previous in reversed(rollbacks):
            remove_path(target)
            if previous and previous.exists():
                previous.replace(target)
        raise
    for _target, previous in rollbacks:
        if previous:
            remove_path(previous)
    for record in records:
        staged_value = str(record.get("staged_path") or "") if isinstance(record, dict) else ""
        if staged_value:
            staged = Path(staged_value).resolve()
            transaction_dir = staged.parents[1] if len(staged.parents) >= 2 else None
            if transaction_dir and transaction_dir.parent == staging_root:
                remove_path(transaction_dir)
    return promoted


def cleanup_staged_themes(root: Path, metadata: dict) -> None:
    staging_root = ((root / "themes").resolve() / ".staging").resolve()
    for record in metadata.get("themes", []) if isinstance(metadata.get("themes"), list) else []:
        if not isinstance(record, dict) or not record.get("staged_path"):
            continue
        staged = Path(str(record.get("staged_path"))).resolve()
        if staging_root not in staged.parents or len(staged.parents) < 2:
            continue
        transaction_dir = staged.parents[1]
        if transaction_dir.parent == staging_root:
            remove_path(transaction_dir)


def install_systemd_units(root: Path) -> None:
    systemd_dir = root / "systemd"
    if not systemd_dir.exists():
        return
    services = [
        SERVICE_PERFORMANCE,
        SERVICE_KMBOXNET_USB,
        SERVICE_MAKCU,
        SERVICE_DAEMON,
        SERVICE_WEB,
        SERVICE_WIFI_BOOTSTRAP,
        SERVICE_SELFHEAL,
        TIMER_SELFHEAL,
        "aiassistance-edid-apply.service",
    ]
    for service in services:
        source = systemd_dir / service
        if source.exists():
            text = source.read_text(encoding="utf-8").replace("__ROOT__", str(root))
            Path("/etc/systemd/system", service).write_text(text, encoding="utf-8")
    run(["systemctl", "daemon-reload"], check=False)
    for service in services:
        if Path("/etc/systemd/system", service).exists():
            run(["systemctl", "enable", service], check=False)


def prune_non_runtime_files(root: Path) -> None:
    for path in [root / "docs", root / "systemd"]:
        if path.exists():
            shutil.rmtree(path)
    (root / "install.sh").unlink(missing_ok=True)
    scripts_dir = root / "scripts"
    for name in NON_RUNTIME_SCRIPTS:
        (scripts_dir / name).unlink(missing_ok=True)
    for base in [root / "Python", root / "scripts", root / "web"]:
        if not base.exists():
            continue
        for cache_dir in base.rglob("__pycache__"):
            shutil.rmtree(cache_dir, ignore_errors=True)


def path_disk_size(path: Path) -> int:
    def stat_size(item: Path, *, follow_symlinks: bool = True) -> int:
        stat = item.stat() if follow_symlinks else item.lstat()
        blocks = getattr(stat, "st_blocks", 0)
        return int(blocks) * 512 if blocks else int(stat.st_size)

    try:
        if path.is_symlink():
            return stat_size(path, follow_symlinks=False)
        if path.is_file():
            return stat_size(path)
        if not path.is_dir():
            return 0
    except OSError:
        return 0

    total = 0
    for current, dirnames, filenames in os.walk(path):
        current_path = Path(current)
        try:
            total += stat_size(current_path)
        except OSError:
            pass
        kept_dirs = []
        for dirname in dirnames:
            child = current_path / dirname
            try:
                if child.is_symlink():
                    total += stat_size(child, follow_symlinks=False)
                    continue
            except OSError:
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs
        for filename in filenames:
            child = current_path / filename
            try:
                total += stat_size(child, follow_symlinks=not child.is_symlink())
            except OSError:
                continue
    return total


def remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def prune_old_rollback_backups(root: Path, current_backup: Path | None = None) -> dict[str, Any]:
    releases_dir = root / "releases"
    result: dict[str, Any] = {
        "keep_count": ROLLBACK_BACKUP_KEEP_COUNT,
        "deleted": [],
        "deleted_count": 0,
        "deleted_bytes": 0,
        "kept": [],
        "errors": [],
    }
    if not releases_dir.exists():
        return result

    releases_dir_resolved = releases_dir.resolve()
    backups: list[tuple[float, Path]] = []
    for path in releases_dir.glob("previous-*"):
        try:
            if path.is_dir() and not path.is_symlink():
                backups.append((path.stat().st_mtime, path))
        except OSError as exc:
            result["errors"].append(f"{path}: {exc}")
    backups.sort(key=lambda item: (item[0], item[1].name), reverse=True)

    keep_names: set[str] = set()
    if current_backup is not None:
        keep_names.add(current_backup.name)
    latest_link = releases_dir / "previous"
    if latest_link.is_symlink():
        try:
            target = latest_link.readlink()
            target_path = target if target.is_absolute() else latest_link.parent / target
            target_path = target_path.resolve()
            if target_path.parent == releases_dir_resolved and target_path.name.startswith("previous-"):
                keep_names.add(target_path.name)
        except OSError as exc:
            result["errors"].append(f"{latest_link}: {exc}")
    keep_names.update(path.name for _, path in backups[:ROLLBACK_BACKUP_KEEP_COUNT])
    result["kept"] = sorted(keep_names)

    for _, path in backups:
        if path.name in keep_names:
            continue
        size = path_disk_size(path)
        try:
            remove_path(path)
        except OSError as exc:
            result["errors"].append(f"{path}: {exc}")
            continue
        result["deleted"].append({"path": str(path), "bytes": size})
        result["deleted_bytes"] += size
    result["deleted_count"] = len(result["deleted"])
    return result


def prune_successful_update_cache(root: Path, preserve_paths: set[Path] | None = None) -> dict[str, Any]:
    updates_dir = root / "updates"
    result: dict[str, Any] = {
        "deleted": [],
        "deleted_count": 0,
        "deleted_bytes": 0,
        "errors": [],
    }
    if not updates_dir.exists():
        return result

    updates_dir_resolved = updates_dir.resolve()
    preserved: set[Path] = set()
    for path in preserve_paths or set():
        try:
            preserved.add(path.resolve())
        except OSError:
            continue
    candidates: dict[Path, None] = {}
    for pattern in UPDATE_CACHE_FILE_PATTERNS:
        for path in updates_dir.glob(pattern):
            if path.is_file() or path.is_symlink():
                candidates[path] = None

    components_dir = updates_dir / "components"
    if components_dir.is_dir():
        for path in components_dir.glob("*.enc"):
            if path.is_file() or path.is_symlink():
                candidates[path] = None

    for path in sorted(candidates):
        try:
            resolved = path.resolve()
        except OSError as exc:
            result["errors"].append(f"{path}: {exc}")
            continue
        if resolved != updates_dir_resolved and updates_dir_resolved not in resolved.parents:
            result["errors"].append(f"{path}: outside updates directory")
            continue
        if resolved in preserved:
            continue
        size = path_disk_size(path)
        try:
            remove_path(path)
        except OSError as exc:
            result["errors"].append(f"{path}: {exc}")
            continue
        result["deleted"].append({"path": str(path), "bytes": size})
        result["deleted_bytes"] += size

    result["deleted_count"] = len(result["deleted"])
    return result


def cleanup_successful_update_artifacts(
    root: Path,
    current_backup: Path | None = None,
    preserve_update_paths: set[Path] | None = None,
) -> dict[str, Any]:
    cleanup: dict[str, Any] = {
        "rollback_backups": {},
        "update_cache": {},
        "errors": [],
    }
    try:
        cleanup["rollback_backups"] = prune_old_rollback_backups(root, current_backup)
    except Exception as exc:
        cleanup["errors"].append(f"rollback backups: {exc}")
    try:
        cleanup["update_cache"] = prune_successful_update_cache(root, preserve_update_paths)
    except Exception as exc:
        cleanup["errors"].append(f"update cache: {exc}")
    return cleanup


def health_check(root: Path) -> None:
    deadline = time.monotonic() + 30.0
    last_error = ""
    state_url = f"http://127.0.0.1:{current_web_port()}/api/state"
    license_url = f"http://127.0.0.1:{current_web_port()}/api/license"
    frontend_url = f"http://127.0.0.1:{current_web_port()}/?view=desktop"
    require_license = (root / "license" / "license.json").exists()
    while time.monotonic() < deadline:
        usb_proxy = run(["systemctl", "is-active", SERVICE_USB_PROXY], check=False)
        daemon = run(["systemctl", "is-active", SERVICE_DAEMON], check=False)
        web = run(["systemctl", "is-active", SERVICE_WEB], check=False)
        state = http_get_ok(state_url)
        frontend = http_get_ok(frontend_url)
        license_payload, license_error = http_get_json(license_url)
        license_state = {}
        core_state = {}
        if isinstance(license_payload, dict) and license_payload.get("ok") is True:
            data = license_payload.get("data")
            if isinstance(data, dict):
                license_state = data.get("license") if isinstance(data.get("license"), dict) else {}
                core_state = data.get("core") if isinstance(data.get("core"), dict) else {}
        license_ok = not require_license or bool(license_state.get("valid"))
        core_ok = not require_license or bool(core_state.get("loaded"))
        if (
            usb_proxy.returncode == 0 and
            daemon.returncode == 0 and
            web.returncode == 0 and
            state.returncode == 0 and
            frontend.returncode == 0 and
            license_ok and
            core_ok
        ):
            return
        last_error = "\n".join(
            part.strip()
            for part in [
                usb_proxy.stderr or usb_proxy.stdout,
                daemon.stderr or daemon.stdout,
                web.stderr or web.stdout,
                state.stderr or state.stdout,
                frontend.stderr or frontend.stdout,
                license_error,
                license_state.get("message", "") if isinstance(license_state, dict) else "",
                core_state.get("message", "") if isinstance(core_state, dict) else "",
            ]
            if part.strip()
        )
        time.sleep(1.0)
    raise RuntimeError(last_error or "health check timed out")


def _replace_recovery_payload(root: Path, package_dir: Path) -> None:
    payload = package_dir / "payload"
    missing = [name for name in RECOVERY_REQUIRED_DIRS if not (payload / name).is_dir()]
    if missing:
        raise RuntimeError(f"recovery full OTA is missing required payload: {', '.join(missing)}")

    for name in RECOVERY_REPLACE_DIRS:
        source = payload / name
        if not source.exists():
            continue
        target = root / name
        remove_path(target)
        shutil.copytree(source, target, symlinks=True)

    copy_tree_contents(payload / "license", root / "license")
    for directory in (root / "license", root / "core", root / "run", root / "updates", root / "releases"):
        directory.mkdir(parents=True, exist_ok=True)
    for script in (root / "scripts").glob("*.sh"):
        script.chmod(script.stat().st_mode | 0o111)
    for script in (root / "scripts").glob("*.py"):
        script.chmod(script.stat().st_mode | 0o111)
    for binary in (root / "bin").iterdir():
        if binary.is_file() and not binary.is_symlink():
            binary.chmod(binary.stat().st_mode | 0o111)

    install_usb_proxy_runtime_payload(package_dir)
    install_systemd_units(root)
    prune_non_runtime_files(root)


def _clear_activation_for_full_recovery(root: Path) -> list[str]:
    paths = [
        root / "license" / "license.json",
        root / "license" / "device.json",
        root / "license" / "device_recovery.json",
        root / "license" / "activation_key.json",
        root / "license" / "core_activation.json",
        root / "license" / "online_grant.json",
        root / "license" / "revoked.json",
        root / "license" / "trial_lock.json",
        root / "license" / "model_key.bin",
        root / "license" / "model_key_activation.json",
        root / "license" / "theme_entitlements.json",
        root / "config" / "ui_theme.json",
        root / "core" / "libai_core.so",
        root / "core" / "libai_core.so.enc",
        USB_PROXY_ROOT / "bin" / "usb-proxy",
        USB_PROXY_ROOT / "bin" / "usb-proxy.enc",
        USB_PROXY_ROOT / "license" / "usb_proxy_activation.json",
    ]
    paths.extend((root / "run").glob("libai_core_*.so"))
    removed: list[str] = []
    for path in paths:
        try:
            if path.exists() or path.is_symlink():
                remove_path(path)
                removed.append(str(path))
        except OSError:
            continue
    shutil.rmtree(USB_PROXY_RUNTIME_DIR, ignore_errors=True)
    shutil.rmtree(MOUSE_RUNTIME_DIR, ignore_errors=True)
    return removed


def recovery_health_check(root: Path) -> None:
    deadline = time.monotonic() + 45.0
    license_url = f"http://127.0.0.1:{current_web_port()}/api/license"
    frontend_url = f"http://127.0.0.1:{current_web_port()}/?view=desktop"
    last_error = ""
    while time.monotonic() < deadline:
        daemon = run(["systemctl", "is-active", SERVICE_DAEMON], check=False)
        web = run(["systemctl", "is-active", SERVICE_WEB], check=False)
        license_response = http_get_ok(license_url)
        frontend_response = http_get_ok(frontend_url)
        if daemon.returncode == 0 and web.returncode == 0 and license_response.returncode == 0 and frontend_response.returncode == 0:
            return
        last_error = "\n".join(
            part.strip()
            for part in [
                daemon.stderr or daemon.stdout,
                web.stderr or web.stdout,
                license_response.stderr or license_response.stdout,
                frontend_response.stderr or frontend_response.stdout,
            ]
            if part.strip()
        )
        time.sleep(1.0)
    raise RuntimeError(last_error or "recovery health check timed out")


def recover(root: Path) -> dict[str, Any]:
    if os.geteuid() != 0:
        raise RuntimeError("updater must run as root")
    status = recovery_status(root, adopt_updates=True)
    if not status.get("available"):
        raise RuntimeError(str(status.get("error") or "no verified local full OTA is available"))
    package_path = Path(str(status["package_path"])).resolve()
    expected_sha = str(status["sha256"])
    if sha256_file(package_path) != expected_sha:
        raise RuntimeError("local recovery package changed before installation")

    version = str(status.get("version") or "")
    write_update_status(
        root,
        status="running",
        stage="recovery_extract",
        message="正在校验并解压本地全量恢复包",
        progress=10,
        version=version,
        package_type="recovery",
        extra={"recovery": True},
    )
    with tempfile.TemporaryDirectory(prefix="aiassistance_recovery_") as tmp:
        package_dir = extract_package(package_path, Path(tmp))
        manifest = load_manifest(package_dir)
        if manifest.get("type") != "full" or str(manifest.get("version") or "") != version:
            raise RuntimeError("local recovery package manifest changed during extraction")

        write_update_status(
            root,
            status="running",
            stage="recovery_stop_services",
            message="正在停止服务并执行本地全量恢复",
            progress=28,
            version=version,
            package_type="recovery",
            extra={"recovery": True},
        )
        for service in (
            SERVICE_WIFI_BOOTSTRAP,
            SERVICE_WEB,
            SERVICE_DAEMON,
            SERVICE_KMBOXNET_USB,
            SERVICE_MAKCU,
            SERVICE_PERFORMANCE,
            SERVICE_USB_PROXY,
        ):
            run(["systemctl", "stop", service], check=False)

        try:
            write_update_status(
                root,
                status="running",
                stage="recovery_install_files",
                message="正在覆盖安装本地全量程序文件",
                progress=48,
                version=version,
                package_type="recovery",
                extra={"recovery": True},
            )
            _replace_recovery_payload(root, package_dir)
            removed = _clear_activation_for_full_recovery(root)
            write_installed_version(root, version)
            installed = {
                "installed": True,
                "version": version,
                "app_version": version,
                "type": "recovery",
                "recovery": True,
                "recovery_package": {
                    key: value
                    for key, value in status.items()
                    if key not in {"package_path", "error"}
                },
                "activation_files_removed": len(removed),
                "completed_at": int(time.time()),
            }
            write_update_status(
                root,
                status="running",
                stage="recovery_restart_services",
                message="全量文件恢复完成，正在启动激活服务",
                progress=82,
                version=version,
                package_type="recovery",
                extra={"recovery": True},
            )
            run(["systemctl", "daemon-reload"], check=False)
            run(["systemctl", "restart", SERVICE_PERFORMANCE], check=False)
            run(["systemctl", "restart", SERVICE_WIFI_BOOTSTRAP], check=False)
            run(["systemctl", "restart", SERVICE_DAEMON])
            run(["systemctl", "restart", SERVICE_WEB])
            recovery_health_check(root)
            write_json_file(root / "updates" / "last-installed.json", installed)
        except Exception:
            run(["systemctl", "restart", SERVICE_DAEMON], check=False)
            run(["systemctl", "restart", SERVICE_WEB], check=False)
            raise

    write_update_status(
        root,
        status="success",
        stage="recovery_complete",
        message="本地全量恢复完成，请重新输入激活码",
        progress=100,
        version=version,
        package_type="recovery",
        extra={"recovery": True},
    )
    return installed


def install(root: Path, package_path: Path | None, metadata_path: Path | None) -> dict:
    if os.geteuid() != 0:
        raise RuntimeError("updater must run as root")
    metadata = load_metadata(metadata_path)
    # A 2026-06-15-era updater leaves a successful full OTA in updates/. Adopt
    # it before this updater can clean that legacy cache during a later update.
    existing_recovery = recovery_status(root, adopt_updates=True)
    version, app_version = resolve_install_versions(metadata, {})
    package_type = str(metadata.get("type", ""))
    write_update_status(
        root,
        status="running",
        stage="extract",
        message="安装服务已启动，正在解压更新包",
        progress=68,
        version=version,
        package_type=package_type,
    )
    with tempfile.TemporaryDirectory(prefix="aiassistance_update_") as tmp:
        package_dir = None
        if package_path is not None:
            package_dir = extract_package(package_path, Path(tmp))
            manifest = load_manifest(package_dir)
            version, app_version = resolve_install_versions(metadata, manifest)
            package_type = str(manifest.get("type") or package_type)
        else:
            manifest = {
                "version": metadata.get("version", ""),
                "type": metadata.get("type", "components"),
            }
            version, app_version = resolve_install_versions(metadata, manifest)
        if package_dir is None and not metadata.get("components"):
            raise RuntimeError("update metadata does not contain an app package or components")
        write_update_status(
            root,
            status="running",
            stage="backup",
            message="正在备份当前版本",
            progress=72,
            version=version,
            package_type=package_type,
        )
        backup_dir = backup_current(root, manifest)
        boot_cma_backup: tuple[Path | None, str] = (None, "")
        write_update_status(
            root,
            status="running",
            stage="stop_services",
            message="正在停止服务并准备替换文件",
            progress=76,
            version=version,
            package_type=package_type,
        )
        run(["systemctl", "stop", SERVICE_WIFI_BOOTSTRAP], check=False)
        run(["systemctl", "stop", SERVICE_WEB], check=False)
        run(["systemctl", "stop", SERVICE_DAEMON], check=False)
        run(["systemctl", "stop", SERVICE_KMBOXNET_USB], check=False)
        run(["systemctl", "stop", SERVICE_MAKCU], check=False)
        run(["systemctl", "stop", SERVICE_PERFORMANCE], check=False)
        run(["systemctl", "stop", SERVICE_USB_PROXY], check=False)
        promoted_themes: list[dict[str, Any]] = []
        try:
            if package_dir is not None:
                write_update_status(
                    root,
                    status="running",
                    stage="install_files",
                    message="正在替换应用文件",
                    progress=82,
                    version=version,
                    package_type=package_type,
                )
                install_files(root, package_dir, manifest)
                write_update_status(
                    root,
                    status="running",
                    stage="configure_boot_memory",
                    message="正在配置启动 CMA 内存预留",
                    progress=84,
                    version=version,
                    package_type=package_type,
                )
                boot_cma_backup = apply_boot_cma_policy(manifest)
                install_systemd_units(root)
                prune_non_runtime_files(root)
            write_update_status(
                root,
                status="running",
                stage="install_components",
                message="正在安装核心和 USB 组件",
                progress=87,
                version=version,
                package_type=package_type,
            )
            install_component_files(root, metadata)
            write_update_status(
                root,
                status="running",
                stage="sync_display_edid",
                message="正在同步 HDMI RX 启动 EDID",
                progress=89,
                version=version,
                package_type=package_type,
            )
            sync_display_boot_edid(root)
            write_update_status(
                root,
                status="running",
                stage="restart_services",
                message="正在重启服务",
                progress=91,
                version=version,
                package_type=package_type,
            )
            run(["systemctl", "restart", SERVICE_PERFORMANCE], check=False)
            run(["systemctl", "restart", SERVICE_USB_PROXY])
            run(["systemctl", "restart", SERVICE_KMBOXNET_USB], check=False)
            run(["systemctl", "restart", SERVICE_MAKCU], check=False)
            run(["systemctl", "restart", SERVICE_WIFI_BOOTSTRAP], check=False)
            run(["systemctl", "restart", SERVICE_DAEMON])
            run(["systemctl", "restart", SERVICE_WEB])
            write_update_status(
                root,
                status="running",
                stage="health_check",
                message="服务已重启，正在做健康检查",
                progress=96,
                version=version,
                package_type=package_type,
            )
            health_check(root)
            scrubber = root / "scripts" / "scrub_usb_proxy_plaintext.py"
            if scrubber.exists():
                run([
                    sys.executable,
                    str(scrubber),
                    "--ai-root",
                    str(root),
                    "--usb-root",
                    str(USB_PROXY_ROOT),
                ], check=False)
            write_installed_version(root, version)
            promoted_themes = promote_staged_themes(root, metadata)
        except Exception:
            write_update_status(
                root,
                status="running",
                stage="rollback",
                message="更新失败，正在回滚到上一版本",
                progress=98,
                version=version,
                package_type=package_type,
            )
            restore_backup(root, backup_dir)
            try:
                restore_boot_cma_policy(boot_cma_backup)
            except OSError:
                pass
            install_systemd_units(root)
            run(["systemctl", "restart", SERVICE_PERFORMANCE], check=False)
            run(["systemctl", "restart", SERVICE_USB_PROXY], check=False)
            run(["systemctl", "restart", SERVICE_KMBOXNET_USB], check=False)
            run(["systemctl", "restart", SERVICE_MAKCU], check=False)
            run(["systemctl", "restart", SERVICE_WIFI_BOOTSTRAP], check=False)
            run(["systemctl", "restart", SERVICE_DAEMON], check=False)
            run(["systemctl", "restart", SERVICE_WEB], check=False)
            cleanup_staged_themes(root, metadata)
            raise
        recovery_result: dict[str, Any] = {
            key: value
            for key, value in existing_recovery.items()
            if key != "package_path"
        }
        preserve_update_paths: set[Path] = set()
        if package_path is not None and manifest.get("type") == "full":
            write_update_status(
                root,
                status="running",
                stage="retain_recovery",
                message="更新成功，正在保留本地全量恢复包",
                progress=97,
                version=version,
                package_type=package_type,
            )
            try:
                retained = retain_recovery_package(
                    root,
                    package_path,
                    str(metadata.get("sha256") or ""),
                )
                recovery_result = {
                    key: value
                    for key, value in retained.items()
                    if key != "package_path"
                }
            except (OSError, RuntimeError) as exc:
                current_recovery = recovery_status(root, adopt_updates=False)
                recovery_result = {
                    key: value
                    for key, value in current_recovery.items()
                    if key != "package_path"
                }
                recovery_result["retention_error"] = str(exc)
                preserve_update_paths.add(package_path)
                if metadata_path is not None:
                    preserve_update_paths.add(metadata_path)
        installed = {
            "installed": True,
            "version": version,
            "app_version": app_version,
            "type": manifest.get("type", "full"),
            "backup": str(backup_dir),
            "metadata": str(metadata_path) if metadata_path else "",
            "recovery_package": recovery_result,
            "components": {
                name: {
                    "version": value.get("activation", {}).get("version", ""),
                    "format": value.get("activation", {}).get("format", ""),
                    "sha256": value.get("activation", {}).get("sha256", ""),
                    "size": value.get("activation", {}).get("size", 0),
                }
                for name, value in (metadata.get("components", {}) if isinstance(metadata.get("components"), dict) else {}).items()
                if isinstance(value, dict)
            },
            "themes": promoted_themes,
        }
        write_update_status(
            root,
            status="running",
            stage="cleanup",
            message="更新成功，正在清理旧回滚备份和更新缓存",
            progress=98,
            version=str(installed.get("version", "")),
            package_type=str(installed.get("type", "")),
        )
        installed["cleanup"] = cleanup_successful_update_artifacts(
            root,
            backup_dir,
            preserve_update_paths,
        )
        (root / "updates").mkdir(parents=True, exist_ok=True)
        (root / "updates" / "last-installed.json").write_text(json.dumps(installed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_update_status(
            root,
            status="success",
            stage="complete",
            message="更新安装成功，页面即将刷新",
            progress=100,
            version=str(installed.get("version", "")),
            package_type=str(installed.get("type", "")),
            extra={"result": installed},
        )
        return installed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/opt/aiassistance")
    sub = parser.add_subparsers(dest="command", required=True)
    install_parser = sub.add_parser("install")
    install_parser.add_argument("package", nargs="?")
    install_parser.add_argument("--metadata", default="")
    sub.add_parser("recovery-status")
    sub.add_parser("recover")
    sub.add_parser("configure-boot-cma")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    try:
        if args.command == "install":
            metadata = Path(args.metadata).resolve() if args.metadata else None
            package = Path(args.package).resolve() if args.package else None
            emit(install(root, package, metadata))
        elif args.command == "recovery-status":
            emit(recovery_status(root, adopt_updates=True))
        elif args.command == "recover":
            emit(recover(root))
        elif args.command == "configure-boot-cma":
            if os.geteuid() != 0:
                raise RuntimeError("boot CMA configuration must run as root")
            backup, result = apply_boot_cma_policy({"type": "full", "boot_cma": "1G"})
            emit({
                "configured": backup is not None or result == "already_configured",
                "changed": backup is not None,
                "result": "updated" if backup is not None else result,
                "path": str(BOOT_ENV_PATH),
            })
        return 0
    except Exception as exc:
        if args.command == "configure-boot-cma":
            emit({"configured": False, "error": str(exc), "path": str(BOOT_ENV_PATH)})
            print(str(exc), file=sys.stderr)
            return 1
        is_recovery = args.command == "recover"
        write_update_status(
            root,
            status="failed",
            stage="failed",
            message="本地全量恢复失败" if is_recovery else "更新失败",
            progress=100,
            package_type="recovery" if is_recovery else "",
            error=str(exc),
            extra={"recovery": True} if is_recovery else None,
        )
        emit({"installed": False, "error": str(exc)})
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
