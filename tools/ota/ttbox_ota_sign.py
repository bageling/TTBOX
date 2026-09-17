#!/usr/bin/env python3
"""TTBOX OTA 离线签名工具（Ed25519 · key_id = ttbox-ota-2026a）。

★★ 契约裁定（工程师裁定，须架构师复核；理由写全，便于推翻）
------------------------------------------------------------
`t1.11-impl-spec.md §3.1` 定义 `UPDATE_SIGN.json` 位于 **tgz 内部**，且其 `sha256`
字段 = **整个 tgz** 的 sha256。这两条**同时成立会自指**：
    改 signature → tgz 内容变 → tgz 的 sha256 变 → 已签的 sha256 立刻失效
（规格 §6 陷阱 3 自己点出了这个循环，并要求"实现二选一并钉死"）。

二选一的两种解法：
  (A) 保"签名在 tgz 内" ⇒ 必须放弃"sha256 = 整个 tgz"，改为"成员的规范化摘要树"；
  (B) 保"sha256 = 整个 tgz" ⇒ 必须把签名移到 tgz **之外**（旁车 `<pkg>.tgz.sign.json`）。

本实现取 **(B)**：保住 §3.1 里"sha256 = 整个 tgz"的字面语义（这条是**完整性判据**，
语义一改，包格式与所有历史包都变），只把签名文件挪到包外。**不实现 (A) 分支** ——
留着未测的兼容分支等于留死代码与绕过面。

★ 私钥纪律（§0.3）：本工具**只读**私钥；私钥默认落在 `tools/ota/.testkeys/`（
已被 .gitignore 排除，永不入库）。仓库内**只有公钥**。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import time
from pathlib import Path

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ImportError:  # pragma: no cover
    sys.stderr.write("需要 cryptography：apt-get install -y python3-cryptography\n")
    raise

DEFAULT_KEY_ID = "ttbox-ota-2026a"
TOOLS_DIR = Path(__file__).resolve().parent
KEYS_DIR = TOOLS_DIR / "keys"            # 只放公钥（入库）
TESTKEYS_DIR = TOOLS_DIR / ".testkeys"   # 私钥（.gitignore 排除，不入库）

# 署名对象的字段集（顺序固定；canonical 时按 sort_keys 排序）
SIGNED_FIELDS = ("sha256", "version", "built_at", "key_id")


def canonical(rec: dict) -> bytes:
    """canonical JSON：键排序、无多余空白、UTF-8（与授权侧同一纪律，design §A3.4）。"""
    return json.dumps(rec, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def unsigned_record(tgz_path: str | Path, version: str, key_id: str,
                    built_at: int | None = None) -> dict:
    """构造**待签**记录（不含 signature 字段）。"""
    return {
        "sha256": sha256_file(tgz_path),
        "version": str(version),
        "built_at": int(built_at if built_at is not None else time.time()),
        "key_id": str(key_id),
    }


def sign_record(unsigned: dict, priv_pem_bytes: bytes) -> str:
    """返回 base64(Ed25519 签名)。署名对象 = 四字段 canonical JSON。"""
    priv = serialization.load_pem_private_key(priv_pem_bytes, password=None)
    if not isinstance(priv, Ed25519PrivateKey):
        raise TypeError("OTA 私钥必须是 Ed25519；实得 %r" % type(priv).__name__)
    return base64.b64encode(priv.sign(canonical(unsigned))).decode("ascii")


def sign_package(tgz_path: str, version: str, key_id: str,
                 priv_pem_path: str, built_at: int | None = None) -> Path:
    """对 tgz 签名，产出旁车 `<tgz>.sign.json`；返回旁车路径。"""
    unsigned = unsigned_record(tgz_path, version, key_id, built_at)
    sig = sign_record(unsigned, Path(priv_pem_path).read_bytes())
    out = Path(str(tgz_path) + ".sign.json")
    out.write_text(json.dumps({**unsigned, "signature": sig},
                              sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def gen_keypair(out_dir: Path, key_id: str) -> tuple[Path, Path]:
    """生成 Ed25519 密钥对；私钥写 `.testkeys/<key_id>.priv.pem`（不入库），公钥写 `keys/<key_id>.pub`。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    priv = Ed25519PrivateKey.generate()
    priv_path = out_dir / f"{key_id}.priv.pem"
    pub_path = KEYS_DIR / f"{key_id}.pub"
    priv_path.write_bytes(priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()))
    priv_path.chmod(0o600)
    pub_path.write_bytes(priv.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo))
    return priv_path, pub_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="TTBOX OTA 离线签名工具（Ed25519）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gen-key", help="生成密钥对（私钥落 .testkeys/，不入库）")
    g.add_argument("--key-id", default=DEFAULT_KEY_ID)
    g.add_argument("--out-dir", default=str(TESTKEYS_DIR))

    s = sub.add_parser("sign", help="对 tgz 签名（产出旁车 .sign.json）")
    s.add_argument("tgz")
    s.add_argument("version")
    s.add_argument("--key-id", default=DEFAULT_KEY_ID)
    s.add_argument("--priv", default=str(TESTKEYS_DIR / f"{DEFAULT_KEY_ID}.priv.pem"))
    s.add_argument("--built-at", type=int, default=None)

    a = ap.parse_args(argv)
    if a.cmd == "gen-key":
        p, pub = gen_keypair(Path(a.out_dir), a.key_id)
        print(f"私钥（不入库）: {p}")
        print(f"公钥（入库）  : {pub}")
        return 0
    if a.cmd == "sign":
        out = sign_package(a.tgz, a.version, a.key_id, a.priv, a.built_at)
        print(f"已签名: {out}")
        print(f"  sha256 = {sha256_file(a.tgz)}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
