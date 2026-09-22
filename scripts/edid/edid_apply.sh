#!/usr/bin/env bash
# edid_apply.sh — TTBOX EDID 统一应用入口
# TTBOX 标准流程：生成 EDID → HPD 重协商 + 注入 HDMI-RX → 回读校验 → 保存固件副本。
# 默认自动切 HPD 让源端重新读取 EDID；TTBOX_EDID_REHANDSHAKE=0 可退回纯注入。
# 不修改 DRM/真实显示器输出。
# 用法：sudo bash /opt/ttbox/scripts/edid/edid_apply.sh [device]  默认 /dev/video0
set -euo pipefail

# 根前缀参数化（A-PATH-4）：板端默认 /opt/ttbox；联调可用 TTBOX_PREFIX 覆盖。
TTBOX_PREFIX="${TTBOX_PREFIX:-/opt/ttbox}"

# ---- 1.5.23：先修 DTB（出厂镜像里 HDMI-RX 是 disabled ⇒ /dev/video0 根本不存在）----
# 为什么挂在这里，而不是只放在 ttbox_release_install.sh：
#   OTA 更新器调的是 /opt/ttbox/current/scripts/ttbox_release_install.sh，而**那一刻
#   current 还指向旧版本** ⇒ 新包里对 install 脚本的改动本次**不会被执行**；
#   而 edid 服务是在 current 切换**之后**才被 ensure 拉起的，跑的是**新包**的脚本。
#   故把修复挂在 EDID 入口最前面（没有 /dev/video0 时 EDID 本来就必失败）。
# 脚本自带指纹门禁（只认已知的坏版本）+ 恒返回 0 ⇒ 不会打断 EDID 流程，也不会反复重启。
DTB_FIX="${TTBOX_PREFIX}/current/scripts/ttbox_dtb_fix.sh"
if [ -x "$DTB_FIX" ]; then
    "$DTB_FIX" || true
fi

CONFIG="${TTBOX_DISPLAY_CONFIG:-${TTBOX_PREFIX}/config/hardware_display.json}"
EDID_DIR="${TTBOX_PREFIX}/runtime/edid"
# B-CONST-4 / V-09：HPD 重协商重试次数默认**单一真源 = 12**（Web 不再覆写；运维可经
# TTBOX_EDID_REHANDSHAKE_ATTEMPTS 覆盖）。登记见 docs/protocols/config-path-env-registry.md §三。
ATTEMPTS_DEFAULT=12
EDID_OUTPUT="${EDID_OUTPUT:-$EDID_DIR/current.bin}"
VIDEO_DEV="${1:-/dev/video0}"
if [ "$VIDEO_DEV" != "/dev/video0" ]; then
  echo "错误的 HDMI-RX 设备 $VIDEO_DEV：EDID 注入必须使用 /dev/video0；/dev/dri/card0 仅用于 loopout" >&2
  exit 2
fi
export PY_ROOT="${PY_ROOT:-${TTBOX_PREFIX}/scripts}"
# 默认按标准流程重协商；明确指定 0 才退回纯注入。
REHANDSHAKE="${TTBOX_EDID_REHANDSHAKE:-1}"
HPD_STATUS=""
if [ "$REHANDSHAKE" = "1" ]; then
  if [ -w /sys/class/hdmirx/hdmirx/status ]; then
    HPD_STATUS="/sys/class/hdmirx/hdmirx/status"
  elif [ -w /sys/devices/platform/fdee0000.hdmirx-controller/hdmirx/hdmirx/status ]; then
    HPD_STATUS="/sys/devices/platform/fdee0000.hdmirx-controller/hdmirx/hdmirx/status"
  else
    echo '{"ok": false, "error": "已请求重协商，但未找到可写 HDMI-RX HPD 节点"}'
    exit 1
  fi
fi
if [ ! -f "$CONFIG" ]; then
  echo '{"ok": false, "error": "hardware_display.json 不存在"}'
  exit 1
fi

