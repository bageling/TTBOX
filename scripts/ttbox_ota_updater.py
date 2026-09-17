#!/usr/bin/env python3
"""TTBOX OTA 更新器（root 独立进程；由 `systemd-run` 拉起，脱离 Web 存活）。

★ 结构必经步骤（t1.11 §0.1）——**任一前置不通过即终止，绝不进入下一步**：
  ① URL scheme 白名单（仅 https）
  ② 下载到**临时目录**（非正式目录）
  ③ sha256 比对（下载物 vs 签名记录的 `sha256`）
  ④ Ed25519 验签（对 canonical JSON）
  ⑤ 展开到 `releases/<ver>.staging/`
  ⑥ 全量 sha256 复验（RELEASE_MANIFEST.json）
  ⑦ 调 T1.01 原子发布 → 健康检查 → 失败自动 rollback

★ 双因子是 `and`（③ 与 ④ 缺一不可，PRD SEC-01 验收 1）。
★ 失败即删包（§0.2）：`finally` 清临时目录；失败分支清 staging。
★ 签名对象 = **旁车 `<pkg>.tgz.sign.json`**（不在 tgz 内）—— 消除 §6 陷阱 3 的自指，
  详见 `tools/ota/ttbox_ota_sign.py` 文件头的契约裁定。
★ 私钥永不入库；本进程**只有公钥**。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:  # pragma: no cover
    sys.stderr.write("需要 cryptography：apt-get install -y python3-cryptography\n")
    raise

DEFAULT_KEY_ID = "ttbox-ota-2026a"
RELEASES = "/opt/ttbox/releases"
STATE = "/opt/ttbox/state"
STATUS_FILE = os.path.join(STATE, "ota_status.json")
KEYS_DIR = Path(__file__).resolve().parents[1] / "tools" / "ota" / "keys"
INSTALL_SCRIPT = "/opt/ttbox/current/scripts/ttbox_release_install.sh"
HEALTH_UNITS = ("ttbox-core", "ttbox-web", "ttbox-usbproxy")
HEALTH_TIMEOUT_S = 30
SIGNED_FIELDS = ("sha256", "version", "built_at", "key_id")


class OtaError(Exception):
    def __init__(self, state: str, detail: str):
        super().__init__(f"{state}: {detail}")
        self.state = state
        self.detail = detail


def canonical(rec: dict) -> bytes:
    return json.dumps(rec, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- 默认（真实）实现
def default_fetch(url: str, dest: str, timeout: float = 60.0) -> None:
    """仅 https；任何非 https 由调用方先拦（§0.1 ①）。"""
    with urllib.request.urlopen(url, timeout=timeout) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)


def default_install(staging: str, version: str) -> int:
    return subprocess.call([INSTALL_SCRIPT, version, staging, "--activate"])


def default_rollback() -> int:
    return subprocess.call([INSTALL_SCRIPT, "--rollback"])


def _is_active(unit: str) -> bool:
    rc = subprocess.call(["systemctl", "is-active", "--quiet", unit])
    return rc == 0


def _ipc_get_status() -> dict:
    """读 core IPC（unix socket）拿业务能力；失败返回 {}（= 业务不可用）。"""
    sock_path = os.environ.get("TTBOX_IPC_SOCKET", "/run/ttbox/core.sock")
    try:
        import socket as _s
        with _s.socket(_s.AF_UNIX, _s.SOCK_STREAM) as c:
            c.settimeout(2.0)
            c.connect(sock_path)
            c.sendall(b'{"cmd":"GET_STATUS"}')
            raw = c.recv(65536)
        d = json.loads(raw.decode("utf-8", "replace"))
        return d.get("data", {}) if isinstance(d, dict) else {}
    except Exception:
        return {}


def default_health(timeout_s: int = HEALTH_TIMEOUT_S) -> bool:
    """业务能力优先（§0.5）：进程 active **且** IPC 显示模型已加载 + 授权可用。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        units_ok = all(_is_active(u) for u in HEALTH_UNITS)
        st = _ipc_get_status()
        biz_ok = bool(st.get("model_loaded")) and bool(st.get("license_available"))
        if units_ok and biz_ok:
            return True
        time.sleep(1)
    return False


