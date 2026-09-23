"""统一安装来源解析。"""
from __future__ import annotations
import shutil
import urllib.request
from pathlib import Path
from urllib.parse import urlparse
from .models import InstallSource


def resolve_package(request, repository=None, download_dir: str | Path | None = None) -> Path:
    source = request.source.value if isinstance(request.source, InstallSource) else str(request.source)
    if source == InstallSource.LOCAL_FILE.value:
        if not request.path: raise ValueError("local_file 需要 path")
        path = Path(request.path)
        if not path.is_file(): raise FileNotFoundError(path)
        return path
    if source == InstallSource.REPOSITORY.value:
        if repository is None or not request.plugin_id or not request.version: raise ValueError("repository 需要 repository、plugin_id、version")
        return Path(repository.download_package(request.plugin_id, request.version))
    if source == InstallSource.URL.value:
        if not request.url: raise ValueError("url 需要 url")
        # ★ 2026-09-23：URL 来源是从网络拿包，必须两件事都卡住——
        #   ① 只允许 http/https：原来的实现遇到 file:// 会直接把本地路径返回，
        #      等于"从 URL 装一个任意本地文件"，且绕过一切下载校验。
        #   ② 必须提供 sha256：下载来的包不校验完整性就解包执行 bin/entry，
        #      是一条完整的供应链攻击链（verify_integrity 在未提供期望值时会跳过）。
        scheme = urlparse(request.url).scheme.lower()
        if scheme not in ("http", "https"):
            raise ValueError(f"不支持的安装来源协议: {scheme or '(无)'}（仅允许 http/https）")
        if not (getattr(request, "expected_sha256", None) or "").strip():
            raise ValueError("url 来源必须提供 sha256，否则无法校验下载包的完整性")
        target_dir = Path(download_dir or Path.cwd() / ".downloads")
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / Path(request.url.split("?", 1)[0]).name
        urllib.request.urlretrieve(request.url, target)
        return target
    raise ValueError(f"未知安装来源: {source}")
