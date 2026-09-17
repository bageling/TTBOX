#!/usr/bin/env python3
"""T1.11 单测（REG-01）：OTA 验签链路。

★ 注册模型（t1.11 §1 注）：本文件是 **Python 侧**单测，**不并入 C++ `ttbox_core_tests`**
  ⇒ **不改变 `ctest -N` 计数**。

依赖全部注入（fetch/install/rollback/health/pubkey），**不开任何生产后门**：
测试替身只替换"外部副作用"，被测的判定逻辑（scheme/sha256/验签/展开/manifest/回滚）全部是真代码。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HERE))

import ttbox_ota_updater as up  # noqa: E402
import ttbox_ota_sign as sign  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  PASS: " if cond else "  FAIL: ") + name + (" | " + detail if detail else ""))
    if not cond:
        FAILS.append(name)


def sha256f(p: str) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class Fixture:
    """造包 + 密钥 + 替身。私钥只在临时目录，绝不入库。"""

    def __init__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="ota-test-"))
        self.priv = Ed25519PrivateKey.generate()
        self.pub_pem = self.priv.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo)
        self.priv_pem = self.priv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption())
        self.releases = self.dir / "releases"
        self.releases.mkdir()
        self.calls = {"install": [], "rollback": 0, "fetch": []}
        self.health_ok = True
        self.install_rc = 0

    # ---- 造包 ----
    def make_tgz(self, name: str = "ttbox-update-1.2.0.tgz", body: bytes | None = None) -> Path:
        root = self.dir / ("src-" + name)
        if root.exists():
            shutil.rmtree(root)
        (root / "payload" / "bin").mkdir(parents=True)
        (root / "payload" / "bin" / "ttbox_core_main").write_bytes(body or b"TTBOX-BINARY-BODY")
        files = {"bin/ttbox_core_main": sha256f(str(root / "payload/bin/ttbox_core_main"))}
        (root / "RELEASE_MANIFEST.json").write_text(json.dumps(
            {"version": "1.2.0", "built_at": "2026-09-17T00:00:00Z",
             "git_sha": "", "files_sha256": files}), encoding="utf-8")
        tgz = self.dir / name
        with tarfile.open(tgz, "w:gz") as t:
            t.add(root / "payload", arcname="payload")
            t.add(root / "RELEASE_MANIFEST.json", arcname="RELEASE_MANIFEST.json")
        return tgz

    def sign_for(self, tgz: Path, key_id: str = "ttbox-ota-2026a",
                 version: str = "1.2.0", corrupt_sig: bool = False,
                 wrong_key_id: bool = False) -> Path:
        unsigned = sign.unsigned_record(tgz, version,
                                        "ttbox-ota-WRONG" if wrong_key_id else key_id)
        sig = self.priv.sign(sign.canonical(unsigned))
        if corrupt_sig:
            raw = bytearray(sig)
            raw[0] ^= 0xFF
            sig = bytes(raw)
        out = Path(str(tgz) + ".sign.json")
        out.write_text(json.dumps({**unsigned, "signature": base64.b64encode(sig).decode()},
                                  sort_keys=True, separators=(",", ":")), encoding="utf-8")
        return out

    # ---- 替身 ----
    def fetcher(self, mapping: dict):
        def _f(url: str, dest: str, timeout: float = 60.0):
            self.calls["fetch"].append(url)
            src = mapping.get(url)
            if src is None:
                raise FileNotFoundError(f"未映射的 URL: {url}")
            shutil.copyfile(src, dest)
        return _f

    def installer(self):
        def _i(staging: str, version: str) -> int:
            self.calls["install"].append((staging, version))
            return self.install_rc
        return _i

    def rollbacker(self):
        def _r() -> int:
            self.calls["rollback"] += 1
            return 0
        return _r

    def health(self):
        return lambda timeout_s=30: self.health_ok

    def updater(self, mapping: dict, key_id: str = "ttbox-ota-2026a"):
        return up.OtaUpdater(
            releases_dir=str(self.releases),
            status_path=str(self.dir / "ota_status.json"),
            fetch=self.fetcher(mapping), install=self.installer(),
            rollback=self.rollbacker(), health=self.health(),
            pubkey_pem=self.pub_pem)

    def status(self) -> dict:
        try:
            return json.loads(Path(self.dir / "ota_status.json").read_text())
        except Exception:
            return {}

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def run_case(fn):
    fx = Fixture()
    try:
        fn(fx)
    finally:
        fx.cleanup()


# ---------------------------------------------------------------- 用例
def case_good_pkg_verifies(fx):
    print("[1] good_pkg_verifies")
    tgz = fx.make_tgz()
    sgn = fx.sign_for(tgz)
    u = fx.updater({"https://x/pkg.tgz": str(tgz), "https://x/pkg.tgz.sign.json": str(sgn)})
    rc = u.run("https://x/pkg.tgz")
    st = fx.status()
    check("退出码 0", rc == 0, str(rc))
    check("state=SUCCESS", st.get("state") == "SUCCESS", json.dumps(st, ensure_ascii=False))
    check("调用了 T1.01 安装", len(fx.calls["install"]) == 1, str(fx.calls["install"]))
    check("未回滚", fx.calls["rollback"] == 0, "")


def case_tamper_1byte_rejected(fx):
    print("[2] tamper_1byte_rejected（多偏移，拒绝率须 100%）")
    tgz = fx.make_tgz()
    sgn = fx.sign_for(tgz)
    data = bytearray(tgz.read_bytes())
    offsets = [0, 1, 32, 64, max(0, len(data) // 2), max(0, len(data) - 1)]
    rejected = 0
    for off in offsets:
        tmp = fx.dir / f"tampered-{off}.tgz"
        b = bytearray(data)
        b[off] ^= 0xFF
        tmp.write_bytes(bytes(b))
        u = fx.updater({"https://x/pkg.tgz": str(tmp),
                        "https://x/pkg.tgz.sign.json": str(sgn)})
        rc = u.run("https://x/pkg.tgz")
        st = fx.status()
        ok = rc == 1 and st.get("state") == "FAILED" and \
            st.get("error") in ("sha256_mismatch", "signature_invalid")
        rejected += 1 if ok else 0
        print(f"    偏移 {off}: rc={rc} error={st.get('error')}")
    check("拒绝率 100%%", rejected == len(offsets), f"{rejected}/{len(offsets)}")
    check("篡改后无 staging 残留",
          not any(p.name.endswith(".staging") for p in fx.releases.iterdir()),
          str([p.name for p in fx.releases.iterdir()]))


def case_http_scheme_rejected(fx):
    print("[3] http_scheme_rejected")
    tgz = fx.make_tgz()
    sgn = fx.sign_for(tgz)
    u = fx.updater({"http://x/pkg.tgz": str(tgz), "http://x/pkg.tgz.sign.json": str(sgn)})
    rc = u.run("http://x/pkg.tgz")
    st = fx.status()
    check("退出码 1", rc == 1, str(rc))
    check("error=scheme_rejected", st.get("error") == "scheme_rejected", json.dumps(st))
    check("**未发生下载**", fx.calls["fetch"] == [], str(fx.calls["fetch"]))


def case_wrong_key_id_rejected(fx):
    print("[4] wrong_key_id_rejected")
    tgz = fx.make_tgz()
    sgn = fx.sign_for(tgz, wrong_key_id=True)
    u = fx.updater({"https://x/pkg.tgz": str(tgz), "https://x/pkg.tgz.sign.json": str(sgn)})
    rc = u.run("https://x/pkg.tgz")
    check("退出码 1", rc == 1, str(rc))
    check("key_id 不符即拒", fx.status().get("error") in ("signature_invalid", "sha256_mismatch"),
          json.dumps(fx.status()))


def case_dual_factor_both_required(fx):
    print("[5] dual_factor_both_required（and 非 or）")
    tgz = fx.make_tgz()
    # a) sha 对（包没动）但签名错 ⇒ 必须拒
    sgn_badsig = fx.sign_for(tgz, corrupt_sig=True)
    u = fx.updater({"https://x/pkg.tgz": str(tgz),
                    "https://x/pkg.tgz.sign.json": str(sgn_badsig)})
    rc_a = u.run("https://x/pkg.tgz")
    err_a = fx.status().get("error")
    check("签名错（sha 对）⇒ 拒", rc_a == 1 and err_a == "signature_invalid", str(err_a))
    # b) 签名对但内容被换（sha 不对）⇒ 必须拒
    other = fx.make_tgz(name="other.tgz", body=b"DIFFERENT-BODY")
    sgn_for_other = fx.sign_for(tgz)          # 签的是原包
    u2 = fx.updater({"https://x/pkg.tgz": str(other),
                     "https://x/pkg.tgz.sign.json": str(sgn_for_other)})
    rc_b = u2.run("https://x/pkg.tgz")
    err_b = fx.status().get("error")
    check("内容换（签对）⇒ 拒", rc_b == 1 and err_b == "sha256_mismatch", str(err_b))


def case_no_residue_on_failure(fx):
    print("[6] no_residue_on_failure")
    # 基线：只认"本次运行新增"的 ttbox-ota-* 残留（陈旧无关目录不误伤，2026-09-17 假红订正）
    baseline = {p.name for p in Path(tempfile.gettempdir()).iterdir()
                if p.name.startswith("ttbox-ota-")}
    tgz = fx.make_tgz()
    sgn = fx.sign_for(tgz)
    # 制造"验签通过但 manifest 不符"：改 payload 内容后重打包会破 sha256，
    # 故改为注入 install 失败来观察 staging 清理
    fx.install_rc = 1
    u = fx.updater({"https://x/pkg.tgz": str(tgz), "https://x/pkg.tgz.sign.json": str(sgn)})
    rc = u.run("https://x/pkg.tgz")
    check("install 失败 ⇒ rc=1", rc == 1, str(rc))
    check("error=install_failed", fx.status().get("error") == "install_failed", json.dumps(fx.status()))
    check("staging 已清理", not any(p.name.endswith(".staging") for p in fx.releases.iterdir()),
          str([p.name for p in fx.releases.iterdir()]))
    now = {p.name for p in Path(tempfile.gettempdir()).iterdir()
           if p.name.startswith("ttbox-ota-")}
    leftovers = sorted(now - baseline)
    check("临时目录零残留（相对基线）", not leftovers, str(leftovers))


def case_rollback_on_health_fail(fx):
    print("[7] rollback_on_health_fail")
    tgz = fx.make_tgz()
    sgn = fx.sign_for(tgz)
    fx.health_ok = False                      # 注入"业务不达标"
    u = fx.updater({"https://x/pkg.tgz": str(tgz), "https://x/pkg.tgz.sign.json": str(sgn)})
    rc = u.run("https://x/pkg.tgz")
    st = fx.status()
    check("健康检查失败 ⇒ rc=1", rc == 1, str(rc))
    check("error=health_check_failed", st.get("error") == "health_check_failed", json.dumps(st))
    check("**已调 rollback**", fx.calls["rollback"] == 1, str(fx.calls["rollback"]))
    check("staging 已清理", not any(p.name.endswith(".staging") for p in fx.releases.iterdir()), "")


def main() -> int:
    print("=== T1.11 OTA 验签单测 ===")
    for fn in (case_good_pkg_verifies, case_tamper_1byte_rejected,
               case_http_scheme_rejected, case_wrong_key_id_rejected,
               case_dual_factor_both_required, case_no_residue_on_failure,
               case_rollback_on_health_fail):
        run_case(fn)
        print()
    print("结果: %d failures" % len(FAILS))
    if FAILS:
        print("FAILED: " + "; ".join(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
