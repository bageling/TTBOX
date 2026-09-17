"""本地预览启动器：用 Flask 内置 dev server 渲染完整 index.html（含 glass-modern.css）。
本机预览专用，不改动 ttbox-web.py 的 waitress 生产入口。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB_BIN = ROOT / "plugins" / "web" / "bin" / "ttbox-web.py"
sys.path.insert(0, str(WEB_BIN.parent))

# 设置必要的环境变量，让模板/静态目录正确解析
import os
os.environ.setdefault("TTBOX_WEB_PREVIEW", "1")

import importlib.util
spec = importlib.util.spec_from_file_location("ttbox_web_preview", str(WEB_BIN))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

app = mod.app
# 禁用 waitress 依赖：直接 run
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8899, debug=False, threaded=True)
