from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any, Callable


THEME_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")
THEME_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
APP_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
ALLOWED_RUNTIME_SUFFIXES = {".css", ".png", ".jpg", ".jpeg", ".webp", ".woff2"}
ALLOWED_PREVIEW_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
MAX_PACKAGE_BYTES = 25 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_FILES = 256


class ThemeError(RuntimeError):
    pass


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    return value


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_archive_name(value: str) -> str:
    normalized = str(value or "").replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    pure = PurePosixPath(normalized)
    if not normalized or pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        return ""
    return pure.as_posix()


def _allowed_file(name: str) -> bool:
    if name == "theme.json":
        return True
    suffix = Path(name).suffix.lower()
    if name.startswith("runtime/"):
        return suffix in ALLOWED_RUNTIME_SUFFIXES
    if name.startswith("previews/"):
        return suffix in ALLOWED_PREVIEW_SUFFIXES
    return False


def _valid_theme_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if THEME_ID_RE.fullmatch(text) else ""


def _valid_version(value: Any) -> str:
    text = str(value or "").strip()
    return text if THEME_VERSION_RE.fullmatch(text) else ""


def _valid_purchase_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 2048:
        return ""
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return ""
    return text


def _compare_version_text(left: Any, right: Any) -> int:
    left_parts = re.findall(r"\d+|[A-Za-z]+", str(left or ""))
    right_parts = re.findall(r"\d+|[A-Za-z]+", str(right or ""))
    for index in range(max(len(left_parts), len(right_parts))):
        left_part = left_parts[index] if index < len(left_parts) else ""
        right_part = right_parts[index] if index < len(right_parts) else ""
        if left_part == right_part:
            continue
        if left_part.isdigit() and right_part.isdigit():
            return int(left_part) - int(right_part)
        return -1 if left_part < right_part else 1
    return 0


def _version_compatible(manifest: dict[str, Any], app_version: str) -> bool:
    current = str(app_version or "").strip()
    if not current:
        return True
    minimum = str(manifest.get("min_app_version") or "").strip()
    maximum = str(manifest.get("max_app_version") or "").strip()
    return not (
        (minimum and _compare_version_text(current, minimum) < 0)
        or (maximum and _compare_version_text(current, maximum) > 0)
    )


