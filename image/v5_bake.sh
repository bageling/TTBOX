#!/usr/bin/env bash
# V5 出厂镜像烘焙驱动（宿主侧 · WSL）
#
# ★ 为什么必须「一次 WSL 会话跑完」：WSL2 空闲约 60s 会关掉 VM，loop 与挂载随之
#   消失。分多次 wsl.exe 调用做「挂载 → 改 → 卸」必然中途丢挂载（本项目实测多次，
#   表现是镜像内文件忽有忽无、chroot 报 no such file）。
#
# 前置（项目外，不入 git/镜像/文档）：
#   /mnt/c/Users/Administrator/ttbox-image-keys/{ssh_host_ed25519_key,ssh_host_rsa_key,*.pub,root-password.txt}
#
# 用法：
#   wsl.exe -d Ubuntu-22.04 -- bash "/mnt/g/WORKBUDDY工作区/TTBOX-最终源码-2026-09-18/image/v5_bake.sh" 2>&1 | tee /root/ttbox-image/v5-bake.log
set -uo pipefail

REPO="/mnt/g/WORKBUDDY工作区/TTBOX-最终源码-2026-09-18"
IMG="/mnt/img"
KEYS="/mnt/c/Users/Administrator/ttbox-image-keys"
WORK="/root/ttbox-image/work.img"
DST="/mnt/c/Users/Administrator/Downloads/ubuntu-22.04-preinstalled-server-arm64-orangepi-5-plus-V5.img"
PASS="$(cat "$KEYS/root-password.txt")"
export TTBOX_HOSTKEY_DIR="$KEYS"

STEP_RC_04=999; STEP_RC_05=999; FIN_RC=999; FSCK_RC=999
die() { echo; echo "!! $*"; echo "!! 停在当前阶段，未回写；$WORK 与挂载现场保留，可直接复跑（04 幂等）。"; exit 1; }

echo "################ V5 烘焙开始 @ $(date -Is) ################"

# ---------------------------------------------------------------- P0
echo
echo "===== P0 干净挂载 + 身份证明 ====="
mountpoint -q "$IMG" && umount -l "$IMG" 2>/dev/null
for l in $(losetup -j "$WORK" --noheadings -O NAME 2>/dev/null); do losetup -d "$l" 2>/dev/null; done
sleep 1
bash "$REPO/image/20_mount.sh" || die "20_mount.sh 失败"
mountpoint -q "$IMG" || die "/mnt/img 未挂载"
[ -f "$IMG/etc/os-release" ]              || die "镜像 /etc/os-release 不存在"
# ★ 只验「有浇筑过的发布树」，不钉具体版本号（升版后旧硬编码会误判挂错盘）
[ -d "$IMG/opt/ttbox/releases" ]          || die "镜像里没有 releases/ —— 挂错盘了"
[ -L "$IMG/opt/ttbox/current" ]           || die "镜像里 current 不是软链 —— 还没浇筑过"
# 期望版本（可选）：由调用方经 TTBOX_VER / TTBOX_EXPECT_VER 传入，传给 90 门禁钉版本
export TTBOX_EXPECT_VER="${TTBOX_EXPECT_VER:-${TTBOX_VER:-}}"
echo "  身份: $(head -1 "$IMG/etc/os-release")"
echo "  current -> $(readlink "$IMG/opt/ttbox/current")"
echo "  改前 root 口令字段: $(awk -F: '$1=="root"{print substr($2,1,24)}' "$IMG/etc/shadow")"
echo "  改前 sshd_config.d: $(ls "$IMG/etc/ssh/sshd_config.d/")"
echo "  改前 ssh.service  : $(readlink "$IMG/etc/systemd/system/ssh.service" 2>/dev/null || echo '(无)')"

# ---------------------------------------------------------------- P1
echo
echo "===== P1 staging（steps/deploy/scripts/payload → ext4）====="
bash "$REPO/image/prepare_stage.sh" || die "prepare_stage.sh 失败"
STAGE="/root/ttbox-image/_stage"
[ -d "$STAGE/steps" ] || die "staging 缺 steps/"
rm -rf "$IMG/root/_bake"
mkdir -p "$IMG/root/_bake"
cp -a "$STAGE/." "$IMG/root/_bake/"
find "$IMG/root/_bake/steps"   -type f -name '*.sh' -exec chmod 0755 {} + 2>/dev/null
find "$IMG/root/_bake/scripts" -type f -name '*.sh' -exec chmod 0755 {} + 2>/dev/null
# 注入固定 host key（随 /root/_bake 由 90 一并清除）
mkdir -p "$IMG/root/_bake/hostkeys"
install -m 0600 "$KEYS/ssh_host_ed25519_key"     "$IMG/root/_bake/hostkeys/"
install -m 0600 "$KEYS/ssh_host_rsa_key"         "$IMG/root/_bake/hostkeys/"
install -m 0644 "$KEYS/ssh_host_ed25519_key.pub" "$IMG/root/_bake/hostkeys/"
install -m 0644 "$KEYS/ssh_host_rsa_key.pub"     "$IMG/root/_bake/hostkeys/"
ls -l "$IMG/root/_bake/hostkeys" | sed 's/^/  /'