mkdir -p "$EDID_DIR" || { echo '{"ok": false, "error": "无法创建 EDID 输出目录: '"$EDID_DIR"'"}'; exit 1; }

# V-EDID-3（板端实测 2026-09-19）＋T1.08（2026-09-21 二次修正）：web「保存并应用」
# 以 ttbox 身份跑本脚本，需要：
#   a) 写 $EDID_DIR/current.bin —— 开机 ttbox-edid.service 以 root 重写后属主归
#      root:root，ttbox 写不进（PermissionError 实录）；
#   b) 写 HPD 节点触发重协商 —— sysfs 节点内核默认 root:root 0644。
# 收敛目标（全部幂等）：目录 root:ttbox 0775、current.bin root:ttbox 0664、
# HPD/status 与 edid 节点 root:ttbox 0660。
#
# ★ 为什么 09-19 那版没修好、以及本次怎么修（2026-09-21 实机定位）：
#   旧版把这个收敛块放在 `if [ "$(id -u)" = "0" ]` 里，且位置在**生成 current.bin 之前**。
#   而 current.bin 由下方 python 块创建 ⇒ **首次运行时文件尚不存在**，收敛里的
#   `if [ -f "$EDID_OUTPUT" ]` 判空跳过；随后 python 以 root 建出 0644 root:root。
#   此后若没有 root 再跑一次，文件就永久是 root:root 0644 ⇒ web（ttbox）写不进。
#   板上实证：`-rw-r--r-- 1 root root current.bin` ＋ ttbox 写入 Permission denied。
#   故本次三点修正：
#     ① 收敛抽成函数并**无条件调用**（非 root 每条 chgrp/chmod 都失败，被 || true 吞掉，
#        不报错、不改语义；root 时抢先修好目录与 HPD 节点，供非 root 路径写 HPD）；
#     ② python 侧改为「同目录临时文件 + 原子替换」——目录是 root:ttbox 0775，ttbox 有
#        目录写权限 ⇒ 能新建临时文件、能用 rename 覆盖 root 建出的文件（rename 不需要
#        目标文件本身的写权限）。这样即便开机收敛从未生效，非 root 路径也能自愈；
#     ③ 生成完成后**再收敛一次**，保证 root 路径下新文件的属主/权限立刻正确。
converge_perms()
{
  chgrp ttbox "$EDID_DIR" 2>/dev/null || true
  chmod 0775 "$EDID_DIR" 2>/dev/null || true
  if [ -f "$EDID_OUTPUT" ]; then
    chgrp ttbox "$EDID_OUTPUT" 2>/dev/null || true
    chmod 0664 "$EDID_OUTPUT" 2>/dev/null || true
  fi
  for _hx in /sys/class/hdmirx/hdmirx \
             /sys/devices/platform/fdee0000.hdmirx-controller/hdmirx/hdmirx; do
    if [ -e "$_hx/status" ] || [ -e "$_hx/edid" ]; then
      for _n in status edid; do
        if [ -e "$_hx/$_n" ]; then
          chown root:ttbox "$_hx/$_n" 2>/dev/null || true
          chmod 0660 "$_hx/$_n" 2>/dev/null || true
        fi
      done
      break
    fi
  done
  # 恒返回 0：本函数只在 root 下才可能真正生效，非 root 下全部失败属预期，
  # 绝不能让 set -e 因"权限收敛失败"把整条 EDID 流程判死。
  return 0
}
converge_perms

# 1. 生成 + 校验 EDID
python3 - "$CONFIG" "$EDID_OUTPUT" <<'PYEOF' || exit 1
import json, os, struct, sys
sys.path.insert(0, os.environ.get("PY_ROOT", "/opt/ttbox/scripts"))
from edid.builder import build_from_config, _pnp_decode
from edid.validator import verify_edid

cfg_path, out_path = sys.argv[1], sys.argv[2]
with open(cfg_path) as f:
    cfg = json.load(f)
# native_mode 保护：空/非法时用 profile 首选或安全兜底（防退化成 1080p60）
try:
    from edid.timing_db import mode_info
    from edid.mode_builder import PROFILES_SET
