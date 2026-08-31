#!/bin/bash
# a10_web_test.sh — Web API 全链路测试（板端执行）
set -e
B=http://127.0.0.1:8080
echo "== 1. 推理启动 =="
curl -s -X POST $B/api/inference -H 'Content-Type: application/json' -d '{"action":"start"}'
sleep 8
echo ""
echo "== 2. 状态（含 FPS）=="
curl -s $B/api/state | python3 -c 'import json,sys; d=json.load(sys.stdin); print("inference:", d["inference"]); print("services:", d["services"])'
echo "== 3. 推理停止 =="
curl -s -X POST $B/api/inference -H 'Content-Type: application/json' -d '{"action":"stop"}'
echo ""
echo "== 4. 模型上传测试（副本 → staging）=="
cp /opt/ttbox/models/registry/installed/huangwa.rknn /tmp/test_upload.rknn
curl -s -X POST $B/api/models/upload -F "file=@/tmp/test_upload.rknn"
echo ""
echo "== 5. 模型激活 =="
curl -s -X POST $B/api/models/activate -H 'Content-Type: application/json' -d '{"name":"test_upload.rknn"}'
echo ""
echo "== 6. 模型列表 =="
curl -s $B/api/models | python3 -c 'import json,sys; [print(m["name"], m["status"], "active" if m["active"] else "") for m in json.load(sys.stdin)["models"]]'
echo "== 7. 删除 active 应拒绝 =="
curl -s -X POST $B/api/models/remove -H 'Content-Type: application/json' -d '{"name":"test_upload.rknn"}'
echo ""
echo "== 8. 重新激活原模型 + 删除测试模型 =="
curl -s -X POST $B/api/models/activate -H 'Content-Type: application/json' -d '{"name":"huangwa.rknn"}'
echo ""
curl -s -X POST $B/api/models/remove -H 'Content-Type: application/json' -d '{"name":"test_upload.rknn"}'
echo ""
echo "== 9. HID 健康 =="
curl -s $B/api/hid | python3 -c 'import json,sys; d=json.load(sys.stdin); print("rc:", d["rc"]); print([l for l in d["output"].splitlines() if "FAIL" in l or "PASS /" in l][-2:])'
echo "== 10. EDID reload =="
curl -s -X POST $B/api/edid -H 'Content-Type: application/json' -d '{"action":"reload"}'
echo ""
echo "== 11. 最终状态 =="
curl -s $B/api/state | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["inference"]); print(d["services"])'
echo "WEB_TEST_DONE"
