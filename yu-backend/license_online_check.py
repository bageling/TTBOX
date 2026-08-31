#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def server_url(root: Path) -> str:
    saved = (root / "license" / "server_url.txt")
    if saved.exists():
        value = saved.read_text(encoding="utf-8").strip()
        if value:
            return value.rstrip("/")
    return ""


def post_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "aiAssistance-daemon-license/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(2 * 1024 * 1024)
    decoded = json.loads(raw.decode("utf-8"))
    return decoded if isinstance(decoded, dict) else {}


def delete_core(root: Path, reason: str, revoked_payload: dict[str, Any] | None = None) -> None:
    license_dir = root / "license"
    revoked_doc = {
        "revoked": True,
        "reason": reason,
        "license_id": str((revoked_payload or {}).get("license_id", "")),
        "device_id": str((revoked_payload or {}).get("device_id", "")),
        "device_fingerprint_hash": str((revoked_payload or {}).get("device_fingerprint_hash", "")),
        "revoked_at": int(time.time()),
        "source": "license_online_check",
    }
    lock_doc = {
        "locked": True,
        "reason": reason,
        "license_id": str((revoked_payload or {}).get("license_id", "")),
        "device_id": str((revoked_payload or {}).get("device_id", "")),
        "device_fingerprint_hash": str((revoked_payload or {}).get("device_fingerprint_hash", "")),
        "locked_at": int(time.time()),
        "source": "license_online_check",
    }
    write_json(license_dir / "revoked.json", revoked_doc)
    write_json(license_dir / "trial_lock.json", lock_doc)
    for path in (
        root / "license" / "core_activation.json",
        root / "license" / "online_grant.json",
        root / "core" / "libai_core.so.enc",
    ):
        path.unlink(missing_ok=True)
    usb_proxy_root = Path(os.environ.get("AIASSISTANCE_USB_PROXY_ROOT", "/opt/usb-proxy"))
    for path in (
        usb_proxy_root / "license" / "usb_proxy_activation.json",
        usb_proxy_root / "bin" / "usb-proxy.enc",
    ):
        path.unlink(missing_ok=True)
    run_dir = root / "run"
    if run_dir.exists():
        for temp_core in run_dir.glob("libai_core_*.so"):
            temp_core.unlink(missing_ok=True)
    shutil.rmtree("/run/usb-proxy", ignore_errors=True)
    shutil.rmtree(root / "run" / "trial-lock.tmp", ignore_errors=True)
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


NON_DESTRUCTIVE_LICENSE_REASONS = {
    "valid license is required",
    "license is not active on this server",
    "license key is already bound to another device",
    "current hardware evidence does not match previous device",
    "current device does not contain previous license fingerprint evidence",
}


def is_destructive_revocation(reason: str, payload: dict[str, Any] | None = None) -> bool:
    normalized = str(reason or (payload or {}).get("reason") or "").strip().lower()
    if normalized in NON_DESTRUCTIVE_LICENSE_REASONS:
        return False
    return bool(payload and payload.get("revoked"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Check aiAssistance license online state")
    parser.add_argument("--root", default="/opt/aiassistance")
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--delete-on-revoked", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    license_path = root / "license" / "license.json"
    device_path = root / "license" / "device.json"
    grant_path = root / "license" / "online_grant.json"
    license_doc = load_json(license_path)
    device_doc = load_json(device_path)
    plan = str(license_doc.get("plan") or "")
    if plan not in {"trial", "permanent"} or not str(license_doc.get("license_id") or ""):
        print(json.dumps({"ok": True, "data": {"plan": plan or "missing"}}))
        return 0
    base_url = server_url(root)
    if not base_url:
        print(json.dumps({"ok": False, "error": "license server URL is missing"}))
        return 2
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", "v1/license-check")
    payload = {
        "license": license_doc,
        "device": device_doc,
        "device_id": device_doc.get("device_id", ""),
        "device_fingerprint_hash": device_doc.get("fingerprint_hash") or license_doc.get("device_fingerprint_hash", ""),
    }
    try:
        response = post_json(url, payload, max(1, args.timeout))
    except urllib.error.HTTPError as exc:
        raw = exc.read(64 * 1024).decode("utf-8", "replace")
        try:
            response = json.loads(raw)
        except json.JSONDecodeError:
            print(json.dumps({"ok": False, "error": f"server returned HTTP {exc.code}: {raw}"}))
            return 2
        data = response.get("data") if isinstance(response, dict) else {}
        if isinstance(data, dict) and data.get("revoked"):
            reason = str(response.get("error") or data.get("reason") or "license revoked")
            destructive = is_destructive_revocation(reason, data)
            if args.delete_on_revoked and destructive:
                delete_core(root, reason, data)
            print(json.dumps({"ok": False, "revoked": destructive, "error": reason, "data": data}, ensure_ascii=False))
            return 3 if destructive else 2
        print(json.dumps({"ok": False, "error": str(response.get("error") or f"server returned HTTP {exc.code}")}, ensure_ascii=False))
        return 2
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"failed to connect license server: {exc}"}, ensure_ascii=False))
        return 2

    if response.get("ok") is False:
        data = response.get("data")
        if isinstance(data, dict) and data.get("revoked"):
            reason = str(response.get("error") or data.get("reason") or "license revoked")
            destructive = is_destructive_revocation(reason, data)
            if args.delete_on_revoked and destructive:
                delete_core(root, reason, data)
            print(json.dumps({"ok": False, "revoked": destructive, "error": reason, "data": data}, ensure_ascii=False))
            return 3 if destructive else 2
        print(json.dumps({"ok": False, "error": str(response.get("error") or "server rejected request")}, ensure_ascii=False))
        return 2

    data = response.get("data", response)
    if not isinstance(data, dict):
        print(json.dumps({"ok": False, "error": "license server response data must be an object"}))
        return 2
    grant = data.get("online_grant")
    if plan == "trial":
        if not isinstance(grant, dict):
            print(json.dumps({"ok": False, "error": "license server response missing online grant"}))
            return 2
        write_json(grant_path, grant)
    elif isinstance(grant, dict):
        write_json(grant_path, grant)
    updated_license = data.get("license")
    if isinstance(updated_license, dict):
        write_json(license_path, updated_license)
    print(json.dumps({"ok": True, "data": data}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