def _compatibility_values(source: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    for field in ("min_app_version", "max_app_version"):
        value = str(source.get(field) or "").strip()
        if value and not APP_VERSION_RE.fullmatch(value):
            raise ThemeError("主题系统兼容版本格式无效")
        values[field] = value
    if (
        values["min_app_version"]
        and values["max_app_version"]
        and _compare_version_text(values["min_app_version"], values["max_app_version"]) > 0
    ):
        raise ThemeError("主题最低系统版本不能高于最高系统版本")
    return values


class ThemeManager:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.theme_root = Path(os.environ.get("AIASSISTANCE_THEME_ROOT", self.root / "themes")).resolve()
        self.state_path = Path(os.environ.get("AIASSISTANCE_THEME_STATE", self.root / "config" / "ui_theme.json")).resolve()
        self.entitlements_path = Path(
            os.environ.get("AIASSISTANCE_THEME_ENTITLEMENTS", self.root / "license" / "theme_entitlements.json")
        ).resolve()
        self.catalog_path = self.theme_root / "catalog.json"

    def _state(self) -> dict[str, Any]:
        value = _read_json(self.state_path, {})
        installed = value.get("installed") if isinstance(value, dict) else {}
        return {
            "active_theme_id": _valid_theme_id(value.get("active_theme_id")) or "default",
            "active_version": _valid_version(value.get("active_version")),
            "installed": installed if isinstance(installed, dict) else {},
        }

    def _save_state(self, state: dict[str, Any]) -> None:
        _write_json_atomic(self.state_path, state)

    def _fallback_default(self, state: dict[str, Any]) -> None:
        if state.get("active_theme_id") == "default" and not state.get("active_version"):
            return
        state["active_theme_id"] = "default"
        state["active_version"] = ""
        self._save_state(state)

    def _manifest_structure(self, theme_id: str, version: str) -> tuple[dict[str, Any], dict[str, dict[str, Any]]] | None:
        root = (self.theme_root / theme_id / version).resolve()
        manifest = _read_json(root / "theme.json", {})
        if not isinstance(manifest, dict) or (
            manifest.get("format") != "aiassistance-theme-v1"
            or manifest.get("ui_brand") != "yu"
            or manifest.get("id") != theme_id
            or manifest.get("version") != version
            or manifest.get("color_scheme") not in {"light", "dark", "system"}
        ):
            return None
        declared: dict[str, dict[str, Any]] = {}
        for item in manifest.get("files", []) if isinstance(manifest.get("files"), list) else []:
            if not isinstance(item, dict):
                return None
            name = _safe_archive_name(str(item.get("path") or ""))
            digest = str(item.get("sha256") or "").lower()
            size = item.get("size")
            if (
                not name
                or name in declared
                or not _allowed_file(name)
                or not re.fullmatch(r"[a-f0-9]{64}", digest)
                or not isinstance(size, int)
                or size < 0
                or size > MAX_FILE_BYTES
            ):
                return None
            declared[name] = {"sha256": digest, "size": size}
        styles = manifest.get("styles") if isinstance(manifest.get("styles"), list) else []
        if not styles or any(
            not isinstance(style, str)
            or not style.startswith("runtime/")
            or not style.endswith(".css")
            or style not in declared
            for style in styles
        ):
            return None
        activation_style = str(manifest.get("activation_style") or "")
        if activation_style:
            return None
        return manifest, declared

    @staticmethod
    def _runtime_file_valid(root: Path, name: str, expected: dict[str, Any]) -> bool:
        target = (root / name).resolve()
        try:
            return bool(
                root in target.parents
                and target.is_file()
                and target.stat().st_size == expected.get("size")
                and _sha256_path(target) == expected.get("sha256")
            )
        except OSError:
            return False

    def _validated_manifest(self, theme_id: str, version: str, *, verify_all: bool) -> dict[str, Any] | None:
        structured = self._manifest_structure(theme_id, version)
        if not structured:
            return None
        manifest, declared = structured
        if verify_all:
            root = (self.theme_root / theme_id / version).resolve()
            runtime_files = {name: item for name, item in declared.items() if name.startswith("runtime/")}
            if not runtime_files or any(
                not self._runtime_file_valid(root, name, item)
                for name, item in runtime_files.items()
            ):
                return None
        return manifest

    @staticmethod
    def _effective_manifest(manifest: dict[str, Any], installed: dict[str, Any] | None) -> dict[str, Any]:
        effective = dict(manifest)
        if isinstance(installed, dict):
            for field in ("min_app_version", "max_app_version"):
                if field in installed:
                    effective[field] = str(installed.get(field) or "").strip()
        return effective

    @staticmethod
    def _catalog_release_compatibility(theme: dict[str, Any], version: str) -> dict[str, str] | None:
        releases = theme.get("release_compatibility") if isinstance(theme, dict) else []
        for release in releases if isinstance(releases, list) else []:
            if isinstance(release, dict) and _valid_version(release.get("version")) == version:
                return _compatibility_values(release)
        return None

    def _entitlements(self) -> dict[str, Any]:
        value = _read_json(self.entitlements_path, {})
        return value if isinstance(value, dict) else {}

    def _owned_ids(self, license_id: str) -> set[str]:
        entitlements = self._entitlements()
        if not license_id or entitlements.get("license_id") != license_id:
            return set()
        return {_valid_theme_id(item) for item in entitlements.get("theme_ids", []) if _valid_theme_id(item)}

    def installed_for_update(self) -> dict[str, Any]:
        state = self._state()
        installed = []
        for theme_id, record in state["installed"].items():
            normalized_id = _valid_theme_id(theme_id)
            version = _valid_version(record.get("version") if isinstance(record, dict) else "")
            if normalized_id and version:
                installed.append({"id": normalized_id, "version": version})
        return {"active_theme_id": state["active_theme_id"], "installed": installed}

    def apply_catalog(self, catalog: dict[str, Any], license_id: str, app_version: str = "") -> dict[str, Any]:
        themes = catalog.get("themes") if isinstance(catalog, dict) else []
        if not isinstance(themes, list):
            raise ThemeError("主题目录格式无效")
        catalog = dict(catalog)
        catalog["purchase_url"] = _valid_purchase_url(catalog.get("purchase_url"))
        owned = {_valid_theme_id(item.get("id")) for item in themes if isinstance(item, dict) and item.get("owned")}
        owned.discard("")
        previous_snapshot = self._entitlements()
        previous_owned = {
            _valid_theme_id(item)
            for item in previous_snapshot.get("theme_ids", [])
            if _valid_theme_id(item)
        }
        if previous_snapshot.get("license_id") == license_id:
            previous_owned = self._owned_ids(license_id)
        revoked = previous_owned - owned
        state = self._state()
        themes_by_id = {
            _valid_theme_id(item.get("id")): item
            for item in themes
            if isinstance(item, dict) and _valid_theme_id(item.get("id"))
        }
        for theme_id in revoked:
            shutil.rmtree(self.theme_root / theme_id, ignore_errors=True)
            state["installed"].pop(theme_id, None)
        if state["active_theme_id"] in revoked:
            state["active_theme_id"] = "default"
            state["active_version"] = ""
        for theme_id, installed in state["installed"].items():
            if not isinstance(installed, dict):
                continue
            version = _valid_version(installed.get("version"))
            theme = themes_by_id.get(_valid_theme_id(theme_id))
            compatibility = self._catalog_release_compatibility(theme, version) if theme and version else None
            if compatibility is not None:
                installed.update(compatibility)
        active_id = state["active_theme_id"]
        active_installed = state["installed"].get(active_id)
        active_version = _valid_version(active_installed.get("version") if isinstance(active_installed, dict) else "")
        active_manifest = self._validated_manifest(active_id, active_version, verify_all=True) if active_version else None
        if active_id != "default" and (
            not active_manifest
            or not _version_compatible(self._effective_manifest(active_manifest, active_installed), app_version)
        ):
            state["active_theme_id"] = "default"
            state["active_version"] = ""
        self._save_state(state)
        _write_json_atomic(self.entitlements_path, {
            "format": "aiassistance-theme-entitlements-v1",
            "license_id": license_id,
            "theme_ids": sorted(owned),
        })
        _write_json_atomic(self.catalog_path, catalog)
        return self.public_state(catalog, license_id, app_version)

    def public_state(self, catalog: dict[str, Any] | None, license_id: str, app_version: str = "") -> dict[str, Any]:
        state = self._state()
        source = catalog if isinstance(catalog, dict) else _read_json(self.catalog_path, {"themes": []})
        themes = source.get("themes") if isinstance(source, dict) else []
        output = [{
            "id": "default",
            "title": "YU 默认主题",
            "description": "系统内置主题，始终可用。",
            "published": True,
            "owned": True,
            "compatible": True,
            "latest_version": "built-in",
            "previews": [],
        }]
        installed = state["installed"]
        owned_ids = self._owned_ids(license_id)
        state_changed = False
        for item in themes if isinstance(themes, list) else []:
            if not isinstance(item, dict):
                continue
            theme_id = _valid_theme_id(item.get("id"))
            if not theme_id:
                continue
            record = dict(item)
            local = installed.get(theme_id) if isinstance(installed.get(theme_id), dict) else {}
            local_version = _valid_version(local.get("version"))
            local_manifest = self._validated_manifest(theme_id, local_version, verify_all=True) if local_version else None
            if local and (not local_version or not local_manifest):
                installed.pop(theme_id, None)
                local = {}
                local_version = ""
                state_changed = True
                if state["active_theme_id"] == theme_id:
                    state["active_theme_id"] = "default"
                    state["active_version"] = ""
            installed_compatible = bool(
                local_manifest
                and _version_compatible(self._effective_manifest(local_manifest, local), app_version)
            ) if local else False
            if local and not installed_compatible and state["active_theme_id"] == theme_id:
                state["active_theme_id"] = "default"
                state["active_version"] = ""
                state_changed = True
            record["owned"] = theme_id in owned_ids and bool(item.get("owned"))
            record["installed"] = bool(local)
            record["installed_version"] = local_version
            record["installed_compatible"] = installed_compatible
            record["active"] = state["active_theme_id"] == theme_id
            record["update_available"] = bool(
                record["installed_version"]
                and record.get("latest_version")
                and record["installed_version"] != record.get("latest_version")
            )
            output.append(record)
        if state_changed:
            self._save_state(state)
        output[0]["installed"] = True
        output[0]["active"] = state["active_theme_id"] == "default"
        output[0]["installed_version"] = "built-in"
        output[0]["update_available"] = False
        return {
            "active_theme_id": state["active_theme_id"],
            "active_version": state["active_version"],
            "purchase_url": _valid_purchase_url(source.get("purchase_url")) if isinstance(source, dict) else "",
            "themes": output,
            "offline": catalog is None,
        }

    def cached_catalog(self) -> dict[str, Any]:
        value = _read_json(self.catalog_path, {"themes": []})
        return value if isinstance(value, dict) else {"themes": []}

    def set_active(self, theme_id_value: str, license_id: str, app_version: str = "") -> dict[str, Any]:
        theme_id = str(theme_id_value or "").strip().lower()
        state = self._state()
        if theme_id == "default":
            state["active_theme_id"] = "default"
            state["active_version"] = ""
            self._save_state(state)
            return state
        if not _valid_theme_id(theme_id) or theme_id not in self._owned_ids(license_id):
            raise ThemeError("尚未购买该主题")
        installed = state["installed"].get(theme_id)
        version = _valid_version(installed.get("version") if isinstance(installed, dict) else "")
        manifest = self._validated_manifest(theme_id, version, verify_all=True) if version else None
        if not manifest:
            raise ThemeError("主题尚未安装")
        if not _version_compatible(self._effective_manifest(manifest, installed), app_version):
            raise ThemeError("当前系统版本与该主题不兼容")
        state["active_theme_id"] = theme_id
        state["active_version"] = version
        self._save_state(state)
        return state

    def active_manifest(self, license_doc: dict[str, Any], ui_brand: str, app_version: str = "") -> dict[str, Any] | None:
        if ui_brand != "yu":
            return None
        license_id = str(license_doc.get("license_id") or "")
        state = self._state()
        theme_id = state["active_theme_id"]
        version = state["active_version"]
        if theme_id == "default":
            return None
        if theme_id not in self._owned_ids(license_id):
            self._fallback_default(state)
            return None
        manifest = self._validated_manifest(theme_id, version, verify_all=True)
        installed = state["installed"].get(theme_id)
        if not manifest or not _version_compatible(self._effective_manifest(manifest, installed), app_version):
            self._fallback_default(state)
            return None
        return self._effective_manifest(manifest, installed)

    def asset_path(self, theme_id_value: str, version_value: str, relative_value: str, license_doc: dict[str, Any]) -> Path:
        theme_id = _valid_theme_id(theme_id_value)
        version = _valid_version(version_value)
        relative = _safe_archive_name(relative_value)
        license_id = str(license_doc.get("license_id") or "")
        if not theme_id or not version or not relative.startswith("runtime/") or theme_id not in self._owned_ids(license_id):
            raise ThemeError("主题资源不可用")
        target_root = (self.theme_root / theme_id / version).resolve()
        target = (target_root / relative).resolve()
        structured = self._manifest_structure(theme_id, version)
        declared = structured[1] if structured else {}
        if (
            target_root not in target.parents
            or target.suffix.lower() not in ALLOWED_RUNTIME_SUFFIXES
            or relative not in declared
            or not self._runtime_file_valid(target_root, relative, declared[relative])
        ):
            state = self._state()
            if state["active_theme_id"] == theme_id and state["active_version"] == version:
                self._fallback_default(state)
            raise ThemeError("主题资源不存在")
        return target

    def _download(self, url: str, destination: Path, progress: Callable[[int, int], None] | None = None) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            with urllib.request.urlopen(url, timeout=120) as response, destination.open("wb") as output:
                total = int(response.headers.get("Content-Length") or 0)
                if total > MAX_PACKAGE_BYTES:
                    raise ThemeError("主题包超过 25 MB 限制")
                downloaded = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > MAX_PACKAGE_BYTES:
                        raise ThemeError("主题包超过 25 MB 限制")
                    output.write(chunk)
                    if progress:
                        progress(downloaded, total)
        except (urllib.error.URLError, OSError) as exc:
            destination.unlink(missing_ok=True)
            raise ThemeError(f"主题包下载失败：{getattr(exc, 'reason', None) or exc}") from exc

    def _validate_and_extract(self, package_path: Path, destination: Path, expected: dict[str, Any]) -> dict[str, Any]:
        if package_path.stat().st_size > MAX_PACKAGE_BYTES:
            raise ThemeError("主题包超过 25 MB 限制")
        expected_sha = str(expected.get("sha256") or "").lower()
        if not re.fullmatch(r"[a-f0-9]{64}", expected_sha) or _sha256_path(package_path) != expected_sha:
            raise ThemeError("主题包 SHA-256 校验失败")
        try:
            archive = tarfile.open(package_path, "r:gz")
        except (tarfile.TarError, OSError) as exc:
            raise ThemeError(f"主题包格式无效：{exc}") from exc
        with archive:
            files: dict[str, tarfile.TarInfo] = {}
            expanded = 0
            for member in archive.getmembers():
                name = _safe_archive_name(member.name)
                if not name:
                    raise ThemeError("主题包包含不安全路径")
                if member.isdir():
                    continue
                if not member.isfile() or member.issym() or member.islnk():
                    raise ThemeError(f"主题包包含链接或特殊文件：{name}")
                if not _allowed_file(name):
                    raise ThemeError(f"主题包包含禁止文件：{name}")
                if name in files:
                    raise ThemeError(f"主题包包含重复文件：{name}")
                if member.size < 0 or member.size > MAX_FILE_BYTES:
                    raise ThemeError(f"主题文件过大：{name}")
                expanded += member.size
                if expanded > MAX_EXPANDED_BYTES or len(files) >= MAX_FILES:
                    raise ThemeError("主题包展开大小或文件数量超限")
                files[name] = member
            manifest_member = files.get("theme.json")
            if not manifest_member:
                raise ThemeError("主题包缺少 theme.json")
            manifest_stream = archive.extractfile(manifest_member)
            try:
                manifest = json.loads((manifest_stream.read() if manifest_stream else b"").decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ThemeError(f"主题清单无效：{exc}") from exc
            theme_id = _valid_theme_id(manifest.get("id"))
            version = _valid_version(manifest.get("version"))
            if (
                manifest.get("format") != "aiassistance-theme-v1"
                or manifest.get("ui_brand") != "yu"
                or theme_id != _valid_theme_id(expected.get("theme_id"))
                or version != _valid_version(expected.get("version"))
            ):
                raise ThemeError("主题包身份与服务器记录不匹配")
            declared = {item.get("path"): item for item in manifest.get("files", []) if isinstance(item, dict)}
            if set(declared) != set(files) - {"theme.json"}:
                raise ThemeError("主题清单文件列表不完整")
            styles = manifest.get("styles") if isinstance(manifest.get("styles"), list) else []
            if not styles or any(style not in files or not str(style).startswith("runtime/") or not str(style).endswith(".css") for style in styles):
                raise ThemeError("主题样式入口无效")
            activation_style = str(manifest.get("activation_style") or "")
            if activation_style:
                raise ThemeError("主题不允许覆盖统一激活页")
            destination.mkdir(parents=True, exist_ok=True)
            for name, member in files.items():
                stream = archive.extractfile(member)
                data = stream.read() if stream else b""
                item = declared.get(name) if name != "theme.json" else None
                if item is not None and (
                    int(item.get("size", -1)) != len(data)
                    or str(item.get("sha256") or "").lower() != hashlib.sha256(data).hexdigest()
                ):
                    raise ThemeError(f"主题文件校验失败：{name}")
                if name.startswith("previews/"):
                    continue
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            return manifest

    def install_package(
        self,
        package: dict[str, Any],
        license_id: str,
        *,
        activate: bool,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        theme_id = _valid_theme_id(package.get("theme_id"))
        version = _valid_version(package.get("version"))
        url = str(package.get("download_url") or "")
        if not theme_id or not version or not url or theme_id not in self._owned_ids(license_id):
            raise ThemeError("主题下载信息或购买权益无效")
        self.theme_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="theme-install-", dir=self.theme_root) as temp_name:
            temp_root = Path(temp_name)
            package_path = temp_root / "theme.tar.gz"
            extracted = temp_root / "extracted"
            self._download(url, package_path, progress)
            manifest = self._validate_and_extract(package_path, extracted, package)
            package_manifest = package.get("manifest") if isinstance(package.get("manifest"), dict) else manifest
            compatibility = _compatibility_values(package_manifest)
            target_parent = self.theme_root / theme_id
            target_parent.mkdir(parents=True, exist_ok=True)
            target = target_parent / version
            backup = target_parent / f".{version}.old"
            shutil.rmtree(backup, ignore_errors=True)
            if target.exists():
                os.replace(target, backup)
            try:
                os.replace(extracted, target)
                state = self._state()
                state["installed"][theme_id] = {
                    "version": version,
                    "color_scheme": manifest.get("color_scheme", "system"),
                    **compatibility,
                }
                if activate or state["active_theme_id"] == theme_id:
                    state["active_theme_id"] = theme_id
                    state["active_version"] = version
                self._save_state(state)
            except Exception:
                shutil.rmtree(target, ignore_errors=True)
                if backup.exists():
                    os.replace(backup, target)
                raise
            shutil.rmtree(backup, ignore_errors=True)
        return {"theme_id": theme_id, "version": version, "active": state["active_theme_id"] == theme_id}

    def stage_package(
        self,
        package: dict[str, Any],
        license_id: str,
        transaction: str,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        theme_id = _valid_theme_id(package.get("theme_id"))
        version = _valid_version(package.get("version"))
        url = str(package.get("download_url") or "")
        transaction_id = re.sub(r"[^A-Za-z0-9._-]", "", str(transaction or ""))[:64]
        if not theme_id or not version or not url or not transaction_id or theme_id not in self._owned_ids(license_id):
            raise ThemeError("主题更新信息或购买权益无效")
        staging_root = (self.theme_root / ".staging" / transaction_id).resolve()
        destination = staging_root / theme_id / version
        shutil.rmtree(destination, ignore_errors=True)
        staging_root.mkdir(parents=True, exist_ok=True)
        package_path = staging_root / f"{theme_id}-{version}.tar.gz"
        try:
            self._download(url, package_path, progress)
            manifest = self._validate_and_extract(package_path, destination, package)
            package_manifest = package.get("manifest") if isinstance(package.get("manifest"), dict) else manifest
            compatibility = _compatibility_values(package_manifest)
        except Exception:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        finally:
            package_path.unlink(missing_ok=True)
        return {
            "theme_id": theme_id,
            "version": version,
            "staged_path": str(destination),
            "color_scheme": str(manifest.get("color_scheme") or "system"),
            **compatibility,
            "activate": self._state()["active_theme_id"] == theme_id,
        }

    def cleanup_staging(self, transaction: str) -> None:
        transaction_id = re.sub(r"[^A-Za-z0-9._-]", "", str(transaction or ""))[:64]
        if transaction_id:
            shutil.rmtree(self.theme_root / ".staging" / transaction_id, ignore_errors=True)

    def promote_staged(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        state = self._state()
        staging_root = (self.theme_root / ".staging").resolve()
        rollbacks: list[tuple[Path, Path | None]] = []
        promoted: list[dict[str, Any]] = []
        try:
            for record in records:
                theme_id = _valid_theme_id(record.get("theme_id"))
                version = _valid_version(record.get("version"))
                staged = Path(str(record.get("staged_path") or "")).resolve()
                if not theme_id or not version or staging_root not in staged.parents or not (staged / "theme.json").is_file():
                    raise ThemeError("待提交主题目录无效")
                manifest = _read_json(staged / "theme.json", {})
                if manifest.get("id") != theme_id or manifest.get("version") != version:
                    raise ThemeError("待提交主题身份无效")
                target_parent = self.theme_root / theme_id
                target_parent.mkdir(parents=True, exist_ok=True)
                target = target_parent / version
                backup = target_parent / f".{version}.theme-backup"
                shutil.rmtree(backup, ignore_errors=True)
                previous: Path | None = None
                if target.exists():
                    os.replace(target, backup)
                    previous = backup
                try:
                    os.replace(staged, target)
                except Exception:
                    if previous and previous.exists() and not target.exists():
                        os.replace(previous, target)
                    raise
                rollbacks.append((target, previous))
                state["installed"][theme_id] = {
                    "version": version,
                    "color_scheme": str(record.get("color_scheme") or "system"),
                    **_compatibility_values(record),
                }
                if record.get("activate") and state["active_theme_id"] == theme_id:
                    state["active_version"] = version
                promoted.append({"theme_id": theme_id, "version": version, "active": state["active_theme_id"] == theme_id})
            self._save_state(state)
        except Exception as exc:
            for target, previous in reversed(rollbacks):
                shutil.rmtree(target, ignore_errors=True)
                if previous and previous.exists():
                    os.replace(previous, target)
            if isinstance(exc, ThemeError):
                raise
            raise ThemeError(f"主题版本提交失败：{exc}") from exc
        for _target, previous in rollbacks:
            if previous:
                shutil.rmtree(previous, ignore_errors=True)
        transactions = {Path(str(record.get("staged_path"))).resolve().parents[1] for record in records if record.get("staged_path")}
        for transaction in transactions:
            if transaction.parent == staging_root:
                shutil.rmtree(transaction, ignore_errors=True)
        return promoted