# ---------------------------------------------------------------- P2
echo
echo "===== P2 应用 04_board_config.sh（SSH 启用 / 固定 host key / 强口令）====="
TTBOX_ROOT_PASS="$PASS" TTBOX_HOSTKEY_DIR=/root/_bake/hostkeys \
    chroot "$IMG" /bin/bash /root/_bake/steps/04_board_config.sh
STEP_RC_04=$?
echo "  >>> rc(04) = $STEP_RC_04"

# ---------------------------------------------------------------- P3
echo
echo "===== P3 全量自检 05_selfcheck.sh（含 SSH 专项 S1–S9）====="
TTBOX_HOSTKEY_DIR=/root/_bake/hostkeys \
    chroot "$IMG" /bin/bash /root/_bake/steps/05_selfcheck.sh 2>&1 | tee /root/selfcheck.txt
STEP_RC_05=${PIPESTATUS[0]}
echo "  >>> rc(05) = $STEP_RC_05"

# ---------------------------------------------------------------- P3b
echo
echo "===== P3b 独立核验（口令可复算 / host key 指纹）====="
H="$(awk -F: '$1=="root"{print $2}' "$IMG/etc/shadow")"
SALT="$(printf '%s' "$H" | cut -d'$' -f3)"
if [ -n "$SALT" ]; then
    CALC="$(openssl passwd -6 -salt "$SALT" "$PASS")"
    if [ "$CALC" = "$H" ]; then echo "  [✓] 口令复算一致：镜像内 root 哈希 == openssl 用同一 salt 复算结果"
    else echo "  [✗] 口令复算不一致（镜像内哈希与我手上的口令对不上）"; fi
else
    echo "  [✗] 取不到盐（root 哈希为 ${H:-空}）"
fi
echo "  镜像 host key sha256(ed25519) = $(sha256sum "$IMG/etc/ssh/ssh_host_ed25519_key" | cut -d' ' -f1)"
echo "  注入源 sha256(ed25519)        = $(sha256sum "$KEYS/ssh_host_ed25519_key" | cut -d' ' -f1)"
echo "  存档指纹                      = $(cat "$KEYS/hostkey-fingerprint.txt")"
echo "  shadow 里不再有明文口令残留？(应只见 \$6\$ 哈希) $(awk -F: '$1=="root"{print substr($2,1,3)}' "$IMG/etc/shadow")"

# ---------------------------------------------------------------- 门禁
if [ "$STEP_RC_04" != "0" ] || [ "$STEP_RC_05" != "0" ]; then
    die "04/05 未全绿（rc04=$STEP_RC_04 rc05=$STEP_RC_05）—— 按门禁不回写"
fi

# ---------------------------------------------------------------- P4
echo
echo "===== P4 宿主侧收尾门禁 90_finalize_host.sh（清 _bake + 断言 + 卸挂载）====="
bash "$REPO/image/90_finalize_host.sh"
FIN_RC=$?
echo "  >>> rc(90) = $FIN_RC"
[ "$FIN_RC" = "0" ] || die "90_finalize_host 门禁未过"

# ---------------------------------------------------------------- P5
echo
echo "===== P5 成品文件系统校验 99_fsck.sh --repair ====="
bash "$REPO/image/99_fsck.sh" --repair
FSCK_RC=$?
echo "  >>> rc(99) = $FSCK_RC"
[ "$FSCK_RC" = "0" ] || die "fsck 未通过"

# ---------------------------------------------------------------- P6
echo
echo "===== P6 回写成品 $DST ====="
df -h "$(dirname "$DST")" | tail -1 | sed 's/^/  空间: /'
TMP="${DST}.new"
rm -f "$TMP"
dd if="$WORK" of="$TMP" bs=8M conv=fsync status=progress || die "dd 失败"
WANT="$(stat -c %s "$WORK")"; GOT="$(stat -c %s "$TMP")"
[ "$WANT" = "$GOT" ] || die "字节数不符 want=$WANT got=$GOT"
SW="$(sha256sum "$WORK" | cut -d' ' -f1)"; ST="$(sha256sum "$TMP" | cut -d' ' -f1)"
[ "$SW" = "$ST" ] || die "回写 sha256 不符"
mv -f "$TMP" "$DST"
SF="$(sha256sum "$DST" | cut -d' ' -f1)"
[ "$SF" = "$SW" ] || die "落盘后 sha256 变了"
printf '%s  %s\n' "$SF" "$(basename "$DST")" > "$DST.sha256"
echo "  [✓] 成品就位: $DST"
echo "  [✓] V5 sha256 = $SF"
echo "  [✓] 指纹文件  : $DST.sha256"

# ---------------------------------------------------------------- P7
echo
echo "################ V5 烘焙完成 @ $(date -Is) ################"
echo "  rc(04)=$STEP_RC_04  rc(05)=$STEP_RC_05  rc(90)=$FIN_RC  rc(99)=$FSCK_RC"
echo "  自检日志: /root/selfcheck.txt（并已由 90 归档到 image/artifacts/）"
exit 0
