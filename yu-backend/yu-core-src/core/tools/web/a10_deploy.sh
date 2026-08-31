#!/bin/bash
# a10_deploy.sh — 板端部署 /opt/ttbox（镜像内容准备，可在运行系统上执行）
set -e
SRC=/tmp/a10_deploy
BUILD=/home/ubuntu/ttbox2/ttbox/core/build
OPT=/opt/ttbox

echo "== 1. 目录结构 =="
mkdir -p $OPT/runtime $OPT/models/registry/{installed,staging,cache,quarantine} \
         $OPT/hid $OPT/edid $OPT/web $OPT/config $OPT/scripts $OPT/tests $OPT/logs \
         $OPT/bin $OPT/lib $OPT/data

echo "== 2. C++ 二进制 =="
cp $BUILD/ttbox_core_main $BUILD/test_worker_hw $OPT/runtime/
cp $BUILD/ttbox-hid-health $BUILD/ttbox-hid-pkg $BUILD/ttbox-hid-test $BUILD/ipc_ping $OPT/runtime/ 2>/dev/null || true
for t in test_capture_hw test_rknn_hw test_decode_align test_hid_loopback test_hid_load_sim \
         test_model_adapter test_model_runtime test_model_switch_hw test_rga_hw test_rga_roi_hw; do
  [ -f $BUILD/$t ] && cp $BUILD/$t $OPT/tests/
done
chmod +x $OPT/runtime/* $OPT/tests/* 2>/dev/null || true

echo "== 3. HID Package（重置 registry 状态）=="
rm -rf $OPT/hid
cp -r /home/ubuntu/ttbox2/hid $OPT/hid
rm -rf $OPT/hid/registry $OPT/hid/packages $OPT/hid/staging $OPT/hid/quarantine \
       $OPT/hid/profiles $OPT/hid/runtime $OPT/hid/validation 2>/dev/null || true

echo "== 4. EDID =="
cp -r /opt/ttbox-edid/. $OPT/edid/
chmod +x $OPT/edid/inject_edid.sh 2>/dev/null || true

echo "== 5. 模型（预置黄瓦 320 INT8）=="
cp /home/ubuntu/ttbox2/models/huangwa.rknn $OPT/models/registry/installed/
echo "huangwa.rknn" > $OPT/models/active_model.txt

echo "== 6. Web =="
cp $SRC/ttbox_web.py $OPT/web/
cp /home/ubuntu/ttbox2/ttbox/core/tools/web/index.html $OPT/web/
cp -r /home/ubuntu/ttbox2/ttbox/core/tools/web/static $OPT/web/static
chmod -R a+rX $OPT/web 2>/dev/null || true

echo "== 7. 配置 =="
cp /home/ubuntu/ttbox2/config/default.json $OPT/config/
cp $SRC/infer.json $OPT/config/
python3 - "$OPT/config/default.json" <<'EOF'
import json, sys
p = sys.argv[1]
try:
    d = json.load(open(p))
except Exception:
    sys.exit(0)
d["model_registry_root"] = "/opt/ttbox/models/registry"
json.dump(d, open(p, "w"), indent=2, ensure_ascii=False)
EOF

echo "== 8. 脚本 =="
[ -f /home/ubuntu/ttbox2/scripts/a9_setup_hid_gadget.sh ] && cp /home/ubuntu/ttbox2/scripts/a9_setup_hid_gadget.sh $OPT/scripts/
[ -f /home/ubuntu/ttbox2/scripts/convert_onnx_to_rknn.py ] && cp /home/ubuntu/ttbox2/scripts/convert_onnx_to_rknn.py $OPT/scripts/
cp $SRC/setup_freq.sh $OPT/scripts/
cp $SRC/ttbox-perf.sh $OPT/scripts/
cp $SRC/ttbox-firstboot.sh $OPT/scripts/
cp $SRC/ttbox-infer.sh $OPT/runtime/
[ -f $SRC/ttbox-diagnostic.sh ] && cp $SRC/ttbox-diagnostic.sh $OPT/scripts/
[ -f $SRC/a10_coldboot_accept.sh ] && cp $SRC/a10_coldboot_accept.sh $OPT/scripts/
chmod +x $OPT/scripts/*.sh $OPT/scripts/*.py $OPT/runtime/*.sh 2>/dev/null || true

echo "== 9. systemd 服务 =="
cp $SRC/ttbox-firstboot.service $SRC/ttbox-runtime.service $SRC/ttbox-web.service \
   $SRC/ttbox-hid.service $SRC/ttbox-infer.service $SRC/ttbox-perf.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable ttbox-firstboot.service ttbox-runtime.service ttbox-web.service ttbox-hid.service ttbox-perf.service
systemctl disable ttbox-infer.service 2>/dev/null || true

echo "== 10. 部署结果 =="
ls -R $OPT | head -40
echo "DEPLOY_DONE"