except Exception:
    mode_info = None
    PROFILES_SET = set()
if mode_info is not None:
    nm = str(cfg.get("native_mode") or "").strip()
    if nm in ("", "auto") or mode_info(nm) is None:
        profile = str(cfg.get("profile") or "boot-safe-full")
        # profile 首选 token 集合（对齐 hdmirx_edid.PROFILES）
        PROFILE_FIRST = {
            "boot-safe-1080p240": "1080p240compat",
            "boot-safe-full": "1080p60compat",
            "standard-dual": "1080p120",
            "single-1440p60": "1440p60",
            "single-1080p120": "1080p120",
            "single-1080p144": "1080p144",
            "single-1080p240": "1080p240",
            "single-1440p144": "1440p144",
            "single-2160p60": "2160p60",
        }
        fallback = PROFILE_FIRST.get(profile) or "1080p60compat"
        cfg["native_mode"] = fallback
        print(json.dumps({"warn": f"native_mode 空/非法，用 profile 首选: {fallback}"}))
try:
    edid = build_from_config(cfg)
except Exception as e:
    print(json.dumps({"ok": False, "error": f"EDID 生成失败: {e}"}))
    sys.exit(1)
ok, errors = verify_edid(edid)
if not ok:
    print(json.dumps({"ok": False, "error": "EDID 验证失败", "errors": errors}))
    sys.exit(1)
# T1.08（2026-09-21）：写 current.bin 必须兼顾 root 与 ttbox 两种身份。
#   直接 open(out_path,"wb") 在文件为 root:root 0644 时，ttbox 必然 PermissionError
#   （面板「保存并应用」实测报错）。改为「同目录临时文件 + os.replace」：
#   目录是 root:ttbox 0775，ttbox 有目录写权限 ⇒ 能建临时文件并用 rename 覆盖目标，
#   rename 只需目录权限、不需目标文件写权限，故对两种身份都成立，且写入是原子的
#   （不会出现半截 EDID——半截 EDID 会让驱动 EDID 状态损坏、源端 fallback 800x600）。
#   极端兜底：目录也不可写（非标准镜像）时退回直写，并给出干净 JSON 错误而非裸 traceback。
_tmp = "%s.tmp.%d" % (out_path, os.getpid())
try:
    with open(_tmp, "wb") as f:
        f.write(edid)
    os.replace(_tmp, out_path)
except OSError:
    try:
        os.unlink(_tmp)
    except OSError:
        pass
    try:
        with open(out_path, "wb") as f:
            f.write(edid)
    except OSError as _e2:
        print(json.dumps({"ok": False, "error": "EDID 写入失败: %s" % _e2}))
        sys.exit(1)
vendor = _pnp_decode(edid[8:10])
pid = struct.unpack("<H", edid[10:12])[0]
ser = struct.unpack("<I", edid[12:16])[0]
name = edid[77:90].rstrip(b"\x0a\x20").decode("ascii", "replace").strip()
print(json.dumps({"ok": True, "file": out_path, "size": len(edid),
                  "vendor": vendor, "product_id": f"0x{pid:04x}",
                  "serial": f"0x{ser:08x}", "name": name}))
PYEOF

# T1.08：生成完成后再次收敛——root 路径下把刚建出的 current.bin 立即修正为
# root:ttbox 0664（否则下次仍以 root 身份覆盖时正确、但 ttbox 路径依旧写不进）。
converge_perms

set_hpd() {
  local state="$1"
  [ -n "$HPD_STATUS" ] || return 0
  printf '%s\n' "$state" > "$HPD_STATUS" 2>/dev/null
}