# ---------------------------------------------------------------- 更新器
class OtaUpdater:
    """依赖全部可注入（fetch/install/rollback/health/pubkey），便于单测而不开后门。"""

    def __init__(self, releases_dir: str = RELEASES, status_path: str = STATUS_FILE,
                 keys_dir: Path | str = KEYS_DIR,
                 fetch=default_fetch, install=default_install,
                 rollback=default_rollback, health=default_health,
                 pubkey_pem: bytes | None = None):
        self.releases_dir = releases_dir
        self.status_path = status_path
        self.keys_dir = Path(keys_dir)
        self.fetch = fetch
        self.install = install
        self.rollback = rollback
        self.health = health
        self._pubkey_pem = pubkey_pem

    # -- 状态 --
    def _write_status(self, doc: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self.status_path), exist_ok=True)
            with open(self.status_path, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, sort_keys=True)
        except Exception as e:  # 状态写失败不得掩盖主流程判定
            sys.stderr.write(f"[warn] 写 ota_status 失败: {e!r}\n")

    def _fail(self, state: str, detail: str) -> int:
        self._write_status({"state": "FAILED", "error": state, "detail": detail})
        sys.stderr.write(f"OTA FAILED: {state}: {detail}\n")
        return 1

    # -- 公钥 --
    def _load_pubkey(self, key_id: str) -> Ed25519PublicKey:
        if self._pubkey_pem:
            return serialization.load_pem_public_key(self._pubkey_pem)
        p = self.keys_dir / f"{key_id}.pub"
        if not p.exists():
            raise OtaError("pubkey_missing", f"无该 key_id 的公钥: {key_id}")
        return serialization.load_pem_public_key(p.read_bytes())

    def _verify_signature(self, signs: dict, key_id: str) -> bool:
        if signs.get("key_id") != key_id:
            return False
        if not all(k in signs for k in (*SIGNED_FIELDS, "signature")):
            return False
        unsigned = {k: signs[k] for k in SIGNED_FIELDS}
        try:
            self._load_pubkey(key_id).verify(base64.b64decode(signs["signature"]),
                                             canonical(unsigned))
            return True
        except Exception:
            return False

    # -- 展开（防路径穿越：root 进程解不可信 tar 是 RCE 面） --
    @staticmethod
    def _safe_members(tar: tarfile.TarFile, dest: str):
        dest_real = os.path.realpath(dest)
        out = []
        for m in tar.getmembers():
            name = m.name
            if not name or name.startswith("/") or ".." in Path(name).parts:
                raise OtaError("unsafe_member", f"非法成员名: {name!r}")
            target = os.path.realpath(os.path.join(dest_real, name))
            if not (target == dest_real or target.startswith(dest_real + os.sep)):
                raise OtaError("unsafe_member", f"成员越界: {name!r}")
            out.append(m)
        return out

    def _extract(self, tgz: str, staging: str) -> None:
        os.makedirs(staging, exist_ok=True)
        with tarfile.open(tgz, "r:*") as tar:
            members = self._safe_members(tar, staging)
            payload = [m for m in members if m.name.startswith("payload/")]
            manifest = [m for m in members if m.name == "RELEASE_MANIFEST.json"]
            if not manifest:
                raise OtaError("manifest_missing", "包内缺 RELEASE_MANIFEST.json")
            chosen = manifest + payload
            for m in chosen:
                if m.name == "RELEASE_MANIFEST.json":
                    tar.extract(m, staging)
                else:
                    m.name = m.name[len("payload/"):]      # 去掉 payload/ 前缀
                    if not m.name:
                        continue
                    tar.extract(m, staging)

    def _verify_manifest(self, staging: str) -> bool:
        mp = os.path.join(staging, "RELEASE_MANIFEST.json")
        try:
            doc = json.loads(Path(mp).read_text(encoding="utf-8"))
        except Exception:
            return False
        files = doc.get("files_sha256") or {}
        if not files:
            return False
        for rel, want in files.items():
            fp = os.path.join(staging, rel)
            if not os.path.isfile(fp) or sha256_file(fp) != want:
                return False
        return True

    # -- 主流程 --
    def run(self, url: str, key_id: str = DEFAULT_KEY_ID, version: str | None = None) -> int:
        # ① scheme 白名单
        u = urllib.parse.urlparse(url)
        if u.scheme != "https":
            return self._fail("scheme_rejected", f"non-https URL: {u.scheme or '<空>'}")

        work = tempfile.mkdtemp(prefix="ttbox-ota-", dir="/var/tmp" if os.path.isdir("/var/tmp") else None)
        staging = ""
        try:
            tgz = os.path.join(work, "pkg.tgz")
            # ② 下载（仅 https）
            self.fetch(url, tgz)
            # ②b 旁车签名
            sign_path = os.path.join(work, "pkg.tgz.sign.json")
            self.fetch(url + ".sign.json", sign_path)
            try:
                signs = json.loads(Path(sign_path).read_text(encoding="utf-8"))
            except Exception as e:
                return self._fail("sign_unreadable", f"旁车签名不可解析: {e!r}")

            # ③ 完整性（and 的前半）
            actual = sha256_file(tgz)
            if actual != signs.get("sha256"):
                return self._fail("sha256_mismatch", "package digest != 记录 sha256")
            # ④ 真实性（and 的后半）
            if not self._verify_signature(signs, key_id):
                return self._fail("signature_invalid", "Ed25519 验签失败")

            ver = str(version or signs.get("version") or "").strip()
            if not ver:
                return self._fail("version_missing", "签名记录缺 version")
            staging = os.path.join(self.releases_dir, f"{ver}.staging")

            # ⑤ 展开（仅双因子都过之后）
            try:
                self._extract(tgz, staging)
            except OtaError as e:
                shutil.rmtree(staging, ignore_errors=True)
                return self._fail(e.state, e.detail)
            # ⑥ 全量复验
            if not self._verify_manifest(staging):
                shutil.rmtree(staging, ignore_errors=True)
                return self._fail("manifest_mismatch", "staging sha256 != RELEASE_MANIFEST")
            # ⑦ 原子发布
            rc = self.install(staging, ver)
            if rc != 0:
                shutil.rmtree(staging, ignore_errors=True)
                return self._fail("install_failed", f"release_install rc={rc}")
            # ⑦b 健康检查（业务能力）
            if not self.health(HEALTH_TIMEOUT_S):
                self.rollback()
                shutil.rmtree(staging, ignore_errors=True)
                return self._fail("health_check_failed", "已 rollback")
            self._write_status({"state": "SUCCESS", "version": ver,
                                "sha256": actual, "key_id": key_id})
            return 0
        except OtaError as e:
            return self._fail(e.state, e.detail)
        except Exception as e:  # 未分类异常一律判失败（绝不静默放行）
            return self._fail("unexpected", repr(e))
        finally:
            shutil.rmtree(work, ignore_errors=True)   # §0.2：无论成败，临时目录零残留


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="TTBOX OTA 更新器（root 独立进程）")
    ap.add_argument("url", help="https://…/ttbox-update-<ver>.tgz")
    ap.add_argument("key_id", nargs="?", default=DEFAULT_KEY_ID)
    ap.add_argument("--version", default=None)
    a = ap.parse_args(argv)
    return OtaUpdater().run(a.url, a.key_id, a.version)


if __name__ == "__main__":
    sys.exit(main())
