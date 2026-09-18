import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

from flask import Flask

ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = ROOT / "plugins" / "web"


def load_framework_api():
    """S1-2026-09-18（A0-3c）：framework_api.py（26 条死路由）已从 ttbox-web.py 摘除注册、
    并移出出货包（仓库内保留）。本测试改为对模块自身构造独立 Flask app，验证其路由契约。"""
    for p in (str(WEB_DIR), str(ROOT)):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location(
        "web_framework_api_test", WEB_DIR / "framework_api.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = Flask("web_framework_api_test")
    module.install_framework_api(app)
    return module, app


class WebFrameworkApiTests(unittest.TestCase):
    def test_framework_and_market_api_are_registered(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            import os
            os.environ["TTBOX_PLUGINS_ROOT"] = str(root / "plugins")
            os.environ["TTBOX_PLUGIN_REPOSITORY_ROOT"] = str(root / "repository")
            module, app = load_framework_api()
            client = app.test_client()
            rules = {rule.rule for rule in app.url_map.iter_rules()}
            for route in ("/api/plugins", "/api/plugins/market", "/api/system/status",
                          "/api/core/status", "/api/model/list", "/api/model/active",
                          "/api/network/status", "/api/wifi/status", "/api/fan/status",
                          "/api/monitor/status", "/api/upgrade/status"):
                self.assertIn(route, rules)
            self.assertEqual(client.get("/api/plugins").status_code, 200)
            self.assertEqual(client.get("/api/plugins/market").status_code, 200)

    def test_market_api_only_reads_local_repository(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            import os
            os.environ["TTBOX_PLUGINS_ROOT"] = str(root / "plugins")
            os.environ["TTBOX_PLUGIN_REPOSITORY_ROOT"] = str(root / "repository")
            module, app = load_framework_api()
            client = app.test_client()
            response = client.get("/api/plugins/market", query_string={"q": "web"})
            payload = response.get_json()
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["data"]["source"], "local")
            self.assertEqual(payload["data"]["online"], False)


if __name__ == "__main__":
    unittest.main()
