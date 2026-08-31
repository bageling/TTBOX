#!/bin/bash
# a10_fix_manifest.sh — 修正 manifest（RKNN 版本等）
P=/home/ubuntu/TTBox-v0.0.1-orangepi5plus-manifest.json
python3 - "$P" <<'EOF'
import json, sys
p = sys.argv[1]
m = json.load(open(p))
m["rknn_runtime_version"] = "2.3.2"
json.dump(m, open(p, "w"), indent=2, ensure_ascii=False)
print("rknn_runtime_version ->", m["rknn_runtime_version"])
print("image sha256 ->", m["sha256"])
EOF