apply_and_verify() {
  v4l2-ctl -d "$VIDEO_DEV" --set-edid=pad=0,file="$EDID_OUTPUT",format=raw || return 1
  # 根因修复：全字节验证（此前只验证 name 字段——注入损坏/半截时仍误判成功，
  # 导致驱动 EDID 状态损坏 → PC 源 fallback 800x600）
  local raw_file ok
  raw_file="$(mktemp)"
  if v4l2-ctl -d "$VIDEO_DEV" --get-edid=pad=0,format=raw > "$raw_file" 2>/dev/null; then
    ok=$(python3 -c "
import sys
data=open('$raw_file','rb').read()
cur=open('$EDID_OUTPUT','rb').read()
# 驱动必须返回完整 EDID，且长度和内容都一致；半截回读不能算成功。
sys.exit(0 if len(data) == len(cur) and data == cur else 1)
" 2>/dev/null && echo yes || echo no)
    rm -f "$raw_file"
    [ "$ok" = "yes" ] || return 1
    return 0
  fi
  rm -f "$raw_file"
  return 1
}

wait_for_lock() {
  local timeout="${TTBOX_EDID_LOCK_TIMEOUT_SEC:-14}"
  local deadline=$(( $(date +%s) + timeout ))
  local status timing
  while [ "$(date +%s)" -lt "$deadline" ]; do
    status="$(cat /sys/kernel/debug/hdmirx/status 2>/dev/null || true)"
    if [ -n "$status" ]; then
      if ! printf '%s\n' "$status" | grep -qE 'Clk-Ch:Lock[[:space:]]+Ch0:Lock[[:space:]]+Ch1:Lock[[:space:]]+Ch2:Lock'; then
        # debugfs 可读但未锁：继续等（root 路径，语义不变）
        sleep 1
        continue
      fi
    fi
    # V-EDID-4（板端实测 2026-09-19）：debugfs 仅 root 可读——ttbox（web 路径）读不到
    # status（空串）时若也走上面的「未锁则等」，锁永远判不上 ⇒ 重试打满 12 轮 ⇒
    # 必然超 web subprocess 60s ⇒ Flask 500（用户实测「保存后没有重新枚举」）。
    # 降级：status 读不到时仅用 v4l2 --query-dv-timing 判锁（走 /dev/video0，video 组即可）。
    timing="$(mktemp)"
    if v4l2-ctl -d "$VIDEO_DEV" --query-dv-timing >"$timing" 2>&1 && ! grep -qE 'failed|No locks' "$timing"; then
      rm -f "$timing"
      return 0
    fi
    rm -f "$timing"
    sleep 1
  done
  return 1
}

EXPECT_NAME=$(python3 -c "
import json
cfg=json.load(open('$CONFIG'))
print(cfg.get('name','TTBOX')[:13])
" 2>/dev/null || echo "TTBOX")

if [ "$REHANDSHAKE" = "1" ]; then
  # RK3588 实际流程：HPD 断开后源端不一定一次就完成重新枚举。
  # 采用有限重试，每轮都重新拉低/拉高 HPD，直到 EDID 回读且 RX 锁定。
  trap 'set_hpd on 2>/dev/null || true' EXIT
fi

if [ "$REHANDSHAKE" = "1" ]; then
  APPLIED=0
  LOCKED=0
  ATTEMPTS="${TTBOX_EDID_REHANDSHAKE_ATTEMPTS:-$ATTEMPTS_DEFAULT}"
  case "$ATTEMPTS" in ''|*[!0-9]*) ATTEMPTS=$ATTEMPTS_DEFAULT ;; esac
  [ "$ATTEMPTS" -gt 0 ] || ATTEMPTS=1
  attempt=1
  while [ "$attempt" -le "$ATTEMPTS" ]; do
    set_hpd off
    sleep 0.2
    if apply_and_verify; then
      APPLIED=1
      set_hpd on
      sleep "${TTBOX_EDID_HPD_SETTLE_SEC:-0.5}"
      if wait_for_lock; then
        LOCKED=1
        trap - EXIT
        break
      fi
    fi
    attempt=$((attempt + 1))
    sleep 0.5
  done
  if [ "$LOCKED" != "1" ]; then
    if [ "$APPLIED" = "1" ]; then
      echo '{"ok": false, "error": "EDID 已写入且回读一致，但 HDMI-RX 多轮重新枚举后仍未锁定输入", "edid_applied": true, "locked": false}'
    else
      echo '{"ok": false, "error": "EDID 注入或回读校验失败，重新枚举未完成", "edid_applied": false, "locked": false}'
    fi
    exit 1
  fi
else
  if ! apply_and_verify; then
    echo '{"ok": false, "error": "EDID 注入或回读校验失败，未执行重试/HPD切换"}'
    exit 1
  fi
fi

if [ "$REHANDSHAKE" = "1" ] && [ "$LOCKED" = "1" ]; then
  # 持久化 firmware（TTBOX 独立路径，供下一次启动恢复）
  FIRMWARE_DIR="/lib/firmware/ttbox"
  mkdir -p "$FIRMWARE_DIR" 2>/dev/null || true
  if ! cp "$EDID_OUTPUT" "$FIRMWARE_DIR/hdmirx_edid.bin" 2>/dev/null || ! chmod 644 "$FIRMWARE_DIR/hdmirx_edid.bin" 2>/dev/null; then
    # V-EDID-5（板端实测 2026-09-19）：web（User=ttbox）路径写不了 /lib/firmware ——
    # 持久化本就是开机 ttbox-edid.service（root）的职责（每次开机重写）。非 root
    # 时降级为警告，不得把已成功的「注入+HPD 重握手」整体判死（面板保存会永远失败）。
    if [ "$(id -u)" = "0" ]; then
      echo '{"ok": false, "error": "EDID 已写入驱动，但固件副本保存失败"}'
      exit 1
    fi
    echo '{"warn": "固件副本保存失败（非 root；开机 edid 服务会代为持久化）"}'
  fi
  echo "Persisted firmware EDID: $FIRMWARE_DIR/hdmirx_edid.bin"
  # T1.04（DEP-02）：此处只是"已成功应用并锁定"之后的诊断性回读计数。
  # 加 `|| true` 防止 set -o pipefail 下它把已成功的结局误报成致命错误抛给 caller
  # （systemd oneshot / Web 两处 caller 都不该因这一行偶发失败而看到失败）。
  CUR=$(v4l2-ctl -d "$VIDEO_DEV" --get-edid=pad=0,format=raw 2>/dev/null | wc -c) || true
  echo "{\"ok\": true, \"hpd\": \"$([ \"$REHANDSHAKE\" = \"1\" ] && echo rehandshake || echo unchanged)\", \"version\": \"$CUR\", \"method\": \"v4l2_ctl\", \"file\": \"$EDID_OUTPUT\", \"mode\": \"$EXPECT_NAME\"}"
  exit 0
fi

# 非重协商模式注入成功后同样持久化，保持原有行为。
FIRMWARE_DIR="/lib/firmware/ttbox"
mkdir -p "$FIRMWARE_DIR" 2>/dev/null || true
if ! cp "$EDID_OUTPUT" "$FIRMWARE_DIR/hdmirx_edid.bin" 2>/dev/null || ! chmod 644 "$FIRMWARE_DIR/hdmirx_edid.bin" 2>/dev/null; then
  # V-EDID-5 同上：非 root（web 路径）降级为警告，root 保持严格。
  if [ "$(id -u)" = "0" ]; then
    echo '{"ok": false, "error": "EDID 已写入驱动，但固件副本保存失败"}'
    exit 1
  fi
  echo '{"warn": "固件副本保存失败（非 root；开机 edid 服务会代为持久化）"}'
fi
echo "Persisted firmware EDID: $FIRMWARE_DIR/hdmirx_edid.bin"
# T1.04（DEP-02）：同上一处——诊断性回读失败不得把已成功的结局误报成致命错误。
CUR=$(v4l2-ctl -d "$VIDEO_DEV" --get-edid=pad=0,format=raw 2>/dev/null | wc -c) || true
echo "{\"ok\": true, \"hpd\": \"unchanged\", \"version\": \"$CUR\", \"method\": \"v4l2_ctl\", \"file\": \"$EDID_OUTPUT\", \"mode\": \"$EXPECT_NAME\"}"
exit 0
